const keys = ['input_modeling', 'theme_decomposition', 'retrieval_evidence', 'landscape_trend', 'crosslink_gap', 'candidate_generation', 'scoring_ranking', 'quality_gate', 'report_publish']
const labels = ['交通污染暴露与儿童呼吸健康的剂量响应边界', '城市绿地可达性与老年人心理健康', '遥感热环境与居民活动暴露的时空差异', '环境暴露监测与社区干预的指标衔接']
const papers = [{ evidence_id: 'E1', title: '城市交通污染与不同年龄居民健康关联的队列研究', abstract: '研究比较交通污染暴露、社区绿地和热环境对不同年龄居民健康指标的影响；观察性关联不构成因果证据。', year: 2024, url: 'https://www.nstl.gov.cn/paper_detail.html?id=qa-paper', source_kind: 'nstl' }]
export const detail: any = {
  id: 'reader-qa', task_id: 'reader-qa', title: '城市人居环境与居民健康风险关联性研究：多源暴露测量、不同年龄人群与社区干预验证边界', raw_user_input: '比较交通污染、绿地与热环境的健康风险关联，明确研究对象、验证边界和可复现指标。', status: 'completed', completed_steps: 9, current_stage: 'theme_decomposition', updated_at: '2026-10-09T10:00:00+08:00',
  state: {
    current_stage: 'report_publish', input_profile: { research_object: '不同年龄社区居民', research_question: '不同环境暴露的健康风险如何比较', documents: [] }, constraint_card: { boundary_text: '城市社区，儿童与老年人，观察性关联与干预验证分开解释。' }, versions: { report_version: 'qa-v1' }, evidence_pool: papers,
    theme_tree: { nodes: labels.map((label, i) => ({ theme_id: `T${i+1}`, label, boundary: `${label}：将暴露测量与健康结局配对，在社区和年龄边界内验证。`, research_object: i === 0 ? '学龄儿童' : '社区居民', research_question: '比较不同暴露水平下的健康差异，并控制混杂因素', method_boundary: '队列随访与空间暴露模型；不外推到未观测人群', application_scenario: '城市社区', seed_terms: [label], evidence_ids: ['E1'] })), edges: [{ source: 'T1', target: 'T4', relation: '将交通污染暴露指标用于社区干预前后比较，评价跨场景适用性。' }] },
    crosslinks: Array.from({ length: 7 }, (_, i) => ({ crosslink_id: `XL-${i+1}`, source_theme_id: `T${i%3+1}`, target_theme_id: 'T4', relation_type: 'shared_data', status: 'candidate', description: `比较第 ${i+1} 类测量条件下暴露指标与社区健康结局的对应关系。`, relation_basis: '现有文献共同涉及暴露测量和健康随访，但干预效果尚未被确认。', research_opportunity: '建立时间对齐的暴露与结局数据，进行独立社区对照验证。', support_evidence_ids: ['E1'], uncertainty: '样本来自观察性研究，不能直接解释为干预因果效应。' })),
    gaps: [{ gap_id: 'G1', label: '不同社区暴露指标的可迁移边界仍需验证', theme_ids: ['T1','T4'], crosslink_ids: ['XL-1'], evidence_ids: ['E1'], validation_status: 'candidate', rationale: '现有研究已测量风险关联，跨社区干预效果仍缺少对照验证。', uncertainty: '当前仅作为研究机会线索。' }],
    candidates: [{ candidate_id: 'C1', title: '交通污染与绿地共同暴露下学龄儿童呼吸症状的时间对齐队列研究及跨社区验证', recommendation_level: 'pending_evidence', research_question: '交通污染和绿地的交互关联是否随暴露窗口改变？', research_object: '城市学龄儿童', hypothesis: '不同暴露窗口对应不同关联强度', method_route: '队列随访、空间暴露建模与敏感性分析', sample_boundary: '两个城市社区，不外推成年样本', validation_path: '独立社区对照', evaluation_metrics: ['症状发生率', '关联强度置信区间'], evidence_ids: ['E1'], gap_ids: ['G1'], risks: ['观察性研究的混杂偏差'], risk_mitigation: ['预注册与负对照'] }],
    source_queries: [{ query_id: 'Q1', source_kind: 'nstl', status: 'completed', sanitized_query: '城市交通污染 儿童 呼吸健康', purpose: '核验具体环境暴露与健康结局的关系', returned_count: 1 }],
    evidence_chunks: [{ evidence_id: 'E1', doc_id: 'internal-doc', chunk_id: 'internal-chunk', section_path: '结果与限制', text: '观察到不同暴露窗口与健康结局的关联，仍需在独立社区控制混杂因素。' }],
    landscape_snapshot: { sample_count: 1, limitations: [] }, report_plan: { sections: [] }, quality_gate: {},
    stage_trace: keys.map(stage_key => ({ stage_key, status: 'completed', progress_percent: 100, summary: '已完成', model_status: 'completed' })),
  },
}
export const history = [
  { ...detail, state: undefined, task_type: '智能选题', created_at: detail.updated_at },
  { id: 'assessment-qa', task_id: 'assessment-qa', task_type: '创新性评估', title: '社区干预方法创新性评估', status: 'completed', created_at: detail.updated_at, completed_steps: 9, total_steps: 9 },
  { id: 'active-qa', task_id: 'active-qa', task_type: '智能选题', title: '正在运行的任务必须保留', status: 'running', created_at: detail.updated_at, completed_steps: 3, total_steps: 9 },
]
export const getTopicSelectionTask = async () => structuredClone(detail)
export const subscribeToTopicTaskStream = async () => () => {}
export const downloadTopicReport = async () => {}
export const fetchTopicReportPdf = async () => { throw new Error('隔离验收不请求报告') }
export const fetchContentAssessmentReportPdf = fetchTopicReportPdf
export const rerunTopicCorrection = async () => {}
export const stopTopicSelectionTask = async () => {}
export const submitTopicConfirmation = async () => {}
export const deleteTopicSelectionTask = async () => {}
export const deleteContentAssessmentTask = async () => {}
export const listAllTasks = async () => ({ items: history, total: history.length, statistics: { total: history.length, statuses: { completed: 2, running: 1 }, agents: { topic: { total: 2 }, assessment: { total: 1 } } } })
export const previewHistoryCleanup = async () => ({ eligible_count: 2, skipped_count: 1 })
export const clearTaskHistory = async () => { throw new Error('验收环境禁止实际删除；仅测试确认弹窗和取消操作') }
export const getAuthManager = () => ({ isAuthenticated: () => true, showLoginModal: () => {} })
