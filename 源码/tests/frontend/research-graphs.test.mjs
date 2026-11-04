import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

function read(name, requireModule = () => ({})) {
  const source = fs.readFileSync(new URL(`../../frontend/web/src/services/${name}.ts`, import.meta.url), 'utf8')
  const context = { exports: {}, require: requireModule }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
  return context.exports
}
const research = read('researchPresentation')
const { safeThemeHierarchy, graphPath, participants, assessmentGraphData } = read('researchGraphs', () => research)

test('theme ancestry never invents a parent for missing or cyclic records', () => {
  const tree = { nodes: [
    { theme_id: 'A', parent_id: 'root' }, { theme_id: 'B', parent_id: 'A' },
    { theme_id: 'C', parent_id: 'missing' }, { theme_id: 'D', parent_id: 'E' }, { theme_id: 'E', parent_id: 'D' },
  ] }
  const rows = safeThemeHierarchy(tree)
  assert.equal(rows.find(x => x.theme_id === 'B').depth, 2)
  for (const id of ['C', 'D', 'E']) {
    assert.equal(rows.find(x => x.theme_id === id).uncertain, true)
    assert.equal(rows.find(x => x.theme_id === id).parent, '')
  }
})

test('only aligned graph nodes have straight connections', () => {
  const a = { x: 0, y: 10, width: 100, height: 40 }
  assert.match(graphPath(a, { x: 200, y: 10, width: 100, height: 40 }), / L /)
  assert.match(graphPath(a, { x: 200, y: 70, width: 100, height: 40 }), / C /)
})

test('participants and frozen assessment graph retain only recorded relationships', () => {
  assert.equal(participants({ core: '计算机科学', expansion: '信息科学' }).length, 2)
  assert.equal(participants({ participating_disciplines: [
    { discipline: '计算机科学', role: 'core' }, { discipline: '统计学', role: 'expansion' },
    { discipline: '统计学', role: 'expansion' },
  ] }).length, 2)
  const chart = { points: [{ id: 'C1', label: '要点 1' }], evidence: [], links: [] }
  const result = assessmentGraphData({ report_snapshot: { chart_manifest: [{ chart_key: 'claim_evidence_network', included: true, chart_data: chart }] },
    claims: [{ claim_id: 'C2' }], evidence_records: [{ evidence_id: 'E2' }] })
  assert.equal(result, chart)
})
