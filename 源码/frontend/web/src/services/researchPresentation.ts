/** Reader presentation only. Source quotations and frozen historical reports stay intact. */
export function researchText(value: unknown): string {
  return String(value || '').split(/(?<=[。！？；])|\n+/)
    .filter(part => !/结论强度|部分支持|(?:缺少|缺失|未配置|尚未配置).*(?:字段|锚点|服务|证据)|质量门|覆盖率将在|字段矩阵|内部评分|综合评分|字符锚点/.test(part))
    .join('').trim()
}

export function researchPointName(claim?: { display_title?: string; text?: string; claim_fields?: Record<string, string> }): string {
  if (!claim) return '研究要点'
  if (claim.display_title) return claim.display_title
  const materialName = String(claim.text || '').match(/^(?:本)?(?:论文|项目|指南)?(?:拟研究|拟开展|拟设置)([^，,。；;\n]{4,36})(?:[，,。；;]|$)/)?.[1]
  if (materialName) return materialName.trim()
  let name = String(claim.claim_fields?.method || claim.text || '')
    .replace(/^(?:本研究|本项目|项目|论文|拟|计划|将|采用|提出|建立|开展|研究|通过)+/, '')
    .split(/[。；;\n]|，(?=拟|并|以|比较|验证)/)[0]?.trim() || '研究方法与验证设计'
  if (name.length > 30) {
    const method = name.match(/^[A-Za-z][A-Za-z0-9-]*/)?.[0]
    name = method ? `${method}方法与应用研究` : String(claim.text || '').split(/[，,；;。]/)[0]
      .replace(/^(?:论文|项目|指南)?(?:拟研究|拟开展|拟设置)/, '')
  }
  return name || '研究方法与验证设计'
}

/** Order live search results by the point's methods; keep all records and their source content. */
export function pointLiteratureOrder<T extends { title?: string; abstract?: string }>(
  records: T[], claim: { text?: string; claim_fields?: Record<string, string> },
): T[] {
  const terms = `${claim.claim_fields?.method || ''} ${claim.text || ''}`
    .match(/[A-Za-z][A-Za-z0-9-]{2,}/g) || []
  const ignored = new Set(['the', 'and', 'for', 'with', 'from', 'using', 'scientific', 'document', 'retrieval', 'learning', 'research'])
  const chinese = (`${claim.claim_fields?.method || ''} ${claim.text || ''}`.match(/[\u4e00-\u9fff]+/g) || [])
    .flatMap(word => Array.from({ length: Math.max(0, word.length - 1) }, (_, i) => word.slice(i, i + 2)))
  const keywords = [...new Set([...terms.map(t => t.toLowerCase()), ...chinese])].filter(t => !ignored.has(t))
  const score = (item: T) => keywords.reduce((sum, term) => {
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const match = new RegExp(/^[a-z]/i.test(term) ? '(?:^|[^a-z0-9])' + escaped + '(?:$|[^a-z0-9])' : escaped, 'i')
    return sum + (match.test(item.title || '') ? 4 : match.test(item.abstract || '') ? 1 : 0)
  }, 0)
  return records.map((item, index) => ({ item, index, rank: score(item) }))
    .sort((a, b) => b.rank - a.rank || a.index - b.index).map(row => row.item)
}
