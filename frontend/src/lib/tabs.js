// 十一屏的清单：一屏一条，写明它读哪个接口、组件在哪。
// `path` 必须是 api/client.js 里 API_PATHS 登记过的那一条，`api/tests/run.py` 逐条比对。
// 概览屏不另发请求（详情接口自己就是它的料），所以 path 为 null。
export const TABS = [
  { key: 'overview', label: '概览', path: null, comp: 'OverviewPanel', group: '身份' },
  { key: 'stats', label: '发病 · 死亡', path: '/diseases/{code}/stats', comp: 'StatsPanel', group: '统计层' },
  { key: 'survival', label: '生存率', path: '/diseases/{code}/survival', comp: 'SurvivalPanel', group: '统计层' },
  { key: 'anatomy', label: '器官', path: '/diseases/{code}/anatomy', comp: 'AnatomyPanel', group: '词表' },
  { key: 'histology', label: '组织学', path: '/diseases/{code}/histology', comp: 'HistologyPanel', group: '词表' },
  { key: 'symptoms', label: '症状', path: '/diseases/{code}/symptoms', comp: 'SymptomsPanel', group: '词表' },
  { key: 'risks', label: '危险因素', path: '/diseases/{code}/risk-factors', comp: 'RiskPanel', group: '词表' },
  { key: 'trials', label: '在招试验', path: '/diseases/{code}/trials', comp: 'ResearchPanel', group: '研究层' },
  { key: 'publications', label: '前沿文献', path: '/diseases/{code}/publications', comp: 'ResearchPanel', group: '研究层' },
  { key: 'targets', label: '关联靶点', path: '/diseases/{code}/targets', comp: 'ResearchPanel', group: '研究层' },
  { key: 'drugs', label: '在研药', path: '/diseases/{code}/drugs', comp: 'ResearchPanel', group: '研究层' },
]

export const TAB_BY_KEY = Object.fromEntries(TABS.map((t) => [t.key, t]))

// 列表页 dims 里的键与屏的对应（详情/列表同一份度量，靠这个映射才不用另发一次请求）。
export const DIM_BY_TAB = {
  stats: 'stat',
  survival: 'survival',
  anatomy: 'anatomy',
  histology: 'histology',
  symptoms: 'symptom',
  risks: 'risk',
  trials: 'trial',
  publications: 'publication',
  targets: 'target',
  drugs: 'drug',
}
