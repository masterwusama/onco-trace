import { ref } from 'vue'
import { get } from '../api/client'

// 一次请求一个号：点筛选项快过回包时，旧响应不许盖掉新响应（翻页连点会看着"数据回去了"）。
export function useApi(tpl, paramsFn) {
  const data = ref(null)
  const error = ref(null)
  const loading = ref(false)
  let seq = 0

  async function load() {
    const my = ++seq
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
