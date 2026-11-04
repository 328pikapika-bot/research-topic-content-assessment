/** Reader-facing grouping never changes a candidate's scientific evidence status. */
export function topicDisplayTitle(state: Record<string, any>, fallback = ''): string {
  return String(state.stage_artifacts?.input_modeling?.analysis?.concise_title
    || state.stage_artifacts?.landscape_trend?.analysis?.concise_title
    || state.input_profile?.title || fallback || '智能选题任务').trim()
}
export function candidateDirection(candidate: Record<string, any>): 'recommended' | 'exploratory' | 'candidate' {
  if (['recommended', 'exploratory'].includes(candidate.direction_type)) return candidate.direction_type
  if (/^探索方向[:：]/.test(candidate.title || '') || /^探索方向[:：]/.test(candidate.display_title || '')) return 'exploratory'
  if (candidate.recommendation_level === 'recommended' || candidate.convergence_valid === true) return 'recommended'
  if (candidate.recommendation_level === 'exploratory') return 'exploratory'
  return 'candidate'
}
export function orderedTopicCandidates<T extends Record<string, any>>(items: T[]): T[] {
  const rank = { recommended: 0, candidate: 1, exploratory: 2 }
  return items.map((item, index) => ({ item, index })).sort((a, b) =>
    rank[candidateDirection(a.item)] - rank[candidateDirection(b.item)] || a.index - b.index).map(row => row.item)
}
export function topicCandidateFields(candidate: Record<string, any>, original: Record<string, any>[], sections: Record<string, any>[]) {
  const blocks = sections.flatMap(section => section.blocks || []).filter(block => block.kind === 'candidate_detail')
  const exact = blocks.find(block => block.candidate_id === candidate.candidate_id)
  const index = original.findIndex(item => item.candidate_id === candidate.candidate_id) + 1
  const title = (text: string) => String(text || '').replace(/^\d+[.、]\s*/, '').trim()
  const byTitle = blocks.find(block => [candidate.title, candidate.display_title].filter(Boolean).includes(title(block.title)))
  return (exact || byTitle || blocks.find(block => index > 0 && block.index === index))?.fields || []
}
export function topicCitationMap(state: Record<string, any>): Record<string, number> {
  const saved = state.report_plan?.citation_map || {}
  if (Object.keys(saved).length) return saved
  return Object.fromEntries((state.evidence_pool || []).map((item: any, index: number) => [item.evidence_id, index + 1]))
}
export function topicCandidateReaderText(value: unknown): string {
  let text = String(value ?? '')
  text = text.replace(/(?:关联)?(?:研究)?主题\s*[TG]\d+(?:\s*[、,，]\s*[TG]\d+)*/g, '')
    .replace(/空白\s*G\d+(?:\s*[、,，]\s*G\d+)*/g, '')
    .replace(/\b(?:E-[A-Za-z0-9_-]+|M-INPUT|[TG]\d+)\b/g, '')
  const diagnostics = /尚未证明|尚不证明候选假设|摘要未提及|摘要或元数据|仅有摘要|全文不可用|证据范围|全文摘录|当前仅有/i
  text = text.split(/(?<=[。；;！？!?])|(?=尚未证明|尚不证明候选假设|摘要未提及|摘要或元数据|仅有摘要|全文不可用|证据范围|全文摘录|当前仅有)/i)
    .filter(part => part.trim() && !diagnostics.test(part)).join('')
  return text.replace(/\s+([，。；、])/g, '$1').replace(/[，；、]{2,}/g, '；').trim().replace(/^[，；、]+|[，；、]+$/g, '')
}
export function topicCandidateReading(candidate: Record<string, any>, original: Record<string, any>[], sections: Record<string, any>[], citationMap: Record<string, number>) {
  const cite = (entry: any) => {
    const text = topicCandidateReaderText(entry?.text)
    const refs = [...new Set<number>((entry?.evidence_ids || []).map((id: string) => citationMap[id]).filter((number: unknown): number is number => typeof number === 'number' && Number.isSafeInteger(number) && number > 0))]
    return text && refs.length ? `${text} ${refs.map((number: number) => `[${number}]`).join(' ')}` : text
  }
  const fields = topicCandidateFields(candidate, original, sections)
  const field = (...labels: string[]) => topicCandidateReaderText(fields.find((item: any) => labels.includes(item.label))?.value || '')
  const design = field('研究内容', '研究设计')
  const pick = (label: string) => design.match(new RegExp(`(?:^|[；;\\n])\\s*${label}：([^；;\\n]+)`))?.[1]?.trim() || ''
  const legacyEvidence = field('文献依据', '证据依据').replace(/(?:；|、)?\s*证据范围：[^；。]*/g, '').replace(/(?:；|、)?\s*全文摘录：[^。]*/g, '')
  const diagnostic = /(?:关联主题\s*[TG]\d+|空白\s*G\d+|尚未证明|尚不证明候选假设|摘要或元数据|仅有摘要)/i
  const safeLegacyEvidence = legacyEvidence
    .split(/(?<=[；;。])|(?=关联主题|空白\s*G\d+|尚未证明|尚不证明候选假设|摘要或元数据|仅有摘要)/)
    .filter((part: string) => part.trim() && !diagnostic.test(part))
    .join('')
    .replace(/[；，、]\s*[；，、]/g, '；')
    .trim()
  const saved = candidate.reader_content && typeof candidate.reader_content === 'object' ? candidate.reader_content : {}
  const savedResearch = saved.research_content && typeof saved.research_content === 'object' ? saved.research_content : {}
  const object = cite(savedResearch.research_object) || pick('对象') || topicCandidateReaderText(candidate.research_object)
  const core = cite(savedResearch.core_question) || pick('核心问题') || pick('问题') || topicCandidateReaderText(candidate.research_question) || topicCandidateReaderText(candidate.hypothesis)
  const method = cite(savedResearch.method_route) || pick('方法') || pick('方法路线') || topicCandidateReaderText(candidate.method_route)
  const reason = cite(saved.reason) || field('选题理由', '核心理由') || topicCandidateReaderText(candidate.innovation) || topicCandidateReaderText(candidate.distinctiveness_rationale)
  const evidenceSummary = cite(saved.evidence_summary) || safeLegacyEvidence
  return {
    reason,
    researchContent: [
      { label: '研究对象', value: object },
      { label: '核心问题', value: core },
      { label: '方法路线', value: method },
    ].filter(item => item.value),
    evidenceSummary,
  }
}
export function topicThemeNodes(tree: Record<string, any>): any[] {
  const seen = new Set<string>()
  return (tree.nodes || []).filter((node: any) => {
    if (!node.theme_id || node.theme_id === 'root' || seen.has(node.theme_id)) return false
    seen.add(node.theme_id); return true
  })
}

export function uniqueTopicEvidence(items: Record<string, any>[]): Record<string, any>[] {
  return [...new Map(items.filter(item => item?.evidence_id).map(item => [item.evidence_id, item])).values()]
}

/** Use published citation numbers; search order and candidate order are unrelated to them. */
export function topicCitationSources(state: Record<string, any>): Record<number, Record<string, any>> {
  const plan = state.report_plan || {}
  const result: Record<number, Record<string, any>> = {}
  const evidence = new Map<string, Record<string, any>>((state.evidence_pool || []).map((item: any) => [item.evidence_id, item]))
  const references = (plan.sections || []).flatMap((section: any) => section.blocks || [])
    .filter((block: any) => block.kind === 'reference_list').flatMap((block: any) => block.items || [])
  const entries = Object.entries(plan.citation_map || {})
  for (const [id, value] of entries) {
    const number = Number(value)
    const item = evidence.get(id) || references.find((reference: any) => reference.evidence_id === id)
    if (Number.isSafeInteger(number) && number > 0 && item) {
      result[number] = { ...item, url: plan.citation_links?.[id] || item.url }
    }
  }
  if (!entries.length) {
    for (const item of references) {
      const number = Number(item.number)
      if (Number.isSafeInteger(number) && number > 0) result[number] = item
    }
    if (!references.length) {
      for (const [index, item] of (state.evidence_pool || []).entries()) {
        result[index + 1] = { ...item, url: item.url || item.nstl_url || item.source_url }
      }
    }
  }
  return result
}

export function citationTextParts(value: unknown): Array<{ text: string; number?: number }> {
  return String(value ?? '').split(/(\[\s*\d+(?:[\s,，、;；]+\d+)*\s*\])/g).filter(Boolean).flatMap(part => {
    if (!/^\[\s*\d/.test(part)) return [{ text: part }]
    if (/^\[\s*\d+\s*\]$/.test(part)) return [{ text: part, number: Number(part.replace(/\D/g, '')) }]
    return part.split(/(\d+)/g).filter(Boolean).map(text => /^\d+$/.test(text) ? { text, number: Number(text) } : { text })
  })
}
