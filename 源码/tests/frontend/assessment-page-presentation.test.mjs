import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/assessmentPagePresentation.ts', import.meta.url), 'utf8')
const comparisonSource = fs.readFileSync(new URL('../../frontend/web/src/services/comparisonEvidencePresentation.ts', import.meta.url), 'utf8')
const comparisonContext = { exports: {} }
vm.runInNewContext(ts.transpileModule(comparisonSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, comparisonContext)
const context = { exports: {}, require: () => comparisonContext.exports }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
const { assessmentTaskTitle: title, assessmentCitationSources: citations, analysisParagraphs: paragraphs } = context.exports

test('task API adapter preserves stage evaluations and reader comparisons before report publication', async () => {
  const rows = ['innovation', 'frontier', 'advancedness'].map(dimension => ({ dimension,
    text: '材料采用统一实验条件比较不同界面方案，为研究方法选择提供具体依据。', evidence_ids: ['E1'] }))
  const output_json = { reader_point_evaluations: { C1: rows },
    reader_evaluation_completion: { C1: { status: 'completed' } },
    reader_comparisons: { C1: { E1: { claim_id: 'C1', evidence_id: 'E1', difference: [] } } } }
  let current_stage = 'assessment'
  const snapshotContext = { exports: {} }
  const transpile = source => ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(transpile(fs.readFileSync(new URL('../../frontend/web/src/services/resultSnapshot.ts', import.meta.url), 'utf8')), snapshotContext)
  const adapterContext = { exports: {}, require: name => {
    if (name === './contentAssessmentApi') return { getContentAssessmentTask: async () => ({
      task_id: 'stage-reader', status: 'running', current_stage,
      state: { current_stage, output_json, claims: [], evidence_records: [], stage_trace: [] },
    }) }
    if (name === './resultSnapshot') return snapshotContext.exports
    if (name === './assessmentEvidencePresentation') return {}
    throw new Error(`Unexpected dependency: ${name}`)
  } }
  const adapterSource = fs.readFileSync(new URL('../../frontend/web/src/services/contentAssessmentUiApi.ts', import.meta.url), 'utf8')
    .replaceAll('import.meta.env', '({})')
  vm.runInNewContext(transpile(adapterSource), adapterContext)
  for (const stage of ['assessment', 'report', 'quality_gate']) {
    current_stage = stage
    const task = await adapterContext.exports.getContentAssessmentTask('stage-reader')
    assert.equal(context.exports.researchEvaluations(task.state, 'C1').length, 3)
    assert.equal(task.state.output_json.reader_comparisons, output_json.reader_comparisons)
    assert.equal(task.state.output_json.reader_evaluation_completion, output_json.reader_evaluation_completion)
  }
})

test('material titles use saved names then explicitly named input, never the type prompt', () => {
  const actual = '面向多中心实验的器官芯片屏障功能标准化检测与质量控制技术指南'
  assert.equal(title(actual, '我要评估的是：指南', ''), actual)
  assert.equal(title('', '我要评估的是：指南', `我要评估的是：指南\n指南名称：${actual}\n研究目标：建立流程`), actual)
  assert.equal(title('论文', '我要评估的是：论文', '', [{ name: '主论文.pdf', role: 'primary' }, { name: '参考指南.pdf', role: 'reference' }]), '主论文')
  assert.equal(title('', '我要评估的是：项目', '', [{ name: '参考论文.pdf', role: 'reference' }, { name: '指南.pdf', role: 'guideline' }]), '创新性评估任务')
  assert.equal(title(undefined, undefined, undefined), '创新性评估任务')
  assert.equal(title('', '指南', '', [{ name: '配套论文.pdf', role: 'REFERENCE' }]), '创新性评估任务')
  assert.equal(title('', '项目', '', [{ name: '参考.pdf', role: 'REFERENCE' }, { name: '芯片研究计划.docx', role: 'PROJECT' }]), '芯片研究计划')
})

test('citation mapping uses frozen numbers across reordering and omits missing or ambiguous entries', () => {
  const first = { evidence_id: 'A', title: '要点A' }, second = { evidence_id: 'B', title: '要点B' }
  const records = [second, first], before = JSON.stringify(records)
  const mapped = citations({ A: 12, B: 3, missing: 7 }, records)
  assert.equal(mapped[12], first)
  assert.equal(mapped[3], second)
  assert.equal(mapped[7], undefined)
  assert.equal(Object.keys(citations(undefined, records)).length, 0)
  assert.equal(Object.keys(citations({ A: 3, B: 3 }, records)).length, 0)
  assert.equal(JSON.stringify(records), before)
})

test('default conclusions preserve the complete first paragraph, citations and applicable conditions', () => {
  const first = '第一段中的方法比较[12]。适用于相同实验条件。'
  assert.equal(paragraphs(`${first}\r\n\r\n第二段说明差异[3]。`).lead, first)
  assert.equal(paragraphs(`${first}\r\n\r\n第二段说明差异[3]。`).rest, '第二段说明差异[3]。')
  assert.equal(paragraphs(first).lead, first)
  assert.equal(paragraphs(first).rest, '')
  assert.equal(paragraphs(undefined).lead, '')
})

test('pasted input and supplementary text never offer a file locator', () => {
  const visible = context.exports.hasUploadedClaimSource
  assert.equal(visible({ source_document_id: 'user_material' }, [{ file_id: 'user_material' }]), false)
  assert.equal(visible({ source_document_id: 'supplement-1' }, [{ file_id: 'file-1' }]), false)
  assert.equal(visible({ source_document_id: 'file-1', source_kind: 'material' }, [{ file_id: 'file-1' }]), true)
  assert.equal(visible({ source_document_id: 'file-1', source_kind: 'evidence' }, [{ file_id: 'file-1' }]), false)
})

test('evaluations preserve complete scholarly paragraphs and source IDs; snapshots cannot borrow runtime conclusions', () => {
  const evaluate = context.exports.researchEvaluations
  const rows = [
    { dimension: 'innovation', text: '具体方法的研究贡献。', evidence_ids: ['E1'] },
    { dimension: 'innovation', text: '与相关研究的具体差异。', evidence_ids: ['E2'] },
    { dimension: 'frontier', text: '方法与科学问题的联系。', evidence_ids: ['E1'] },
  ]
  const output_json = { reader_point_evaluations: { C1: rows } }
  const result = evaluate({ output_json }, 'C1')
  assert.equal(result.length, 2)
  assert.equal(result[0].lead, '具体方法的研究贡献。\n\n与相关研究的具体差异。')
  assert.deepEqual(Array.from(result[0].evidenceIds), ['E1', 'E2'])
  assert.equal(evaluate({ report_snapshot: { audit_data: {} }, output_json }, 'C1').length, 0)
  const state = { report_snapshot: { audit_data: { reader_point_evaluations: { C1: rows } } }, output_json }
  const before = JSON.stringify(state)
  assert.equal(evaluate(state, 'C1')[1].label, '前沿性')
  assert.equal(JSON.stringify(state), before)
})

test('published evaluations read final report paragraphs and their actual references, not earlier drafts', () => {
  const evaluate = context.exports.researchEvaluations
  const heading = text => ({ kind: 'heading', text })
  const paragraph = (claim_id, text, evidence_ids) => ({ kind: 'paragraph', claim_id, text, evidence_ids })
  const state = { report_snapshot: {
    sections: [{ section_key: 'judgement', blocks: [
      heading('6.1 创新性评价'),
      { kind: 'heading', claim_id: 'C1', text: '创新方法的研究' },
      paragraph('C1', '正式报告的方法判断[4]。', ['E4']),
      paragraph('C2', '另一要点的判断[8]。', ['E8']),
      heading('6.2 前沿性评价'), paragraph('C1', '正式报告的前沿分析。', ['E2', 'E1']),
      heading('6.3 先进性评价'), paragraph('C1', '正式报告的实验比较。', ['E3', 'E4', 'E5', 'E6']),
    ] }],
    audit_data: { reader_point_evaluations: { C1: [{ dimension: 'innovation', text: '旧稿评价', evidence_ids: ['OLD'] }] } },
  }, output_json: { reader_point_evaluations: { C1: [{ text: '运行中判断' }] } } }
  const before = JSON.stringify(state)
  const rows = evaluate(state, 'C1')
  assert.deepEqual(Array.from(rows, row => row.lead), ['正式报告的方法判断[4]。', '正式报告的前沿分析。', '正式报告的实验比较。'])
  assert.deepEqual(Array.from(rows, row => Array.from(row.evidenceIds)), [['E4'], ['E2', 'E1'], ['E3', 'E4', 'E5', 'E6']])
  assert.equal(evaluate(state, 'C2').length, 1)
  assert.equal(evaluate(state, 'missing').length, 0)
  assert.equal(JSON.stringify(state), before)
  const auditNotice = '没有经过全文锚点和字段级可比性校验的证据，不能独立判断先进性。。先进性判断不从创新性标签或关键词相似度推导。'
  const mixedState = { report_snapshot: { sections: [{ section_key: 'judgement', blocks: [
    heading('6.3 先进性评价'), paragraph('C1', `同一载量和压力下开展循环测试。 ${auditNotice}`, ['E4']),
    paragraph('C2', auditNotice, []),
  ] }] } }
  assert.equal(evaluate(mixedState, 'C1')[0].lead, '同一载量和压力下开展循环测试。')
  assert.equal(evaluate(mixedState, 'C2').length, 0)
})

test('an empty final judgement does not borrow audit conclusions, while older frozen reports retain fallback', () => {
  const evaluate = context.exports.researchEvaluations
  const audit_data = { reader_point_evaluations: { C1: [{ dimension: 'innovation', text: '历史冻结评价', evidence_ids: ['E1'] }] } }
  assert.equal(evaluate({ report_snapshot: { sections: [{ section_key: 'judgement', blocks: [] }], audit_data } }, 'C1').length, 0)
  assert.equal(evaluate({ report_snapshot: { sections: [], audit_data } }, 'C1')[0].lead, '历史冻结评价')
})

test('assessment dimensions expose one literature entry without the repeated group source list', () => {
  const page = fs.readFileSync(new URL('../../frontend/web/src/pages/TaskDetailPage.vue', import.meta.url), 'utf8')
  const section = page.split("activeStage.stage_key === 'assessment'")[1].split("activeStage.stage_key === 'landscape'")[0]
  assert.match(section, /label="查看相关文献" collapse-label="收起相关文献"/)
  assert.doesNotMatch(section, /查看评价依据|group.sources/)
  assert.match(page, /<h5>研究分析<\/h5>/)
})

test('retrieval keeps the real pool when the report cites only one source and restores saved order', () => {
  const select = context.exports.retrievalLiteratureForPoint
  const records = Array.from({ length: 36 }, (_, i) => ({ evidence_id: `E${i}`, claim_ids: ['C1'], source_type: 'literature' }))
  records.push({ evidence_id: 'OTHER', claim_ids: ['C2'], source_type: 'literature' })
  records.push({ evidence_id: 'INPUT', claim_ids: ['C1'], source_type: 'project_material' })
  const state = { output_json: { report_source_selection: { C1: ['E0'] }, retrieval_source_order: { C1: ['E3', 'E2'] } } }
  assert.equal(select(state, 'C1', records).length, 30)
  assert.deepEqual(Array.from(select(state, 'C1', records).slice(0, 3), e => e.evidence_id), ['E3', 'E2', 'E0'])
  const frozen = { ...state, report_snapshot: { audit_data: { report_source_selection: { C1: ['E0'] }, retrieval_source_order: { C1: ['E5', 'unknown', 'E4'] } } } }
  assert.deepEqual(Array.from(select(frozen, 'C1', records).slice(0, 3), e => e.evidence_id), ['E5', 'E4', 'E0'])
  assert.deepEqual(Array.from(select(state, 'C2', records), e => e.evidence_id), ['OTHER'])
  assert.equal(select({ ...state, report_snapshot: { audit_data: {} } }, 'C1', records)[0].evidence_id, 'E0')
})

test('point synthesis has its own anchored sources and frozen tasks never borrow runtime prose', () => {
  const read = context.exports.relatedResearchAnalysis
  const runtime = { C1: { claim_id: 'C1', material_summary: { text: '材料采用时间切分。' }, literature_summary: [
    { text: '两项相关研究分别检验随机切分和时间切分。', evidence_ids: ['E1', 'E2'], anchors: [{ evidence_id: 'E1' }, { evidence_id: 'E2' }] },
    { text: '没有来源的总结', evidence_ids: [], anchors: [] },
  ] } }
  const state = { output_json: { reader_research_analyses: runtime } }
  assert.equal(read(state, 'C1').material, '材料采用时间切分。')
  assert.deepEqual(Array.from(read(state, 'C1').literature), ['两项相关研究分别检验随机切分和时间切分。'])
  assert.equal(read(state, 'C2').literature.length, 0)
  assert.equal(read({ ...state, report_snapshot: { audit_data: {} } }, 'C1').literature.length, 0)
  assert.equal(read({ ...state, report_snapshot: { audit_data: { reader_research_analyses: runtime } } }, 'C1').literature.length, 1)
})

test('grounded comparison and synthesis sources stay visible independently of report selection', () => {
  const read = context.exports.comparisonLiteratureLinks
  const claim_evidence_links = ['E1', 'E2', 'E3', 'E4'].map(evidence_id => ({ claim_id: 'C1', evidence_id }))
  claim_evidence_links.push({ claim_id: 'C2', evidence_id: 'E2' })
  const audit_data = { report_source_selection: { C1: ['E1'] },
    reader_comparisons: { C1: { E2: { claim_id: 'C1', evidence_id: 'E2', difference: [] },
      E4: { claim_id: 'C2', evidence_id: 'E4' } } },
    reader_research_analyses: { C1: { claim_id: 'C1', literature_summary: [
      { text: '该研究采用不同的时间切分方法。', evidence_ids: ['E3'], anchors: [{ evidence_id: 'E3' }] },
    ] } },
  }
  const state = { claim_evidence_links, output_json: audit_data }
  assert.deepEqual(Array.from(read(state, 'C1'), row => row.evidence_id), ['E1', 'E2', 'E3'])
  const frozen = { ...state, report_snapshot: { audit_data,
    chart_manifest: [{ chart_key: 'claim_evidence_network', included: true, chart_data: { links: [] } }],
  } }
  const before = JSON.stringify(frozen)
  assert.deepEqual(Array.from(read(frozen, 'C1'), row => row.evidence_id), ['E2', 'E3'])
  assert.equal(JSON.stringify(frozen), before)
  assert.equal(read({ ...state, report_snapshot: { audit_data: { report_source_selection: { C1: [] } } } }, 'C1').length, 0)
})

test('saved stage evaluations remain readable before report, while drafting, after failure and after refresh', () => {
  const evaluate = context.exports.researchEvaluations
  const rows = ['innovation', 'frontier', 'advancedness'].map((dimension, i) => ({
    dimension, text: `该维度的具体研究评价${i}。`, evidence_ids: Array.from({ length: i + 1 }, (_, j) => `E${j}`),
  }))
  for (const current_stage of ['assessment', 'report', 'awaiting_confirmation']) {
    const state = { current_stage, output_json: { reader_point_evaluations: { C1: rows },
      scholarly_drafts: { C1: { judgement: [{ dimension: 'innovation', text: '其他草稿' }] } } } }
    const refreshed = JSON.parse(JSON.stringify(state))
    assert.equal(evaluate(refreshed, 'C1').length, 3)
    assert.deepEqual(Array.from(evaluate(refreshed, 'C1'), row => row.lead), rows.map(row => row.text))
    assert.equal(evaluate(refreshed, 'C2').length, 0)
  }
  const fallback = evaluate({ output_json: { reader_point_evaluations: { C1: [] },
    scholarly_drafts: { C1: { judgement: rows } } } }, 'C1')
  assert.equal(fallback.length, 3)
})

test('report evaluations fill missing dimensions without replacing accepted stage evaluations', () => {
  const evaluate = context.exports.researchEvaluations
  const accepted = { dimension: 'innovation', text: '评估阶段校核通过的创新性评价。', evidence_ids: ['E1'] }
  const state = { output_json: {
    reader_point_evaluations: { C1: [accepted] },
    scholarly_drafts: { C1: { judgement: [
      { dimension: 'innovation', text: '报告草稿中不同的创新性表述。', evidence_ids: ['E2'] },
      { dimension: 'frontier', text: '报告阶段补充的前沿性评价。', evidence_ids: ['E2'] },
      { dimension: 'advancedness', text: '报告阶段补充的先进性评价。', evidence_ids: ['E3'] },
    ] } },
  } }
  const rows = evaluate(state, 'C1')
  assert.deepEqual(Array.from(rows, row => row.label), ['创新性', '前沿性', '先进性'])
  assert.equal(rows[0].lead, accepted.text)
  assert.equal(rows[1].lead, '报告阶段补充的前沿性评价。')
  assert.equal(rows[2].lead, '报告阶段补充的先进性评价。')
})
