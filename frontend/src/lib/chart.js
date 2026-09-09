// 图表只在 lib 层做一件事：把"一条线=一个口径"这件事画得无法误解。
// 数值、单位、出处一律由接口给，这里不补任何默认口径；每张图旁边面板都配了同数据的表，
// 图只是让人看见形状，不是唯一读数途径。
export const COLOR_OBSERVED = '#2d6cdf'
export const COLOR_FITTED = '#b8860b'
export const PALETTE = ['#2d6cdf', '#18a058', '#d03050', '#7c5cff', '#0f9bb8', '#e07b39', '#8a94a6']

export function lineSeries(name, points, opts = {}) {
  const data = points.map((p) => [p.year, p.value])
  return {
    name,
    type: 'line',
    data,
    smooth: false,
    showSymbol: data.length <= 40,
    symbolSize: 4,
    lineStyle: { width: opts.width || 1.8, type: opts.dashed ? 'dashed' : 'solid', color: opts.color },
    itemStyle: { color: opts.color },
    ...opts.extra,
  }
}

// x 轴是"值"轴不是"类目"轴：源按分段发布，中间可以整年没有值。
// 类目轴会把缺档压成相邻两点，一条断线就被画成了连续趋势。
export function baseOption({ series, yName = '', xName = '年', legend = true, yFmt = null }) {
  return {
    animation: false,
    // 图例独占一行：y 轴单位名画在 grid.top - nameGap 处，不给够间距就会和图例首项叠在同一行上。
    grid: { left: 56, right: 18, top: legend ? 46 : 12, bottom: 26 },
    color: PALETTE,
    legend: legend
      ? { type: 'scroll', top: 0, left: 0, itemWidth: 14, textStyle: { fontSize: 11, color: '#8a94a6' } }
      : { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      textStyle: { fontSize: 12 },
    },
    // scale:true 是必须的——值轴默认把 0 含进区间，1975–2024 的年就会摊在 0–2500 上，
    // 一条真实的趋势线会被压成贴右边缘的一根刺。
    xAxis: {
      type: 'value',
      scale: true,
      name: xName,
      nameGap: 6,
      minInterval: 1,
      axisLabel: { fontSize: 11, color: '#8a94a6', formatter: (v) => String(v) },
      axisLine: { lineStyle: { color: '#e3e8ef' } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: yName,
      nameGap: 22,
      nameTextStyle: { fontSize: 11, color: '#8a94a6', align: 'left' },
      axisLabel: { fontSize: 11, color: '#8a94a6', formatter: yFmt || (v => v) },
      splitLine: { lineStyle: { color: '#eef1f6' } },
    },
    series,
  }
}
