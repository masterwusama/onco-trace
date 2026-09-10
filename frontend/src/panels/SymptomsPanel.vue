<script setup>
import { computed } from 'vue'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import PanelState from '../components/PanelState.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })
const { data, error, loading, load } = useApi('/diseases/{code}/symptoms', () => ({ code: props.code }))
load()

// 一个源一块，块内按源自己的小节分组：并表或跨源去重会让只有英文源的病看起来也有中文名，
// 而 items 的顺序就是接口给的（source_id, heading, name），这里不重排。
const blocks = computed(() => (data.value?.sources || []).map((s) => {
  const groups = []
  for (const it of s.items) {
    const last = groups[groups.length - 1]
    if (last && last.heading === (it.heading || '')) last.items.push(it)
    else groups.push({ heading: it.heading || '', items: [it] })
  }
  return { ...s, groups }
}))

function url(item) {
  return item.source_url ? item.source_url + (item.anchor ? '#' + item.anchor : '') : null
}
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="症状">
    <p class="legend-note">
      {{ num(data.n_sources) }} 个源、各自一块；en {{ num(data.measures?.en) }} 条 / zh
      {{ num(data.measures?.zh) }} 条 / 带频率 {{ num(data.measures?.freq) }} 条。
      名称是源里的说法原样存，不做同义词归并，也不当翻译列读。
    </p>
    <section v-for="b in blocks" :key="b.source.code" class="block">
      <div class="block-head">
        <h3>
          <a v-if="b.source.home_url" :href="b.source.home_url" target="_blank"
             rel="noopener noreferrer">{{ b.source.name }}</a>
          <template v-else>{{ b.source.name }}</template>
        </h3>
        <span class="badge lang">{{ b.name_lang }}</span>
        <span class="badge">{{ b.extract_method }}</span>
        <span class="h">{{ num(b.n_items) }} 条 · {{ b.headings.length }} 小节 ·
          数据集 {{ text(b.dataset?.code) }} {{ text(b.dataset?.upstream_version) }} ·
          许可 {{ text(b.source.license) }}</span>
      </div>
      <p v-if="!Number(b.source.commercial_use)" class="nci">
        该源许可标注为非商用（{{ text(b.source.license) }}），本站只作演示与内部研究。
      </p>

      <template v-for="g in b.groups" :key="g.heading">
        <h4 v-if="g.heading" class="subhead">{{ g.heading }}</h4>
        <table class="grid tight">
          <tbody>
            <tr v-for="it in g.items" :key="it.id">
              <td class="wide">
                {{ text(it.name) }}
                <RouterLink :to="{ name: 'reverse-symptom', params: { name: it.name }, query: { lang: b.name_lang } }"
                            class="rev mono small" title="反查：这个症状挂在哪些病上">反查</RouterLink>
              </td>
              <td style="width: 90px">
                <span v-if="it.freq_band" class="badge">{{ it.freq_band }}</span>
                <span v-else class="nil" title="freq_band 建而不填">—</span>
              </td>
              <td class="mono small" style="width: 120px">{{ text(it.extract_kind) }}</td>
              <td class="mono small" style="width: 92px">{{ text(it.page_lastmod) }}</td>
              <td style="width: 74px">
                <a v-if="url(it)" :href="url(it)" target="_blank" rel="noopener noreferrer">原文 ↗</a>
                <span v-else class="nil">—</span>
              </td>
              <td style="width: 150px">
                <ProvenanceTag :prov="it.provenance" />
                <em v-if="it.derive_marker" class="muted small"> · {{ it.derive_marker }}</em>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>
    <p v-if="data && !blocks.length" class="emptyp">这一病没有未判非的症状行（判非的留在库里做留痕）。</p>
  </PanelState>
</template>
