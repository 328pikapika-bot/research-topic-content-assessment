// Isolated replay of one real generated point. Never writes published tasks.
import base from './research-stages-vite.config.mjs'
import { readFileSync } from 'node:fs'

export default { ...base, server: { ...base.server, port: 18134 }, plugins: [{
  name: 'related-research-depth-preview',
  transformIndexHtml() { return [{ tag: 'link', attrs: { rel: 'stylesheet', href: '/frontend/web/src/styles/fonts.css' }, injectTo: 'head' }] },
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.includes('/v1/content-assessment/tasks/')) return next()
      const sample = new URL('../fixtures/related-research-depth-qa/preview-task.json', import.meta.url)
      const detail = JSON.parse(readFileSync(sample, 'utf8'))
      detail.id = detail.task_id = request.url.match(/\/tasks\/([^/?]+)/)[1]
      // This isolated preview covers analysis only, never the old report or
      // evaluation. Start on the completed analysis instead of a PDF export.
      detail.current_stage = detail.state.current_stage = 'comparison'
      detail.stage_trace = detail.state.stage_trace = detail.state.stage_trace
        .filter(stage => !['assessment', 'landscape', 'report', 'quality_gate'].includes(stage.stage_key))
      detail.completed_steps = detail.stage_trace.filter(stage => stage.status === 'completed').length
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify(detail))
    })
  },
}, ...base.plugins] }
