<script setup>
import { onMounted, ref } from 'vue'
import { get } from './api/client'
import MetaBar from './components/MetaBar.vue'

const meta = ref(null)
const metaErr = ref(null)

onMounted(async () => {
  try {
    meta.value = await get('/meta')
  } catch (e) {
    metaErr.value = e
  }
})
</script>

<template>
  <header class="top">
    <RouterLink to="/" class="brand">onco-trace</RouterLink>
    <MetaBar :meta="meta" :error="metaErr" />
  </header>
  <main class="body">
    <RouterView />
  </main>
  <footer class="foot">
    本站只读：数据全部来自公开源自动采集，无人工录入。每个数字随行带出处
    （源 · 数据集版本 · 抽取方式 · 复核状态 · 装载时间）。本站不做诊断，症状与危险度页面输出的是
    参考排序，不是结论。
  </footer>
</template>
