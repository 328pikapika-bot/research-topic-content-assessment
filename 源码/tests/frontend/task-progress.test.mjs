import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/taskProgress.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const flush = () => new Promise(resolve => setImmediate(resolve))
function setup() {
  const intervals = new Map()
  let nextTimer = 1, now = 0
  const context = { exports: {}, Date: { now: () => now },
    setInterval: (callback, delay) => { assert.equal(delay, 5000); const id = nextTimer++; intervals.set(id, callback); return id },
    clearInterval: id => intervals.delete(id),
  }
  vm.runInNewContext(compiled, context)
  const tick = async () => { now += 5000; for (const callback of [...intervals.values()]) await callback() }
  const advance = milliseconds => { now += milliseconds }
  const check = async () => { for (const callback of [...intervals.values()]) await callback() }
  return { follow: context.exports.followTaskProgress, intervals, tick, advance, check }
}
const base = { isLive: detail => detail.status === 'running', onSyncing: () => {}, version: detail => detail.version }
const running = (stage, version) => ({ status: 'running', stage, version })

test('new task reads its latest material immediately, even before the first stream event', async () => {
  const { follow, intervals } = setup(), seen = []
  let close = 0, stream
  const stop = follow({ ...base, subscribe: onChange => { stream = onChange; return () => close++ },
    fetch: async () => running('retrieval', 2), onChange: detail => seen.push(detail.stage) })
  await flush()
  assert.deepEqual(seen, ['retrieval'])
  stream(running('material', 1))
  assert.deepEqual(seen, ['retrieval'])
  stop()
  assert.equal(intervals.size, 0)
  assert.equal(close, 1)
  stream(running('comparison', 3))
  assert.deepEqual(seen, ['retrieval'])
})

test('a silently connected stream synchronizes after five seconds and preserves real stage durations', async () => {
  const { follow, tick } = setup(), seen = []
  let count = 0
  const stop = follow({ ...base, subscribe: () => () => {},
    fetch: async () => { count++; return count === 1 ? running('material', 1) : { ...running('retrieval', 2), materialDuration: .01 } },
    onChange: detail => seen.push(detail) })
  await flush(); await tick()
  assert.equal(count, 2)
  assert.equal(seen.at(-1).stage, 'retrieval')
  assert.equal(seen.at(-1).materialDuration, .01)
  stop()
})

test('healthy events avoid polling; recovery beats an outstanding poll and older events cannot roll back state', async () => {
  const { follow, tick } = setup(), seen = []
  let stream, error, resolveFetch, count = 0
  const stop = follow({ ...base, subscribe: (onChange, onError) => { stream = onChange; error = onError; return () => {} },
    fetch: () => { count++; return new Promise(resolve => { resolveFetch = resolve }) },
    onChange: detail => seen.push(detail.stage) })
  stream(running('retrieval', 2))
  resolveFetch(running('material', 1)); await flush()
  const pending = tick()
  stream(running('comparison', 3))
  resolveFetch(running('old-retrieval', 2)); await pending
  assert.deepEqual(seen, ['retrieval', 'comparison'])
  // Immediately delivered healthy events do not cause another fetch.
  stream(running('assessment', 4))
  assert.equal(count, 2)
  stream(running('material', 1))
  assert.deepEqual(seen, ['retrieval', 'comparison', 'assessment'])
  error()
  const fallback = tick()
  resolveFetch({ status: 'completed', stage: 'report', version: 5 }); await fallback
  assert.equal(seen.at(-1), 'report')
  stop()
})

test('terminal read cleans up watchdog and closes a late subscription; disposed fetches cannot apply', async () => {
  const { follow, tick, intervals } = setup(), seen = []
  let resolveSubscription, closed = 0, count = 0
  follow({ ...base, subscribe: () => new Promise(resolve => { resolveSubscription = resolve }),
    fetch: async () => { count++; return { status: 'completed', stage: 'report', version: 5 } },
    onChange: detail => seen.push(detail.status) })
  await flush()
  assert.deepEqual(seen, ['completed']); assert.equal(intervals.size, 0)
  resolveSubscription(() => closed++); await flush(); await tick()
  assert.equal(closed, 1); assert.equal(count, 1)
  let resolveFetch
  const stop = follow({ ...base, subscribe: () => () => {}, fetch: () => new Promise(resolve => { resolveFetch = resolve }), onChange: detail => seen.push(detail.stage) })
  stop(); resolveFetch(running('old-task', 99)); await flush()
  assert.deepEqual(seen, ['completed'])
})

test('regular stream events suppress watchdog fetches, silence resumes them', async () => {
  const { follow, advance, check } = setup()
  let stream, count = 0
  const stop = follow({ ...base, subscribe: change => { stream = change; return () => {} },
    fetch: async () => { count++; return running('material', count) }, onChange: () => {} })
  await flush()
  for (let i = 0; i < 4; i++) {
    advance(4000); stream(running('retrieval', i + 2)); advance(1000); await check()
  }
  assert.equal(count, 1)
  advance(5000); await check()
  assert.equal(count, 2)
  stop()
})
