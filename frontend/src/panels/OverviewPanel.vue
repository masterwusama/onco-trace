<script setup>
import { computed } from 'vue'
import { text, num } from '../lib/format'
import GapNote from '../components/GapNote.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })

const d = computed(() => props.detail || {})
const ids = computed(() => [
  ['ICD-10', d.value.icd10], ['ICD-O-3', d.value.icdo3], ['ICD-9', d.value.icd9],
  ['MONDO', d.value.mondo_id], ['NCIt', d.value.ncit_id], ['OT 节点', d.value.ot_node],
  ['GBD cause', d.value.gbd_cause], ['GCO today', d.value.gco_today], ['GCO time', d.value.gco_time],
])
const dims = computed(() => Object.entries(d.value.dims || {}))
const xrefs = computed(() => Object.entries(d.value.xrefs || {}))
</script>

<template>
  <div v-if="detail">
    <div class="pane">
      <h2>身份</h2>
      <p class="kv">
        <span>学名 <b>{{ text(d.mondo_name) }}</b></span>
        <span>类别 <b>{{ text(d.category) }}</b></span>
        <span>性别限定 <b>{{ text(d.sex) }}</b></span>
      </p>
      <p class="kv mono">
        <span v-for="[k, v] in ids" :key="k">{{ k }} <b>{{ text(v) }}</b></span>
      </p>
      <p class="src-line">
        主档出处 <ProvenanceTag :prov="d.provenance" />
        <em v-if="d.provenance?.source?.name">{{ d.provenance.source.name }} ·
          {{ d.provenance.source.license }}</em>
      </p>
    </div>

    <div class="pane">
      <h2>十维覆盖</h2>
      <table class="grid tight">
        <thead><tr><th>维</th><th>条数</th><th>度量</th><th>口径</th></tr></thead>
        <tbody>
          <tr v-for="[k, v] in dims" :key="k">
            <td>{{ v.label }}</td>
            <td>
              <b v-if="v.available">{{ num(v.count) }}</b>
              <span v-else class="empty">空</span>
            </td>
            <td class="mono small">
              {{ Object.entries(v.measures || {}).map(([a, b]) => a + '=' + (b ?? '—')).join(' · ') }}
            </td>
            <td class="note">{{ v.note }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pane" v-if="d.gaps?.length">
      <h2>空态（{{ d.gaps.length }} 条）</h2>
      <GapNote v-for="(g, i) in d.gaps" :key="i" :gap="g" />
    </div>

    <div class="pane">
      <h2>检索与页面</h2>
      <p class="tags">
        <span v-for="t in d.search_terms || []" :key="t" class="tag">{{ t }}</span>
      </p>
      <p class="kv small">
        PDQ 页面：<span v-for="p in d.pdq_pages || []" :key="p" class="mono">{{ p }}</span>
        <em v-if="!(d.pdq_pages || []).length">无</em>
      </p>
      <details v-if="xrefs.length">
        <summary>MONDO 交叉引用 {{ xrefs.length }} 组</summary>
        <p v-for="[k, list] in xrefs" :key="k" class="mono small">
          {{ k }} <span v-for="x in list" :key="x">{{ x }}</span>
        </p>
      </details>
    </div>
  </div>
</template>
