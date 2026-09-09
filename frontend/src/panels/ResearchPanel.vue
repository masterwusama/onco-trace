<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RESEARCH } from '../lib/research'
import { useApi } from '../lib/useApi'
import { num, isNil, DASH } from '../lib/format'
import PanelState from '../components/PanelState.vue'
import Pager from '../components/Pager.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, dim: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const cfg = computed(() => RESEARCH[props.dim])
const opened = ref(new Set())

// 分页与筛选全挂在 URL 上；换 dim 时只带属于新维的键，否则 trials 的 status_bucket
// 会被带进 drugs，那是一次意外 404 而不是一次筛选。
const KEYS = ['limit', 'offset', 'status_bucket', 'phase', 'year', 'is_oa', 'include']
const query = computed(() => {
  const cfgFilters = Object.keys(cfg.value.filters)
  const out = {}
  for (const k of [...KEYS.filter((x) => cfgFilters.includes(x)), 'limit', 'offset', 'include'])
    if (route.query[k] != null) out[k] = String(route.query[k])
  return out
})

// 路径在实例生命周期内取定：换维时父级按 tab 钉了 key，这台会重挂，所以这里不需要响应式路径。
const { data, error, loading, load } = useApi(cfg.value.path, () => ({
  code: props.code, ...query.value,
}))

watch(() => [props.dim, route.fullPath], () => { opened.value = new Set(); load() }, { immediate: true })

function setQuery(patch) {
  const q = { ...route.query, ...patch }
  for (const [k, v] of Object.entries(q)) if (v === null || v === undefined || v === '') delete q[k]
  router.replace({ name: 'detail', params: { code: props.code }, query: { ...q, tab: props.dim } })
}

function pickFilter(key, ev) {
  const v = ev.target.value
  setQuery({ [key]: v === '' ? null : v, offset: null })
}

function cell(row, col) {
  const v = col.k.split('.').reduce((o, k) => (o == null ? o : o[k]), row)
  if (isNil(v) || v === '') return null
  switch (col.type) {
    case 'num': return num(v, 0)
    case 'score': return num(v, 3)
    case 'count': return Array.isArray(v) ? String(v.length) : null
    case 'flag': return Number(v) ? '是' : '否'
    case 'list': return Array.isArray(v) ? (v.length ? v.join('、') : null) : String(v)
    case 'datasources':
      return Array.isArray(v) ? v.map((x) => x.id + ' ' + num(x.score, 2)).join(' / ') : null
    default: return String(v)
  }
}

function extraKeys(row) {
  const shown = new Set(cfg.value.cols.map((c) => c.k.split('.')[0]))
  shown.add('provenance'); shown.add('mounted'); shown.add(cfg.value.rowKey)
  return Object.keys(row).filter((k) => !shown.has(k))
}

function detail(v) {
  if (isNil(v) || v === '') return DASH
  // 纯标量数组摊成一行（与 cell() 的 list 列同一写法）；只有真嵌套的结构才值得铺成 JSON——
  // 而 String() 压一行就是 [object Object]，字段全丢。
  if (Array.isArray(v) && v.every((x) => x === null || typeof x !== 'object'))
    return v.length ? v.map((x) => String(x)).join('、') : DASH
  if (typeof v === 'object') return JSON.stringify(v, null, 1)
  return String(v)
}

const rows = computed(() => data.value?.items || [])
const filters = computed(() => data.value?.filters || {})
const hit = computed(() => data.value?.source_hit || null)
const page = computed(() => data.value?.page || { limit: 50, offset: 0, total_rows: 0, returned: 0, has_more: false })
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" :note="cfg.label">
    <p v-if="hit" class="hit">
      源报 <b>{{ num(hit.value) }}</b> {{ hit.unit || '' }}<span v-if="hit.metric">（{{ hit.metric }}）</span>
      · 库内 <b>{{ num(hit.stored_rows) }}</b> 行
      · <em>{{ hit.captured ? '两数相等：这一次查询取满了' : '未取满：差额 ' + num(hit.not_captured) + ' 条是装载时按口径截掉的' }}</em>
    </p>

    <div v-if="data.facets && Object.keys(data.facets).length" class="facets">
      <label v-for="(list, key) in data.facets" :key="key" class="facet">
        <span>{{ cfg.filters[key] || key }}</span>
        <select :value="filters[key] == null ? '' : String(filters[key])" @change="pickFilter(key, $event)">
          <option value="">全部</option>
          <option v-for="f in list" :key="String(f.value)" :value="String(f.value)">
            {{ f.value }}（{{ num(f.rows) }}）
          </option>
        </select>
      </label>
      <span class="facet-hint">选项与条数是未过滤整维的，不跟着当前筛选缩水</span>
    </div>

    <label v-if="dim === 'trials'" class="facet">
      <span>大字段</span>
      <select :value="query.include || ''" @change="pickFilter('include', $event)">
        <option value="">不带（默认）</option>
        <option value="eligibility">入排标准</option>
        <option value="publications">引出文献</option>
        <option value="eligibility,publications">两段都带（一页可达 230 KB）</option>
      </select>
    </label>

    <table class="grid">
      <thead>
        <tr>
          <th v-if="cfg.link"></th>
          <th v-for="c in cfg.cols" :key="c.k">{{ c.label }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="(row, i) in rows" :key="row[cfg.rowKey] ?? i">
          <tr :class="{ open: opened.has(i) }">
            <td v-if="cfg.link" class="ext">
              <a v-if="cfg.link(row)" :href="cfg.link(row)" target="_blank" rel="noopener">↗</a>
            </td>
            <td v-for="c in cfg.cols" :key="c.k" :class="['t-' + (c.type || 'text'), { wide: c.type === 'wide' }]">
              <ProvenanceTag v-if="c.type === 'prov'" :prov="row.provenance" />
              <span v-else-if="cell(row, c) === null" class="nil">—</span>
              <span v-else :title="cell(row, c)">{{ cell(row, c) }}</span>
            </td>
            <td class="rowbtn">
              <button @click="opened.has(i) ? opened.delete(i) : opened.add(i)">
                {{ opened.has(i) ? '收起' : '其余列' }}
              </button>
            </td>
          </tr>
          <tr v-if="opened.has(i)" class="detail">
            <td :colspan="cfg.cols.length + (cfg.link ? 2 : 1)">
              <dl>
                <template v-for="k in (cfg.expand || []).concat(extraKeys(row))" :key="k">
                  <dt>{{ k }}</dt>
                  <dd class="mono json">{{ detail(row[k]) }}</dd>
                </template>
              </dl>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length && page.total_rows > 0">
          <td :colspan="cfg.cols.length + (cfg.link ? 2 : 1)" class="empty">
            offset {{ page.offset }} 越界：整维共 {{ num(page.total_rows) }} 行，这一刀在表外，接口回的是空页
          </td>
        </tr>
      </tbody>
    </table>

    <Pager :page="page" :page-size="page.limit"
           @page="(v) => setQuery({ offset: v || null })"
           @size="(v) => setQuery({ limit: v, offset: null })" />
  </PanelState>
</template>
