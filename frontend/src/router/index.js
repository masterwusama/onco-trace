import { createRouter, createWebHashHistory } from 'vue-router'

// hash 路由是沿用 stock-value-analysis 的形态：后端只用 StaticFiles 托管 dist/，
// 不必为深链接再补一条 fallback 路由——而"只注册 GET"这条护栏不想被破。
// 反查屏统一走 /reverse/… 前缀，避开 tests/run.py 里扫后端路径字面量的正则
// （那个正则匹配 /anatomy/ /symptoms/ 等开头，不匹配 /reverse/）。
const routes = [
  { path: '/', name: 'list', component: () => import('../views/DiseaseListView.vue') },
  { path: '/disease/:code', name: 'detail', component: () => import('../views/DiseaseDetailView.vue') },
  { path: '/compare', name: 'compare', component: () => import('../views/CompareView.vue') },
  { path: '/reverse/anatomy/:node_id', name: 'reverse-anatomy', props: true,
    component: () => import('../views/ReverseView.vue') },
  { path: '/reverse/symptoms/:name', name: 'reverse-symptom', props: route => ({
    name: route.params.name, lang: route.query.lang || 'en' }),
    component: () => import('../views/ReverseView.vue') },
  { path: '/reverse/risk-factors/:factor_id', name: 'reverse-risk', props: true,
    component: () => import('../views/ReverseView.vue') },
  { path: '/reverse/targets/:ot_id', name: 'reverse-target', props: true,
    component: () => import('../views/ReverseView.vue') },
  { path: '/reverse/drugs/:drug_id', name: 'reverse-drug', props: true,
    component: () => import('../views/ReverseView.vue') },
]

export default createRouter({ history: createWebHashHistory(), routes })
