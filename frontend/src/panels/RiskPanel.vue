<script setup>
import { computed, ref, watch } from 'vue'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import PanelState from '../components/PanelState.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })

// 榜的接口没有 offset：一次取 limit 行，按 pvalue_mlog 降序。所以这里只有"取多少行"，
// 没有下一页——把截断说成截断，不假装能翻下去。
const limit = ref(100)
const { data, error, loading, load } = useApi('/diseases/{code}/risk-factors',
  () => ({ code: props.code, limit: limit.value }))
watch(() => props.code, () => { limit.value = 100; load() }, { immediate: true })

const genetic = computed(() => data.value?.genetic || null)
const exposure = computed(() => data.value?.exposure || null)
const hidden = computed(() => (genetic.value ? genetic.value.total_rows - genetic.value.returned : 0))

function pmi(id) {
  return Number(id) ? 'https://pubmed.ncbi.nlm.nih.gov/' + id + '/' : null
}
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="危险因素">
    <section v-if="genetic" class="metric-block">
      <h3 class="metric-title">
        遗传关联 <span>{{ num(genetic.total_rows) }} 行 · 本页 {{ num(genetic.returned) }} 行</span>
        <label class="facet">取前
          <select v-model="limit" @change="load()">
            <option v-for="n in [100, 200, 300, 500]" :key="n" :value="n">{{ n }}</option>
          </select>
          行
        </label>
      </h3>
      <table class="grid tight">
        <thead>
          <tr><th>uri_tier</th><th class="n">rows_</th><th class="n">loci</th>
              <th class="n">studies</th><th class="n">releases</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in genetic.tiers" :key="t.uri_tier">
            <td class="mono">{{ text(t.uri_tier) }}</td>
            <td class="n">{{ num(t.rows_) }}</td>
            <td class="n">{{ num(t.loci) }}</td>
            <td class="n">{{ num(t.studies) }}</td>
            <td class="n">{{ num(t.releases) }}</td>
          </tr>
        </tbody>
      </table>
      <p class="legend-note">
        一行是一个关联（研究 × 位点），不是一个位点：所以 rows_ 与 loci / studies 各是各的数。
        <template v-if="genetic.truncated">本页按 <code>pvalue_mlog</code> 降序取了前
          {{ num(genetic.returned) }} 行，库里还有 <b>{{ num(hidden) }}</b> 行没回（这一台没有 offset，翻不到）。</template>
        <template v-else>未截断：这一病的关联行全部在这页上。</template>
      </p>
      <table class="grid tight">
        <thead>
          <tr>
            <th>位点 label</th><th>snps</th><th>风险等位</th><th>位置</th>
            <th class="n">freq</th><th class="n">or_beta</th><th>ci95</th>
            <th>p 值</th><th class="n">-log10(p)</th><th>trait_label</th><th>study / PMID</th>
            <th class="n">paf</th><th>关联出处</th><th>位点出处</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in genetic.items" :key="r.id">
            <td>
              {{ text(r.factor?.label) }}<em v-if="r.factor?.label_zh" class="muted"> / {{ r.factor.label_zh }}</em>
              <RouterLink v-if="r.factor?.id" :to="{ name: 'reverse-risk', params: { factor_id: r.factor.id } }"
                          class="rev mono small" title="反查：这个危险因素关联哪几个病">反查</RouterLink>
            </td>
            <td class="mono trunc" :title="String(r.snps)">{{ text(r.snps) }}</td>
            <td class="mono">{{ text(r.risk_allele) }}</td>
            <td class="mono">{{ r.chr_id ? r.chr_id + ':' + text(r.chr_pos) : '—' }}</td>
            <td class="n">{{ num(r.risk_allele_freq, 3) }}</td>
            <td class="n">{{ num(r.or_beta, 3) }}</td>
            <td class="mono">{{ text(r.ci95_text) }}</td>
            <td class="mono">{{ text(r.p_value_text) }}</td>
            <td class="n">{{ num(r.pvalue_mlog, 1) }}</td>
            <td class="trunc" :title="String(r.trait_label)">{{ text(r.trait_label) }}</td>
            <td class="mono small">
              {{ text(r.study_accession) }}
              <a v-if="pmi(r.pubmedid)" :href="pmi(r.pubmedid)" target="_blank" rel="noopener noreferrer">
                {{ r.pubmedid }} ↗</a>
              <span v-else class="nil" title="CRA 行没有文献号，库里存 0">—</span>
            </td>
            <td class="n">{{ num(r.paf, 2) }}</td>
            <td><ProvenanceTag :prov="r.provenance" /></td>
            <td><ProvenanceTag :prov="r.factor?.provenance" /></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="exposure" class="metric-block">
      <h3 class="metric-title">可干预暴露 <span>{{ num(exposure.returned) }} 条 · 全回，不截断</span></h3>
      <table class="grid tight">
        <thead>
          <tr><th>暴露 label</th><th>kind</th><th>trait_label</th><th>trait_uri</th>
              <th>关联出处</th><th>节点出处</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in exposure.items" :key="r.id">
            <td>
              {{ text(r.factor?.label) }}<em v-if="r.factor?.label_zh" class="muted"> / {{ r.factor.label_zh }}</em>
              <RouterLink v-if="r.factor?.id" :to="{ name: 'reverse-risk', params: { factor_id: r.factor.id } }"
                          class="rev mono small" title="反查：这个危险因素关联哪几个病">反查</RouterLink>
            </td>
            <td class="mono">{{ text(r.factor?.kind) }}</td>
            <td class="trunc" :title="String(r.trait_label)">{{ text(r.trait_label) }}</td>
            <td class="mono small">{{ text(r.trait_uri) }}</td>
            <td><ProvenanceTag :prov="r.provenance" /></td>
            <td><ProvenanceTag :prov="r.factor?.provenance" /></td>
          </tr>
        </tbody>
      </table>
      <p class="legend-note">
        这一层给得出名字、一个强度都没有：paf 两半都在 IHME 授权门后，
        所以这里既不做归因分数榜，也不把 exposure 与上面的 genetic 相加。
      </p>
    </section>
  </PanelState>
</template>
