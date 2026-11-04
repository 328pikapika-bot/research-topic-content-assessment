export interface NstlTrackerInstance {
  trackPage(data?: Record<string, unknown>): void
  track(data?: Record<string, unknown>): void
  trackOther(data?: Record<string, unknown>): void
}

export function init(options?: Record<string, unknown>): NstlTrackerInstance
export function create(options?: Record<string, unknown>): NstlTrackerInstance
export function getInstance(): NstlTrackerInstance | undefined

declare const NstlTracker: {
  init: typeof init
  create: typeof create
  getInstance: typeof getInstance
}

export default NstlTracker
