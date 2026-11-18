export const NINE_FIELDS = [
  { key: 'research_direction', label: '研究方向', array: false },
  { key: 'research_goal', label: '研究目标', array: false },
  { key: 'research_content', label: '研究内容', array: false },
  { key: 'technical_route', label: '技术路线', array: false },
  { key: 'key_technologies', label: '关键技术', array: true },
  { key: 'evaluation_indicators', label: '考核指标', array: true },
  { key: 'timeline', label: '时间节点', array: false },
  { key: 'application_scenarios', label: '应用场景', array: true },
  { key: 'expected_outcomes', label: '预期成果', array: true },
] as const
export type NineFieldKey = typeof NINE_FIELDS[number]['key']
export type NineFields = Record<NineFieldKey, string | string[]>
export type NineDraft = Record<NineFieldKey, string>
export type SelectedAssessmentType = 'paper' | 'project' | 'guideline'
export function requiredNineFieldKeys(type: SelectedAssessmentType | 'auto'): NineFieldKey[] {
  if (type === 'paper' || type === 'project') return ['research_direction', 'research_content', 'key_technologies']
  if (type === 'guideline') return ['research_goal', 'research_content', 'evaluation_indicators', 'application_scenarios']
  // Resolve the type first; it never implies that all legacy fields are required.
  return []
}
export type OutputPreference = 'balanced' | 'recommended' | 'exploratory'
export function emptyNineDraft(): NineDraft {
  return Object.fromEntries(NINE_FIELDS.map(field => [field.key, ''])) as NineDraft
}
export function serializeNineDraft(draft: NineDraft): NineFields {
  return Object.fromEntries(NINE_FIELDS.map(field => [field.key, field.array
    ? draft[field.key].split(/[\n；;]/).map(value => value.trim()).filter(Boolean)
    : draft[field.key].trim()])) as NineFields
}
export function mergeNineDraft(draft: NineDraft, incoming: Partial<NineFields>, touched: Set<NineFieldKey>) {
  for (const field of NINE_FIELDS) {
    if (touched.has(field.key)) continue
    const value = incoming[field.key]
    draft[field.key] = Array.isArray(value) ? value.join('\n') : value || ''
  }
}
function meaningful(value: string) { return !!value.trim() && !/^(待确认|待从|待依据|未提供|未说明|材料未说明|不详|未知|待补充|暂无|未提取|无$)/.test(value.trim()) }
export function missingNineFields(draft: NineDraft, type: SelectedAssessmentType | 'auto' = 'auto') {
  const values = serializeNineDraft(draft)
  const required = requiredNineFieldKeys(type)
  const missing = NINE_FIELDS.filter(field => required.includes(field.key) && !(Array.isArray(values[field.key]) ? (values[field.key] as string[]).some(meaningful) : meaningful(values[field.key] as string))
    && !(type !== 'auto' && field.key === 'key_technologies' && meaningful(values.technical_route as string)))
  return missing
}
export function topicYearRange(range: string, year = new Date().getFullYear()): number[] {
  const count = Number(range)
  return [3, 5, 10].includes(count) ? [year - count + 1, year] : []
}

export interface HomepageCase { id: string; title: string; category: string; icon: string; agent: 'topic' | 'assessment'; input: string; scenarioId?: string }
import caseCatalog from './homepageCases.json'
export const PUBLIC_CASES = caseCatalog as HomepageCase[]
export const TOPIC_CASES = PUBLIC_CASES.filter(sample => sample.agent === 'topic')
export const ASSESSMENT_CASES = PUBLIC_CASES.filter(sample => sample.agent === 'assessment')
