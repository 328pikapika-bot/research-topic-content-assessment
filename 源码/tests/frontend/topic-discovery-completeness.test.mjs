import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/topicDiscovery.ts', import.meta.url), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { discoveryReadingText, methodEvolutionRows, discoveryRows, opportunityReading } = context.exports

test('reading text removes internal references and empty separators while preserving scientific content', () => {
  assert.equal(discoveryReadingText('本次可读检索样本中的观察（不代表领域整体）：分析algC突变（E-123, E-456）。'), '分析algC突变。')
  assert.equal(discoveryReadingText('验证抗性变化（E-123, FW-002），保留比较条件（24–48小时）。'), '验证抗性变化，保留比较条件（24–48小时）。')
  assert.equal(discoveryReadingText('检测IL-6和MIC（微量肉汤稀释法）。'), '检测IL-6和MIC（微量肉汤稀释法）。')
})

test('legacy periods display without assigning recent findings to future', () => {
  const rows = methodEvolutionRows({ stage_artifacts: { landscape_trend: { analysis: { method_evolution: [
    { finding: '早期采用棋盘法', evidence_ids: ['E1'] },
    { finding: '近期开展动物模型验证', evidence_ids: ['E2'] },
    { finding: '未来研究跨菌株外推', evidence_ids: ['E3'] },
  ] } } } })
  assert.deepEqual(Array.from(rows, row => row.period), ['past', 'present', 'future'])
  assert.equal(rows[1].evidence_ids[0], 'E2')
})

test('frozen method content wins over mutable stage results without mutation', () => {
  const frozen = [{ period: 'past', finding: '已发布的方法分析', evidence_ids: ['E1'] }]
  const state = { report_plan: { chart_specs: [{ key: 'discipline_network', data: { method_evolution: frozen } }] },
    stage_artifacts: { landscape_trend: { analysis: { method_evolution: [{ period: 'future', finding: '后来的不同结论' }] } } } }
  const before = JSON.stringify(state)
  assert.equal(methodEvolutionRows(state)[0].finding, '已发布的方法分析')
  assert.equal(JSON.stringify(state), before)
})

test('research direction graph and label use only disciplines backed by its evidence', () => {
  const state = { report_plan: { chart_specs: [{ key: 'discipline_network', data: { rows: [{
    crosslink_id: 'X1', opportunity: '城市热暴露的跨尺度分析', evidence_ids: ['E1', 'E2'],
    participating_disciplines: [
      { discipline: '地理学', role: 'core', evidence_ids: ['E1'] },
      { discipline: '公共卫生', role: 'expansion', evidence_ids: ['E2'] },
      { discipline: '经济学', role: 'expansion', evidence_ids: ['E3'] },
    ],
  }] } }] } }
  const row = discoveryRows(state)[0]
  assert.equal(row.participating_disciplines.length, 2)
  assert.equal(row.core, '地理学')
  assert.equal(row.expansion, '公共卫生')
  const reading = opportunityReading({ ...row, support_evidence_ids: row.evidence_ids }, [])
  assert.equal(reading.kind, '交叉研究方向')
  assert.equal(opportunityReading({ research_opportunity: '', relation_basis: '遥感温度可与街区暴露记录进行联合分析' }, []).title, '遥感温度可与街区暴露记录进行联合分析')
})
