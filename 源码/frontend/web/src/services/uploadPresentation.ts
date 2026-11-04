export function isUploadedDocument(record: any): boolean {
  const id = String(record?.file_id || record?.document_id || record?.id || '')
  return Boolean(id && record?.name && !/^(user_material|M-INPUT)(?:$|[_-]|\d)/i.test(id))
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
