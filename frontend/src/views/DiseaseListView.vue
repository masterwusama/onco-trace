<script setup>
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
import { num, text } from '../lib/format'
import GapNote from '../components/GapNote.vue'

const data = ref(null)
const err = ref(null)
const q = ref('')
const dimLabels = ref({})

onMounted(async () => {
  try {
    data.value = await get('/diseases', { limit: 100 })
    const meta = await get('/meta')
    dimLabels.value = Object.fromEntries((meta.dims || []).map((d) => [d.key, d.label]))
  } catch (e) {
    err.value = e
  }
})

const items = computed(() => {
  const list = data.value?.items || []
  const s = q.value.trim().toLowerCase()
  if (!s) return list
  // 只筛不排：接口给的 code 序就是响应序，前端重排会让列表与详情两边对不上。
  return list.filter((i) => [i.code, i.name_zh, i.name_en, i.icd10, i.mondo_id]
    .some((v) => (v || '').toLowerCase().includes(s)))
})

function measurePairs(dims) {
  return Object.entries(dims || {}).map(([k, v]) => [dimLabels.value[k] || k, v.measures || {}])
}

function filled(dims) {
  return Object.values(dims || {}).filter((v) =>
    Object.values(v.measures || {}).some((n) => Number(n) > 0)).length
}
</script>

<template>
  <section v-if="err" class="error">接口没答上：{{ err.message }}</section>
  <section v-else-if="!data" class="loading">正在读 /api/diseases…</section>
  <section v-else>
    <div class="bar">
      <input v-model="q" placeholder="按病名 / 码 / MONDO 过滤" />
      <span class="bar-info">{{ num(items.length) }} / {{ num(data.total) }} 病</span>
    </div>
    <table class="grid">
      <thead>
        <tr>
          <th>疾病</th><th>类别</th><th>ICD-10</th><th>MONDO</th>
          <th>十维覆盖</th><th>空态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="i in items" :key="i.code" class="row">
          <td class="cell-name">
            <RouterLink :to="'/disease/' + i.code">{{ i.name_zh }}</RouterLink>
            <em>{{ i.name_en }}</em>
          </td>
          <td>{{ text(i.category) }}</td>
          <td class="mono">{{ text(i.icd10) }}</td>
          <td class="mono">{{ text(i.mondo_id) }}</td>
          <td class="dims">
            <span class="dims-n">{{ filled(i.dims) }}/10 维有数</span>
            <details>
              <summary>逐维度量</summary>
              <ul>
                <li v-for="[label, m] in measurePairs(i.dims)" :key="label">
                  <b>{{ label }}</b>
                  <span v-if="!Object.keys(m).length" class="muted">该维无度量</span>
                  <span v-else class="mono">{{ Object.entries(m).map(([k, v]) => k + '=' + (v ?? '—')).join(' · ') }}</span>
                </li>
              </ul>
            </details>
          </td>
          <td>
            <span v-if="!i.gaps?.length" class="muted">无</span>
            <details v-else>
              <summary>{{ i.gaps.length }} 条</summary>
              <GapNote v-for="(g, n) in i.gaps" :key="n" :gap="g" />
            </details>
          </td>
        </tr>
      </tbody>
    </table>
    <p class="foot-note">
      度量原样取自 <code>/api/diseases</code> 的 <code>dims</code>，各维口径不同（见每维
      <code>note</code>），所以这里既不相加也不排序。
    </p>
  </section>
</template>
