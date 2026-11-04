import type { ClaimEvidenceLink, NoveltyDecision } from './contentAssessmentApi'
import type { EvidenceItem } from './contentAssessmentUiApi'

/** Read published analysis without substituting live audit decisions. */
export function frozenPointResult(snapshot: any, claimId: string, judgement = false, separator = ' '): string {
  const paragraphs = (snapshot?.sections || [])
    .filter((section: any) => ['comparison', 'comparison_novelty'].includes(section.section_key))
    .flatMap((section: any) => section.blocks || [])
    .filter((block: any) => block.claim_id === claimId && block.kind === 'paragraph')
  const saved = snapshot?.audit_data?.reader_point_summaries?.[claimId]
  const rows = judgement ? saved || paragraphs.slice(-2) : paragraphs.slice(0, 2)
  return rows.map((block: any) => {
    const refs = (block.evidence_ids || []).map((id: string) => snapshot?.citation_numbers?.[id]).filter(Boolean)
    return `${block.text}${[...new Set(refs)].map(n => ` [${n}]`).join('')}`
  }).join(separator)
}

export function retrievalInfo(item: EvidenceItem): { label: string; value: string }[] {
  const entries: { label: string; value: string }[] = []
  const topic = item.query_group?.trim()
  if (topic && !['综合检索文献', '首页推荐案例核验文献'].includes(topic)) entries.push({ label: '检索主题', value: topic })
  const query = item.retrieval_query?.trim()
  if (query && query !== '综合检索') entries.push({ label: '检索式', value: query })
  if (item.doi?.trim()) entries.push({ label: 'DOI', value: item.doi.trim() })
  return entries
}

export function decisionSources(
  decision: NoveltyDecision,
  records: EvidenceItem[],
  links: ClaimEvidenceLink[],
) {
  const ids = [...new Set(decision.evidence_ids || [])]
  return ids.flatMap((evidenceId, index) => {
    const record = records.find((item) => item.evidence_id === evidenceId)
    if (!record?.title) return []
    const link = links.find((item) => item.claim_id === decision.claim_id && item.evidence_id === evidenceId)
    const differences = [link?.difference, ...(link?.incomparability_reasons || [])]
      .map((value) => String(value || '').trim())
      .filter((value) => value && !/^缺少.+字段$/.test(value))
    if (ids.length === 1 && index === 0 && !link) return []
    return [{ evidenceId, record, relation: link?.relation || '', difference: [...new Set(differences)].join('；') }]
  })
}
