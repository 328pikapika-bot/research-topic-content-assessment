import { requirePlatformLogin } from './nstlAuth'
import { readUploadError } from './fileUploadErrors'
import { readApiError } from './apiErrors'
import type { NineFields } from './homepageDraft'
import { requireCurrentAssessmentPreview } from './assessmentInput'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/service/nstl/ai/agent/content-assessment').replace(/\/$/, '')

export type DocumentRole = 'PROJECT' | 'BASELINE' | 'REFERENCE' | 'EVIDENCE'
export type ReportMode = 'auto' | 'standard' | 'deep'
export type ArtifactType = 'auto' | 'paper' | 'project' | 'guideline' | 'technology_result' | 'mixed' | 'unknown'
export type StageStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'blocked' | 'failed'

export interface UploadedFileRef {
  file_id: string
  name: string
  type: string
  size?: number
  url?: string
  parsed_at?: string
}

export interface StageTraceItem {
  stage_key: string
  stage_name: string
  status: StageStatus
  summary: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
}
export interface ProcessTraceStep { step_key: string; title: string; status: StageStatus; summary?: string; started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null; evidence_refs?: string[] }

export interface Claim { claim_id: string; text: string; assertion_type: string; claim_type: string; claim_fields?: Record<string, string>; source_document_id?: string; source_kind?: 'material' | 'evidence'; source_evidence_id?: string; source_span: string; source_page_ref?: string; source_paragraph_ref?: string; source_locator?: Record<string, unknown> }
export interface EvidenceRecord { evidence_id: string; title: string; source_type: string; year?: number; excerpt: string; abstract?: string; authors?: string[]; venue?: string; doi?: string; query_group?: string; retrieval_query?: string; comparison_fields?: Record<string, string>; claim_ids?: string[]; indicator_ids?: string[]; retrieval_score?: number; url?: string; nstl_url?: string; source_url?: string; evidence_level: string; page_ref?: string; paragraph_ref?: string; fulltext_available?: boolean; anchor_verified?: boolean }
export interface ClaimEvidenceLink { claim_id: string; evidence_id: string; relation: string; confidence: number; comparable_fields?: string[]; field_matrix?: Record<string, string>; evidence_chunk_ids?: string[]; incomparability_reasons?: string[]; difference?: string; uncertainty?: string }
export interface NoveltyDecision { claim_id: string; label: string; rationale: string; evidence_ids: string[]; risk_notes: string[]; rewrite_advice: string }
export interface ScoreCardItem { dimension: string; assessment: string; score_range?: string; basis?: string[]; uncertainty?: string }
export interface QualityGate { passed: boolean; blockers: string[]; warnings: string[]; review_required: boolean; report_eligibility: string; checks?: Array<{ key: string; title: string; status: 'passed' | 'warning' | 'blocked'; completed: number; total: number; message: string; basis?: string; anchor?: string }>; model_status?: string; model_findings?: Array<{ message: string; claim_id?: string; evidence_ids?: string[] }>; post_report_checks?: Array<{ key: string; status: string; message: string }> }
export type ReviewDecision = 'approve' | 'limit' | 'return_to_retrieval' | 'dismiss'
export interface ReviewTask { review_task_id: string; review_type: string; claim_id: string; title: string; reason: string; status: string; evidence_ids: string[]; suggested_actions: string[]; created_version: string; resolved_by: string; resolved_at: string; resolution_reason: string }
export interface ResolutionLog { resolution_id: string; review_task_id: string; decision: ReviewDecision; operator: string; occurred_at: string; reason: string; evidence_version: string; affected_stages: string[] }

export interface ContentAssessmentState {
  parent_task_id?: string
  scenario_id?: string
  raw_user_input: string
  current_stage: string
  stage_trace: StageTraceItem[]
  process_trace?: Record<string, ProcessTraceStep[]>
  stage_progress?: Record<string, { stage_key: string; completed: number; total: number; display_name: string; updated_at?: string }>
  assessment_profile: { title: string; assessment_object: string; scope_boundary: string; document_records: Array<{ document_id: string; name: string; role: DocumentRole; status: string; excerpt: string }>; assumptions: string[]; artifact_type?: string; scope_summary?: string }
  boundary_matrix?: Record<string, unknown>
  work_packages?: Array<Record<string, unknown>>
  nine_fields: Record<string, string | string[]>
  confirmation: { needed: boolean; reason?: string; prompt: string; options: Array<{ label: string; value: string }>; selected_option: string; user_note: string }
  claims: Claim[]
  indicators: Array<{ indicator_id: string; name: string; value: string; unit: string; status: string }>
  indicator_validations?: Array<Record<string, unknown>>
  evidence_records: EvidenceRecord[]
  evidence_chunks?: Array<Record<string, unknown>>
  claim_evidence_links: ClaimEvidenceLink[]
  claim_assessments?: Array<Record<string, unknown>>
  novelty_decisions: NoveltyDecision[]
  scorecard: ScoreCardItem[]
  trend_analysis: Record<string, unknown>
  quality_gate: QualityGate
  report_preferences?: { mode?: ReportMode; resolved_mode?: 'standard' | 'deep'; mode_source?: 'auto' | 'user'; recommended_mode?: 'standard' | 'deep'; artifact_type?: ArtifactType; resolved_artifact_type?: string; scope_focus?: 'focused' | 'comprehensive'; scope_summary?: string; section_overrides?: Record<string, { enabled?: boolean; order?: number | null; depth?: string }> }
  report_plan: { report_version: string; report_title: string; report_type: string; executive_summary: string; limitations: string[]; report_mode?: 'standard' | 'deep'; mode_source?: 'auto' | 'user'; recommended_mode?: 'standard' | 'deep'; artifact_type?: string; scope_summary?: string; evidence_strength?: 'high' | 'medium' | 'low'; evidence_strength_reason?: string; auto_detection?: Record<string, unknown>; target_page_min?: number; target_page_max?: number; quality_status?: string; actual_page_count?: number | null; section_specs?: Array<Record<string, unknown>>; content_unit_counts?: Record<string, number> }
  report_metrics?: Record<string, unknown>
  report_snapshot?: { snapshot_id: string; generated_at: string; citation_numbers?: Record<string, number>; sections?: Array<Record<string, unknown>>; chart_manifest?: Array<Record<string, any>>; render_context?: Record<string, any>; audit_data?: Record<string, any>; chart_data_snapshot?: Record<string, any> }
  output_markdown: string
  output_json?: Record<string, unknown>
  review_tasks?: ReviewTask[]
  resolution_logs?: ResolutionLog[]
  versions?: Record<string, unknown>
}

export interface ContentAssessmentRunResponse { success: boolean; run_id: string; status: string; state: ContentAssessmentState }
export interface ContentAssessmentTaskHistoryItem { id: string; task_id: string; title: string; task_type: string; status: string; current_stage: string; completed_steps: number; total_steps: number; summary: string; created_at: string; updated_at: string; completed_at?: string; duration_seconds?: number }
export interface ContentAssessmentTaskDetail extends ContentAssessmentTaskHistoryItem { state?: ContentAssessmentState | null; output_markdown: string; error_message?: string }

export interface RunContentAssessmentPayload {
  user_input: string
  scenario_id?: string
  nine_fields?: NineFields
  assessment_title?: string
  year_range?: number[]
  uploaded_files?: UploadedFileRef[]
  document_roles?: Record<string, DocumentRole>
  report_mode?: ReportMode
  artifact_type?: ArtifactType
  scope_focus?: 'auto' | 'focused' | 'comprehensive'
  scope_summary?: string
  section_overrides?: Record<string, { enabled?: boolean; order?: number | null; depth?: 'compact' | 'expanded' | 'full' }>
}

async function headers(json = false): Promise<Record<string, string>> {
  const userScope = await requirePlatformLogin()
  return json ? { 'Content-Type': 'application/json', 'X-User-Scope': userScope } : { 'X-User-Scope': userScope }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...(await headers(Boolean(init.body))), ...(init.headers || {}) } })
  if (!response.ok) throw await readApiError(response)
  return response.json() as Promise<T>
}

async function messageFor(response: Response): Promise<string> {
  return (await readApiError(response)).message
}

export function previewNineFields(user_input: string, uploaded_files: UploadedFileRef[], artifact_type: ArtifactType = 'auto') {
  return request<{ nine_fields: NineFields; missing_fields: string[]; warnings: string[]; resolved_artifact_type?: string; extraction_failed?: boolean; validation_version?: string }>('/v1/content-assessment/preview-nine-fields', { method: 'POST', body: JSON.stringify({ user_input, uploaded_files, artifact_type }) }).then(requireCurrentAssessmentPreview)
}
export interface TaskStatistics { total: number; statuses: Record<string, number>; status_groups?: { running: number; completed: number; attention: number; stopped: number }; agents: Record<string, { total: number } & Record<string, number>> }
export interface HistoryCleanup { success: boolean; eligible_count: number; deleted_count: number; skipped_count: number; skipped_reasons: Record<string, number>; task_ids: string[] }
export function previewHistoryCleanup() { return request<HistoryCleanup>('/v1/tasks/history') }
export function clearTaskHistory() { return request<HistoryCleanup>('/v1/tasks/history', { method: 'DELETE' }) }
export function listAllTasks(params: { agent?: string; status?: string; search?: string; days?: number; page?: number; pageSize?: number } = {}) {
  const query = new URLSearchParams({ page: String(params.page || 1), page_size: String(params.pageSize || 20) })
  for (const key of ['agent', 'status', 'search', 'days'] as const) if (params[key]) query.set(key, String(params[key]))
  return request<{ items: ContentAssessmentTaskHistoryItem[]; total: number; statistics: TaskStatistics; quotas: Record<string, { occupied: number; running: number; queued: number; limit: number }> }>(`/v1/tasks?${query}`)
}

export function runContentAssessment(payload: RunContentAssessmentPayload) {
  return request<ContentAssessmentRunResponse>('/v1/content-assessment/run', { method: 'POST', body: JSON.stringify(payload) })
}

export function listContentAssessmentTasks(params: { status?: string; search?: string; page?: number; pageSize?: number } = {}) {
  const query = new URLSearchParams({ page: String(params.page || 1), page_size: String(params.pageSize || 50) })
  if (params.status && params.status !== 'all') query.set('status', params.status)
  if (params.search?.trim()) query.set('search', params.search.trim())
  return request<{ items: ContentAssessmentTaskHistoryItem[]; total: number }>(`/v1/content-assessment/tasks?${query}`)
}

export function getContentAssessmentTask(taskId: string) {
  return request<ContentAssessmentTaskDetail>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}`)
}

export function submitContentAssessmentConfirmation(taskId: string, selectedOption: string, userNote = '', preferences: { supplemental_input?: string; removed_file_ids?: string[]; nine_fields?: Record<string, string | string[]>; confirmed_claims?: Array<{ claim_id: string; text: string; source_document_id?: string; source_kind?: 'material' | 'evidence'; source_evidence_id?: string; source_span: string }>; report_mode?: ReportMode; artifact_type?: ArtifactType; scope_focus?: 'focused' | 'comprehensive'; scope_summary?: string; section_overrides?: Record<string, { enabled?: boolean; order?: number | null; depth?: 'compact' | 'expanded' | 'full' }> } = {}) {
  return request<ContentAssessmentRunResponse>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/confirmation`, { method: 'POST', body: JSON.stringify({ selected_option: selectedOption, user_note: userNote, ...preferences }) })
}

export function listContentAssessmentReviews(taskId: string) {
  return request<{ success: boolean; run_id: string; status: string; items: ReviewTask[]; resolution_logs: ResolutionLog[] }>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/reviews`)
}

export function resolveContentAssessmentReview(taskId: string, reviewTaskId: string, decision: ReviewDecision, reason = '', operator = '') {
  return request<ContentAssessmentRunResponse>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/reviews/${encodeURIComponent(reviewTaskId)}`, { method: 'POST', body: JSON.stringify({ decision, reason, operator }) })
}

export function stopContentAssessmentTask(taskId: string) {
  return request<{ success: boolean; task_id: string }>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/stop`, { method: 'POST' })
}

export function deleteContentAssessmentTask(taskId: string) {
  return request<{ success: boolean; task_id: string }>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function uploadMaterial(file: File): Promise<UploadedFileRef> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/v1/files/upload`, { method: 'POST', headers: await headers(), body: form })
  if (!response.ok) throw await readUploadError(response)
  const body = await response.json()
  const data = body.data || body
  return { file_id: data.file_id || data.fileId, name: data.name, type: data.type, size: data.size, url: data.url, parsed_at: data.parsed_at || data.parsedAt }
}

export function createReportRevision(taskId: string) {
  return request<ContentAssessmentRunResponse>(`/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/revisions`, { method: 'POST' })
}

export async function fetchReportBlob(taskId: string, format: 'docx' | 'pdf' | 'md' | 'zip' = 'docx', disposition: 'attachment' | 'inline' = 'attachment', signal?: AbortSignal) {
  const artifact = format === 'zip' ? 'evidence' : 'report'
  const response = await fetch(`${API_BASE}/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/export?format=${format}&artifact=${artifact}&disposition=${disposition}`, { headers: await headers(), signal })
  if (!response.ok) throw new Error(await messageFor(response))
  return { blob: await response.blob(), disposition: response.headers.get('content-disposition') || '' }
}

export async function downloadReport(taskId: string, format: 'docx' | 'pdf' | 'md' | 'zip' = 'docx') {
  const { blob, disposition } = await fetchReportBlob(taskId, format)
  const target = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = target
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const fallback = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  let filename = `内容评估报告.${format}`
  if (encoded) {
    try { filename = decodeURIComponent(encoded) } catch { filename = fallback || filename }
  } else if (fallback) {
    filename = fallback
  }
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  // Keep the Blob URL alive until the browser has accepted the download.
  window.setTimeout(() => {
    link.remove()
    URL.revokeObjectURL(target)
  }, 1000)
}

export async function subscribeToTaskStream(taskId: string, callbacks: { onChange: (detail: ContentAssessmentTaskDetail) => void; onError?: () => void }) {
  const scope = await requirePlatformLogin()
  const stream = new EventSource(`${API_BASE}/v1/content-assessment/tasks/${encodeURIComponent(taskId)}/stream?user_scope=${encodeURIComponent(scope)}`)
  const handle = (event: MessageEvent) => callbacks.onChange(JSON.parse(event.data) as ContentAssessmentTaskDetail)
  stream.addEventListener('state', handle)
  stream.addEventListener('delta', handle)
  stream.addEventListener('complete', (event) => { handle(event as MessageEvent); stream.close() })
  stream.onerror = () => callbacks.onError?.()
  return () => stream.close()
}
