export const isNil = (v) => v === null || v === undefined

// 空值一律显示「—」，绝不显示 0：库里 NULL 与 0 是两件事（0 是源给了零，NULL 是源没给），
// 把它们压成同一个字符就是替源撒了个谎。
export const DASH = '—'

export function num(value, digits) {
  if (isNil(value) || value === '') return DASH
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  if (Number.isInteger(n)) return n.toLocaleString('zh-CN')
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits ?? 2,
    maximumFractionDigits: digits ?? 2,
  })
}

export function text(value) {
  return isNil(value) || value === '' ? DASH : String(value)
}

// 出处对象（serialize.py 收拢的那五列）压成一行角标文本：谁给的、哪一版、什么时候进库。
export function provBrief(prov) {
  if (!prov) return DASH
  const src = prov.source?.name || prov.source?.code
  const ds = prov.dataset?.code
  const at = (prov.loaded_at || '').slice(0, 10)
  return [src, ds, at && '装载 ' + at].filter(Boolean).join(' · ')
}

export function provLicense(prov) {
  const s = prov?.source
  if (!s) return ''
  return [s.license, s.attribution_required ? '需署名' : ''].filter(Boolean).join(' / ')
}
