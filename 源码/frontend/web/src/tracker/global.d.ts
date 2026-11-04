import type { NstlTrackerInstance } from './nstl-tracker.esm'

declare global {
  interface Window {
    NstlTracker?: {
      init(options?: Record<string, unknown>): NstlTrackerInstance
      create(options?: Record<string, unknown>): NstlTrackerInstance
      getInstance(): NstlTrackerInstance | undefined
    }
    sendOtherLog?: (data?: Record<string, unknown>) => void
    hm_basic_log?: Record<string, unknown>
  }
}

export {}
