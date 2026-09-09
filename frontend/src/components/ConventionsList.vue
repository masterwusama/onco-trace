<script setup>
import { computed } from 'vue'

const props = defineProps({ conventions: { type: Object, default: () => ({}) } })

// 后端把"这一维怎么读"写在 conventions 里（每条一句话加实测数）。前端只负责把它
// 摊开在一个固定的折叠块里，不改写、不摘要——摘要就是前端在替接口重新定口径。
const items = computed(() => Object.entries(props.conventions || {}))
</script>

<template>
  <details v-if="items.length" class="conv">
    <summary>这一屏的口径（{{ items.length }} 条）</summary>
    <dl>
      <template v-for="[k, v] in items" :key="k">
        <dt>{{ k }}</dt>
        <dd>{{ v }}</dd>
      </template>
    </dl>
  </details>
</template>
