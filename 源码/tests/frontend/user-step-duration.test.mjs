import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

function moduleAt(path) {
  const source = fs.readFileSync(new URL(path, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText
  const context = {exports: {}}
  vm.runInNewContext(compiled, context)
  return context.exports
}
const {aggregateSteps, stepDurationLabel, stageRunPresentation, ASSESSMENT_STEPS} = moduleAt('../../frontend/web/src/services/userSteps.ts')
const {pointLiteratureOrder} = moduleAt('../../frontend/web/src/services/researchPresentation.ts')
const definitions = [{key: 'step', stages: ['a', 'b']}]

test('running composite duration grows each second and legacy Beijing time is interpreted correctly', () => {
  const traces = [{stage_key: 'a', status: 'completed', duration_seconds: 7}, {stage_key: 'b', status: 'running', started_at: '2026-10-15T12:00:00'}]
  const now = Date.parse('2026-10-15T04:00:10Z')
  assert.equal(aggregateSteps(definitions, traces, now)[0].duration_seconds, 17)
  assert.equal(stepDurationLabel(aggregateSteps(definitions, traces, now + 1000)[0]), ' · 已用时18秒')
})
test('paused and completed durations remain fixed, including a saved zero', () => {
  const traces = [{stage_key:'a',status:'completed',duration_seconds:0}, {stage_key:'b',status:'blocked',duration_seconds:4,started_at:'2026-10-15T04:00:00Z'}]
  assert.equal(aggregateSteps(definitions, traces, Date.now(), false)[0].duration_seconds, 4)
  assert.equal(aggregateSteps(definitions, traces, Date.now() + 999999, false)[0].duration_seconds, 4)
  assert.equal(aggregateSteps([{key:'only',stages:['a']}], traces)[0].duration_seconds, 0)
})
test('historical missing time stays unknown and recorded end time is used', () => {
  assert.equal(aggregateSteps(definitions, [{stage_key:'a',status:'completed'}])[0].duration_seconds, null)
  assert.equal(aggregateSteps(definitions, [{stage_key:'a',status:'completed',started_at:'2026-10-15T04:00:00Z',completed_at:'2026-10-15T04:00:06Z'}])[0].duration_seconds, 6)
})

test('missing start time never invents an elapsed second; quick completion reads under one second', () => {
  const running = aggregateSteps([{key:'only',stages:['a']}], [{stage_key:'a',status:'running'}])[0]
  assert.equal(stepDurationLabel(running), ' · 正在同步计时')
  const completed = aggregateSteps([{key:'only',stages:['a']}], [{stage_key:'a',status:'completed',duration_seconds:0}])[0]
  assert.equal(stepDurationLabel(completed), ' · 用时不足1秒')
  assert.equal(stepDurationLabel({status:'pending',duration_seconds:null}), '')
})

test('paused task stops user-step clock even when a child trace is still running', () => {
  const traces = [{stage_key:'a',status:'completed',duration_seconds:3}, {stage_key:'b',status:'running',started_at:'2026-10-15T04:00:00Z',updated_at:'2026-10-15T04:00:08Z'}]
  const paused = aggregateSteps(definitions, traces, Date.parse('2026-10-15T04:20:00Z'), false, 'awaiting_confirmation')[0]
  assert.equal(paused.status, 'blocked')
  assert.equal(paused.duration_seconds, 11)
  assert.equal(stepDurationLabel(paused), ' · 用时11秒')
})

test('material intake stays active while point extraction runs, and the notice uses the active stage', () => {
  const traces = [
    {stage_key:'document_intake',status:'completed',duration_seconds:0},
    {stage_key:'claim_extraction',status:'running',started_at:'2026-10-15T04:00:00Z'},
  ]
  const steps = aggregateSteps(ASSESSMENT_STEPS, traces, Date.parse('2026-10-15T04:00:12Z'))
  assert.equal(steps[0].status, 'running')
  assert.equal(steps[0].duration_seconds, 12)
  const notice = stageRunPresentation(steps, traces, 'claim_extraction', 'running')
  assert.match(notice.title, /材料解析.*12秒/)
  assert.match(notice.description, /材料已读取.*提取研究要点/)
  assert.match(notice.reference, /参考耗时约10–90秒/)
  assert.equal(stageRunPresentation(steps, traces, 'claim_extraction', 'awaiting_clarification'), null)
})

test('long running stage drops estimate but continues to show actual elapsed time', () => {
  const traces = [{stage_key:'retrieval',status:'running',started_at:'2026-10-15T04:00:00Z'}]
  const steps = aggregateSteps(ASSESSMENT_STEPS, traces, Date.parse('2026-10-15T04:06:00Z'))
  const notice = stageRunPresentation(steps, traces, 'retrieval', 'running')
  assert.match(notice.title, /仍在处理中.*360秒/)
  assert.doesNotMatch(notice.reference, /参考耗时/)
})
test('Chinese topical titles rank first and English terms respect word boundaries', () => {
  const records = [{title:'无关文献'}, {title:'树冠结构与城市热环境'}]
  assert.equal(pointLiteratureOrder(records,{text:'树冠结构'})[0].title, records[1].title)
  assert.equal(pointLiteratureOrder([{title:'Chipset design'},{title:'Chip perfusion'}],{text:'chip'})[0].title, 'Chip perfusion')
})
