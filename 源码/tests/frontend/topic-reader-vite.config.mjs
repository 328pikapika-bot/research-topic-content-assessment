import { defineConfig } from '../../frontend/web/node_modules/vite/dist/node/index.js'
import vue from '../../frontend/web/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../../', import.meta.url))
const fixture = fileURLToPath(new URL('./topic-reader-fixtures.ts', import.meta.url))
export default defineConfig({ root, publicDir: 'frontend/web/public', cacheDir: '.tmp/topic-reader-vite-cache', resolve: { alias: [
  ...['topicSelectionApi', 'contentAssessmentApi', 'contentAssessmentUiApi', 'nstlAuth'].map(name => ({ find: new RegExp(`^\\.\\./services/${name}$`), replacement: fixture })),
  { find: 'vue', replacement: fileURLToPath(new URL('../../frontend/web/node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)) },
  { find: 'pdfjs-dist', replacement: fileURLToPath(new URL('../../frontend/web/node_modules/pdfjs-dist', import.meta.url)) },
] }, optimizeDeps: { exclude: ['pdfjs-dist/build/pdf.worker.min.mjs?url'] }, plugins: [vue()], server: { host: '127.0.0.1', port: 18129, strictPort: true, fs: { allow: [root] } } })
