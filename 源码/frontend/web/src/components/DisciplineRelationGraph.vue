<template>
  <section class="opportunity-network" aria-label="学科与研究方向关联图">
    <h4>研究方向关联图</h4><p class="guide">{{ root }} · 同色连线关联同一研究方向及其参与学科；点击方向查看下方说明。</p>
    <ResearchGraphCanvas v-if="rows.length" :nodes="layout.nodes" :edges="layout.edges" :width="960" :height="layout.height" label="学科与研究方向关联图" :selected-ids="layout.selected" @select="select" />
    <div v-if="rows.length" class="legend" aria-label="研究方向图例"><span v-for="row in visible" :key="row.crosslink_id" :style="{ color: color(row) }">● {{ row.opportunity || '研究方向' }}</span></div>
  </section>
</template>
<script setup lang="ts">
import { computed, watch } from 'vue'
import type { DiscoveryRow } from '../services/topicDiscovery'
import { graphColors, participants, type GraphNode, type GraphEdge } from '../services/researchGraphs'
import ResearchGraphCanvas from './ResearchGraphCanvas.vue'
const props = defineProps<{ root: string; rows: DiscoveryRow[]; selected: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const visible = computed(() => props.rows.slice(0, 3))
function number(row: DiscoveryRow) { return visible.value.indexOf(row) + 1 }
function color(row: DiscoveryRow) { return row.color || graphColors[(number(row) - 1) % graphColors.length] }
watch(() => props.rows.map(r => r.crosslink_id).join('|'), () => {
  if (!visible.value.some(r => r.crosslink_id === props.selected)) emit('select', visible.value[0]?.crosslink_id || '')
}, { immediate: true })
const layout = computed(() => {
  const core = [...new Set(visible.value.flatMap(row => participants(row).filter((p: any) => p.role === 'core').map((p: any) => p.discipline)))]
  const expansion = [...new Set(visible.value.flatMap(row => participants(row).filter((p: any) => p.role !== 'core').map((p: any) => p.discipline)))]
  const opportunityHeights = visible.value.map(row => {
    const length = String(row.opportunity || row.method || '研究方向').length
    return Math.max(104, 52 + Math.max(2, Math.ceil(length / 23)) * 23)
  })
  const opportunityGap = 24
  const opportunityTotal = opportunityHeights.reduce((total, value) => total + value, 0) + Math.max(0, opportunityHeights.length - 1) * opportunityGap
  const height = Math.max(390, core.length * 90 + 50, expansion.length * 86 + 50, opportunityTotal + 60)
  const nodes: GraphNode[] = [
    ...core.map((name, i) => ({ id: `core:${name}`, label: name, subtitle: '核心学科', kind: 'discipline', x: 20, y: (height - core.length * 90) / 2 + i * 90, width: 190, height: 68 })),
    ...expansion.map((name, i) => ({ id: `expansion:${name}`, label: name, subtitle: '拓展学科', kind: 'discipline', x: 735, y: (height - expansion.length * 86) / 2 + i * 86, width: 205, height: 68 })),
  ]
  const edges: GraphEdge[] = []
  let opportunityY = (height - opportunityTotal) / 2
  visible.value.forEach((row, i) => {
    const nodeHeight = opportunityHeights[i]
    nodes.push({ id: row.crosslink_id, label: row.opportunity || row.method || '研究方向', subtitle: row.method || undefined, kind: 'opportunity', x: 300, y: opportunityY, width: 390, height: nodeHeight, color: color(row) })
    participants(row).forEach((p: any, j: number) => edges.push({ id: `${row.crosslink_id}:${j}`, source: p.role === 'core' ? `core:${p.discipline}` : row.crosslink_id, target: p.role === 'core' ? row.crosslink_id : `expansion:${p.discipline}`, color: color(row), dashed: false, active: row.crosslink_id === props.selected }))
    opportunityY += nodeHeight + opportunityGap
  })
  return { nodes, edges, height, selected: [props.selected, ...edges.filter(e => e.active).flatMap(e => [e.source, e.target])] }
})
function select(id: string) { if (props.rows.some(r => r.crosslink_id === id)) emit('select', id) }
</script>
<style scoped>
.opportunity-network{min-width:0;margin:18px 0}.opportunity-network h4{font-size:15px;margin:0 0 8px}.guide{font-size:13px;color:#69768a;line-height:1.7;margin:8px 0}.legend{display:flex;flex-direction:column;gap:6px;margin-top:10px;font-size:13px;line-height:1.6}.legend span{display:block;overflow-wrap:anywhere}.paging{display:flex;align-items:center;justify-content:flex-end;gap:12px;font-size:12px}.paging button{background:white;color:#5364ad;border:1px solid #dfe4ed;border-radius:6px;padding:7px 12px;cursor:pointer}.paging button:disabled{opacity:.4;cursor:default}
</style>
