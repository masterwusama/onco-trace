<script setup>
import { ref, watch } from 'vue'
import { get } from '../api/client'
import { useApi } from '../lib/useApi'
import { num, text } from '../lib/format'
import PanelState from '../components/PanelState.vue'
import ProvenanceTag from '../components/ProvenanceTag.vue'

const props = defineProps({ code: { type: String, required: true }, detail: { type: Object, default: null } })
const { data, error, loading, load } = useApi('/diseases/{code}/histology', () => ({ code: props.code }))

// 组档是聚合，出处不在这一层；点一档才发第二次请求取那一档的码行（?group=），
// 那一批行才有两份出处。默认响应刻意不含码行——208 行摊在组档上面会把聚合与事实混成一张表。
const picked = ref(null)
const drill = ref(null)
const drillErr = ref(null)
const drillLoading = ref(false)
let seq = 0

// 换病要先把下钻态清掉，否则上一病的档还挂在屏幕上，而表里已经是另一个病的组码。
// 这一条必须在上面那几个 ref 之后：immediate 的回调在 setup 里同步跑，早于声明就是 TDZ。
watch(() => props.code, () => { picked.value = null; drill.value = null; drillErr.value = null; load() }, { immediate: true })

async function pick(group) {
  picked.value = group
  drill.value = null
  drillErr.value = null
  if (!group) return
  const my = ++seq
  drillLoading.value = true
  try {
    const d = await get('/diseases/{code}/histology', { code: props.code, group })
    if (my === seq) drill.value = d
  } catch (e) {
    if (my === seq) drillErr.value = e
  } finally {
    if (my === seq) drillLoading.value = false
  }
}
</script>

<template>
  <PanelState :loading="loading" :error="error" :data="data" note="组织学">
    <p class="legend-note">
      {{ num(data.n_groups) }} 个三位组码档 · 共 {{ num(data.measures?.codes) }} 个形态学码。
      点一行取那一档的码行；组档本身是聚合，所以不带出处。
    </p>
    <table class="grid tight">
      <thead>
        <tr>
          <th>group_code</th><th>group_label</th>
          <th class="n">label_variants</th><th class="n">codes</th><th class="n">via_recodes</th>
          <th class="n">mount_releases</th><th class="n">code_releases</th><th class="n">bases</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="g in data.groups" :key="g.group_code"
            class="clickable" :class="{ on: picked === g.group_code }"
            @click="pick(picked === g.group_code ? null : g.group_code)">
          <td class="mono">{{ text(g.group_code) }}</td>
          <td>{{ text(g.group_label) }}</td>
          <td class="n">{{ num(g.label_variants) }}</td>
          <td class="n">{{ num(g.codes) }}</td>
          <td class="n">{{ num(g.via_recodes) }}</td>
          <td class="n">{{ num(g.mount_releases) }}</td>
          <td class="n">{{ num(g.code_releases) }}</td>
          <td class="n">{{ num(g.bases) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="picked" class="drill">
      <div class="drill-head">
        <b>档 {{ picked }}</b>
        <span v-if="drillLoading">读取中…</span>
        <span v-else-if="drillErr" class="error">{{ drillErr.status }} {{ drillErr.detail || drillErr.message }}</span>
        <template v-else-if="drill">
          <span>{{ num(drill.n_codes) }} 个码 · 每行两份出处（码表一行 / 逐病展开一行）</span>
          <button @click="pick(null)">收起</button>
        </template>
      </div>
      <p v-if="drill?.note" class="dimnote">{{ drill.note }}</p>
      <table v-if="drill" class="grid tight">
        <thead>
          <tr><th>code_behavior</th><th>label</th><th>behavior</th>
              <th>via_recode</th><th>basis</th><th>码表出处</th><th>挂载出处</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in drill.codes" :key="c.id">
            <td class="mono">{{ text(c.code_behavior) }}</td>
            <td>{{ text(c.label) }}</td>
            <td class="mono">{{ text(c.behavior) }}</td>
            <td class="mono trunc" :title="String(c.mounted?.via_recode)">{{ text(c.mounted?.via_recode) }}</td>
            <td><span class="badge">{{ text(c.mounted?.basis) }}</span></td>
            <td><ProvenanceTag :prov="c.provenance" /></td>
            <td><ProvenanceTag :prov="c.mounted?.provenance" /></td>
          </tr>
        </tbody>
      </table>
    </div>
  </PanelState>
</template>
