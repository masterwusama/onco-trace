<script setup>
import { num } from '../lib/format'
import GapNote from './GapNote.vue'
import ConventionsList from './ConventionsList.vue'

defineProps({
  loading: { type: Boolean, default: false },
  error: { type: Object, default: null },
  data: { type: Object, default: null },
  note: { type: String, default: null },
})

function measures(m) {
  if (!m) return []
  return Object.entries(m).filter(([k]) => !['count', 'rows'].includes(k))
}
</script>

<template>
  <div class="panel">
    <p v-if="loading" class="state">读取中…</p>
    <p v-else-if="error" class="state error">
      这一台没答上：<b>{{ error.status || '' }} {{ error.detail || error.message }}</b>
    </p>
    <template v-else-if="data">
      <div class="panel-head">
        <span v-if="data.measures" class="measures">
          <i>{{ num(data.measures.rows ?? data.measures.count ?? data.measures.any) }} 行</i>
          <b v-for="[k, v] in measures(data.measures)" :key="k">{{ k }} {{ num(v) }}</b>
        </span>
        <span v-if="note" class="note">{{ note }}</span>
      </div>
      <p v-if="data.note" class="dimnote">{{ data.note }}</p>
      <slot />
      <GapNote v-for="(g, i) in data.gaps || []" :key="'g' + i" :gap="g" />
      <ConventionsList :conventions="data.conventions" />
    </template>
  </div>
</template>
