<template>
  <section class="claim-graph" aria-label="要点与文献对应图">
    <header class="graph-heading"><h4>要点与相关文献</h4><p class="guide">选择要点查看文献，逐项比较见下方。</p></header>
    <div class="point-selector" role="group" aria-label="选择研究要点">
      <button v-for="point in data.points" :key="point.id" type="button" :aria-pressed="currentPoint?.id === point.id" :class="{ active: currentPoint?.id === point.id }" @click="emit('select', point.id)">{{ point.label }}</button>
    </div>
    <p v-if="!data.points.length" class="guide">正在整理研究要点。</p>
    <div v-if="currentPoint" ref="canvas" class="focused-network" :class="{ 'is-list': columns === 1 }" :style="{ '--columns': columns }">
      <svg v-if="columns > 1" class="network-lines" aria-hidden="true"><path v-for="(path, index) in paths" :key="index" :d="path" /></svg>
      <div class="point-hub"><strong>{{ currentPoint.label }}</strong><span>{{ relatedEvidence.length }} 篇关联文献</span></div>
      <div class="evidence-grid">
        <article v-for="item in visibleEvidence" :key="item.id" :class="{ selected: evidenceId === item.id }" class="evidence-node">
          <small v-if="recordFor(item.id)?.year || item.number">{{ recordFor(item.id)?.year || '' }}{{ item.number ? ' · [' + item.number + ']' : '' }}</small>
          <a v-if="sourceUrl(item.id)" :href="sourceUrl(item.id)" target="_blank" rel="noopener noreferrer">{{ item.label }}</a><strong v-else>{{ item.label }}</strong>
          <span v-if="recordFor(item.id)?.authors?.length" class="authors">{{ recordFor(item.id)?.authors?.slice(0, 3).join('、') }}</span>
          <button type="button" class="source-toggle" :aria-expanded="evidenceId === item.id" @click="evidenceId = evidenceId === item.id ? '' : item.id">{{ evidenceId === item.id ? '收起关联内容' : '查看关联内容' }}</button>
        </article>
      </div>
      <p v-if="!relatedEvidence.length" class="guide">该要点的关联文献正在整理，已有分析可在下方查看。</p>
    </div>
    <button v-if="relatedEvidence.length > 6" type="button" class="source-toggle expand-sources" :aria-expanded="showAll" @click="showAll = !showAll">{{ showAll ? '收起其余关联文献' : '展开其余关联文献（' + (relatedEvidence.length - 6) + ' 篇）' }} <span aria-hidden="true">{{ showAll ? '⌃' : '⌄' }}</span></button>
    <div v-if="source" class="source-detail"><LiteratureReferences :items="[source]" /><ResultDisclosure v-if="sourceDetail.excerpt || sourceDetail.location" :key="source.evidence_id" label="查看文献内容" collapse-label="收起文献内容"><p>{{ sourceDetail.sourceKind }}<span v-if="sourceDetail.location"> · {{ sourceDetail.location }}</span></p><blockquote v-if="sourceDetail.excerpt">{{ sourceDetail.excerpt }}</blockquote></ResultDisclosure></div>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import LiteratureReferences from './LiteratureReferences.vue'
import ResultDisclosure from './ResultDisclosure.vue'
import { assessmentGraphData } from '../services/researchGraphs'
import { comparisonEvidenceDetail } from '../services/comparisonEvidencePresentation'
import { resolveEvidenceUrl } from '../services/evidenceLinks'
const props = defineProps<{ state: Record<string, any>; selected: string; summary?: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const data = computed(() => assessmentGraphData(props.state))
const evidenceId = ref(''), showAll = ref(false)
const currentPoint = computed(() => data.value.points.find((p: any) => p.id === props.selected) || data.value.points[0])
const relatedEvidence = computed(() => {
  const ids = new Set(data.value.links.filter((link: any) => link.source === currentPoint.value?.id).map((link: any) => link.target))
  return data.value.evidence.filter((item: any) => ids.has(item.id))
})
const visibleEvidence = computed(() => showAll.value ? relatedEvidence.value : relatedEvidence.value.slice(0, 6))
function recordFor(id: string) { return (props.state.evidence_records || []).find((record: any) => record.evidence_id === id) }
function sourceUrl(id: string) { const record = recordFor(id); return record ? resolveEvidenceUrl(record) : '' }
const source = computed(() => relatedEvidence.value.some((item: any) => item.id === evidenceId.value) ? recordFor(evidenceId.value) : null)
const sourceDetail = computed(() => comparisonEvidenceDetail((props.state.claim_evidence_links || []).find((link: any) => link.claim_id === currentPoint.value?.id && link.evidence_id === evidenceId.value) || {}, props.state.evidence_records || [], props.state.evidence_chunks || [], (props.state.claims || []).find((claim: any) => claim.claim_id === currentPoint.value?.id)))
watch(() => currentPoint.value?.id, () => { evidenceId.value = ''; showAll.value = false })
watch(() => currentPoint.value?.id, () => { if (currentPoint.value && currentPoint.value.id !== props.selected) emit('select', currentPoint.value.id) }, { immediate: true })
const canvas = ref<HTMLElement | null>(null), columns = ref(3), paths = ref<string[]>([])
let observer: ResizeObserver | undefined, disposed = false
function measure() {
  if (!canvas.value || disposed) return
  const root = canvas.value.getBoundingClientRect()
  const count = root.width <= 480 ? 1 : root.width < 760 ? 2 : 3
  if (columns.value !== count) { columns.value = count; void nextTick(measure); return }
  const hub = canvas.value.querySelector('.point-hub')?.getBoundingClientRect()
  if (!hub || count === 1) { paths.value = []; return }
  const x = hub.left + hub.width / 2 - root.left, y = hub.bottom - root.top
  paths.value = Array.from(canvas.value.querySelectorAll('.evidence-node')).map(node => {
    const rect = node.getBoundingClientRect(), tx = rect.left + rect.width / 2 - root.left, ty = rect.top - root.top
    return 'M ' + x + ' ' + y + ' V ' + (ty - 15) + ' H ' + tx + ' V ' + ty
  })
}
watch([canvas, visibleEvidence], async () => { await nextTick(); observer?.disconnect(); if (canvas.value) observer?.observe(canvas.value); measure() }, { flush: 'post' })
onMounted(() => { observer = new ResizeObserver(measure); if (canvas.value) observer.observe(canvas.value); measure() })
onBeforeUnmount(() => { disposed = true; observer?.disconnect() })
</script>
<style scoped>
.claim-graph { min-width:0; max-width:100%; margin-bottom:28px; font-family:inherit; color:#26364b; }
.claim-graph .graph-heading { display:flex; align-items:baseline; flex-wrap:wrap; gap:6px 18px; margin:0 0 16px; }
.claim-graph .graph-heading h4 { display:block; margin:0; font-family:inherit; font-size:17px; line-height:1.6; font-weight:600; letter-spacing:normal; }
.claim-graph .guide { margin:0; font-size:14px; font-weight:400; color:#69768a; line-height:1.7; }
.point-selector { display:flex; flex-wrap:wrap; gap:10px; margin:0 0 24px; }
.point-selector button { font:inherit; font-size:16px; font-weight:400; line-height:1.65; text-align:left; padding:9px 14px; background:white; border:1px solid #dce3ed; border-radius:6px; color:#485971; cursor:pointer; overflow-wrap:anywhere; }
.point-selector button.active { color:#245d92; font-weight:500; background:#eef5fc; border-color:#91b1d1; }
.point-selector button:focus-visible,.source-toggle:focus-visible { outline:2px solid #5269c7; outline-offset:3px; }
.focused-network { position:relative; isolation:isolate; min-width:0; }
.network-lines { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; z-index:-1; overflow:visible; }
.network-lines path { fill:none; stroke:#bdcde0; stroke-width:1.5; }
.point-hub { display:flex; flex-direction:column; gap:5px; max-width:480px; margin:0 auto 32px; padding:14px 20px; border:1px solid #bccde2; border-radius:8px; background:#f2f6fc; line-height:1.7; text-align:center; box-sizing:border-box; }
.point-hub strong { font-size:16px; font-weight:500; overflow-wrap:anywhere; }
.point-hub span { font-size:13px; font-weight:400; color:#6a788a; }
.evidence-grid { display:grid; grid-template-columns:repeat(var(--columns),minmax(0,1fr)); gap:32px 16px; }
.evidence-node { display:flex; flex-direction:column; align-items:flex-start; gap:7px; min-width:0; padding:16px; background:white; border:1px solid #e0e6ef; border-radius:7px; line-height:1.7; }
.evidence-node.selected { border-color:#739fce; background:#f8fbff; }
.evidence-node small,.authors { font-size:13px; font-weight:400; color:#69768a; }
.evidence-node a,.evidence-node>strong { font-family:inherit; font-size:16px; line-height:1.7; overflow-wrap:anywhere; text-decoration:none; font-weight:500; }
.evidence-node a { color:#245d92; }
.evidence-node a:hover { text-decoration:underline; }
.source-toggle { border:0; padding:0; background:none; color:#245d92; font:inherit; font-size:14px; cursor:pointer; text-align:left; line-height:1.7; }
.expand-sources { margin-top:16px; }
.source-detail { font-size:15px; line-height:1.8; margin-top:18px; }
.source-detail blockquote { margin:10px 0; padding-left:14px; border-left:2px solid #b7c9dc; white-space:pre-wrap; overflow-wrap:anywhere; }
.is-list .point-hub { margin-bottom:14px; text-align:left; }
.is-list .evidence-grid { gap:0; }
.is-list .evidence-node { border:0; border-radius:0; border-bottom:1px solid #e5eaf1; padding:14px 0; }
@media(max-width:480px) { .point-selector { gap:8px; margin-bottom:20px; } .point-selector button { width:100%; } }
</style>
