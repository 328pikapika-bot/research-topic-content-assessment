import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/topicEvidencePresentation.ts', import.meta.url), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { topicReaderText } = context.exports

test('reader hides counts but keeps usable excerpts and evidence limits', () => {
  assert.equal(topicReaderText('文献 108 篇，全文覆盖 0 篇（0 段）。'), '文献 108 篇。')
  assert.equal(topicReaderText('摘要 108、全文 0'), '检索样本')
  assert.equal(topicReaderText('待补证据方向'), '探索方向')
  assert.equal(topicReaderText('原文片段：结果显示无显著差异'), '原文片段：结果显示无显著差异')
  assert.equal(topicReaderText('返回 100 个候选分块，最终采用 3 段。'), '已完成相关文献依据筛选。')
})

test('history cleanup uses authoritative preview and protects active tasks', () => {
  const page = fs.readFileSync(new URL('../../frontend/web/src/pages/TaskHistoryPage.vue', import.meta.url), 'utf8')
  assert.match(page, /previewHistoryCleanup/)
  assert.match(page, /eligible_count/)
  assert.match(page, /clearTaskHistory/)
  assert.match(page, /canDelete/)
  assert.match(page, /正在运行/)
})
