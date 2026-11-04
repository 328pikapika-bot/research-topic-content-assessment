import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

function service(name) {
  const context = { exports: {} }
  const source = fs.readFileSync(new URL(`../../frontend/web/src/services/${name}.ts`, import.meta.url), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
  return context.exports
}
const { aggregateSteps, TOPIC_STEPS, ASSESSMENT_STEPS } = service('userSteps')
const { topicResultState, assessmentResultState, topicTrendSeries, assessmentTrendSeries } = service('resultSnapshot')
const { literatureEvidenceStatus, chunkLocation } = service('literaturePresentation')
const plain = value => JSON.parse(JSON.stringify(value))

test('live literature ranks the point method before shared background without losing source records', () => {
  const { pointLiteratureOrder, researchPointName } = service('researchPresentation')
  const background = { title: 'Probabilistic information retrieval', abstract: 'BM25 ranking' }
  const method = { title: 'SPECTER: Citation-informed Transformers', abstract: 'document representations' }
  const records = [background, method]
  const claim = { text: '项目拟开展引文表征与学科泛化，以SPECTER考察适用条件。', claim_fields: { method: 'SPECTER citation-informed embeddings' } }
  assert.equal(researchPointName(claim), '引文表征与学科泛化')
  assert.equal(pointLiteratureOrder(records, claim)[0], method)
  assert.deepEqual(records, [background, method])
  assert.equal(pointLiteratureOrder(records, claim).length, 2)
})

test('user stages aggregate completion, failure, confirmation and missing historical stages', () => {
  assert.equal(TOPIC_STEPS.length, 6)
  assert.equal(ASSESSMENT_STEPS.length, 5)
  for (const steps of [TOPIC_STEPS, ASSESSMENT_STEPS]) {
    assert.equal(aggregateSteps(steps, []).every(step => step.status === 'pending'), true)
    const traces = steps.flatMap(step => step.stages.map(stage_key => ({ stage_key, status: 'completed' })))
    assert.equal(aggregateSteps(steps, traces).every(step => step.status === 'completed'), true)
    const target = steps.findIndex(step => step.stages.includes(traces[1].stage_key))
    traces[1].status = 'awaiting_confirmation'
    assert.equal(aggregateSteps(steps, traces)[target].status, 'blocked')
    traces[1].status = 'failed'
    assert.equal(aggregateSteps(steps, traces)[target].status, 'failed')
    traces[1].status = 'pending'
    assert.equal(aggregateSteps(steps, traces)[target].status, steps[target].stages.length > 1 ? 'running' : 'pending')
    const report = aggregateSteps(steps, traces).at(-1)
    assert.equal(report.stages.includes('quality_gate'), true)
  }
})

test('accepted stage titles and grouped degraded progress are stable', () => {
  assert.deepEqual(plain(TOPIC_STEPS).map(s => s.title), ['需求解析', '主题探索', '文献检索', '研究机会分析', '选题推荐', '选题报告'])
  assert.deepEqual(plain(ASSESSMENT_STEPS).map(s => s.title), ['材料解析', '文献检索', '相关研究分析', '创新性评估', '评估报告'])
  for (const steps of [TOPIC_STEPS, ASSESSMENT_STEPS]) {
    for (const step of steps) {
      assert.ok(step.description && step.artifact && step.completion)
      assert.ok(step.stages.length)
    }
    const report = steps.at(-1)
    const traces = [{ stage_key: report.stages[0], status: 'degraded' }, { stage_key: report.stages[1], status: 'pending' }]
    const before = JSON.stringify(traces)
    assert.equal(aggregateSteps(steps, traces).at(-1).status, 'running')
    assert.equal(aggregateSteps(steps, traces).at(-1).progress_percent, 50)
    traces[1].status = 'completed'
    assert.equal(aggregateSteps(steps, traces).at(-1).status, 'degraded')
    assert.equal(aggregateSteps(steps, traces).at(-1).progress_percent, 100)
    traces[1].status = 'pending'
    assert.equal(JSON.stringify(traces), before)
    traces[0].status = 'completed'
    traces[0].model_status = 'degraded'
    traces[1].status = 'completed'
    assert.equal(aggregateSteps(steps, traces).at(-1).status, 'degraded')
  }
})

test('topic candidates, evidence, citations and trend retain published data while execution remains live', () => {
  const plan = { report_version: 'published', template_version: 'TopicReportV5', citation_map: { E1: 1 }, chart_specs: [{ key: 'trend_comparison', data: { years: [2024], series: [{ label: '数学方法', values: [2] }, { label: '信息检索', values: [2] }] } }] }
  const state = { candidates: [{ title: 'later draft' }], evidence_pool: [], current_stage: 'running', stage_trace: [], report_snapshot: { report_plan_snapshot: plan, result_data_snapshot: { candidates: [{ title: 'published candidate' }], evidence_pool: [{ evidence_id: 'E1' }] } } }
  const before = JSON.stringify(state)
  const result = topicResultState(state)
  assert.equal(result.candidates[0].title, 'published candidate')
  assert.equal(result.evidence_pool[0].evidence_id, 'E1')
  assert.equal(result.report_plan.citation_map.E1, 1)
  assert.equal(result.current_stage, 'running')
  assert.equal(result.stage_trace, state.stage_trace)
  assert.deepEqual(plain(topicTrendSeries(result)).map(series => series.points[0].count), [2, 2])
  assert.equal(JSON.stringify(state), before)
  assert.equal(topicResultState({ candidates: [] }).report_plan, undefined)
})

test('assessment point, quality and charts use snapshot and coverage never becomes annual data', () => {
  const state = { current_stage: 'running', quality_gate: { overall_status: 'failed' }, claims: [], report_snapshot: { audit_data: { claims: [{ claim_id: 'C1', text: 'published point' }] }, render_context: { quality_gate: { overall_status: 'passed' } }, sections: [{ section_key: 'executive', content: 'published conclusion' }], chart_manifest: [{ chart_key: 'years', included: true, render_mode: 'primary', chart_data: { '信息检索': [[2024, 3]], '数学方法': [[2024, 3]] } }] } }
  const result = assessmentResultState(state)
  assert.equal(result.claims[0].text, 'published point')
  assert.equal(result.quality_gate.overall_status, 'passed')
  assert.equal(result.current_stage, 'running')
  assert.equal(result.report_plan.sections[0].content, 'published conclusion')
  assert.equal(assessmentTrendSeries(result).length, 2)
  state.report_snapshot.chart_manifest[0].render_mode = 'coverage'
  assert.equal(assessmentTrendSeries(state).length, 0)
  assert.equal(assessmentTrendSeries({}).length, 0)
  state.report_metrics = { actual_page_count: 11, page_count_verified: true, page_count_source: 'pdf:frozen' }
  const verified = assessmentResultState(state)
  assert.equal(verified.report_metrics.actual_page_count, 11)
  assert.equal(verified.report_plan.actual_page_count, 11)
  assert.equal(verified.report_plan.sections[0].content, 'published conclusion')
})

test('indexed or linked literature only becomes fulltext after readable located content is saved', () => {
  const item = { evidence_id: 'E1', has_fulltext: true, source_url: 'https://example.org' }
  assert.equal(literatureEvidenceStatus(item, []), '题录')
  assert.equal(literatureEvidenceStatus({ ...item, abstract: 'abstract' }, []), '摘要')
  assert.equal(literatureEvidenceStatus(item, [{ evidence_id: 'E1', text: 'body' }]), '题录')
  const chunk = { evidence_id: 'E1', text: 'readable body', locator: { page: 3, paragraph: 2 } }
  assert.equal(chunkLocation(chunk), '页 3 · 段落 2')
  assert.equal(literatureEvidenceStatus(item, [chunk]), '可定位全文片段')
  assert.equal(literatureEvidenceStatus(item, [{ ...chunk, reopenable: false }]), '题录')
})
