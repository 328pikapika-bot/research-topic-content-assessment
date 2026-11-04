import { createApp, h, onBeforeUnmount, onMounted, ref } from 'vue'
import TopicThemeGraph from '../../frontend/web/src/components/TopicThemeGraph.vue'
import DisciplineRelationGraph from '../../frontend/web/src/components/DisciplineRelationGraph.vue'
import ClaimEvidenceGraph from '../../frontend/web/src/components/ClaimEvidenceGraph.vue'
import StageRunNotice from '../../frontend/web/src/components/StageRunNotice.vue'
import { ASSESSMENT_STEPS, aggregateSteps, stageRunPresentation, stepDurationLabel } from '../../frontend/web/src/services/userSteps'
import '../../frontend/web/src/styles/workspace-modern.css'

const tree = { root: '计算机科学中的跨学科文献理解', display_root: '跨学科文献理解', nodes: [
  { theme_id: 'T1', label: '跨学科术语对齐', parent_id: 'root', boundary: '比较不同领域中的同义术语和概念边界', discipline_tags: ['计算机科学', '信息科学'], seed_terms: ['术语映射', '语义消歧'] },
  { theme_id: 'T2', label: '证据质量评估', parent_id: 'root', boundary: '核查证据来源和方法适用条件', discipline_tags: ['计算机科学', '科学计量学'], seed_terms: ['证据等级'] },
  { theme_id: 'T3', label: '多语种概念映射', parent_id: 'T1', boundary: '检验多语种表达的一致性', discipline_tags: ['语言学'], seed_terms: ['跨语种检索'] },
  { theme_id: 'T4', label: '概念变化追踪', parent_id: 'T3', boundary: '研究时间维度上的概念漂移', discipline_tags: ['信息科学'], seed_terms: ['语义漂移'] },
] }
const rows = [
  { crosslink_id: 'XL1', core: '计算机科学', expansion: '信息科学', method: '概念对齐', opportunity: '跨学科术语对齐', evidence_ids: ['E1'], grounded: true, association_kind: 'interdisciplinary', number: 1, participating_disciplines: [
    { discipline: '计算机科学', role: 'core' }, { discipline: '信息科学', role: 'expansion' }, { discipline: '语言学', role: 'expansion' }] },
  { crosslink_id: 'XL2', core: '计算机科学', expansion: '科学计量学', method: '引文网络分析', opportunity: '新兴交叉方向发现', evidence_ids: ['E2'], grounded: false, association_kind: 'interdisciplinary', number: 2, participating_disciplines: [
    { discipline: '计算机科学', role: 'core' }, { discipline: '科学计量学', role: 'expansion' }, { discipline: '信息科学', role: 'expansion' }, { discipline: '统计学', role: 'expansion' }] },
  { crosslink_id: 'XL3', core: '计算机科学', expansion: '统计学', method: '不确定性估计', opportunity: '研究证据可信度评估', evidence_ids: ['E3'], grounded: true, association_kind: 'interdisciplinary', number: 3, participating_disciplines: [
    { discipline: '计算机科学', role: 'core' }, { discipline: '统计学', role: 'expansion' }, { discipline: '科学计量学', role: 'expansion' }] },
]
const assessment = { claims: [
  { claim_id: 'C1', text: '提出跨学科术语对齐方法', display_title: '术语对齐方法' },
  { claim_id: 'C2', text: '评估交叉方向发现的可靠性', display_title: '交叉方向可靠性' },
  { claim_id: 'C3', text: '研究尚未有可比证据的指标', display_title: '待验证指标' },
], evidence_records: [
  { evidence_id: 'E1', title: '跨学科术语映射研究', abstract: '比较了两个领域的术语语义映射。' },
  { evidence_id: 'E2', title: '引文网络中的交叉方向发现', abstract: '通过引文关系观察领域交叉。' },
  { evidence_id: 'E3', title: '统计不确定性估计', abstract: '讨论了评估区间与可靠性。' },
  ...Array.from({ length: 6 }, (_, index) => ({
    evidence_id: `E${index + 4}`,
    title: `跨学科要点参考文献 ${index + 4}`,
    abstract: '用于检查多篇关联文献时的分栏与展开交互。',
  })),
], claim_evidence_links: [
  { claim_id: 'C1', evidence_id: 'E1', relation: 'supports' },
  { claim_id: 'C1', evidence_id: 'E2', relation: 'partially_supports' },
  { claim_id: 'C2', evidence_id: 'E2', relation: 'partially_supports' },
  { claim_id: 'C2', evidence_id: 'E3', relation: 'supports' },
  ...Array.from({ length: 6 }, (_, index) => ({ claim_id: 'C1', evidence_id: `E${index + 4}`, relation: 'supports' })),
] }
createApp({ setup() {
  const selectedOpportunity = ref('XL2'), selectedPoint = ref('C1')
  const now = ref(Date.now())
  const startedAt = new Date(now.value - 12000).toISOString()
  let timer: ReturnType<typeof setInterval> | undefined
  onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
  onBeforeUnmount(() => { if (timer) clearInterval(timer) })
  return () => h('main', { class: 'demo' }, [
    h('section', { class: 'card' }, [
      h('h2', '执行进度 · 文献检索'),
      h('p', `文献检索：执行中${stepDurationLabel(aggregateSteps(ASSESSMENT_STEPS, [{ stage_key: 'retrieval', status: 'running', started_at: startedAt }], now.value)[1])}`),
      h(StageRunNotice, { notice: stageRunPresentation(aggregateSteps(ASSESSMENT_STEPS, [{ stage_key: 'retrieval', status: 'running', started_at: startedAt }], now.value), [{ stage_key: 'retrieval', status: 'running', started_at: startedAt }], 'retrieval', 'running') }),
    ]),
    h('section', { class: 'card' }, [h('h2', '主题探索 · 研究主题与边界'), h(TopicThemeGraph, { tree })]),
    h('section', { class: 'card' }, [h('h2', '研究机会分析 · 方法演进之后'), h(DisciplineRelationGraph, { root: tree.display_root, rows, selected: selectedOpportunity.value, onSelect: (v: string) => selectedOpportunity.value = v }), h('p', `选中机会：${selectedOpportunity.value}`)]),
    h('section', { class: 'card' }, [h('h2', '相关研究分析 · 逐项分析之前'), h(ClaimEvidenceGraph, { state: assessment, selected: selectedPoint.value, onSelect: (v: string) => selectedPoint.value = v }), h('p', `选中要点：${selectedPoint.value}`)]),
  ])
} }).mount('#app')
