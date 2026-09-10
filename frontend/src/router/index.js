import { createRouter, createWebHashHistory } from 'vue-router'

// hash 路由是沿用 stock-value-analysis 的形态：后端只用 StaticFiles 托管 dist/，
// 不必为深链接再补一条 fallback 路由——而"只注册 GET"这条护栏不想被破。
const routes = [
  { path: '/', name: 'list', component: () => import('../views/DiseaseListView.vue') },
  { path: '/disease/:code', name: 'detail', component: () => import('../views/DiseaseDetailView.vue') },
  { path: '/compare', name: 'compare', component: () => import('../views/CompareView.vue') },
]

export default createRouter({ history: createWebHashHistory(), routes })
