<template>
  <section class="topic-page">
    <div v-if="loading && !detail" class="topic-empty"><span class="topic-spinner"></span><b>正在同步选题任务</b></div>
    <div v-else-if="error && !detail" class="topic-empty"><span class="material-icons-outlined">error_outline</span><h1>任务暂时无法打开</h1><p>{{ error }}</p><button class="primary" @click="load">重新加载</button></div>

    <template v-else-if="detail">
      <header class="task-head">
        <div><span class="eyebrow">智能选题任务</span><h1>{{ conciseDisplayTitle }}</h1></div>
        <div class="head-actions"><span :class="['status-pill', `is-${detail.status}`]">{{ recoveryLabel(recovery) || statusLabel(detail.status) }}</span><button v-if="isLive" class="stop" :disabled="stopping" @click="stopTask"><span class="material-icons-outlined">stop_circle</span>{{ stopping ? '正在停止' : '停止任务' }}</button></div>
      </header>

      <section class="task-context">
        <details class="original-question"><summary>查看研究边界</summary><p>{{ state?.constraint_card?.boundary_text || detail.raw_user_input }}</p></details>
        <dl><div><dt>证据范围</dt><dd>{{ timeRange }}</dd></div><div><dt>文献</dt><dd>{{ literatureCount }} 篇</dd></div></dl>
      </section>

      <section v-if="state?.parent_task_id || correctionChildId" class="version-links">
        <span class="material-icons-outlined">account_tree</span>
        <p v-if="state?.parent_task_id">本任务来源于补正或关联版本。</p><button v-if="state?.parent_task_id" @click="navigateTask(state.parent_task_id)">查看来源任务</button>
        <p v-if="correctionChildId">原任务已保留，后续分析在补正版中继续。</p><button v-if="correctionChildId" @click="navigateTask(correctionChildId)">打开补正版</button>
      </section>

      <nav class="stage-bar" :style="{ gridTemplateColumns: `repeat(${userSteps.length}, minmax(112px, 1fr))` }" :aria-label="`${userSteps.length}阶段选题流程`">
        <button v-for="(step, index) in userSteps" :key="step.key" :aria-current="selectedStep.key === step.key ? 'step' : undefined" :class="[`is-${step.status}`, { selected: selectedStep.key === step.key }]" @click="selectUserStep(step.key)">
          <span class="stage-number"><span v-if="step.status === 'completed'" class="material-icons-outlined">check</span><span v-else>{{ index + 1 }}</span></span>
          <span><b>{{ step.title }}</b><small>{{ stepStatusLabel(step.status) }}{{ stepDurationLabel(step) }}</small></span>
        </button>
      </nav>

      <StageRunNotice v-if="!recoveryPresentation" :notice="runNotice" />
      <section v-else class="recovery-notice" role="status" aria-live="polite"><strong>{{ recoveryPresentation.title }} · {{ recoveryStageTitle }}</strong><p>{{ recoveryPresentation.description }}</p><small v-if="recoveryPresentation.reference">{{ recoveryPresentation.reference }}</small></section>
      <section v-if="!isLive" class="run-strip">
        <div><span class="material-icons-outlined">{{ liveIcon }}</span><p><b>{{ liveTitle }}</b><small>{{ liveMessage }}</small></p></div>
        <dl><div><dt>步骤</dt><dd>{{ userSteps.findIndex(item => item.key === selectedStep.key) + 1 }}/{{ userSteps.length }}</dd></div><div><dt>总进度</dt><dd>{{ completionPercent }}%</dd></div></dl>
      </section>

      <main id="topic-stage-workbench" class="workbench">
        <header class="stage-head">
          <div><span>步骤 {{ userSteps.findIndex(item => item.key === selectedStep.key) + 1 }}</span><h2>{{ selectedStep.title }}</h2><p>{{ stepDescription(selectedStep.key) }}</p></div>
          <span :class="['stage-status', `is-${selectedStep.status}`]">{{ stepStatusLabel(selectedStep.status) }}{{ stepDurationLabel(selectedStep) }}</span>
        </header>
        <div class="stage-progress"><i :style="{ width: `${selectedStep.progress_percent}%` }"></i></div>
        <p class="stage-completion">阶段产物：{{ selectedStep.artifact }} · {{ selectedStep.status === 'completed' ? selectedStep.completion : `完成标准：${selectedStep.completion}` }}</p>



        <section v-if="detail.status === 'awaiting_confirmation'" class="panel confirmation-panel">
          <header><h3>任务需要处理</h3></header><p>{{ state?.confirmation?.prompt || state?.confirmation?.question || state?.confirmation?.reason }}</p>
          <label for="topic-confirmation-note">补充说明</label><textarea id="topic-confirmation-note" v-model="confirmationNote" rows="3"></textarea>
          <div class="confirmation-actions"><button v-for="option in state?.confirmation?.options || []" :key="option.value || option.id" class="primary" :disabled="actionPending" @click="confirmTask(option.value || option.id)">{{ option.label || option.title || option.value }}</button></div>
        </section>

        <section v-if="selectedStep.key === 'intent'" class="panel intent-summary"><h3>需求理解</h3><p>{{ state?.input_profile?.research_question || detail.raw_user_input }}</p><p><b>研究边界：</b>{{ state?.constraint_card?.boundary_text || '正在识别研究边界' }}</p><p><b>关键词：</b>{{ state?.term_map?.map((item: any) => item.preferred).filter(Boolean).slice(0, 8).join('、') || '正在提取' }}</p></section>
        <details v-if="selectedStep.key === 'intent'" class="panel material-results" :open="materialResultsOpen ?? false" @toggle="materialResultsOpen = ($event.target as HTMLDetailsElement).open">
          <summary>材料解析结果</summary>
          <p v-if="!uploadInfo.hasFiles">{{ uploadInfo.message }}</p>
          <article v-for="doc in uploadInfo.documents" :key="doc.document_id"><h4>{{ doc.name }}</h4><p>解析状态：{{ doc.status || '待处理' }}</p><p>{{ doc.excerpt || doc.error_message || doc.parse_warnings?.join('；') || '未保存摘录' }}</p></article>
          <p v-if="uploadInfo.hasFiles && !uploadInfo.documents.length">已上传文件，解析记录正在整理。</p>
        </details>


        <section v-if="selectedStep.key === 'themes'" class="panel readable-results">
          <header><div><span class="material-icons-outlined">account_tree</span><h3>研究主题与边界</h3></div><small>{{ themeNodes.length }} 个研究主题</small></header>
          <TopicThemeGraph :tree="presentationThemeTree"><template #detail="{ node }">
            <p>{{ node.boundary }}</p>
            <p v-if="node.discipline_tags?.length"><b>相关学科：</b>{{ node.discipline_tags.join('、') }}</p>
            <p v-if="node.seed_terms?.length"><b>检索关键词：</b>{{ node.seed_terms.join('、') }}</p>
          </template></TopicThemeGraph>
        </section>

        <section v-if="selectedStep.key === 'discovery'" class="panel readable-results discovery-panel">
          <header><div><span class="material-icons-outlined">hub</span><h3>学科与研究方向</h3></div></header>
          <h4>文献年份趋势</h4>
          <LiteratureTrendChart :series="frozenTrendSeries" />
          <section v-if="landscapeConclusion" class="trend-analysis"><h4>趋势分析</h4><p>{{ landscapeConclusion }}</p></section>
          <section v-if="methodSummary.text" class="method-evolution"><h4>方法演进</h4><p><InlineCitations :text="summaryWithCitations(methodSummary)" :sources="citationSources" /></p></section>
          <section v-if="futureSummary.text" class="method-evolution future-summary"><h4>后续研究方向</h4><p><InlineCitations :text="summaryWithCitations(futureSummary)" :sources="citationSources" /></p></section>
          <DisciplineRelationGraph v-if="graphRows.length" :root="state?.theme_tree?.display_root || state?.theme_tree?.root || conciseDisplayTitle" :rows="graphRows" :selected="selectedCrosslink" @select="selectedCrosslink = $event" />
          <p v-else-if="state?.stage_artifacts?.crosslink_gap?.generation_status !== 'no_supported_opportunities'" class="graph-recovery-note" role="status">研究方向分析正在整理，已完成的趋势总结和选题方向已保存。</p>
          <article v-for="link in opportunityCards" :key="link.crosslink_id" :class="{ 'selected-opportunity': selectedCrosslink === link.crosslink_id }">
            <h4><small>{{ opportunityReading(link).kind }}</small>{{ opportunityReading(link).title }}</h4>
            <p v-if="opportunityReading(link).themes.length"><b>关联主题：</b>{{ opportunityReading(link).themes.join('、') }}</p>
            <p v-if="opportunityReading(link).method"><b>可借鉴的方法：</b>{{ opportunityReading(link).method }}</p>
            <p v-if="opportunityReading(link).clue"><b>关键线索：</b><InlineCitations :text="summaryWithCitations({ text: opportunityReading(link).clue, evidence_ids: opportunityReading(link).evidence_ids })" :sources="citationSources" /></p>
          </article>
        </section>

        <section v-if="selectedStep.key === 'literature'" id="retrieval-queries" class="panel topic-retrieval-panel">
          <header><div><span class="material-icons-outlined">travel_explore</span><h3>相关文献</h3></div><small>{{ topicLiteratureGroups.length }} 个主题</small></header>
          <section v-for="(group, index) in topicLiteratureGroups" :key="group.id" class="literature-theme-group">
            <header class="literature-group-heading"><span class="group-number">{{ String(index + 1).padStart(2, '0') }}</span><div><h4>{{ group.title }}</h4><p>{{ group.items.length }} 篇相关文献<span v-if="group.items.length > 2"> · 优先呈现相关文献2篇</span></p></div></header>
            <div class="topic-literature-list"><LiteratureCard v-for="evidence in group.items.slice(0, 2)" :key="evidence.evidence_id" layout="row" :item="evidence" :chunks="state?.evidence_chunks || []" /></div>
            <details v-if="group.items.length > 2" class="more-literature"><summary><span class="more-literature-label">展开其余 {{ group.items.length - 2 }} 篇文献</span><span class="less-literature-label">收起其余文献</span><span class="material-icons-outlined" aria-hidden="true">expand_more</span></summary><div class="topic-literature-list"><LiteratureCard v-for="evidence in group.items.slice(2)" :key="evidence.evidence_id" layout="row" :item="evidence" :chunks="state?.evidence_chunks || []" /></div></details>
            <details v-if="themeSearchInfo(group.id).length" class="theme-query-records"><summary>查看检索信息</summary><p class="theme-final-count">本主题实际收录 {{ group.items.length }} 篇</p><div class="query-list"><article v-for="(query, queryIndex) in themeSearchInfo(group.id)" :key="`${group.id}-${queryIndex}`"><span>检索词</span><div><b>{{ query.term }}</b><p>来源：{{ query.sources.join('、') }} · 时间范围：{{ query.timeRange }}</p></div></article></div></details>
          </section>
          <details v-if="unassignedLiterature.length" class="source-disclosure"><summary>其他来源文献（{{ unassignedLiterature.length }}篇）</summary><LiteratureReferences :items="unassignedLiterature" /></details>
        </section>



        <section v-if="selectedStep.key === 'candidates'" class="panel candidate-panel">
          <header><div><span class="material-icons-outlined">lightbulb</span><h3>{{ selectedStep.artifact }}</h3></div><small>{{ candidates.length }} 个方向</small></header>
          <div class="candidate-grid">
            <article v-for="(candidate, index) in candidates" :key="candidate.candidate_id" :class="['candidate-card', `direction-${candidateDirection(candidate)}`]">
              <header><span>{{ String(index + 1).padStart(2, '0') }}</span><div><small>{{ directionLabel(candidate) }}</small><h4>{{ (candidate.display_title || candidate.title).replace(/^探索方向[:：]\s*/, '') }}</h4></div></header>
              <p class="question"><InlineCitations :text="researchText(candidate.research_question)" :sources="citationSources" /></p>
              <details class="candidate-design"><summary>查看研究内容与文献依据</summary>
              <section v-if="candidateReading(candidate).reason" class="candidate-rationale"><h5>选题理由</h5><p><InlineCitations :text="researchText(candidateReading(candidate).reason)" :sources="citationSources" /></p></section>
              <section v-if="candidateReading(candidate).researchContent.length" class="candidate-content"><h5>研究内容</h5><dl><div v-for="field in candidateReading(candidate).researchContent" :key="field.label"><dt>{{ field.label }}</dt><dd><InlineCitations :text="researchText(field.value)" :sources="citationSources" /></dd></div></dl></section>
              <section v-if="candidateReading(candidate).evidenceSummary" class="candidate-evidence"><h5>文献依据</h5><p><InlineCitations :text="researchText(candidateReading(candidate).evidenceSummary)" :sources="citationSources" /></p></section>
              <footer v-if="candidate.theme_ids?.length"><span>相关主题：{{ themeLabels(candidate.theme_ids) }}</span></footer></details>
            </article>
          </div>
        </section>



        <section v-if="selectedStep.key === 'report' && ['awaiting_correction', 'superseded'].includes(detail.status)" class="notice correction">
          <span class="material-icons-outlined">edit_note</span><div><b>{{ detail.status === 'superseded' ? '已进入补正版本' : '待补正，当前任务未完成，不会自动重跑' }}</b><p>补正原因：{{ correctionContext.reason || latestCorrectionReason || '未记录' }}</p><small>操作人：{{ correctionContext.operator || '未记录' }} · 时间：{{ formatDate(correctionContext.returned_at) }}</small><button v-if="detail.status === 'awaiting_correction'" class="primary correction-action" :disabled="correctionRunning" @click="rerunCorrection">{{ correctionRunning ? '正在创建补正版' : '根据补正意见重新执行' }}</button></div>
        </section>

        <section v-if="selectedStep.key === 'report'" class="report-stage">
          <div v-if="detail.status === 'failed' && state?.resume_from_stage === 'report_publish' && !reportSections.length" class="notice" role="alert"><p>报告生成未完成，已有分析结果已保留。</p><button class="primary" :disabled="actionPending" @click="confirmTask('retry_report')">{{ actionPending ? '正在恢复' : '重试生成报告' }}</button></div>
          <header class="topic-report-header">
            <div><span>REPORT {{ state?.versions?.report_version || '' }}</span><h3>{{ state?.report_plan?.report_title || detail.title }}</h3><p v-if="reportPageCount">报告实际 {{ reportPageCount }} 页</p></div>
            <ReportDownloadActions :disabled="!reportSections.length" :docx-loading="downloading.docx" :pdf-loading="downloading.pdf" :md-loading="downloading.md" @download="download" />
          </header>
          <ReportPdfPreview v-if="reportSections.length" :task-id="taskId" :title="state?.report_plan?.report_title || detail.title" :version="state?.versions?.report_version" :load-pdf="fetchTopicReportPdf" :downloading="downloading.pdf" @download="download('pdf')" @loaded="reportPageCount = $event" />
          <p v-else class="muted topic-report-pending">报告尚未发布，生成完成后可预览和下载。</p>
        </section>




        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      </main>
    </template>
  </section>
</template>

<script setup lang="ts">
import { researchText, pointLiteratureOrder } from '../services/researchPresentation'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { uploadPresentation } from '../services/uploadPresentation'
import { downloadTopicReport, fetchTopicReportPdf, getTopicSelectionTask, rerunTopicCorrection, stopTopicSelectionTask, submitTopicConfirmation, subscribeToTopicTaskStream, type TopicCandidate, type TopicRunResponse, type TopicStageTrace, type TopicTaskDetail } from '../services/topicSelectionApi'
import ReportPdfPreview from '../components/ReportPdfPreview.vue'
import ReportDownloadActions from '../components/ReportDownloadActions.vue'
import LiteratureReferences from '../components/LiteratureReferences.vue'
import InlineCitations from '../components/InlineCitations.vue'
import { topicDisplayTitle, candidateDirection, orderedTopicCandidates, topicCandidateReading, topicThemeNodes, topicCitationMap, topicCitationSources, uniqueTopicEvidence } from '../services/topicPagePresentation'
import { topicReaderText } from '../services/topicEvidencePresentation'
import { aggregateSteps, stepDurationLabel, stepForStage, TOPIC_STEPS, userStepStatusLabel, stageRunPresentation } from '../services/userSteps'
import StageRunNotice from '../components/StageRunNotice.vue'
import { followTaskProgress } from '../services/taskProgress'
import { recoveryLabel, recoveryNotice } from '../services/generationRecovery'
import LiteratureCard from '../components/LiteratureCard.vue'
import LiteratureTrendChart from '../components/LiteratureTrendChart.vue'
import DisciplineRelationGraph from '../components/DisciplineRelationGraph.vue'
import TopicThemeGraph from '../components/TopicThemeGraph.vue'
import { discoveryReadingText, discoveryRows, futureResearchSummary, methodEvolutionSummary, opportunityReading as buildOpportunityReading } from '../services/topicDiscovery'
import { topicResultState, topicTrendSeries } from '../services/resultSnapshot'

const props = withDefaults(defineProps<{ taskId: string; mode?: 'running' | 'detail'; initialRun?: TopicRunResponse | null; workspaceCommand?: { type: 'clarify' | 'continue' | 'retry' | 'stop' | 'download'; key: number } | null }>(), { mode: 'detail', initialRun: null, workspaceCommand: null })
const emit = defineEmits<{ navigate: [page: string, taskId?: string]; 'workspace-meta': [meta: { status: string; running: boolean }] }>()
const initial = props.initialRun ? ({ id: props.initialRun.run_id, task_id: props.initialRun.run_id, title: props.initialRun.state.input_profile.title, task_type: '智能选题', status: props.initialRun.status, current_stage: props.initialRun.state.current_stage, completed_steps: 0, total_steps: 9, needs_confirmation: false, confirmation_status: 'not_required', summary: '', created_at: '', updated_at: '', raw_user_input: props.initialRun.state.raw_user_input, state: props.initialRun.state, output_markdown: '', output_json: {}, stage_trace: props.initialRun.state.stage_trace } as TopicTaskDetail) : null
const detail = ref<TopicTaskDetail | null>(initial)
const loading = ref(!initial); const error = ref(''); const stopping = ref(false); const downloading = ref({ docx: false, pdf: false, md: false }); const correctionRunning = ref(false)
const confirmationNote = ref(''); const actionPending = ref(false); const reportPageCount = ref(0)
const selectedStageKey = ref(initial?.current_stage || 'input_modeling'); const logOpen = ref(false)
let unsubscribe: (() => void) | null = null
let pageDisposed = false, loadGeneration = 0
const progressSyncing = ref(false)
let manualSelection = false

const state = computed(() => detail.value?.state ? topicResultState(detail.value.state) : null)
const citationSources = computed(() => topicCitationSources(state.value || {}))
const citationMap = computed(() => topicCitationMap(state.value || {}))
const materialResultsOpen = ref<boolean | null>(null)
const uploadInfo = computed(() => uploadPresentation(state.value, state.value?.input_profile?.documents || []))
const stageKeys = TOPIC_STEPS.flatMap(step => step.stages)
const stages = computed<TopicStageTrace[]>(() => {
  const saved = detail.value?.stage_trace?.length ? detail.value.stage_trace : state.value?.stage_trace || []
  return stageKeys.map((key, index) => saved.find(item => item.stage_key === key) || ({ stage_key: key, stage_name: shortStageName(key), status: index < (detail.value?.completed_steps || 0) ? 'completed' : 'pending', summary: '历史任务未保存该阶段执行明细', progress_percent: index < (detail.value?.completed_steps || 0) ? 100 : 0 } as TopicStageTrace))
})
const durationTick = ref(Date.now())
let durationTimer: number | null = null
const userSteps = computed(() => aggregateSteps(TOPIC_STEPS, stages.value, durationTick.value, isLive.value, detail.value?.status || ''))
const recovery = computed(() => state.value?.output_json?.generation_recovery || detail.value?.recovery)
const recoveryPresentation = computed(() => recoveryNotice(recovery.value))
const recoveryStageTitle = computed(() => stepForStage(TOPIC_STEPS, recovery.value?.stage || detail.value?.current_stage || '').title)
const runNotice = computed(() => stageRunPresentation(userSteps.value, stages.value, detail.value?.current_stage || '', detail.value?.status || '', progressSyncing.value))
const selectedStep = computed(() => userSteps.value.find(item => item.key === stepForStage(TOPIC_STEPS, selectedStageKey.value).key) || userSteps.value[0])
const conciseDisplayTitle = computed(() => topicDisplayTitle(state.value || {}, detail.value?.title))
function selectUserStep(key: string) {
  const step = userSteps.value.find(item => item.key === key)
  if (!step || (step.status === 'pending' && isLive.value)) return
  const available = step.stages.map(stage => stages.value.find(item => item.stage_key === stage)).filter((item): item is TopicStageTrace => Boolean(item))
  const target = available.find(item => item.status === 'running') || available.find(item => item.status !== 'pending') || available[0]
  if (target) selectStage(target)
}
function stepStatusLabel(status: string) { return userStepStatusLabel(status) }
function stepDescription(key: string) { return TOPIC_STEPS.find(step => step.key === key)?.description || '' }
const themeNodes = computed(() => topicThemeNodes(state.value?.theme_tree || {}))
const presentationThemeTree = computed(() => ({ ...state.value?.theme_tree, nodes: themeNodes.value }))
const topicLiteratureGroups = computed(() => {
  const evidence = uniqueTopicEvidence(state.value?.evidence_pool || [])
  return themeNodes.value.map((theme: any) => ({ id: theme.theme_id, title: theme.display_label || theme.label, items: pointLiteratureOrder(evidence.filter((item: any) => item.theme_ids?.includes(theme.theme_id)), { text: [theme.label, ...(theme.seed_terms || [])].join(' ') }) }))
})
const unassignedLiterature = computed(() => uniqueTopicEvidence(state.value?.evidence_pool || []).filter((item: any) => !item.theme_ids?.some((id: string) => themeNodes.value.some(theme => theme.theme_id === id))))
const frozenTrendSeries = computed(() => topicTrendSeries(state.value || {}))
const landscapeConclusion = computed(() => {
  const analysis = state.value?.stage_artifacts?.landscape_trend?.analysis || {}
  const findings = (analysis.trend_findings || []).map((item: any) => item.finding).filter(Boolean)
  return discoveryText(findings.slice(0, 2).join('；') || analysis.overview)
})
const methodSummary = computed(() => methodEvolutionSummary(state.value || {}))
const futureSummary = computed(() => futureResearchSummary(state.value || {}))
const selectedStage = computed(() => stages.value.find(item => item.stage_key === selectedStageKey.value) || stages.value[0])
const selectedStageIndex = computed(() => Math.max(0, stages.value.findIndex(item => item.stage_key === selectedStage.value?.stage_key)))
const selectedStagePercent = computed(() => selectedStage.value?.progress_percent ?? (selectedStage.value?.status === 'completed' ? 100 : 0))
const isReportComplete = computed(() => Boolean(state.value?.report_snapshot?.frozen && state.value?.report_plan?.publication_status === 'published'))
const completionPercent = computed(() => isReportComplete.value ? 100 : Math.round(userSteps.value.reduce((sum, step) => sum + step.progress_percent, 0) / userSteps.value.length))
const isLive = computed(() => ['queued', 'running'].includes(detail.value?.status || ''))
const rawCandidates = computed(() => state.value?.candidates || [])
const candidates = computed(() => orderedTopicCandidates(rawCandidates.value))
function candidateReading(candidate: any) {
  return topicCandidateReading(candidate, rawCandidates.value, state.value?.report_plan?.sections || [], citationMap.value)
}
function directionLabel(candidate: TopicCandidate) { return ({ recommended: '推荐选题', exploratory: '探索方向', candidate: '候选选题' })[candidateDirection(candidate)] }
const sourceQueries = computed(() => (state.value?.source_queries || []).filter((item: any) => item.source_kind === 'nstl' || item.external_request_allowed || item.returned_count > 0))
function themeSearchInfo(id: string) {
  const branches = (state.value?.query_plan?.branches || []).filter((item: any) => item.theme_id === id)
  return branches.map((branch: any) => {
    const source = sourceQueries.value.find((item: any) => item.query_id === branch.branch_id)
    const years = branch.year_range?.length ? branch.year_range : source?.time_range?.length ? source.time_range : state.value?.input_profile?.time_range || []
    const sourceNames = source ? [sourceLabel(source.source_kind)] : (branch.source_scope || []).map((item: string) => sourceLabel(item))
    return {
      term: source?.sanitized_query || source?.query || branch.query || '',
      sources: [...new Set(sourceNames.filter(Boolean))],
      timeRange: years.length === 2 ? `${years[0]}–${years[1]}` : '不限',
    }
  }).filter((item: any) => item.term)
}
const correctionContext = computed(() => state.value?.correction_context || {})
const correctionChildId = computed(() => { const id = String(correctionContext.value.correction_task_id || ''); return id === props.taskId ? '' : id })
const latestCorrectionReason = computed(() => [...(state.value?.resolution_logs || [])].reverse().find(item => item.decision !== 'approve')?.reason || '')
const timeRange = computed(() => state.value?.input_profile?.time_range?.length === 2 ? state.value.input_profile.time_range.join(' - ') : '不限')
const literatureCount = computed(() => state.value?.evidence_pool?.length || 0)
const visibleCrosslinks = computed(() => {
  const seen = new Set<string>()
  return [...(state.value?.crosslinks || [])].filter((link: any) => link.transferable_method && (link.research_opportunity || link.relation_basis) && link.method_evidence?.some((source: any) => source.verified))
    .sort((a: any, b: any) => Number(b.status === 'validated') - Number(a.status === 'validated') || (b.support_evidence_ids?.length || 0) - (a.support_evidence_ids?.length || 0))
    .filter((link: any) => { const key = `${link.transferable_method}|${link.research_opportunity}`.replace(/\s/g, '').toLowerCase(); if (seen.has(key)) return false; seen.add(key); return true }).slice(0, 3)
})
const selectedCrosslink = ref('')
watch(() => props.taskId, () => { selectedCrosslink.value = '' })
const graphRows = computed(() => visibleCrosslinks.value.flatMap((link: any) => discoveryRows(state.value || {}).filter(row => link.crosslink_id === row.crosslink_id)))
const opportunityCards = computed(() => visibleCrosslinks.value)
function opportunityReading(link: any) { return buildOpportunityReading(link, themeNodes.value) }
function discoveryText(value: unknown) {
  return discoveryReadingText(researchText(value))
}
function summaryWithCitations(summary: { text: string; evidence_ids?: string[] }) {
  const refs = [...new Set((summary.evidence_ids || []).map(id => citationMap.value[id]).filter((number): number is number => Number.isSafeInteger(number) && number > 0))]
  return `${summary.text || ''}${refs.length ? ` ${refs.map(number => `[${number}]`).join(' ')}` : ''}`.trim()
}
const reportSections = computed(() => (isReportComplete.value ? state.value?.report_plan?.sections || [] : []).map((item: any, index: number) => ({ key: item.key || item.section_key || `section-${index}`, title: item.title || item.display_label || `章节 ${index + 1}`, content: item.content || item.summary || item.abstract || '', blocks: Array.isArray(item.blocks) ? item.blocks : [] })))
const selectedAuditEvents = computed<any[]>(() => [...(state.value?.progress_events || []).filter(item => item.stage_key === selectedStageKey.value), ...(state.value?.process_trace?.[selectedStageKey.value] || []), ...(state.value?.model_calls || []).filter(item => item.stage_key === selectedStageKey.value)].sort((a, b) => String(a.occurred_at || a.started_at || '').localeCompare(String(b.occurred_at || b.started_at || ''))))
const stageOutput = computed(() => buildStageOutput(selectedStageKey.value).map(item => ({ ...item, title: topicReaderText(item.title), detail: topicReaderText(item.detail) })).filter(item => item.title))
const handoffItems = computed(() => buildHandoff(selectedStageKey.value))
const liveTitle = computed(() => isReportComplete.value ? '智能选题已完成' : ({ awaiting_confirmation: '任务需要处理', awaiting_correction: '等待补正', superseded: '已进入补正版本', failed: '任务执行失败', cancelled: '任务已停止', model_unavailable: '服务暂不可用' } as Record<string, string>)[detail.value?.status || ''] || `正在完成${selectedStep.value.title}`)
const liveMessage = computed(() => isReportComplete.value ? '完整报告已生成，可直接阅读或下载 Word、PDF。' : detail.value?.status === 'awaiting_confirmation' ? '请查看下方任务提示后继续。' : detail.value?.status === 'awaiting_correction' ? '请根据已保存的补正意见继续执行。' : detail.value?.status === 'superseded' ? '原任务已保留，请进入关联补正版查看后续进展。' : '已完成的结果可通过上方步骤查看。')
const liveIcon = computed(() => isReportComplete.value ? 'task_alt' : ({ awaiting_correction: 'edit_note', superseded: 'account_tree', failed: 'error_outline', cancelled: 'stop_circle' } as Record<string, string>)[detail.value?.status || ''] || 'radar')

function shortStageName(key: string) { return ({ input_modeling: '输入建模', theme_decomposition: '主题拆解', retrieval_evidence: '检索证据', landscape_trend: '趋势分析', crosslink_gap: '交叉与空白', candidate_generation: '候选生成', scoring_ranking: '候选研判', quality_gate: '自动质检', report_publish: '报告发布' } as Record<string, string>)[key] || key }
function stageDescription(key: string) { return ({ input_modeling: '固化研究边界、约束、输入材料与术语。', theme_decomposition: '形成主题节点、关系与可检索种子。', retrieval_evidence: '执行真实查询，定位摘要和全文片段并锁定证据池。', landscape_trend: '从锁定证据中归纳研究趋势、方法演进和限制。', crosslink_gap: '核验学科交叉关系、反证与研究空白。', candidate_generation: '把主题和空白收敛为对象、假设、路线和验证边界明确的候选题。', scoring_ranking: '比较证据覆盖、适配性、可行性、风险和验证优先级。', quality_gate: '检查字段完整性、证据回链、降级原因与发布条件。', report_publish: '使用同一报告快照展示正文、表格、图表、引用并提供下载。' } as Record<string, string>)[key] || '等待阶段开始。' }
function statusLabel(status: string) { if (isReportComplete.value) return '已完成'; return ({ queued: '排队中', running: '进行中', completed: '已完成', awaiting_confirmation: '待确认', awaiting_review: '报告已生成', awaiting_correction: '待补正', superseded: '已进入补正版本', model_unavailable: '模型不可用', failed: '执行失败', cancelled: '已停止' } as Record<string, string>)[status] || status }
function stageVisualState(stage?: TopicStageTrace) { if (!stage) return 'pending'; if (stage.status === 'failed') return 'failed'; if (stage.status === 'completed') return 'completed'; if (stage.status === 'blocked') return 'blocked'; return stage.status }
function stageStatusText(stage?: TopicStageTrace) { const visual = stageVisualState(stage); return ({ pending: '等待执行', running: '执行中', completed: '已完成', degraded: '已降级', failed: '失败', blocked: '等待处理', skipped: '已跳过' } as Record<string, string>)[visual] || visual }
function stageCaption(stage: TopicStageTrace) { return stage.status === 'running' ? stage.active_step || '执行中' : stage.status === 'completed' ? formatDuration(stage.duration_seconds) : stageStatusText(stage) }
function canOpen(stage: TopicStageTrace) { return stage.status !== 'pending' || ['completed', 'awaiting_review', 'awaiting_correction', 'superseded', 'failed', 'cancelled', 'model_unavailable'].includes(detail.value?.status || '') }
function selectStage(stage: TopicStageTrace) { if (canOpen(stage)) { manualSelection = true; selectedStageKey.value = stage.stage_key } }
function executionModeLabel(value?: string) { return ({ model: '模型分析', mixed: '模型 + 规则', deterministic: '确定性规则', deterministic_fallback: '规则降级', disabled: '模型未启用' } as Record<string, string>)[value || ''] || '待执行' }
function modelStatusLabel(value?: string) { return ({ completed: '已完成', running: '分析中', degraded: '已降级', disabled: '未启用', not_requested: '未请求' } as Record<string, string>)[value || ''] || '未请求' }
function sourceLabel(value: string) { return ({ nstl: 'NSTL 文献检索', NSTL: 'NSTL 文献检索', fulltext_library: '全文库补充', web: '联网', cloud_model: '云模型' } as Record<string, string>)[value] || value }
function recommendationLabel(value?: string) { return ({ recommended: '优先推荐', exploratory: '探索方向', pending_evidence: '探索方向' } as Record<string, string>)[value || ''] || '探索方向' }
function evidenceScopeLabel(value?: string) { return ({ fulltext: '全文证据', abstract: '摘要证据', metadata: '元数据', external: '动态外部证据' } as Record<string, string>)[value || ''] || '证据' }
function formatDuration(value?: number | null) { if (value == null) return '--'; if (value < 1) return `${value.toFixed(1)} 秒`; if (value < 60) return `${Math.round(value)} 秒`; return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒` }
function formatDate(value?: string) { const date = new Date(value || ''); return Number.isNaN(date.getTime()) ? '--' : date.toLocaleString('zh-CN', { hour12: false }) }
function formatTime(value?: string) { const date = new Date(value || ''); return Number.isNaN(date.getTime()) ? '--:--:--' : date.toLocaleTimeString('zh-CN', { hour12: false }) }
function formatRecord(value: any) { return topicReaderText(value.summary || value.description || '处理记录已保存。') }
function chunksFor(ids?: string[], evidenceId?: string) { const wanted = new Set(ids || []); return (state.value?.evidence_chunks || []).filter(item => wanted.has(item.chunk_id) && (!evidenceId || item.evidence_id === evidenceId)) }
function evidenceFor(id: string) { return (state.value?.evidence_pool || []).find(item => item.evidence_id === id) }
function themeLabels(ids?: string[]) { return (ids || []).map(id => id === 'root' ? state.value?.theme_tree?.root || '原始主题' : (state.value?.theme_tree?.nodes || []).find((item: any) => item.theme_id === id)?.label || '历史记录未保存主题名称').join('、') || '未关联' }
function gapFor(id: string) { return (state.value?.gaps || []).find(item => item.gap_id === id) }
function gapLabel(id: string) { return gapFor(id)?.label || '历史记录未保存空白说明' }
function evidenceLabels(ids?: string[]) { return (ids || []).map(id => `${state.value?.report_plan?.citation_map?.[id] ? '[' + state.value.report_plan.citation_map[id] + '] ' : ''}${evidenceFor(id)?.title || '证据未保存'}${evidenceFor(id)?.year ? `（${evidenceFor(id)?.year}）` : ''}`).join('；') || '当前资料未提供' }
function crossStatus(link: any) { return link.method_evidence?.some((source: any) => source.verified) ? '方法来源有文献依据，适用性待验证' : '待验证线索' }
function crossType(type: string) { return ({ shared_method: '方法可迁移', shared_object: '共享研究对象', shared_data: '共同数据基础', shared_bottleneck: '共同问题与验证边界', citation_path: '引用关系' } as Record<string, string>)[type] || '研究方向关联' }
function readableRelation(link: any) { return link.description?.startsWith('关系仅在') ? '主题关联，方法迁移效果可通过后续对照研究检验。' : researchText(link.description) }
function relatedGaps(id: string) { return (state.value?.gaps || []).filter((gap: any) => gap.crosslink_ids?.includes(id)).map((gap: any) => gap.label).join('；') }
function relationLabel(value?: string) { return ({ supports: '支持', contradicts: '反证', context: '背景' } as Record<string, string>)[value || ''] || value }
function scoreFor(id: string) { return (state.value?.score_breakdowns || []).find(item => item.candidate_id === id) }
function navigateTask(id: string) { emit('navigate', 'topic-task-detail', id) }

function buildStageOutput(key: string) {
  const s: any = state.value || {}; const rows: Array<{ label: string; title: string; detail?: string }> = []
  if (key === 'input_modeling') {
    const profile = s.input_profile || {}; const card = s.constraint_card || {}
    rows.push({ label: '研究问题', title: profile.research_question || detail.value?.raw_user_input || '' }, { label: '研究边界', title: card.boundary_text || '待确认', detail: `必须包含：${(card.must_include || []).join('；') || '未限定'}；排除：${(card.must_exclude || []).join('；') || '未限定'}` })
    rows.push({ label: '对象与场景', title: profile.research_object || '未单独限定', detail: `方法：${profile.method_paradigm || '未限定'}；应用场景：${profile.application_scenario || '未限定'}` }, { label: '数据与资源约束', title: [...(profile.data_constraints || []), ...(profile.resource_constraints || []), ...(card.resource_limits || [])].join('；') || '未限定', detail: `假设：${[...(profile.assumptions || []), ...(card.assumptions || [])].join('；') || '无'}；待明确：${[...(profile.missing_fields || []), ...(card.unresolved || [])].join('；') || '无'}` })
    ;(s.term_map || []).forEach((item: any) => rows.push({ label: item.term_id || '术语', title: item.preferred, detail: [...(item.alternatives || []), ...(item.english_terms || [])].join('、') }))
  }
  if (key === 'theme_decomposition') {
    ;(s.theme_tree?.nodes || []).forEach((item: any) => rows.push({ label: item.theme_id, title: item.label, detail: `边界：${item.boundary || '沿用任务边界'}；检索种子：${(item.seed_terms || []).join('、') || '待补充'}` }))
    ;(s.theme_tree?.edges || []).forEach((item: any) => rows.push({ label: '主题关系', title: themeLabels([item.source || item.source_theme_id, item.target || item.target_theme_id].filter(Boolean)), detail: item.relation || item.relation_type || item.description || '未保存关系说明' }))
    ;(s.theme_tree?.overlap_warnings || []).forEach((text: string) => rows.push({ label: '重叠提示', title: text }))
  }
  if (key === 'retrieval_evidence') rows.push({ label: '文献查询', title: `${sourceQueries.value.length} 个分支`, detail: `${sourceQueries.value.filter((item: any) => item.status === 'failed').length} 个失败` }, { label: '文献', title: `${literatureCount.value} 篇`, detail: s.evidence_lock?.locked ? '文献依据已锁定用于后续分析' : '正在整理文献依据' })
  if (key === 'landscape_trend') {
    const analysis = s.stage_artifacts?.landscape_trend?.analysis || {}
    rows.push({ label: '样本', title: `${s.landscape_snapshot?.sample_count || 0} 条`, detail: '趋势结论仅适用于本次检索样本' })
    if (analysis.overview) rows.push({ label: '趋势概览', title: analysis.overview })
    for (const [field, label] of [['trend_findings', '研究趋势'], ['method_evolution', '方法演进'], ['conflicts', '冲突与反证']]) (analysis[field] || []).forEach((item: any) => rows.push({ label, title: item.finding, detail: `支持：${evidenceLabels(item.evidence_ids || item.support_evidence_ids)}${item.counter_evidence_ids?.length ? `；反证：${evidenceLabels(item.counter_evidence_ids)}` : ''}` }))
    ;[...(s.landscape_snapshot?.limitations || []), ...(analysis.coverage_limitations || [])].forEach((text: string) => rows.push({ label: '证据限制', title: text }))
    ;(s.future_work_signals || []).forEach((item: any) => rows.push({ label: item.signal_type, title: item.text, detail: `${evidenceLabels([item.evidence_id])} · ${item.locator}` }))
  }
  if (key === 'crosslink_gap') {
    ;(s.crosslinks || []).forEach((item: any) => rows.push({ label: item.crosslink_id, title: item.title, detail: `${item.description}；${themeLabels([item.source_theme_id, item.target_theme_id])}；关系依据：${item.relation_basis || '待核验'}；支持：${evidenceLabels(item.support_evidence_ids)}；反证：${evidenceLabels(item.counter_evidence_ids)}；状态：${item.status}；不确定性：${item.uncertainty || '未单独记录'}；替代解释：${(item.alternative_explanations || []).join('；') || '未记录'}` }))
    ;(s.gaps || []).forEach((item: any) => rows.push({ label: item.gap_id, title: item.label, detail: `${item.rationale || ''}；证据：${evidenceLabels(item.evidence_ids)}；验证状态：${item.validation_status}；不确定性：${item.uncertainty || '未单独记录'}` }))
  }
  if (key === 'candidate_generation' || key === 'scoring_ranking') candidates.value.forEach(item => rows.push({ label: item.candidate_id, title: item.title, detail: `${recommendationLabel(item.recommendation_level)} · ${(item.evidence_links || []).length} 条证据链` }))
  if (key === 'quality_gate') rows.push({ label: '自动质检', title: '研究设计、引用和证据关联已检查', detail: '证据限制已写入报告，无需额外处理。' })
  if (key === 'report_publish') reportSections.value.forEach((item: { key: string; title: string; content: string }) => rows.push({ label: item.key, title: item.title, detail: item.content.slice(0, 120) }))
  return rows
}
function buildHandoff(key: string) {
  const s: any = state.value || {}
  const map: Record<string, string[]> = {
    input_modeling: [`研究问题：${s.input_profile?.research_question || ''}`, `边界与约束：${s.constraint_card?.boundary_text || '待确认'}`],
    theme_decomposition: [`研究主题：${(s.theme_tree?.nodes || []).map((item: any) => item.label).join('；')}`, `检索关键词：${[...new Set((s.theme_tree?.nodes || []).flatMap((item: any) => item.seed_terms || []))].join('、')}`],
    retrieval_evidence: [`文献 ${literatureCount.value} 篇`, '已保存文献依据与查询记录'],
    landscape_trend: ['研究趋势与方法演进', `未来工作信号：${s.future_work_signals?.length || 0} 条`],
    crosslink_gap: [`交叉关系：${visibleCrosslinks.value.length} 条`, `研究空白：${s.gaps?.map((item: any) => item.label).join('；') || '无'}`],
    candidate_generation: [`候选方向：${candidates.value.length} 个`, '研究设计、验证边界与证据依据'],
    scoring_ranking: [`候选研判：${s.score_breakdowns?.length || 0} 项`, '验证路线、风险和证据边界'],
    quality_gate: ['自动质量检查已完成', `证据限制：${s.quality_gate?.warnings?.length || 0} 项，已写入报告`],
    report_publish: [`报告版本：${s.versions?.report_version || '待生成'}`, '固定结构：摘要、八章和参考文献'],
  }
  return (map[key] || []).filter(item => !item.endsWith('：'))
}

async function load() {
  const tid = props.taskId, generation = ++loadGeneration
  loading.value = true; error.value = ''
  try { const next = await getTopicSelectionTask(tid); if (pageDisposed || generation !== loadGeneration || tid !== props.taskId) return; detail.value = next; syncSelection() }
  catch (err: any) { if (!pageDisposed && generation === loadGeneration && tid === props.taskId) { detail.value = null; error.value = err?.message || '读取任务失败' } }
  finally { if (!pageDisposed && generation === loadGeneration) { loading.value = false; emitMeta() } }
}
function syncSelection() { if (manualSelection && stages.value.some(item => item.stage_key === selectedStageKey.value)) return; const current = isReportComplete.value ? 'report_publish' : ['awaiting_correction', 'superseded'].includes(detail.value?.status || '') ? 'quality_gate' : detail.value?.current_stage; if (current && stages.value.some(item => item.stage_key === current && canOpen(item))) selectedStageKey.value = current; else selectedStageKey.value = [...stages.value].reverse().find(canOpen)?.stage_key || stages.value[0]?.stage_key || 'input_modeling' }
async function startStream() {
  unsubscribe?.()
  if (pageDisposed) return
  const tid = props.taskId
  unsubscribe = followTaskProgress<TopicTaskDetail>({
    subscribe: (onChange, onError) => subscribeToTopicTaskStream(tid, { onChange, onError }),
    fetch: () => getTopicSelectionTask(tid),
    version: next => Date.parse(next.updated_at),
    isLive: next => ['running', 'queued'].includes(next.status),
    onChange: next => { if (!pageDisposed && tid === props.taskId) { detail.value = next; syncSelection(); emitMeta() } },
    onSyncing: value => { progressSyncing.value = value },
  })
}
function emitMeta() { emit('workspace-meta', { status: isReportComplete.value ? 'completed' : detail.value?.status || 'loading', running: isLive.value }) }
async function stopTask() { stopping.value = true; try { await stopTopicSelectionTask(props.taskId); await load() } catch (err: any) { error.value = err?.message || '停止任务失败' } finally { stopping.value = false } }
async function download(format: 'docx' | 'pdf' | 'md') {
  if (downloading.value[format] || !reportSections.value.length) return
  downloading.value[format] = true; error.value = ''
  try { await downloadTopicReport(props.taskId, format) }
  catch (err: any) { error.value = err?.message || '下载失败' }
  finally { downloading.value[format] = false }
}
async function rerunCorrection() { correctionRunning.value = true; error.value = ''; try { const run = await rerunTopicCorrection(props.taskId); emit('navigate', 'topic-task-running', run.run_id) } catch (err: any) { error.value = err?.message || '创建补正版失败' } finally { correctionRunning.value = false } }
async function confirmTask(value: string) { if (actionPending.value) return; actionPending.value = true; try { await submitTopicConfirmation(props.taskId, value, confirmationNote.value.trim()); await load(); if (isLive.value) await startStream() } catch (err: any) { error.value = err?.message || '提交确认失败' } finally { actionPending.value = false } }

watch(() => props.taskId, async () => { const tid = props.taskId; unsubscribe?.(); manualSelection = false; materialResultsOpen.value = null; detail.value = null; await load(); if (tid === props.taskId && isLive.value) await startStream() })
watch(() => [props.taskId, selectedStageKey.value, state.value?.versions?.report_version], () => { reportPageCount.value = 0 })
watch(() => props.workspaceCommand?.key, () => { const command = props.workspaceCommand?.type; if (command === 'stop') stopTask(); if (command === 'download') download('docx'); if (command === 'continue' && detail.value?.status === 'awaiting_confirmation') { const option = state.value?.confirmation?.options?.[0]; if (option) confirmTask(option.value || option.id) } })
onMounted(async () => { durationTimer = window.setInterval(() => { durationTick.value = Date.now() }, 1000); await load(); if (isLive.value) await startStream() })
onBeforeUnmount(() => { pageDisposed = true; loadGeneration++; if (durationTimer != null) window.clearInterval(durationTimer); unsubscribe?.() })
</script>

<style scoped>
.recovery-notice{margin:16px 0;padding:16px 20px;border:1px solid #dce5f3;border-radius:10px;background:#f5f8fc;color:#304463;overflow-wrap:anywhere}.recovery-notice strong{font-size:15px}.recovery-notice p{margin:7px 0;font-size:14px;line-height:1.8}.recovery-notice small{font-size:13px;color:#67758b}

.stage-completion{padding:0 22px;color:#667085;font-size:12px;line-height:1.8}
.candidate-grid{grid-template-columns:minmax(0,1fr)}
.topic-trend{display:flex;align-items:end;gap:8px;min-height:160px;overflow-x:auto;padding:14px 4px 4px;border-bottom:1px solid #dfe3ef}
.topic-trend>div{display:flex;flex:1 0 34px;min-width:34px;height:145px;flex-direction:column;align-items:center;justify-content:end;gap:4px}
.topic-trend i{display:block;width:min(100%,25px);min-height:4px;border-radius:3px 3px 0 0;background:#6475e6}
.topic-trend strong,.topic-trend span{font-size:10px;color:#606b80}
.material-results summary { cursor: pointer; color: #263044; font-weight: 600; }
.material-results p { line-height: 1.8; }
.readable-results article{padding:18px 0;border-bottom:1px solid #e5e7ed;overflow-wrap:anywhere}.readable-results h4{margin:0 0 8px;font-size:15px;color:#263044;line-height:1.6}.readable-results p{font-size:13px;line-height:1.8;white-space:pre-wrap}.readable-results small{display:block;margin-bottom:8px;color:#6a7486}.readable-results dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.readable-results dt{font-size:12px;color:#6a7486}.readable-results dd{margin:4px 0 0;font-size:13px;line-height:1.7}.readable-results details{margin-top:12px}.readable-results summary{cursor:pointer;color:#425aaf;font-size:13px}.relation-evidence{padding:12px 0;border-bottom:1px solid #e5e7ed}.relation-evidence a{color:#425aaf}.relation-evidence p{max-height:12em;overflow-y:auto}@media(max-width:720px){.readable-results dl{grid-template-columns:1fr}}
.topic-report-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; padding: 20px; border-bottom: 1px solid #e0e4ed; }
.topic-report-header > div:first-child { flex: 1; min-width: min(100%, 240px); }
.topic-report-header span { color: #64748b; font-size: 11px; }
.topic-report-header h3 { margin: 6px 0 0; color: #263044; font-size: 18px; line-height: 1.5; overflow-wrap: anywhere; }
.topic-report-header p { margin: 6px 0 0; color: #64748b; font-size: 12px; }
.topic-report-pending { padding: 20px; }
button:disabled{cursor:default;opacity:.55}.confirmation-panel textarea{display:block;box-sizing:border-box;width:100%;margin:8px 0 12px;padding:9px;border:1px solid #d4d8e2;border-radius:5px;font:inherit;resize:vertical}.confirmation-actions{display:flex;flex-wrap:wrap;gap:8px}.report-chart{display:block;max-width:100%;height:auto;margin:12px auto}.candidate-card,.audit-list,.chunk-list{overflow-wrap:anywhere}.evidence-chain a,.report-document a{color:#5264d0;overflow-wrap:anywhere}.audit-list article,.query-list article,.notice,.finding-list button{scroll-margin-top:90px}.panel>*{min-width:0}
.topic-page{width:min(calc(100% - 48px),1380px);margin:0 auto;padding:34px 0 68px;color:#263044}.topic-empty{min-height:340px;display:grid;place-content:center;justify-items:center;gap:12px;text-align:center}.topic-spinner{width:28px;height:28px;border:3px solid #dfe3f2;border-top-color:#6475f4;border-radius:50%;animation:spin .8s linear infinite}.primary,.secondary,.stop,.report-actions button,.version-links button{min-height:36px;padding:0 13px;border-radius:6px;cursor:pointer}.primary,.report-actions button{color:#fff;background:#596be8}.secondary,.version-links button{border:1px solid #bfc7df;color:#4354bd;background:#fff}.task-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:18px}.task-head>div:first-child{min-width:0}.eyebrow{color:#5869df;font-size:10px;font-weight:800}.task-head h1{max-width:960px;margin:7px 0 6px;font-size:30px;line-height:1.35;overflow-wrap:anywhere}.task-head p{display:-webkit-box;max-width:980px;overflow:hidden;margin:0;color:#747d90;font-size:12px;line-height:1.7;-webkit-line-clamp:2;-webkit-box-orient:vertical}.head-actions{display:flex;align-items:center;gap:9px}.status-pill{padding:7px 10px;border:1px solid #dce1ec;border-radius:999px;color:#626c81;background:#f7f8fa;font-size:11px;white-space:nowrap}.status-pill.is-running,.status-pill.is-queued{border-color:#d9defd;color:#4d5fca;background:#f0f2ff}.status-pill.is-awaiting_correction{border-color:#f0d4a6;color:#956319;background:#fff7e9}.status-pill.is-superseded{color:#536076;background:#eef1f5}.stop{display:flex;align-items:center;gap:5px;color:#a73c34;background:#fff;border:1px solid #ebcac7}.stop .material-icons-outlined{font-size:17px}.task-context{display:grid;grid-template-columns:minmax(300px,1fr) auto;gap:24px;align-items:center;padding:17px 20px;border:1px solid #e0e3eb;border-radius:8px;background:#fff;box-shadow:0 6px 20px rgba(35,45,75,.04)}.task-context>div span,.task-context dt{display:block;color:#9aa0ad;font-size:9px}.task-context>div b{display:-webkit-box;overflow:hidden;margin-top:5px;color:#555d70;font-size:12px;line-height:1.6;-webkit-line-clamp:2;-webkit-box-orient:vertical}.task-context dl{display:flex;gap:24px;margin:0}.task-context dd{margin:4px 0 0;color:#4f5769;font-size:11px;font-weight:700;white-space:nowrap}.version-links{display:flex;align-items:center;gap:10px;margin-top:12px;padding:10px 14px;border:1px solid #dfe3ef;border-radius:7px;background:#f8f9fc}.version-links p{margin:0;color:#667085;font-size:11px}.version-links .material-icons-outlined{color:#6374e5}.stage-bar{display:grid;grid-template-columns:repeat(9,minmax(112px,1fr));gap:1px;margin-top:16px;overflow-x:auto;border:1px solid #e1e4eb;border-radius:8px;background:#e1e4eb}.stage-bar button{display:grid;min-width:112px;grid-template-columns:28px minmax(0,1fr);gap:8px;align-items:center;padding:12px 9px;color:#747c8e;background:#fff;text-align:left;cursor:pointer}.stage-bar button:disabled{cursor:not-allowed;opacity:.48}.stage-bar button.selected{position:relative;color:#4d5ed0;background:#f2f3ff}.stage-bar button.selected:after{position:absolute;right:8px;bottom:0;left:8px;height:3px;background:#6475f4;content:''}.stage-number{display:grid;width:26px;height:26px;place-items:center;border:1px solid #d9ddea;border-radius:50%;font-size:10px;font-weight:800}.stage-number .material-icons-outlined{font-size:15px}.stage-bar button.is-completed .stage-number{border-color:#b9dfcf;color:#18815a;background:#edf9f4}.stage-bar button.is-running .stage-number{border-color:#6274ed;color:#fff;background:#6274ed}.stage-bar button.is-degraded .stage-number,.stage-bar button.is-blocked .stage-number{border-color:#e9c789;color:#9c681d;background:#fff5df}.stage-bar button.is-failed .stage-number{border-color:#e5b8b4;color:#a63c34;background:#fff0ef}.stage-bar b,.stage-bar small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stage-bar b{font-size:11px}.stage-bar small{margin-top:3px;color:#979daa;font-size:9px}.run-strip{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-top:14px;padding:14px 18px;border:1px solid #dfe3f5;border-left:3px solid #6475f4;border-radius:8px;background:#f8f8ff}.run-strip>div{display:flex;align-items:center;gap:10px}.run-strip .material-icons-outlined{color:#596be2}.run-strip p,.run-strip dl{margin:0}.run-strip p b,.run-strip p small{display:block}.run-strip p b{font-size:12px}.run-strip p small{margin-top:4px;color:#737b8d;font-size:10px}.run-strip dl{display:flex;gap:26px}.run-strip dt{color:#9aa0ae;font-size:9px}.run-strip dd{margin:4px 0 0;font-size:12px;font-weight:800}.workbench{margin-top:14px;overflow:hidden;border:1px solid #dfe3eb;border-radius:8px;background:#fff;box-shadow:0 8px 24px rgba(35,45,75,.04)}.stage-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:22px}.stage-head>div{min-width:0}.stage-head>div>span{color:#8991a3;font-size:10px}.stage-head h2{margin:5px 0;color:#31394c;font-size:21px}.stage-head p{margin:0;color:#737c8e;font-size:11px;line-height:1.7}.stage-status{padding:6px 10px;border-radius:999px;color:#687184;background:#f0f2f5;font-size:10px;white-space:nowrap}.stage-status.is-completed{color:#147b56;background:#eaf7f1}.stage-status.is-running{color:#4d5dcc;background:#eef0ff}.stage-status.is-degraded,.stage-status.is-blocked{color:#8c601e;background:#fff3db}.stage-status.is-failed{color:#a33b32;background:#fff0ee}.stage-progress{height:3px;background:#eef0f5}.stage-progress i{display:block;height:100%;background:#6475f4}.stage-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-bottom:1px solid #e8eaf0}.stage-facts div{padding:13px 20px;border-right:1px solid #eceef3}.stage-facts div:last-child{border-right:0}.stage-facts span,.stage-facts b{display:block}.stage-facts span{color:#9aa0ae;font-size:9px}.stage-facts b{margin-top:5px;font-size:12px}.panel{padding:20px 22px;border-bottom:1px solid #e8eaf0}.panel>header,.report-actions{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:13px}.panel>header>div{display:flex;align-items:center;gap:7px}.panel h3{margin:0;font-size:13px}.panel>header .material-icons-outlined{color:#6374e6;font-size:18px}.panel>header small{color:#969daa;font-size:9px}.muted{margin:0;padding:16px;color:#9097a5;background:#fafbfc;font-size:10px;text-align:center}.output-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;border:1px solid #e8eaf0;border-radius:6px;overflow:hidden;background:#e8eaf0}.output-grid article{min-width:0;padding:13px 15px;background:#fff}.output-grid span,.output-grid b,.output-grid p{display:block}.output-grid span{color:#999fac;font-size:9px}.output-grid b{margin-top:4px;color:#465064;font-size:11px;overflow-wrap:anywhere}.output-grid p{margin:4px 0 0;color:#7b8393;font-size:10px;line-height:1.6;overflow-wrap:anywhere}.notice{display:flex;gap:11px;margin:16px 22px 0;padding:13px;border:1px solid #ead4a7;border-radius:7px;color:#77591e;background:#fff9ee}.notice .material-icons-outlined{font-size:19px}.notice b{font-size:11px}.notice p{margin:4px 0 0;font-size:10px;line-height:1.65}.notice small{display:block;margin-top:5px}.notice.correction{margin-bottom:20px;border-color:#e9c584}.correction-action{display:block;margin-top:12px}.query-list,.evidence-list,.chunk-list{display:grid;gap:8px}.query-list article{display:grid;grid-template-columns:60px minmax(0,1fr) 54px;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid #eef0f4}.query-list article>span{padding:5px;color:#5263cf;background:#eef0ff;font-size:9px;font-weight:800;text-align:center}.query-list b,.query-list p,.query-list em{display:block;overflow-wrap:anywhere}.query-list b{font-size:11px}.query-list p,.query-list em{margin:3px 0 0;color:#858c9b;font-size:9px}.query-list em{color:#a4443d;font-style:normal}.query-list strong{font-size:11px;text-align:right}.evidence-list{margin-top:16px}.evidence-list article{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:13px;border:1px solid #e7e9ef;border-radius:6px}.evidence-list article>div{min-width:0}.evidence-list span{color:#737ee0;font-size:9px}.evidence-list b{display:block;margin:4px 0;font-size:11px}.evidence-list p{display:-webkit-box;overflow:hidden;margin:0;color:#737b8c;font-size:10px;line-height:1.6;-webkit-line-clamp:3;-webkit-box-orient:vertical}.evidence-list a{flex:none;color:#5264d0;font-size:10px}.chunk-list article{padding:14px;border-left:3px solid #7080e7;background:#f9f9fe}.chunk-list header{display:flex;gap:12px;align-items:center}.chunk-list header b{font-size:10px}.chunk-list header span{flex:1;color:#7c8494;font-size:9px}.chunk-list header em{font-size:9px;font-style:normal}.chunk-list p{margin:8px 0;color:#596276;font-size:11px;line-height:1.75}.chunk-list small{color:#9298a5;font-size:9px}.candidate-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.candidate-card{min-width:0;padding:17px;border:1px solid #e0e3eb;border-radius:7px}.candidate-card>header{display:grid;grid-template-columns:32px minmax(0,1fr);gap:10px}.candidate-card>header>span{display:grid;width:30px;height:30px;place-items:center;border-radius:6px;color:#fff;background:#6374e6;font-size:10px;font-weight:800}.candidate-card header small{color:#727ee1;font-size:9px}.candidate-card h4{margin:3px 0;font-size:14px;line-height:1.5;overflow-wrap:anywhere}.candidate-card .question{margin:12px 0;color:#566075;font-size:11px;line-height:1.7}.candidate-card dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0}.candidate-card dl div{padding:10px;border-left:2px solid #dfe2f7;background:#fafaff}.candidate-card dt{color:#747ee0;font-size:9px}.candidate-card dd{margin:4px 0 0;color:#626b7e;font-size:10px;line-height:1.6}.candidate-card details{margin-top:12px;border-top:1px solid #e9ebf0}.candidate-card summary{padding:10px 0;color:#5665c9;font-size:10px;cursor:pointer}.evidence-chain{display:grid;gap:7px}.evidence-chain article{padding:10px;background:#f7f8fb}.evidence-chain b{font-size:9px}.evidence-chain p,.evidence-chain small{display:block;margin:4px 0;color:#737b8c;font-size:9px;line-height:1.55}.evidence-chain blockquote{margin:7px 0 0;padding:8px 10px;border-left:2px solid #aab3ea;color:#535d70;background:#fff;font-size:10px;line-height:1.7}.candidate-card footer{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.candidate-card footer span{padding:4px 6px;border-radius:4px;color:#70798b;background:#f1f3f6;font-size:8px}.finding-list{display:grid;gap:7px}.finding-list button{display:grid;width:100%;grid-template-columns:20px minmax(0,1fr) 20px;gap:9px;align-items:center;padding:12px;border:1px solid #e5e7ed;border-radius:6px;color:#4f596c;background:#fff;text-align:left;cursor:pointer}.finding-list button.is-warning,.finding-list button.is-review{border-color:#ecd5aa;background:#fffbf3}.finding-list button.is-blocker{border-color:#e9c1bd;background:#fff5f4}.finding-list .material-icons-outlined{font-size:17px}.finding-list b,.finding-list small{display:block}.finding-list b{font-size:10px;line-height:1.5}.finding-list small{margin-top:3px;color:#8a91a0;font-size:9px}.review-panel article{display:flex;align-items:flex-end;justify-content:space-between;gap:16px}.review-panel article>div:first-child{min-width:0;flex:1}.review-panel article b{font-size:11px}.review-panel article p{color:#737b8c;font-size:10px}.review-panel textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #d4d8e2;border-radius:5px;font:inherit;resize:vertical}.review-panel article>div:last-child{display:flex;gap:7px}.report-stage{border-bottom:1px solid #e7e9ef}.report-actions{padding:20px 22px;margin:0;border-bottom:1px solid #e7e9ef}.report-actions span{color:#6676df;font-size:9px}.report-actions h3{margin:5px 0 0;font-size:17px}.report-actions>div:last-child{display:flex;gap:7px}.report-actions button{display:flex;align-items:center;gap:5px}.report-actions button .material-icons-outlined{font-size:16px}.report-reader{display:grid;grid-template-columns:220px minmax(0,1fr);align-items:start}.report-reader aside{position:sticky;top:80px;display:grid;gap:3px;padding:20px;border-right:1px solid #e7e9ef}.report-reader aside>b{margin-bottom:10px;font-size:12px}.report-reader aside a{display:grid;grid-template-columns:22px 1fr;gap:6px;padding:7px;color:#626b7e;font-size:10px;line-height:1.5;text-decoration:none}.report-reader aside a:hover{color:#5061cf;background:#f3f4ff}.report-reader aside span{color:#a0a6b2;font-size:8px}.report-document{min-width:0}.report-document>section{padding:27px 34px;border-bottom:1px solid #eceef2;scroll-margin-top:80px}.report-document>section>span{color:#6776dd;font-size:8px;font-weight:800}.report-document h3{margin:6px 0 12px;font-size:17px}.report-document>section>p{margin:0;color:#5d6678;font-size:11px;line-height:1.9;white-space:pre-wrap;overflow-wrap:anywhere}.report-table h4{margin:20px 0 8px;font-size:12px}.table-scroll{overflow-x:auto}.report-table table{width:100%;border-collapse:collapse;font-size:9px}.report-table th,.report-table td{padding:7px;border:1px solid #dfe2e8;vertical-align:top}.report-table th{background:#f3f4f7}.report-document figure{margin:20px 0 0;padding:14px;border:1px solid #e2e5eb}.report-document figcaption{font-size:11px;font-weight:700}.chart-preview{display:grid;gap:7px;margin-top:12px}.chart-preview>div{display:grid;grid-template-columns:120px 1fr 42px;gap:8px;align-items:center}.chart-preview span,.chart-preview b{font-size:9px}.chart-preview i{height:7px;background:#edf0f5}.chart-preview em{display:block;height:100%;background:#737fcb}.chart-preview p{color:#7d8493;font-size:10px;text-align:center}.report-document figure>small{display:block;margin-top:10px;color:#9197a4;font-size:8px}.handoff-list{display:flex;flex-wrap:wrap;gap:7px}.handoff-list span{padding:7px 9px;border:1px solid #dfe3ed;border-radius:5px;color:#596377;background:#f8f9fb;font-size:9px}.audit-panel{padding:0}.audit-toggle{display:flex;width:100%;min-height:50px;align-items:center;justify-content:space-between;padding:0 22px;color:#4e596d;background:#fff;cursor:pointer}.audit-toggle>span{display:flex;align-items:center;gap:8px}.audit-toggle .material-icons-outlined{font-size:17px}.audit-toggle small{color:#9299a7;font-size:9px}.audit-list{max-height:380px;overflow:auto;padding:0 22px 15px}.audit-list article{display:grid;grid-template-columns:64px minmax(0,1fr);gap:10px;padding:9px 0;border-top:1px solid #eff0f3}.audit-list time{color:#9a9fac;font-size:8px}.audit-list b{font-size:10px}.audit-list p{margin:3px 0 0;color:#7e8594;font-size:9px;line-height:1.6}.inline-error{margin:15px 22px;padding:10px;color:#9f352d;background:#fff2f1;font-size:10px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:1100px){.topic-page{width:min(calc(100% - 32px),1000px)}.task-context{grid-template-columns:1fr}.task-context dl{justify-content:space-between}.candidate-grid{grid-template-columns:1fr}.report-reader{grid-template-columns:180px minmax(0,1fr)}}
@media(max-width:720px){.topic-page{width:calc(100% - 24px);padding:20px 0 48px}.task-head{flex-direction:column}.task-head h1{font-size:24px}.head-actions{width:100%;justify-content:space-between}.task-context{padding:14px}.task-context dl{display:grid;grid-template-columns:1fr 1fr;gap:12px}.version-links{align-items:flex-start;flex-wrap:wrap}.stage-bar{display:flex;scroll-snap-type:x mandatory}.stage-bar button{flex:0 0 128px;scroll-snap-align:start}.run-strip{align-items:flex-start;flex-direction:column}.run-strip dl{width:100%;display:grid;grid-template-columns:1fr 1fr;gap:12px}.stage-head{padding:18px}.stage-facts{grid-template-columns:1fr 1fr}.stage-facts div:nth-child(2){border-right:0}.stage-facts div:nth-child(-n+2){border-bottom:1px solid #eceef3}.output-grid{grid-template-columns:1fr}.panel{padding:17px}.candidate-card dl{grid-template-columns:1fr}.query-list article{grid-template-columns:50px minmax(0,1fr)}.query-list strong{grid-column:2;text-align:left}.evidence-list article{flex-direction:column}.review-panel article{align-items:stretch;flex-direction:column}.report-actions{align-items:flex-start;flex-direction:column}.report-actions>div:last-child{width:100%;flex-wrap:wrap}.report-actions button{flex:1}.report-reader{grid-template-columns:1fr}.report-reader aside{position:static;display:flex;overflow-x:auto;border-right:0;border-bottom:1px solid #e7e9ef}.report-reader aside>b{display:none}.report-reader aside a{flex:0 0 160px}.report-document>section{padding:22px 18px}.chart-preview>div{grid-template-columns:80px 1fr 34px}.notice{margin-right:14px;margin-left:14px}.audit-list{padding-right:17px;padding-left:17px}}
@media(prefers-reduced-motion:reduce){.topic-spinner{animation:none}.stage-progress i{transition:none}}
/* Structured report blocks share the formal DOCX hierarchy in the reader. */
.report-document>section>p.report-lead{padding:10px 12px;border-left:3px solid #4f81bd;color:#324b66;background:#f4f8fc;font-weight:700;text-indent:0}
.report-document ul,.report-document ol{padding-left:2em;color:#5d6678;font-size:11px;line-height:1.8}
.report-chart-limit{padding:10px 12px!important;border-left:3px solid #e0a458;color:#687585!important;background:#f7f9fc}
.report-heading{padding-top:34px!important;border-top:5px solid #4f81bd}.report-heading h2{margin:0 0 18px;color:#17365d;font-size:25px;line-height:1.45;overflow-wrap:anywhere}.report-heading dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;margin:0}.report-heading dl div{min-width:0;padding:10px 12px;border-top:1px solid #e1e6ef}.report-heading dt{color:#8b93a2;font-size:9px}.report-heading dd{margin:4px 0 0;color:#4c566b;font-size:11px;overflow-wrap:anywhere}.report-subheading{margin:20px 0 8px;color:#17365d;font-size:13px;line-height:1.5}.candidate-evidence{margin-top:14px;padding-top:12px;border-top:1px solid #e5e7ef}.candidate-evidence h5{margin:0 0 8px;color:#4f5fbe;font-size:10px}.report-candidate p{white-space:pre-wrap;overflow-wrap:anywhere}
.report-table th{background:#dceaf7;color:#17365d}
.report-candidate{margin-top:20px;padding-top:12px;border-top:1px solid #d9e2f3}
.report-candidate h4{margin:0 0 4px;color:#4f81bd;font-size:13px}
.report-candidate>small{color:#7f8795;font-size:9px}
.report-candidate p{margin:7px 0;color:#5d6678;font-size:10px;line-height:1.7}
.report-candidate p b{color:#4f81bd}
.report-references{padding-left:0!important;list-style:none}
.report-references li{padding-left:2em;text-indent:-2em}
.report-document h3{color:#4f81bd;font-size:21px}
.report-document>section>p{margin:10px 0;font-size:14px;line-height:1.85;text-indent:2em}
.report-document ul,.report-document ol{font-size:14px;line-height:1.85}
.report-subheading,.report-table h4,.report-candidate h4{font-size:16px}
.report-candidate p{font-size:14px;line-height:1.85}
.report-candidate>small{font-size:12px}
.report-table table{font-size:12px}
.report-document figure{box-sizing:border-box;max-width:100%;overflow-x:auto}
.report-document figcaption{font-size:14px}
.report-document figure>.muted{padding:10px 0;background:transparent;font-size:12px;line-height:1.7;text-align:left}
.report-document figure>small{font-size:11px;line-height:1.7}
@media(max-width:720px){.report-reader aside{max-width:100%}.report-heading dl{grid-template-columns:1fr}.report-heading h2{font-size:21px}.table-scroll{max-width:100%}}
@media(max-width:720px){.report-chart{min-width:560px}.report-table table{min-width:560px}.report-document h3{font-size:19px}}
@media(min-width:721px){
  .stage-bar{grid-template-columns:repeat(5,minmax(0,1fr));overflow-x:visible}
  .stage-bar button{min-width:0;grid-template-columns:minmax(0,1fr);justify-items:center;gap:6px;padding:12px 4px;text-align:center}
  .stage-bar button>span:last-child{min-width:0;max-width:100%}
  .stage-bar b,.stage-bar small{white-space:normal;overflow-wrap:anywhere;line-height:1.4}
}
.topic-page{min-width:0;overflow-x:clip}.candidate-grid{grid-template-columns:minmax(0,1fr)}
@media(max-width:720px){.stage-bar{grid-template-columns:repeat(5,minmax(112px,1fr));max-width:100%;overflow-x:auto}}
</style>

<style scoped>

.task-head>div:first-child{flex:1}.task-head h1{max-width:none;font-size:26px;line-height:1.5;letter-spacing:.01em}.task-head .eyebrow{font-size:12px;letter-spacing:.08em}.head-actions{flex:none;padding-top:10px}
.task-head .original-question p{display:block;overflow:visible;max-width:none;-webkit-line-clamp:unset;font-size:14px;margin-top:10px}.original-question summary{font-size:14px;color:#536785;cursor:pointer}.original-question p{font-size:14px;line-height:1.85}
.stage-bar b{font-size:14px}.stage-bar small{font-size:12px}.stage-head p{font-size:14px}.stage-head>div>span{font-size:12px}.stage-status{font-size:12px}.panel h3{font-size:17px;font-weight:600}.panel>header small{font-size:13px;color:#748297}.readable-results>header small{margin:0}.intent-summary p{font-size:15px;line-height:1.85}.readable-results p{font-size:15px;line-height:1.85}.readable-results h4{font-size:17px}.run-strip p b{font-size:14px}.run-strip p small{font-size:13px}.task-context dt{font-size:12px}.task-context dd{font-size:14px}
.trend-analysis{margin-top:28px}.discovery-panel .trend-analysis>h4{margin:0 0 10px}.trend-analysis p{margin:0;color:#52657b}
.method-evolution{margin:32px 0;padding:24px 0;border-top:1px solid #e7ecf2;border-bottom:1px solid #e7ecf2}.discovery-panel .method-evolution>h4{margin:0 0 18px}.method-evolution article{display:grid;grid-template-columns:48px minmax(0,1fr);gap:0 18px;padding:16px 0;border:0;border-bottom:1px solid #edf0f5}.method-evolution article:first-of-type{padding-top:0}.method-evolution article:last-child{padding-bottom:0;border-bottom:0}.method-evolution h5{grid-column:1;margin:0;padding-top:2px;color:#768398;font-size:14px;font-weight:500;line-height:1.85}.method-evolution p{grid-column:2;margin:0;line-height:1.85}.method-evolution .literature-references{grid-column:2}.selected-opportunity{border-left:3px solid #5269c7;padding-left:14px!important}
.inline-source-toggle{display:inline-flex;align-items:center;gap:3px;vertical-align:baseline;margin:0 0 0 4px;padding:2px 0;border:0;background:transparent;color:#2465ae;font:inherit;font-size:14px;font-weight:400;line-height:24px;white-space:nowrap;cursor:pointer}.inline-source-toggle:hover{color:#174c87}.source-chevron{font-size:18px;line-height:1;transition:transform .15s}.inline-source-toggle[aria-expanded="true"] .source-chevron{transform:rotate(180deg)}.inline-source-toggle:focus-visible,summary:focus-visible{outline:2px solid #2465ae;outline-offset:3px}.discovery-panel>article>.inline-source-toggle{margin-left:0}.discovery-panel .literature-references{margin:6px 0 0;padding:0}
.simple-source-disclosure>summary{display:flex;align-items:center;gap:3px;list-style:none;font-weight:400}.simple-source-disclosure>summary::-webkit-details-marker{display:none}.source-collapse-label{display:none}.simple-source-disclosure[open]>summary .source-expand-label{display:none}.simple-source-disclosure[open]>summary .source-collapse-label{display:inline}.simple-source-disclosure[open]>summary .source-chevron{transform:rotate(180deg)}
.source-disclosure>summary,.more-literature>summary,.theme-query-records>summary{width:fit-content;max-width:100%;font-size:14px;color:#2465ae;cursor:pointer;line-height:1.8;padding:6px 0}.source-disclosure{margin-top:12px}
.literature-theme-group{padding:24px 0;border-top:1px solid #e7ecf2}.literature-theme-group:first-of-type{border-top:0;padding-top:6px}.literature-group-heading{display:flex;gap:12px;align-items:flex-start;margin-bottom:16px}.group-number{display:grid;place-items:center;flex:none;width:34px;height:34px;color:#536cc2;background:#eff3fc;border-radius:8px;font-size:14px;font-weight:600}.literature-group-heading h4{margin:0;color:#263d58;font-size:17px;font-weight:600;line-height:1.65}.literature-group-heading p{margin:4px 0 0;font-size:13px;color:#7b8797}.topic-literature-list{display:grid;gap:12px}.more-literature{margin-top:10px}.more-literature[open]>.topic-literature-list{margin-top:10px}.theme-query-records{margin-top:2px}.theme-query-records>summary{color:#78869a;font-size:13px}.theme-query-records .query-list b{font-size:14px;font-weight:500}.theme-query-records .query-list p,.theme-query-records .query-list article>span{font-size:12px}.theme-query-records .query-list strong{font-size:13px}
.topic-retrieval-panel{box-sizing:border-box;width:100%;min-width:0}.topic-retrieval-panel .literature-group-heading{margin-bottom:8px}.topic-retrieval-panel .topic-literature-list{display:grid;grid-template-columns:minmax(0,1fr);width:100%;gap:0}.more-literature>summary{display:flex;align-items:center;gap:5px;list-style:none}.more-literature>summary::-webkit-details-marker{display:none}.more-literature>summary .material-icons-outlined{font-size:18px;transition:transform .15s}.less-literature-label{display:none}.more-literature[open]>summary .more-literature-label{display:none}.more-literature[open]>summary .less-literature-label{display:inline}.more-literature[open]>summary .material-icons-outlined{transform:rotate(180deg)}
.candidate-grid{gap:18px}.candidate-card{padding:22px 24px;border:1px solid #e0e7f0;border-radius:12px;box-shadow:0 2px 6px #253e5e04}.candidate-card>header{grid-template-columns:36px minmax(0,1fr);gap:14px}.candidate-card>header>span{width:36px;height:36px;border-radius:9px;color:#4867b9;background:#eef3fc;font-size:15px;font-weight:600}.candidate-card header small{display:inline-block;padding:2px 9px;color:#4364ad;background:#edf3ff;border-radius:4px;font-size:12px;line-height:1.7}.candidate-card.direction-exploratory header small{color:#8c6c30;background:#fbf4e5}.candidate-card.direction-exploratory>header>span{color:#957333;background:#fbf5e9}.candidate-card.direction-candidate header small{color:#65758a;background:#f0f3f7}.candidate-card h4{margin:7px 0 0;font-size:18px;font-weight:600;line-height:1.6;color:#233b57}.candidate-card .question{margin:14px 0 8px;color:#52657b;font-size:15px;line-height:1.85}.candidate-card .candidate-design{margin-top:16px;border-top:1px solid #e8edf3}.candidate-card summary{padding:12px 0;color:#2465ae;font-size:15px;font-weight:500;line-height:1.6}.candidate-rationale{display:grid;grid-template-columns:150px minmax(0,1fr);gap:18px;padding:15px 0;margin:0;border-bottom:1px solid #edf0f5}.candidate-rationale h5{margin:0;padding-top:2px;font-size:14px;font-weight:500;color:#738297}.candidate-rationale p{margin:0;color:#435971;font-size:15px;line-height:1.85}.candidate-card dl{grid-template-columns:1fr;gap:0}.candidate-card dl div{display:grid;grid-template-columns:150px minmax(0,1fr);gap:18px;padding:15px 0;border:0;border-bottom:1px solid #edf0f5;background:transparent}.candidate-card dt{padding-top:2px;font-size:14px;color:#738297}.candidate-card dd{margin:0;color:#435971;font-size:15px;line-height:1.85}.candidate-evidence{padding-top:18px}.candidate-evidence h5{font-size:15px;font-weight:600;color:#4f6580}.candidate-card footer span{font-size:13px;line-height:1.8;padding:5px 9px}.candidate-card .muted{font-size:14px;text-align:left;background:transparent;padding:5px 0;color:#738297}
@media(min-width:1101px){.task-head h1{white-space:nowrap}}
@media(max-width:1100px){.task-head h1{font-size:24px}.task-head{gap:16px;flex-wrap:wrap}.task-head>div:first-child{flex-basis:80%}}
@media(max-width:720px){.task-head h1{font-size:22px;white-space:normal}.task-head>div:first-child{flex-basis:auto}.head-actions{padding-top:0}.literature-group-heading h4{font-size:16px}.candidate-card{padding:17px 15px}.candidate-card h4{font-size:17px}.candidate-card dl div,.candidate-rationale{grid-template-columns:1fr;gap:5px;padding:12px 0}.candidate-card dd{font-size:15px}.candidate-card .question{font-size:15px}.panel>header{align-items:flex-start}.panel>header small{white-space:nowrap}.group-number{width:30px;height:30px}.readable-results h4{font-size:16px}}
</style>
