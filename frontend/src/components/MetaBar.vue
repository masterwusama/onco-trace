<script setup>
import { computed } from 'vue'
import { num, text } from '../lib/format'

const props = defineProps({ meta: { type: Object, default: null }, error: { default: null } })

// 站点头只报三件能自证的事：几个病、库里多少行、最后一次装载到什么时候。
// "数据截至"不写常量，取 /api/meta 的 loads 里最晚那次 finished_at。
const rows = computed(() => {
  const t = props.meta?.tables || {}
  return Object.values(t).reduce((a, b) => a + (Number(b) || 0), 0)
})
const lastLoad = computed(() => {
  const ls = props.meta?.loads || []
  return ls.map((l) => l.finished_at).filter(Boolean).sort().pop() || null
})
const sources = computed(() => props.meta?.sources?.length ?? 0)
</script>

<template>
  <div class="meta">
    <template v-if="meta">
      <span>{{ num(meta.site.diseases) }} 个基准病</span>
      <span>库内 {{ num(rows) }} 行</span>
      <span>{{ num(sources) }} 个来源</span>
      <span>装载至 {{ text(lastLoad) }}</span>
    </template>
    <span v-else class="meta-err">
      取不到 /api/meta{{ error ? '：' + error.message : '' }}。后端起了吗（默认 127.0.0.1:8001）？
    </span>
  </div>
</template>
