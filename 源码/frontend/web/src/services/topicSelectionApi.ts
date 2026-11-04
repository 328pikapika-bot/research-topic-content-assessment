import { requirePlatformLogin } from './nstlAuth'
import { readApiError } from './apiErrors'
import type { GenerationRecovery } from './generationRecovery'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/service/nstl/ai/agent/content-assessment').replace(/\/$/, '')

export interface TopicStageTrace { stage_key: string; stage_name: string; status: string; summary: string; started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null; evidence_refs?: string[]; progress_percent?: number; active_step?: string; metrics?: Record<string, any>; model_status?: 'not_requested' | 'running' | 'completed' | 'degraded' | 'disabled'; prompt_version?: string; model_call_count?: number; model_duration_ms?: number | null; deterministic_duration_ms?: number | null; external_io_duration_ms?: number | null; model_error_code?: string; execution_mode?: string; fallback_reason?: string; degradation_reason?: string }
export interface TopicProgressEvent { event_id: string; sequence: number; stage_key: string; step_key: string; kind: 'status' | 'metric' | 'artifact' | 'warning'; status: 'running' | 'completed' | 'failed' | 'blocked'; title: string; message: string; progress_percent: number; current?: number | null; total?: number | null; unit?: string; metrics?: Record<string, any>; artifact_refs?: string[]; occurred_at: string }
export interface TopicEvidenceChunk { chunk_id: string; doc_id: string; evidence_id?: string; text: string; chunk_index?: number | null; chunk_type?: string; section_path?: string; char_start?: number | null; char_end?: number | null; token_count?: number | null; score?: number; locator?: string }
export interface CandidateEvidenceLink { link_id: string; candidate_id: string; theme_ids?: string[]; gap_ids?: string[]; evidence_id: string; chunk_ids?: string[]; relation?: string; rationale?: string; evidence_scope?: 'fulltext' | 'abstract' | 'metadata' | 'external'; locator?: string }
export interface TopicQualityFinding { code: string; severity: 'info' | 'warning' | 'review' | 'blocker'; message: string; stage_key: string; query_id?: string; model_call_id?: string; anchor?: string; candidate_id?: string; evidence_ids?: string[] }
export interface TopicReaderText { text: string; evidence_ids?: string[] }
export interface TopicCandidateReaderContent { reason?: TopicReaderText; research_content?: { research_object?: TopicReaderText; core_question?: TopicReaderText; method_route?: TopicReaderText }; evidence_summary?: TopicReaderText }
export interface TopicCandidate { candidate_id: string; title: string; research_question: string; research_object?: string; hypothesis?: string; innovation?: string; method_route?: string; validation_path?: string; data_requirements?: string[]; evaluation_metrics?: string[]; risks?: string[]; recommendation_level?: string; evidence_ids?: string[]; evidence_links?: CandidateEvidenceLink[]; theme_ids?: string[]; gap_ids?: string[]; user_material_refs?: string[]; representative_evidence_ids?: string[]; external_evidence_ids?: string[]; fit_rationale?: string; distinctiveness_rationale?: string; uncertainties?: string[]; validation_status?: string; reader_content?: TopicCandidateReaderContent }
export interface TopicCandidate { display_title?: string; sample_boundary?: string; convergence_notes?: string[]; convergence_valid?: boolean; risk_mitigation?: string[]; direction_type?: 'recommended' | 'exploratory' | null }
export interface TopicScore { candidate_id: string; total: number; value: number; novelty: number; evidence_support: number; feasibility: number; data_availability: number; resource_fit: number; competition_pressure: number; expected_impact: number; explanation?: string; score_range?: number[]; dimensions?: Array<{ key: string; label: string; score: number; max_score: number; weight: number; rationale?: string; evidence_ids?: string[]; material_refs?: string[]; missing_information?: string[]; uncertainty?: string; requires_review?: boolean }> }
export interface TopicState { resume_from_stage?: string; raw_user_input: string; current_stage: string; stage_trace: TopicStageTrace[]; active_progress?: TopicProgressEvent | null; progress_events?: TopicProgressEvent[]; input_profile: Record<string, any>; constraint_card?: Record<string, any>; term_map?: any[]; theme_tree: Record<string, any>; query_plan?: Record<string, any>; source_queries?: any[]; evidence_pool: any[]; evidence_chunks?: TopicEvidenceChunk[]; landscape_snapshot?: Record<string, any>; future_work_signals?: any[]; crosslinks?: any[]; gaps?: any[]; candidates?: TopicCandidate[]; score_breakdowns?: TopicScore[]; quality_gate?: Record<string, any> & { findings?: TopicQualityFinding[] }; review_tasks?: any[]; resolution_logs?: any[]; correction_context?: Record<string, any>; confirmation?: Record<string, any>; report_plan?: Record<string, any>; report_snapshot?: Record<string, any>; output_markdown: string; output_json?: Record<string, any>; versions?: Record<string, any>; stage_artifacts?: Record<string, any>; process_trace?: Record<string, any[]>; model_calls?: any[]; analysis_policy?: Record<string, any>; parent_task_id?: string }
export interface TopicRunResponse { success: boolean; run_id: string; status: string; state: TopicState }
export interface TopicTask { id: string; task_id: string; title: string; task_type: string; status: string; current_stage: string; completed_steps: number; total_steps: number; needs_confirmation: boolean; confirmation_status: string; summary: string; created_at: string; updated_at: string; completed_at?: string | null; duration_seconds?: number | null }
export interface TopicTaskDetail extends TopicTask { raw_user_input: string; state?: TopicState | null; output_markdown: string; output_json: Record<string, any>; stage_trace: TopicStageTrace[]; error_message?: string | null }
export interface TopicTask { recovery?: GenerationRecovery | null }

async function headers(json = false): Promise<Record<string, string>> {
  const scope = await requirePlatformLogin()
  return json ? { 'Content-Type': 'application/json', 'X-User-Scope': scope } : { 'X-User-Scope': scope }
}
async function messageFor(response: Response): Promise<string> {
  return (await readApiError(response)).message
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...(await headers(Boolean(init.body))), ...(init.headers || {}) } })
  if (!response.ok) throw await readApiError(response)
  return response.json() as Promise<T>
}

export function runTopicSelection(payload: { user_input: string; topic_title?: string; language_preference?: string; year_range?: number[]; uploaded_files?: any[]; constraints?: string[]; exclusions?: string[]; analysis_mode?: 'deep' | 'fast'; candidate_count?: number; output_preference?: import('./homepageDraft').OutputPreference }) {
  return request<TopicRunResponse>('/v1/topic-selection/run', { method: 'POST', body: JSON.stringify(payload) })
}
export function listTopicSelectionTasks(params: { status?: string; search?: string; page?: number; pageSize?: number } = {}) {
  const query = new URLSearchParams({ page: String(params.page || 1), page_size: String(params.pageSize || 50) })
  if (params.status && params.status !== 'all') query.set('status', params.status)
  if (params.search?.trim()) query.set('search', params.search.trim())
  return request<{ items: TopicTask[]; total: number }>(`/v1/topic-selection/tasks?${query}`)
}
export function getTopicSelectionTask(taskId: string) { return request<{ success: boolean; data: TopicTaskDetail }>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}`).then((body) => body.data) }
export async function getTopicReportChart(taskId: string, chartKey: string): Promise<string> {
  const response = await fetch(`${API_BASE}/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/report/charts/${encodeURIComponent(chartKey)}`, { headers: await headers() })
  if (!response.ok) throw await readApiError(response)
  return response.status === 204 ? '' : URL.createObjectURL(await response.blob())
}
export function stopTopicSelectionTask(taskId: string) { return request<{ success: boolean; task_id: string }>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/stop`, { method: 'POST' }) }
export function deleteTopicSelectionTask(taskId: string) { return request<{ success: boolean; task_id: string }>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' }) }
export function submitTopicConfirmation(taskId: string, selectedOption: string, userNote = '') { return request<TopicRunResponse>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/confirmation`, { method: 'POST', body: JSON.stringify({ selected_option: selectedOption, user_note: userNote }) }) }
export function listTopicReviews(taskId: string) { return request<{ success: boolean; items: any[]; resolution_logs: any[] }>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/reviews`) }
export function resolveTopicReview(taskId: string, reviewTaskId: string, decision: string, reason = '', operator = '') { return request<TopicRunResponse>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/reviews/${encodeURIComponent(reviewTaskId)}`, { method: 'POST', body: JSON.stringify({ decision, reason, operator }) }) }
export function rerunTopicCorrection(taskId: string) { return request<TopicRunResponse>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/corrections/rerun`, { method: 'POST' }) }
export function rerunTopicRetrieval(taskId: string) { return request<TopicRunResponse>(`/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/retrieval-rerun`, { method: 'POST' }) }
export async function fetchTopicReportBlob(taskId: string, format: 'docx' | 'pdf' | 'html' | 'md' = 'docx', signal?: AbortSignal) {
  const response = await fetch(`${API_BASE}/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/export?format=${format}`, { headers: await headers(), signal })
  if (!response.ok) throw new Error(await messageFor(response))
  return { blob: await response.blob(), disposition: response.headers.get('content-disposition') || '' }
}
export async function fetchTopicReportPdf(taskId: string, signal?: AbortSignal): Promise<Blob> {
  return (await fetchTopicReportBlob(taskId, 'pdf', signal)).blob
}
export async function downloadTopicReport(taskId: string, format: 'docx' | 'pdf' | 'html' | 'md' = 'docx') {
  const { blob, disposition } = await fetchTopicReportBlob(taskId, format)
  const target = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = target
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]; const fallback = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  link.download = encoded ? decodeURIComponent(encoded) : (fallback || `智能选题正式报告.${format}`); document.body.appendChild(link); link.click(); window.setTimeout(() => { link.remove(); URL.revokeObjectURL(target) }, 1000)
}
export async function subscribeToTopicTaskStream(taskId: string, callbacks: { onChange: (detail: TopicTaskDetail) => void; onError?: () => void }) {
  const scope = await requirePlatformLogin(); const stream = new EventSource(`${API_BASE}/v1/topic-selection/tasks/${encodeURIComponent(taskId)}/stream?user_scope=${encodeURIComponent(scope)}`)
  const handle = (event: MessageEvent) => callbacks.onChange(JSON.parse(event.data) as TopicTaskDetail)
  stream.addEventListener('state', handle); stream.addEventListener('delta', handle); stream.addEventListener('complete', (event) => { handle(event as MessageEvent); stream.close() }); stream.onerror = () => callbacks.onError?.(); return () => stream.close()
}
