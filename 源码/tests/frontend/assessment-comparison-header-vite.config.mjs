// Isolated visual QA of the real page with a saved, completed task. No writes.
import base from './research-stages-vite.config.mjs'
import { readFileSync } from 'node:fs'

const sample = new URL('../fixtures/assessment-comparison-header-qa/', import.meta.url)
export default { ...base, server: { ...base.server, port: 18133 }, plugins: [{
  transformIndexHtml() { return [{ tag: 'link', attrs: { rel: 'stylesheet', href: '/frontend/web/src/styles/fonts.css' }, injectTo: 'head' }] },
  name: 'saved-assessment-comparison', configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.includes('/v1/content-assessment/tasks/')) return next()
      if (request.url.includes('/export')) {
        response.setHeader('Content-Type', 'application/pdf')
        response.end(readFileSync(new URL('assessment.pdf', sample)))
      } else {
        response.setHeader('Content-Type', 'application/json')
        const detail = JSON.parse(readFileSync(new URL('task.json', sample), 'utf8'))
        detail.id = detail.task_id = request.url.match(/\/tasks\/([^/?]+)/)[1]
        // Replay the saved stage appraisals without a report, never regenerate or
        // write to the actual task. The URL explicitly labels this QA scenario.
        if (String(request.headers.referer || '').includes('before-report=1')) {
          const state = detail.state
          state.report_snapshot = null
          state.output_markdown = detail.output_markdown = ''
          detail.status = 'running'
          detail.current_stage = state.current_stage = 'assessment'
          for (const stage of state.stage_trace) {
            if (['landscape', 'quality_gate', 'report'].includes(stage.stage_key)) {
              stage.status = 'pending'
              stage.started_at = stage.ended_at = null
              stage.duration_ms = null
            }
          }
          detail.stage_trace = state.stage_trace
        }
        response.end(JSON.stringify(detail))
      }
    })
  },
}, ...base.plugins] }
