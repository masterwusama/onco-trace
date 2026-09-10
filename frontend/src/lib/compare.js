// 跨病榜的四个口径轴。键名就是接口的参数名（也是 stat_fact 的列名）——
// 分面轴名与参数名必须同一个名字，否则前端按参数名过白名单会把选项丢掉。
export const AXES = [
  { k: 'region', label: '地区' },
  { k: 'estimate_basis', label: '估算依据' },
  { k: 'sex', label: '性别' },
  { k: 'age_band', label: '年龄组' },
]

// 取值一律用库里的原值，不翻译不合并：`both` 与 `national_estimate` 是列上的枚举，
// 给它们换中文说法就会造出一个页面上有、库里没有的档。只有空值得解释一句——
// stat_fact.region 是 NOT NULL DEFAULT ''，'' 说的是"这一路的源根本没有地区这一列"。
export function valueLabel(v) {
  return v === '' ? '（空：这一路的源没有这一列）' : String(v)
}

// year=0 不是公元 0 年，是"源没给年份"的单点（国家级估算、年龄组构成、查询计数）。
export function yearLabel(y) {
  return Number(y) === 0 ? '无年份（单点）' : String(y)
}

// 一跨度里两端都可能是那个哨兵，直接拼会写成「无年份（单点）–无年份（单点）」。
export function spanLabel(a, b) {
  if (Number(a) === 0 && Number(b) === 0) return '只有无年份单点'
  if (Number(a) === 0) return `无年份单点–${b}`
  return `${a}–${b}`
}
