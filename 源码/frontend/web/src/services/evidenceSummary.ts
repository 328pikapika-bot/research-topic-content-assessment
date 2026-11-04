import { researchText } from './researchPresentation'
import { resolveEvidenceUrl } from './evidenceLinks'

export interface EvidenceSummaryItem { key: string; title: string; relation: string; scope: string; source: string; byline: string; locator: string; excerpt: string; limitation: string; url: string }

export function evidenceSummaries(links: any[], records: any[], chunks: any[], ownerId: string): EvidenceSummaryItem[] {
  const byId = new Map(records.map(record => [record.evidence_id, record]))
  const seen = new Set<string>()
  const relations: Record<string, string> = { supports: '支持依据', partially_supports: '相关方法研究', contradicts: '存在反证', context: '背景资料', not_comparable: '适用条件不同', coverage_limit: '证据范围有限', insufficient_evidence: '暂缺直接依据' }
  return [...links].sort((a, b) => Number(b.relation === 'contradicts') - Number(a.relation === 'contradicts')).flatMap(link => {
    const record = byId.get(link.evidence_id)
    if (!record || seen.has(record.evidence_id)) return []
    seen.add(record.evidence_id)
    const allowed = link.chunk_ids || link.evidence_chunk_ids || []
    const chunk = chunks.find(c => c.evidence_id === record.evidence_id
      && (!allowed.length || allowed.includes(c.chunk_id))
      && (!c.claim_ids?.length || c.claim_ids.includes(ownerId))
      && (!c.theme_ids?.length || !link.theme_ids?.length || c.theme_ids.some((id: string) => link.theme_ids.includes(id))))
    const fulltext = Boolean(record.fulltext_available && record.anchor_verified && chunk
      && (chunk.reopenable !== false) && chunk.evidence_level !== 'B')
    const location = chunk?.locator
    const locator = typeof location === 'string' ? location : location?.display || chunk?.section_path || chunk?.section_ref || record.paragraph_ref || ''
    return [{ key: record.evidence_id, title: record.title, relation: relations[link.relation] || '背景资料',
      scope: fulltext ? '正文依据' : record.abstract ? '摘要依据' : '文献线索',
      source: record.source_kind === 'fulltext_library' || record.retrieval_source === 'fulltext_library' ? '全文库补充来源' : '文献检索来源',
      byline: [...(record.authors || []).slice(0, 2), record.year].filter(Boolean).join('，'), locator,
      excerpt: String(fulltext ? chunk.text || chunk.excerpt || '' : record.abstract || '').slice(0, 900),
      limitation: researchText(link.uncertainty || link.rationale),
      url: resolveEvidenceUrl(record) }]
  }).slice(0, 5)
}
