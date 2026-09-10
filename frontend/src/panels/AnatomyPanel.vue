<script setup>
import { computed } from 'vue'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import PanelState from '../components/PanelState.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })
const { data, error, loading, load } = useApi('/diseases/{code}/anatomy', () => ({ code: props.code }))
load()

// 两档分开列，不相加也不混排（conventions.roles 与 no_parent_edge 就是这条的理由）：
// 库里没有父子边，把 subsite 缩进 primary 下面就是画一条不存在的层级。
const tiers = computed(() => [
  { key: 'primary', label: '器官级分组', rows: data.value?.primary || [] },
  { key: 'subsite', label: '亚部位（MONDO term，只做下钻）', rows: data.value?.subsite || [] },
])

function codesOf(row) {
  return (row.mounted?.matched_codes || '').split(',').filter(Boolean)
}
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="器官">
    <section v-for="t in tiers" :key="t.key" class="metric-block">
      <h3 class="metric-title">
        {{ t.label }} <span>{{ num(t.rows.length) }} 条</span>
      </h3>
      <p v-if="!t.rows.length" class="emptyp">
        接口在这一档回了 0 行。是不是缺数据、还是这一病本就没有这一档，看下面 conventions
        里 <code>roles</code> 与 <code>basis</code> 两条——页面不替它选一个解释。
      </p>
      <div v-for="row in t.rows" :key="row.id" class="node">
        <div class="label">
          {{ text(row.label) }}
          <em v-if="row.label_zh">{{ row.label_zh }}</em>
          <span class="badge">{{ text(row.kind) }}</span>
          <RouterLink :to="{ name: 'reverse-anatomy', params: { node_id: row.id } }"
                      class="rev mono small" title="反查：这个节点挂在哪些病上">反查</RouterLink>
        </div>
        <div class="codes mono">
          code {{ text(row.code) }}
          <template v-if="row.icdo3_range"> · icdo3_range {{ row.icdo3_range }}</template>
          <template v-if="row.icd9"> · icd9 {{ row.icd9 }}</template>
        </div>
        <div class="mount">
          挂载依据 <span class="badge">{{ text(row.mounted?.basis) }}</span>
          <template v-if="codesOf(row).length">
            · 命中 {{ codesOf(row).length }} 个码
            <details>
              <summary class="mono small">{{ codesOf(row).slice(0, 8).join(',') }}{{ codesOf(row).length > 8 ? ' …' : '' }}</summary>
              <p class="mono small">{{ codesOf(row).join(', ') }}</p>
            </details>
          </template>
          <em v-else class="muted"> · matched_codes 空</em>
        </div>
        <div class="two-prov">
          <span>节点 <ProvenanceTag :prov="row.provenance" /></span>
          <span>挂载行 <ProvenanceTag :prov="row.mounted?.provenance" /></span>
        </div>
      </div>
    </section>
  </PanelState>
</template>
