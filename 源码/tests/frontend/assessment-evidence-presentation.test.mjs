import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/assessmentEvidencePresentation.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const context = { exports: {} }
vm.runInNewContext(compiled, context)
const { evidenceSourceLabel, buildClaimEvidencePresentation: present } = context.exports
const claim = { claim_id: 'C-001', text: '优化器官芯片制造工艺并降低单批实验成本20%', claim_type: 'technical', assertion_type: 'target' }
const record = { evidence_id: 'E-001', source_type: 'literature', evidence_level: 'B', abstract: 'Manufacturing methods and cost efficiency of microfluidic devices.' }
const link = { claim_id: 'C-001', evidence_id: 'E-001', relation: 'partially_supports', confidence: .4 }

test('source categories use Chinese labels rather than treating literature as language', () => {
  assert.equal(evidenceSourceLabel('literature'), '科学文献')
  assert.equal(evidenceSourceLabel('technical-report'), '技术报告')
  assert.equal(evidenceSourceLabel('user_material'), '用户输入材料')
  assert.equal(evidenceSourceLabel('internal_unknown_code'), '其他来源')
  assert.equal(evidenceSourceLabel('公开网页'), '公开网页')
})

test('directional evidence and untested project targets have independent states', () => {
  const decision = { risk_notes: ['文献不能支撑项目成本降低20%已经达标'], evidence_ids: ['E-001'] }
  const result = present(claim, [link], [record], decision)
  assert.equal(result.status, 'partial')
  assert.equal(result.tone, 'info')
  assert.equal(result.verification, '项目目标待实测验证')
  assert.equal(result.evidenceIds.join(','), 'E-001')
  assert.match(result.summary, /研究方向或技术路线/)
})

test('counterevidence remains visible even when supporting literature exists', () => {
  const result = present(claim, [link, { ...link, evidence_id: 'E-002', relation: 'contradicts' }], [record, { ...record, evidence_id: 'E-002' }])
  assert.equal(result.status, 'conflicting')
  assert.equal(result.tone, 'danger')
  assert.equal(result.evidenceIds.length, 2)
})

test('missing sources and self-reported materials cannot produce positive support', () => {
  const result = present(claim, [link], [{ ...record, source_type: 'user_material' }], { evidence_ids: ['E-001', 'missing'], risk_notes: [] })
  assert.equal(result.status, 'unmatched')
  assert.equal(result.tone, 'warning')
  assert.equal(result.evidenceIds.length, 0)
})

test('actual subject mismatch and isolated technical terms are not promoted to support', () => {
  const mismatch = present(claim, [link], [record], { risk_notes: ['证据与主张主题不匹配，需要对应的方法依据'], evidence_ids: ['E-001'] })
  assert.equal(mismatch.status, 'related')
  const term = present({ ...claim, text: '流程挖掘', claim_type: 'innovation' }, [link], [record])
  assert.equal(term.label, '技术要素待具体化')
  assert.equal(term.status, 'related')
})

test('abstract and metadata records cannot be shown as direct fulltext proof', () => {
  assert.equal(present(claim, [{ ...link, relation: 'supports' }], [record]).status, 'partial')
  assert.equal(present(claim, [link], [{ ...record, evidence_level: 'C', abstract: '', excerpt: '' }]).status, 'related')
  const direct = present(claim, [{ ...link, relation: 'supports' }], [{ ...record, evidence_level: 'A', fulltext_available: true, anchor_verified: true }])
  assert.equal(direct.status, 'direct')
  assert.equal(direct.tone, 'success')
})

test('legacy decision references retain traceability without inventing a support relation', () => {
  const result = present(claim, [], [record], { evidence_ids: ['E-001', 'E-001', 'missing'], risk_notes: [] })
  assert.equal(result.status, 'related')
  assert.equal(result.evidenceIds.join(','), 'E-001')
})
