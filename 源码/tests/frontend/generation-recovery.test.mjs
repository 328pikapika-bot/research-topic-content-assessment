import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/generationRecovery.ts', import.meta.url), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
const { recoveryLabel, recoveryNotice } = context.exports

test('paused source checks explain the stage and do not claim a retry is scheduled', () => {
  const record = { enabled: true, phase: 'waiting_system', category: 'content', stage: 'crosslink_gap', next_retry_at: null }
  assert.equal(recoveryLabel(record, 'queued'), '内容校核待修复')
  const notice = recoveryNotice(record, 'queued')
  assert.match(notice.description, /研究机会分析已暂停/)
  assert.match(notice.description, /方法与引用原文的对应关系/)
  assert.equal(notice.reference, '')
  assert.match(recoveryNotice({ ...record, stage: 'report_publish' }).description, /报告正文或文献引用/)
})

test('finished and stopped tasks cannot display stale recovery state', () => {
  const record = { enabled: true, phase: 'waiting_system', category: 'content' }
  for (const status of ['completed', 'cancelled', 'superseded', 'awaiting_correction', 'failed', 'time_out', 'awaiting_review']) {
    assert.equal(recoveryLabel(record, status), '')
    assert.equal(recoveryNotice(record, status), null)
  }
  assert.equal(recoveryLabel({ enabled: true, phase: 'running', category: 'content' }, 'running'), '正在自动修复')
})

test('service recovery retry times use Beijing time independently of the computer time zone', () => {
  const record = { enabled: true, phase: 'scheduled', category: 'transient', next_retry_at: '2026-11-12T09:53:00Z' }
  assert.equal(recoveryLabel(record), '等待服务恢复')
  assert.match(recoveryNotice(record).reference, /17:53:00.*北京时间/)
})
