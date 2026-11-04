import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/comparisonEvidencePresentation.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const helperSource = fs.readFileSync(new URL('../../frontend/web/src/services/researchPresentation.ts', import.meta.url), 'utf8')
const helper = { exports: {} }
vm.runInNewContext(ts.transpileModule(helperSource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, helper)
const context = { exports: {}, require: () => helper.exports }
vm.runInNewContext(compiled, context)
const { comparisonEvidenceDetail: present, comparisonFieldSummary, comparisonResearchAnalysis, comparisonReaderText, readerComparisonFor } = context.exports

const record = { evidence_id: 'E-L-001', title: '技术研究', authors: ['王甲', '李乙'], year: 2024,
  abstract: '摘要中的实际研究结果。', fulltext_available: true, anchor_verified: true,
  comparison_fields: { object: '对象 A', method: '方法 B', metric: '指标 C', time: '6 个月' } }
const link = { claim_id: 'C-001', evidence_id: 'E-L-001', relation: 'supports',
  evidence_chunk_ids: ['CH-1'], field_matrix: { object: 'covered', method: 'partial', metric: 'conflict', time: 'not_comparable' },
  incomparability_reasons: ['指标口径不同'], uncertainty: '仅在相同测试条件下适用。' }
const chunk = { evidence_id: 'E-L-001', chunk_id: 'CH-1', claim_ids: ['C-001'],
  locator: { display: '结果第 2 节' }, text: '原文记录的实验结果。', reopenable: true }

test('verified fulltext shows saved locator, excerpt and only assessed Chinese fields', () => {
  const view = present(link, [record], [chunk], { claim_id: 'C-001', text: '主张', claim_fields: { object: '对象 A', method: '方法 B', metric: '指标 D' } })
  assert.equal(view.sourceKind, '正文片段')
  assert.equal(view.location, '结果第 2 节')
  assert.equal(view.excerpt, chunk.text)
  assert.equal(view.fields.length, 3)
  assert.equal(view.fields.find(item => item.key === 'object').status, '已对应')
  assert.equal(view.fields.find(item => item.key === 'method').status, '部分对应')
  assert.equal(view.fields.find(item => item.key === 'metric').status, '存在差异')
  assert.equal(view.fields.find(item => item.key === 'time'), undefined)
  assert.equal(view.fields.find(item => item.key === 'data'), undefined)
  assert.equal(view.fields.find(item => item.key === 'object').claimValue, '对象 A')
  assert.equal(view.fields.find(item => item.key === 'object').evidenceValue, '对象 A')
  assert.equal(view.differences[0], '指标口径不同')
})

test('empty field matrix does not render a fake seven-item comparison', () => {
  const view = present({ ...link, field_matrix: {}, incomparability_reasons: ['缺少对象字段', '缺少方法字段'] }, [record], [])
  assert.equal(view.fields.length, 0)
  assert.equal(view.differences.length, 0)
  assert.equal(comparisonFieldSummary({ ...link, field_matrix: {} }), '')
})

test('partial support with abstract is never presented as verified fulltext', () => {
  const view = present({ ...link, relation: 'partially_supports' }, [record], [])
  assert.equal(view.sourceKind, '文献摘要')
  assert.equal(view.excerpt, record.abstract)
  assert.equal(view.location, '')
})

test('related, counter and insufficient relations retain their stored differences', () => {
  for (const relation of ['not_comparable', 'contradicts', 'insufficient_evidence']) {
    const view = present({ ...link, relation }, [record], [chunk])
    assert.equal(view.differences[0], '指标口径不同')
    assert.equal(view.uncertainty, link.uncertainty)
  }
})

test('unverified excerpt and missing record do not invent a source location', () => {
  const saved = present(link, [{ ...record, abstract: '', excerpt: '保存的片段', anchor_verified: false }], [chunk])
  assert.equal(saved.sourceKind, '文献片段')
  assert.equal(saved.location, '')
  const missing = present(link, [], [])
  assert.equal(missing.hasRecord, false)
  assert.equal(missing.excerpt, '')
  assert.equal(missing.location, '')
  assert.equal(missing.sourceKind, '文献信息')
})

test('a search topic is not treated as a fulltext location', () => {
  const topicOnly = present(link, [{ ...record, query_group: '界面研究' }], [{ ...chunk, locator: {}, section_ref: '界面研究' }])
  assert.equal(topicOnly.location, '')
  assert.equal(topicOnly.sourceKind, '正文片段')
  const withPage = present(link, [record], [{ ...chunk, locator: {}, page_ref: '第 3 页' }])
  assert.equal(withPage.location, '第 3 页')
  const unverified = present(link, [record], [{ ...chunk, reopenable: false }])
  assert.equal(unverified.location, '')
  assert.notEqual(unverified.sourceKind, '正文片段')
})

test('reader detail omits audit counts and missing field diagnostics but retains actual research limits', () => {
  const analysis = comparisonResearchAnalysis({ ...link,
    similarity: '字段覆盖0/7；两项研究均采用微流控灌注。',
    difference: '关键词交集0项；缺少方法字段；文献缺少长期随访结果。',
    incomparability_reasons: ['缺少对象字段', '实验温度不同。'],
    uncertainty: '验证条件为37摄氏度培养。',
  })
  assert.equal(analysis.similarity, '两项研究均采用微流控灌注。')
  assert.match(analysis.difference, /文献缺少长期随访结果/)
  assert.equal(analysis.conditions, '')
  assert.doesNotMatch(analysis.difference, /关键词交集|缺少方法字段|缺少对象字段/)
  assert.equal(comparisonReaderText('字段覆盖0/7；关键词交集0项；'), '')
})

test('audit availability and relation labels are removed without hiding actual methods or metric conditions', () => {
  const raw = { ...link,
    similarity: '证据未覆盖方法。支持或矛盾无法判断。两项研究均采用时间切分。',
    difference: '文献未涉及主张中的具体方法；材料未采用随机切分，采用时间切分。文献缺少长期随访结果。',
    uncertainty: '文献为摘要依据，无法确认全文是否涉及PICO拆解；指标不同，无法直接比较效果。',
    incomparability_reasons: ['B级摘要或部分文本仅支持方向性判断，待全文核验', '指标不同，无法直接比较效果。'],
  }
  const before = JSON.stringify(raw)
  const analysis = comparisonResearchAnalysis(raw)
  assert.equal(analysis.similarity, '两项研究均采用时间切分。')
  assert.equal(analysis.difference, '材料未采用随机切分，采用时间切分。文献缺少长期随访结果。')
  assert.equal(analysis.conditions, '')
  assert.equal(JSON.stringify(raw), before)
  assert.deepEqual(Object.values(comparisonResearchAnalysis({ ...link,
    similarity: '字段覆盖0/7；关键词交集0项。', difference: '缺少对象字段；缺少可重新打开的全文字符锚点',
    uncertainty: '证据未涉及指标。', incomparability_reasons: ['B级摘要或部分文本仅支持方向性判断，待全文核验'],
  })), ['', '', ''])
  const savedBattery = comparisonResearchAnalysis({ ...link,
    difference: '证据E-L-048涉及原位聚合制备聚合物固态电解质，循环230次后118 mAh/g，但未涉及硫化物界面，也未比较三种方案。',
    uncertainty: '证据等级B且为摘要，数据来自不同体系，无法直接支持主张中的界面方案比较。',
    incomparability_reasons: ['证据等级B且为摘要，仅提供方向性支持。', '证据为综述性摘要，未提供具体实验数据或方案比较。', '文献使用磷酸铁锂正极而非高镍正极。'],
  })
  // An ambiguous historical sentence is not rewritten into a positive finding.
  assert.equal(savedBattery.difference, '')
  assert.equal(savedBattery.conditions, '')
})

test('new reader comparisons use anchored pairs and never borrow mutable prose for frozen tasks', () => {
  const anchored = text => ({ text, material_quote: '材料采用时间切分。', literature_quote: '文献采用随机切分。' })
  const reader = { claim_id: link.claim_id, evidence_id: link.evidence_id,
    similarity: [anchored('两者分析相同研究对象。')],
    difference: [anchored('材料未采用随机切分，文献采用随机切分。')],
    conditions: [anchored('材料在高温下性能下降，文献在室温下测试。')] }
  const state = { output_json: { reader_comparisons: { [link.claim_id]: { [link.evidence_id]: reader } } } }
  const view = comparisonResearchAnalysis(link, readerComparisonFor(state, link))
  assert.equal(view.conditions, reader.conditions[0].text)
  assert.equal(view.difference, reader.difference[0].text)
  assert.equal(readerComparisonFor({ ...state, report_snapshot: { audit_data: {} } }, link), undefined)
  const frozen = { ...state, report_snapshot: { audit_data: state.output_json } }
  assert.equal(readerComparisonFor(frozen, link), reader)
  assert.equal(readerComparisonFor(state, { ...link, claim_id: 'other' }), undefined)
  assert.equal(comparisonResearchAnalysis(link, { ...reader, conditions: [], difference: [] }).difference, '')
  assert.equal(comparisonResearchAnalysis(link, { ...reader, conditions: [{ text: '只有单方数据。', material_quote: '材料', literature_quote: '' }] }).conditions, '')
})

test('actual user-reported diagnostics disappear while real negative results remain unchanged', () => {
  const analysis = comparisonResearchAnalysis({ ...link,
    similarity: '字段覆盖0/7；两项研究比较相同对象。',
    difference: '摘要证据无法确认全文。该文献未涉及目标方法。不能支撑目标。高温下性能下降。材料未采用随机切分，采用时间切分。',
    uncertainty: '摘要证据无法确认全文。' })
  assert.equal(analysis.similarity, '两项研究比较相同对象。')
  assert.equal(analysis.difference, '高温下性能下降。材料未采用随机切分，采用时间切分。')
  assert.equal(analysis.conditions, '')
  assert.equal(comparisonResearchAnalysis({ ...link,
    difference: '该方法不能支持在线更新，采用离线批量训练。' }).difference,
    '该方法不能支持在线更新，采用离线批量训练。')
})

test('comparison UI uses frozen conclusions with lightweight sources and omits internal-id disclosure', () => {
  const page = fs.readFileSync(new URL('../../frontend/web/src/pages/TaskDetailPage.vue', import.meta.url), 'utf8')
  const component = fs.readFileSync(new URL('../../frontend/web/src/components/ComparisonEvidenceDetail.vue', import.meta.url), 'utf8')
  assert.match(page, /label="查看详细分析" collapse-label="收起详细分析"/)
  const analysis = page.split("activeStage.stage_key === 'comparison'")[1].split("activeStage.stage_key === 'assessment'")[0]
  assert.equal((analysis.match(/<ResultDisclosure\b/g) || []).length, 1)
  assert.doesNotMatch(analysis, /ComparisonEvidenceDetail|查看原文与指标|文献摘要/)
  assert.match(analysis, /<LiteratureReferences[^\n]+show-abstract/)
  assert.doesNotMatch(page, /LiteratureCard v-for="item in group.sources"/)
  assert.match(page, /frozenPointResult\(taskState.value\?\.report_snapshot, claimId, judgement, '\\n\\n'\)/)
  assert.doesNotMatch(page, /内部证据编号：\{\{ claim\.evidenceRefs/)
  assert.match(component, /label="查看原文与指标" collapse-label="收起原文与指标"/)
  assert.match(component, /v-if="hasUsefulDetail"/)
  assert.doesNotMatch(component, /尚未形成逐项同口径比对/)
  assert.match(component, /target="_blank" rel="noopener noreferrer"/)
  assert.match(component, /v-if="url"/)
})

test('a category label is not a concrete difference, but named methods and conditions remain', () => {
  assert.equal(comparisonResearchAnalysis({ ...link, difference: '研究对象不同。研究问题不同。' }).difference, '')
  assert.equal(comparisonResearchAnalysis({ ...link, difference: '材料采用时间切分，文献采用随机切分。' }).difference,
    '材料采用时间切分，文献采用随机切分。')
})

test('analysis preserves natural paragraphs and optional multiple source anchors', () => {
  const text = '指南将TEER与示踪通量纳入检测流程。\n\n文献通过阻抗谱分析温度影响，为记录测量条件提供方法基础。'
  assert.equal(comparisonReaderText(text), text)
  const reader = { claim_id: link.claim_id, evidence_id: link.evidence_id,
    similarity: [{ text, material_quotes: ['材料原文'], literature_quotes: ['文献原文'] }],
    difference: [], conditions: [] }
  assert.equal(comparisonResearchAnalysis(link, reader).similarity, text)
  assert.equal(comparisonResearchAnalysis(link, { ...reader, similarity: [reader.similarity[0], reader.similarity[0]] }).similarity, text)
  const cleaned = comparisonReaderText('字段覆盖0/7。\n\n' + text + '\n\n关键词交集0项。')
  assert.equal(cleaned, text)
})
