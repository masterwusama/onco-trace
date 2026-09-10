import { ref } from 'vue'
import { get } from '../api/client'

// 一次请求一个号：点筛选项快过回包时，旧响应不许盖掉新响应（翻页连点会看着"数据回去了"）。
export function useApi(tpl, paramsFn, opts = {}) {
  const data = ref(null)
  const error = ref(null)
  const loading = ref(false)
  let seq = 0

  async function load() {
    const my = ++seq
    // skip：这一台此刻没东西可问（榜还没选度量就是这种）。跳过时也要把上一次的清掉、
    // 并且顺手让在路上的那一单作废，否则屏幕上留的是旧前提下的结果。
    if (opts.skip && opts.skip()) {
      data.value = null
      error.value = null
      loading.value = false
      return
    }
    loading.value = true
    error.value = null
    try {
      const d = await get(tpl, paramsFn())
      if (my === seq) data.value = d
    } catch (e) {
      if (my === seq) {
        error.value = e
        data.value = null
      }
    } finally {
      if (my === seq) loading.value = false
    }
  }

  return { data, error, loading, load }
}
