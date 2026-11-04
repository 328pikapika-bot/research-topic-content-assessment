/** User steps are a projection of execution traces; they never rewrite them. */
export interface StepDefinition { key: string; title: string; description: string; artifact: string; completion: string; stages: string[] }
export interface ExecutionTrace { stage_key: string; status: string; progress_percent?: number; duration_seconds?: number | null; prior_duration_seconds?: number; started_at?: string | null; completed_at?: string | null; finished_at?: string | null; updated_at?: string | null; model_status?: string; degradation_reason?: string }
function timestamp(value?: string | null): number {
  if (!value) return NaN
  // Legacy topic timestamps were saved in the server's Beijing local time.
  return Date.parse(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}+08:00`)
}
export function userStepStatusLabel(status: string): string {
  return ({ pending: '等待开始', running: '执行中', completed: '已完成', skipped: '已跳过', degraded: '已完成', blocked: '等待补充', awaiting_confirmation: '等待补充', failed: '执行失败', cancelled: '已取消' } as Record<string, string>)[status] || status
}
export function stepDurationLabel(step: { duration_seconds: number | null; status?: string; timing_pending?: boolean }): string {
  if (step.status === 'pending') return ''
  if (step.status === 'running' && (step.timing_pending || step.duration_seconds == null)) return ' · 正在同步计时'
  if (step.duration_seconds == null) return ''
  const prefix = step.status === 'running' ? '已用时' : '用时'
  return ` · ${prefix}${step.duration_seconds < 1 ? '不足1秒' : `${Math.floor(step.duration_seconds)}秒`}`
}
export const TOPIC_STEPS: StepDefinition[] = [
  { key: 'intent', title: '需求解析', description: '理解研究问题、材料、研究边界和资源约束。', artifact: '研究需求与边界', completion: '研究对象、问题和约束已整理。', stages: ['input_modeling'] },
  { key: 'themes', title: '主题探索', description: '梳理研究方向、主题关系与中英文检索术语。', artifact: '研究主题与关系', completion: '主题及检索术语已形成。', stages: ['theme_decomposition'] },
  { key: 'literature', title: '文献检索', description: '按主题检索、筛选代表性文献并整理可读证据。', artifact: '主题文献与检索记录', completion: '相关文献与证据已整理。', stages: ['retrieval_evidence'] },
  { key: 'discovery', title: '研究机会分析', description: '结合研究趋势、交叉关系和研究空白识别机会。', artifact: '趋势与研究机会', completion: '机会判断及其证据和限制已形成。', stages: ['landscape_trend', 'crosslink_gap'] },
  { key: 'candidates', title: '选题推荐', description: '形成不同的研究方向与研究内容。', artifact: '选题推荐', completion: '候选方向及文献依据已形成。', stages: ['candidate_generation', 'scoring_ranking'] },
  { key: 'report', title: '选题报告', description: '核对引用、生成报告并提供阅读和下载。', artifact: '选题报告', completion: '报告检查完成并已发布。', stages: ['quality_gate', 'report_publish'] },
]
export const ASSESSMENT_STEPS: StepDefinition[] = [
  { key: 'material', title: '材料解析', description: '识别材料类型、研究内容和评估要点。', artifact: '材料与研究要点', completion: '材料与评估要点已整理。', stages: ['document_intake', 'claim_extraction'] },
  { key: 'literature', title: '文献检索', description: '按评估要点检索代表性文献并整理证据。', artifact: '检索主题与代表文献', completion: '文献与证据已整理。', stages: ['retrieval'] },
  { key: 'comparison', title: '相关研究分析', description: '围绕各项研究内容，分析相关文献的方法、研究条件与具体差异。', artifact: '逐项内容与指标对照', completion: '各要点的比较与证据关联已形成。', stages: ['comparison'] },
  { key: 'assessment', title: '创新性评估', description: '分别评价创新性、前沿性和先进性，并分析技术趋势。', artifact: '分类判断与技术趋势', completion: '评价结论及其证据边界已形成。', stages: ['assessment', 'landscape'] },
  { key: 'report', title: '评估报告', description: '核对引用并生成研究型评估报告，提供阅读和下载。', artifact: '评估报告', completion: '报告检查完成并已发布。', stages: ['quality_gate', 'report'] },
]
export function stepForStage(definitions: StepDefinition[], stage: string) {
  return definitions.find(step => step.key === stage || step.stages.includes(stage)) || definitions[0]
}
export function aggregateSteps(definitions: StepDefinition[], traces: ExecutionTrace[], now = Date.now(), live = true, taskStatus = '') {
  return definitions.map(step => {
    const members = step.stages.map(key => traces.find(trace => trace.stage_key === key))
    const statuses = members.map(trace => trace && ['completed', 'skipped'].includes(trace.status)
      && (trace.model_status === 'degraded' || trace.degradation_reason) ? 'degraded' : trace?.status || 'pending')
    const terminal = ['completed', 'skipped', 'degraded']
    const complete = statuses.every(status => terminal.includes(status))
    const derivedStatus = statuses.includes('failed') ? 'failed'
      : statuses.some(value => ['blocked', 'awaiting_confirmation'].includes(value)) ? 'blocked'
      : statuses.includes('running') ? 'running'
      : complete ? statuses.includes('degraded') ? 'degraded' : 'completed'
      : statuses.some(value => terminal.includes(value)) ? 'running' : 'pending'
    const status = !live && derivedStatus === 'running'
      ? ['awaiting_confirmation', 'awaiting_clarification', 'awaiting_quality_continue', 'blocked'].includes(taskStatus) ? 'blocked'
        : ['failed', 'time_out', 'model_unavailable'].includes(taskStatus) ? 'failed'
          : taskStatus === 'cancelled' ? 'cancelled' : derivedStatus
      : derivedStatus
    const progress = Math.round(members.reduce((sum, trace) => sum + (trace
      ? terminal.includes(trace.status) ? 100 : Math.max(0, Math.min(100, trace.progress_percent || 0)) : 0), 0) / members.length)
    const durations = members.flatMap(trace => {
      if (!trace || trace.status === 'pending') return []
      const start = timestamp(trace.started_at)
      const end = timestamp(trace.completed_at || trace.finished_at)
      if (trace.status === 'running' && live && Number.isFinite(start)) return [Math.max(0, trace.prior_duration_seconds || 0) + Math.max(0, (now - start) / 1000)]
      if (trace.duration_seconds != null && Number.isFinite(trace.duration_seconds)) return [Math.max(0, trace.duration_seconds)]
      if (Number.isFinite(start) && Number.isFinite(end)) return [Math.max(0, (end - start) / 1000)]
      // A paused task can retain a running child trace. Never count time after its last saved update.
      const updated = timestamp(trace.updated_at)
      if (!live && Number.isFinite(start) && Number.isFinite(updated)) return [Math.max(0, (updated - start) / 1000)]
      return []
    })
    return { ...step, stage_key: step.key, stage_name: step.title, status, progress_percent: progress,
      timing_pending: status === 'running' && members.some(trace => trace?.status === 'running' && !Number.isFinite(timestamp(trace.started_at))),
      duration_seconds: durations.length ? durations.reduce((sum, duration) => sum + duration, 0) : null }
  })
}

const runMessages: Record<string, string> = {
  input_modeling: '正在理解研究问题并整理研究边界。', theme_decomposition: '正在梳理研究主题与检索方向。',
  document_intake: '正在读取材料并识别评估对象。', claim_extraction: '材料已读取，正在提取研究要点。',
  retrieval: '正按研究要点检索并整理相关研究。', retrieval_evidence: '正按研究主题检索并整理相关文献。',
  comparison: '正在逐项比较研究内容、方法与指标。', assessment: '正在综合分析各项研究结果。',
  landscape: '正在整理相关研究进展与趋势。', landscape_trend: '正在分析研究趋势与方法演进。', crosslink_gap: '正在整理学科联系与研究机会。',
  candidate_generation: '正在形成候选研究方向与研究设计。', scoring_ranking: '正在比较候选方向并整理推荐依据。',
  quality_gate: '正在核对报告内容与文献引用。', report: '正在整理报告并准备预览和下载。', report_publish: '正在整理报告并准备预览和下载。',
}

export function stageRunPresentation(steps: ReturnType<typeof aggregateSteps>, traces: ExecutionTrace[], currentStage: string, taskStatus: string, syncing = false) {
  if (!['running', 'queued'].includes(taskStatus)) return null
  if (taskStatus === 'queued') return { title: '任务已提交，等待开始', description: '开始处理后将显示当前阶段和实时用时。', reference: '', syncing }
  const running = traces.find(trace => trace.stage_key === currentStage && trace.status === 'running') || traces.find(trace => trace.status === 'running')
  const step = steps.find(item => item.stages.includes(running?.stage_key || currentStage)) || steps.find(item => item.status === 'running')
  if (!step) return { title: '正在同步执行进度', description: '已完成的结果可通过上方步骤查看。', reference: '', syncing }
  const early = ['intent', 'themes', 'material'].includes(step.key)
  const report = step.key === 'report'
  const maximum = early ? 90 : 300
  const overdue = step.duration_seconds != null && step.duration_seconds > maximum
  return {
    title: `${overdue ? '仍在处理中' : `正在${step.title}`}${stepDurationLabel(step)}`,
    description: runMessages[running?.stage_key || currentStage] || step.description,
    reference: overdue ? '已完成的内容将陆续展示。' : `参考耗时${early ? '约10–90秒' : report ? '约2–5分钟' : '约1–5分钟'}，随材料及检索情况变化。`,
    syncing,
  }
}
