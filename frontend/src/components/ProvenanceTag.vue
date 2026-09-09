<script setup>
import { computed } from 'vue'
import { provBrief, provLicense, text } from '../lib/format'

const props = defineProps({ prov: { type: Object, default: null } })

// 角标只放源代号，完整一句话（源 · 数据集版本 · 装载日）挂在 title 上；
// 署名要求（attribution_required）在角标上直接可见，不藏进 hover。
const code = computed(() => props.prov?.source?.code || '')
const brief = computed(() => provBrief(props.prov))
const license = computed(() => provLicense(props.prov))
const review = computed(() => text(props.prov?.review_status))
const method = computed(() => text(props.prov?.extract_method))
</script>

<template>
  <span v-if="!prov" class="prov none" :title="'这一行没有出处列（聚合层不给出处）'">—</span>
  <span v-else class="prov" :title="brief + ' · 抽取 ' + method + ' · 复核 ' + review + ' · ' + license">
    <a v-if="prov.source?.home_url" :href="prov.source.home_url" target="_blank"
       rel="noopener noreferrer">{{ code }}</a>
    <template v-else>{{ code }}</template>
    <b v-if="prov.source?.attribution_required" class="attr" title="该源要求署名">©</b>
  </span>
</template>
