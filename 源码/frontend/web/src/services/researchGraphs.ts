import { researchPointName } from './researchPresentation'
export const graphColors = ['#5269c7', '#228579', '#b77928', '#9060ac', '#b75465', '#437f9d']
export interface GraphNode { id: string; label: string; subtitle?: string; x: number; y: number; width: number; height: number; color?: string; kind?: string }
export interface GraphEdge { id: string; source: string; target: string; color: string; dashed?: boolean; active?: boolean }
export function graphPath(a: GraphNode, b: GraphNode) {
  const forward = b.x >= a.x
  const sx = forward ? a.x + a.width : a.x, tx = forward ? b.x : b.x + b.width
  const sy = a.y + a.height / 2, ty = b.y + b.height / 2, mid = (sx + tx) / 2
  return Math.abs(sy - ty) < 1 ? `M ${sx} ${sy} L ${tx} ${ty}` : `M ${sx} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${tx} ${ty}`
}
export function participants(row: Record<string, any>) {
  const source = row.participating_disciplines?.length ? row.participating_disciplines : [
    { discipline: row.core || row.core_discipline, role: 'core' },
    { discipline: row.expansion || row.expansion_discipline, role: 'expansion' },
  ]
  const seen = new Set<string>()
  return source.filter((p: any) => {
    const key = String(p.discipline || '').trim()
    if (!key || key === '学科待核验' || seen.has(key)) return false
    seen.add(key); return true
  })
}
export function safeThemeHierarchy(tree: Record<string, any>) {
  const nodes: any[] = tree.nodes || [], known = new Map(nodes.map(n => [n.theme_id, n]))
  return nodes.map(node => {
    let current = node, depth = 1
    const seen = new Set([node.theme_id])
    while (current.parent_id && current.parent_id !== 'root') {
      if (!known.has(current.parent_id) || seen.has(current.parent_id)) return { ...node, depth: 1, parent: '', uncertain: true }
      seen.add(current.parent_id); current = known.get(current.parent_id); depth++
    }
    return { ...node, depth, parent: node.parent_id && node.parent_id !== 'root' ? node.parent_id : 'root', uncertain: false }
  })
}
export function assessmentGraphData(state: Record<string, any>) {
  const frozen = state.report_snapshot?.chart_manifest?.find((m: any) => m.chart_key === 'claim_evidence_network' && m.included)?.chart_data
  if (frozen) return frozen
  const selection = state.report_snapshot ? state.report_snapshot.audit_data?.report_source_selection : state.output_json?.report_source_selection
  const records = new Map((state.evidence_records || []).map((e: any) => [e.evidence_id, e]))
  const points = new Set((state.claims || []).map((c: any) => c.claim_id))
  const links = (state.claim_evidence_links || []).filter((l: any) => points.has(l.claim_id) && records.has(l.evidence_id) &&
    (!selection || !Object.prototype.hasOwnProperty.call(selection, l.claim_id) || selection[l.claim_id].includes(l.evidence_id)))
  const ids = new Set(links.map((l: any) => l.evidence_id))
  return { points: (state.claims || []).map((c: any, i: number) => ({ id: c.claim_id, label: researchPointName(c), number: i + 1 })),
    evidence: (state.evidence_records || []).filter((e: any) => ids.has(e.evidence_id)).map((e: any) => ({ id: e.evidence_id, label: e.title, number: state.report_snapshot?.citation_numbers?.[e.evidence_id] })),
    links: links.map((l: any) => ({ source: l.claim_id, target: l.evidence_id, relation: l.relation })) }
}
