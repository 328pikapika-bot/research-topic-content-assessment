import type { App } from 'vue'

declare const NstlTrackerVue: {
  install(app: App, options?: Record<string, unknown>): unknown
}

export default NstlTrackerVue
