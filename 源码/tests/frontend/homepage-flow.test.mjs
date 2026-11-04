import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import vm from 'node:vm'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const require = createRequire(import.meta.url)
const vue = require('../../frontend/web/node_modules/vue')
function loadVueCompiler() {
  try {
    return require('../../frontend/web/node_modules/@vue/compiler-sfc')
  } catch {
    const pnpmRoot = fileURLToPath(new URL('../../frontend/web/node_modules/.pnpm/', import.meta.url))
    const packageDir = readdirSync(pnpmRoot).find(name => name.startsWith('@vue+compiler-sfc@'))
    assert.ok(packageDir, 'Vue compiler dependency is not installed')
    return require(path.join(pnpmRoot, packageDir, 'node_modules', '@vue', 'compiler-sfc'))
  }
}
const { parse, compileScript } = loadVueCompiler()
const source = await readFile(new URL('../../frontend/web/src/pages/HomeLanding.vue', import.meta.url), 'utf8')
const compiled = ts.transpileModule(compileScript(parse(source).descriptor, { id: 'homepage-flow-test' }).content,
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText

async function service(name) {
  const text = await readFile(new URL(`../../frontend/web/src/services/${name}.ts`, import.meta.url), 'utf8')
  let js = ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  if (name === 'assessmentInput') {
    const draftSource = await readFile(new URL('../../frontend/web/src/services/homepageDraft.ts', import.meta.url), 'utf8')
    const draftJs = ts.transpileModule(draftSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
    js = js.replace("'./homepageDraft'", `'data:text/javascript;base64,${Buffer.from(draftJs).toString('base64')}'`)
  }
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const draft = await service('homepageDraft')
const errors = await service('apiErrors')
const assessmentInput = await service('assessmentInput')
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); await vue.nextTick() }

function completeFields(prefix = '示例') {
  return {
    research_direction: `${prefix}研究方向`,
    research_goal: `${prefix}研究目标`,
    research_content: `${prefix}研究内容`,
    technical_route: `${prefix}技术路线`,
    key_technologies: [`${prefix}关键技术`],
    evaluation_indicators: [`${prefix}考核指标`],
    timeline: `${prefix}时间节点`,
    application_scenarios: [`${prefix}应用场景`],
    expected_outcomes: [`${prefix}预期成果`],
  }
}

async function harness(options = {}) {
  const events = [], calls = [], deleted = [], warnings = []
  let cleanup
  const props = vue.reactive({ activeAgent: options.agent || 'assessment' })
  const mocks = {
    vue: { ...vue, onMounted() {}, onBeforeUnmount(callback) { cleanup = callback } },
    '../services/fileParserService': { FileParserService: class { getFileType() { return 'txt' } async deleteParsedFile(id) { deleted.push(id) } } },
    '../services/fileUploadApi': { uploadFile: options.upload || (async () => ({ fileId: 'file' })) },
    '../services/fileUploadErrors': { getUploadErrorInfo: error => ({ message: error?.message || 'error', code: error?.code || 'error', label: 'error' }) },
    '../services/nstlAuth': { getAuthManager: () => ({ requirePlatformLogin: async () => {}, showLoginModal() {} }) },
    '../services/contentAssessmentUiApi': { runContentAssessment: async body => { calls.push(body); return options.run ? options.run(body) : { run_id: 'task' } } },
    '../services/topicSelectionApi': { runTopicSelection: async body => { calls.push(body); return { run_id: 'topic' } } },
    '../services/contentAssessmentApi': { previewNineFields: options.preview || (async () => ({ nine_fields: completeFields(), resolved_artifact_type: 'project', missing_fields: [], warnings: [] })) },
    '../tracker/contentAssessmentTracker': { errorKind: () => '', trackEvent() {}, trackOutcome() {} },
    '../services/apiErrors': errors,
    '../services/homepageDraft': draft,
    '../services/assessmentInput': assessmentInput,
    '../agents': { AGENTS: [{ id: 'topic', name: '智能选题' }, { id: 'assessment', name: '内容评估' }], getAgent: id => ({ id, name: id }), isAgentAvailable: () => true },
    '../composables/useToast': { useToast: () => ({ show: message => warnings.push(message), error: message => warnings.push(message) }) },
  }
  const context = vm.createContext({ console, Error, setTimeout, document: { activeElement: { focus() {} }, getElementById: () => ({ focus() {} }), removeEventListener() {} }, window: { clearTimeout() {}, removeEventListener() {} } })
  const component = new vm.SourceTextModule(compiled, { context })
  await component.link(specifier => {
    const values = specifier.endsWith('.png') ? { default: 'fixture.png' } : mocks[specifier]
    assert.ok(values, `Unexpected dependency ${specifier}`)
    return new vm.SyntheticModule(Object.keys(values), function() { for (const [key, value] of Object.entries(values)) this.setExport(key, value) }, { context })
  })
  await component.evaluate()
  const state = component.namespace.default.setup(props, { expose() {}, emit(...args) { events.push(args); if (args[0] === 'select-agent') props.activeAgent = args[1] } })
  return { state, props, events, calls, deleted, cleanup: () => cleanup(), warnings }
}

test('assessment case fills only source material and one arrow click extracts then starts', async () => {
  let previews = 0
  const h = await harness({ preview: async text => {
    previews++
    assert.equal(text, draft.ASSESSMENT_CASES[0].input)
    return { nine_fields: completeFields('案例'), resolved_artifact_type: 'paper', missing_fields: [], warnings: [] }
  } })
  h.state.fillCase(draft.ASSESSMENT_CASES[0])
  await flush()
  assert.equal(h.state.form.topic, draft.ASSESSMENT_CASES[0].input.split('\n').slice(1).join('\n'))
  assert.equal(h.state.selectedAssessmentType.value, 'paper')
  assert.equal(h.state.nineDraft.research_direction, '')
  assert.equal(h.state.assessmentNeedsSupplement.value, false)
  await h.state.handleSubmit()
  assert.equal(previews, 1)
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].nine_fields.research_direction, '案例研究方向')
  assert.equal(h.calls[0].scenario_id, draft.ASSESSMENT_CASES[0].id)
  assert.equal(h.events.filter(event => event[0] === 'navigate').length, 1)
})

test('editing a recommended case clears its scenario marker', async () => {
  const h = await harness()
  h.state.fillCase(draft.ASSESSMENT_CASES[0])
  h.state.form.topic += '\n用户补充内容'
  h.state.onTopicInput()
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].scenario_id, undefined)
})

test('all six cases carry their declared type through preview and task creation', async () => {
  const types = { 论文: 'paper', 项目: 'project', 指南: 'guideline' }
  for (const sample of draft.ASSESSMENT_CASES) {
    const expectedType = types[sample.category.split(' · ')[0]]
    const h = await harness({ preview: async (text, _refs, type) => {
      assert.equal(text, sample.input)
      assert.equal(type, expectedType, sample.id)
      const fields = type === 'guideline'
        ? { research_goal: '统一试验口径', research_content: '规范流程', evaluation_indicators: ['跨实验室复现'], application_scenarios: ['实验室质量控制'] }
        : { research_direction: '领域研究', research_content: '方法验证', key_technologies: ['受控实验'] }
      return { nine_fields: fields, resolved_artifact_type: type, missing_fields: [], warnings: [], validation_version: 'assessment-input-semantic-v2' }
    } })
    h.state.fillCase(sample)
    await flush()
    assert.equal(h.state.selectedAssessmentType.value, expectedType, sample.id)
    assert.doesNotMatch(h.state.form.topic, /我要评估的是/)
    await h.state.handleSubmit()
    assert.equal(h.calls.length, 1, sample.id)
    assert.equal(h.calls[0].artifact_type, expectedType, sample.id)
    assert.equal(h.calls[0].nine_fields.timeline, '', sample.id)
    assert.doesNotMatch(h.state.assessmentFeedback.value, /请在输入框中说明/)
  }
})

test('obsolete service is reported as a service problem and never submits a case', async () => {
  assert.throws(() => assessmentInput.requireCurrentAssessmentPreview({ missing_fields: ['research_goal', 'timeline'] }),
    error => error.name === 'AssessmentServiceVersionError' && /旧版输入规则/.test(error.message))
  const h = await harness({ preview: async () => assessmentInput.requireCurrentAssessmentPreview({ missing_fields: ['research_goal', 'timeline'] }) })
  h.state.fillCase(draft.ASSESSMENT_CASES[0])
  const original = h.state.form.topic
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.equal(h.state.form.topic, original)
  assert.match(h.state.assessmentFeedback.value, /旧版输入规则/)
  const current = { validation_version: 'assessment-input-semantic-v2', missing_fields: [] }
  assert.equal(assessmentInput.requireCurrentAssessmentPreview(current), current)
})

test('declared guideline still requires its missing information and retains attachments', async () => {
  const h = await harness({ preview: async (_text, _refs, type) => {
    assert.equal(type, 'guideline')
    return { nine_fields: { research_content: '指南内容' }, resolved_artifact_type: type, missing_fields: ['research_goal', 'evaluation_indicators', 'application_scenarios'], warnings: [] }
  } })
  h.state.form.topic = '我要评估的是指南\n研究内容：指南内容'
  h.state.uploadedFiles.value = [{ name: '参考论文.txt', fileId: 'reference', type: 'txt', status: 'saved' }]
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.match(h.state.assessmentFeedback.value, /已识别为指南.*研究目标.*考核指标.*应用场景/)
  assert.equal(h.state.uploadedFiles.value.length, 1)
})

test('a response without extracted fields cannot start just because a type is declared', async () => {
  const h = await harness({ preview: async () => ({ nine_fields: {}, resolved_artifact_type: 'unknown', missing_fields: ['artifact_type'], warnings: [] }) })
  h.state.fillCase(draft.ASSESSMENT_CASES.find(sample => sample.category.startsWith('指南')))
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.match(h.state.assessmentFeedback.value, /已识别为指南.*研究目标.*研究内容.*考核指标.*应用场景/)
  assert.doesNotMatch(h.state.assessmentFeedback.value, /请在输入框中说明/)
})

test('legacy preview without type starts a complete guideline case without attachments', async () => {
  const h = await harness({ preview: async (_text, files) => {
    assert.equal(files.length, 0)
    return { nine_fields: { research_goal: '统一规范', research_content: '制备培养检测流程', evaluation_indicators: ['三家实验室复现验证'], application_scenarios: ['屏障研究'] }, missing_fields: ['research_direction', 'technical_route', 'timeline'], warnings: [] }
  } })
  h.state.fillCase(draft.ASSESSMENT_CASES.find(sample => sample.id === 'assessment-standard'))
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].artifact_type, 'guideline')
  assert.equal(h.calls[0].uploaded_files.length, 0)
})

test('legacy preview still stops a guideline with missing indicators', async () => {
  const h = await harness({ preview: async () => ({ nine_fields: { research_goal: '统一规范', research_content: '制备培养检测流程', application_scenarios: ['屏障研究'] }, missing_fields: [], warnings: [] }) })
  h.state.fillCase(draft.ASSESSMENT_CASES.find(sample => sample.id === 'assessment-standard'))
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.match(h.state.assessmentFeedback.value, /补充考核指标/)
})

test('missing information stays in composer and appended text is checked before starting', async () => {
  let previews = 0
  const h = await harness({ preview: async (text, _refs, type) => {
    previews++
    assert.equal(type, 'paper')
    return previews === 1
      ? { nine_fields: { research_direction: '已提取方向' }, resolved_artifact_type: 'paper', missing_fields: ['research_content', 'key_technologies'], warnings: [] }
      : { nine_fields: { research_direction: '已提取方向', research_content: '研究内容补充值', key_technologies: ['关键技术补充值'] }, resolved_artifact_type: 'paper', missing_fields: [], warnings: [] }
  } })
  h.state.form.topic = '请评估这篇论文\n研究方向：材料科学'
  h.state.uploadedFiles.value = [{ name: '论文.txt', fileId: 'paper-source', type: 'txt', status: 'saved' }]
  await h.state.handleSubmit()
  assert.equal(previews, 1)
  assert.equal(h.state.form.topic, '请评估这篇论文\n研究方向：材料科学')
  assert.equal(h.state.assessmentNeedsSupplement.value, true)
  assert.match(h.state.assessmentFeedback.value, /研究内容.*关键技术/)
  assert.equal(h.calls.length, 0)
  h.state.form.topic += '\n研究内容：研究内容补充值\n关键方法：关键技术补充值'
  h.state.onTopicInput()
  assert.equal(h.state.assessmentSubmitLabel.value, '补充并继续')
  await h.state.handleSubmit()
  assert.equal(previews, 2)
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].artifact_type, 'paper')
  assert.equal(h.calls[0].uploaded_files[0].file_id, 'paper-source')
  assert.deepEqual(h.calls[0].nine_fields.expected_outcomes, [])
})

test('duplicate arrow clicks share one extraction and one task creation', async () => {
  const preview = deferred()
  const pending = deferred()
  const h = await harness({ preview: () => preview.promise, run: () => pending.promise })
  h.state.fillCase(draft.ASSESSMENT_CASES[0])
  const first = h.state.handleSubmit()
  const duplicate = h.state.handleSubmit()
  await flush()
  preview.resolve({ nine_fields: completeFields(), resolved_artifact_type: 'project', missing_fields: [], warnings: [] })
  await flush()
  assert.equal(h.calls.length, 1)
  pending.resolve({ run_id: 'task' })
  await first
  await duplicate
  assert.equal(h.events.filter(event => event[0] === 'navigate').length, 1)
})

test('failed submission retains extracted fields and exposes the backend reason', async () => {
  const h = await harness({ run: async () => { throw new Error('failure') } })
  h.state.fillCase(draft.ASSESSMENT_CASES[1])
  await h.state.handleSubmit()
  assert.equal(h.state.submitError.value, 'failure')
  assert.equal(h.state.isLoading.value, false)
  assert.equal(h.state.nineDraft.research_direction, '示例研究方向')
})

test('obsolete extraction cannot overwrite fields for changed source text', async () => {
  const requests = []
  const h = await harness({ preview: () => { const request = deferred(); requests.push(request); return request.promise } })
  h.state.form.topic = 'first text'
  const old = h.state.extractNineFields()
  await flush()
  h.state.form.topic = 'new text'
  h.state.onTopicInput()
  const current = h.state.extractNineFields()
  await flush()
  requests[1].resolve({ nine_fields: { research_direction: 'new', timeline: 'model' }, resolved_artifact_type: 'project', missing_fields: [], warnings: [] })
  await current
  requests[0].resolve({ nine_fields: { research_direction: 'old' }, resolved_artifact_type: 'project', missing_fields: [], warnings: [] })
  await old
  assert.equal(h.state.nineDraft.research_direction, 'new')
  assert.equal(h.state.nineDraft.timeline, 'model')
  assert.equal(h.state.isExtracting.value, false)
  assert.equal(h.calls.length, 0)
})

test('preview failure retains input and asks to retry without treating service error as missing data', async () => {
  const h = await harness({ preview: async () => ({ nine_fields: {}, extraction_failed: true, warnings: ['failed'] }) })
  h.state.form.topic = 'text'
  await h.state.handleSubmit()
  assert.equal(h.state.form.topic, 'text')
  assert.match(h.state.assessmentFeedback.value, /重试.*粘贴/)
  assert.equal(h.calls.length, 0)
  assert.equal(h.state.isExtracting.value, false)
  assert.equal(h.state.assessmentNeedsSupplement.value, false)
})

test('multi-file changes ignore obsolete fields and recheck remaining references', async () => {
  const captured = [], requests = []
  const h = await harness({ preview: async (_text, refs) => { captured.push(refs); const request = deferred(); requests.push(request); return request.promise } })
  h.state.uploadedFiles.value = [{ name: 'one.txt', fileId: 'one', type: 'txt', status: 'saved' }, { name: 'two.txt', fileId: 'two', type: 'txt', status: 'saved' }]
  const extraction = h.state.extractNineFields()
  await flush()
  h.state.removeFile(0)
  await flush()
  assert.deepEqual(captured.map(refs => refs.map(file => file.file_id)), [['one', 'two']])
  requests[0].resolve({ nine_fields: { timeline: 'removed' }, resolved_artifact_type: 'project', missing_fields: [], warnings: [] })
  await extraction
  const remainingExtraction = h.state.extractNineFields()
  await flush()
  assert.deepEqual(captured.map(refs => refs.map(file => file.file_id)), [['one', 'two'], ['two']])
  requests[1].resolve({ nine_fields: { research_goal: 'new goal', timeline: 'remaining' }, resolved_artifact_type: 'project', missing_fields: [], warnings: [] })
  await remainingExtraction
  assert.equal(h.state.nineDraft.timeline, 'remaining')
  assert.equal(h.state.nineDraft.research_goal, 'new goal')
})

test('upload invalidates extraction but does not automatically extract before arrow click', async () => {
  const captured = []
  const h = await harness({ preview: async (_text, refs) => {
    captured.push(refs)
    return { nine_fields: { research_direction: 'uploaded direction', research_goal: 'model goal' }, resolved_artifact_type: 'project', missing_fields: ['research_content', 'key_technologies'], warnings: [] }
  } })
  h.state.fillCase(draft.ASSESSMENT_CASES[0])
  await h.state.processFiles([{ name: 'material.txt', size: 10, lastModified: 1 }])
  await flush()
  assert.equal(captured.length, 0)
  await h.state.handleSubmit()
  await flush()
  assert.equal(captured.length, 1)
  assert.equal(captured[0][0].file_id, 'file')
  assert.equal(h.state.nineDraft.research_direction, 'uploaded direction')
  assert.equal(h.calls.length, 0)
})

test('concurrent upload blocked and late upload after leaving cleans up its parsed file', async () => {
  const pending = deferred()
  let uploads = 0
  const h = await harness({ upload: () => { uploads++; return pending.promise } })
  const file = { name: 'one.txt', size: 10, lastModified: 1 }
  const upload = h.state.processFiles([file])
  await h.state.processFiles([{ ...file, name: 'two.txt' }])
  assert.equal(uploads, 1)
  h.cleanup()
  pending.resolve({ fileId: 'late' })
  await upload
  assert.deepEqual(h.deleted, ['late'])
})

test('topic sends five candidates and selected conditions; switching tag returns public cases', async () => {
  const h = await harness({ agent: 'topic' })
  h.state.form.topic = 'research question'
  h.state.timeRange.value = 'all'
  h.state.outputPreference.value = 'exploratory'
  await h.state.handleSubmit()
  assert.equal(h.calls[0].candidate_count, 5)
  assert.equal(h.calls[0].output_preference, 'exploratory')
  assert.deepEqual(Array.from(h.calls[0].year_range), [])
  h.state.selectAgent(null)
  await flush()
  assert.equal(h.state.visibleCases.value.length, 9)
})


test('unknown type requests text clarification without defaulting to project', async () => {
  const h = await harness({ preview: async () => ({ nine_fields: completeFields(), resolved_artifact_type: 'unknown', missing_fields: ['artifact_type'], warnings: [] }) })
  h.state.form.topic = '研究材料'
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.equal(h.state.assessmentType.value, 'auto')
  assert.match(h.state.assessmentFeedback.value, /论文、项目还是指南/)
})

test('changing declared type rechecks the text and replaces previously extracted fields', async () => {
  const h = await harness({ preview: async text => text.includes('指南')
    ? { nine_fields: { research_content: '指南内容' }, resolved_artifact_type: 'guideline', missing_fields: ['research_goal', 'evaluation_indicators', 'application_scenarios'], warnings: [] }
    : { nine_fields: { research_direction: '论文方向' }, resolved_artifact_type: 'paper', missing_fields: ['research_content', 'key_technologies'], warnings: [] } })
  h.state.form.topic = '请评估这篇论文'
  await h.state.handleSubmit()
  h.state.form.topic = '请评估这份指南\n研究内容：芯片标准化'
  h.state.onTopicInput()
  await h.state.handleSubmit()
  assert.equal(h.state.assessmentType.value, 'guideline')
  assert.equal(h.state.nineDraft.research_direction, '')
  assert.match(h.state.assessmentFeedback.value, /研究目标.*考核指标.*应用场景/)
  assert.equal(h.calls.length, 0)
})


test('inline type selection inserts guides without preview and switches only empty labels', async () => {
  let previews = 0
  const h = await harness({ preview: async () => { previews++; throw new Error('not expected') } })
  assert.equal(h.state.form.topic, '')
  assert.equal(h.state.selectedAssessmentType.value, 'auto')
  h.state.selectAssessmentType('paper')
  await flush()
  assert.equal(h.state.form.topic, '研究方向：\n研究内容：\n关键方法：')
  assert.equal(previews, 0)
  h.state.form.topic = h.state.form.topic.replace('研究方向：', '研究方向：数字图书馆')
  h.state.onTopicInput()
  h.state.selectAssessmentType('guideline')
  assert.match(h.state.form.topic, /研究方向：数字图书馆/)
  assert.match(h.state.form.topic, /研究目标：\n研究内容：\n考核指标：\n应用场景：/)
  assert.doesNotMatch(h.state.form.topic, /关键方法：/)
})

test('guided intake respects IME and rejects type-only or empty guides as material', async () => {
  let previews = 0
  const h = await harness({ preview: async () => { previews++; throw new Error('not expected') } })
  await h.state.handleSubmit()
  assert.equal(previews, 0)
  h.state.form.topic = '我要评估的是：论文'
  h.state.onTopicInput({ isComposing: true })
  assert.equal(h.state.form.topic, '我要评估的是：论文')
  h.state.onTopicInput({ isComposing: false })
  assert.match(h.state.form.topic, /研究方向：/)
  assert.equal(h.state.selectedAssessmentType.value, 'paper')
  assert.equal(assessmentInput.homepageAssessmentBody(h.state.form.topic), '')
  await h.state.handleSubmit()
  assert.equal(previews, 0)
  assert.equal(h.calls.length, 0)
  assert.match(h.state.validationErrors.topic, /填写评估内容/)
})

test('handwritten assessment type guides the body and reaches preview and task creation', async () => {
  const captured = []
  const h = await harness({ preview: async (text, files, type) => {
    captured.push({ text, files, type })
    return { validation_version: 'v2', nine_fields: completeFields(), resolved_artifact_type: type, missing_fields: [] }
  } })
  h.state.assessmentTypeText.value = '论文'
  h.state.onAssessmentTypeInput()
  assert.equal(h.state.selectedAssessmentType.value, 'paper')
  assert.equal(h.state.form.topic, '研究方向：\n研究内容：\n关键方法：')
  h.state.form.topic = '研究方向：证据链验证\n研究内容：比较来源追踪策略\n关键方法：引文图谱分析'
  h.state.onTopicInput()
  await h.state.handleSubmit()
  assert.equal(captured[0].type, 'paper')
  assert.match(captured[0].text, /^我要评估的是：论文\n研究方向：证据链验证/)
  assert.equal(h.calls[0].artifact_type, 'paper')
})

test('explicit type survives prose and attachment changes and is sent with both requests', async () => {
  const captured = []
  const h = await harness({ preview: async (text, files, type) => {
    captured.push({ text, files, type })
    return { validation_version: 'v2', nine_fields: completeFields(), resolved_artifact_type: type, missing_fields: [] }
  } })
  h.state.selectAssessmentType('guideline')
  h.state.form.topic = '研究目标：建立标准。参考论文及项目仅用于背景。'
  h.state.onTopicInput()
  await h.state.processFiles([{ name: '参考论文.txt', size: 10, lastModified: 1 }])
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
  await h.state.handleSubmit()
  assert.equal(captured[0].type, 'guideline')
  assert.match(captured[0].text, /^我要评估的是：指南\n研究目标：/)
  assert.equal(h.calls[0].artifact_type, 'guideline')
  assert.equal(h.calls[0].user_input, captured[0].text)
  assert.equal(h.calls[0].uploaded_files[0].file_id, 'file')
})

test('attachments alone can start with selected type, while prose stays untouched on type switch', async () => {
  const h = await harness({ preview: async (text, files, type) => {
    assert.equal(text, '我要评估的是：论文')
    assert.equal(files[0].file_id, 'main')
    assert.equal(type, 'paper')
    return { nine_fields: completeFields(), resolved_artifact_type: type, missing_fields: [] }
  } })
  h.state.form.topic = '完整研究说明，引用指南和论文作为参考资料。'
  h.state.selectAssessmentType('project')
  assert.equal(h.state.form.topic, '完整研究说明，引用指南和论文作为参考资料。')
  h.state.form.topic = ''
  h.state.selectAssessmentType('paper')
  h.state.uploadedFiles.value = [{ name: 'material.pdf', fileId: 'main', status: 'saved' }]
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].artifact_type, 'paper')
})

test('switching type while preview runs discards the previous response', async () => {
  const pending = deferred(), captured = []
  const h = await harness({ preview: async (_text, _files, type) => {
    captured.push(type)
    return captured.length === 1 ? pending.promise : { validation_version: 'v2', nine_fields: completeFields(), resolved_artifact_type: type, missing_fields: [] }
  } })
  h.state.selectAssessmentType('paper')
  h.state.form.topic = '研究内容：保留有效正文'
  const first = h.state.handleSubmit()
  await flush()
  h.state.selectAssessmentType('guideline')
  pending.resolve({ validation_version: 'v2', nine_fields: completeFields('旧'), resolved_artifact_type: 'paper', missing_fields: [] })
  await first
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
  assert.equal(h.state.assessmentType.value, 'auto')
  assert.equal(h.calls.length, 0)
  await h.state.handleSubmit()
  assert.deepEqual(captured, ['paper', 'guideline'])
  assert.equal(h.calls[0].artifact_type, 'guideline')
})

test('versioned preview cannot silently change an explicitly selected type', async () => {
  const h = await harness({ preview: async () => ({ validation_version: 'v2', nine_fields: completeFields(), resolved_artifact_type: 'project', missing_fields: [] }) })
  h.state.selectAssessmentType('guideline')
  h.state.form.topic = '研究内容：器官芯片标准化研究'
  await h.state.handleSubmit()
  assert.equal(h.calls.length, 0)
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
  assert.match(h.state.assessmentFeedback.value, /不一致.*重试/)
})

test('natural declaration updates selection, while reference mentions do not', async () => {
  const h = await harness()
  h.state.selectAssessmentType('paper')
  h.state.form.topic = '请评估这份指南，研究内容：多器官芯片。参考论文中比较了不同方法。'
  h.state.onTopicInput()
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
  assert.equal(h.state.form.topic, '研究内容：多器官芯片。参考论文中比较了不同方法。')
  h.state.form.topic += '\n参考材料：论文、项目。'
  h.state.onTopicInput()
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
})

test('public-home case switch selects the case type and topic cases keep their full text', async () => {
  const h = await harness({ agent: 'topic' })
  const sample = draft.ASSESSMENT_CASES.find(sample => sample.category.startsWith('指南'))
  h.state.fillCase(sample)
  await flush()
  assert.equal(h.props.activeAgent, 'assessment')
  assert.equal(h.state.selectedAssessmentType.value, 'guideline')
  assert.equal(h.state.selectedAssessmentScenarioId.value, sample.id)
  h.state.fillCase(draft.TOPIC_CASES[0])
  await flush()
  assert.equal(h.state.form.topic, draft.TOPIC_CASES[0].input)
  assert.equal(h.state.selectedAssessmentType.value, 'auto')
})

test('versioned preview remains authoritative and does not acquire extra frontend requirements', () => {
  const preview = { validation_version: 'assessment-input-semantic-v2', resolved_artifact_type: 'guideline',
    nine_fields: { research_content: '摘要' }, missing_fields: [], extraction_failed: false }
  assert.equal(assessmentInput.normalizeAssessmentPreview(preview, '我要评估的是：指南'), preview)
  assert.equal(assessmentInput.assessmentPreviewReady(preview), true)
})

test('old response reconciles a stale missing list with actual extracted fields', () => {
  const preview = { resolved_artifact_type: 'guideline', nine_fields: completeFields(), missing_fields: ['evaluation_indicators'] }
  const result = assessmentInput.normalizeAssessmentPreview(preview, '我要评估的是：指南')
  assert.deepEqual(result.missing_fields, [])
  assert.equal(assessmentInput.assessmentPreviewReady(result), true)
})

test('indicator clarification accepts qualitative requirements and keeps technical keys out of feedback', () => {
  const message = assessmentInput.assessmentPreviewMessage({ resolved_artifact_type: 'guideline', missing_fields: ['evaluation_indicators'] })
  assert.match(message, /操作规范、验证要求或性能指标/)
  assert.match(message, /不必都写成数值/)
  assert.doesNotMatch(message, /evaluation_indicators/)
})
