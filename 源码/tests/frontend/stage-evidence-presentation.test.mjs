import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/stageEvidencePresentation.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const context = { exports: {} }
vm.runInNewContext(compiled, context)
const { retrievalInfo, decisionSources, frozenPointResult } = context.exports

test('retrieval disclosure only includes saved, useful search data', () => {
  assert.equal(retrievalInfo({ query_group: '电池界面', retrieval_query: '固态电解质 界面', doi: '10.1/test' }).length, 3)
  assert.equal(retrievalInfo({ query_group: '电池界面' })[0].value, '电池界面')
  assert.equal(retrievalInfo({ query_group: '综合检索文献', retrieval_query: '综合检索' }).length, 0)
})

test('decision sources preserve saved relations, deduplicate records and skip missing titles', () => {
  const decision = { claim_id: 'C-001', evidence_ids: ['E-1', 'E-1', 'E-2', 'E-3'] }
  const records = [{ evidence_id: 'E-1', title: '界面研究' }, { evidence_id: 'E-2', title: '实验结果' }]
  const links = [{ claim_id: 'C-001', evidence_id: 'E-1', relation: 'partially_supports', difference: '温度条件不同', incomparability_reasons: ['缺少方法字段'] }]
  const result = decisionSources(decision, records, links)
  assert.equal(result.length, 2)
  assert.equal(result[0].relation, 'partially_supports')
  assert.equal(result[0].difference, '温度条件不同')
  assert.equal(result[1].difference, '')
  assert.equal(decisionSources({ claim_id: 'C-002', evidence_ids: [] }, records, links).length, 0)
  assert.equal(decisionSources({ claim_id: 'C-002', evidence_ids: ['E-2'] }, records, links).length, 0)
})

test('task detail uses conditional disclosures without internal evidence ids', () => {
  const page = fs.readFileSync(new URL('../../frontend/web/src/pages/TaskDetailPage.vue', import.meta.url), 'utf8')
  assert.match(page, /LiteratureCard v-for="item in group.items.slice\(0, 2\)"/)
  assert.match(page, /section v-if="group.sources.length" class="assessment-comparison-section"/)
  assert.match(page, /v-for="row in comparisonRows\(group.key\)" :key="row.source.evidence_id"/)
  assert.match(page, /LiteratureReferences :items="\[row.source\]" show-abstract/)
  assert.doesNotMatch(page, /内部编号：\{\{ item\.evidence_id \}\}/)
  assert.doesNotMatch(page, /内部编号：\{\{ decision\.claim_id \}\}/)
  assert.doesNotMatch(page, /本条文献尚未形成逐项同口径比对/)
})

test('published point result uses its own frozen citation map and never changes legacy content', () => {
  const snapshot = { sections: [{ section_key: 'comparison_novelty', blocks: [
    {kind:'paragraph', claim_id:'C1', text:'已有词项方法。', evidence_ids:['E1','E1']},
    {kind:'paragraph', claim_id:'C2', text:'另一个对象。', evidence_ids:['E2']},
  ]}], citation_numbers:{E1:7,E2:2}, audit_data:{reader_point_summaries:{C1:[{text:'建议固定分词策略。',evidence_ids:['E1']}]}} }
  const before = JSON.stringify(snapshot)
  assert.equal(frozenPointResult(snapshot,'C1'), '已有词项方法。 [7]')
  assert.equal(frozenPointResult(snapshot,'C1',true), '建议固定分词策略。 [7]')
  assert.equal(frozenPointResult(snapshot,'unknown'), '')
  assert.equal(JSON.stringify(snapshot),before)
  assert.equal(frozenPointResult({sections:snapshot.sections,citation_numbers:snapshot.citation_numbers},'C1',true),'已有词项方法。 [7]')
  assert.equal(frozenPointResult(undefined,'C1'),'')
})
