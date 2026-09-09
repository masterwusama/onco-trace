// 前端会碰到的接口路径。这份清单同时是一道契约：api/tests/run.py 拿它去对
// `python -m onco_api routes` 真注册出来的那些路径——拼错、或在飞的一个都不许过；
// 后端注册了而这里没有的路径（跨病榜 /stats/compare 就是）由那道门禁打出来，
// 但门禁不要求两边等长：这里只登记页面真的会打的接口，不替还没画出来的屏占位。
// 唯一的路径参数是 {code}，值一律取 disease.code（同时是前端 slug）。
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
]

// 只发 GET：这台后端只注册了 GET，写方法在它面前是 405，而本站没有任何录入通路。
export async function get(tpl, params = {}) {
  const path = tpl.replace('{code}', params.code)
  if (!API_PATHS.includes(tpl)) throw new Error('未登记的接口路径：' + tpl)
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (k === 'code') continue
    if (v === undefined || v === null || v === '') continue
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
