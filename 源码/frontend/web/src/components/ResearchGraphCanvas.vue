<template>
  <div class="research-graph" :class="{ expanded }">
    <div class="graph-scroll" :style="{ maxHeight: expanded ? 'none' : `${maxHeight}px` }" tabindex="0" :aria-label="`${label}，可滚动查看`">
      <div class="graph-canvas" :style="{ width: `${width}px`, height: `${height}px` }" role="group" :aria-label="label">
        <svg :viewBox="`0 0 ${width} ${height}`" aria-hidden="true">
          <path v-for="edge in paths" :key="edge.id" :d="edge.path" fill="none" :stroke="edge.color" :stroke-width="edge.active ? 3.4 : 2" :opacity="edge.active ? 1 : .72" :stroke-dasharray="edge.dashed ? '6 5' : undefined" />
        </svg>
        <button v-for="node in nodes" :key="node.id" type="button" class="graph-node" :class="[node.kind, { selected: selectedIds.includes(node.id) }]"
          :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${node.width}px`, height: `${node.height}px`, '--node-color': node.color || '#6575c7' }"
          :title="[node.label, node.subtitle].filter(Boolean).join(' · ')" :aria-pressed="selectedIds.includes(node.id)" @click="$emit('select', node.id)">
          <strong>{{ node.label }}</strong><small v-if="node.subtitle">{{ node.subtitle }}</small>
        </button>
      </div>
    </div>
    <button v-if="height > maxHeight" class="expand-graph" type="button" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起图形' : '展开查看完整图形' }}</button>
  </div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { graphPath, type GraphNode, type GraphEdge } from '../services/researchGraphs'
const props = withDefaults(defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; width: number; height: number; label: string; selectedIds?: string[]; maxHeight?: number }>(), { selectedIds: () => [], maxHeight: 480 })
defineEmits<{ select: [id: string] }>()
const expanded = ref(false)
const paths = computed(() => {
  const nodes = new Map(props.nodes.map(n => [n.id, n]))
  return props.edges.flatMap(e => { const a = nodes.get(e.source), b = nodes.get(e.target); return a && b ? [{ ...e, path: graphPath(a, b) }] : [] })
})
</script>
<style scoped>
.research-graph{min-width:0;max-width:100%;margin:14px 0}.graph-scroll{overflow:auto;border-radius:8px;background:#f8f9fc;overscroll-behavior-x:contain;overscroll-behavior-y:auto}.graph-scroll:focus-visible{outline:2px solid #6575c7}.graph-canvas{position:relative;margin:auto}.graph-canvas svg{position:absolute;width:100%;height:100%;inset:0;pointer-events:none}.graph-node{position:absolute;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;gap:5px;padding:10px 14px;border:1px solid #dfe4ed;border-left:3px solid var(--node-color);border-radius:8px;background:#fff;color:#303d54;font:inherit;text-align:center;cursor:pointer}.graph-node strong{font-size:14px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.graph-node.opportunity strong,.graph-node.theme strong{display:block;overflow:visible}.graph-node small{font-size:12px;color:#66748a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.graph-node.discipline{border-radius:40px}.graph-node.keyword{border-left-width:1px;background:#f2f5fb;height:auto}.graph-node.keyword strong{font-size:13px;font-weight:400}.graph-node.selected{border-color:var(--node-color);background:color-mix(in srgb,var(--node-color) 10%,white);box-shadow:0 0 0 1px var(--node-color)}.graph-node:focus-visible{outline:3px solid #6575c7;outline-offset:3px}.expand-graph{margin:8px 0;padding:6px 10px;border:1px solid #dfe4ed;border-radius:6px;background:white;color:#5263ac;cursor:pointer;font-size:12px}
</style>
