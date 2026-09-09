// 研究层四台的列与筛选项配置。四台共用一个组件，是因为四份响应的形状本来就同构
// （shell 头部 + filters + facets + page + items + source_hit），差别只在显示哪几列。
// 列名一律用 db/schema.sql 的列名：接口不另起名字，前端也就不该另起一套。

const PROV = { k: '_prov', label: '出处', type: 'prov' }

export const RESEARCH = {
  trials: {
    label: '在招试验',
    path: '/diseases/{code}/trials',
    filters: { status_bucket: '状态档', phase: '分期档' },
    rowKey: 'nct_id',
    link: (row) => 'https://clinicaltrials.gov/study/' + row.nct_id,
    cols: [
      { k: 'nct_id', label: 'NCT 号' },
      { k: 'brief_title', label: '简称', type: 'wide' },
      { k: 'phases', label: '分期', type: 'list' },
      { k: 'study_type', label: '类型' },
      { k: 'enrollment', label: '入组例数', type: 'num' },
      { k: 'elig_sex', label: '性别' },
      { k: 'location_countries', label: '国家数', type: 'count' },
      { k: 'lead_sponsor', label: '主办方' },
      { k: 'matched_terms', label: '命中词', type: 'list' },
      PROV,
    ],
    expand: ['title', 'conditions', 'interventions', 'design_info', 'primary_outcome', 'arm_groups'],
  },
  publications: {
    label: '前沿文献',
    path: '/diseases/{code}/publications',
    filters: { year: '出版年', is_oa: '开放获取' },
    rowKey: 'id',
    link: (row) => (row.pmid ? 'https://pubmed.ncbi.nlm.nih.gov/' + row.pmid + '/'
      : row.doi ? 'https://doi.org/' + row.doi : null),
    cols: [
      { k: 'pub_year', label: '年' },
      { k: 'title', label: '标题', type: 'wide' },
      { k: 'journal', label: '期刊' },
      { k: 'is_oa', label: '开放获取', type: 'flag' },
      { k: 'has_abstract', label: '有摘要', type: 'flag' },
      { k: 'matched_terms', label: '命中词', type: 'list' },
      PROV,
    ],
  },
  targets: {
    label: '关联靶点',
    path: '/diseases/{code}/targets',
    filters: {},
    rowKey: 'ot_id',
    link: (row) => 'https://platform.opentargets.org/target/' + row.ot_id,
    cols: [
      { k: 'approved_symbol', label: '符号' },
      { k: 'approved_name', label: '名称', type: 'wide' },
      { k: 'mounted.score', label: '合成分', type: 'score' },
      { k: 'mounted.novelty', label: '新颖度', type: 'num' },
      { k: 'mounted.datasource_scores', label: '逐源分', type: 'datasources' },
      { k: 'mounted.node_used', label: '查询节点' },
      PROV,
    ],
  },
  drugs: {
    label: '在研药',
    path: '/diseases/{code}/drugs',
    // 这一档的取值是 OT 的 maxClinicalStage（PHASE_2 / PHASE_1_2 这种写法），与试验页那台
    // CT 的 phases（PHASE2）不是同一套词表，所以两台的筛选项在页面上各画各的、不合并。
    filters: { phase: '临床阶段（OT）' },
    rowKey: 'drug_name',
    cols: [
      { k: 'drug_name', label: '药名', type: 'wide' },
      { k: 'phase', label: '最高临床阶段' },
      { k: 'moa', label: '机制', type: 'list' },
      { k: 'drug_id', label: 'DrugBank' },
      PROV,
    ],
  },
}

// 分页列表不许在前端重排：接口给的序在 MySQL 的 utf8mb4_0900_ai_ci 里才是那个序，
// JS 默认按码位排，两边对不上时翻页会看着跳行。所以这里只留列，不留排序器。
