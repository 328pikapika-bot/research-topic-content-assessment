/** Project published results without changing the live execution state. */
type ResultState = Record<string, any>
function overlay(state: ResultState, values: ResultState, keys: string[]) {
  const result = { ...state }
  for (const key of keys) if (Object.prototype.hasOwnProperty.call(values, key)) result[key] = values[key]
  return result
}
export function topicResultState<T extends ResultState>(state: T): T {
  const snapshot = state.report_snapshot
  if (!snapshot) return state
  const result = overlay(state, snapshot.result_data_snapshot || {}, [
    'input_profile', 'constraint_card', 'term_map', 'theme_tree', 'evidence_pool',
    'evidence_chunks', 'landscape_snapshot', 'future_work_signals', 'crosslinks',
    'gaps', 'candidates', 'score_breakdowns', 'stage_artifacts',
  ])
  if (snapshot.report_plan_snapshot && Object.keys(snapshot.report_plan_snapshot).length) {
    result.report_plan = snapshot.report_plan_snapshot
    result.versions = { ...state.versions, report_version: result.report_plan.report_version,
      template_version: result.report_plan.template_version }
  }
  return result as T
}
export function assessmentResultState<T extends ResultState>(state: T): T {
  const snapshot = state.report_snapshot
  if (!snapshot) return state
  const values = { ...snapshot.render_context, ...snapshot.audit_data, ...snapshot.chart_data_snapshot }
  const result = overlay(state, values, [
    'assessment_profile', 'boundary_matrix', 'nine_fields', 'report_preferences',
    'quality_gate', 'report_metrics', 'versions',
    'claims', 'indicators', 'indicator_validations', 'evidence_records', 'evidence_chunks',
    'claim_evidence_links', 'claim_assessments', 'novelty_decisions', 'advancedness_decisions',
    'query_plans', 'guideline_requirements', 'work_packages', 'trend_analysis', 'scorecard',
    'material_observations',
    'market_data_cards', 'competitive_data_cards', 'regulatory_data_cards', 'report_plan',
  ])
  if (snapshot.sections?.length) result.report_plan = { ...result.report_plan,
    section_specs: snapshot.sections, sections: snapshot.sections }
  // Render counts are verified after freezing; they are not research content.
  const verified = state.report_metrics
  if (verified?.page_count_verified) {
    result.report_metrics = { ...result.report_metrics }
    for (const key of ['actual_page_count', 'page_count_verified', 'page_count_source',
      'page_target_status', 'page_target_reason']) result.report_metrics[key] = verified[key]
    result.report_plan = { ...result.report_plan, actual_page_count: verified.actual_page_count }
  }
  return result as T
}
export interface TrendSeries { label: string; points: Array<{ year: number; count: number }> }
export function topicTrendSeries(state: ResultState): TrendSeries[] {
  const spec = state.report_plan?.chart_specs?.find((item: ResultState) => item.key === 'trend_comparison')
  if (spec) return (spec.data?.series || []).map((series: ResultState) => ({ label: series.label,
    points: (spec.data.years || []).map((year: number, index: number) => ({ year, count: Number(series.values[index] || 0) })) }))
  const points = state.landscape_snapshot?.year_counts || []
  return points.length ? [{ label: '检索样本', points }] : []
}
export function assessmentTrendSeries(state: ResultState): TrendSeries[] {
  const entry = state.report_snapshot?.chart_manifest?.find((item: ResultState) => item.chart_key === 'years')
  if (entry) {
    // Coverage counts are not annual counts, and multi-theme counts overlap.
    if (!entry.included || entry.render_mode !== 'primary') return []
    return Object.entries(entry.chart_data || {}).map(([label, points]) => ({ label,
      points: (points as number[][]).map(([year, count]) => ({ year, count })) }))
  }
  const counts = new Map<number, number>()
  for (const item of state.evidence_records || []) {
    if (item.source_type === 'literature' && Number(item.year) > 0) counts.set(Number(item.year), (counts.get(Number(item.year)) || 0) + 1)
  }
  return counts.size ? [{ label: '检索样本', points: [...counts].sort(([a], [b]) => a - b).map(([year, count]) => ({ year, count })) }] : []
}
