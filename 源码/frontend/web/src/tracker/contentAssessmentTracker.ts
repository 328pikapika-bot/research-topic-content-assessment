export type TrackerEventPayload = Record<string, string | number | boolean | null | undefined>
export type TrackerResult = 'success' | 'failure' | 'blocked' | 'cancelled'
export type ContentAssessmentPage = 'home' | 'tasks' | 'task_running' | 'task_detail' | 'sample_detail'

type TrackerInstance = {
  trackPage?: (data?: Record<string, unknown>) => void
  track?: (data?: Record<string, unknown>) => void
  trackOther?: (data?: Record<string, unknown>) => void
}

const TRACKER_ORIGIN = 'https://www.nstl.gov.cn'
const DEFAULT_TRACKER_MODE_ID = 'deepseek-reasoner'
const SAFE_TEXT_MAX = 80
const EVENT_DEDUPE_MS = 800
const blockedExtraKeyPattern = /(raw|content|contact|filename|file_name|title|abstract|description|user_input|feedback|paper|doi|url)$/i
const pagePathMap: Record<ContentAssessmentPage, string> = {
  home: '/content-assessment/home',
  tasks: '/content-assessment/tasks',
  task_running: '/content-assessment/task-running',
  task_detail: '/content-assessment/task-detail',
  sample_detail: '/content-assessment/sample-detail',
}

let lastEventKey = ''
let lastEventAt = 0

export function resolveTrackerUrl(path: '/hm.gif' | '/log.gif'): string {
  if (typeof window === 'undefined') return path

  const { hostname, port } = window.location
  const isNstlDomain = hostname === 'nstl.gov.cn' || hostname.endsWith('.nstl.gov.cn')
  const isNonDefaultPort = Boolean(port && port !== '80' && port !== '443')
  const isLocalHost = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'

  if (isNstlDomain && !isNonDefaultPort && !isLocalHost) return path
  return `${TRACKER_ORIGIN}${path}`
}

export function trackerOptions() {
  return {
    hmUrl: resolveTrackerUrl('/hm.gif'),
    logUrl: resolveTrackerUrl('/log.gif'),
    autoTrackPage: false,
    autoTrackUnload: false,
    autoTrackClick: true,
    debug: import.meta.env.VITE_TRACKER_DEBUG === 'true',
    extraBaseParams: {
      app: 'content-assessment',
      ver: import.meta.env.VITE_APP_VERSION || '0.1.0',
      mode_id: import.meta.env.VITE_TRACKER_MODE_ID || DEFAULT_TRACKER_MODE_ID,
    },
  }
}

export function logValue(type: string, text: string, extra: TrackerEventPayload = {}) {
  return {
    type,
    text: safeText(text),
    ...sanitizeExtra(extra),
  }
}

export async function questionFingerprint(value: string): Promise<string | undefined> {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized || !globalThis.crypto?.subtle) return undefined

  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(normalized))
  return Array.from(new Uint8Array(digest))
    .slice(0, 8)
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

export function trackPage(page: ContentAssessmentPage, extra: TrackerEventPayload = {}) {
  const tracker = getTracker()
  if (!tracker?.trackPage) return

  tracker.trackPage({
    ph: pagePathMap[page],
    u: typeof window !== 'undefined' ? window.location.href : '',
    tt: typeof document !== 'undefined' ? document.title : '',
    page_type: page,
    ...sanitizeExtra(extra),
  })
}

export function trackEvent(type: string, text: string, extra: TrackerEventPayload = {}) {
  const payload = {
    type,
    text: safeText(text),
    module: 'content-assessment',
    path: typeof window !== 'undefined' ? window.location.pathname : '',
    ...sanitizeExtra(extra),
  }
  if (isDuplicateEvent(payload)) return

  const tracker = getTracker()
  if (tracker?.track) {
    tracker.track(payload)
    return
  }
  if (typeof window !== 'undefined' && typeof window.sendOtherLog === 'function') {
    window.sendOtherLog(payload)
  }
}

export function trackOutcome(
  type: string,
  text: string,
  result: TrackerResult,
  extra: TrackerEventPayload = {},
) {
  trackEvent(type, text, {
    result,
    ...extra,
  })
}

export function errorKind(error: unknown): string {
  if (error instanceof Error && error.message) {
    if (/401|unauthorized|login/i.test(error.message)) return 'unauthorized'
    if (/rate[_ -]?limit|too many/i.test(error.message)) return 'rate_limit'
    if (/network|fetch/i.test(error.message)) return 'network'
  }
  return 'unknown'
}

function getTracker(): TrackerInstance | undefined {
  if (typeof window === 'undefined') return undefined
  return window.NstlTracker?.getInstance?.()
}

function sanitizeExtra(extra: TrackerEventPayload) {
  const sanitized: TrackerEventPayload = {}
  const hasJobId = extra.job_id !== undefined

  for (const [key, value] of Object.entries(extra)) {
    if (value === undefined || blockedExtraKeyPattern.test(key)) continue
    if (key === 'task_id' && hasJobId) continue

    const normalizedKey = key === 'task_id' ? 'job_id' : key
    sanitized[normalizedKey] = sanitizeValue(value)
  }

  return sanitized
}

function sanitizeValue(value: string | number | boolean | null | undefined) {
  if (typeof value !== 'string') return value
  return safeText(value)
}

function safeText(value: string) {
  return String(value || '').trim().slice(0, SAFE_TEXT_MAX)
}

function isDuplicateEvent(payload: Record<string, unknown>) {
  const now = Date.now()
  const key = JSON.stringify({
    type: payload.type,
    text: payload.text,
    result: payload.result,
    job_id: payload.job_id,
    stage_key: payload.stage_key,
  })
  if (key === lastEventKey && now - lastEventAt < EVENT_DEDUPE_MS) return true
  lastEventKey = key
  lastEventAt = now
  return false
}
