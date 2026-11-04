import { defineConfig } from '../../frontend/web/node_modules/vite/dist/node/index.js'
import vue from '../../frontend/web/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../../', import.meta.url))
export default defineConfig({ root, cacheDir: '.tmp/report-pdf-vite-cache', optimizeDeps: { exclude: ['pdfjs-dist/build/pdf.worker.min.mjs?url'] }, resolve: { alias: {
  vue: fileURLToPath(new URL('../../frontend/web/node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)),
  'pdfjs-dist': fileURLToPath(new URL('../../frontend/web/node_modules/pdfjs-dist', import.meta.url)),
} }, plugins: [vue(), { name: 'isolated-report-response', configureServer(server) {
  server.middlewares.use((request, response, next) => {
    if (!request.url?.includes('/v1/content-assessment/tasks/reference-')) return next()
    if (request.headers['x-user-scope'] !== 'platform_admin') { response.statusCode = 401; response.end('请先登录演示账号'); return }
    if (request.url.includes('reference-error')) { response.statusCode = 503; response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ detail: '测试：报告转换服务不可用，请重试或下载 Word。' })); return }
    response.setHeader('Content-Type', 'application/pdf'); response.setHeader('Content-Disposition', 'inline; filename="report.pdf"')
    response.end(readFileSync(new URL('../fixtures/report-reference-v2-qa/report.pdf', import.meta.url)))
  })
} }], server: { host: '127.0.0.1', port: 18126, strictPort: true, fs: { allow: [root] } } })
