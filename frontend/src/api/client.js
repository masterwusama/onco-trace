// 前端会碰到的接口路径。这份清单同时是一道契约：api/tests/run.py 拿它去对
// `python -m onco_api routes` 真注册出来的那些路径——拼错、或在飞的一个都不许过；
// 后端注册了而这里没有的路径（跨病榜 /stats/compare 就是）由那道门禁打出来，
// 但门禁不要求两边等长：这里只登记页面真的会打的接口，不替还没画出来的屏占位。
// 路径参数（{code}、{node_id}、{name}、{factor_id}、{ot_id}、{drug_id}）
// 在 get() 里统一替换，其余入参走查询串。
export const API_PATHS = [
  '/meta',
  '/diseases',
  '/diseases/{code}',
  '/diseases/{code}/stats',
  '/diseases/{code}/survival',
  '/diseases/{code}/anatomy',
  '/diseases/{code}/histology',
  '/diseases/{code}/symptoms',
  '/diseases/{code}/risk-factors',
  '/diseases/{code}/trials',
  '/diseases/{code}/publications',
  '/diseases/{code}/targets',
  '/diseases/{code}/drugs',
  '/stats/metrics',
  '/stats/compare',
  '/anatomy/{node_id}/diseases',
  '/symptoms/{name}/diseases',
  '/risk-factors/{factor_id}/diseases',
  '/targets/{ot_id}/diseases',
  '/drugs/{drug_id}/diseases',
]

// 只发 GET：这台后端只注册了 GET，写方法在它面前是 405，而本站没有任何录入通路。
export async function get(tpl, params = {}) {
  if (!API_PATHS.includes(tpl)) throw new Error('未登记的接口路径：' + tpl)
  const used = new Set()
  const path = tpl.replace(/\{(\w+)\}/g, (_, k) => {
    used.add(k)
    return encodeURIComponent(String(params[k] ?? ''))
  })
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (used.has(k)) continue
    // 空串是一个真值，不是"没填"：stat_fact.region 是 NOT NULL DEFAULT ''，
    // "源没有地区列"就按空串存，榜的 region 轴上它是可取的一项。要不发这个参数
    // 就别往 params 里放这个键（各面板把"未选"归成 null，正是这个意思）。
    if (v === undefined || v === null) continue
    qs.set(k, String(v))
  }
  const url = '/api' + path + (qs.toString() ? '?' + qs.toString() : '')
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body)
    } catch { /* 非 JSON 的错误体（网关页之类）只报状态码 */ }
    const err = new Error(res.status + ' ' + url + (detail ? ' · ' + detail : ''))
    err.status = res.status
    err.detail = detail
    err.url = url
    throw err
  }
  return res.json()
}
