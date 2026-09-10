<script setup>
import { computed, ref, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'

const route = useRoute()

const TITLES = {
  'reverse-anatomy': '器官反查',
  'reverse-symptom': '症状反查',
  'reverse-risk': '危险因素反查',
  'reverse-target': '靶点反查',
  'reverse-drug': '药物反查',
}

const type = computed(() => route.name)
const title = computed(() => TITLES[type.value] || '反查')
const loading = ref(false)
const data = ref(null)
const err = ref(null)

watchEffect(async () => {
  data.value = null
  err.value = null
  loading.value = true
  const t = type.value
  try {
    if (t === 'reverse-anatomy') {
      data.value = await get('/anatomy/{node_id}/diseases', { node_id: route.params.node_id })
    } else if (t === 'reverse-symptom') {
      data.value = await get('/symptoms/{name}/diseases', {
        name: route.params.name, lang: route.query.lang || 'en' })
    } else if (t === 'reverse-risk') {
      data.value = await get('/risk-factors/{factor_id}/diseases', { factor_id: route.params.factor_id })
    } else if (t === 'reverse-target') {
      data.value = await get('/targets/{ot_id}/diseases', { ot_id: route.params.ot_id })
    } else if (t === 'reverse-drug') {
      data.value = await get('/drugs/{drug_id}/diseases', { drug_id: route.params.drug_id })
    }
  } catch (e) {
    err.value = e
  } finally {
    loading.value = false
  }
})

const diseases = computed(() => {
  if (!data.value) return []
  if (data.value.diseases) return data.value.diseases
  return []
})

// risk 响应没有 diseases/n_diseases，只有 genetic/exposure 两层各一份清单（reverse.py
// 按两层分列回），总数在这里合成。库里没有同时挂两层的因素，相加不会重计。
const nTotal = computed(() => {
  if (!data.value) return 0
  if (type.value === 'reverse-risk')
    return (data.value.genetic?.length || 0) + (data.value.exposure?.length || 0)
  return data.value.n_diseases || diseases.value.length
})

// 症状分源块里每一行是那个源的 item（一个病一行），病名从全局清单按 id 查回。
function disOf(id) {
  return diseases.value.find((d) => d.id === id) || null
}

const entity = computed(() => data.value?.entity || null)
</script>

<template>
  <section>
    <RouterLink to="/" class="back">‹ 全部疾病</RouterLink>
    <h1>{{ title }}</h1>

    <div v-if="err" class="error">{{ err.message }}</div>
    <div v-else-if="loading" class="loading">正在加载…</div>
    <div v-else>
      <div v-if="entity" class="entity">
        <h2>{{ entity.approved_symbol || entity.label || entity.name || entity.drug_name || entity.drug_id }}</h2>
        <p v-if="entity.approved_name" class="sub">{{ entity.approved_name }}</p>
        <p v-if="entity.label_zh" class="sub">{{ entity.label_zh }}</p>
      </div>

      <p class="count">关联疾病：{{ nTotal }} 个</p>

      <template v-if="type === 'reverse-risk'">
        <div v-if="data.genetic?.length" class="block">
          <h3>遗传关联（{{ data.n_genetic }}）</h3>
          <ul>
            <li v-for="d in data.genetic" :key="d.id">
              <RouterLink :to="{ name: 'detail', params: { code: d.code } }">
                {{ d.name_zh }} <em>{{ d.code }}</em>
              </RouterLink>
            </li>
          </ul>
        </div>
        <div v-if="data.exposure?.length" class="block">
          <h3>可干预暴露（{{ data.n_exposure }}）</h3>
          <ul>
            <li v-for="d in data.exposure" :key="d.id">
              <RouterLink :to="{ name: 'detail', params: { code: d.code } }">
                {{ d.name_zh }} <em>{{ d.code }}</em>
              </RouterLink>
            </li>
          </ul>
        </div>
      </template>

      <template v-else-if="type === 'reverse-symptom' && data.sources">
        <div v-for="src in data.sources" :key="src.source_code" class="block">
          <h3>{{ src.source_code }}（{{ src.n_items }} 条）</h3>
          <ul>
            <li v-for="it in src.items" :key="it.id">
              <RouterLink v-if="disOf(it.disease_id)"
                          :to="{ name: 'detail', params: { code: disOf(it.disease_id).code } }">
                {{ disOf(it.disease_id).name_zh }} <em>{{ disOf(it.disease_id).code }}</em>
              </RouterLink>
              <span v-if="it.freq_band" class="mono"> · {{ it.freq_band }}</span>
            </li>
          </ul>
        </div>
      </template>

      <template v-else>
        <ul v-if="diseases.length">
          <li v-for="d in diseases" :key="d.id">
            <RouterLink :to="{ name: 'detail', params: { code: d.code } }">
              {{ d.name_zh }} <em>{{ d.code }}</em>
              <span v-if="d.score != null" class="mono"> · score {{ d.score }}</span>
              <span v-if="d.entries?.length" class="mono"> · {{ d.entries.length }} 条目</span>
            </RouterLink>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.back { display: inline-block; margin-bottom: 1rem; color: #666; }
h1 { margin: 0 0 1rem; }
.entity h2 { margin: 0.5rem 0 0.25rem; }
.entity .sub { margin: 0; color: #666; }
.count { margin: 1rem 0 0.5rem; font-weight: 600; }
.block { margin: 1rem 0; }
.block h3 { margin: 0.5rem 0; }
ul { list-style: none; padding: 0; margin: 0; }
li { padding: 0.4rem 0; border-bottom: 1px solid #eee; }
li a { text-decoration: none; color: #0066cc; }
li em { color: #888; font-style: normal; font-size: 0.9em; }
.mono { font-family: monospace; font-size: 0.85em; color: #666; }
.error { color: #c00; padding: 1rem; }
.loading { color: #666; padding: 1rem; }
</style>
