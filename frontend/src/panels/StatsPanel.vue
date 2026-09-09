<script setup>
import { computed } from 'vue'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import { baseOption, lineSeries } from '../lib/chart'
import PanelState from '../components/PanelState.vue'
import ChartBox from '../components/ChartBox.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })
const { data, error, loading, load } = useApi('/diseases/{code}/stats', () => ({ code: props.code }))
load()

// 分组键与列名都取自响应的 conventions.series_key：接口换了口径列，表格跟着换，
// 前端不抄一份列清单（抄了就等于在接口之外另立一份契约）。
const KEY = computed(() => data.value?.conventions?.series_key || [])
const COLS = computed(() => KEY.value.filter((k) => k !== 'metric'))

const groups = computed(() => {
  const byMetric = new Map()
  for (const s of data.value?.series || []) {
    if (!byMetric.has(s.metric)) byMetric.set(s.metric, [])
    byMetric.get(s.metric).push(s)
  }
  const out = []
  for (const [metric, list] of byMetric) {
    const unit = list.find((s) => s.unit)?.unit || ''
    const charted = list
      .map((s) => ({ s, pts: (s.points || []).filter((p) => Number(p.year) > 0) }))
      .filter((x) => x.pts.length > 1)
    const diff = diffCols(charted.map((x) => x.s))
    out.push({
      metric, unit, series: list, charted, diff,
      option: charted.length ? optionFor(unit, charted, diff) : null,
    })
  }
  return out
})

function optionFor(unit, charted, diff) {
  return baseOption({
    yName: unit,
    yFmt: (v) => (Number.isInteger(v) ? String(v) : v.toFixed(2)),
    series: charted.map((x) => lineSeries(
      diff.map((c) => x.s[c]).filter((v) => v !== '' && v != null).join(' / ') || text(x.s.dataset_code),
      x.pts,
    )),
  })
}

// 图例必须能把自己认出来：同一 metric 下哪几列取值不同，就拿那几列拼名字。
// 全同的情况下只剩 dataset_code，宁可名字长，也不要两条线共用一个图例项。
function diffCols(list) {
  if (list.length < 2) return KEY.value.filter((k) => k !== 'metric')
  return COLS.value.filter((c) => new Set(list.map((s) => String(s[c]))).size > 1)
}

function span(s) {
  const years = (s.points || []).map((p) => Number(p.year))
  if (!years.length) return '—'
  if (years.every((y) => y === 0)) return '无年份'
  const pos = years.filter((y) => y > 0)
  return pos.length ? `${Math.min(...pos)}–${Math.max(...pos)}` : '无年份'
}

const counts = computed(() => data.value?.counts || [])
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="统计层">
    <div v-if="counts.length" class="baseline">
      <div v-for="c in counts" :key="c.metric" class="card-num">
        <div class="v">{{ num(c.value) }}</div>
        <div class="k">{{ c.metric }} <span class="badge">{{ c.unit }}</span></div>
        <div class="u"><ProvenanceTag :prov="c.provenance" /> · year={{ c.year }}</div>
        <p class="small muted" style="margin: 4px 0 0">{{ text(c.cohort_note) }}</p>
      </div>
    </div>

    <section v-for="g in groups" :key="g.metric" class="metric-block">
      <h3 class="metric-title">
        {{ g.metric }}
        <span>{{ g.unit }} · {{ g.series.length }} 条序列{{ g.charted.length ? '，其中 ' + g.charted.length + ' 条可连成线' : '，没有一条能连成线' }}</span>
      </h3>
      <ChartBox v-if="g.option" :option="g.option" />
      <p v-else class="legend-note">
        这一组里没有一条序列有第二个年份点——单点不成线，画出来就是一条假趋势。
      </p>

      <table class="grid tight">
        <thead>
          <tr>
            <th v-for="c in COLS" :key="c">{{ c }}</th>
            <th class="n">点</th><th>年份</th><th class="n">末值</th><th>出处</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in g.series" :key="g.metric + '-' + i">
            <td v-for="c in COLS" :key="c" :class="c === 'cohort_note' ? 'trunc' : ''"
                :title="c === 'cohort_note' ? String(s[c]) : null">{{ text(s[c]) }}</td>
            <td class="n">{{ num(s.n_points) }}</td>
            <td>{{ span(s) }}</td>
            <td class="n">{{ num(s.points?.[s.points.length - 1]?.value) }}</td>
            <td><ProvenanceTag :prov="s.provenance" /></td>
          </tr>
        </tbody>
      </table>
      <p class="legend-note">
        表里每一行就是一条序列，口径列全列都在；序列之间能不能对比，看
        <code>estimate_basis</code> 与 <code>region</code> 是否同值——不同值不相减。
      </p>
    </section>
    <p v-if="data && !data.series.length && !counts.length" class="emptyp">
      这一病在 stat_fact 里没有未判非的行（条数见概览屏的 dims）。
    </p>
  </PanelState>
</template>
