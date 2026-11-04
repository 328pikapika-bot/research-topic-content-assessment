<template>
  <figure class="theme-graph">
    <figcaption>研究主题层级</figcaption>
    <ResearchGraphCanvas v-if="hierarchy.length" :nodes="layout.nodes" :edges="layout.edges" :width="layout.width" :height="layout.height" :max-height="420" label="研究主题层级树" :selected-ids="[selected]" @select="select" />
    <p v-else class="caption">主题正在整理。</p>
    <p class="caption">层级表示研究范围的归属，关键词是检索线索。横向联系与方法依据见研究机会分析。</p>
    <div v-if="current" class="theme-detail">
      <h4>{{ current.display_label || current.label }}</h4>
      <p v-if="current.uncertain" class="caption">此分支的层级待确认，保留原始记录。</p>
      <slot name="detail" :node="current"><p>{{ current.boundary }}</p><p>{{ current.discipline_tags?.join('、') }}</p></slot>
      <button v-if="hasChildren(current.theme_id)" type="button" class="branch-toggle" :aria-expanded="expanded.has(current.theme_id)" @click="toggle(current.theme_id)">{{ expanded.has(current.theme_id) ? '收起下级主题' : '展开下级主题' }}</button>
    </div>
  </figure>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { safeThemeHierarchy, graphColors, type GraphNode, type GraphEdge } from '../services/researchGraphs'
import ResearchGraphCanvas from './ResearchGraphCanvas.vue'
const props = defineProps<{ tree: Record<string, any> }>()
const hierarchy = computed(() => safeThemeHierarchy(props.tree))
const selected = ref(''), expanded = ref(new Set<string>())
watch(() => hierarchy.value.map(n => `${n.theme_id}:${n.parent}`).join('|'), () => {
  if (!hierarchy.value.some(n => n.theme_id === selected.value)) selected.value = hierarchy.value.find(n => n.depth === 1)?.theme_id || ''
  hierarchy.value.filter(n => n.depth === 1).forEach(n => expanded.value.add(n.theme_id))
}, { immediate: true })
const current = computed(() => hierarchy.value.find(n => n.theme_id === selected.value))
function hasChildren(id: string) { return hierarchy.value.some(n => n.parent === id) }
function toggle(id: string) { const next = new Set(expanded.value); next.has(id) ? next.delete(id) : next.add(id); expanded.value = next }
function select(id: string) { if (hierarchy.value.some(n => n.theme_id === id)) selected.value = id }
const layout = computed(() => {
  const ordered: any[] = []
  function visit(n: any) { ordered.push(n); if (expanded.value.has(n.theme_id)) hierarchy.value.filter(c => c.parent === n.theme_id).forEach(visit) }
  hierarchy.value.filter(n => n.depth === 1).forEach(visit)
  const deepest = Math.max(1, ...ordered.map(n => n.depth))
  const rowHeights = ordered.map(node => Math.max(96, 54 + Math.ceil(String(node.display_label || node.label || '').length / 15) * 22))
  const rowGap = 22
  const contentHeight = rowHeights.reduce((total, value) => total + value, 0) + Math.max(0, rowHeights.length - 1) * rowGap
  const height = Math.max(300, contentHeight + 48)
  const nodes: GraphNode[] = [{ id: 'root', label: props.tree.display_root || props.tree.root || '研究总方向', subtitle: '研究总方向', x: 20, y: height / 2 - 42, width: 200, height: 84 }]
  const edges: GraphEdge[] = []
  let rowY = (height - contentHeight) / 2
  ordered.forEach((n,i) => {
    const color = graphColors[(n.depth - 1) % graphColors.length]
    const rowHeight = rowHeights[i]
    nodes.push({ id: n.theme_id, label: n.display_label || n.label, subtitle: n.uncertain ? '层级待确认' : `研究主题${hasChildren(n.theme_id) ? ' · 可展开' : ''}`, kind: 'theme', x: 280 + (n.depth - 1) * 270, y: rowY, width: 240, height: rowHeight, color })
    if (!n.uncertain) edges.push({ id: n.theme_id, source: n.parent, target: n.theme_id, color, active: n.theme_id === selected.value })
    if (!hasChildren(n.theme_id) && n.seed_terms?.length) {
      const id = `keywords:${n.theme_id}`
      const keywordHeight = Math.max(90, 48 + Math.ceil(n.seed_terms.join('、').length / 16) * 20)
      nodes.push({ id, label: n.seed_terms.join('、'), subtitle: '关键词', kind: 'keyword', x: 280 + deepest * 270, y: rowY, width: 220, height: keywordHeight, color })
      edges.push({ id, source: n.theme_id, target: id, color, active: n.theme_id === selected.value })
    }
    rowY += rowHeight + rowGap
  })
  return { nodes, edges, height, width: 540 + deepest * 270 }
})
</script>
<style scoped>
.theme-graph{margin:16px 0;min-width:0}.theme-graph figcaption{font-size:15px;font-weight:600}.caption{font-size:14px;color:#69768a;line-height:1.7}.theme-detail{border-top:1px solid #e5e8f0;padding-top:18px}.theme-detail h4{font-size:17px;margin:0 0 10px;color:#263d58}.theme-detail :deep(p){font-size:15px;line-height:1.85;margin:8px 0}.branch-toggle{border:1px solid #dfe4ed;background:white;color:#5364ad;padding:7px 12px;border-radius:6px;cursor:pointer;font-size:14px}
</style>
