import type { Claim, ClaimEvidenceLink, EvidenceRecord, NoveltyDecision } from './contentAssessmentApi'

export function evidenceSourceLabel(value?: string): string {
  const source = String(value || '').trim()
  const labels: Record<string, string> = {
    literature: '科学文献', paper: '科学文献', article: '科学文献', journal: '期刊文献',
    conference: '会议文献', patent: '专利文献', standard: '标准规范', guideline: '指南与规范',
    report: '研究报告', technical_report: '技术报告', web: '公开网页', website: '公开网页',
    nstl: 'NSTL 文献', europe_pmc: '公开学术文献', europepmc: '公开学术文献',
    project: '项目材料', project_material: '项目材料', user_material: '用户输入材料',
    uploaded_file: '上传材料', material: '上传材料', baseline: '对照材料', reference: '参考材料',
    evidence: '证据材料', regulation: '法规政策', regulatory: '法规政策', dataset: '数据集',
  }
  return labels[source.toLowerCase().replace(/[-\s]+/g, '_')] || (/^[\u3400-\u9fff、与（）()]+$/.test(source) ? source : '其他来源')
}

export interface ClaimEvidencePresentation {
  status: 'direct' | 'partial' | 'related' | 'unmatched' | 'conflicting'
  label: string
  tone: 'success' | 'info' | 'warning' | 'danger'
  icon: string
  summary: string
  verification: string
  evidenceIds: string[]
}

export function buildClaimEvidencePresentation(
  claim: Claim,
  links: ClaimEvidenceLink[],
  evidence: EvidenceRecord[],
  decision?: NoveltyDecision,
): ClaimEvidencePresentation {
  const records = new Map(evidence.filter(item => !['project', 'project_material', 'user_material', 'material', 'uploaded_file'].includes(item.source_type?.toLowerCase())).map(item => [item.evidence_id, item]))
  const relatedLinks = links.filter(link => link.claim_id === claim.claim_id && records.has(link.evidence_id))
  const hasContent = (id: string) => {
    const record = records.get(id)
    return record?.evidence_level !== 'C' && Boolean(record?.abstract?.trim() || record?.excerpt?.trim())
  }
  const support = relatedLinks.filter(link => ['supports', 'support', 'partially_supports', 'partial'].includes(link.relation) && hasContent(link.evidence_id))
  const direct = support.filter(link => {
    const record = records.get(link.evidence_id)!
    return ['supports', 'support'].includes(link.relation) && record.evidence_level === 'A' && record.anchor_verified && record.fulltext_available
  })
  const counter = relatedLinks.filter(link => ['contradicts', 'contradict'].includes(link.relation) && hasContent(link.evidence_id))
  const subjectMismatch = (decision?.risk_notes || []).some(note => /(?:证据|文献).{0,30}(?:主张|主题).{0,15}(?:不匹配|不同|不相关)/.test(note))
  const bareTerm = claim.text.trim().length < 12 && !/构建|开发|实现|提出|验证|制备|检测|提高|降低|优化|研究|建立|评估/.test(claim.text)
  const ids = (items: ClaimEvidenceLink[]) => [...new Set(items.map(item => item.evidence_id))]
  const fallback = [...new Set([
    ...relatedLinks.filter(link => !['insufficient_evidence', 'uncertain'].includes(link.relation)).map(link => link.evidence_id),
    ...(decision?.evidence_ids || []).filter(id => records.has(id)),
  ])]
  const verification = claim.assertion_type === 'target' ? '项目目标待实测验证'
    : claim.assertion_type === 'planned' ? '研究计划待开展验证'
      : claim.assertion_type === 'completed' ? '材料声明已有结果，达标情况按实测证据核验' : '材料未明确项目实测状态'
  if (counter.length) return { status: 'conflicting', label: '存在反证，需复核', tone: 'danger', icon: 'error_outline', summary: '关联文献存在与本主张相反的结果，需结合对象、条件和测试口径进一步核验。', verification, evidenceIds: [...new Set([...ids(counter), ...ids(support)])] }
  if (direct.length) return { status: 'direct', label: '已有直接文献支持', tone: 'success', icon: 'check_circle_outline', summary: `已找到 ${ids(direct).length} 篇有原文定位的支持文献。文献支持相关技术事实，项目目标是否达标另行核验。`, verification, evidenceIds: ids(support) }
  if (support.length && !subjectMismatch && !bareTerm) return { status: 'partial', label: '相关方法与研究依据', tone: 'info', icon: 'library_books', summary: `已关联 ${ids(support).length} 篇文献，为研究方向或技术路线提供依据；具体指标与适用条件需结合项目验证。`, verification, evidenceIds: ids(support) }
  if (fallback.length) return { status: 'related', label: bareTerm ? claim.claim_type === 'application' ? '应用场景待具体化' : '技术要素待具体化' : '已有相近或背景文献', tone: 'info', icon: 'info_outline', summary: bareTerm ? `此项目前是${claim.claim_type === 'application' ? '应用场景' : '技术'}名称，需结合研究对象、方法与验证条件形成可检验主张；关联文献仅作为背景资料。` : '已找到相近或背景资料，当前尚未形成该具体主张的支持结论。可展开查看对应关系与验证说明。', verification, evidenceIds: fallback }
  return { status: 'unmatched', label: '暂缺可用支持证据', tone: 'warning', icon: 'search', summary: '本轮尚未找到可用于支持该主张的文献；这表示证据有待补充，不能据此认定方案不可行。', verification, evidenceIds: [] }
}
