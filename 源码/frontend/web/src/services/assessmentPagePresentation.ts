import { comparisonReaderText } from './comparisonEvidencePresentation'

/** Retrieval results are independent of the smaller report citation selection. */
export function retrievalLiteratureForPoint<T extends { evidence_id: string; claim_ids?: string[]; source_type?: string }>(
  state: Record<string, any>, claimId: string, records: T[],
): T[] {
  const links = state.claim_evidence_links || []
  const pool = records.filter(item => (!item.source_type || item.source_type === 'literature')
    && (item.claim_ids?.includes(claimId) || links.some((link: any) => link.claim_id === claimId && link.evidence_id === item.evidence_id)))
  const saved = state.report_snapshot ? state.report_snapshot.audit_data?.retrieval_source_order
    : state.output_json?.retrieval_source_order
  const order: string[] = saved?.[claimId] || []
  const rank = new Map(order.map((id, index) => [id, index]))
  return pool.map((item, index) => ({ item, index }))
    .sort((a, b) => (rank.get(a.item.evidence_id) ?? order.length) - (rank.get(b.item.evidence_id) ?? order.length) || a.index - b.index)
    .slice(0, 30).map(row => row.item)
}

/** Read frozen synthesis only for published tasks; never regenerate history. */
export function relatedResearchAnalysis(state: Record<string, any>, claimId: string) {
  const records = state.report_snapshot ? state.report_snapshot.audit_data?.reader_research_analyses
    : state.output_json?.reader_research_analyses
  const record = records?.[claimId]
  if (record?.claim_id !== claimId) return { material: '', literature: [] as string[], evidenceIds: [] as string[] }
  const paragraphs = (Array.isArray(record.literature_summary) ? record.literature_summary : [])
    .filter((row: any) => row.evidence_ids?.length && row.anchors?.length && comparisonReaderText(row.text))
  return {
    material: comparisonReaderText(record.material_summary?.text),
    literature: paragraphs.map((row: any) => comparisonReaderText(row.text)) as string[],
    evidenceIds: [...new Set<string>(paragraphs.flatMap((row: any) => row.evidence_ids))],
  }
}

/** A saved, grounded comparison remains readable even when not cited in the report. */
export function comparisonLiteratureLinks(state: Record<string, any>, claimId: string): Array<Record<string, any>> {
  const snapshot = state.report_snapshot
  const saved = snapshot ? snapshot.audit_data : state.output_json
  const selected = saved?.report_source_selection?.[claimId]
  const graph = snapshot?.chart_manifest?.find((entry: any) => entry.chart_key === 'claim_evidence_network' && entry.included)?.chart_data?.links
  const synthesisIds = new Set(relatedResearchAnalysis(state, claimId).evidenceIds)
  return (state.claim_evidence_links || []).filter((link: any) => {
    if (link.claim_id !== claimId) return false
    const reader = saved?.reader_comparisons?.[claimId]?.[link.evidence_id]
    const grounded = reader?.claim_id === claimId && reader.evidence_id === link.evidence_id
    return grounded || synthesisIds.has(link.evidence_id) || ((!Array.isArray(selected) || selected.includes(link.evidence_id))
      && (!Array.isArray(graph) || graph.some((edge: any) => edge.source === claimId && edge.target === link.evidence_id)))
  })
}

/** Presentation helpers never rewrite saved research content or infer citation numbers. */
export function analysisParagraphs(value: unknown): { lead: string; rest: string } {
  const text = String(value || '').trim()
  // Keep a complete saved paragraph. A single-paragraph analysis stays intact.
  const boundary = text.search(/\r?\n[\t ]*(?:\r?\n)*/)
  return boundary < 0 ? { lead: text, rest: '' }
    : { lead: text.slice(0, boundary), rest: text.slice(boundary).trim() }
}

/** Only a claim's actual uploaded file offers a useful material locator. */
export function hasUploadedClaimSource(claim: { source_kind?: string; source_document_id?: string }, files: Array<{ file_id?: string }> = []): boolean {
  return claim.source_kind !== 'evidence' && Boolean(claim.source_document_id
    && claim.source_document_id !== 'user_material' && files.some(file => file.file_id === claim.source_document_id))
}

/** Published dimensions and sources come from the same paragraphs as the report. */
function frozenJudgementRows(snapshot: Record<string, any>, claimId: string): Array<Record<string, any>> | undefined {
  const section = snapshot.sections?.find((item: any) => (item.section_key || item.key) === 'judgement')
  if (!Array.isArray(section?.blocks)) return undefined
  let dimension = ''
  const rows: Array<Record<string, any>> = []
  for (const block of section.blocks) {
    if (block.kind === 'heading') {
      // Only chapter subheadings establish a dimension; claim headings keep it.
      if (!block.claim_id) {
        const heading = String(block.text || '').replace(/^[\d.、\s]+/, '')
        if (/^创新性(?:评[估价]|分析)?$/.test(heading)) dimension = 'innovation'
        else if (/^前沿性(?:评[估价]|分析)?$/.test(heading)) dimension = 'frontier'
        else if (/^先进性(?:评[估价]|分析)?$/.test(heading)) dimension = 'advancedness'
        else dimension = ''
      }
      continue
    }
    if (block.claim_id === claimId && ['paragraph', 'callout'].includes(block.kind) && block.text?.trim()) {
      if (block.dimension || dimension) rows.push({ ...block, dimension: block.dimension || dimension })
    }
  }
  return rows
}

export function researchEvaluations(state: Record<string, any>, claimId: string) {
  const saved = state.output_json?.reader_point_evaluations?.[claimId]
  const drafted = state.output_json?.scholarly_drafts?.[claimId]?.judgement || []
  const savedDimensions = new Set((Array.isArray(saved) ? saved : []).map((row: any) => row.dimension).filter(Boolean))
  const runtimeRows = Array.isArray(saved) && saved.length
    ? [...saved, ...drafted.filter((row: any) => !savedDimensions.has(row.dimension))]
    : drafted
  const rows = state.report_snapshot
    ? frozenJudgementRows(state.report_snapshot, claimId)
      ?? state.report_snapshot.audit_data?.reader_point_evaluations?.[claimId] ?? []
    : runtimeRows
  return [ ['innovation', '创新性'], ['frontier', '前沿性'], ['advancedness', '先进性'] ].flatMap(([dimension, label]) => {
    const paragraphs = rows.filter((row: any) => row.dimension === dimension || (!row.dimension && dimension === 'innovation'))
      .map((row: any) => ({ ...row, text: String(row.text || '').split(/\n\s*\n/).map(comparisonReaderText).filter(Boolean).join('\n\n') }))
      .filter((row: any) => row.text)
    if (!paragraphs.length) return []
    return [{ label, lead: paragraphs.slice(0, 2).map((row: any) => row.text).join('\n\n'),
      rest: paragraphs.slice(2).map((row: any) => row.text).join('\n\n'),
      evidenceIds: [...new Set<string>(paragraphs.flatMap((row: any) => row.evidence_ids || []))] }]
  })
}

export function assessmentCitationSources<T extends { evidence_id: string }>(
  numbers: Record<string, number> | undefined, records: T[],
): Record<number, T> {
  const result: Record<number, T> = {}
  const entries = Object.entries(numbers || {}).filter(([, number]) => Number.isInteger(number) && number > 0)
  for (const [id, number] of entries) {
    // A malformed duplicate mapping is ambiguous: keep the original citation text.
    if (entries.filter(([, n]) => n === number).length !== 1) continue
    const record = records.find(item => item.evidence_id === id)
    if (record) result[number] = record
  }
  return result
}

export function assessmentTaskTitle(profileTitle: unknown, taskTitle: unknown, raw: unknown, documents: Array<{ name?: string; role?: string }> = []): string {
  const clean = (value: unknown) => String(value || '').trim().replace(/^(?:论文|项目|指南)(?:名称|题目)[：:]\s*/, '').trim()
  const generic = /^(?:(?:我(?:要|想)?|本次|本次我要)?评估(?:的(?:是|对象))?\s*[：:]?\s*)?(?:论文|项目|指南)(?:[。.!！\s]*)$/
  const valid = (value: string) => Boolean(value && !generic.test(value) && !/^(?:创新性评估任务|评估任务|新建评估|待识别)$/.test(value))
  for (const candidate of [profileTitle, taskTitle]) {
    const title = clean(candidate)
    if (valid(title) && !title.includes('\n')) return title
  }
  const named = String(raw || '').match(/(?:^|\n)\s*(?:(?:论文|项目|指南)(?:名称|题目|标题)|材料名称|题目|标题)\s*[：:]\s*([^\r\n]+)/)?.[1]?.trim()
  if (named && valid(named)) return named
  const primary = documents.find(doc => /^(?:primary|main|main_material|project|主材料|正文)$/i.test(doc.role || ''))
    || (documents.length === 1 && !documents[0].role ? documents[0] : undefined)
  const filename = clean(primary?.name?.replace(/\.(?:pdf|docx?|txt|md|rtf)$/i, ''))
  return valid(filename) ? filename : '创新性评估任务'
}
