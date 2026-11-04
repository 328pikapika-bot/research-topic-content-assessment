// Isolated product pages use synthetic report data and no application database.
import base from './report-reading-vite.config.mjs'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
export default { ...base, server: { ...base.server, port: 18131 },
  resolve: { ...base.resolve, alias: [
    { find: /^(?:\.\/nstlAuth|.*\/services\/nstlAuth)$/, replacement: fileURLToPath(new URL('./research-stage-auth.ts', import.meta.url)) },
    ...Object.entries(base.resolve.alias).map(([find, replacement]) => ({ find, replacement })),
  ] },
  plugins: [{ name: 'research-stage-samples', configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.includes('/v1/content-assessment/') || !request.url.includes('reading-qa')) return next()
      if (request.url.includes('/export')) {
        response.setHeader('Content-Type', 'application/pdf')
        response.end(readFileSync(new URL('../fixtures/research-report-v6-qa/project.pdf', import.meta.url)))
        return
      }
      if (!request.url.includes('/tasks/')) return next()
      const state = JSON.parse(readFileSync(new URL('../fixtures/research-report-v6-qa/state.json', import.meta.url), 'utf8'))
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ id: 'reading-qa', task_id: 'reading-qa', title: state.assessment_profile.title,
        task_type: '创新性评估', status: 'completed', current_stage: 'report', raw_user_input: state.raw_user_input,
        completed_steps: 8, total_steps: 8, created_at: '2026-10-14T10:00:00+08:00', updated_at: '2026-10-14T10:00:00+08:00',
        state, stage_trace: state.stage_trace, output_json: {}, output_markdown: state.output_markdown }))
    })
  } }, ...base.plugins] }
