import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../../frontend/web/src/services/uploadPresentation.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { uploadPresentation, materialSummary, associatedLiteratureCount } = context.exports

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

test('uploaded material details show a concise analysis summary instead of parsed source text', () => {
  const excerpt = '全文解析内容。'.repeat(400)
  const document = { document_id: 'FILE-2', name: '项目方案.pdf', status: 'parsed', excerpt }
  const state = { input_profile: {
    title: '城市热暴露与绿地结构研究',
    research_question: '比较绿地结构和树冠覆盖对夏季热暴露的影响。'.repeat(20),
    material_summary: '项目采用遥感和地面观测评估城市绿地的降温作用及其空间差异。',
  }, term_map: [{ preferred: '遥感' }, { preferred: '地面观测' }] }
  const summary = materialSummary(document, state)
  assert.match(summary, /^材料概括：项目采用遥感/)
  assert(!summary.includes(excerpt))
  assert(summary.length < 260)
  assert.match(materialSummary({ summary: '单份材料的短摘要。', excerpt }, state), /单份材料的短摘要/)
  const legacy = materialSummary({ excerpt }, {
    input_profile: { title: state.input_profile.title, research_question: state.input_profile.research_question },
    term_map: state.term_map,
  })
  assert(legacy.includes('研究主题：城市热暴露与绿地结构研究'))
  assert(!legacy.includes(excerpt))
  assert.equal(materialSummary({ excerpt: '', parse_warnings: [] }, {}), '该历史任务未保存独立的材料概括。')
})
test('literature count is not capped by summary preview and merges source aliases', () => {
  const records = Array.from({ length: 8 }, (_, i) => ({ evidence_id: `E${i}`, doi: `10.1000/${i}`, title: `文献${i}` }))
  records.push({ evidence_id: 'alias', doi: '10.1000/0', title: '文献0' })
  records.push({ evidence_id: 'doi-url-alias', url: 'https://doi.org/10.1000/0', title: '文献0' })
  const links = records.map(record => ({ claim_id: 'C1', evidence_id: record.evidence_id }))
  assert.equal(associatedLiteratureCount({ id: 'C1', evidenceRefs: ['E0'] }, links, records), 8)
})
