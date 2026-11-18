import { researchPointName } from './researchPresentation'

const focusLabels: Record<string, string> = {
  system_prior_art: '相近技术路线和已有实现',
  mechanism_method: '关键原理、作用机制和研究方法',
  metric_baseline: '技术指标、公开基线和测量方法',
  application_validation: '应用场景、验证条件和测试结果',
  industry_market_competitive: '产业应用、市场情况和同类方案',
  industry_standard_regulatory: '相关标准、规范和监管要求',
  point_supplement: '该研究要点对应的补充文献',
}

const languageLabels: Record<string, string> = { zh: '中文', en: '英文' }

export function queryPlanPresentation(query: Record<string, any>, claims: Array<Record<string, any>> = []) {
  const claimIds = new Set([...(query.claim_ids || []), query.claim_id].filter(Boolean).map(String))
  const claim = claims.find(item => claimIds.has(String(item.claim_id)))
  const topic = String(query.reader_topic || (claim ? researchPointName(claim) : '')).trim() || '本次研究要点'
  const focus = String(query.reader_focus || focusLabels[query.query_role] || '相关研究和验证依据')
  const years = Array.isArray(query.year_range) ? query.year_range.filter(Boolean) : []
  const languages = (query.languages || query.filters?.languages || []).map((item: string) => languageLabels[item] || item).filter(Boolean)
  const sources = (query.source_scope || []).filter(Boolean)
  const scope = String(query.reader_scope || [
    languages.length ? `语种：${[...new Set(languages)].join('、')}` : '',
    years.length >= 2 ? `年份：${years[0]}—${years[1]}` : '年份：未限定',
    sources.length ? `来源：${sources.join('、')}` : '',
  ].filter(Boolean).join('；'))
  const result = String(query.reader_result || (query.selected_count > 0 ? `本组筛选出 ${query.selected_count} 篇文献进入后续分析` : '本组未记录筛选数量'))
  return { topic, summary: String(query.reader_summary || `围绕“${topic}”，查找${focus}相关研究。`), focus, scope, result }
}
