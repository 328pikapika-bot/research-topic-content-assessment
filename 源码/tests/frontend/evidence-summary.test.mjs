import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

function moduleAt(name, imports = {}) {
  const source = fs.readFileSync(new URL(`../../frontend/web/src/services/${name}.ts`, import.meta.url), 'utf8')
  const context = { exports: {}, URL, require: key => imports[key] }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
  return context.exports
}
const links = moduleAt('evidenceLinks')
const { evidenceSummaries } = moduleAt('evidenceSummary', { './evidenceLinks': links, './researchPresentation': moduleAt('researchPresentation') })

test('counterevidence is visible among the main sources and repeated citations merge', () => {
  const records = Array.from({ length: 7 }, (_, i) => ({ evidence_id: `E${i}`, title: `Paper ${i}`, abstract: 'Abstract', fulltext_available: true, anchor_verified: true }))
  const relation = records.map((r, i) => ({ evidence_id: r.evidence_id, relation: i === 6 ? 'contradicts' : 'context' }))
  relation.push(relation[0])
  const result = evidenceSummaries(relation, records, [], 'C1')
  assert.equal(result[0].relation, '存在反证')
  assert.equal(result.length, 5)
  assert.equal(result[0].scope, '摘要依据')
})

test('a paragraph belonging to another claim cannot be presented as this claims fulltext', () => {
  const r = { evidence_id: 'E1', title: 'Paper', abstract: 'Abstract', fulltext_available: true, anchor_verified: true, url: 'javascript:alert(1)' }
  const c = { evidence_id: 'E1', chunk_id: 'CH1', claim_ids: ['C2'], excerpt: 'Other claim paragraph', reopenable: true }
  const result = evidenceSummaries([{ evidence_id: 'E1', relation: 'context' }], [r], [c], 'C1')
  assert.equal(result[0].scope, '摘要依据')
  assert.equal(result[0].excerpt, 'Abstract')
  assert.equal(result[0].url, '')
})
