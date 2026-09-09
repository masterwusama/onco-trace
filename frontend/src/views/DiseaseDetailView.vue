<script setup>
import { computed, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get } from '../api/client'
import { TABS, TAB_BY_KEY, DIM_BY_TAB } from '../lib/tabs'
import { text } from '../lib/format'
import OverviewPanel from '../panels/OverviewPanel.vue'
import StatsPanel from '../panels/StatsPanel.vue'
import SurvivalPanel from '../panels/SurvivalPanel.vue'
import AnatomyPanel from '../panels/AnatomyPanel.vue'
import HistologyPanel from '../panels/HistologyPanel.vue'
import SymptomsPanel from '../panels/SymptomsPanel.vue'
import RiskPanel from '../panels/RiskPanel.vue'
import ResearchPanel from '../panels/ResearchPanel.vue'

const COMPS = {
  OverviewPanel, StatsPanel, SurvivalPanel, AnatomyPanel, HistologyPanel,
  SymptomsPanel, RiskPanel, ResearchPanel,
}

const route = useRoute()
const router = useRouter()
const code = computed(() => String(route.params.code || ''))
const detail = ref(null)
const err = ref(null)

const PANEL_PROPS = {
  trials: 'trials', publications: 'publications', targets: 'targets', drugs: 'drugs',
}

watchEffect(async () => {
  detail.value = null
  err.value = null
  if (!code.value) return
  try {
    detail.value = await get('/diseases/{code}', { code: code.value })
  } catch (e) {
    err.value = e
  }
})

const tabKey = computed(() => {
  const k = String(route.query.tab || 'overview')
  return TAB_BY_KEY[k] ? k : 'overview'
})

function selectTab(key) {
  router.replace({ name: 'detail', params: { code: code.value }, query: { ...route.query, tab: key } })
}

const activeTab = computed(() => TAB_BY_KEY[tabKey.value])
const panelProps = computed(() => {
  const t = activeTab.value
  const base = { code: code.value, detail: detail.value }
  if (PANEL_PROPS[t.key]) return { ...base, dim: PANEL_PROPS[t.key] }
  return base
})
const activeComp = computed(() => COMPS[activeTab.value.comp])

// 标签上的条数用详情响应自己带的 dims.count（与列表页同一台聚合算出来的），
// 不为数个数再发一次请求；available 为 false 的维如实标"空"，不显示 0。
function tabRows(key) {
  const dim = detail.value?.dims?.[DIM_BY_TAB[key]]
  if (!dim) return null
  return dim.available ? dim.count : '空'
}

const grouped = computed(() => {
  const g = []
  for (const t of TABS) {
    const last = g[g.length - 1]
    if (last && last.group === t.group) last.tabs.push(t)
    else g.push({ group: t.group, tabs: [t] })
  }
  return g
})
</script>

<template>
  <section v-if="err" class="error">{{ err.message }}</section>
  <section v-else-if="!detail" class="loading">正在读 /api/diseases/{{ code }}…</section>
  <section v-else>
    <div class="head">
      <RouterLink to="/" class="back">‹ 全部疾病</RouterLink>
      <div class="title">
        <h1>{{ detail.name_zh }} <em>{{ detail.name_en }}</em></h1>
        <span class="ids mono">
          {{ text(detail.category) }} · ICD-10 {{ text(detail.icd10) }} ·
          ICD-O-3 {{ text(detail.icdo3) }} · MONDO {{ text(detail.mondo_id) }}
        </span>
      </div>
    </div>
    <nav class="tabs">
      <template v-for="g in grouped" :key="g.group">
        <span class="tab-group">{{ g.group }}</span>
        <RouterLink
          v-for="t in g.tabs" :key="t.key" class="tab"
          :class="{ on: t.key === tabKey }"
          :to="{ name: 'detail', params: { code }, query: { tab: t.key } }">
          {{ t.label }}<em v-if="tabRows(t.key) != null">{{ tabRows(t.key) }}</em>
        </RouterLink>
      </template>
    </nav>
    <!-- :key 是必须的：四个研究维共用一个组件，不钉 key 时 Vue 会复用实例，
         而请求路径是 setup 里取定的——换维就会拿着上一维的接口。 -->
    <component :is="activeComp" :key="activeTab.key" v-bind="panelProps" />
  </section>
</template>
