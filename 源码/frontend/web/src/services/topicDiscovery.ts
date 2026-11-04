export interface DiscoveryRow {
  crosslink_id: string; core: string; expansion: string; method: string; reason: string;
  opportunity: string; evidence_ids: string[]; grounded: boolean; association_kind: string;
  participating_disciplines?: Record<string, any>[]; number?: number; color?: string;
}
export function discoveryReadingText(value: string): string {
  return value.replace(/本次可读检索样本中的观察（不代表领域整体）[:：]?/g, '')
    .replace(/(?<![A-Za-z0-9])(?:E-[A-Za-z0-9_-]+|M-INPUT|CAND-\d+|FW-\d+)(?![A-Za-z0-9])/g, '')
    .replace(/(?:关联)?主题\s*[TG]\d+(?:\s*[、,，]\s*[TG]\d+)*/g, '')
    .replace(/空白\s*G\d+(?:\s*[、,，]\s*G\d+)*/g, '')
    .replace(/[（(][\s,，、;；]*[）)]/g, '').trim()
}
export function methodEvolutionRows(state: Record<string, any>): Record<string, any>[] {
  const frozen = state.report_plan?.chart_specs?.find((item: any) => item.key === 'discipline_network')?.data?.method_evolution
  const rows = Array.isArray(frozen) ? frozen : state.stage_artifacts?.landscape_trend?.analysis?.method_evolution || []
  return rows.filter((item: any) => item && typeof item.finding === 'string' && item.finding.trim()).map((item: any) => {
    let period = String(item.period || '').trim().toLowerCase()
    if (!period) {
      const text = String(item.label || item.finding).trim()
      period = /^(未来|后续|下一步|建议|拟开展|future|next\b)/i.test(text) ? 'future'
        : /^(早期|过去|传统|初期|中期|此前|early\b|past\b)/i.test(text) ? 'past' : 'present'
    }
    return { ...item, period }
  }).filter((item: any) => ['past', 'present', 'future'].includes(item.period))
}
function landscapeAnalysis(state: Record<string, any>): Record<string, any> {
  const frozen = state.report_snapshot?.result_data_snapshot?.stage_artifacts?.landscape_trend?.analysis
  return frozen || state.stage_artifacts?.landscape_trend?.analysis || {}
}
function savedSummary(state: Record<string, any>, field: string, fallbackPeriod: string) {
  const analysis = landscapeAnalysis(state)
  const saved = analysis[field]
  if (saved && typeof saved.text === 'string' && saved.text.trim()) {
    return { text: discoveryReadingText(saved.text), evidence_ids: saved.evidence_ids || [] }
  }
  const rows = methodEvolutionRows(state).filter(row => fallbackPeriod === 'method'
    ? row.period !== 'future' : row.period === 'future')
  return {
    text: rows.map(row => discoveryReadingText(String(row.finding || ''))).filter(Boolean).join('；'),
    evidence_ids: [...new Set(rows.flatMap(row => row.evidence_ids || []))],
  }
}
export function methodEvolutionSummary(state: Record<string, any>) {
  return savedSummary(state, 'method_evolution_summary', 'method')
}
export function futureResearchSummary(state: Record<string, any>) {
  return savedSummary(state, 'future_research_summary', 'future')
}
export function opportunityReading(link: Record<string, any>, nodes: Record<string, any>[]) {
  const support = new Set<string>(link.support_evidence_ids || [])
  const disciplines = Array.from(new Set((link.participating_disciplines || [])
    .filter((item: any) => (item.evidence_ids || []).some((id: string) => support.has(id)))
    .map((item: any) => String(item.discipline || '').trim()).filter(Boolean)))
  const themeNames = Array.from(new Set([link.source_theme_id, link.target_theme_id]
    .map((id: string) => nodes.find(node => node.theme_id === id)?.display_label || nodes.find(node => node.theme_id === id)?.label)
    .filter(Boolean)))
  const evidenceIds = Array.from(new Set([
    ...(link.support_evidence_ids || []),
    ...(link.method_evidence || []).filter((item: any) => item.verified).map((item: any) => item.evidence_id),
  ].filter(Boolean)))
  return {
    kind: disciplines.length > 1 ? '交叉研究方向' : '研究切入点',
    title: discoveryReadingText(String(link.research_opportunity || link.relation_basis || link.title || '')).trim(),
    themes: themeNames,
    method: discoveryReadingText(String(link.transferable_method || '')).trim(),
    clue: discoveryReadingText(String(link.relation_basis || link.description || '')).trim(),
    evidence_ids: evidenceIds,
  }
}
export function discoveryRows(state: Record<string, any>): DiscoveryRow[] {
  const spec = state.report_plan?.chart_specs?.find((item: any) => item.key === 'discipline_network')
  const rows = spec ? spec.data?.rows || [] : (state.crosslinks || []).map((link: any) => ({
    crosslink_id: link.crosslink_id, core: link.core_discipline, expansion: link.expansion_discipline,
    method: link.transferable_method, reason: link.relation_basis, opportunity: link.research_opportunity,
    evidence_ids: link.support_evidence_ids, method_evidence: link.method_evidence,
    status: link.status, association_kind: link.association_kind,
    participating_disciplines: link.participating_disciplines,
  }))
  return rows.map((row: any) => {
    const evidenceIds = new Set<string>(row.evidence_ids || row.support_evidence_ids || [])
    const participants = (row.participating_disciplines || []).filter((item: any) =>
      (item.evidence_ids || []).some((id: string) => evidenceIds.has(id)))
    return {
      ...row,
      core: participants.find((item: any) => item.role === 'core')?.discipline || '',
      expansion: participants.find((item: any) => item.role === 'expansion')?.discipline || '',
      method: row.method || '',
      evidence_ids: Array.from(evidenceIds),
      participating_disciplines: participants,
      grounded: !!row.method_evidence?.some((item: any) => item.verified),
      association_kind: row.association_kind || 'unverified',
    }
  })
}
export function themeBranches(tree: Record<string, any>) {
  const nodes = tree.nodes || []
  const known = new Set(nodes.map((node: any) => node.theme_id))
  const seen = new Set<string>()
  function branch(node: any): any {
    seen.add(node.theme_id)
    return { ...node, children: nodes.filter((child: any) => child.parent_id === node.theme_id && !seen.has(child.theme_id)).map(branch) }
  }
  const roots = nodes.filter((node: any) => !known.has(node.parent_id) || node.parent_id === node.theme_id).map(branch)
  for (const node of nodes) if (!seen.has(node.theme_id)) roots.push(branch(node))
  return roots
}
