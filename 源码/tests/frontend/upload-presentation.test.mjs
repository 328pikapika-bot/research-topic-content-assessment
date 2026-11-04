import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../../frontend/web/src/services/uploadPresentation.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { uploadPresentation, associatedLiteratureCount } = context.exports

test('text input is not a file; absent legacy metadata remains unknown', () => {
  assert.equal(uploadPresentation({ uploaded_files: [] }, [{ document_id: 'user_material', name: '用户输入材料' }]).hasFiles, false)
  assert.equal(uploadPresentation({ uploaded_files: [] }, []).message, '您尚未上传文件，本次分析基于您输入的内容进行。')
  assert.equal(uploadPresentation({}, []).message, '该历史任务未记录上传文件信息')
  assert.equal(uploadPresentation({}, [{ document_id: 'user_material', name: '用户输入材料' }]).message, '该历史任务未记录上传文件信息')
})
test('API display conversion does not manufacture historical upload records', () => {
  const apiSource = fs.readFileSync(new URL('../../frontend/web/src/services/contentAssessmentUiApi.ts', import.meta.url), 'utf8')
    .replace(/import\.meta\.env/g, '({})') + '\nexport { mapState }\n'
  const snapshotContext = { exports: {} }
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../../frontend/web/src/services/resultSnapshot.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, snapshotContext)
  const apiContext = { exports: {}, require: name => name === './resultSnapshot' ? snapshotContext.exports : {} }
  vm.runInNewContext(ts.transpileModule(apiSource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, apiContext)
  const legacy = apiContext.exports.mapState({ raw_user_input: '研究方案', assessment_profile: { document_records: [] } })
  assert.equal(uploadPresentation(legacy, []).message, '该历史任务未记录上传文件信息')
  const knownEmpty = apiContext.exports.mapState({ raw_user_input: '研究方案', uploaded_files: [], assessment_profile: { document_records: [] } })
  assert.equal(uploadPresentation(knownEmpty, []).message, '您尚未上传文件，本次分析基于您输入的内容进行。')
  const legacyFile = { document_id: 'FILE-1', name: '材料.pdf', status: 'failed' }
  const uploaded = apiContext.exports.mapState({ raw_user_input: '研究方案', assessment_profile: { document_records: [legacyFile] } })
  assert.equal(uploadPresentation(uploaded, uploaded.assessment_profile.document_records).hasFiles, true)
})
test('failed parsing stays an uploaded file, including definite legacy records', () => {
  const docs = [{ document_id: 'FILE-1', name: '研究.pdf', status: 'failed', error_message: '解析失败' }]
  assert.equal(uploadPresentation({ uploaded_files: [{ file_id: 'FILE-1' }] }, docs).documents[0].status, 'failed')
  assert.equal(uploadPresentation({}, docs).hasFiles, true)
})
test('literature count is not capped by summary preview and merges source aliases', () => {
  const records = Array.from({ length: 8 }, (_, i) => ({ evidence_id: `E${i}`, doi: `10.1000/${i}`, title: `文献${i}` }))
  records.push({ evidence_id: 'alias', doi: '10.1000/0', title: '文献0' })
  records.push({ evidence_id: 'doi-url-alias', url: 'https://doi.org/10.1000/0', title: '文献0' })
  const links = records.map(record => ({ claim_id: 'C1', evidence_id: record.evidence_id }))
  assert.equal(associatedLiteratureCount({ id: 'C1', evidenceRefs: ['E0'] }, links, records), 8)
})
