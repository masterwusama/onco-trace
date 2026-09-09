<script setup>
defineProps({ gap: { type: Object, required: true } })
</script>

<!-- 空态一律走接口给的那句话，前端不补 0、不画占位线（docs/MVP裁定.md §二 的六处约定）。 -->
<template>
  <p class="gap-note">
    <b>{{ gap.label || '暂无可靠来源' }}</b>
    <span class="gap-text">{{ gap.text }}</span>
    <em v-if="gap.basis" class="gap-basis">判据：{{ gap.basis }}</em>
  </p>
</template>
