import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

async function loadService(name) {
  const source = await readFile(new URL(`../../frontend/web/src/services/${name}.ts`, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
}
const { NINE_FIELDS, emptyNineDraft, serializeNineDraft, mergeNineDraft, missingNineFields, topicYearRange, PUBLIC_CASES, TOPIC_CASES, ASSESSMENT_CASES } = await loadService('homepageDraft')
const { ApiError, readApiError } = await loadService('apiErrors')

test('time ranges use complete inclusive years and unlimited stays empty', () => {
  assert.deepEqual(topicYearRange('5', 2026), [2022, 2026])
  assert.deepEqual(topicYearRange('3', 2026), [2024, 2026])
  assert.deepEqual(topicYearRange('10', 2026), [2017, 2026])
  assert.deepEqual(topicYearRange('all', 2026), [])
})

test('unresolved type does not require eight fields; paper checks only its own fields', () => {
  const draft = emptyNineDraft()
  assert.equal(missingNineFields(draft).length, 0)
  assert.deepEqual(missingNineFields(draft, 'paper').map(field => field.key), ['research_direction', 'research_content', 'key_technologies'])
  for (const key of ['research_direction', 'research_content', 'key_technologies']) draft[key] = ' valid '
  assert.equal(missingNineFields(draft, 'paper').length, 0)
  assert.deepEqual(serializeNineDraft(draft).expected_outcomes, [])
  draft.key_technologies = 'first; second； third\n\n'
  assert.deepEqual(serializeNineDraft(draft).key_technologies, ['first', 'second', 'third'])
  draft.key_technologies = '; \n；'
  assert.equal(missingNineFields(draft, 'paper')[0].key, 'key_technologies')
  draft.technical_route = '制备后测试'
  assert.equal(missingNineFields(draft, 'project').length, 0)
})

test('multi-file changes refresh untouched fields and preserve manually cleared fields', () => {
  const draft = emptyNineDraft()
  mergeNineDraft(draft, { research_goal: 'extracted', timeline: 'old', expected_outcomes: ['outcome'] }, new Set())
  draft.expected_outcomes = ''
  const touched = new Set(['expected_outcomes'])
  mergeNineDraft(draft, { research_goal: 'new', timeline: 'new', expected_outcomes: ['replacement'] }, touched)
  assert.equal(draft.research_goal, 'new')
  assert.equal(draft.expected_outcomes, '')
  mergeNineDraft(draft, {}, touched)
  assert.equal(draft.timeline, '')
  assert.equal(draft.expected_outcomes, '')
})

test('assessment cases contain complete labelled source material instead of injected fields', () => {
  assert.equal(PUBLIC_CASES.length, 9)
  assert.equal(TOPIC_CASES.length, 3)
  assert.equal(ASSESSMENT_CASES.length, 6)
  assert.ok(TOPIC_CASES.every(sample => sample.agent === 'topic' && sample.input.length > 5))
  const byType = { 论文: ['研究方向', '研究内容', '关键方法'], 项目: ['研究方向', '研究内容', '关键技术或技术路线'], 指南: ['研究目标', '研究内容', '考核指标', '应用场景'] }
  for (const sample of ASSESSMENT_CASES) {
    assert.equal('fields' in sample, false)
    assert.equal(sample.scenarioId, sample.id)
    assert.ok(sample.input.length > 120)
    const type = sample.category.split(' · ')[0]
    assert.match(sample.input, new RegExp(`^我要评估的是：${type}$`, 'm'))
    for (const field of byType[type]) assert.match(sample.input, new RegExp(`^${field}：\\S+`, 'm'))
    for (const field of NINE_FIELDS.map(item => item.label).filter(label => !byType[type].includes(label))) assert.doesNotMatch(sample.input, new RegExp(`^${field}：`, 'm'))
  }
  assert.equal(ASSESSMENT_CASES.filter(sample => sample.category.startsWith('论文 · ')).length, 2)
  assert.equal(ASSESSMENT_CASES.filter(sample => sample.category.startsWith('项目 · ')).length, 2)
  assert.equal(ASSESSMENT_CASES.filter(sample => sample.category.startsWith('指南 · ')).length, 2)
  assert.equal(new Set(ASSESSMENT_CASES.map(sample => sample.scenarioId)).size, 6)
})

test('structured 429 displays running and queued separately and retains agent', async () => {
  const error = await readApiError(new Response(JSON.stringify({ detail: { code: 'rate_limit_exceeded', message: '额度不足', agent: 'assessment', running: 3, queued: 2, limit: 5 } }), { status: 429 }))
  assert.ok(error instanceof ApiError)
  assert.equal(error.status, 429)
  assert.equal(error.detail.agent, 'assessment')
  assert.match(error.message, /3/)
  assert.match(error.message, /2/)
})
