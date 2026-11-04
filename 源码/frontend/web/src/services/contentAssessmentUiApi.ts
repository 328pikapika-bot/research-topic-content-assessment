import {
  deleteContentAssessmentTask as deleteRawTask,
  downloadReport as downloadRawReport,
  fetchReportBlob as fetchRawReportBlob,
  getContentAssessmentTask as getRawTask,
  listContentAssessmentTasks as listRawTasks,
  listContentAssessmentReviews as listRawReviews,
  runContentAssessment as runRawAssessment,
  resolveContentAssessmentReview as resolveRawReview,
  stopContentAssessmentTask as stopRawTask,
  submitContentAssessmentConfirmation as submitRawConfirmation,
  type ContentAssessmentRunResponse as RawRunResponse,
  type ContentAssessmentState as RawState,
  type ContentAssessmentTaskDetail as RawTaskDetail,
  type ContentAssessmentTaskHistoryItem as RawTaskHistoryItem,
  type EvidenceRecord as RawEvidenceRecord,
  type ReviewDecision,
  type ReviewTask,
  type ResolutionLog,
  type UploadedFileRef as RawUploadedFileRef,
  type ReportMode,
  type ArtifactType,
} from './contentAssessmentApi'
import { assessmentResultState } from './resultSnapshot'
import { buildClaimEvidencePresentation, type ClaimEvidencePresentation } from './assessmentEvidencePresentation'

export type UploadedFileRef = RawUploadedFileRef

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/service/nstl/ai/agent/content-assessment').replace(/\/$/, '')
const NSTL_WEB_BASE_URL = '/api/service/nstl/web'

export interface StageTraceItem {
  stage_key: string
  stage_name: string
  status: string
  summary: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
}

export interface ProcessTraceStep {
  step_key: string
  title: string
  status: string
  summary: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  evidence_refs?: string[]
}

export interface EvidenceItem {
  evidence_id: string
  paper_id?: string
  source_type?: string
  title: string
  authors?: string[]
  year: number
  venue: string
  doi?: string
  url?: string
  nstl_url?: string
  source_url?: string
  language?: string
  abstract?: string
  score?: number
  retrieval_query?: string
  query_group?: string
  claim_ids?: string[]
  evidence_level?: string
  support_summary: string
}

export interface FinalQuestion {
  question_id: string
  rank: number
  title: string
  question: string
  recommendation_reason: string
  recommendation_level?: string
  score: {
    total_score: number
    score_reason: string
    novelty: number
    researchability: number
    evidence_support: number
    clarity: number
    domain_value: number
    feasibility: number
  }
  evidence_refs: string[]
  next_steps: string[]
}

export interface ContentAssessmentReport {
  ready: boolean
  title: string
  version: string
  type: string
  executiveSummary: string
  assessmentObject: string
  scopeBoundary: string
  evidenceCount: number
  claimCount: number
  sectionQuotaCompletion?: number
  claimCoverage?: number
  formalClaimCoverage?: number
  directionalClaimCoverage?: number
  indicatorCoverage?: number
  citationIntegrity?: number
  deficiencies?: string[]
  bodyWords?: number
  appendixWords?: number
  bodyPages?: number
  appendixPages?: number
  bodyRatio?: number
  appendixRatio?: number
  appendixCompressionStatus?: string
  longReportStatus?: string
  estimatedPages?: number
  actualPageCount?: number | null
  reportMode?: 'standard' | 'deep'
  recommendedMode?: 'standard' | 'deep'
  artifactType?: string
  scopeSummary?: string
  pageCountVerified?: boolean
  pageTargetStatus?: string
  pageTargetReason?: string
  evidenceStrength?: 'high' | 'medium' | 'low'
  evidenceStrengthReason?: string
  citationNumbers?: Record<string, number>
  autoDetection?: Record<string, unknown>
  sectionSpecs?: Array<{
    section_key: string
    title: string
    target_words: number
    status: string
    order?: number
    level?: number
    is_appendix?: boolean
    content?: string
    evidence_refs?: string[]
    actual_words?: number
    actual_evidence_refs?: number
    target_evidence_refs?: number
  }>
  qualityGate: {
    eligibility: string
    passed: boolean
    reviewRequired: boolean
    blockers: string[]
    warnings: string[]
  }
  claims: Array<{
    id: string
    text: string
    label: string
    rationale: string
    evidenceRefs: string[]
    risks: string[]
    rewriteAdvice: string
    evidenceRelation: ClaimEvidencePresentation
  }>
  scorecard: Array<{
    dimension: string
    assessment: string
    scoreRange: string
    basis: string[]
    uncertainty: string
  }>
  trend: {
    summary: string
    highlights: string[]
    competition: string[]
    risks: string[]
  }
}

export interface ContentAssessmentOutputJson {
  summary?: string
  output_title?: string
  output_desc?: string
  final_questions?: FinalQuestion[]
  quality_judgement?: Record<string, any>
  final_task_profile?: Record<string, any>
  [key: string]: any
}

export interface ContentAssessmentUiState {
  [key: string]: any
  raw_user_input: string
  current_stage: string
  stage_trace: StageTraceItem[]
  final_questions: FinalQuestion[]
  report: ContentAssessmentReport
  retrieval_results: Record<string, EvidenceItem[]>
  output_json: ContentAssessmentOutputJson
  review_tasks?: ReviewTask[]
  resolution_logs?: ResolutionLog[]
}

export interface ContentAssessmentRunResponse {
  success: boolean
  run_id: string
  status: string
  state: ContentAssessmentUiState
}

export interface ContentAssessmentTaskHistoryItem {
  id: string
  task_id: string
  title: string
  task_type: string
  status: string
  current_stage: string
  completed_steps: number
  total_steps: number
  needs_clarification: boolean
  clarification_status: string
  summary: string
  created_at: string
  updated_at: string
  completed_at?: string | null
  duration_seconds?: number | null
}

export interface ContentAssessmentTaskDetail extends ContentAssessmentTaskHistoryItem {
  user_scope: string
  user_name: string
  raw_user_input: string
  state?: ContentAssessmentUiState | null
  output_markdown: string
  output_json: ContentAssessmentOutputJson
  stage_trace: StageTraceItem[]
  error_message?: string | null
}

export interface RunContentAssessmentPayload {
  user_input: string
  scenario_id?: string
  nine_fields?: import('./homepageDraft').NineFields
  language_preference?: string
  year_range?: number[]
  uploaded_files?: UploadedFileRef[]
  report_mode?: ReportMode
  artifact_type?: ArtifactType
  scope_focus?: 'auto' | 'focused' | 'comprehensive'
  scope_summary?: string
  section_overrides?: Record<string, { enabled?: boolean; order?: number | null; depth?: 'compact' | 'expanded' | 'full' }>
}

export async function runContentAssessment(payload: RunContentAssessmentPayload): Promise<ContentAssessmentRunResponse> {
  const response = await runRawAssessment({
    user_input: payload.user_input,
    scenario_id: payload.scenario_id,
    nine_fields: payload.nine_fields,
    assessment_title: firstLine(payload.user_input),
    year_range: payload.year_range,
    uploaded_files: payload.uploaded_files,
    document_roles: Object.fromEntries((payload.uploaded_files || []).map((file) => [file.file_id, 'PROJECT'])),
    report_mode: payload.report_mode,
    artifact_type: payload.artifact_type,
    scope_focus: payload.scope_focus,
    scope_summary: payload.scope_summary,
    section_overrides: payload.section_overrides,
  })
  return mapRunResponse(response)
}

export async function listContentAssessmentTasks(params: { status?: string; search?: string; page?: number; pageSize?: number } = {}) {
  const response = await listRawTasks({ ...params, status: params.status === 'awaiting_clarification' ? 'awaiting_confirmation' : params.status })
  return {
    items: response.items.map(mapTaskHistory),
    total: response.total,
    page: params.page ?? 1,
    page_size: params.pageSize ?? 50,
  }
}

export async function getContentAssessmentTask(taskId: string): Promise<ContentAssessmentTaskDetail> {
  return mapTaskDetail(await getRawTask(taskId))
}

export async function submitContentAssessmentConfirmation(taskId: string, selectedOption: string, userNote = '', preferences: { removed_file_ids?: string[]; supplemental_input?: string; nine_fields?: Record<string, string | string[]>; confirmed_claims?: Array<{ claim_id: string; text: string; source_document_id?: string; source_kind?: 'material' | 'evidence'; source_evidence_id?: string; source_span: string }>; report_mode?: ReportMode; artifact_type?: ArtifactType; scope_focus?: 'focused' | 'comprehensive'; scope_summary?: string; section_overrides?: Record<string, { enabled?: boolean; order?: number | null; depth?: 'compact' | 'expanded' | 'full' }> } = {}): Promise<ContentAssessmentRunResponse> {
  return mapRunResponse(await submitRawConfirmation(taskId, selectedOption, userNote, preferences))
}

export async function listContentAssessmentReviews(taskId: string) {
  return listRawReviews(taskId)
}

export async function resolveContentAssessmentReview(taskId: string, reviewTaskId: string, decision: ReviewDecision, reason = '', operator = ''): Promise<ContentAssessmentRunResponse> {
  return mapRunResponse(await resolveRawReview(taskId, reviewTaskId, decision, reason, operator))
}

export async function resumeContentAssessment(taskId: string): Promise<ContentAssessmentRunResponse> {
  const detail = await getRawTask(taskId)
  const state = detail.state
  const option = isGenerationRecovery(state) ? 'retry_generation' : state?.confirmation?.selected_option || state?.confirmation?.options?.[0]?.value || 'confirm'
  return mapRunResponse(await submitRawConfirmation(taskId, option, ''))
}

export function isGenerationRecovery(state?: RawState | null): boolean {
  return (state?.output_json?.generation_recovery as { status?: string } | undefined)?.status === 'interrupted'
    || ['report_sources', 'report_drafting', 'assessment_analysis', 'claim_extraction'].includes(state?.confirmation?.reason || '')
}

export function stopContentAssessmentTask(taskId: string) {
  return stopRawTask(taskId)
}

export function deleteContentAssessmentTask(taskId: string) {
  return deleteRawTask(taskId)
}

export function downloadContentAssessmentReport(taskId: string, format: 'docx' | 'pdf' | 'md' | 'zip' = 'docx') {
  return downloadRawReport(taskId, format)
}

export async function fetchContentAssessmentReportPdf(taskId: string, signal?: AbortSignal): Promise<Blob> {
  const result = await fetchRawReportBlob(taskId, 'pdf', 'inline', signal)
  return result.blob
}

export function subscribeToTaskStream(
  taskId: string,
  userScope: string,
  callbacks: {
    onState?: (detail: ContentAssessmentTaskDetail) => void
    onDelta?: (detail: ContentAssessmentTaskDetail) => void
    onComplete?: (detail: ContentAssessmentTaskDetail) => void
    onError?: (error: Event) => void
  },
): () => void {
  const stream = new EventSource(`${API_BASE}/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/stream?user_scope=${encodeURIComponent(userScope)}`)
  const handle = (callback: ((detail: ContentAssessmentTaskDetail) => void) | undefined) => (event: Event) => {
    if (!callback) return
    try {
      callback(mapTaskDetail(JSON.parse((event as MessageEvent).data) as RawTaskDetail))
    } catch {
      callbacks.onError?.(event)
    }
  }
  stream.addEventListener('state', handle(callbacks.onState))
  stream.addEventListener('delta', handle(callbacks.onDelta))
  stream.addEventListener('complete', (event) => {
    handle(callbacks.onComplete)(event)
    stream.close()
  })
  stream.addEventListener('error', (event) => callbacks.onError?.(event))
  return () => stream.close()
}

export interface NstlCollectResponse {
  code?: number | string
  msg?: string
  message?: string
  data?: unknown
}

export async function getNstlCollectionIds(): Promise<string[]> {
  const result = await postNstlForm('/user/collect/getMyCollectInfo')
  return result.data == null ? [] : normalizeCollectionIds(result.data)
}

export async function addNstlCollection(articleIds: string | string[]): Promise<NstlCollectResponse> {
  const ids = (Array.isArray(articleIds) ? articleIds : [articleIds]).map((id) => String(id).trim()).filter(Boolean)
  if (!ids.length) throw new Error('缺少可收藏的文献ID')
  const result = await postNstlRawForm('/user/collect/add', `articleIds=${ids.join(',')}&rl=${encodeURIComponent(JSON.stringify([]))}&shid=''&shwdid=''`)
  assertNstlCollectOk(result, '收藏失败')
  return result
}

export async function deleteNstlCollection(articleId: string): Promise<NstlCollectResponse> {
  const id = String(articleId || '').trim()
  if (!id) throw new Error('缺少可取消收藏的文献ID')
  const result = await postNstlRawForm('/user/collect/delMyCollectInfo', `id=${id}`)
  assertNstlCollectOk(result, '取消收藏失败')
  return result
}

function mapRunResponse(response: RawRunResponse): ContentAssessmentRunResponse {
  return { ...response, status: normalizeStatus(response.status), state: mapState(response.state) }
}

function mapTaskHistory(task: RawTaskHistoryItem): ContentAssessmentTaskHistoryItem {
  const rawState = (task as RawTaskHistoryItem & { state?: RawState | null }).state
  const state = rawState ? mapState(rawState) : undefined
  const stageTrace = state?.stage_trace || []
  const totalSteps = task.total_steps || stageTrace.length || 8
  return {
    id: task.id || task.task_id,
    task_id: task.task_id || task.id,
    title: task.title || state?.final_task_profile?.final_task_title || '创新性评估任务',
    task_type: '创新性评估',
    status: isGenerationRecovery(rawState) && ['awaiting_confirmation', 'awaiting_clarification'].includes(task.status) ? 'failed' : normalizeStatus(task.status),
    current_stage: task.current_stage || state?.current_stage || '',
    completed_steps: task.completed_steps ?? completedCount(stageTrace),
    total_steps: totalSteps,
    needs_clarification: !isGenerationRecovery(rawState) && Boolean(state?.clarification?.needed || rawState?.confirmation?.needed),
    clarification_status: state?.clarification?.selected_option ? 'resolved' : state?.clarification?.needed ? 'required' : 'not_required',
    summary: task.summary || state?.output_json?.summary || '',
    created_at: task.created_at,
    updated_at: task.updated_at,
    completed_at: task.completed_at || null,
    duration_seconds: task.duration_seconds ?? null,
  }
}

function normalizeStatus(status: string) {
  return status === 'awaiting_confirmation' ? 'awaiting_clarification' : status
}

function mapTaskDetail(task: RawTaskDetail): ContentAssessmentTaskDetail {
  const state = task.state ? mapState(task.state) : undefined
  const history = mapTaskHistory(task)
  return {
    ...history,
    title: task.title || state?.final_task_profile?.final_task_title || history.title,
    raw_user_input: task.state?.raw_user_input || '',
    user_scope: '',
    user_name: '',
    state,
    output_markdown: task.output_markdown || task.state?.output_markdown || '',
    output_json: state?.output_json || {},
    stage_trace: state?.stage_trace || [],
    error_message: task.error_message || null,
  }
}

function mapState(state: RawState): ContentAssessmentUiState {
  state = assessmentResultState(state)
  const claims = Array.isArray(state.claims) ? state.claims : []
  const evidence = Array.isArray(state.evidence_records) ? state.evidence_records : []
  const decisions = Array.isArray(state.novelty_decisions) ? state.novelty_decisions : []
  const evidenceById = new Map(evidence.map((item) => [item.evidence_id, item]))
  const evidenceByClaim = new Map<string, string[]>()
  for (const link of state.claim_evidence_links || []) {
    const refs = evidenceByClaim.get(link.claim_id) || []
    refs.push(link.evidence_id)
    evidenceByClaim.set(link.claim_id, refs)
  }
  const decisionByClaim = new Map(decisions.map((item) => [item.claim_id, item]))
  const findings: FinalQuestion[] = claims.map((claim, index) => {
    const decision = decisionByClaim.get(claim.claim_id)
    const refs = decision?.evidence_ids?.length ? decision.evidence_ids : evidenceByClaim.get(claim.claim_id) || []
    const score = scoreForFinding(state, decision?.label || '')
    return {
      question_id: claim.claim_id,
      rank: index + 1,
      title: `要点 ${claim.claim_id}${decision?.label ? ` · ${decision.label}` : ''}`,
      question: claim.text,
      recommendation_reason: decision?.rationale || '等待要点级证据比对结论。',
      recommendation_level: decision?.label || '待判定',
      score,
      evidence_refs: refs,
      next_steps: uniqueText([...(decision?.risk_notes || []), decision?.rewrite_advice || '补充可定位的独立外部证据。']).slice(0, 4),
    }
  })
  const mappedEvidence = evidence.map(mapEvidence)
  const retrievalResults = { content_assessment_evidence: mappedEvidence }
  const assessmentProfile = state.assessment_profile || { title: '', assessment_object: '', scope_boundary: '', assumptions: [] }
  const confirmation = isGenerationRecovery(state)
    ? { ...state.confirmation, needed: false, prompt: '', options: [], selected_option: '', user_note: '' }
    : state.confirmation || { needed: false, prompt: '', options: [], selected_option: '', user_note: '' }
  const stageTrace = (state.stage_trace || []).map((stage) => ({ ...stage }))
  const qualityGate = state.quality_gate || { passed: false, blockers: [], warnings: [], review_required: false, report_eligibility: '' }
  const qualityJudgement = {
    enabled: Boolean(decisions.length || qualityGate.report_eligibility),
    conclusion: qualityGate.report_eligibility === 'formal' ? '评估质量门通过，可发布正式报告。' : qualityGate.review_required ? '需要专家复核后发布评估报告。' : '评估结论已生成。',
    basis: uniqueText([...(qualityGate.blockers || []), ...(qualityGate.warnings || [])]),
    supporting_evidence_refs: mappedEvidence.slice(0, 8).map((item) => item.evidence_id),
    supporting_literature: mappedEvidence,
    can_continue_generation: false,
    terminated: false,
    termination_reason: '',
    judged_original_question: assessmentProfile.assessment_object || state.raw_user_input,
    summary: state.report_plan?.executive_summary || '已完成要点、证据与质量门综合评估。',
  }
  const report = buildReport(state, assessmentProfile, decisions, evidenceByClaim, qualityGate)
  const trend = trendFor(state, mappedEvidence)
  const topicAnalysis = {
    scope: { topic_boundary: assessmentProfile.scope_boundary || '评估范围待确认', limits: assessmentProfile.assumptions || [], refs: [] },
    evidence_basis: { summary: `已纳入 ${mappedEvidence.length} 条证据记录。`, refs: mappedEvidence.map((item) => item.evidence_id) },
    overall: { summary: String(state.trend_analysis?.summary || state.report_plan?.executive_summary || ''), points: [], refs: mappedEvidence.slice(0, 5).map((item) => item.evidence_id) },
    tracks: textRows(state.trend_analysis?.tracks),
    methods: textRows(state.trend_analysis?.methods),
    tensions: textRows(uniqueText([...(qualityGate.blockers || []), ...(qualityGate.warnings || [])])),
    advantages: textRows((state.scorecard || []).map((item) => `${item.dimension}：${item.assessment}`)),
    opportunities: textRows(findings.map((item) => item.next_steps[0]).filter(Boolean)),
    trend,
  }
  const finalTaskProfile = {
    final_task_title: displayReportTitle(state.report_plan?.report_title || assessmentProfile.title || state.raw_user_input),
    task_mode: 'content_assessment',
    research_domain: firstField(state.nine_fields, 'research_direction'),
    research_object: assessmentProfile.assessment_object || firstField(state.nine_fields, 'research_content'),
    research_goal: '形成可溯源的创新性评估结论与报告。',
    confirmed_focus: assessmentProfile.scope_boundary || '待确认',
    scope_boundary: assessmentProfile.scope_boundary || '待确认',
    retrieval_intent: '建立要点级检索证据池并完成比对。',
    search_seed_terms: arrayField(state.nine_fields, 'key_technologies'),
    must_include: [],
    must_exclude: [],
    clarification_trace: {},
    summary: state.report_plan?.executive_summary || '',
  }
  const processTrace = state.process_trace && Object.keys(state.process_trace).length
    ? state.process_trace
    : Object.fromEntries(stageTrace.map((stage) => [stage.stage_key, [{
    step_key: stage.stage_key,
    title: stage.stage_name,
    status: stage.status,
    summary: stage.summary,
    started_at: stage.started_at,
    completed_at: stage.completed_at,
    evidence_refs: stage.stage_key.includes('evidence') || stage.stage_key.includes('retrieval') ? mappedEvidence.slice(0, 3).map((item) => item.evidence_id) : [],
  }]]))
  const outputJson: ContentAssessmentOutputJson = {
    // Preserve saved stage prose and reader comparisons before adding UI fields.
    // Replacing output_json would hide accepted evaluations until report freeze.
    ...state.output_json,
    summary: state.report_plan?.executive_summary || '',
    output_title: displayReportTitle(state.report_plan?.report_title || assessmentProfile.title || state.raw_user_input),
    output_desc: state.report_plan?.report_type || '创新性评估',
    final_questions: findings,
    quality_judgement: qualityJudgement,
    final_task_profile: finalTaskProfile,
    report,
  }
  return {
    ...state,
    stage_trace: stageTrace,
    raw_user_input: state.raw_user_input || '',
    input_understanding: {
      topic: assessmentProfile.assessment_object || assessmentProfile.title || '创新性评估任务',
      user_intent: '创新性评估',
      research_domain: firstField(state.nine_fields, 'research_direction'),
      core_entities: arrayField(state.nine_fields, 'key_technologies'),
      constraints: { input_type: 'assessment_material' },
      ambiguity_level: confirmation.needed ? 'medium' : 'low',
      clarification_needed: Boolean(confirmation.needed),
      summary: assessmentProfile.scope_boundary || '',
    },
    preprocessed_input: {
      raw_text: state.raw_user_input || '',
      normalized_text: state.raw_user_input || '',
      input_length: (state.raw_user_input || '').length,
      input_type: 'assessment_material',
      language_hint: 'auto',
      is_valid: true,
      block_reason: '',
      should_continue: true,
      lightweight_hints: assessmentProfile.assumptions || [],
    },
    research_task_draft: {
      task_mode: 'content_assessment',
      topic_label: assessmentProfile.title || '创新性评估任务',
      research_domain: firstField(state.nine_fields, 'research_direction'),
      research_object: assessmentProfile.assessment_object || '',
      research_goal: '完成要点、证据、风险与报告评估。',
      scenario: firstField(state.nine_fields, 'application_scenarios'),
      key_concepts: arrayField(state.nine_fields, 'key_technologies'),
      variables_or_metrics: arrayField(state.nine_fields, 'evaluation_indicators'),
      known_constraints: {},
      missing_slots: [],
      retrieval_risk: { reason: qualityGate.warnings?.[0] || '' },
      clarification_candidate: {},
      summary: assessmentProfile.scope_boundary || '',
    },
    clarification: {
      needed: Boolean(confirmation.needed),
      reason: confirmation.reason || '',
      prompt: confirmation.prompt || '',
      question: confirmation.prompt || '请确认评估边界。',
      options: confirmation.options || [],
      selected_option: confirmation.selected_option || '',
      selected_label: (confirmation.options || []).find((item) => item.value === confirmation.selected_option)?.label || '',
      user_note: confirmation.user_note || '',
    },
    quality_judgement: qualityJudgement,
    final_task_profile: finalTaskProfile,
    final_questions: findings,
    report,
    retrieval_results: retrievalResults,
    evidence_analysis: Object.fromEntries(findings.map((item) => [item.question_id, {
      question_id: item.question_id,
      supporting_evidence: item.evidence_refs,
      gap_signals: item.next_steps,
      controversy_signals: [],
      maturity_judgement: item.recommendation_level || '待判定',
      evidence_summary: item.recommendation_reason,
    }])),
    topic_analysis: topicAnalysis,
    retrieval_detail: {
      query_plan: arrayField(state.nine_fields, 'key_technologies'),
      retrieval_mix: ['要点拆分', '证据检索', '要点级比对'],
      total: mappedEvidence.length,
      reranked_top_k: mappedEvidence.length,
      keywords: arrayField(state.nine_fields, 'key_technologies'),
      highlights: mappedEvidence.slice(0, 3).map((item) => ({ title: item.title, year: item.year, why: item.support_summary, evidence_id: item.evidence_id })),
    },
    generation_detail: {
      candidate_total: findings.length,
      selected_count: findings.length,
      strategies: ['要点级比对', '证据可追溯性', '质量门校验'],
      ranking_basis: (state.scorecard || []).map((item) => item.dimension),
    },
    process_trace: processTrace,
    // Keep missing historical upload metadata distinct from a confirmed empty list.
    // Definite legacy file records are handled by uploadPresentation at display time.
    uploaded_file_context: (state as any).uploaded_file_context,
    output_json: outputJson,
    report_snapshot: state.report_snapshot,
    report_metrics: state.report_metrics || {},
    boundary_matrix: state.boundary_matrix || {},
    work_packages: state.work_packages || [],
    indicator_validations: state.indicator_validations || [],
  }
}

function buildReport(
  state: RawState,
  profile: RawState['assessment_profile'],
  decisions: NonNullable<RawState['novelty_decisions']>,
  evidenceByClaim: Map<string, string[]>,
  qualityGate: NonNullable<RawState['quality_gate']>,
): ContentAssessmentReport {
  const decisionByClaim = new Map(decisions.map((item) => [item.claim_id, item]))
  const claims = (state.claims || []).map((claim) => {
    const decision = decisionByClaim.get(claim.claim_id)
    const evidenceRelation = buildClaimEvidencePresentation(claim, state.claim_evidence_links || [], state.evidence_records || [], decision)
    return {
      id: claim.claim_id,
      text: claim.text,
      label: decision?.label || '待判定',
      rationale: decision?.rationale || '尚未形成要点级比对结论。',
      evidenceRefs: evidenceRelation.evidenceIds,
      risks: decision?.risk_notes || [],
      rewriteAdvice: decision?.rewrite_advice || '',
      evidenceRelation,
    }
  })
  const reportPlan = state.report_plan || { report_title: '', report_version: '', report_type: '', executive_summary: '' }
  const snapshotSections: any[] = Array.isArray(state.report_snapshot?.sections) ? state.report_snapshot.sections : []
  const frozenConclusion = snapshotSections.find((item: any) => item.section_key === 'executive')
    ?.blocks?.find((block: any) => block.kind === 'paragraph' && block.text?.startsWith('主要结论：'))?.text
  return {
    ready: Boolean(reportPlan.report_version || claims.length || (state.scorecard || []).length),
    title: displayReportTitle(reportPlan.report_title || profile?.assessment_object || profile?.title || state.raw_user_input),
    version: reportPlan.report_version || '待生成',
    type: reportPlan.report_type || '创新性评估报告',
    executiveSummary: frozenConclusion || (reportPlan.executive_summary || '已完成材料、要点、证据和质量门的综合评估。').replace(/主张/g, '要点'),
    assessmentObject: profile?.assessment_object || profile?.title || state.raw_user_input || '待确认',
    scopeBoundary: profile?.scope_boundary || '以当前任务材料与检索证据为边界。',
    evidenceCount: (state.evidence_records || []).length,
    claimCount: claims.length,
    longReportStatus: String((state.report_metrics as any)?.long_report_status || reportPlan.quality_status || 'pending'),
    estimatedPages: Number((state.report_metrics as any)?.estimated_pages || 0),
    actualPageCount: reportPlan.actual_page_count ?? ((state.report_metrics as any)?.actual_page_count ?? null),
    reportMode: reportPlan.report_mode,
    recommendedMode: reportPlan.recommended_mode,
    artifactType: reportPlan.artifact_type || profile?.artifact_type,
    scopeSummary: reportPlan.scope_summary || profile?.scope_summary,
    pageCountVerified: Boolean((state.report_metrics as any)?.page_count_verified),
    pageTargetStatus: String((state.report_metrics as any)?.page_target_status || 'pending'),
    pageTargetReason: String((state.report_metrics as any)?.page_target_reason || ''),
    evidenceStrength: reportPlan.evidence_strength || (state.report_metrics as any)?.evidence_strength || 'low',
    evidenceStrengthReason: reportPlan.evidence_strength_reason || (state.report_metrics as any)?.evidence_strength_reason || '',
    citationNumbers: state.report_snapshot?.citation_numbers || {},
    autoDetection: reportPlan.auto_detection || {},
    sectionQuotaCompletion: Number((state.report_metrics as any)?.section_quota_completion || 0),
    claimCoverage: Number((state.report_metrics as any)?.claim_coverage || 0),
    formalClaimCoverage: Number((state.report_metrics as any)?.formal_claim_coverage || 0),
    directionalClaimCoverage: Number((state.report_metrics as any)?.directional_claim_coverage || 0),
    indicatorCoverage: Number((state.report_metrics as any)?.indicator_coverage || 0),
    citationIntegrity: Number((state.report_metrics as any)?.citation_integrity || 0),
    bodyWords: Number((state.report_metrics as any)?.body_words || 0),
    appendixWords: Number((state.report_metrics as any)?.appendix_words || 0),
    bodyPages: Number((state.report_metrics as any)?.body_pages || 0),
    appendixPages: Number((state.report_metrics as any)?.appendix_pages || 0),
    bodyRatio: Number((state.report_metrics as any)?.body_ratio || 0),
    appendixRatio: Number((state.report_metrics as any)?.appendix_ratio || 0),
    appendixCompressionStatus: String((state.report_metrics as any)?.appendix_compression_status || 'pending'),
    deficiencies: ((state.report_metrics as any)?.deficiencies || []) as string[],
    sectionSpecs: (snapshotSections.length ? snapshotSections : reportPlan.section_specs || []).map((item: any) => ({
      section_key: String(item.section_key || ''),
      title: String(item.title || ''),
      target_words: Number(item.target_words || 0),
      actual_words: Number(item.actual_words || 0),
      target_evidence_refs: Number(item.target_evidence_refs || 0),
      actual_evidence_refs: Number(item.actual_evidence_refs || 0),
      status: String(item.status || 'pending'),
      order: Number(item.order || 0),
      level: Number(item.level || 1),
      is_appendix: Boolean(item.is_appendix),
      content: String(item.content || ''),
      evidence_refs: Array.isArray(item.evidence_refs) ? item.evidence_refs.map(String) : [],
    })),
    qualityGate: {
      eligibility: qualityGate.report_eligibility || 'exploratory',
      passed: Boolean(qualityGate.passed),
      reviewRequired: Boolean(qualityGate.review_required),
      blockers: qualityGate.blockers || [],
      warnings: qualityGate.warnings || [],
    },
    claims,
    scorecard: (state.scorecard || []).map((item) => ({
      dimension: item.dimension,
      assessment: item.assessment,
      scoreRange: item.score_range || '',
      basis: item.basis || [],
      uncertainty: item.uncertainty || '',
    })),
    trend: {
      summary: String(state.trend_analysis?.summary || ''),
      highlights: textRows(state.trend_analysis?.trend_highlights).map((item) => item.text),
      competition: textRows(state.trend_analysis?.competition_observations).map((item) => item.text),
      risks: textRows(state.trend_analysis?.risk_notes).map((item) => item.text),
    },
  }
}

function mapEvidence(item: RawEvidenceRecord): EvidenceItem {
  return {
    evidence_id: item.evidence_id,
    paper_id: (item as any).paper_id || item.evidence_id,
    source_type: item.source_type,
    title: item.title || item.evidence_id,
    authors: item.authors || [],
    year: Number(item.year || 0),
    venue: item.venue || item.source_type || '检索证据',
    doi: item.doi,
    url: item.url,
    nstl_url: item.nstl_url || '',
    source_url: item.source_url || '',
    evidence_level: item.evidence_level || '',
    retrieval_query: item.retrieval_query || '',
    query_group: item.query_group || '',
    claim_ids: item.claim_ids || [],
    abstract: item.abstract || item.excerpt || '',
    support_summary: item.excerpt || item.evidence_level || '待补充证据摘要。',
  }
}

function scoreForFinding(state: RawState, label: string) {
  const scores = state.scorecard || []
  const lookup = (name: string) => Number(String(scores.find((item) => item.dimension.includes(name))?.score_range || '').match(/\d+(?:\.\d+)?/)?.[0] || 0)
  const total = scores.length ? Math.min(10, Math.max(0, scores.reduce((sum, item) => sum + Number(String(item.score_range || '').match(/\d+(?:\.\d+)?/)?.[0] || 0), 0) / scores.length)) : 0
  return { total_score: total, score_reason: label || '待完成评估', novelty: lookup('创新'), researchability: lookup('可行'), evidence_support: lookup('证据'), clarity: lookup('质量'), domain_value: lookup('前沿'), feasibility: lookup('风险') }
}

function trendFor(state: RawState, evidence: EvidenceItem[]) {
  const rawTrend = state.trend_analysis?.trend as any
  if (rawTrend && Array.isArray(rawTrend.bars)) return rawTrend
  const countByYear = new Map<number, number>()
  for (const item of evidence) {
    if (!item.year) continue
    countByYear.set(item.year, (countByYear.get(item.year) || 0) + 1)
  }
  const years = [...countByYear.keys()].sort((a, b) => a - b)
  return { text: String(state.trend_analysis?.summary || ''), years, bars: years.map((year) => ({ year, value: countByYear.get(year) || 0 })), refs: evidence.map((item) => item.evidence_id) }
}

function textRows(values: unknown) {
  const rows = Array.isArray(values) ? values : values ? [values] : []
  return rows.map((item) => ({ text: typeof item === 'string' ? item : String((item as any)?.text || (item as any)?.summary || ''), refs: [] })).filter((item) => item.text)
}

function completedCount(stages: StageTraceItem[]) {
  return stages.filter((stage) => stage.status === 'completed' || stage.status === 'skipped').length
}

function firstLine(value: string) {
  return value.split('\n').map((item) => item.trim()).find(Boolean) || '创新性评估任务'
}

function displayReportTitle(value: unknown) {
  const raw = String(value || '').split(/[\r\n。！？!?；;]/, 1)[0]
    .replace(/[ ：:，,。；;_\-—]+$/g, '')
    .trim()
  return raw.endsWith('报告') ? raw : raw ? `${raw}创新性评估报告` : '创新性评估报告'
}

function firstField(fields: Record<string, string | string[]> | undefined, key: string) {
  const value = fields?.[key]
  return Array.isArray(value) ? value.join('、') : value || ''
}

function arrayField(fields: Record<string, string | string[]> | undefined, key: string) {
  const value = fields?.[key]
  if (Array.isArray(value)) return value
  return value ? value.split(/[、；;，,]/).map((item) => item.trim()).filter(Boolean) : []
}

function uniqueText(values: string[]) {
  return [...new Set(values.map((item) => String(item || '').trim()).filter(Boolean))]
}

async function postNstlForm(path: string, params: Record<string, string> = {}): Promise<NstlCollectResponse> {
  return postNstlRawForm(path, new URLSearchParams(params).toString())
}

async function postNstlRawForm(path: string, body: string): Promise<NstlCollectResponse> {
  const response = await fetch(`${NSTL_WEB_BASE_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'include', body })
  const text = await response.text()
  if (/^\s*</.test(text)) throw new Error('收藏服务暂不可用，请稍后重试')
  let payload: NstlCollectResponse = {}
  try { payload = text ? JSON.parse(text) as NstlCollectResponse : {} } catch { payload = { code: 0, data: text } }
  if (!response.ok) throw new Error(String(payload.msg || payload.message || `请求失败(${response.status})`))
  return payload
}

function assertNstlCollectOk(result: NstlCollectResponse, fallback: string) {
  if (result.code == null || String(result.code) === '0') return
  throw new Error(String(result.msg || result.message || fallback))
}

function normalizeCollectionIds(input: unknown): string[] {
  const ids: string[] = []
  const collect = (value: unknown) => {
    if (typeof value === 'string' || typeof value === 'number') { ids.push(String(value)); return }
    if (Array.isArray(value)) { value.forEach(collect); return }
    if (value && typeof value === 'object') Object.values(value as Record<string, unknown>).forEach(collect)
  }
  collect(input)
  return [...new Set(ids.map((id) => id.trim()).filter((id) => id && id.length <= 120 && /^[A-Za-z0-9_.:-]+$/.test(id)))]
}
