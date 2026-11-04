import { defineConfig } from '../../frontend/web/node_modules/vite/dist/node/index.js'
import vue from '../../frontend/web/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../../', import.meta.url))
export default defineConfig({ root, publicDir: 'frontend/web/public', cacheDir: '.tmp/report-reading-vite-cache', resolve: { alias: {
  vue: fileURLToPath(new URL('../../frontend/web/node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)),
  'pdfjs-dist': fileURLToPath(new URL('../../frontend/web/node_modules/pdfjs-dist', import.meta.url)),
} }, optimizeDeps: { exclude: ['pdfjs-dist/build/pdf.worker.min.mjs?url'] }, plugins: [vue(), {
  name: 'isolated-report-reading', configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.includes('/v1/') || !request.url.includes('reading-qa')) return next()
      if (request.url.includes('/export')) {
        response.setHeader('Content-Type', 'application/pdf')
        response.end(readFileSync(new URL(`../fixtures/report-reading-qa/${request.url.includes('/topic-selection/') ? 'topic' : 'assessment'}.pdf`, import.meta.url)))
        return
      }
      if (!request.url.includes('/tasks/')) return next()
      const state = JSON.parse(readFileSync(new URL('../fixtures/report-reading-qa/state.json', import.meta.url), 'utf8'))
      const id = request.url.match(/reading-qa[\w-]*/)[0]
      if (id.endsWith('-upload') || id.endsWith('-failed')) {
        const failed = id.endsWith('-failed')
        state.uploaded_files = [{ file_id: 'FILE-1', name: '研究方案.pdf', type: 'application/pdf' }]
        state.assessment_profile.document_records = [{ document_id: 'FILE-1', name: '研究方案.pdf', role: 'PROJECT', status: failed ? 'failed' : 'parsed', excerpt: failed ? '' : '研究方案内容', error_message: failed ? '文件内容无法解析，请检查文件格式。' : '' }]
      } else if (id.endsWith('-legacy')) {
        delete state.uploaded_files
        delete state.uploaded_file_context
      }
      if (request.url.includes('/topic-selection/')) {
        const keys = ['input_modeling', 'theme_decomposition', 'retrieval_evidence', 'landscape_trend', 'crosslink_gap', 'candidate_generation', 'scoring_ranking', 'quality_gate', 'report_publish']
        const topic = { raw_user_input: '研究任务隔离验收', current_stage: 'input_modeling', input_profile: { title: '科研选题任务', documents: state.assessment_profile.document_records }, theme_tree: { nodes: [], edges: [] }, evidence_pool: [],
          ...(state.uploaded_files ? { uploaded_files: state.uploaded_files } : {}),
          stage_trace: keys.map(stage_key => ({ stage_key, status: 'completed', summary: '已完成' })), report_plan: { sections: [] }, quality_gate: {} }
        response.setHeader('Content-Type', 'application/json')
        response.end(JSON.stringify({ success: true, data: { id, task_id: id, title: '科研选题任务', status: 'completed', current_stage: 'input_modeling', completed_steps: 9, total_steps: 9, raw_user_input: topic.raw_user_input, state: topic, stage_trace: topic.stage_trace } }))
        return
      }
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({
        id, task_id: id, title: '隔离验收研究任务', task_type: '创新性评估', status: 'completed',
        raw_user_input: '隔离验收：以文字材料评估研究方案', created_at: '2026-10-10T10:00:00+08:00', updated_at: '2026-10-10T10:01:00+08:00',
        completed_steps: 8, total_steps: 8, current_stage: 'report', state, stage_trace: state.stage_trace, output_json: {}, output_markdown: '',
      }))
    })
  },
}], server: { host: '127.0.0.1', port: 18127, strictPort: true, fs: { allow: [root] } } })
