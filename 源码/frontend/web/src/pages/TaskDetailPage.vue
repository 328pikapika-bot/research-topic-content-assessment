<template>
  <div class="task-detail-page" v-cloak>
    <section v-if="isLoading" class="modern-task-workspace">
      <div class="modern-card modern-loading-state"><span class="workspace-spinner"></span><div><h1>正在同步任务详情</h1><p>正在载入研究要点、相关文献和评估结果。</p></div></div>
    </section>
    <section v-else-if="loadError" class="modern-task-workspace">
      <div class="modern-card modern-error-state"><span class="material-icons-outlined" aria-hidden="true">error_outline</span><div><h1>任务暂时无法打开</h1><p>{{ loadError }}</p><button type="button" class="workspace-action workspace-action--primary" @click="handleLoadRetry">重新加载</button></div></div>
    </section>
    <section v-else-if="taskDetail" class="modern-task-workspace">
      <div class="modern-task-workspace__heading">
        <div>
          <span class="modern-eyebrow">NOVELTY ASSESSMENT · 创新性评估任务</span>
          <h1>{{ taskDetailTitle }}</h1>
          <p>先阅读评估结论，再按要点查看文献和依据。</p>
        </div>
        <span class="workspace-status" :class="`workspace-status--${taskStatus}`">{{ statusLabel }}</span>
      </div>

      <section v-if="canRetryGeneration" class="modern-card modern-generation-recovery" role="status">
        <div><h2>生成中断</h2><p>{{ generationRecoveryMessage }}</p><p v-if="retryError" role="alert">{{ retryError }}</p></div>
        <button type="button" class="workspace-action workspace-action--primary" :disabled="retryLoading" @click="handleContinueGenerationAfterQuality">{{ retryLoading ? '正在恢复…' : '重试当前步骤' }}</button>
      </section>

      <details class="modern-card modern-task-context modern-task-input">
        <summary>查看完整材料与研究边界</summary>
        <div class="modern-task-context__query">{{ taskDetail.raw_user_input || taskDetail.title }}</div>
        <p v-if="taskOverview.scopeBoundary">研究边界：{{ taskOverview.scopeBoundary }}</p>
        <div class="modern-meta-list"><span>文献年份：{{ taskYearRange }}</span><span>创建时间：{{ formatDateTime(taskDetail.created_at) }}</span></div>
        <div v-if="reportDocuments.length" class="modern-file-list"><span v-for="doc in reportDocuments" :key="doc.name" class="modern-file-chip">{{ doc.name }}</span></div>
        <div class="modern-domain-grid">
          <div><span>研究领域</span><strong>{{ taskOverview.researchDomain }}</strong></div>
          <div><span>核心对象</span><strong>{{ taskOverview.researchObject }}</strong></div>
          <div><span>关键技术</span><strong>{{ taskOverview.keyTechnologies }}</strong></div>
        </div>
        <section v-if="reportNineFieldEntries.some(([, value]) => value)" class="modern-nine-fields">
          <h3>材料信息</h3>
          <div class="modern-nine-fields__grid"><article v-for="([key, value]) in reportNineFieldEntries.filter(([, value]) => value)" :key="key"><span>{{ nineFieldLabel(key) }}</span><p>{{ value }}</p></article></div>
        </section>
      </details>

      <section class="modern-card modern-workflow-card">
        <div class="modern-card__heading"><div><span class="modern-card__eyebrow">评估流程</span><h2>{{ userSteps.length }}个评估阶段</h2></div><span class="modern-card__hint">{{ completedUserStepCount }}/{{ userSteps.length }} 阶段完成 · {{ reportProgressPercentage }}%</span></div>
        <div class="modern-stage-strip" :aria-label="`${userSteps.length}阶段评估流程`">
          <button v-for="(step, index) in userSteps" :key="step.key" type="button"
                  :class="[{ active: selectedUserStep.key === step.key }, `is-${step.status}`]"
                  :aria-current="selectedUserStep.key === step.key ? 'step' : undefined"
                  @click="selectUserStep(step.key)">
            <span><i><span v-if="step.status === 'completed'" class="material-icons-outlined">check</span><span v-else>{{ index + 1 }}</span></i><em>{{ stageStatusLabel(step.status) }}{{ stepDurationLabel(step) }}</em></span>
            <strong>{{ step.title }}</strong>
          </button>
        </div>
      </section>
      <StageRunNotice :notice="runNotice" />
      <section class="modern-card modern-stage-description"><div class="assessment-stage-heading"><h2>{{ selectedUserStep.title }}</h2><span>{{ stageStatusLabel(selectedUserStep.status) }}{{ stepDurationLabel(selectedUserStep) }}</span></div><p>{{ selectedUserStep.description }}</p></section>

      <section v-if="selectedUserStep.key === 'material'" class="modern-card modern-stage-output">
        <h3>材料概括</h3>
        <p>{{ materialOverview }}</p>
        <p v-if="taskState?.output_json?.input_depth_notice" class="modern-material-notice" role="status">{{ taskState.output_json.input_depth_notice }}</p>
        <p>{{ reportDocuments.length ? `已登记 ${reportDocuments.length} 份材料` : '依据提交的文字材料' }}，识别 {{ taskState?.claims?.length || 0 }} 项研究要点。</p>
        <details v-if="materialObservations.length"><summary>材料中的目标与观测记录</summary><article v-for="(item, index) in materialObservations" :key="index"><p>{{ item.text }}</p><small>{{ item.kind === 'target' ? '计划目标，尚非实测' : item.kind === 'reported' ? '材料报告记录，未独立核验' : '目标与记录分别核验' }} · {{ materialLocationLabel(item.source_locator) }}</small><p>{{ item.comparison_note }}</p></article></details>
        <details v-if="materialRequirements.length"><summary>项目与配套指南要求</summary><article v-for="item in materialRequirements" :key="item.requirement_id"><p>{{ item.text }}</p><p>项目应答：{{ item.response_excerpt || '尚未找到可定位应答' }}</p><small>{{ item.comparison_note }}</small></article></details>
        <div class="assessment-point-list"><section v-for="claim in taskState?.claims || []" :key="claim.claim_id" class="assessment-point"><header><h4>{{ researchPointName(claim) }}</h4><span>{{ claimTypeLabel(claim.claim_type) }}</span></header><p><InlineCitations :text="claim.text" :sources="citationSources" /></p><ResultDisclosure v-if="hasUploadedClaimSource(claim, taskState?.uploaded_file_context?.files)" label="查看材料定位" collapse-label="收起材料定位"><p>{{ claimSourceLabel(claim) }}</p></ResultDisclosure></section></div>
      </section>
      <section v-for="activeStage in activeStepStages" :key="activeStage.stage_key" class="modern-card modern-stage-output">
        <h3 v-if="activeStepStages.length > 1 && activeStage.stage_key !== 'landscape'">{{ stageArtifactTitle(activeStage.stage_key) }}</h3>
        <section class="modern-stage-artifacts">
          <div v-if="activeStage.stage_key === 'retrieval'" class="modern-evidence-groups">
            <details v-if="taskState?.query_plans?.length"><summary>查看检索主题与检索式</summary><article v-for="(query, index) in taskState.query_plans" :key="index"><h4>{{ query.query_group || '相关主题' }}</h4><p>{{ query.query }}</p><p v-if="query.rationale">{{ query.rationale }}</p></article></details>
            <section v-for="group in claimLiteratureGroups" :key="group.key" class="modern-artifact-section assessment-literature-group"><h4>{{ group.title }} <small>{{ group.items.length }} 篇文献</small></h4><div class="assessment-literature-rows"><LiteratureCard v-for="item in group.items.slice(0, 2)" :key="item.evidence_id" layout="row" :item="item" :chunks="taskState?.evidence_chunks || []" /></div><ResultDisclosure v-if="group.items.length > 2" :label="`展开其余文献（${group.items.length - 2} 篇）`" collapse-label="收起其余文献"><div class="assessment-literature-rows"><LiteratureCard v-for="item in group.items.slice(2)" :key="item.evidence_id" layout="row" :item="item" :chunks="taskState?.evidence_chunks || []" /></div></ResultDisclosure></section>
            <div v-if="!evidenceItems.length" class="modern-visual-empty">正在整理各要点的相关文献。</div>
          </div>
          <div v-else-if="activeStage.stage_key === 'comparison'" class="modern-comparison-groups">
            <ClaimEvidenceGraph :state="taskState || {}" :selected="selectedComparisonPoint" @select="selectComparisonPoint" />
            <section v-for="group in comparisonGroups" :id="`comparison-${group.key}`" :key="group.key" class="modern-artifact-section" :class="{ 'selected-comparison-point': group.key === selectedComparisonPoint }">
              <h4>{{ group.title }}</h4><p v-if="group.conclusion"><InlineCitations :text="analysisParagraphs(group.conclusion).lead" :sources="citationSources" /></p>
              <ResultDisclosure label="查看详细分析" collapse-label="收起详细分析">
                <div class="assessment-comparison-detail">
                  <section v-if="group.analysis.material || claimForId(group.key)?.text" class="assessment-comparison-section">
                    <h5>材料中的研究内容</h5><p v-for="(paragraph, index) in researchParagraphs(group.analysis.material || claimForId(group.key)?.text)" :key="index">{{ paragraph }}</p>
                  </section>
                  <section v-if="group.sources.length" class="assessment-comparison-section">
                    <h5>相关研究与比较</h5>
                    <p v-for="(paragraph, index) in group.analysis.literature.flatMap(researchParagraphs)" :key="index" class="assessment-research-synthesis"><InlineCitations :text="paragraph" :sources="citationSources" /></p>
                    <section v-for="row in comparisonRows(group.key)" :key="row.source.evidence_id" class="assessment-comparison-source">
                      <LiteratureReferences :items="[row.source]" show-abstract />
                      <div v-for="section in comparisonSections(row.analysis)" :key="section.label" class="assessment-comparison-prose">
                        <h6>{{ section.label }}</h6><p v-for="(paragraph, index) in section.paragraphs" :key="index"><InlineCitations :text="paragraph" :sources="citationSources" /></p>
                      </div>
                    </section>
                  </section>
                  <section v-if="analysisParagraphs(group.conclusion).rest" class="assessment-comparison-section">
                    <h5>研究分析</h5>
                    <p><InlineCitations :text="analysisParagraphs(group.conclusion).rest" :sources="citationSources" /></p>
                  </section>
                </div>
              </ResultDisclosure>
            </section>
            <div v-if="!comparisonGroups.length" class="modern-visual-empty">正在整理逐项比较。</div>
          </div>
          <div v-else-if="activeStage.stage_key === 'assessment'" class="assessment-point-list">
            <section v-for="group in comparisonGroups" :key="group.key" class="assessment-point">
              <h4>{{ group.title }}</h4>
              <section v-for="item in pointEvaluations(group.key)" :key="item.label" class="assessment-dimension">
                <h5>{{ item.label }}</h5><p><InlineCitations :text="item.lead" :sources="citationSources" /></p>
                <ResultDisclosure v-if="item.rest" label="展开详细分析" collapse-label="收起详细分析"><p><InlineCitations :text="item.rest" :sources="citationSources" /></p></ResultDisclosure>
                <ResultDisclosure v-if="item.sources.length" label="查看相关文献" collapse-label="收起相关文献"><LiteratureReferences :items="item.sources" /></ResultDisclosure>
              </section>
              <p v-if="!pointEvaluations(group.key).length" class="assessment-evaluation-pending">{{ isTaskInProgress ? '正在结合研究材料与相关文献形成逐项评价，完成后将在此显示。' : '逐项评价尚未形成，已有材料与比对内容已保留，请查看任务状态继续评价。' }}</p>
            </section>
          </div>
          <div v-else-if="activeStage.stage_key === 'landscape'" class="modern-landscape-output"><h3>研究趋势</h3><div v-if="frozenTrendSeries.length" class="assessment-trend-figure"><LiteratureTrendChart :series="frozenTrendSeries" /><p class="assessment-figure-caption">按报告引用文献统计年份分布，反映本次检索样本。</p></div><div class="assessment-trend-analysis"><p v-for="(paragraph, index) in landscapeParagraphs" :key="index"><InlineCitations :text="paragraph" :sources="citationSources" /></p></div></div>
          <template v-else-if="activeStage.stage_key === 'report'">
            <section v-if="reportDisplayState === 'published'" class="modern-summary-card">
              <div class="modern-card__heading"><div><span class="modern-card__eyebrow">综合结果</span><h3>创新性评估结论</h3></div><ReportDownloadActions :docx-loading="isReportDownloading('docx')" :pdf-loading="isReportDownloading('pdf')" :md-loading="isReportDownloading('md')" @download="handleReportDownload" /></div>
              <p class="modern-summary-card__text">{{ compactReportSummary }}</p>
              <details v-if="(assessmentReport?.executiveSummary?.length || 0) > 260"><summary>阅读完整评估摘要</summary><p class="modern-summary-card__text">{{ assessmentReport?.executiveSummary }}</p></details>
              <p>{{ assessmentReport?.pageCountVerified ? `报告实际 ${assessmentReport.actualPageCount} 页` : '报告已生成' }} · {{ assessmentReport?.claimCount || 0 }} 项研究要点。</p>


            </section>
            <section v-else class="modern-running-card" role="status"><div class="modern-running-card__icon"><span class="material-icons-outlined">autorenew</span></div><div><h3>{{ reportStatusTitle }}</h3><p>{{ reportStatusDescription }}</p></div></section>


          </template>
        </section>


      </section>

      <section v-if="selectedUserStep.key === 'report' && reportDisplayState === 'published'" class="modern-report-reader modern-report-reader--pdf">
        <ReportPdfPreview :task-id="taskDetail.task_id" :title="assessmentReport?.title || taskDetailTitle" :version="assessmentReport?.version" :downloading="isReportDownloading('pdf')" @download="handleReportDownload('pdf')" @loaded="loadTask({ showLoading: false })" />
      </section>
    </section>
    <section v-else class="modern-task-workspace"><div class="modern-card modern-error-state"><span class="material-icons-outlined" aria-hidden="true">folder_off</span><div><h1>任务不存在</h1><p>该任务可能已被删除或当前账号没有访问权限。</p><button type="button" class="workspace-action" @click="goBack">返回上一层</button></div></div></section>

    <Teleport to="body">
      <div v-if="showClarificationDialog && clarificationState?.needed" class="gq-dialog-overlay" @click.self="!clarificationSubmitting && (showClarificationDialog = false)">
        <div class="gq-clarify-dialog">
          <div class="gq-clarify-dialog__head">
            <span class="ui-icon ui-icon-forum" aria-hidden="true"></span>
            <h2>{{ isReportRecovery ? '继续研究分析' : isClaimExtractionConfirmation ? '技术要点提取恢复' : '评估边界确认' }}</h2>
            <button class="gq-clarify-dialog__close" type="button" @click="showClarificationDialog = false" aria-label="关闭">
              <span class="ui-icon ui-icon-close" aria-hidden="true"></span>
            </button>
          </div>
          <p class="gq-clarify-dialog__question">{{ isMissingFields ? missingInputPrompt : clarificationState.question }}</p>
          <div v-if="isObjectConflict" class="gq-clarify-settings">
            <p>每个任务只评估一个研究对象。勾选要移除的文件；正常附件将保留。</p>
            <label v-for="file in taskDetail?.state?.assessment_profile.document_records" :key="file.document_id"><input v-model="removedFileIds" type="checkbox" :value="file.document_id" />移除 {{ file.name }}</label>
          </div>
          <div v-if="isMissingFields">
            <label>补充评估材料<textarea v-model="supplementalInput" class="gq-clarify-custom-input" rows="5" maxlength="12000" placeholder="在此补充提示中需要的研究信息，原材料会保留，无需重复上传。" :disabled="clarificationSubmitting" /></label>
          </div>
          <p v-if="!isObjectConflict && !isMissingFields" class="gq-clarify-dialog__hint">{{ isReportRecovery ? '材料和已有分析已保存，重试后从相应步骤继续。' : isClaimExtractionConfirmation ? '原文件已保留。可重试提取，也可逐条填写原文；系统核对文件和定位后继续。' : '系统已按任务描述和材料自动确定评估边界。如无补充说明，可直接继续评估。' }}</p>
          <div v-if="isClaimExtractionConfirmation" class="gq-choice-grid">
            <button v-for="option in clarificationState.options" :key="option.value" class="gq-choice"
                    :class="{ active: option.value === pendingClarificationOption }" type="button"
                    :disabled="clarificationSubmitting"
                    @click="selectClarificationOption(option.value)">
              <strong>{{ option.label }}</strong>
            </button>
          </div>
          <div v-if="isClaimExtractionConfirmation && pendingClarificationOption === 'confirm_claims'">
            <div v-for="(claim, index) in manualClaims" :key="index" class="gq-clarify-settings gq-claim-source-row">
              <label>要点 {{ index + 1 }} 来源<select :value="claimSourceValue(claim)" :disabled="clarificationSubmitting" @change="updateClaimSource(claim, ($event.target as HTMLSelectElement).value)"><option value="">请选择材料</option><optgroup v-if="taskDetail?.state?.assessment_profile.document_records?.length" label="上传材料"><option v-for="file in taskDetail.state.assessment_profile.document_records" :key="`material:${file.document_id}`" :value="`material:${file.document_id}`">{{ file.name }}</option></optgroup><optgroup v-if="evidenceItems.length" label="检索文献"><option v-for="item in evidenceItems" :key="`evidence:${item.evidence_id}`" :value="`evidence:${item.evidence_id}`">{{ item.title }}</option></optgroup></select></label>
              <textarea v-model="claim.source_span" class="gq-clarify-custom-input" rows="3" placeholder="从所选材料或检索文献完整复制要点原文，保留标点和单位。" :disabled="clarificationSubmitting" />
              <button type="button" class="workspace-action gq-claim-row-action" :disabled="clarificationSubmitting" @click="manualClaims.splice(index, 1)"><span class="material-icons-outlined">remove_circle_outline</span>移除此条</button>
            </div>
            <button type="button" class="workspace-action gq-claim-row-action" :disabled="clarificationSubmitting" @click="manualClaims.push(emptyManualClaim())"><span class="material-icons-outlined">add</span>添加要点</button>
          </div>
          <p v-if="clarificationError" class="gq-clarify-error" role="alert">{{ clarificationError }}</p>
          <label v-if="!isClaimExtractionConfirmation && !isObjectConflict && !isMissingFields && !['report_drafting', 'assessment_analysis'].includes(clarificationState?.reason || '')" class="gq-clarify-boundary-note">
            <span>{{ isReportRecovery ? '研究对象与方法关键词' : '补充边界说明' }} <small>选填</small></span>
            <textarea v-model="customClarificationInput" class="gq-clarify-custom-input" rows="3" maxlength="500"
                      placeholder="如需限定对象、技术、时间或应用场景，可在此补充" :disabled="clarificationSubmitting"
                      @input="handleCustomClarificationInput" />
          </label>
          <div class="gq-clarify-dialog__actions">
            <button v-if="isClaimExtractionConfirmation" class="gq-clarify-dialog__cancel" type="button"
                    @click="showClarificationDialog = false; pendingClarificationOption = ''; customClarificationInput = ''"
                    :disabled="clarificationSubmitting">取消</button>
            <button class="gq-clarify-dialog__confirm" type="button"
                    :disabled="(isClaimExtractionConfirmation && !pendingClarificationOption) || clarificationSubmitting"
                    @click="confirmClarification">
              {{ clarificationSubmitting ? '提交中...' : isMissingFields ? '补充并继续' : clarificationState?.reason === 'assessment_analysis' ? '继续评价' : isReportRecovery ? (clarificationState?.reason === 'report_sources' ? '重新检索并继续' : '重新生成报告') : '已确认，继续评估' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { researchText, researchPointName, pointLiteratureOrder } from '../services/researchPresentation'
import { emptyNineDraft, mergeNineDraft, missingNineFields, requiredNineFieldKeys, type SelectedAssessmentType } from '../services/homepageDraft'
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { uploadPresentation, associatedLiteratureCount } from '../services/uploadPresentation'
import {
  downloadContentAssessmentReport,
  getContentAssessmentTask,
  resumeContentAssessment,
  stopContentAssessmentTask,
  submitContentAssessmentConfirmation,
  subscribeToTaskStream,
  type ContentAssessmentTaskDetail,
  type ContentAssessmentRunResponse,
  type ProcessTraceStep,
  type StageTraceItem,
  type FinalQuestion,
  type EvidenceItem,
  type ContentAssessmentReport,
} from '../services/contentAssessmentUiApi'
import { getAuthManager, requirePlatformLogin } from '../services/nstlAuth'
import { useNstlLiteratureCollection } from '../composables/useNstlLiteratureCollection'
import { errorKind, trackEvent, trackOutcome } from '../tracker/contentAssessmentTracker'
import { useToast } from '../composables/useToast'
import { resolveEvidenceUrl } from '../services/evidenceLinks'
import ReportPdfPreview from '../components/ReportPdfPreview.vue'
import ReportDownloadActions from '../components/ReportDownloadActions.vue'
import { buildClaimEvidencePresentation, evidenceSourceLabel } from '../services/assessmentEvidencePresentation'
import EvidenceSummary from '../components/EvidenceSummary.vue'
import LiteratureCard from '../components/LiteratureCard.vue'
import LiteratureReferences from '../components/LiteratureReferences.vue'
import InlineCitations from '../components/InlineCitations.vue'
import ResultDisclosure from '../components/ResultDisclosure.vue'
import { analysisParagraphs, assessmentCitationSources, assessmentTaskTitle, hasUploadedClaimSource, researchEvaluations, retrievalLiteratureForPoint, relatedResearchAnalysis, comparisonLiteratureLinks } from '../services/assessmentPagePresentation'
import LiteratureTrendChart from '../components/LiteratureTrendChart.vue'
import ClaimEvidenceGraph from '../components/ClaimEvidenceGraph.vue'
import { assessmentTrendSeries } from '../services/resultSnapshot'
import { aggregateSteps, stepDurationLabel, ASSESSMENT_STEPS, stepForStage, userStepStatusLabel, stageRunPresentation } from '../services/userSteps'
import StageRunNotice from '../components/StageRunNotice.vue'
import { followTaskProgress } from '../services/taskProgress'
import { comparisonResearchAnalysis, readerComparisonFor } from '../services/comparisonEvidencePresentation'
import { evidenceSummaries } from '../services/evidenceSummary'
import { retrievalInfo, decisionSources as presentDecisionSources, frozenPointResult } from '../services/stageEvidencePresentation'
import { assessmentPreviewMessage } from '../services/assessmentInput'
import '../styles/assessment-results.css'

const props = defineProps<{
  taskId: string
  mode?: 'running' | 'detail'
  fromTaskList?: boolean
  initialRun?: ContentAssessmentRunResponse | null
  initialDetail?: ContentAssessmentTaskDetail | null
  workspaceCommand?: { type: 'clarify' | 'continue' | 'retry' | 'stop' | 'download'; key: number } | null
}>()

function assetUrl(path: string): string {
  return `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`
}
const emit = defineEmits<{ navigate: [page: string, taskId?: string]; 'workspace-meta': [meta: { status: string; running: boolean }] }>()
const { success: showSuccess, error: showError } = useToast()

const CONTENT_ASSESSMENT_STAGES = ASSESSMENT_STEPS.flatMap(step => step.stages.map(key => ({ key, title: step.title, desc: step.description })))

// Controlled Chinese labels used by the thinking panel. These labels are
// intentionally independent from model-generated step titles so older tasks
// containing English/technical names remain readable.
const CONTENT_ASSESSMENT_PROCESS_STEPS: Record<string, string[]> = {
  document_intake: ['文件解析', '章节与页码索引', '材料角色识别', '评估边界整理', '材料质量检查'],
  claim_extraction: ['九字段抽取', '工作包拆分', '原子要点拆分', '指标结构化', '指南要求拆分'],
  retrieval: ['查询组规划', '术语扩展', '分组检索', '去重与排序', '证据片段提取'],
  comparison: ['要点匹配', '同口径字段比较', '支持与反证识别', '证据等级判定', '证据覆盖统计'],
  assessment: ['要点判定', '指标验证', '可行性分析', '风险识别', '评分与改写建议'],
  landscape: ['年份分布统计', '主题聚类', '方法路线归纳', '竞争态势观察', '监管边界核验'],
  quality_gate: ['材料完整性检查', '证据覆盖率检查', '引用完整性检查', '重复与空洞检查', '章节配额检查'],
  report: ['章节配额规划', '分批生成报告章节', '引用校验', '报告版本固化', '多格式报告导出'],
}

const isLoading = ref(false)
const loadError = ref('')
const taskDetail = ref<ContentAssessmentTaskDetail | null>(null)
const sseCleanup = ref<(() => void) | null>(null)
let literatureProgressInterval: ReturnType<typeof setInterval> | null = null
let literatureProgressTimer: ReturnType<typeof setTimeout> | null = null
const isThinkingExpanded = ref(false)
const expandedStages = ref(new Set<string>())
const autoExpandedStageKey = ref('')
const manualExpandedStages = ref(new Set<string>())
const highlightedEvidenceId = ref('')
const literatureTooltip = ref({ visible: false, text: '', left: 0, top: 0 })
const retryLoading = ref(false)
const stopLoading = ref(false)
const showClarificationDialog = ref(false)
const clarificationAutoShown = ref(false)
const pendingClarificationOption = ref('')
const customClarificationInput = ref('')
const clarificationSubmitting = ref(false)
const isObjectConflict = computed(() => taskDetail.value?.state?.confirmation?.reason === 'multiple_objects')
const isMissingFields = computed(() => taskDetail.value?.state?.confirmation?.reason === 'missing_fields')
const removedFileIds = ref<string[]>([])
const supplementalInput = ref('')
const missingInputPrompt = computed(() => {
  const state = taskDetail.value?.state
  const missing = state?.output_json?.missing_fields
  return Array.isArray(missing) ? assessmentPreviewMessage({ resolved_artifact_type: state?.report_preferences?.resolved_artifact_type, missing_fields: missing.map(String) }) : '请在下方补充任务提示中所需的信息，原文字和附件会保留。'
})
watch(() => taskDetail.value?.state?.confirmation?.needed, needed => {
  if (needed) {
    removedFileIds.value = []
    supplementalInput.value = ''
  }
})
const isClaimExtractionConfirmation = computed(() => taskDetail.value?.state?.confirmation?.reason === 'claim_extraction')
const reportedStatusKeys = ref(new Set<string>())
const expandedAbstractIds = ref(new Set<string>())
const literatureProgressPercentage = ref(0)
const reportDownloadFormats = ref(new Set<string>())
const clarificationError = ref('')
const selectedStageKey = ref('')
const expandedNineFields = ref(new Set<string>())
function toggleNineField(key: string) { const next = new Set(expandedNineFields.value); next.has(key) ? next.delete(key) : next.add(key); expandedNineFields.value = next }
const durationTick = ref(Date.now())
let durationTimer: number | undefined
let pendingAuthenticatedAction: (() => void) | null = null

const isSampleMode = computed(() => props.taskId?.startsWith('sample_'))
const fromTaskList = computed(() => Boolean(props.fromTaskList))
const taskStatus = computed(() => taskDetail.value?.status || '')
const taskStatusClass = computed(() => {
  const s = taskStatus.value
  return {
    'is-running': s === 'running' || s === 'queued' || s === 'awaiting_clarification',
    'is-failed': s === 'failed' || s === 'time_out',
    completed: s === 'completed',
    running: s === 'running' || s === 'queued' || s === 'awaiting_clarification',
    failed: s === 'failed' || s === 'time_out',
    cancelled: s === 'cancelled',
    waiting: s === 'awaiting_quality_continue',
  }
})
const statusLabel = computed(() => {
  const m: Record<string, string> = { completed: '已完成', running: '进行中', queued: '排队中', failed: '失败', time_out: '已超时', cancelled: '已取消', awaiting_clarification: '待澄清', awaiting_quality_continue: '待继续生成' }
  return m[taskStatus.value] || taskStatus.value || '未知'
})

const isTaskInProgress = computed(() => ['running', 'queued', 'awaiting_clarification'].includes(taskStatus.value))
// Running and historical detail pages share the same task card so users can
// always see the identifier, timing and current stage while the stream updates.
const showTaskMeta = computed(() => Boolean(taskDetail.value))
const taskState = computed(() => taskDetail.value?.state)
const citationSources = computed(() => assessmentCitationSources(taskState.value?.report_snapshot?.citation_numbers, taskState.value?.evidence_records || []))
const materialObservations = computed<any[]>(() => (taskState.value as any)?.material_observations || (taskState.value?.output_json as any)?.material_observations || [])
function materialLocationLabel(locator: Record<string, unknown> | string | undefined) {
  if (!locator || typeof locator === 'string') return locator || '原文位置未提供'
  const labels: Record<string, string> = { page: '页', line: '行', paragraph: '段', row: '表格行', sheet: '工作表', section: '章节' }
  return Object.entries(locator).filter(([key, value]) => key in labels && String(value || '').trim()).map(([key, value]) => `${labels[key]} ${value}`).join('，') || '原文位置未提供'
}
const materialRequirements = computed<any[]>(() => ((taskState.value as any)?.guideline_requirements || []).filter((item: any) => item.source_document_id && item.source_span))
const automaticAnalysisOpen = ref(false)
const materialResultsOpen = ref<boolean | null>(null)
const expandedReportClaims = ref(new Set<string>())
const uploadInfo = computed(() => uploadPresentation(taskState.value, taskState.value?.assessment_profile?.document_records || []))
function setReportClaimExpanded(id: string, open: boolean) {
  const updated = new Set(expandedReportClaims.value)
  if (open) updated.add(id); else updated.delete(id)
  expandedReportClaims.value = updated
}
const clarificationState = computed(() => taskState.value?.clarification)
const retryError = ref('')
const canRetryGeneration = computed(() => taskStatus.value === 'failed' && Boolean(taskState.value?.output_json?.generation_recovery || ['report_sources', 'report_drafting', 'assessment_analysis', 'claim_extraction'].includes(clarificationState.value?.reason || '')))
const generationRecoveryMessage = computed(() => String(taskState.value?.output_json?.generation_recovery?.message || '已有分析和材料已保存，可从中断步骤重试。'))
const qualityJudgement = computed(() => taskState.value?.quality_judgement ?? taskState.value?.output_json?.quality_judgement ?? taskDetail.value?.output_json?.quality_judgement)
const needsClarification = computed(() => (
  taskStatus.value === 'awaiting_clarification' && clarificationState.value?.needed === true
))

const displaySteps = computed(() => {
  const currentKey = taskState.value?.current_stage || ''
  const firstPending = stageTraces.value.find((item) => item.status === 'pending')?.stage_key || ''
  return CONTENT_ASSESSMENT_STAGES.map((s) => {
    const trace = stageTraces.value.find((item) => item.stage_key === s.key)
    const rawStatus = trace?.status || 'pending'
    const isActivePending = isTaskInProgress.value && rawStatus === 'pending' && (s.key === currentKey || (!currentKey && s.key === firstPending))
    return { id: s.key, name: s.title, status: isActivePending ? 'running' : rawStatus, summary: trace?.summary || '' }
  })
})

const finalQuestions = computed<FinalQuestion[]>(() => (
  taskState.value?.final_questions
  || taskState.value?.output_json?.final_questions
  || taskDetail.value?.output_json?.final_questions
  || []
))
const assessmentReport = computed<ContentAssessmentReport | undefined>(() => (
  taskState.value?.report
  || taskState.value?.output_json?.report
  || taskDetail.value?.output_json?.report
))
const compactReportSummary = computed(() => {
  const summary = assessmentReport.value?.executiveSummary || '评估结论已生成。'
  if (summary.length <= 260) return summary
  const boundary = summary.lastIndexOf('。', 260)
  return boundary >= 80 ? summary.slice(0, boundary + 1) : summary.slice(0, 240) + '…'
})
const claimPage = ref(1)
const claimPageSize = 8
const reportClaims = computed(() => assessmentReport.value?.claims || [])
const reportClaimRelationCounts = computed(() => ({
  supported: reportClaims.value.filter(claim => ['direct', 'partial'].includes(claim.evidenceRelation.status)).length,
  related: reportClaims.value.filter(claim => claim.evidenceRelation.status === 'related').length,
  attention: reportClaims.value.filter(claim => ['unmatched', 'conflicting'].includes(claim.evidenceRelation.status)).length,
}))
const claimPageCount = computed(() => Math.max(1, Math.ceil(reportClaims.value.length / claimPageSize)))
const claimPageStart = computed(() => reportClaims.value.length ? (claimPage.value - 1) * claimPageSize + 1 : 0)
const claimPageEnd = computed(() => Math.min(claimPage.value * claimPageSize, reportClaims.value.length))
const pagedReportClaims = computed(() => reportClaims.value.slice((claimPage.value - 1) * claimPageSize, claimPage.value * claimPageSize))
watch(claimPageCount, (count) => { claimPage.value = Math.min(claimPage.value, count) })
const NINE_FIELD_LABELS: Record<string, string> = { research_direction: '研究方向', research_goal: '研究目标', research_content: '研究内容', technical_route: '技术路线', key_technologies: '关键技术', evaluation_indicators: '考核指标', timeline: '时间节点', application_scenarios: '应用场景', expected_outcomes: '预期成果' }
function nineFieldLabel(key: string) { return NINE_FIELD_LABELS[key] || key }
const assessmentArtifactType = computed<SelectedAssessmentType>(() => {
  const value = taskDetail.value?.state?.report_preferences?.resolved_artifact_type
  return value === 'paper' || value === 'guideline' ? value : 'project'
})
const materialOverview = computed(() => {
  const fields = taskState.value?.nine_fields || {}
  return [fields.research_direction, fields.research_content, fields.research_goal].filter(Boolean).join('；').slice(0, 360) || (taskOverview.value.scopeBoundary || taskDetail.value?.raw_user_input || '材料概括正在生成。').slice(0, 360)
})
const reportNineFieldEntries = computed<[string, string][]>(() => Object.keys(NINE_FIELD_LABELS).map((key) => {
  const value = taskState.value?.nine_fields?.[key]
  return [key, Array.isArray(value) ? value.join('、') : String(value || '')] as [string, string]
}).filter(([key, value]) => value.trim() || requiredNineFieldKeys(assessmentArtifactType.value).includes(key as any)))
const nineFieldCompletedCount = computed(() => reportNineFieldEntries.value.filter(([, value]) => value.trim()).length)
const primaryMaterialLabel = computed(() => reportDocuments.value[0]?.name || '用户输入材料')
const CLAIM_TYPE_LABELS: Record<string, string> = { technical: '技术方案要点', innovation: '创新性要点', indicator: '量化指标', application: '应用要点', compliance: '规范与合规要求' }
const ASSERTION_TYPE_LABELS: Record<string, string> = { target: '目标值', planned: '计划开展', completed: '已有结果', unknown: '材料未说明' }
function claimTypeLabel(value?: string) { return CLAIM_TYPE_LABELS[value || ''] || '技术方案要点' }
function assertionTypeLabel(value?: string) { return ASSERTION_TYPE_LABELS[value || ''] || '材料未说明' }
const enrichedIndicators = computed(() => (taskState.value?.indicators || []).map((item: any) => ({
  ...item,
  validation: (taskState.value?.indicator_validations || []).find((entry: any) => entry.indicator_id === item.indicator_id),
  targetDisplay: item.value ? `${item.value}${item.unit || ''}` : '材料未提供量化目标',
})))
const indicatorAssessedCount = computed(() => enrichedIndicators.value.filter((item: any) => item.validation?.assessment_conclusion).length)
function indicatorEvidenceLabel(validation?: any) { return ({ comparable: '找到同口径基线', directional: '找到方向性依据', none: '未找到可比基线' } as Record<string, string>)[validation?.literature_baseline_status || ''] || '历史评估记录' }
function indicatorConclusionDisplay(validation?: any) {
  if (validation?.assessment_conclusion) return validation.assessment_conclusion
  return taskStatus.value === 'completed'
    ? '该历史版本未单独记录指标评估结论。'
    : '正在结合检索文献和项目材料形成指标判断。'
}
const STAGE_ARTIFACT_TITLES: Record<string, string> = {
  document_intake: '材料解析结果', claim_extraction: '材料信息提取结果', retrieval: '检索文献',
  comparison: '研究要点与相关文献', assessment: '创新性、前沿性与先进性判断', landscape: '研究现状与技术趋势',
  quality_gate: '质量检查结果', report: '报告与交付物',
}
function stageArtifactTitle(key: string) { return STAGE_ARTIFACT_TITLES[key] || '阶段结果' }
function claimIndex(claimId: string) { const index = (taskState.value?.claims || []).findIndex((item: any) => item.claim_id === claimId); return index >= 0 ? index + 1 : 0 }
function claimUserLabel(claimId: string) { return researchPointName(claimForId(claimId)) }
function evidenceIndex(evidenceId: string) { const index = evidenceItems.value.findIndex((item) => item.evidence_id === evidenceId); return index >= 0 ? index + 1 : 0 }
function evidenceUserLabel(item: EvidenceItem) { const index = evidenceIndex(item.evidence_id); return index ? `检索文献 ${index}` : '检索文献' }
function evidenceUserLabelById(evidenceId: string) { const index = evidenceIndex(evidenceId); return index ? `检索文献 ${index}` : '检索文献' }
function userFacingTraceText(value: string) {
  return String(value || '')
    .replace(/\bC-\d{3,}\b/g, (id) => claimUserLabel(id))
    .replace(/\bE-L-\d{3,}\b/g, (id) => evidenceUserLabelById(id))
}
function evidenceTitle(evidenceId: string) { return evidenceItems.value.find((item) => item.evidence_id === evidenceId)?.title || '来源文献' }
function formatAuthors(authors?: string[]) { return authors?.length ? authors.slice(0, 4).join('、') : '作者未提供' }
function evidenceLevelLabel(value?: string) { return ({ A: '全文可定位', B: '摘要级依据', C: '线索记录' } as Record<string, string>)[value || ''] || '证据级别待确认' }
function claimLabelsForEvidence(item: EvidenceItem) { return (item.claim_ids || []).map(claimUserLabel).filter((value, index, values) => values.indexOf(value) === index).slice(0, 4) }
const evidenceGroups = computed(() => {
  const groups = new Map<string, EvidenceItem[]>()
  evidenceItems.value.forEach((item) => {
    const name = item.query_group === '首页推荐案例核验文献' ? '案例核验文献' : item.query_group || '综合检索文献'
    groups.set(name, [...(groups.get(name) || []), item])
  })
  return [...groups.entries()].map(([name, items]) => ({ name, items }))
})
const claimLiteratureGroups = computed(() => {
  const claims = taskState.value?.claims || []
  const records = evidenceItems.value
  const groups = claims.map((claim: any) => ({ key: claim.claim_id, title: claimUserLabel(claim.claim_id),
    items: retrievalLiteratureForPoint(taskState.value || {}, claim.claim_id, pointLiteratureOrder(records, claim)) }))
  const groupedIds = new Set(groups.flatMap((group: { items: EvidenceItem[] }) => group.items.map((item: EvidenceItem) => item.evidence_id)))
  const remaining = records.filter(item => item.source_type === 'literature' && !groupedIds.has(item.evidence_id) && !item.claim_ids?.length && !(taskState.value?.claim_evidence_links || []).some((link: any) => link.evidence_id === item.evidence_id)).slice(0, 30)
  return remaining.length ? [...groups, { key: 'other', title: '其他检索文献', items: remaining }] : groups
})
function pointResult(claimId: string, judgement = false) {
  return frozenPointResult(taskState.value?.report_snapshot, claimId, judgement, '\n\n')
}
function comparisonLinks(claimId: string) {
  return comparisonLiteratureLinks(taskState.value || {}, claimId)
}
function pointEvaluations(claimId: string) {
  return researchEvaluations(taskState.value || {}, claimId).map(item => ({ ...item,
    sources: item.evidenceIds.flatMap(id => evidenceItems.value.find(record => record.evidence_id === id) || []),
  }))
}
const isReportRecovery = computed(() => ['report_sources', 'report_drafting', 'assessment_analysis'].includes(clarificationState.value?.reason || ''))
const comparisonGroups = computed(() => (taskState.value?.claims || []).map((claim: any) => {
  const analysis = relatedResearchAnalysis(taskState.value || {}, claim.claim_id)
  const ids = new Set([...analysis.evidenceIds, ...comparisonLinks(claim.claim_id).map(link => link.evidence_id)])
  return { key: claim.claim_id, title: researchPointName(claim),
    conclusion: pointResult(claim.claim_id),
    analysis,
    sources: evidenceItems.value.filter(record => ids.has(record.evidence_id)),
  }
}))
const selectedComparisonPoint = ref('')
watch(() => comparisonGroups.value.map((g: any) => g.key).join('|'), () => {
  if (!comparisonGroups.value.some((g: any) => g.key === selectedComparisonPoint.value)) selectedComparisonPoint.value = comparisonGroups.value[0]?.key || ''
}, { immediate: true })
function selectComparisonPoint(id: string) {
  selectedComparisonPoint.value = id
}
function researchParagraphs(text: unknown): string[] {
  return String(text || '').split(/\r?\n[\t ]*(?:\r?\n)*/).map(part => part.trim()).filter(Boolean)
}
function comparisonSections(analysis: { similarity: string; difference: string; conditions: string }) {
  return [
    { label: '研究联系', paragraphs: researchParagraphs(analysis.similarity) },
    { label: '研究差异', paragraphs: researchParagraphs(analysis.difference) },
    { label: '比较条件', paragraphs: researchParagraphs(analysis.conditions) },
  ].filter(section => section.paragraphs.length)
}
function comparisonRows(claimId: string) {
  const links = comparisonLinks(claimId)
  return (comparisonGroups.value.find((group: { key: string }) => group.key === claimId)?.sources || []).map((source: EvidenceItem) => {
    const link = links.find(link => link.evidence_id === source.evidence_id)
    return { source, analysis: link ? comparisonResearchAnalysis(link as any, readerComparisonFor(taskState.value || {}, link as any))
      : { similarity: '', difference: '', conditions: '' } }
  })
}
function claimForId(claimId: string) { return (taskState.value?.claims || []).find((item: any) => item.claim_id === claimId) }
function comparisonCoverageSummary(link: any) {
  const labels: Record<string, string> = { object: '对象', method: '方法', data: '数据', baseline: '基线', metric: '指标', time: '时间', validation: '验证条件' }
  const statuses: Record<string, string> = { covered: '已对应', partial: '部分对应', conflict: '存在差异', not_comparable: '口径不可比' }
  const assessed = Object.entries(link.field_matrix || {}).filter(([key, value]) => labels[key] && statuses[value as string]).map(([key, value]) => `${labels[key]}${statuses[value as string]}`)
  return assessed.length ? `已核对：${assessed.join('、')}` : ''
}
function decisionEvidenceSummary(decision: any) { const titles = (decision.evidence_ids || []).map(evidenceTitle).filter(Boolean); return titles.length ? `已关联 ${titles.length} 篇文献，包括《${titles[0]}》${titles.length > 1 ? '等' : ''}。` : '当前检索范围内尚未关联到可用文献，结论仅用于界定后续验证。' }
function decisionSources(decision: any) {
  return presentDecisionSources(decision, evidenceItems.value, taskState.value?.claim_evidence_links || [])
}
function decisionDifference(decision: any) { const links = (taskState.value?.claim_evidence_links || []).filter((item: any) => item.claim_id === decision.claim_id); const supported = links.filter((item: any) => ['supports', 'partially_supports', 'not_comparable'].includes(item.relation)); return supported.length ? `已识别 ${supported.length} 条相关证据关系；项目在具体对象、指标或验证条件上仍有可核验差异。` : '暂未发现可用于同口径比较的公开记录，不据此作否定性判断。' }
function shouldShowRewriteAdvice(decision: any) { const claim = (taskState.value?.claims || []).find((item: any) => item.claim_id === decision.claim_id); return Boolean(decision.rewrite_advice && (!claim?.claim_fields || Object.keys(claim.claim_fields).length < 3)) }
const qualityChecks = computed(() => {
  const checks = taskState.value?.quality_gate?.checks
  if (checks?.length) return checks
  const state: any = taskState.value || {}
  const claimTotal = state.claims?.length || 0
  const covered = new Set((state.claim_evidence_links || []).filter((item: any) => ['supports', 'partially_supports', 'not_comparable', 'related'].includes(item.relation)).map((item: any) => item.claim_id)).size
  const kind = state.report_preferences?.resolved_artifact_type || 'auto'
  const draft = emptyNineDraft()
  mergeNineDraft(draft, state.nine_fields || {}, new Set())
  const missing = missingNineFields(draft, kind)
  const requiredCount = requiredNineFieldKeys(kind).length
  return [
    { key: 'input_completeness', title: '输入完整度', status: missing.length ? 'blocked' : 'passed', completed: requiredCount - missing.length, total: requiredCount, message: missing.length ? `缺少：${missing.map(field => field.label).join('、')}` : '材料已包含当前类型所需信息。' },
    { key: 'retrieval_completeness', title: '文献检索完成度', status: evidenceItems.value.length ? 'passed' : 'warning', completed: evidenceItems.value.length ? 1 : 0, total: 1, message: `当前纳入 ${evidenceItems.value.length} 条证据记录。` },
    { key: 'claim_coverage', title: '要点证据覆盖度', status: covered >= claimTotal * 0.8 ? 'passed' : 'warning', completed: covered, total: claimTotal, message: `${covered}/${claimTotal} 条要点获得方向性或更强证据。` },
    { key: 'report_readiness', title: '报告发布准备度', status: state.quality_gate?.blockers?.length ? 'blocked' : state.quality_gate?.passed ? 'passed' : 'warning', completed: state.quality_gate?.passed ? 4 : 3, total: 4, message: state.quality_gate?.passed ? '已满足正式报告发布条件。' : '评估已完成，报告将说明证据边界。' },
  ]
})
function qualityCheckIcon(status: string) { return status === 'passed' ? 'check_circle' : status === 'blocked' ? 'cancel' : 'info' }
const reportTocSections = computed(() => {
  return reportBodySections.value
})
const reportBodySections = computed(() => (
  [...(assessmentReport.value?.sectionSpecs || [])]
    .sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
))
const reportDocuments = computed(() => uploadInfo.value.documents.map((doc: any) => ({ name: String(doc.name || '未命名材料'), role: String(doc.role || doc.material_role || '') })))
const reportIndicators = computed(() => (taskState.value?.indicators || []).map((item: any) => String(item.name || item.label || item.description || item.indicator || '')).filter(Boolean))
const reportGuidelines = computed(() => (taskState.value?.guideline_requirements || []).map((item: any) => String(item.text || item.requirement || item.description || '')).filter(Boolean))
function reportSectionParagraphs(section: NonNullable<ContentAssessmentReport['sectionSpecs']>[number]) {
  return String(section.content || '')
    .split(/\n\s*\n|\n(?=\S)/)
    .map((item) => String(item).trim())
    .filter((item) => item.length > 0)
}
function sanitizeReportDisplayText(value: unknown) {
  return String(value || '')
    .replace(/不合成单一总分/g, '不据此合并为单一推荐结论')
    .split(/(?<=[。；;\n])/)
    .filter((part) => !/(?:内部(?:评分|分数)|(?:评分|得分|总分|机会分)\s*(?:[:：为]|\d)|优先建议|首选题)/i.test(part))
    .join('')
    .trim()
}
const rawStageTraces = computed<StageTraceItem[]>(() => taskState.value?.stage_trace?.length ? taskState.value.stage_trace : taskDetail.value?.stage_trace || [])
const stageTraces = computed<StageTraceItem[]>(() => {
  const traceByKey = new Map(rawStageTraces.value.map((stage) => [stage.stage_key, stage]))
  const ordered = CONTENT_ASSESSMENT_STAGES
    .map((stage) => {
      const trace = traceByKey.get(stage.key)
      return trace
        ? { ...trace, stage_name: stage.title, summary: stageSummaryDisplay(trace, stage.key) }
        : buildFallbackStage(stage.key)
    })
    .filter((stage) => stage.stage_key !== 'clarification' || shouldShowClarificationStage.value)
    .filter((stage) => stage.stage_key !== 'quality_judgement' || shouldShowQualityJudgementStage.value)
  return ordered
})
const userSteps = computed(() => aggregateSteps(ASSESSMENT_STEPS, stageTraces.value, durationTick.value, taskStatus.value === 'running', taskStatus.value))
const progressSyncing = ref(false)
const runNotice = computed(() => stageRunPresentation(userSteps.value, stageTraces.value, taskDetail.value?.current_stage || '', taskStatus.value, progressSyncing.value))
const selectedUserStep = computed(() => userSteps.value.find(step => step.key === stepForStage(ASSESSMENT_STEPS, selectedStageKey.value || activeStage.value?.stage_key || '').key) || userSteps.value[0])
const activeStepStages = computed(() => selectedUserStep.value.stages.filter(key => !['quality_gate', 'document_intake', 'claim_extraction'].includes(key)).map(key => stageTraces.value.find(stage => stage.stage_key === key)).filter((stage): stage is StageTraceItem => Boolean(stage)))
const completedUserStepCount = computed(() => userSteps.value.filter(step => ['completed', 'degraded'].includes(step.status)).length)
function selectUserStep(key: string) {
  const step = userSteps.value.find(item => item.key === key)
  if (!step || (step.status === 'pending' && isTaskInProgress.value)) return
  const order = step.key === 'report' ? [...step.stages].reverse() : step.stages
  const target = order.map(stage => stageTraces.value.find(item => item.stage_key === stage)).find((stage): stage is StageTraceItem => Boolean(stage && canSelectStage(stage)))
  if (target) selectModernStage(target.stage_key)
}

type ReportDisplayState =
  | 'published'
  | 'running'
  | 'queued'
  | 'awaiting_clarification'
  | 'awaiting_quality_continue'
  | 'failed'
  | 'cancelled'
  | 'unavailable'

const reportDisplayState = computed<ReportDisplayState>(() => {
  const status = taskStatus.value
  if (status === 'completed') return assessmentReport.value?.ready ? 'published' : 'unavailable'
  if (status === 'queued') return 'queued'
  if (status === 'running') return 'running'
  if (status === 'awaiting_clarification') return 'awaiting_clarification'
  if (status === 'awaiting_quality_continue') return 'awaiting_quality_continue'
  if (status === 'failed' || status === 'time_out') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return 'unavailable'
})

const completedStageCount = computed(() => stageTraces.value.filter((stage) => stage.status === 'completed' || stage.status === 'skipped').length)
const reportProgressPercentage = computed(() => Math.round(userSteps.value.reduce((sum, step) => sum + step.progress_percent, 0) / userSteps.value.length))
const reportCurrentStageName = computed(() => {
  if (reportDisplayState.value === 'queued') return '等待任务开始'
  if (reportDisplayState.value === 'awaiting_clarification') return '等待评估边界确认'
  return activeStage.value?.stage_name || '正在准备报告'
})
const reportStatusTitle = computed(() => {
  if (canRetryGeneration.value) return '生成中断'
  const labels: Record<ReportDisplayState, string> = {
    published: '报告已生成',
    running: '报告正在生成中',
    queued: '报告正在排队',
    awaiting_clarification: '等待确认评估边界',
    awaiting_quality_continue: '报告待继续生成',
    failed: '报告生成未完成',
    cancelled: '报告生成已停止',
    unavailable: '报告暂不可用',
  }
  return labels[reportDisplayState.value]
})
const reportStatusDescription = computed(() => {
  const state = reportDisplayState.value
  if (state === 'running' || state === 'queued') return '系统正在整理材料、证据和评估结论，完成后将在此展示完整报告。'
  if (state === 'awaiting_clarification') return isReportRecovery.value ? clarificationState.value?.prompt || '材料和已有结果已保存，请重试继续分析。' : '请确认研究范围，随后继续检索、比对并生成报告。'
  if (state === 'awaiting_quality_continue') return '分析结果已整理，继续后将生成评估报告。'
  if (state === 'failed') return canRetryGeneration.value ? generationRecoveryMessage.value : '本次生成未完成，已有结果已保留。'
  if (state === 'cancelled') return '任务已停止。你可以返回历史列表查看已保留的思考过程。'
  return '任务已完成，但当前没有可展示的报告版本，请稍后重试或返回历史列表。'
})
const reportStatusIcon = computed(() => {
  const icons: Record<ReportDisplayState, string> = {
    published: 'description',
    running: 'autorenew',
    queued: 'schedule',
    awaiting_clarification: 'help_outline',
    awaiting_quality_continue: 'play_circle_outline',
    failed: 'error_outline',
    cancelled: 'stop_circle',
    unavailable: 'description',
  }
  return icons[reportDisplayState.value]
})
const shouldShowClarificationStage = computed(() => {
  if (taskDetail.value?.needs_clarification) return true
  if (taskState.value?.clarification?.needed) return true
  if (taskState.value?.input_understanding?.clarification_needed) return true
  const stage = rawStageTraces.value.find((item) => item.stage_key === 'clarification')
  return Boolean(stage && stage.status !== 'skipped' && stage.status !== 'pending')
})
const shouldShowQualityJudgementStage = computed(() => {
  if (qualityJudgement.value?.enabled) return true
  const stage = rawStageTraces.value.find((item) => item.stage_key === 'quality_judgement')
  return Boolean(stage && stage.status !== 'skipped' && stage.status !== 'pending')
})
const activeStage = computed(() => (
  stageTraces.value.find((stage) => stage.stage_key === selectedStageKey.value)
  ?? stageTraces.value.find((stage) => stage.status === 'running')
  ?? stageTraces.value.find((stage) => stage.status === 'failed' || stage.status === 'blocked')
  ?? (isTaskInProgress.value ? stageTraces.value.find((stage) => stage.status === 'pending') : undefined)
  ?? [...stageTraces.value].reverse().find((stage) => stage.status === 'completed' || stage.status === 'skipped')
  ?? stageTraces.value[0]
))
const taskDetailTitle = computed(() => assessmentTaskTitle(taskState.value?.assessment_profile?.title, taskDetail.value?.title, taskDetail.value?.raw_user_input, reportDocuments.value))
const taskYearRange = computed(() => {
  const raw = taskDetail.value?.raw_user_input || ''
  const match = raw.match(/(?:年份范围|文献年份范围)\s*[:：]\s*(\d{4})\s*[-至]\s*(\d{4})/)
  return match ? `${match[1]}-${match[2]}` : '未限定'
})
const taskOverview = computed(() => {
  const state: any = taskState.value || {}
  const profile: any = state.assessment_profile || {}
  const fields: any = state.nine_fields || {}
  const field = (key: string) => {
    const value = fields[key]
    return Array.isArray(value) ? value.join('、') : String(value || '')
  }
  return {
    researchDomain: profile.research_domain || field('research_direction') || state.input_understanding?.research_domain || '待识别',
    researchObject: profile.assessment_object || field('research_content') || state.input_understanding?.topic || '待识别',
    keyTechnologies: field('key_technologies') || (Array.isArray(state.input_understanding?.core_entities) ? state.input_understanding.core_entities.join('、') : String(state.input_understanding?.core_entities || '')) || '待识别',
    scopeBoundary: profile.scope_boundary || state.final_task_profile?.scope_boundary || '待确认',
  }
})
const taskLanguageLabel = computed(() => getLanguageLabel(taskDetail.value))
const taskDurationDisplay = computed(() => {
  const detail = taskDetail.value
  if (!detail) return '-'
  if (detail.duration_seconds != null) return formatDuration(detail.duration_seconds)
  if (isTaskInProgress.value && detail.created_at) {
    const elapsed = Math.max(0, Math.floor((durationTick.value - new Date(detail.created_at).getTime()) / 1000))
    return formatDuration(elapsed)
  }
  return '-'
})
const taskCurrentStepText = computed(() => {
  const total = taskDetail.value?.total_steps || stageTraces.value.length || CONTENT_ASSESSMENT_STAGES.length
  const completed = taskDetail.value?.completed_steps ?? stageTraces.value.filter((stage) => stage.status === 'completed' || stage.status === 'skipped').length
  const currentIndex = Math.max(1, Math.min(total, isTaskInProgress.value ? completed + 1 : completed || total))
  return `${currentIndex}/${total}`
})

function getLanguageLabel(task: any): string {
  const candidates = [
    task?.language_preference,
    task?.language_pref,
    task?.state?.language_preference,
    task?.state?.language_pref,
    task?.state?.preprocessed_input?.language_preference,
    task?.state?.preprocessed_input?.language_pref,
    task?.state?.preprocessed_input?.language_hint,
  ]
  const raw = candidates.find((item) => item && item !== 'unknown')
  const hint = String(raw || '').trim().toLowerCase()
  if (!hint) return '自动识别'
  const map: Record<string, string> = {
    zh: '中文优先',
    chinese: '中文优先',
    'zh-cn': '中文优先',
    cn: '中文优先',
    en: '英文优先',
    english: '英文优先',
    mixed: '中英混合',
    bilingual: '中英混合',
    auto: '自动识别',
    unknown: '自动识别',
  }
  return map[hint] || String(raw)
}
const evidenceItems = computed<EvidenceItem[]>(() => {
  const retrieved = Object.values(taskState.value?.retrieval_results ?? {}).flat()
  return dedupeEvidence(retrieved)
})

const evidenceSourceDistribution = computed(() => {
  const counts = new Map<string, number>()
  evidenceItems.value.forEach((item) => {
    const label = evidenceSourceLabel(item.source_type)
    counts.set(label, (counts.get(label) || 0) + 1)
  })
  const total = evidenceItems.value.length
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count, percent: total ? Math.max(4, Math.round(count / total * 100)) : 0 }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)
})
const evidenceCitationMap = computed(() => new Map(Object.entries(assessmentReport.value?.citationNumbers || {})))
const {
  literatureSelectionMode,
  selectedLiterature,
  selectedLiteratureCount,
  allLiteratureSelected,
  literatureCollectLoading,
  literatureCollectNotice,
  enterLiteratureSelectionMode,
  cancelLiteratureSelection,
  toggleLiteratureSelectAll,
  toggleLiteratureSelection,
  batchCollectLiterature,
  refreshLiteratureCollections,
  toggleLiteratureCollection,
  isLiteratureCollected,
  isLiteratureCollecting,
  maxLiteratureSelection,
} = useNstlLiteratureCollection(evidenceItems)
const literaturePanelNotice = computed(() => literatureCollectNotice.value)
const retrievalStage = computed(() => stageTraces.value.find((stage) => stage.stage_key === 'retrieval'))
const hasReachedRetrievalStage = computed(() => Boolean(retrievalStage.value && retrievalStage.value.status !== 'pending'))
const shouldShowLiteratureProgress = computed(() => isTaskInProgress.value && !evidenceItems.value.length)
const literatureProgressType = computed(() => {
  const retrieval = retrievalStage.value
  if (!shouldShowLiteratureProgress.value) return 3
  if (!retrieval || retrieval.status === 'pending') return -1
  if (retrieval.status === 'running') return 1
  return 2
})
const literatureProgressColor = computed(() => {
  if (literatureProgressPercentage.value < 35) return '#ff9a5a'
  if (literatureProgressPercentage.value < 70) return '#409eff'
  return '#67c23a'
})
const literatureProgressText = computed(() => {
  if (literatureProgressPercentage.value < 35) return 'AI智能处理中'
  if (literatureProgressPercentage.value < 70) return '正在进行全库查询'
  return '已完成查询，正在精选文献'
})

function clearLiteratureProgressTimers() {
  if (literatureProgressInterval) {
    clearInterval(literatureProgressInterval)
    literatureProgressInterval = null
  }
  if (literatureProgressTimer) {
    clearTimeout(literatureProgressTimer)
    literatureProgressTimer = null
  }
}

function animateLiteratureProgress(start: number, end: number, duration: number) {
  clearLiteratureProgressTimers()
  const normalizedStart = Math.max(0, Math.min(100, start))
  const normalizedEnd = Math.max(normalizedStart, Math.min(100, end))
  if (normalizedEnd === normalizedStart) {
    literatureProgressPercentage.value = normalizedEnd
    return
  }
  const increment = (normalizedEnd - normalizedStart) / (duration / 16)
  let current = normalizedStart
  literatureProgressInterval = setInterval(() => {
    current += increment
    if (current >= normalizedEnd) {
      current = normalizedEnd
      clearLiteratureProgressTimers()
    }
    literatureProgressPercentage.value = Math.round(current)
  }, 16)
}

function syncLiteratureProgress() {
  clearLiteratureProgressTimers()
  if (!shouldShowLiteratureProgress.value) {
    if (literatureProgressPercentage.value > 0 && literatureProgressPercentage.value < 100) {
      animateLiteratureProgress(literatureProgressPercentage.value, 100, 300)
    } else {
      literatureProgressPercentage.value = 0
    }
    return
  }

  const current = literatureProgressPercentage.value
  if (literatureProgressType.value === -1) {
    literatureProgressTimer = setTimeout(() => {
      animateLiteratureProgress(current, 10, 500)
    }, 1500)
  } else if (literatureProgressType.value === 1) {
    animateLiteratureProgress(current, 50, 500)
  } else if (literatureProgressType.value === 2) {
    animateLiteratureProgress(current, 75, 500)
  } else if (literatureProgressType.value === 3) {
    animateLiteratureProgress(current, 100, 500)
  }
}

// 趋势图表
const frozenTrendSeries = computed(() => taskState.value?.report_snapshot ? assessmentTrendSeries(taskState.value) : [])
const landscapeParagraphs = computed(() => {
  const snapshot = taskState.value?.report_snapshot as any
  if (!snapshot) return [taskState.value?.trend_analysis?.summary || '正在综合相关研究进展与技术趋势。']
  if (snapshot.template_version === 'ContentAssessmentResearchReportV6') {
    return (snapshot.sections || []).filter((section: any) => section.section_key === 'progress')
      .flatMap((section: any) => section.blocks || []).filter((block: any) => block.kind === 'paragraph')
      .slice(0, 6).map((block: any) => block.text)
  }
  if (snapshot.template_version === 'ContentAssessmentResultReportV5') {
    return (snapshot.sections || []).filter((section: any) => section.section_key === 'recommendations')
      .flatMap((section: any) => section.blocks || []).filter((block: any) => block.kind === 'paragraph')
      .slice(0, 4).map((block: any) => {
        const refs = [...new Set((block.evidence_ids || []).map((id: string) => snapshot.citation_numbers?.[id]).filter(Boolean))]
        return `${block.text}${refs.map(n => ` [${n}]`).join('')}`
      })
  }
  return [taskTopicAnalysis.value?.overall?.summary || taskState.value?.trend_analysis?.summary || '请在报告中查看研究进展与建议。']
})
const taskTopicAnalysis = computed(() => taskState.value?.topic_analysis)
const taskTrendDecadeBars = computed(() => {
  const bars = taskTopicAnalysis.value?.trend?.bars ?? []
  if (!bars.length) return []
  const values = bars.map(barValue)
  const max = Math.max(...values, 1)
  return bars.map((bar: any, index: number) => ({
    year: barYear(bar, index),
    value: values[index],
    percent: Math.round((values[index] / max) * 100),
  }))
})
const taskTrendPeakValue = computed(() => {
  const bars = taskTopicAnalysis.value?.trend?.bars ?? []
  if (!bars.length) return 0
  return Math.max(...bars.map(barValue))
})
const taskTrendHoveredBar = ref<{ year: number; value: number; percent: number } | null>(null)
const taskTrendChartVisible = ref(true)
const taskTrendDescription = computed(() => {
  const trend = taskTopicAnalysis.value?.trend
  if (!trend) return ''
  const bars = taskTrendDecadeBars.value
  if (bars.length < 2) return trend.text || ''
  const firstYear = bars[0].year
  const lastYear = bars[bars.length - 1].year
  const recent5 = bars.filter((b: any) => b.year >= lastYear - 4)
  const recentTotal = recent5.reduce((sum: number, b: any) => sum + b.value, 0)
  const olderBars = bars.filter((b: any) => b.year < lastYear - 4 && b.year >= lastYear - 9)
  const olderTotal = olderBars.reduce((sum: number, b: any) => sum + b.value, 0)
  const growthPart = olderTotal > 0
    ? `近五年（${lastYear - 4}-${lastYear}）文献量较前五年增长 ${((recentTotal - olderTotal) / olderTotal * 100).toFixed(0)}%`
    : `近五年（${lastYear - 4}-${lastYear}）累计 ${recentTotal} 篇文献`
  return `文献年份跨度 ${firstYear}-${lastYear}，${growthPart}。${trend.text || ''}`
})
const representativeLiteratureItems = computed(() => {
  return evidenceItems.value
    .slice(0, 3)
    .map((item) => ({
      key: item.evidence_id,
      text: String(item.title || '').trim(),
      refId: item.evidence_id,
    }))
    .filter((item) => item.text)
})
const taskAnalysisThinkingSections = computed(() => {
  const analysis = taskTopicAnalysis.value
  if (!analysis) return []
  const sections = [
    { key: 'tensions', title: '研究矛盾和未解问题', rows: normalizeAnalysisRows(analysis.tensions, 3) },
    { key: 'advantages', title: '现有优势', rows: normalizeAnalysisRows(analysis.advantages, 3) },
    { key: 'opportunities', title: '待解决问题与本次研究切入点', rows: normalizeAnalysisRows(analysis.opportunities, 3) },
    {
      key: 'overall',
      title: '整体总结判断',
      rows: normalizeAnalysisRows([
        {
          text: String(analysis.overall?.summary || '').trim(),
          refs: analysis.overall?.refs ?? [],
        },
      ], 1),
    },
  ]
  return sections.filter((section) => section.rows.length)
})
function barValue(bar: number | { value?: number; count?: number }): number {
  if (typeof bar === 'number') return bar
  return Number((bar as any).value ?? (bar as any).count ?? 0)
}
function barYear(bar: number | { year?: number }, index: number): number {
  if (typeof bar !== 'number' && typeof (bar as any).year === 'number') return (bar as any).year
  const years = taskTopicAnalysis.value?.trend?.years ?? []
  return years[index] ?? 2019 + index
}

function stepClass(step: { status: string }) {
  return {
    'is-completed': step.status === 'completed',
    'is-running': step.status === 'running',
    'is-active': step.status === 'running',
  }
}

function buildFallbackStage(key: string): StageTraceItem {
  const matched = CONTENT_ASSESSMENT_STAGES.find((stage) => stage.key === key)
  return { stage_key: key, stage_name: matched?.title || key, status: 'pending', summary: '' }
}

/**
 * Process traces are persisted by the backend and may come from an older
 * run/model with English summaries. The task page is Chinese-only, so we
 * deliberately fall back to the stage's controlled Chinese description when
 * a summary is not readable instead of exposing raw model/technical text.
 */
function stageSummaryDisplay(trace: Pick<StageTraceItem, 'summary'>, stageKey: string): string {
  const summary = String(trace.summary || '').trim()
  const looksTechnical = /(?:input_count|output_count|completed_count|substep_count|coverage_rate|claim_coverage|indicator_coverage|citation_integrity|[_=]{1,2})/i.test(summary)
  if (summary && /[\u4e00-\u9fff]/.test(summary) && !looksTechnical) return summary
  return CONTENT_ASSESSMENT_STAGES.find((stage) => stage.key === stageKey)?.desc || '正在准备该阶段的评估工作。'
}

function dedupeEvidence(items: EvidenceItem[]) {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = item.evidence_id || item.title
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function toggleThinking() {
  isThinkingExpanded.value = !isThinkingExpanded.value
  trackEvent('content_assessment_detail_thinking_toggle', isThinkingExpanded.value ? '展开思考过程' : '收起思考过程', {
    task_id: props.taskId,
    status: taskStatus.value,
  })
}

function toggleStage(stageKey: string) {
  selectedStageKey.value = stageKey
  const next = new Set(expandedStages.value)
  const expanded = !next.has(stageKey)
  const manualOpen = new Set(manualExpandedStages.value)
  if (!expanded) {
    next.delete(stageKey)
    manualOpen.delete(stageKey)
  } else {
    next.add(stageKey)
    manualOpen.add(stageKey)
  }
  manualExpandedStages.value = manualOpen
  expandedStages.value = next
  trackEvent('content_assessment_detail_stage_toggle', expanded ? '展开阶段' : '收起阶段', {
    task_id: props.taskId,
    stage_key: stageKey,
  })
}

function syncActiveStageExpansion(scroll = false) {
  const key = activeStage.value?.stage_key
  if (!key) {
    autoExpandedStageKey.value = ''
    expandedStages.value = new Set(manualExpandedStages.value)
    if (!isTaskInProgress.value) isThinkingExpanded.value = false
    return
  }

  if (isTaskInProgress.value) {
    autoExpandedStageKey.value = key
    isThinkingExpanded.value = true
    const next = new Set(manualExpandedStages.value)
    next.add(key)
    expandedStages.value = next
  } else {
    autoExpandedStageKey.value = ''
    // Historical detail pages open the thinking panel by default, matching the
    // deep-research report view; users can still collapse it interactively.
    isThinkingExpanded.value = props.mode === 'detail'
    expandedStages.value = new Set(manualExpandedStages.value)
  }

  if (scroll) {
    nextTick(() => {
      document.getElementById(`trace-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }
}

function resetStageExpansionState() {
  autoExpandedStageKey.value = ''
  expandedStages.value = new Set()
  manualExpandedStages.value = new Set()
}

function resetLiteratureProgressState() {
  clearLiteratureProgressTimers()
  literatureProgressPercentage.value = 0
}

function thinkingCardClass(stage: StageTraceItem) {
  return {
    'is-completed': stage.status === 'completed' || stage.status === 'skipped',
    'is-running': stage.status === 'running',
    active: activeStage.value?.stage_key === stage.stage_key,
  }
}

function stageStatusLabel(status: string) {
  return userStepStatusLabel(status)
}

function stageDurationLabel(stage: StageTraceItem): string {
  if (stage.status === 'pending') return '未开始'
  if (stage.status === 'skipped') return '未计时'
  const started = stage.started_at ? Date.parse(stage.started_at) : NaN
  const completed = stage.completed_at ? Date.parse(stage.completed_at) : NaN
  const stored = stage.duration_seconds
  const elapsed = stored != null && Number.isFinite(stored) && stage.status !== 'running'
    ? stored
    : Number.isFinite(started)
      ? Math.max(0, ((stage.status === 'running' ? durationTick.value : completed) - started) / 1000)
      : NaN
  if (!Number.isFinite(elapsed)) return stage.status === 'running' ? '计时中' : '用时未记录'
  if (stage.status === 'running' && elapsed < 1) return '计时中'
  if (elapsed < 1 && stage.status !== 'running') return elapsed < 0.001 ? '不足1毫秒' : `${Math.round(elapsed * 1000)}毫秒`
  return formatDuration(elapsed)
}

function processStepDurationLabel(step: ProcessTraceStep): string {
  if (step.status === 'running') return '处理中'
  if (/^\w+:\d+$/.test(step.step_key) && step.duration_seconds === 0.001) return '用时未记录'
  const duration = step.duration_seconds
  if (duration == null || !Number.isFinite(duration)) return '用时未记录'
  if (duration < 1) return duration < 0.001 ? '不足1毫秒' : `${Math.round(duration * 1000)}毫秒`
  return formatDuration(duration)
}

const qualityModelStatusLabel = computed(() => ({
  completed: '模型已复核规则结果；报告资格仍以规则检查为准。',
  fallback: '模型复核未完成，已按规则完成质量检查。',
  not_needed: '当前材料和证据不足以进行模型复核，已按规则完成质量检查。',
} as Record<string, string>)[taskState.value?.quality_gate?.model_status || ''] || '')

function stageProgressText(stageKey: string): string {
  const progress = taskState.value?.stage_progress?.[stageKey]
  if (!progress || !progress.total) return ''
  return `${progress.display_name} ${progress.completed}/${progress.total}`
}

function traceStatusClass(status: string) {
  return {
    'status-completed': status === 'completed' || status === 'skipped',
    'status-running': status === 'running',
  }
}

function stageProcessTrace(stage: StageTraceItem): ProcessTraceStep[] {
  const processTrace = taskState.value?.process_trace
  if (!processTrace) return []
  return processTrace[stage.stage_key] || []
}

function stageProcessMetrics(stageKey: string): { input_count?: number; output_count?: number; completed_count?: number; substep_count?: number } | null {
  const metrics = taskState.value?.output_json?.process_metrics as Record<string, unknown> | undefined
  const item = metrics?.[stageKey]
  return item && typeof item === 'object' ? item as { input_count?: number; output_count?: number; completed_count?: number; substep_count?: number } : null
}

const PROCESS_STEP_TITLE_MAP: Record<string, string> = {
  parse: '文件解析',
  'document parse': '文件解析',
  'file parse': '文件解析',
  index: '章节与页码索引',
  'section/page index': '章节与页码索引',
  'section page index': '章节与页码索引',
  role: '材料角色识别',
  'material role identification': '材料角色识别',
  boundary: '评估边界整理',
  'boundary matrix': '评估边界整理',
  quality: '材料质量检查',
  'material quality check': '材料质量检查',
  'nine-field extraction': '九字段抽取',
  'nine fields extraction': '九字段抽取',
  'work package split': '工作包拆分',
  'atomic claim split': '原子要点拆分',
  'indicator structuring': '指标结构化',
  'guideline requirement split': '指南要求拆分',
  'query family planning': '查询组规划',
  'term expansion': '术语扩展',
  'grouped retrieval': '分组检索',
  'deduplication and ranking': '去重与排序',
  'dedupe ranking': '去重与排序',
  'evidence snippet extraction': '证据片段提取',
  'claim matching': '要点匹配',
  'same-caliber field comparison': '同口径字段比较',
  'support and counter-evidence identification': '支持与反证识别',
  'evidence level determination': '证据等级判定',
  'coverage statistics': '证据覆盖统计',
  'coverage rate statistics': '证据覆盖统计',
  'claim determination': '要点判定',
  'indicator validation': '指标验证',
  'feasibility analysis': '可行性分析',
  'risk identification': '风险识别',
  'scoring and rewriting': '评分与改写建议',
  'year statistics': '年份分布统计',
  'topic clustering': '主题聚类',
  'method route summary': '方法路线归纳',
  'competition observation': '竞争态势观察',
  'regulatory boundary': '监管边界核验',
  'material completeness': '材料完整性检查',
  'evidence coverage': '证据覆盖率检查',
  'citation completeness': '引用完整性检查',
  'duplicate and empty check': '重复与空洞检查',
  'section quota check': '章节配额检查',
  'section quota planning': '章节配额规划',
  'batched section generation': '分批生成报告章节',
  'citation validation': '引用校验',
  'version finalization': '报告版本固化',
  'multi-format export': '多格式报告导出',
  '章节/页码索引': '章节与页码索引',
  '章节页码索引': '章节与页码索引',
  '边界矩阵': '评估边界整理',
  '查询族规划': '查询组规划',
  '查询族': '查询组规划',
  '去重排序': '去重与排序',
  '证据片段抽取': '证据片段提取',
  '覆盖率统计': '证据覆盖统计',
  '覆盖统计率': '证据覆盖统计',
  '评分与改写': '评分与改写建议',
  '年份统计': '年份分布统计',
  '竞争观察': '竞争态势观察',
  '监管边界': '监管边界核验',
  '材料完整性': '材料完整性检查',
  '证据覆盖率': '证据覆盖率检查',
  '引用完整性': '引用完整性检查',
  '分批章节生成': '分批生成报告章节',
  '版本固化': '报告版本固化',
  '多格式导出': '多格式报告导出',
}

const PROCESS_STEP_SUMMARY_MAP: Record<string, string> = {
  '文件解析': '已读取材料并建立可检索的文本内容。',
  '章节与页码索引': '已记录章节、页码等定位信息，便于后续引用。',
  '材料角色识别': '已确认材料属于项目、指南、查新或参考材料。',
  '评估边界整理': '已整理评估对象、纳入范围和排除范围。',
  '材料质量检查': '已检查材料完整性、可读性和关键信息缺口。',
  '九字段抽取': '已提取研究方向、目标、内容、技术路线等结构化字段。',
  '工作包拆分': '已将评估任务拆分为可执行的工作包。',
  '原子要点拆分': '已把材料中的复合表述拆分为可逐项核验的要点。',
  '指标结构化': '已识别可量化、可验证的评价指标及其口径。',
  '指南要求拆分': '已整理指南要求并准备进行符合性映射。',
  '查询组规划': '已围绕要点、指标和边界生成检索查询组。',
  '术语扩展': '已补充同义词、缩写和相关技术术语。',
  '分组检索': '已按主题和要点分组检索 NSTL 文献证据。',
  '去重与排序': '已去除重复记录并按相关性和证据质量排序。',
  '证据片段提取': '已提取可定位的摘要、原文片段和来源信息。',
  '要点匹配': '已将每条要点与候选证据进行关联。',
  '同口径字段比较': '已按对象、方法、指标和条件进行同口径比较。',
  '支持与反证识别': '已分别标记支持性证据、反例和证据缺口。',
  '证据等级判定': '已根据来源和定位完整性标注证据等级。',
  '证据覆盖统计': '正在统计每条要点的证据覆盖情况。',
  '要点判定': '已形成支持、存疑或证据不足等要点级判定。',
  '指标验证': '已核验指标定义、数值口径和验证条件。',
  '可行性分析': '已从技术、资源、时间和实施条件分析可行性。',
  '风险识别': '已识别创新性、口径、合规和实施风险。',
  '评分与改写建议': '已生成评分依据、风险提示和可执行改写建议。',
  '年份分布统计': '已统计证据的年份分布，准备分析研究趋势。',
  '主题聚类': '已按研究主题聚合证据并识别热点方向。',
  '方法路线归纳': '已归纳主要技术路线、方法差异和验证方式。',
  '竞争态势观察': '已整理代表性团队、方案和研究空白。',
  '监管边界核验': '已检查适用标准、监管要求和结论边界。',
  '材料完整性检查': '已检查必需材料是否齐全以及关键缺口。',
  '证据覆盖率检查': '已检查要点、指标与证据关联的覆盖程度。',
  '引用完整性检查': '已检查引用编号、来源定位和引用一致性。',
  '重复与空洞检查': '已检查章节重复、空泛表述和无法验证的结论。',
  '章节配额检查': '已检查报告章节的字数、证据和图表配额。',
  '章节配额规划': '已规划报告章节结构、篇幅和证据分配。',
  '分批生成报告章节': '已按章节逐步生成报告正文和结论。',
  '引用校验': '已校验报告中的引用编号与证据记录。',
  '报告版本固化': '已固化报告版本和任务状态，确保前后一致。',
  '多格式报告导出': '已准备网页预览和 Word 文档导出。',
}

function normalizeProcessStepTitle(step: ProcessTraceStep): string {
  const raw = String(step.title || '').trim()
  if (!raw) return '阶段处理'
  if (PROCESS_STEP_TITLE_MAP[raw]) return PROCESS_STEP_TITLE_MAP[raw]
  const normalized = raw.toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ')
  const mapped = PROCESS_STEP_TITLE_MAP[normalized] || PROCESS_STEP_TITLE_MAP[normalized.replace(/^\w+:\s*/, '')]
  if (mapped) return mapped
  // Do not expose technical identifiers such as coverage_rate or
  // input_count in the reader-facing panel.
  if (/[A-Za-z_=:[\]{}]/.test(raw)) return '阶段处理'
  return /覆盖.*统计|统计.*覆盖/.test(raw) ? '证据覆盖统计' : raw
}

function processStepTitle(stage: StageTraceItem, step: ProcessTraceStep): string {
  const title = normalizeProcessStepTitle(step)
  if (title !== '阶段处理') return title
  const index = Number(String(step.step_key || '').split(':').pop() || 0)
  const fallback = CONTENT_ASSESSMENT_PROCESS_STEPS[stage.stage_key]?.[index - 1]
  return fallback || `${stage.stage_name}处理`
}

function processStepSummary(stage: StageTraceItem, step: ProcessTraceStep): string {
  const title = processStepTitle(stage, step)
  const summary = String(step.summary || '').trim()
  const looksTechnical = /(?:input_count|output_count|completed_count|substep_count|coverage_rate|claim_coverage|indicator_coverage|citation_integrity|[_=]{1,2})/i.test(summary)
  if (summary && /[\u4e00-\u9fff]/.test(summary) && !looksTechnical) return summary
  return PROCESS_STEP_SUMMARY_MAP[title] || `${stage.stage_name}正在处理相关评估信息。`
}

function processMetricsSummary(stageKey: string): string {
  const progress = stageProgressText(stageKey)
  if (progress) {
    const completed = taskState.value?.stage_trace?.find((item: any) => item.stage_key === stageKey)?.status === 'completed'
    return `${progress}，${completed ? '本阶段已完成。' : '正在处理中。'}`
  }
  const metrics = stageProcessMetrics(stageKey)
  if (!metrics) return ''
  const input = Number(metrics.input_count || 0)
  const output = Number(metrics.output_count || 0)
  const completed = Number(metrics.completed_count || 0)
  const total = Number(metrics.substep_count || 0)
  const text: Record<string, string> = {
    document_intake: `已处理 ${input} 份材料，形成 ${output} 项边界与材料信息。`,
    claim_extraction: `已从 ${input} 份材料提取 ${output} 项要点与指标。`,
    retrieval: `已完成 ${input} 组检索计划，汇集 ${output} 条证据记录。`,
    comparison: `已比对 ${input} 条要点，建立 ${output} 条要点—证据关联。`,
    assessment: `已完成 ${input} 项要点与指标的创新性、可行性及风险判断。`,
    landscape: `已根据 ${input} 条证据归纳 ${output} 个趋势主题。`,
    quality_gate: `已完成证据、引用和报告质量检查，发现 ${output} 项需关注提示。`,
    report: `已规划 ${output} 个报告章节，完成 ${completed}/${total} 项发布准备工作。`,
  }
  return text[stageKey] || `已完成 ${completed}/${total} 项阶段处理，形成 ${output} 项结果。`
}

function displayedProcessTrace(stage: StageTraceItem): ProcessTraceStep[] {
  const steps = stageProcessTrace(stage)
  if (stage.stage_key === 'retrieval' && representativeLiteratureItems.value.length) {
    return steps.filter((step) => step.step_key !== 'highlights')
  }
  if (stage.stage_key === 'landscape' && taskAnalysisThinkingSections.value.length) {
    return steps.filter((step) => !['tensions', 'advantages', 'opportunities'].includes(step.step_key))
  }
  return steps
}

function hasProcessDisplay(stage: StageTraceItem): boolean {
  return Boolean(
    displayedProcessTrace(stage).length
    || (stage.stage_key === 'retrieval' && representativeLiteratureItems.value.length)
    || (stage.stage_key === 'landscape' && taskAnalysisThinkingSections.value.length),
  )
}

function stageDetail(stage: StageTraceItem) {
  if (stage.summary) return stage.summary
  const matched = CONTENT_ASSESSMENT_STAGES.find((item) => item.key === stage.stage_key)
  return matched?.desc || '等待执行'
}

function uploadedFileContextLines(stageKey: string): string[] {
  const context = taskState.value?.uploaded_file_context
  if (!context?.enabled || !context.total_files) return []
  const files: Array<{ name?: string; keywords?: string[] }> = context.files || []
  const names = files.map((file) => file.name).filter(Boolean).slice(0, 3).join('、') || '上传材料'
  const suffix = context.total_files > 3 ? `等${context.total_files}个文件` : `${context.total_files}个文件`
  const keywords = Array.from(new Set(files.flatMap((file) => file.keywords || []))).slice(0, 8)
  if (stageKey === 'document_intake') {
    return [`上传材料：已接收并解析${suffix}（${names}），解析文本已作为创新性评估的材料依据。`]
  }
  if (stageKey === 'claim_extraction') {
    return ['材料内容已用于提取九字段、要点与指标；材料本身不作为独立外部证据。']
  }
  if (stageKey === 'retrieval') {
    return [`检索提示：${keywords.length ? `已提取 ${keywords.length} 个关键词参与证据检索（${keywords.join('、')}）` : '已参与证据检索准备'}；上传材料不会替代右侧独立来源证据。`]
  }
  if (stageKey === 'comparison' || stageKey === 'assessment') {
    return ['解析内容已作为比对背景和术语边界；正式判定仍以可定位的独立外部证据为准。']
  }
  if (stageKey === 'quality_gate' || stageKey === 'report') {
    return ['材料角色、评估边界和独立证据链均会纳入质量门与报告版本记录。']
  }
  return []
}

function stageTraceForReport(stageKey: string) {
  const stage = rawStageTraces.value.find((item) => item.stage_key === stageKey)
  return stage?.summary || (stage?.status === 'completed' ? '已完成该阶段并写入任务状态。' : '该阶段尚未完成，结果将在任务继续后补充。')
}

function stageDetailItems(stage: StageTraceItem) {
  const state = taskState.value
  const profile = state?.final_task_profile ?? state?.output_json?.final_task_profile
  const retrievalDetail = state?.retrieval_detail
  const currentEvidence = evidenceItems.value
  const currentFindings = finalQuestions.value
  const qualityGate = state?.quality_gate

  if (stage.stage_key === 'document_intake') {
    const documents = state?.assessment_profile?.document_records || []
    return [
      `材料数量：已识别 ${documents.length} 份材料。`,
      `评估对象：${state?.assessment_profile?.assessment_object || taskDetail.value?.title || '待确认'}`,
      `评估边界：${state?.assessment_profile?.scope_boundary || '待确认'}`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'claim_extraction') {
    return [
      `要点数量：已提取 ${state?.claims?.length || 0} 条可评估要点。`,
      `指标数量：已提取 ${state?.indicators?.length || 0} 项指标。`,
      `评估方向：${profile?.research_domain || '待识别'}`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'retrieval') {
    return [
      `证据规模：当前组织 ${currentEvidence.length} 条独立来源证据。`,
      `检索策略：${retrievalDetail?.retrieval_mix?.join(' → ') || retrievalDetail?.query_plan?.join('；') || '围绕要点、指标和评估边界建立检索计划。'}`,
      ...(retrievalDetail?.keywords?.length ? [`检索关键词：${retrievalDetail.keywords.slice(0, 8).join('、')}`] : []),
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'comparison') {
    return [
      `相关研究分析：已形成 ${state?.claim_evidence_links?.length || 0} 条要点证据关联。`,
      `待判定要点：${currentFindings.length || 0} 条。`,
      `可追溯证据：${currentEvidence.length} 条。`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'assessment') {
    return [
      `创新性与风险判定：已形成 ${state?.novelty_decisions?.length || 0} 条结论。`,
      `主要风险：${state?.quality_gate?.warnings?.[0] || '等待风险识别。'}`,
      `改写建议：${state?.novelty_decisions?.[0]?.rewrite_advice || '等待生成。'}`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'landscape') {
    return [
      `趋势结论：${state?.topic_analysis?.overall?.summary || '等待形成趋势与竞争态势结论。'}`,
      `趋势证据：${currentEvidence.length} 条。`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'quality_gate') {
    return [
      `质量门结论：${qualityGate?.report_eligibility === 'formal' ? '可发布正式报告。' : '需按质量门要求补强或复核。'}`,
      `阻断项：${qualityGate?.blockers?.length || 0} 项。`,
      `自动检查：${qualityGate?.warnings?.length || qualityGate?.blockers?.length ? '已记录数据限制与验证建议。' : '未发现阻塞性问题。'}`,
      ...uploadedFileContextLines(stage.stage_key),
    ]
  }

  if (stage.stage_key === 'report') return [
    `报告版本：${state?.report_plan?.report_version || '待生成'}`,
    `报告类型：${state?.report_plan?.report_type || '创新性评估报告'}`,
    `发布摘要：${state?.report_plan?.executive_summary || '等待报告发布。'}`,
    ...uploadedFileContextLines(stage.stage_key),
  ]

  return [stage.summary || '等待执行']
}

function inputTypeLabel(type?: string) {
  const labels: Record<string, string> = {
    invalid: '无效输入',
    short_topic: '短主题',
    research_topic: '研究主题',
    existing_question: '已有问题',
    long_material: '长材料片段',
    pasted_reference: '参考文献片段',
  }
  return labels[type || ''] ?? (type || '待识别')
}

function taskModeLabel(mode?: string) {
  const labels: Record<string, string> = {
    content_assessment: '创新性评估',
    document_review: '材料评审',
    evidence_comparison: '要点证据比对',
    trend_analysis: '趋势与竞争态势分析',
  }
  return labels[mode || ''] ?? (mode || '')
}

function questionEvidenceFor(question: FinalQuestion) {
  const matches = (question.evidence_refs ?? [])
    .map((ref) => evidenceItems.value.find((item) => item.evidence_id === ref))
    .filter((item): item is EvidenceItem => Boolean(item))
  if (matches.length) return matches
  return evidenceItems.value.slice(0, 3)
}

function reportEvidenceFor(claim: ContentAssessmentReport['claims'][number]) {
  const matches = (claim.evidenceRefs || [])
    .map((ref) => evidenceItems.value.find((item) => item.evidence_id === ref))
    .filter((item): item is EvidenceItem => Boolean(item))
  return matches
}

async function handleReportDownload(format: 'docx' | 'pdf' | 'md' | 'zip' = 'docx') {
  const taskId = taskDetail.value?.task_id || props.taskId
  if (!taskId || isReportDownloading(format)) return
  if (!(await getAuthManager().checkAuth())) {
    pendingAuthenticatedAction = () => { void handleReportDownload(format) }
    getAuthManager().showLoginModal()
    loadError.value = '请先登录平台账号后再下载报告'
    return
  }
  reportDownloadFormats.value = new Set([...reportDownloadFormats.value, format])
  trackEvent('content_assessment_report_download', `下载 ${format.toUpperCase()} 报告`, { task_id: taskId, format })
  try {
    await downloadContentAssessmentReport(taskId, format)
    if (format === 'pdf') await loadTask({ showLoading: false })
    trackOutcome('content_assessment_report_download', `下载 ${format.toUpperCase()} 报告`, 'success', { task_id: taskId, format })
  } catch (err: any) {
    trackOutcome('content_assessment_report_download', '下载 Word 报告', 'failure', {
      task_id: taskId,
      error_kind: errorKind(err),
    })
    showError(err?.message || `${format.toUpperCase()} 报告下载失败`)
  } finally {
    const next = new Set(reportDownloadFormats.value)
    next.delete(format)
    reportDownloadFormats.value = next
  }
}

function stageDescription(key: string) { return CONTENT_ASSESSMENT_STAGES.find((item) => item.key === key)?.desc || '' }
function canSelectStage(stage: StageTraceItem) { return stage.status !== 'pending' || stage.stage_key === taskState.value?.current_stage }
function documentRoleLabel(role: string) { return ({ PROJECT: '项目材料', BASELINE: '对照基线', REFERENCE: '参考材料', EVIDENCE: '证据材料' } as Record<string, string>)[role] || role }
function relationLabel(value: string) { return ({ supports: '直接支持', support: '直接支持', partially_supports: '相关方法研究', partial: '相关方法研究', not_comparable: '相近技术', insufficient_evidence: '暂未找到同口径证据', contradicts: '存在反证', contradict: '存在反证', related: '相近技术', uncertain: '待进一步核验', neutral: '相关线索' } as Record<string, string>)[value] || '待进一步核验' }
function claimSourceLabel(claim: any) {
  if (claim.source_kind === 'evidence') return evidenceItems.value.find((item) => item.evidence_id === claim.source_evidence_id)?.title || claim.source_evidence_id || '来源待核对'
  if (!claim.source_document_id || claim.source_document_id === 'user_material') return '用户输入材料'
  return taskState.value?.assessment_profile?.document_records?.find((item: any) => item.document_id === claim.source_document_id)?.name || '用户输入材料'
}
const landscapeTags = computed(() => {
  const trend = taskState.value?.trend_analysis || {}
  return [trend.themes, trend.trend_highlights, trend.competition_observations, trend.risk_notes].flatMap((items) => Array.isArray(items) ? items.map((item: any) => typeof item === 'string' ? item : item.title || item.name || item.summary || '').filter(Boolean) : [])
})
function stageHandoffItems(key: string): string[] {
  const state = taskState.value
  if (!state) return ['当前阶段尚未形成可传递结果。']
  const join = (items: any[], field: string) => items.map((item) => item[field]).filter(Boolean).join('、')
  const rows: Record<string, string[]> = {
    document_intake: [state.assessment_profile?.assessment_object ? `评估对象：${state.assessment_profile.assessment_object}` : '', state.assessment_profile?.scope_boundary ? `评估边界：${state.assessment_profile.scope_boundary}` : '', join(state.assessment_profile?.document_records || [], 'name') ? `材料：${join(state.assessment_profile.document_records, 'name')}` : ''],
    claim_extraction: [(state.claims || []).map((item: any) => `${claimUserLabel(item.claim_id)}：${item.text}`).join('；'), join(state.indicators || [], 'name') ? `指标：${join(state.indicators, 'name')}` : ''],
    retrieval: [(state.query_plans || []).map((item: any) => `${item.query_group || '查询主题'}：已完成检索`).join('；'), evidenceItems.value.map((item) => `${evidenceUserLabel(item)}《${item.title}》`).join('；')],
    comparison: [(state.claim_evidence_links || []).map((item: any) => `${claimUserLabel(item.claim_id)} →《${evidenceTitle(item.evidence_id)}》（${relationLabel(item.relation)}）`).join('；')],
    assessment: [(state.novelty_decisions || []).map((item: any) => `${claimUserLabel(item.claim_id)}：${item.label}；${item.rationale}`).join('；')],
    landscape: [state.trend_analysis?.summary || '', ...landscapeTags.value],
    quality_gate: [...qualityChecks.value.map((item: any) => `${item.title}：${item.completed}/${item.total}`), state.quality_gate?.report_eligibility ? `报告状态：${state.quality_gate.report_eligibility === 'formal' ? '正式报告' : '探索性报告'}` : ''],
    report: [assessmentReport.value?.ready ? `正式报告：${reportBodySections.value.map((item) => item.title).join('、')}` : ''],
  }
  return (rows[key] || []).filter(Boolean).length ? rows[key].filter(Boolean) : ['当前阶段尚未形成可传递结果。']
}
function isReportDownloading(format: string) { return reportDownloadFormats.value.has(format) }

function questionPoints(question: FinalQuestion): Array<{ label: string; text: string }> {
  const topic = compactQuestionTopic(question)
  const refs = questionEvidenceFor(question)
  const refCount = refs.length
  const evidenceText = refCount ? `已关联 ${refCount} 条来源证据` : '当前证据仍需继续补充'
  return [
    {
      label: '判定',
      text: `${topic}已按要点、指标和评估边界形成可追溯的判定结论。`,
    },
    {
      label: '证据',
      text: `${evidenceText}；可继续补充独立外部来源以提升证据充分性。`,
    },
    {
      label: '风险',
      text: '需关注结论口径、材料边界和未被证据充分支持的表述。',
    },
    {
      label: '趋势',
      text: buildFrontierText(question),
    },
  ]
}

function compactQuestionTopic(question: FinalQuestion): string {
  const cleaned = [question.title, question.question]
    .map((item) => cleanQuestionTopicText(item))
    .find(Boolean) || ''
  if (!cleaned) return '该要点'
  return cleaned.length > 34 ? `${cleaned.slice(0, 34)}...` : cleaned
}

function cleanQuestionTopicText(value?: string): string {
  return String(value || '')
    .trim()
    .replace(/^(边界条件与影响机制|证据差异与比较验证|方法路径与落地约束)[:：\s-]*/g, '')
    .replace(/[？?。！!]\s*$/g, '')
}

function citationRefIds(refs?: string[]): string[] {
  if (!refs?.length) return []
  const seen = new Set<string>()
  return refs
    .filter((id) => evidenceCitationMap.value.has(id))
    .filter((id) => {
      if (seen.has(id)) return false
      seen.add(id)
      return true
    })
}

function citationNumber(refId: string): number | string {
  return evidenceCitationMap.value.get(refId) ?? '?'
}

function citationButtonTitle(refId: string): string {
  return `点击跳转至右侧来源证据 [${citationNumber(refId)}]`
}

function normalizeAnalysisRows(items: any, limit: number): Array<{ key: string; text: string; refs: string[] }> {
  const source = Array.isArray(items) ? items : []
  return source
    .map((item, index) => {
      const text = String(item?.text || item?.summary || '').trim()
      return {
        key: `${index}-${text.slice(0, 16)}`,
        text,
        refs: Array.isArray(item?.refs) ? item.refs : [],
      }
    })
    .filter((item) => item.text)
    .slice(0, limit)
}

function buildFrontierText(question: FinalQuestion): string {
  const reason = sanitizeRecommendationReason(question.recommendation_reason)
  if (reason) return reason
  const refs = questionEvidenceFor(question)
  const recentYears = refs.map((item) => Number(item.year)).filter((year) => Number.isFinite(year) && year >= 2020)
  if (recentYears.length) {
    return '近年来源证据已覆盖相关线索，说明该要点与正在发展的研究议题保持连接，适合继续跟踪最新证据。'
  }
  return '该要点仍需结合检索结果、证据补强与口径校验持续评估。'
}

function sanitizeRecommendationReason(reason?: string): string {
  const raw = String(reason || '').trim()
  if (!raw) return ''
  const forbidden = ['边界条件与影响机制', '证据差异与比较验证', '方法路径与落地约束']
  if (forbidden.some((item) => raw === item || raw.includes(`${item}：`) || raw.includes(`${item}:`))) return ''
  return formatEvidenceTextForDisplay(raw
    .replace(/该问题对应研究现状中的[^。；]*[。；]?/g, '')
    .replace(/依据层级包括[^。；]*[。；]?/g, '')
    .replace(/可验证路径是：?[^。]*。?/g, '')
    .replace(/边界风险是：?[^。]*。?/g, '')
    .trim())
}

function formatEvidenceTextForDisplay(value?: string | null): string {
  if (!value) return ''
  return String(value)
    .replace(/\bevi_standard_(\d+)\b/gi, '文献$1')
    .replace(/\bevi(?:dence)?[_-](\d+)\b/gi, '文献$1')
}

function evidenceCitationLabel(item: EvidenceItem) {
  return `[${evidenceCitationMap.value.get(item.evidence_id) ?? '-'}]`
}

function questionEvidenceLabels(question: FinalQuestion): string[] {
  return questionEvidenceFor(question).map((item) => evidenceCitationLabel(item))
}

function evidenceLinkUrl(item: EvidenceItem): string {
  return resolveEvidenceUrl(item)
}
function evidenceUrlForRef(ref: string) { const item = evidenceItems.value.find((e) => e.evidence_id === ref); return item ? evidenceLinkUrl(item) : '' }

function isAbstractExpanded(evidenceId: string): boolean {
  return expandedAbstractIds.value.has(evidenceId)
}

function toggleAbstract(evidenceId: string) {
  const next = new Set(expandedAbstractIds.value)
  if (next.has(evidenceId)) next.delete(evidenceId)
  else next.add(evidenceId)
  expandedAbstractIds.value = next
}

function literatureAbstractText(item: EvidenceItem): string {
  return item.abstract || item.support_summary || ''
}

function literatureAbstractDisplay(item: EvidenceItem): string {
  const abstract = literatureAbstractText(item)
  if (abstract.length <= 110 || isAbstractExpanded(item.evidence_id)) {
    return abstract
  }
  return `${abstract.slice(0, 110)}...`
}

function formatVenue(venue: string): string {
  if (!venue) return ''
  if (venue.startsWith('{') && venue.includes('title')) {
    const single = venue.match(/'title':\s*'([^']+)'/)
    const double = venue.match(/"title":\s*"([^"]+)"/)
    return single?.[1] || double?.[1] || venue
  }
  return venue
}

function formatVenueYear(item: EvidenceItem): string {
  const venue = formatVenue(item.venue) || item.source_type || 'NSTL文献库'
  return item.year ? `${venue}（${item.year}）` : venue
}

function literatureSourceTitle(item: EvidenceItem): string {
  return formatVenue(item.venue) || item.source_type || 'NSTL文献库'
}

function literatureYearDisplay(item: EvidenceItem): string {
  const year = Number(item.year)
  return Number.isFinite(year) && year > 0 ? `（${year}）` : ''
}

function showLiteratureTooltip(event: MouseEvent, text?: string) {
  const value = String(text || '').trim()
  if (!value) return
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  literatureTooltip.value = {
    visible: true,
    text: value,
    left: Math.min(Math.max(rect.left + rect.width / 2, 16), window.innerWidth - 16),
    top: Math.max(rect.top - 8, 8),
  }
}

function hideLiteratureTooltip() {
  literatureTooltip.value = { ...literatureTooltip.value, visible: false }
}

async function copyLiteratureDoi(doi?: string) {
  const value = String(doi || '').trim()
  if (!value) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = value
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    literatureCollectNotice.value = 'DOI 已复制'
    window.setTimeout(() => {
      if (literatureCollectNotice.value === 'DOI 已复制') literatureCollectNotice.value = ''
    }, 1600)
  } catch {
    literatureCollectNotice.value = 'DOI 复制失败，请手动复制'
  }
}

function highlightEvidence(evidenceId: string) {
  highlightedEvidenceId.value = evidenceId
  trackEvent('content_assessment_detail_evidence_jump', '引用跳转', {
    task_id: props.taskId,
    evidence_index: evidenceCitationMap.value.get(evidenceId) ?? 0,
  })
  requestAnimationFrame(() => {
    document.getElementById(`evidence-${evidenceId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

function goHome() {
  trackEvent('content_assessment_detail_go_home', '返回首页', {
    task_id: props.taskId,
    status: taskStatus.value,
  })
  emit('navigate', 'home')
}
function goTasks() {
  trackEvent('content_assessment_detail_go_tasks', '任务列表', {
    task_id: props.taskId,
    status: taskStatus.value,
  })
  emit('navigate', 'tasks')
}
function goBack() { props.fromTaskList ? goTasks() : goHome() }

function selectModernStage(stageKey: string) {
  const stage = stageTraces.value.find((item) => item.stage_key === stageKey)
  if (!stage || !canSelectStage(stage)) return
  selectedStageKey.value = stageKey
  expandedStages.value = new Set([...expandedStages.value, stageKey])
  nextTick(() => document.querySelector('.modern-stage-output')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
function stageIndex(stageKey: string) {
  const index = CONTENT_ASSESSMENT_STAGES.findIndex((stage) => stage.key === stageKey)
  return index >= 0 ? index + 1 : '-'
}

function formatDateTime(dateStr?: string | null): string {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

function formatDuration(seconds?: number | null): string {
  if (!seconds && seconds !== 0) return '-'
  const total = Math.floor(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return m > 0 ? `${m}分${s}秒` : `${s}秒`
}

function openClarificationDialog() {
  showClarificationDialog.value = true
  pendingClarificationOption.value = isClaimExtractionConfirmation.value ? '' : 'continue_with_assumptions'
  customClarificationInput.value = ''
  trackEvent('content_assessment_detail_clarification_open', '打开澄清弹窗', {
    task_id: props.taskId,
  })
}

function selectClarificationOption(value: string) {
  pendingClarificationOption.value = value
  customClarificationInput.value = ''
  trackEvent('content_assessment_detail_clarification_option', '选择澄清选项', {
    task_id: props.taskId,
    option_len: value.length,
  })
}

function handleCustomClarificationInput() {
  if (isClaimExtractionConfirmation.value) pendingClarificationOption.value = ''
  trackEvent('content_assessment_detail_clarification_custom_input', '输入自定义澄清', {
    task_id: props.taskId,
    note_len: customClarificationInput.value.trim().length,
  })
}

async function handleContinueGenerationAfterQuality() {
  if (!taskDetail.value?.task_id || retryLoading.value || isTaskInProgress.value) return
  if (!(await getAuthManager().checkAuth())) {
    pendingAuthenticatedAction = () => { void handleContinueGenerationAfterQuality() }
    getAuthManager().showLoginModal()
    retryError.value = '请先登录平台账号后再重试'
    return
  }
  retryLoading.value = true
  retryError.value = ''
  trackEvent('content_assessment_detail_quality_continue', '继续形成评估结论', {
    task_id: taskDetail.value.task_id,
  })
  try {
    const response = await resumeContentAssessment(taskDetail.value.task_id)
    applyTaskDetail({
      ...taskDetail.value,
      status: response.status,
      state: response.state,
    } as ContentAssessmentTaskDetail)
    if (response.status === 'running' || response.status === 'queued') {
      startSSE(taskDetail.value.task_id)
    }
    trackOutcome('content_assessment_detail_quality_continue', '继续形成评估结论', 'success', {
      task_id: taskDetail.value.task_id,
      status: response.status,
    })
  } catch (err: any) {
    trackOutcome('content_assessment_detail_quality_continue', '继续形成评估结论', 'failure', {
      task_id: taskDetail.value.task_id,
      error_kind: errorKind(err),
    })
    retryError.value = err?.message || '恢复未成功，请重试'
  } finally {
    retryLoading.value = false
  }
}

async function handleStop() {
  if (!taskDetail.value || stopLoading.value) return
  if (!(await getAuthManager().checkAuth())) {
    pendingAuthenticatedAction = () => { void handleStop() }
    getAuthManager().showLoginModal()
    loadError.value = '请先登录平台账号后再结束任务'
    return
  }
  stopLoading.value = true
  stopSSE()
  const taskId = taskDetail.value.task_id
  trackEvent('content_assessment_detail_stop', '结束任务', {
    task_id: taskId,
    status: taskStatus.value,
  })
  try {
    await stopContentAssessmentTask(taskId)
    const cleanedTraces = (taskDetail.value.stage_trace || []).map((t) =>
      t.status === 'running' ? { ...t, status: 'cancelled', summary: t.summary || '任务已终止' } : t
    )
    taskDetail.value = {
      ...taskDetail.value,
      status: 'cancelled',
      stage_trace: cleanedTraces,
      state: taskDetail.value.state
        ? {
            ...taskDetail.value.state,
            stage_trace: (taskDetail.value.state.stage_trace || []).map((t) =>
              t.status === 'running' ? { ...t, status: 'cancelled', summary: t.summary || '任务已终止' } : t
            ),
          }
        : undefined,
    }
    trackOutcome('content_assessment_detail_stop', '结束任务', 'success', {
      task_id: taskId,
    })
  } catch (err) {
    trackOutcome('content_assessment_detail_stop', '结束任务', 'failure', {
      task_id: taskId,
      error_kind: errorKind(err),
    })
  }
  finally { stopLoading.value = false }
}

type ManualClaim = { source_kind: 'material' | 'evidence'; source_document_id: string; source_evidence_id: string; source_span: string }
function emptyManualClaim(): ManualClaim { return { source_kind: 'material', source_document_id: '', source_evidence_id: '', source_span: '' } }
const manualClaims = ref<ManualClaim[]>([emptyManualClaim()])
function claimSourceValue(claim: ManualClaim) { const id = claim.source_kind === 'evidence' ? claim.source_evidence_id : claim.source_document_id; return id ? `${claim.source_kind}:${id}` : '' }
function updateClaimSource(claim: ManualClaim, value: string) { const split = value.indexOf(':'); const kind = value.slice(0, split); const id = value.slice(split + 1); claim.source_kind = kind === 'evidence' ? 'evidence' : 'material'; claim.source_document_id = kind === 'material' ? id : ''; claim.source_evidence_id = kind === 'evidence' ? id : '' }

async function confirmClarification() {
  const customInput = customClarificationInput.value.trim()
  const optionValue = isObjectConflict.value ? 'remove_conflicting_materials' : isMissingFields.value ? 'supplement_fields' : isClaimExtractionConfirmation.value ? pendingClarificationOption.value : 'continue_with_assumptions'
  if (!taskDetail.value?.task_id || clarificationSubmitting.value || !optionValue) return
  if (!(await getAuthManager().checkAuth())) {
    pendingAuthenticatedAction = () => { void confirmClarification() }
    getAuthManager().showLoginModal()
    clarificationError.value = '请先登录平台账号后再提交评估边界'
    return
  }
  if (isMissingFields.value && !supplementalInput.value.trim()) {
    clarificationError.value = '请在输入框中补充所需信息后继续。'
    return
  }
  clarificationSubmitting.value = true
  clarificationError.value = ''
  trackEvent('content_assessment_detail_clarification_submit', '提交澄清', {
    task_id: taskDetail.value.task_id,
    has_custom_note: Boolean(customInput),
    note_len: customInput.length,
  })
  try {
    if (optionValue === 'confirm_claims' && (!manualClaims.value.length || manualClaims.value.some(c => !claimSourceValue(c) || !c.source_span.trim()))) {
      throw new Error('请为每条要点选择来源材料或检索文献，并填写完整原文。')
    }
    const userNote = customInput ? `用户补充边界说明：${customInput}` : undefined
    const response = await submitContentAssessmentConfirmation(taskDetail.value.task_id, optionValue, userNote, {
      scope_summary: customInput,
      removed_file_ids: isObjectConflict.value ? removedFileIds.value : undefined,
      supplemental_input: isMissingFields.value ? supplementalInput.value.trim() : undefined,
      confirmed_claims: optionValue === 'confirm_claims' ? manualClaims.value.map((c, i) => ({ ...c, claim_id: `C-${String(i + 1).padStart(3, '0')}`, text: c.source_span })) : undefined,
    })
    applyTaskDetail({
      ...taskDetail.value,
      status: response.status,
      state: response.state,
    } as ContentAssessmentTaskDetail)
    const resumed = response.status === 'running' || response.status === 'queued'
    if (resumed) {
      showClarificationDialog.value = false
      pendingClarificationOption.value = ''
      customClarificationInput.value = ''
      supplementalInput.value = ''
      showSuccess('已确认，评估任务正在继续')
      startSSE(taskDetail.value.task_id)
    } else {
      clarificationError.value = '材料补充要求已保存，请补充材料后再次确认。'
    }
    trackOutcome('content_assessment_detail_clarification_submit', '提交澄清', 'success', {
      task_id: taskDetail.value.task_id,
      status: response.status,
      has_custom_note: Boolean(customInput),
      note_len: customInput.length,
    })
  } catch (err) {
    clarificationError.value = err instanceof Error ? err.message : '提交失败，请稍后重试'
    trackOutcome('content_assessment_detail_clarification_submit', '提交澄清', 'failure', {
      task_id: taskDetail.value.task_id,
      error_kind: errorKind(err),
      has_custom_note: Boolean(customInput),
      note_len: customInput.length,
    })
  } finally {
    clarificationSubmitting.value = false
  }
}

async function loadTask(options: { showLoading?: boolean } = {}) {
  const taskId = props.taskId
  const generation = loadGeneration
  const showLoading = options.showLoading ?? true
  if (showLoading) isLoading.value = true
  loadError.value = ''
  try {
    const detail = await getContentAssessmentTask(taskId)
    if (pageDisposed || taskId !== props.taskId || generation !== loadGeneration) return
    applyTaskDetail(detail)
    if (detail.status === 'running' || detail.status === 'queued') void startSSE(taskId)
    trackOutcome('content_assessment_detail_load', '加载任务详情', 'success', {
      task_id: taskId,
      status: detail.status,
      mode: props.mode || '',
    })
  } catch (err: any) {
    if (pageDisposed || taskId !== props.taskId || generation !== loadGeneration) return
    trackOutcome('content_assessment_detail_load', '加载任务详情', 'failure', {
      task_id: taskId,
      error_kind: errorKind(err),
      mode: props.mode || '',
    })
    if (isAuthError(err)) {
      loadError.value = '请先登录平台账号后再查看任务详情'
      getAuthManager().showLoginModal()
    } else if (isNotFoundError(err)) {
      loadError.value = '任务不存在或已被删除'
    } else {
      loadError.value = err?.message || '加载任务详情失败'
    }
  } finally {
    if (showLoading && !pageDisposed && taskId === props.taskId && generation === loadGeneration) isLoading.value = false
  }
}

function applyInitialRunIfAvailable(taskId = props.taskId) {
  const initialRun = props.initialRun
  if (!initialRun || initialRun.run_id !== taskId) return false

  applyTaskDetail(buildInitialTaskDetail(initialRun))
  isLoading.value = false
  if (initialRun.status === 'running' || initialRun.status === 'queued') {
    void startSSE(taskId)
  }
  return true
}

function applyInitialDetailIfAvailable(taskId = props.taskId) {
  const detail = props.initialDetail
  if (!detail) return false
  const initialId = detail.task_id || detail.id
  if (initialId !== taskId) return false

  applyTaskDetail(detail)
  isLoading.value = false
  if (detail.status === 'running' || detail.status === 'queued') {
    void startSSE(taskId)
  }
  return true
}

function buildInitialTaskDetail(initialRun: ContentAssessmentRunResponse): ContentAssessmentTaskDetail {
  const state = initialRun.state
  const now = new Date().toISOString()
  const rawInput = state.raw_user_input || ''
  const stageTrace = state.stage_trace || []
  const completedSteps = stageTrace.filter((stage) => stage.status === 'completed' || stage.status === 'skipped').length
  const title = rawInput.split('\n').map((item) => item.trim()).find(Boolean) || initialRun.run_id
  const timestamp = (state as any).queued_at || now

  return {
    id: initialRun.run_id,
    task_id: initialRun.run_id,
    title,
    task_type: '创新性评估',
    status: initialRun.status || 'running',
    current_stage: state.current_stage || 'pending',
    completed_steps: completedSteps,
    total_steps: stageTrace.length || CONTENT_ASSESSMENT_STAGES.length,
    needs_clarification: false,
    clarification_status: 'not_required',
    summary: '',
    user_scope: state.user_scope || '',
    user_name: state.user_scope || '',
    raw_user_input: rawInput,
    created_at: timestamp,
    updated_at: timestamp,
    completed_at: null,
    duration_seconds: null,
    state,
    output_markdown: state.output_markdown || '',
    output_json: state.output_json || {},
    stage_trace: stageTrace,
  }
}

function applyTaskDetail(detail: ContentAssessmentTaskDetail) {
  if (detail.task_id !== props.taskId && detail.id !== props.taskId) return
  taskDetail.value = detail
  reportTaskStatus(detail)
  syncActiveStageExpansion(detail.status === 'running')
  emit('workspace-meta', {
    status: detail.status || '',
    running: ['running', 'queued', 'awaiting_clarification', 'awaiting_quality_continue'].includes(detail.status || ''),
  })
}

let progressGeneration = 0
let loadGeneration = 0
let pageDisposed = false
async function startSSE(tid: string) {
  stopSSE()
  const generation = progressGeneration
  let userScope = ''
  try {
    userScope = await requirePlatformLogin()
  } catch (err: any) {
    trackOutcome('content_assessment_detail_sse_start', '订阅任务流', 'failure', {
      task_id: tid,
      error_kind: errorKind(err),
    })
    if (pageDisposed || generation !== progressGeneration || tid !== props.taskId) return
    loadError.value = err?.message || '请先登录平台账号'
    return
  }
  if (pageDisposed || generation !== progressGeneration || tid !== props.taskId) return
  trackOutcome('content_assessment_detail_sse_start', '订阅任务流', 'success', {
    task_id: tid,
  })
  sseCleanup.value = followTaskProgress<ContentAssessmentTaskDetail>({
    subscribe: (onChange, onError) => subscribeToTaskStream(tid, userScope, { onState: onChange, onDelta: onChange, onComplete: onChange, onError }),
    fetch: () => getContentAssessmentTask(tid),
    version: detail => Date.parse(detail.updated_at),
    isLive: detail => ['running', 'queued'].includes(detail.status),
    onChange: detail => { if (!pageDisposed && tid === props.taskId) applyTaskDetail(detail) },
    onSyncing: value => { progressSyncing.value = value },
  })
}

function stopSSE() { progressGeneration++; sseCleanup.value?.(); sseCleanup.value = null; progressSyncing.value = false }

function isAuthError(err: unknown): boolean {
  const message = String((err as any)?.message || err || '').toLowerCase()
  return message.includes('登录') || message.includes('login') || message.includes('401') || message.includes('unauthorized')
}

function isNotFoundError(err: unknown): boolean {
  const message = String((err as any)?.message || err || '').toLowerCase()
  return message.includes('404') || message.includes('not found') || message.includes('不存在')
}

function handleLoadRetry() {
  trackEvent('content_assessment_detail_load_retry', '重试加载详情', {
    task_id: props.taskId,
  })
  void loadTask()
}

watch(() => props.workspaceCommand?.key, () => {
  const command = props.workspaceCommand?.type
  if (!command) return
  if (command === 'clarify') openClarificationDialog()
  else if (command === 'continue') void handleContinueGenerationAfterQuality()
  else if (command === 'retry') canRetryGeneration.value ? void handleContinueGenerationAfterQuality() : handleLoadRetry()
  else if (command === 'stop') void handleStop()
  else if (command === 'download') void handleReportDownload()
})

function trackFullTextClick(item: EvidenceItem, index: number) {
  trackEvent('content_assessment_detail_full_text', '获取全文', {
    task_id: props.taskId,
    evidence_index: index + 1,
    has_paper_id: Boolean(item.paper_id),
  })
}

function reportTaskStatus(detail: ContentAssessmentTaskDetail) {
  const key = `${detail.task_id}:${detail.status}:${detail.current_stage || detail.state?.current_stage || ''}`
  if (reportedStatusKeys.value.has(key)) return
  reportedStatusKeys.value = new Set([...reportedStatusKeys.value, key])
  if (!['completed', 'failed', 'cancelled', 'awaiting_clarification', 'awaiting_quality_continue'].includes(detail.status)) return
  trackOutcome('content_assessment_detail_task_status', '任务状态变更', statusToResult(detail.status), {
    task_id: detail.task_id,
    status: detail.status,
    stage_key: detail.current_stage || detail.state?.current_stage || '',
  })
}

function statusToResult(status: string) {
  if (status === 'completed' || status === 'awaiting_clarification' || status === 'awaiting_quality_continue') return 'success'
  if (status === 'cancelled') return 'cancelled'
  return 'failure'
}

// === Sample Data ===
function buildQuestion(rank: number, title: string, question: string, _score: number, reason: string): FinalQuestion {
  return {
    question_id: `q${rank}`, rank, title, question, recommendation_reason: reason,
    score: { total_score: 0, novelty: 0, researchability: 0, evidence_support: 0, clarity: 0, domain_value: 0, feasibility: 0, score_reason: '' },
    evidence_refs: [`ref_${rank}01`, `ref_${rank}02`],
    next_steps: ['文献调研与综述', '实验方案设计', '数据采集与分析'],
  }
}

function buildSample(id: string, title: string, summary: string, traces: StageTraceItem[], questions: FinalQuestion[]): ContentAssessmentTaskDetail {
  const sampleReport: ContentAssessmentReport = {
    ready: true,
    title: `${title} · 创新性评估报告`,
    version: 'sample-1.0',
    type: '创新性评估报告',
    executiveSummary: summary,
    assessmentObject: title,
    scopeBoundary: '以示例主题、公开检索证据和当前任务边界为准；定量结论需在真实材料与检索完成后确认。',
    evidenceCount: 0,
    claimCount: questions.length,
    qualityGate: { eligibility: 'exploratory', passed: false, reviewRequired: true, blockers: [], warnings: ['示例任务未连接实时证据库，结论仅用于页面演示。'] },
    claims: questions.map((question) => ({ id: question.question_id, text: question.question, label: '当前不可判断', rationale: question.recommendation_reason, evidenceRefs: [], risks: ['当前资料未覆盖可定位的独立证据。'], rewriteAdvice: '结合实际材料和证据链进一步改写。', evidenceRelation: buildClaimEvidencePresentation({ claim_id: question.question_id, text: question.question, claim_type: 'technical', assertion_type: 'unknown', source_span: '' }, [], []) })),
    scorecard: ['指南符合性', '前沿性', '创新性与先进性', '可行性', '风险', '证据充分性', '申报文本质量'].map((dimension) => ({ dimension, assessment: '示例任务待基于真实数据评估。', scoreRange: '待定', basis: [], uncertainty: '证据不足' })),
    trend: { summary: '示例任务未返回趋势数据，正式任务将绑定当前检索结果渲染图表。', highlights: [], competition: [], risks: [] },
  }
  return {
    id, task_id: id, title, task_type: '执行创新性评估',
    status: 'completed', current_stage: 'report', completed_steps: 8, total_steps: 8,
    needs_clarification: false, clarification_status: 'not_required',
    summary, user_scope: 'sample', user_name: '示例用户', raw_user_input: title,
    created_at: '2025-06-15T10:30:00Z', updated_at: '2025-06-15T10:38:00Z',
    completed_at: '2025-06-15T10:38:00Z', duration_seconds: 480,
    stage_trace: traces, output_markdown: '', output_json: { final_questions: questions, report: sampleReport } as any,
  }
}

function viewTaskFromHeader(taskId: string) {
  if (!taskId || taskId === props.taskId) return
  emit('navigate', 'task-detail', taskId)
}

const SAMPLE_TASKS: Record<string, ContentAssessmentTaskDetail> = {
  sample_1: buildSample('sample_1', '室温超导材料的研究争议与验证路径',
    '系统梳理室温超导领域的研究争议焦点，分析不同实验验证路径的可行性与局限性，为该领域的后续研究提供清晰的方向指引。',
    [
      { stage_key: 'document_intake', stage_name: '材料解析与边界确认', status: 'completed', summary: '成功解析评估材料，识别关键词：室温超导、高压物理、材料合成、实验验证' },
      { stage_key: 'claim_extraction', stage_name: '九字段、要点与指标提取', status: 'completed', summary: '提取评估对象、关键要点和验证指标' },
      { stage_key: 'retrieval', stage_name: '检索证据池', status: 'completed', summary: '从 NSTL 文献库检索到 42 条可追溯证据' },
      { stage_key: 'landscape', stage_name: '趋势与竞争态势', status: 'completed', summary: '定位室温超导领域核心争议点和关键研究方向' },
      { stage_key: 'report', stage_name: '报告发布与导出', status: 'completed', summary: '生成包含争议分析、验证路径对比与研究建议的创新性评估报告' },
    ] as StageTraceItem[],
    [
      buildQuestion(1, '氢化物超导体的高压合成路径优化与临界温度提升机制研究', '当前氢化物超导体在超高压条件下展现出接近室温的超导性，但合成路径复杂、临界温度不稳定。本研究旨在系统对比不同氢化物体系的合成方法与超导性能，探索降低所需压力的可行策略。', 8.9, '该问题直接针对室温超导领域的核心技术瓶颈，具有明确的研究路径和重要的应用前景'),
      buildQuestion(2, '基于机器学习势函数的室温超导材料高通量筛选方法研究', '传统的第一性原理计算虽能预测超导性，但计算成本高昂。本研究探索将机器学习势函数与高通量计算相结合，加速新型超导材料的发现过程。', 8.5, '交叉学科方法创新，有望大幅提升材料筛选效率'),
      buildQuestion(3, '超导材料中电子-声子耦合机制的多尺度模拟与实验验证', '电子-声子耦合是理解超导机制的关键，但目前理论模型与实验观测存在显著差异。本研究通过多尺度模拟方法弥合理论与实验的鸿沟。', 8.3, '基础理论问题，对理解非常规超导机制具有深远意义'),
      buildQuestion(4, '极端条件下超导材料的结构稳定性与相变行为研究', '超导材料在实际应用中常面临温度、压力、磁场等极端条件，其结构稳定性直接影响性能。系统研究相变行为对材料设计至关重要。', 8.1, '工程化应用的前置基础研究，产业化价值明显'),
      buildQuestion(5, '室温超导实验可重复性危机的元科学分析', '近年来室温超导领域多起实验结果难以复现，引发学界广泛讨论。本研究从元科学视角出发，系统审查已发表实验的关键数据与方法论质量。', 7.8, '独特的元科学视角，对提升领域研究规范性具有重要贡献'),
    ]
  ),
  sample_2: buildSample('sample_2', '土壤微塑料对农作物生长与食品安全的影响机制',
    '深入分析土壤微塑料污染的来源、分布特征及其在农作物中的迁移富集规律，评估其对食品安全和人体健康的潜在风险。',
    [
      { stage_key: 'document_intake', stage_name: '材料解析与边界确认', status: 'completed', summary: '识别评估材料：土壤微塑料、农作物吸收、食品安全、健康风险' },
      { stage_key: 'claim_extraction', stage_name: '九字段、要点与指标提取', status: 'completed', summary: '提取风险表述、关键要点和评价指标' },
      { stage_key: 'retrieval', stage_name: '检索证据池', status: 'completed', summary: '检索到 56 条可追溯证据' },
      { stage_key: 'landscape', stage_name: '趋势与竞争态势', status: 'completed', summary: '定位微塑料土壤污染研究热点和关键研究方向' },
      { stage_key: 'report', stage_name: '报告发布与导出', status: 'completed', summary: '生成包含污染现状、迁移机制、风险评估与政策建议的创新性评估报告' },
    ] as StageTraceItem[],
    [
      buildQuestion(1, '微塑料在不同土壤类型中的迁移行为及对作物根系吸收的关键影响因素研究', '微塑料在土壤中的迁移受土壤质地、pH值、有机质含量等多因素影响，不同作物对其吸收效率差异显著。本研究旨在建立土壤-微塑料-作物系统的定量迁移模型。', 8.7, '填补土壤微塑料迁移机制研究的空白，为污染防控提供科学依据'),
      buildQuestion(2, '微塑料与土壤微生物群落的互作效应及其对作物养分吸收的影响', '微塑料进入土壤后可能改变微生物群落结构和功能，进而影响作物的养分吸收效率。本研究探讨微塑料-微生物-作物的三方博弈关系。', 8.4, '揭示微塑料间接影响作物生长的生物机制，研究视角新颖'),
      buildQuestion(3, '纳米级微塑料跨作物细胞膜屏障的分子机制与食品安全风险评估', '纳米塑料可能穿越作物根系的细胞屏障进入可食部分，但其分子层面的穿越机制尚不清楚。本研究结合分子动力学模拟与室内暴露实验开展系统研究。', 8.6, '前沿纳米毒理学问题，对食品安全标准制定具有直接指导价值'),
      buildQuestion(4, '不同种植模式下土壤微塑料污染的时空演变特征与源头管控策略', '设施农业、露天种植、有机农业等不同模式下的微塑料积累特征差异显著。本研究通过长期定点监测揭示演变规律并提出源头管控方案。', 8.0, '面向实际生产场景的应用研究，政策转化潜力高'),
      buildQuestion(5, '微塑料与土壤中重金属、农药残留的协同污染效应及联合毒性评价', '实际土壤环境中微塑料常与重金属、农药等传统污染物共存，协同效应可能放大生态风险。本研究建立多种污染物的联合毒性评价方法。', 8.2, '贴近真实环境污染场景，解决复合污染评价方法学难题'),
    ]
  ),
  sample_3: buildSample('sample_3', '基于深度学习的医学影像分析技术在肺癌早期筛查中的应用方法',
    '系统评估深度学习技术在肺部CT影像分析中的最新进展，比较不同模型架构与训练策略在肺结节检测、分割和分类任务中的性能表现。',
    [
      { stage_key: 'document_intake', stage_name: '材料解析与边界确认', status: 'completed', summary: '识别评估材料：深度学习、医学影像、肺癌筛查、CT影像分析' },
      { stage_key: 'claim_extraction', stage_name: '九字段、要点与指标提取', status: 'completed', summary: '提取技术要点、比较指标和临床约束' },
      { stage_key: 'retrieval', stage_name: '检索证据池', status: 'completed', summary: '检索到 72 条可追溯证据' },
      { stage_key: 'landscape', stage_name: '趋势与竞争态势', status: 'completed', summary: '定位医学影像 AI 领域前沿热点和竞争态势' },
      { stage_key: 'report', stage_name: '报告发布与导出', status: 'completed', summary: '生成包含技术对比、性能基准、临床落地路径的创新性评估报告' },
    ] as StageTraceItem[],
    [
      buildQuestion(1, '基于视觉Transformer的肺结节检测模型在多中心CT数据上的泛化性能研究', '传统CNN模型在单一中心数据上表现优异，但跨中心的泛化能力不足。本研究探究Vision Transformer架构在多中心、多设备CT数据上的检测性能与鲁棒性。', 9.1, '直击医学影像AI临床落地的核心瓶颈——跨域泛化，具有重要的临床转化价值'),
      buildQuestion(2, '自监督预训练策略在医学影像分析中的有效性研究——以肺结节良恶性分类为例', '标注数据稀缺是医学影像AI的主要挑战之一。本研究系统比较不同自监督预训练策略在有限标注样本下的肺结节分类性能。', 8.8, '解决医学影像标注瓶颈的关键技术路径，方法学创新性强'),
      buildQuestion(3, '多模态融合——结合CT影像与临床文本数据的肺癌早期筛查模型研究', '单一影像模态信息有限，融合电子病历文本数据可提供更全面的诊断依据。本研究探索影像-文本多模态融合策略。', 8.6, '多模态方法符合临床实际诊断流程，应用前景广阔'),
      buildQuestion(4, '联邦学习框架下肺结节检测模型的隐私保护训练与性能优化', '医疗数据隐私保护限制了多中心数据共享。本研究在联邦学习框架下训练肺结节检测模型，并探索非独立同分布数据场景下的性能优化策略。', 8.4, '解决医疗数据共享中的隐私合规问题，兼具技术创新与社会价值'),
      buildQuestion(5, '深度学习辅助诊断对放射科医生肺结节检出率与阅片效率影响的随机对照研究', '虽有大量算法研究，但缺乏高质量临床证据支撑。本研究设计前瞻性随机对照试验评估AI辅助对放射科医生实际诊断行为的影响。', 8.3, '提供临床实践中AI辅助效果的因果证据，证据等级高'),
    ]
  ),
}

onMounted(async () => {
  window.addEventListener('authSuccess', handleAuthSuccess)
  durationTimer = window.setInterval(() => { durationTick.value = Date.now() }, 1000)
  void refreshLiteratureCollections()
  if (isSampleMode.value) {
    taskDetail.value = SAMPLE_TASKS[props.taskId] || null
    return
  }
  if (applyInitialRunIfAvailable()) return
  if (applyInitialDetailIfAvailable()) {
    void loadTask({ showLoading: false })
    return
  }
  await loadTask()
})

onBeforeUnmount(() => {
  pageDisposed = true
  loadGeneration++
  window.removeEventListener('authSuccess', handleAuthSuccess)
  stopSSE()
  clearLiteratureProgressTimers()
  if (durationTimer) window.clearInterval(durationTimer)
})

function handleAuthSuccess() {
  const action = pendingAuthenticatedAction
  pendingAuthenticatedAction = null
  if (action) { window.setTimeout(action, 0); return }
  if (isSampleMode.value || taskDetail.value) return
  window.setTimeout(() => { void loadTask() }, 0)
}

watch(
  [shouldShowLiteratureProgress, literatureProgressType],
  syncLiteratureProgress,
  { immediate: true },
)

watch(needsClarification, (needs) => {
  if (needs && !clarificationAutoShown.value) {
    clarificationAutoShown.value = true
    openClarificationDialog()
  }
})

watch(
  () => props.taskId,
  async (next, prev) => {
    if (!next || next === prev) return
    loadGeneration++
    automaticAnalysisOpen.value = false
    materialResultsOpen.value = null
    expandedReportClaims.value = new Set()
    stopSSE()
    taskDetail.value = null
    selectedStageKey.value = ''
    claimPage.value = 1
    clarificationError.value = ''
    manualClaims.value = [emptyManualClaim()]
    resetStageExpansionState()
    resetLiteratureProgressState()
    cancelLiteratureSelection()
    highlightedEvidenceId.value = ''
    showClarificationDialog.value = false
    clarificationAutoShown.value = false
    pendingClarificationOption.value = ''
    customClarificationInput.value = ''
    if (isSampleMode.value) {
      taskDetail.value = SAMPLE_TASKS[next] || null
      syncActiveStageExpansion()
      return
    }
    if (applyInitialRunIfAvailable(next)) return
    if (applyInitialDetailIfAvailable(next)) {
      void loadTask({ showLoading: false })
      return
    }
    await loadTask()
  },
)
</script>
