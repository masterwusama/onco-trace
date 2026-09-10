<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get } from '../api/client'
import { useApi } from '../lib/useApi'
import { AXES, valueLabel, yearLabel, spanLabel } from '../lib/compare'
import { num, text, isNil } from '../lib/format'
import ProvenanceTag from '../components/ProvenanceTag.vue'
import ConventionsList from '../components/ConventionsList.vue'

const route = useRoute()
const router = useRouter()

// 榜的入口只有一个：先给 metric。没给的时候不猜——页面把度量清单本身摊开给你看，
// 每个带单位、覆盖几病、跨几年，选一个才往下走。
const metric = computed(() =>
  isNil(route.query.metric) || route.query.metric === '' ? null : String(route.query.metric))

// 已钉的轴从 URL 原样带过。region 与 age_band 都可能是空串（那是库里的一个真取值，
// 不是"没填"），所以这里只问"这个键在不在 URL 上"，不问它空不空。
const PARAMS = AXES.map((a) => a.k).concat('year')
const params = computed(() => {
  const out = { metric: metric.value }
  for (const k of PARAMS) if (!isNil(route.query[k])) out[k] = String(route.query[k])
  return out
})

const { data, error, loading, load } = useApi('/stats/compare', () => params.value, {
  skip: () => !metric.value,
})
watch(() => route.fullPath, load, { immediate: true })

// 度量清单不并进 /api/meta：那份的 dims.stat 是照口径切的（cn_point / age_case /
// query_count…），不是照 stat_fact.metric 切的，两个数各说各的事。
const metrics = ref(null)
const metricsErr = ref(null)
onMounted(async () => {
  try {
    metrics.value = await get('/stats/metrics')
  } catch (e) {
    metricsErr.value = e
  }
})

// select 里"还没选"必须用一个不可能是列取值的哨兵：'' 在 region / age_band 两轴上
// 是一个真选择，拿它当占位符就永远点不到"源没有这一列"那一档。
const PICK = '__pick__'

const d = computed(() => data.value)
const items = computed(() => d.value?.items || [])
const axisLabels = { metric: '度量', ...Object.fromEntries(AXES.map((a) => [a.k, a.label])), year: '年份' }

// 口径条按钉的顺序摊：metric → 四轴 → year。year 在接口还没成榜时是 null
// （它只是"要哪一刀"，不是切片轴），那就不显示，显示出来会是「无年份（单点）」——
// 那是 stat_fact 里 year=0 那批单点行的说法，跟这个不是一件事。
const pins = computed(() => {
  const p = d.value?.pinned || {}
  const auto = d.value?.auto_pinned || []
  return ['metric', ...AXES.map((a) => a.k), 'year']
    .filter((k) => k in p && !isNil(p[k]))
    .map((k) => ({
      k,
      label: axisLabels[k] || k,
      text: k === 'year' ? yearLabel(p[k]) : valueLabel(p[k]),
      auto: auto.includes(k),
    }))
})

function setQuery(patch) {
  // 只清"没这个键"，不清空串：'' 在 region / age_band 两轴上是一个真取值，
  // 把它删出 URL 等于用户选了"源没有这一列"那一档而请求里没带上，接口会重新
  // 把这一轴放回 needs——那一档就永远钉不上了。
  const q = { ...route.query, ...patch }
  for (const [k, v] of Object.entries(q)) if (v === undefined || v === null) delete q[k]
  router.replace({ name: 'compare', query: q })
}

// 换度量等于换一个口径空间：四轴与年份整个不要。留着旧值不是"记住选择"，是一次注定
// 404 的请求；给它们赋 null 也不行——vue-router 会序列化成裸键 ?region&，
// 而 region 的裸键读起来是"取值空串"，那是另一个意思。
function pickMetric(v) {
  router.replace({ name: 'compare', query: v ? { metric: v } : {} })
}

// 轴按接口回的 needs 顺序钉。改前面这一刀会改变后面几刀还能选什么，
// 所以清掉的是这条之后的轴，留下的是之前已经钉好的（后端会重新 auto_pin）。
function pickAxis(key, ev) {
  const v = ev.target.value
  if (v === PICK) return
  const order = AXES.map((a) => a.k)
  const q = { year: null, [key]: v }
  for (const k of order.slice(order.indexOf(key) + 1)) q[k] = null
  setQuery(q)
}

function pickYear(ev) {
  const v = ev.target.value
  setQuery({ year: v === '' ? null : v })
}

function clearAll() {
  router.replace({ name: 'compare', query: {} })
}

const axisCount = (i, k) => i.axes[k]
const unitText = (u) => (Array.isArray(u) ? u.join(' / ') : text(u))
</script>

<template>
  <p v-if="metricsErr" class="state error">度量清单没答上：{{ metricsErr.message }}</p>

  <div v-if="!metric" class="pane">
    <h2>跨病榜：先挑一个度量</h2>
    <p class="dimnote">
      同一度量、同一套口径，18 个病才排得成一张榜。五个口径（度量 + 地区 + 估算依据 +
      性别 + 年龄组）没钉全的，接口不出榜，只回还剩哪些可挑、各自覆盖几个病。
      所以"口径轴"那一列的取值个数是未过滤时的数，真要钉几刀以选完之后接口回的 needs 为准。
    </p>
    <p v-if="!metrics && !metricsErr" class="state">度量清单读取中…</p>
    <table v-else class="grid">
      <thead>
        <tr>
          <th>度量</th><th>单位</th><th class="n">库内行数</th><th class="n">覆盖病数</th>
          <th>年份</th><th>口径轴（各有几个取值）</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="i in metrics?.items || []" :key="i.metric">
          <td class="mono">{{ i.metric }}</td>
          <td>{{ unitText(i.unit) }}</td>
          <td class="n">{{ num(i.rows) }}</td>
          <td class="n">{{ i.diseases }}/18</td>
          <td>
            <span v-if="i.year_first === null" class="muted">
              {{ num(i.rows) }} 行全部无年份（单点）
            </span>
            <span v-else>
              {{ i.year_first }}–{{ i.year_last }}
              <em v-if="i.year_zero_rows" class="muted small">
                · 另有 {{ num(i.year_zero_rows) }} 行无年份
              </em>
            </span>
          </td>
          <td class="mono small">{{ AXES.map((a) => a.k + ' ' + axisCount(i, a.k)).join(' · ') }}</td>
          <td class="act"><button @click="pickMetric(i.metric)">排这个度量</button></td>
        </tr>
      </tbody>
    </table>
    <ConventionsList :conventions="metrics?.conventions" />
  </div>

  <div v-else class="pane">
    <div class="bar">
      <label class="facet">
        <span>度量</span>
        <select :value="metric" @change="pickMetric($event.target.value)">
          <option v-for="i in metrics?.items || []" :key="i.metric" :value="i.metric">
            {{ i.metric }}（{{ Array.isArray(i.unit) ? i.unit.join(' / ') : i.unit }}，{{ num(i.rows) }} 行 ·
            {{ i.diseases }}/18 病）
          </option>
        </select>
      </label>
      <button @click="clearAll">重来</button>
      <span class="bar-info"><RouterLink to="/">回疾病列表</RouterLink></span>
    </div>

    <p v-if="loading" class="state">读取中…</p>
    <p v-else-if="error" class="state error">
      这一刀接口没认：<b>{{ error.status }}</b> {{ error.detail || error.message }}
      <span class="muted">——接口把可取的取值写在括号里了，照它挑一个。</span>
    </p>

    <template v-else-if="d">
      <div class="kv">
        <span v-for="p in pins" :key="p.k" class="pin">
          <b>{{ p.label }}</b> <span class="mono">{{ p.text }}</span>
          <em v-if="p.auto" class="badge">这一维只有一个取值，自动钉</em>
        </span>
      </div>

      <div v-if="d.needs.length" class="facets">
        <label v-for="k in d.needs" :key="k" class="facet">
          <span>{{ axisLabels[k] || k }}</span>
          <select :value="params[k] === undefined ? PICK : String(params[k])" @change="pickAxis(k, $event)">
            <option :value="PICK" disabled>挑一个（可取 {{ (d.choices[k] || []).length }} 个）</option>
            <option v-for="ch in d.choices[k] || []" :key="String(ch.value)" :value="String(ch.value)">
              {{ valueLabel(ch.value) }}（{{ ch.diseases }} 病 · {{ num(ch.rows_) }} 行 ·
              跨 {{ spanLabel(ch.year_first, ch.year_last) }}）
            </option>
          </select>
        </label>
        <span class="facet-hint">
          还差 {{ d.needs.length }} 刀才成榜；下一刀能选什么由上面几刀决定，所以选项是照当前口径现算的。
        </span>
      </div>

      <p v-if="d.needs.length" class="state">
        口径没钉全，这一屏不回榜：这个状态下接口的 <code>items</code> 是空的、<code>coverage</code>
        是 null——凑一个榜出来就是拿两批不同的人凑一个率。
      </p>

      <template v-else>
        <div class="headline">
          <div class="big">{{ items.length }}<i>病上了榜</i></div>
          <div class="meta-lines">
            <div>这一切片 <b>{{ d.coverage }}</b> 病有行 · 单位 <b>{{ unitText(d.unit) }}</b></div>
            <div>
              取的年份 <b>{{ yearLabel(d.year_used) }}</b>
              <select class="yearsel" :value="params.year === undefined ? '' : String(params.year)"
                      @change="pickYear">
                <option value="">最新一年（{{ yearLabel(d.years_available[d.years_available.length - 1]) }}）</option>
                <option v-for="y in d.years_available" :key="y" :value="String(y)">{{ yearLabel(y) }}</option>
              </select>
              <span class="muted">该口径 {{ d.years_available.length }} 个年份有行</span>
            </div>
          </div>
        </div>

        <table class="grid">
          <thead>
            <tr>
              <th class="n">#</th><th>疾病</th><th class="n">{{ metric }}</th>
              <th>cohort_note（这一格的口径）</th><th>出处</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(it, i) in items" :key="it.code">
              <td class="n">{{ i + 1 }}</td>
              <td class="cell-name">
                <RouterLink :to="'/disease/' + it.code">{{ it.name_zh }}</RouterLink>
                <em class="mono">{{ it.code }}</em>
              </td>
              <td class="n mono">{{ num(it.value) }}</td>
              <td class="small">{{ text(it.cohort_note) }}</td>
              <td><ProvenanceTag :prov="it.provenance" /></td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="5" class="empty">
                各轴都钉死了却零行——这种口径在库里一行没有，接口不会拿别的年份的行来顶
              </td>
            </tr>
          </tbody>
        </table>
        <p class="foot-note">
          名次就是接口返回的序（<code>value</code> 降序、并列按病码），前端没有重排；
          榜只在这五样完全相同的时候比大小，跨度量、跨口径、跨年份既不相减也不并列。
        </p>

        <div v-if="d.absent.length" class="block">
          <div class="block-head">
            <h3>这一口径缺席的病（{{ d.absent.length }}）</h3>
            <span class="h">缺席不是零：源没给这一病这一档的行，就不排它</span>
          </div>
          <div class="tags">
            <RouterLink v-for="a in d.absent" :key="a.code" class="tag" :to="'/disease/' + a.code">
              {{ a.name_zh }}
            </RouterLink>
          </div>
        </div>
      </template>

      <p v-if="d.note" class="dimnote">{{ d.note }}</p>
    </template>
  </div>
</template>
