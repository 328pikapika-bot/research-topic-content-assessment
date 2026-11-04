import { NINE_FIELDS, emptyNineDraft, mergeNineDraft, missingNineFields, type NineFields } from './homepageDraft'

export const assessmentPlaceholder = '在这里输入材料类型和评估内容，也可粘贴正文或上传材料。'
export const assessmentTypeLabels = { paper: '论文', project: '项目', guideline: '指南' } as const
export type AssessmentInputType = keyof typeof assessmentTypeLabels
export const assessmentInputStarter = '我要评估的是：'
const assessmentTemplateFields = {
  paper: ['研究方向', '研究内容', '关键方法'],
  project: ['研究方向', '研究内容', '关键技术或技术路线'],
  guideline: ['研究目标', '研究内容', '考核指标', '应用场景'],
} as const
const templateLabels = new Set<string>(Object.values(assessmentTemplateFields).flat())

export function declaredAssessmentType(text: string): AssessmentInputType | undefined {
  // Read the user's declaration, never the case category or words in references.
  const header = /^\s*(?:我要评估的是\s*[：:]?\s*)(论文|项目|指南)(?=$|[\s，,。；;])/m.exec(text)
    || /^\s*(论文|项目|指南)\s*$/m.exec(text.split('\n')[0] || '')
    || /(?:请|我要|我想|我需要)?评估\s*(?:这|该)?\s*(?:篇|个|份|项)?\s*(论文|项目|指南)(?=$|[\s，,。；;])/.exec(text)
  return header ? ({ 论文: 'paper', 项目: 'project', 指南: 'guideline' } as const)[header[1] as '论文' | '项目' | '指南'] : undefined
}

export function assessmentGuidedInput(text: string, previousType?: AssessmentInputType) {
  const header = /^(?:我要评估的是[：:]\s*|)(论文|项目|指南)[ \t]*$/.exec(text.split('\n')[0] || '')
  const type = header ? ({ 论文: 'paper', 项目: 'project', 指南: 'guideline' } as const)[header[1] as '论文' | '项目' | '指南'] : undefined
  if (!type || type === previousType) return { text, type, cursor: undefined }
  // Replace only empty prompt rows when the type changes; preserve all user content.
  const remaining = text.split('\n').slice(1).filter(line => {
    const match = /^([^：:]+)[：:]\s*$/.exec(line.trim())
    return !match || !templateLabels.has(match[1])
  }).join('\n').trimEnd()
  let result = assessmentInputStarter + assessmentTypeLabels[type] + (remaining ? '\n' + remaining : '')
  let cursor: number | undefined
  for (const label of assessmentTemplateFields[type]) {
    const equivalent = label.startsWith('关键') ? /(?:关键技术或技术路线|关键技术或方法|关键技术|关键方法|技术路线)[：:]/ : new RegExp(label + '[：:]')
    if (equivalent.test(remaining)) continue
    result += '\n' + label + '：'
    if (cursor === undefined) cursor = result.length
  }
  return result.length <= 12000 ? { text: result, type, cursor } : { text, type, cursor: undefined }
}

export function assessmentSourceText(text: string) {
  return text.split('\n').filter(line => {
    if (line.trim() === assessmentInputStarter) return false
    const match = /^([^：:]+)[：:]\s*$/.exec(line.trim())
    return !match || !templateLabels.has(match[1])
  }).join('\n').trim()
}

// Homepage keeps the main-object declaration outside the editable material body.
// Only consume an explicit opening declaration; reference-material names stay intact.
export function splitHomepageAssessmentInput(text: string) {
  const declaration = /^\s*(?:我要评估的是\s*[：:]?\s*|(?:请|我要|我想|我需要)?评估\s*(?:这|该)?\s*(?:篇|个|份|项)?\s*)(论文|项目|指南)(?=$|[\s，,。；;])/u.exec(text)
    || /^\s*(论文|项目|指南)[ \t]*(?=\r?\n|$)/u.exec(text)
  if (!declaration) return { text: text.trim() === assessmentInputStarter ? '' : text, type: undefined }
  const type = ({ 论文: 'paper', 项目: 'project', 指南: 'guideline' } as const)[declaration[1] as '论文' | '项目' | '指南']
  return { text: text.slice(declaration[0].length).replace(/^[ \t，,。；;]*(?:\r?\n)?/u, ''), type }
}

export function homepageAssessmentBody(text: string) {
  return assessmentSourceText(splitHomepageAssessmentInput(text).text)
}

export function homepageAssessmentSource(text: string, selectedType?: AssessmentInputType) {
  const input = splitHomepageAssessmentInput(text)
  const body = assessmentSourceText(input.text)
  const type = selectedType || input.type
  return type ? `${assessmentInputStarter}${assessmentTypeLabels[type]}${body ? '\n' + body : ''}` : body
}

export function homepageAssessmentGuide(text: string, type: AssessmentInputType) {
  const body = splitHomepageAssessmentInput(text).text
  const remaining = body.split('\n').filter(line => {
    const match = /^([^：:]+)[：:]\s*$/.exec(line.trim())
    return !match || !templateLabels.has(match[1])
  }).join('\n').trimEnd()
  // Complete pasted prose needs no additional template. Update guides only while
  // the user is still filling a template, or when starting with a blank body.
  if (remaining.trim() && !body.split('\n').some(line => {
    const match = /^([^：:]+)[：:]\s*$/.exec(line.trim())
    return Boolean(match && templateLabels.has(match[1]))
  })) return { text: body, cursor: undefined }
  let result = remaining
  let cursor: number | undefined
  for (const label of assessmentTemplateFields[type]) {
    const equivalent = label.startsWith('关键') ? /(?:关键技术或技术路线|关键技术或方法|关键技术|关键方法|技术路线)[：:]/ : new RegExp(label + '[：:]')
    if (equivalent.test(remaining)) continue
    result += (result ? '\n' : '') + label + '：'
    if (cursor === undefined) cursor = result.length
  }
  return result.length <= 12000 ? { text: result, cursor } : { text: body, cursor: undefined }
}
export interface AssessmentPreview {
  resolved_artifact_type?: string
  missing_fields: string[]
  extraction_failed?: boolean
  validation_version?: string
}
export function normalizeAssessmentPreview<T extends AssessmentPreview & { nine_fields?: Partial<NineFields> }>(preview: T, sourceText: string): T {
  // Versioned responses use the shared server rules, including its source review.
  if (preview.validation_version) return preview
  const declared = declaredAssessmentType(sourceText)
  const type = declared || (recognizedAssessmentType(preview.resolved_artifact_type) ? preview.resolved_artifact_type : undefined)
  if (!type || !preview.nine_fields) return preview
  // Older responses can omit the type or carry a stale missing-fields list.
  // Reconcile their extracted fields with the user's declared main object.
  const draft = emptyNineDraft()
  mergeNineDraft(draft, preview.nine_fields, new Set())
  return { ...preview, resolved_artifact_type: type, missing_fields: missingNineFields(draft, type).map(field => field.key) }
}
export function requireCurrentAssessmentPreview<T extends AssessmentPreview>(preview: T): T {
  if (preview.validation_version !== 'assessment-input-semantic-v2') {
    const error = new Error('当前连接的评估服务仍使用旧版输入规则，请重启本地服务并刷新页面；案例文字已保留，无需补填前八项。')
    error.name = 'AssessmentServiceVersionError'
    throw error
  }
  return preview
}
export function recognizedAssessmentType(value?: string): value is AssessmentInputType {
  return value === 'paper' || value === 'project' || value === 'guideline'
}
export function assessmentPreviewReady(preview: AssessmentPreview) {
  return recognizedAssessmentType(preview.resolved_artifact_type) && !preview.extraction_failed && preview.missing_fields.length === 0
}
export function assessmentPreviewMessage(preview: AssessmentPreview, sourceText = '') {
  if (preview.extraction_failed) return '材料识别暂未完成，请重试，或在输入框中粘贴材料正文。'
  if (!recognizedAssessmentType(preview.resolved_artifact_type) && declaredAssessmentType(sourceText)) return '材料类型已填写，但本次识别未完成，请重试；输入内容已保留。'
  if (!recognizedAssessmentType(preview.resolved_artifact_type)) return '请在输入框中说明要评估的是论文、项目还是指南。'
  const type = assessmentTypeLabels[preview.resolved_artifact_type]
  const missing = preview.missing_fields.map(key => key === 'key_technologies' ? (preview.resolved_artifact_type === 'paper' ? '关键技术或方法' : '关键技术或技术路线') : NINE_FIELDS.find(field => field.key === key)?.label || key)
  if (preview.missing_fields.length === 1) {
    const examples: Record<string, string> = {
      research_direction: '说明主要研究的对象或所属领域即可',
      research_goal: '说明这份材料希望解决什么问题、达到什么目标即可',
      research_content: '说明准备开展或已经开展的主要研究工作即可',
      key_technologies: '说明采用的实验方法、算法或实施路线即可，不必同时填写技术和路线',
      evaluation_indicators: '可以是需要形成的操作规范、验证要求或性能指标，不必都写成数值',
      application_scenarios: '说明面向哪些对象、使用环境或实际用途即可',
    }
    const example = examples[preview.missing_fields[0]]
    if (example) return `已识别为${type}，请在原文后补充${missing[0]}；${example}。已有文字和材料会保留。`
  }
  return missing.length ? `已识别为${type}，请在输入框中补充${missing.join('、')}，输入内容会保留。` : `已识别为${type}，正在开始评估。`
}
