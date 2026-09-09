<script setup>
import { computed } from 'vue'
import { num } from '../lib/format'

const props = defineProps({
  page: { type: Object, required: true },
  pageSize: { type: Number, required: true },
})
const emit = defineEmits(['page', 'size'])

const from = computed(() => (props.page.total_rows ? props.page.offset + 1 : 0))
const to = computed(() => props.page.offset + props.page.returned)
const lastOffset = computed(() =>
  Math.max(0, Math.ceil(props.page.total_rows / props.pageSize) - 1) * props.pageSize)
</script>

<template>
  <div class="pager">
    <button :disabled="page.offset <= 0" @click="emit('page', 0)">首</button>
    <button :disabled="page.offset <= 0" @click="emit('page', Math.max(0, page.offset - pageSize))">上一页</button>
    <span class="pager-pos">
      第 {{ num(from) }}–{{ num(to) }} 行 / 共 {{ num(page.total_rows) }} 行
      <em v-if="page.returned === 0 && page.total_rows > 0">（offset 越界：这一刀在表外，回空页）</em>
    </span>
    <button :disabled="!page.has_more" @click="emit('page', Math.min(lastOffset, page.offset + pageSize))">下一页</button>
    <button :disabled="!page.has_more" @click="emit('page', lastOffset)">尾</button>
    <label class="pager-size">每页
      <select :value="String(pageSize)" @change="emit('size', Number($event.target.value))">
        <option v-for="n in [50, 100, 200]" :key="n" :value="n">{{ n }}</option>
      </select>
    </label>
  </div>
</template>
