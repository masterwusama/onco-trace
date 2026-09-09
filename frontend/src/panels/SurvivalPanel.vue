<script setup>
import { computed } from 'vue'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import { baseOption, lineSeries, COLOR_OBSERVED, COLOR_FITTED } from '../lib/chart'
import PanelState from '../components/PanelState.vue'
import ChartBox from '../components/ChartBox.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })
const { data, error, loading, load } = useApi('/diseases/{code}/survival', () => ({ code: props.code }))
load()

const headline = computed(() => data.value?.headline || null)
const byStage = computed(() => data.value?.by_stage || [])
const layers = computed(() => data.value?.layers || {})
const TREND_COLS = computed(() => (data.value?.conventions?.series_key || []).filter((k) => k !== 'is_observed'))

// 一张图一组档：stage_scheme 与 stage 相同的观测线与拟合线放进同一坐标系才比得出来，
// window_label 不同也放一起（源自己就是这么并排的），但图例把窗写全，不简称"观测/拟合"。
const trendGroups = computed(() => {
  const map = new Map()
  for (const s of data.value?.trend || []) {
    const key = [s.stage_scheme, s.stage].join(' | ')
    if (!map.has(key)) map.set(key, { key, stage_scheme: s.stage_scheme, stage: s.stage, list: [] })
    map.get(key).list.push(s)
  }
  const out = [...map.values()]
  for (const g of out) {
    const obsYears = g.list.filter(isObs).flatMap((s) => s.points.map((p) => Number(p.year)))
    g.lastObs = obsYears.length ? Math.max(...obsYears) : null
    g.option = baseOption({
      yName: '%',
      yFmt: (v) => v + '%',
      series: g.list.map((s) => {
        const pts = s.points.map((p) => ({ year: Number(p.year), value: p.rate_pct }))
        const fitted = !isObs(s)
        const line = lineSeries(legendName(s), pts, {
          dashed: fitted, color: fitted ? COLOR_FITTED : COLOR_OBSERVED,
        })
        // 拟合段标出来：观测止于哪一年，那条竖线就画在哪一年。
        // 不标等于把 2019–2023 的模型值和 1975–2018 的实测值说成同一种东西。
        if (fitted && g.lastObs != null) {
          const maxFit = Math.max(...pts.map((p) => p.year))
          if (maxFit > g.lastObs) {
            line.markLine = {
              symbol: ['none', 'none'],
              lineStyle: { color: COLOR_FITTED, type: 'dotted', width: 1 },
              label: { formatter: `观测止于 ${g.lastObs}`, fontSize: 11, color: COLOR_FITTED },
              data: [{ xAxis: g.lastObs }],
            }
          }
        }
        return line
      }),
    })
  }
  return out
})

function isObs(s) { return Number(s.is_observed) === 1 }
function legendName(s) {
  return `${isObs(s) ? '观测' : '拟合'} · ${s.region} · ${s.window_label}`
}
function span(s) {
  const years = (s.points || []).map((p) => Number(p.year))
  return years.length ? `${Math.min(...years)}–${Math.max(...years)}` : '—'
}
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="生存率">
    <div v-if="headline" class="pane">
      <h2>{{ layers.headline || '头条' }}</h2>
      <div class="headline">
        <div class="big">{{ num(headline.rate_pct, 1) }}<i>%</i></div>
        <div class="meta-lines">
          <div>
            <span :class="['badge', Number(headline.is_observed) ? 'obs' : 'fit']">
              {{ Number(headline.is_observed) ? '观测值' : '拟合值' }}
            </span>
            <b>{{ headline.year }}</b> 年队列 · {{ text(headline.window_label) }}
          </div>
          <div>分期 <b>{{ text(headline.stage) }}</b>（方案 {{ text(headline.stage_scheme) }}）
            · 地区 <b>{{ text(headline.region) }}</b> · 数据集 {{ text(headline.dataset_code) }}</div>
          <div>出处 <ProvenanceTag :prov="headline.provenance" /></div>
        </div>
      </div>
    </div>
    <p v-else class="emptyp">
      这一病没有全分期头条行（分期档与逐年序列若有数，仍在下面各节，不因为它们存在就当头条有数）。
    </p>

    <section v-if="byStage.length" class="metric-block">
      <h3 class="metric-title">分期档 <span>{{ byStage.length }} 档 · {{ layers.by_stage }}</span></h3>
      <table class="grid tight">
        <thead>
          <tr><th>stage</th><th>stage_scheme</th><th>window_label</th><th>year</th>
              <th class="n">rate_pct</th><th>观测/拟合</th><th>出处</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in byStage" :key="r.stage_scheme + '-' + r.stage + '-' + i">
            <td class="wide">{{ text(r.stage) }}</td>
            <td>{{ text(r.stage_scheme) }}</td>
            <td class="trunc" :title="String(r.window_label)">{{ text(r.window_label) }}</td>
            <td>{{ text(r.year) }}</td>
            <td class="n">{{ num(r.rate_pct, 1) }}%</td>
            <td><span :class="['badge', Number(r.is_observed) ? 'obs' : 'fit']">
              {{ Number(r.is_observed) ? '观测' : '拟合' }}</span></td>
            <td><ProvenanceTag :prov="r.provenance" /></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-for="g in trendGroups" :key="g.key" class="metric-block">
      <h3 class="metric-title">
        逐年序列 · {{ g.stage }}
        <span>{{ g.stage_scheme }} · {{ g.list.length }} 条线（观测 {{ g.list.filter(isObs).length }} / 拟合 {{ g.list.filter((s) => !isObs(s)).length }}）</span>
      </h3>
      <p class="legend-note">
        虚线金色是<b>拟合值</b>，实线蓝色是<b>观测值</b>；{{ layers.trend }}
      </p>
      <ChartBox :option="g.option" />
      <table class="grid tight">
        <thead>
          <tr><th v-for="c in TREND_COLS" :key="c">{{ c }}</th>
              <th class="n">is_observed</th><th class="n">点</th><th>年份</th>
              <th class="n">起</th><th class="n">止</th><th>出处</th></tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in g.list" :key="i">
            <td v-for="c in TREND_COLS" :key="c" :class="c === 'window_label' ? 'trunc' : ''"
                :title="c === 'window_label' ? String(s[c]) : null">{{ text(s[c]) }}</td>
            <td class="n">{{ num(s.is_observed) }}</td>
            <td class="n">{{ num(s.n_points) }}</td>
            <td>{{ span(s) }}</td>
            <td class="n">{{ num(s.points[0].rate_pct, 1) }}%</td>
            <td class="n">{{ num(s.points[s.points.length - 1].rate_pct, 1) }}%</td>
            <td><ProvenanceTag :prov="s.provenance" /></td>
          </tr>
        </tbody>
      </table>
    </section>

    <p v-if="data && !byStage.length && !trendGroups.length && !headline" class="emptyp">
      这一病在 survival 里没有未判非的行。
    </p>
  </PanelState>
</template>
