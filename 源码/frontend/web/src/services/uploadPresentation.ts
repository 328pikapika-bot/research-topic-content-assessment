export function isUploadedDocument(record: any): boolean {
  const id = String(record?.file_id || record?.document_id || record?.id || '')
  return Boolean(id && record?.name && !/^(user_material|M-INPUT)(?:$|[_-]|\d)/i.test(id))
}

export function materialSummary(document: any, state: any): string {
  if (document.error_message) return document.error_message
  const profile = state?.input_profile || {}
  const analysis = state?.stage_artifacts?.input_modeling?.analysis || {}
  const clean = (value: unknown, limit: number) => {
    let text = String(value || '').replace(/<[^>]*>/g, ' ').replace(/&#x20;|&nbsp;/gi, ' ')
    text = text.replace(/&amp;/gi, '&').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&quot;/gi, '"')
      .replace(/\s+/g, ' ').trim()
    if (text.length <= limit) return text
    const head = text.slice(0, limit)
    const boundary = Math.max(head.lastIndexOf('。'), head.lastIndexOf('；'), head.lastIndexOf('？'), head.lastIndexOf('！'))
    return `${boundary >= limit * 0.55 ? head.slice(0, boundary + 1) : head.slice(0, limit - 1).trim()}…`
  }
  const analyzed = document.summary || profile.material_summary || analysis.material_summary || analysis.summary
  if (analyzed) return `材料概括：${clean(analyzed, 240)}`

  const title = clean(profile.title || profile.research_object, 48)
  const question = clean(profile.research_question || state?.constraint_card?.boundary_text, 150)
  const terms = (state?.term_map || []).map((item: any) => clean(item.preferred, 24)).filter(Boolean).slice(0, 5)
  const parts = [title && `研究主题：${title}`, question && question !== title && `核心问题：${question}`,
    terms.length && `识别术语：${terms.join('、')}`].filter(Boolean)
  return parts.length ? `材料概括：${parts.join('。')}。` : document.parse_warnings?.join('；') || '该历史任务未保存独立的材料概括。'
}

export function uploadPresentation(state: any, records: any[] = []) {
  const uploads = Array.isArray(state?.uploaded_files) ? state.uploaded_files : null
  const context = Array.isArray(state?.uploaded_file_context?.files) ? state.uploaded_file_context.files : null
  const documents = records.filter(isUploadedDocument)
  const hasFiles = Boolean(uploads?.length || context?.length || documents.length)
  const known = hasFiles || uploads !== null || context !== null
  return { hasFiles, documents, message: hasFiles ? '' : known
    ? '您尚未上传文件，本次分析基于您输入的内容进行。'
    : '该历史任务未记录上传文件信息' }
}

export function associatedLiteratureCount(claim: any, links: any[], records: any[]): number {
  const ids = new Set([...(claim.evidenceRefs || []), ...links.filter(link => link.claim_id === claim.id).map(link => link.evidence_id)])
  const identities = records.filter(record => ids.has(record.evidence_id)).map(record =>
    String(record.doi || record.nstl_url || record.source_url || record.url || record.title || record.evidence_id).trim().toLowerCase().replace(/^https?:\/\/(?:dx\.)?doi\.org\/|^doi:\s*/, ''))
  return new Set(identities).size
}
