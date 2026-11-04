import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/web/src/services/topicPagePresentation.ts', import.meta.url), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
const { topicDisplayTitle, candidateDirection, orderedTopicCandidates, topicCandidateFields, topicCandidateReading, topicCandidateReaderText, topicThemeNodes, topicCitationSources, citationTextParts, uniqueTopicEvidence } = context.exports

test('saved AI title is shown in full instead of a sliced original question', () => {
  const title = '长三角高密度城市夏季热环境与人体热暴露差异研究'
  assert.equal(topicDisplayTitle({ input_profile: { title: '对象为长三角三座高密度城市' }, stage_artifacts: { input_modeling: { analysis: { concise_title: title } } } }), title)
  assert.equal(topicDisplayTitle({}, '旧任务研究题目'), '旧任务研究题目')
})

test('mixed recommendations precede exploration without changing evidence or the saved order', () => {
  const items = [
    { candidate_id: 'C4', direction_type: 'exploratory', recommendation_level: 'pending_evidence' },
    { candidate_id: 'C2', direction_type: 'recommended', recommendation_level: 'pending_evidence' },
    { candidate_id: 'C5', direction_type: 'exploratory' },
    { candidate_id: 'C1', direction_type: 'recommended' },
    { candidate_id: 'C3', direction_type: 'recommended' },
  ]
  const before = JSON.stringify(items)
  assert.equal(orderedTopicCandidates(items).map(item => item.candidate_id).join(','), 'C2,C1,C3,C4,C5')
  assert.equal(JSON.stringify(items), before)
  assert.equal(orderedTopicCandidates(items)[0].recommendation_level, 'pending_evidence')
  for (const type of ['recommended', 'exploratory']) {
    assert.equal(orderedTopicCandidates(items.map(item => ({ ...item, direction_type: type }))).map(item => item.candidate_id).join(','), 'C4,C2,C5,C1,C3')
  }
})

test('historical candidate classification uses saved research design rather than evidence status alone', () => {
  assert.equal(candidateDirection({ recommendation_level: 'pending_evidence', convergence_valid: true }), 'recommended')
  assert.equal(candidateDirection({ title: '探索方向：城市气候模型', recommendation_level: 'pending_evidence' }), 'exploratory')
  assert.equal(candidateDirection({ recommendation_level: 'pending_evidence' }), 'candidate')
})

test('reordered candidate design stays attached to its original candidate', () => {
  const original = [{ candidate_id: 'C4', title: '拓展性研究' }, { candidate_id: 'C1', title: '研究设计' }]
  const sections = [{ blocks: [
    { kind: 'candidate_detail', index: 1, title: '拓展性研究', fields: [{ label: '研究设计', value: '拓展方案' }] },
    { kind: 'candidate_detail', index: 2, title: '2. 研究设计', fields: [{ label: '研究设计', value: '首选方案' }] },
  ] }]
  assert.equal(topicCandidateFields(original[1], original, sections)[0].value, '首选方案')
  assert.equal(topicCandidateFields(original[0], original, sections)[0].value, '拓展方案')
  assert.equal(topicCandidateFields({ candidate_id: 'new' }, original, sections).length, 0)
})

test('candidate reading falls back field by field when saved reader content is empty or partial', () => {
  const candidate = {
    candidate_id: 'C1', title: '城市热暴露研究', reader_content: { reason: { text: '新的选题理由', evidence_ids: ['E2'] }, research_content: {} },
    research_object: '长三角高密度城区', research_question: '比较不同绿地结构下的热暴露差异', method_route: '结合遥感温度与街区绿地数据开展分层分析',
  }
  const result = topicCandidateReading(candidate, [candidate], [], { E2: 7 })
  assert.equal(result.reason, '新的选题理由 [7]')
  assert.equal(result.researchContent.map(item => item.value).join('|'), [candidate.research_object, candidate.research_question, candidate.method_route].join('|'))
})

test('legacy candidate evidence keeps substantive statements and removes internal diagnostics', () => {
  const candidate = { candidate_id: 'C1', title: '方向', reader_content: {}, research_question: '核心问题' }
  const sections = [{ blocks: [{ kind: 'candidate_detail', candidate_id: 'C1', fields: [
    { label: '文献依据', value: '相关研究支持以遥感温度比较街区热暴露[4]；关联主题 T1；空白 G2；尚未证明候选假设' },
  ] }] }]
  const result = topicCandidateReading(candidate, [candidate], sections, {})
  assert.match(result.evidenceSummary, /遥感温度比较街区热暴露\[4\]/)
  assert.doesNotMatch(result.evidenceSummary, /T1|G2|尚未证明/)
})

test('candidate text strips internal theme codes and evidence coverage diagnostics', () => {
  const text = topicCandidateReaderText('比较城市热暴露的遥感方法[4]；关联主题 T1、T2；空白 G3；尚未证明候选假设。')
  assert.match(text, /比较城市热暴露的遥感方法\[4\]/)
  assert.doesNotMatch(text, /T1|T2|G3|关联主题|空白|尚未证明/)
})

test('theme list deduplicates saved nodes and never treats the research root as a theme', () => {
  const nodes = [{ theme_id: 'root' }, { theme_id: 'T1' }, { theme_id: 'T2' }, { theme_id: 'T1' }, { theme_id: 'T3' }]
  assert.equal(topicThemeNodes({ nodes }).map(item => item.theme_id).join(','), 'T1,T2,T3')
})

test('theme literature counts can use the deduplicated evidence pool', () => {
  const grouped = uniqueTopicEvidence([
    { evidence_id: 'E1', title: '论文一' }, { evidence_id: 'E1', title: '重复记录' }, { evidence_id: 'E2', title: '论文二' },
  ])
  assert.equal(grouped.length, 2)
  assert.equal(grouped.map(item => item.evidence_id).join(','), 'E1,E2')
})

test('citations follow saved report numbering even when literature is reordered', () => {
  const state = { evidence_pool: [{ evidence_id: 'b', title: '第二篇' }, { evidence_id: 'a', title: '第一篇' }],
    report_plan: { citation_map: { a: 34, b: 60 }, citation_links: { a: 'https://example.org/paper-a' } } }
  const before = JSON.stringify(state)
  const sources = topicCitationSources(state)
  assert.equal(sources[34].title, '第一篇')
  assert.equal(sources[34].url, 'https://example.org/paper-a')
  assert.equal(sources[60].title, '第二篇')
  assert.equal(sources[1], undefined)
  assert.equal(JSON.stringify(state), before)
  const oldSources = topicCitationSources({ report_plan: { sections: [{ blocks: [{ kind: 'reference_list', items: [{ number: 41, title: '旧文献' }] }] }] } })
  assert.equal(oldSources[41].title, '旧文献')
  assert.equal(topicCitationSources({ evidence_pool: state.evidence_pool })[1].title, '第二篇')
})

test('single and grouped citation markers preserve text and individual destinations', () => {
  const text = '已有研究[34][41]；方法比较[34, 41、60]，年份2025及普通文字保持原样。'
  const parts = citationTextParts(text)
  assert.equal(parts.map(part => part.text).join(''), text)
  assert.equal(parts.filter(part => part.number).map(part => part.number).join(','), '34,41,34,41,60')
  assert.equal(parts.find(part => part.number === 34).text, '[34]')
  assert.equal(citationTextParts('<script>无引用</script>').length, 1)
})
