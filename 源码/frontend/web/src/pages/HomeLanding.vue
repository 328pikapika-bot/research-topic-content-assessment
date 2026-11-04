<template>
  <div v-if="isPageLoading" class="hl-loading" role="status" aria-live="polite">
    <span class="hl-loading__spinner" aria-hidden="true"></span>
    <span>正在初始化服务...</span>
  </div>
  <div id="app" class="hl-page" clastag="gqHome" v-cloak>
    <div class="hl-topbar">
      <span class="hl-status" :class="healthClass" :title="healthTitle">
        <span class="hl-status__dot" aria-hidden="true"></span>{{ healthLabel }}
      </span>
    </div>

    <span class="hl-decor hl-decor--network material-icons-outlined" aria-hidden="true">hub</span>
    <span class="hl-decor hl-decor--flask material-icons-outlined" aria-hidden="true">science</span>
    <span class="hl-decor hl-decor--dna material-icons-outlined" aria-hidden="true">biotech</span>

    <div class="hl-content">
      <section class="hl-hero">
        <h2 class="hl-hero__title">{{ heroTitle }}</h2>
        <p class="hl-hero__desc">{{ heroDescription }}</p>
      </section>

      <div v-if="activeAgent" class="hl-agent-tag">
        <img :src="agentIconUrl(activeAgent)" alt="" />
        <span>{{ activeAgentDefinition?.name }}</span>
        <button type="button" aria-label="返回公共首页" title="返回公共首页" @click="selectAgent(null)"><span class="material-icons-outlined">close</span></button>
      </div>

      <section
        class="hl-composer"
        :class="{ 'is-drag-over': isDragOver, 'is-upload-active': isUploadActive }"
        @dragenter.prevent="handleDragEnter"
        @dragover.prevent
        @dragleave.prevent="handleDragLeave"
        @drop.prevent="handleDrop"
      >
        <div v-if="isDragOver" class="hl-drop-overlay" aria-hidden="true">
          <span class="material-icons-outlined">upload_file</span>
          <span>松开以上传文件</span>
        </div>
        <form class="hl-composer__form" @submit.prevent="handleSubmit">
            <input ref="fileInputRef" type="file" multiple accept=".pdf,.docx,.xlsx,.csv,.pptx,.txt,.md" hidden @change="handleFileSelect" />
            <button type="button" class="hl-upload-card" title="最多10个，每个不超过50MB；支持 PDF、DOCX、XLSX、CSV、PPTX、TXT、Markdown" aria-label="上传材料，最多10个文件，每个不超过50MB" :disabled="isParsing || isLoading" @click="handleUploadClick">
              <span class="hl-upload-card__back" aria-hidden="true"></span>
              <span class="hl-upload-card__paper" aria-hidden="true">
                <span class="material-icons-outlined hl-upload-card__glyph" aria-hidden="true">description</span>
                <span class="hl-upload-card__lines"><i></i><i></i></span>
                <span class="hl-upload-card__title">上传材料</span>
                <span class="hl-upload-card__format">PDF / WORD 等</span>
              </span>
              <span class="hl-upload-card__add" aria-hidden="true"><span class="material-icons-outlined">add</span></span>
            </button>
          <div v-if="uploadedFiles.length" class="hl-composer__attachments" aria-label="已上传材料">
            <div v-for="(f, i) in uploadedFiles" :key="`${f.name}-${f.lastModified ?? i}`" class="hl-attachment" :class="f.status">
              <span class="material-icons-outlined hl-attachment__icon" :style="{ color: getFileIconColor(f.type) }" aria-hidden="true">{{ getFileIcon(f.type) }}</span>
              <span class="hl-attachment__body">
                <span class="hl-attachment__name" :title="f.name">{{ f.name }}</span>
                <span v-if="f.status === 'error'" class="hl-attachment__status" :title="f.error">{{ getUploadErrorInfo({ code: f.errorCode }).label }}</span>
                <span v-else class="hl-attachment__status" role="status"><span v-if="f.status !== 'saved'" class="composer-parsing__spinner" aria-hidden="true"></span>{{ f.status === 'saved' ? '已解析' : f.status === 'queued' ? '等待解析' : '解析中…' }}</span>
              </span>
              <button type="button" class="hl-attachment__remove" :title="`移除 ${f.name}`" :aria-label="`移除 ${f.name}`" :disabled="isLoading" @click.stop="removeFile(i)"><span class="material-icons-outlined" aria-hidden="true">close</span></button>
              <span v-if="f.status === 'error'" class="hl-attachment__error" role="alert">{{ f.error }}</span>
            </div>
          </div>
          <span v-if="validationErrors.files" class="error-message">{{ validationErrors.files }}</span>

          <div v-if="activeAgent === 'assessment'" class="hl-assessment-type-row">
            <span id="homepage-assessment-type-label">我要评估的是：</span>
            <input v-model="assessmentTypeText" class="hl-assessment-type-input" aria-labelledby="homepage-assessment-type-label" placeholder="（论文／项目／指南）" autocomplete="off" maxlength="60" :disabled="isLoading" @input="onAssessmentTypeInput" @compositionend="onAssessmentTypeInput" @keydown.enter.prevent="focusAssessmentBody" />
          </div>
          <textarea
            ref="composerInputRef"
            v-model="form.topic"
            class="hl-composer__textarea"
            :class="{ 'has-error': validationErrors.topic }"
            rows="4"
            maxlength="12000"
            aria-label="研究问题或评估材料文字"
            :aria-describedby="activeAgent === 'assessment' && assessmentFeedback ? 'assessment-input-feedback' : undefined"
            :placeholder="composerPlaceholder"
            @input="onTopicInput"
            @compositionend="onTopicInput"
            @focus="trackEvent('content_assessment_home_input_focus', '输入研究主题')"
          ></textarea>

          <p v-if="activeAgent === 'assessment' && assessmentFeedback" id="assessment-input-feedback" class="hl-preview-warning" role="status">{{ assessmentFeedback }}</p>

          <div class="hl-composer__bar">
            <button type="button" class="hl-model-pill" disabled aria-disabled="true" title="模型切换功能暂未开放">
              <img class="hl-model-pill__star" :src="iconStarUrl" alt="" aria-hidden="true" width="13" height="13" />
              <span>{{ modelLabel }}</span>
              <span class="material-icons-outlined hl-model-pill__caret" aria-hidden="true">expand_more</span>
            </button>
            <div class="hl-composer__spacer"></div>
            <button type="button" class="hl-round-btn hl-mic" disabled aria-disabled="true" title="语音输入功能暂未开放" aria-label="语音输入功能暂未开放">
              <img :src="iconMikeUrl" alt="" aria-hidden="true" width="17" height="21" />
            </button>
            <button type="submit" class="hl-round-btn hl-send" :disabled="isLoading || isParsing || isExtracting" :title="assessmentSubmitLabel" :aria-label="assessmentSubmitLabel">
              <img v-if="!isLoading && !isExtracting" :src="iconUpArrowUrl" alt="" aria-hidden="true" width="15" height="16" />
              <span v-else class="hl-send__spinner" aria-hidden="true"></span>
            </button>
          </div>
          <span v-if="validationErrors.topic" class="error-message">{{ validationErrors.topic }}</span>
          <span v-if="submitError" class="error-message" role="alert">{{ submitError }}</span>
        </form>
      </section>

      <section v-if="showTaskReminders && authenticated && reminders.length" class="hl-reminders" aria-label="实时提醒">
        <div class="hl-section-heading hl-reminders__heading">
          <h3><span class="material-icons-outlined" aria-hidden="true">notifications</span>实时提醒</h3>
          <button type="button" class="hl-reminders__read" @click="markAllRemindersRead">一键已读</button>
        </div>
        <button v-for="item in reminders" :key="item.key" type="button" class="hl-reminder" @click="openReminder(item)">
          <span class="hl-reminder__dot" :class="`hl-reminder__dot--${item.kind}`" aria-hidden="true"></span>
          <span class="hl-reminder__body"><strong>{{ item.title }}</strong><small>{{ item.agent }} · {{ item.statusLabel }} · {{ relativeTime(item.updatedAt) }}</small></span>
          <span class="material-icons-outlined" aria-hidden="true">chevron_right</span>
        </button>
      </section>
      <section v-else-if="showTaskReminders && authenticated" class="hl-reminders hl-reminders--empty" aria-label="实时提醒">
        <div class="hl-section-heading hl-reminders__heading"><h3><span class="material-icons-outlined" aria-hidden="true">notifications_none</span>实时提醒</h3></div>
        <p>暂无新的任务提醒</p>
      </section>

      <section v-if="!activeAgent" class="hl-pills" aria-label="选择智能体">
        <button
          v-for="agent in AGENTS"
          :key="agent.id"
          type="button"
          class="hl-pill"
          :class="{ 'is-active': activeAgent === agent.id }"
          @click="selectAgent(agent.id)"
        >
          <img class="hl-pill__icon" :src="agentIconUrl(agent.id)" alt="" aria-hidden="true" />
          <span>{{ agent.name }}</span>
        </button>
      </section>

      <section v-if="activeAgent === 'topic'" class="hl-topic-options" aria-label="选题条件">
        <label>时间范围<select v-model="timeRange" aria-label="时间范围"><option value="all">不限</option><option value="3">近三年</option><option value="5">近五年</option><option value="10">近十年</option></select></label>
        <label>输出偏好<select v-model="outputPreference" aria-label="输出偏好"><option value="balanced">3个推荐＋2个探索</option><option value="recommended">5个推荐</option><option value="exploratory">5个探索</option></select></label>
      </section>

      <section class="hl-cases" aria-label="推荐案例">
        <div class="hl-section-heading"><h3>推荐案例</h3></div>
        <template v-if="activeAgent === 'assessment'">
          <div class="hl-assessment-case-columns">
            <div v-for="group in assessmentCaseGroups" :key="group.key" class="hl-assessment-case-column" :class="`is-${group.key}`" :aria-label="group.label">
              <button v-for="(sample, index) in group.items" :key="sample.id" type="button" class="hl-case" :disabled="isLoading" @click="fillCase(sample)">
                <span class="hl-case__visual material-icons-outlined" aria-hidden="true">{{ sample.icon }}</span>
                <small><b>{{ group.label.replace('案例', '') }}</b> · {{ sample.category.replace(/^.*? · /, '') }} · 案例 {{ String(index + 1).padStart(2, '0') }}</small>
                <strong>{{ sample.title }}</strong><span class="hl-case__agent">点击后直接填入{{ group.label.replace('案例', '') }}材料</span>
              </button>
            </div>
          </div>
        </template>
        <div v-else class="hl-cases-grid">
          <button v-for="(sample, index) in visibleCases" :key="sample.id" type="button" class="hl-case" :disabled="isLoading" @click="fillCase(sample)">
            <span class="hl-case__visual material-icons-outlined" aria-hidden="true">{{ sample.icon }}</span>
            <small>案例 {{ String(index + 1).padStart(2, '0') }} · {{ sample.category }}</small>
            <strong>{{ sample.title }}</strong><span class="hl-case__agent">{{ sample.agent === 'topic' ? '智能选题' : '内容评估' }}</span>
          </button>
        </div>
      </section>
    </div>

    <div id="securityWarningOverlay" class="security-warning-overlay" :style="{ display: showSecurityWarning ? 'flex' : 'none' }" @click="cancelSecurityWarning">
      <div class="security-warning-modal" role="dialog" aria-modal="true" aria-labelledby="security-warning-title" @click.stop>
        <button class="security-close-btn" type="button" aria-label="关闭安全提示" @click="cancelSecurityWarning">
          <span class="material-icons-outlined">close</span>
        </button>
        <div class="my_dialog_content">
          <p id="security-warning-title" class="my_dialog_content_tit"><span class="material-icons-outlined security-warning-symbol">error</span>提示</p>
          <p class="red">严禁在本互联网非涉密平台处理、传输国家秘密，请确认扫描、传输的文件资料不涉及国家秘密！本平台不保存传输的文件资料和解读结果。</p>
        </div>
        <div class="dialog-footer"><button class="btn" type="button" @click="confirmSecurityWarning">同意</button></div>
      </div>
    </div>

    <div id="rateLimitWarningOverlay" class="security-warning-overlay" :style="{ display: showRateLimitWarning ? 'flex' : 'none' }" @click="cancelRateLimitWarning">
      <div class="security-warning-modal" role="dialog" aria-modal="true" aria-labelledby="rate-limit-title" @click.stop>
        <button class="security-close-btn" type="button" aria-label="关闭频控提示" @click="cancelRateLimitWarning">
          <span class="material-icons-outlined">close</span>
        </button>
        <div class="my_dialog_content">
          <p id="rate-limit-title" class="my_dialog_content_tit"><span class="material-icons-outlined">warning</span>提示</p>
          <p class="red">{{ rateLimitMessage }}</p>
        </div>
        <div class="dialog-footer"><button class="btn" type="button" @click="openQuotaHistory('running')">查看执行任务</button><button class="btn" type="button" @click="openQuotaHistory('queued')">查看排队任务</button><button class="btn" type="button" @click="confirmRateLimitWarning">关闭</button></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { FileParserService } from '../services/fileParserService'
import { uploadFile } from '../services/fileUploadApi'
import { getUploadErrorInfo } from '../services/fileUploadErrors'
import { getAuthManager } from '../services/nstlAuth'
import { runContentAssessment, type ContentAssessmentRunResponse, type UploadedFileRef } from '../services/contentAssessmentUiApi'
import { runTopicSelection } from '../services/topicSelectionApi'
import { errorKind, trackEvent, trackOutcome } from '../tracker/contentAssessmentTracker'
import * as contentAssessmentApi from '../services/contentAssessmentApi'
import { ApiError } from '../services/apiErrors'
import { emptyNineDraft, mergeNineDraft, serializeNineDraft, topicYearRange, PUBLIC_CASES, TOPIC_CASES, ASSESSMENT_CASES, type OutputPreference, type HomepageCase, type SelectedAssessmentType } from '../services/homepageDraft'
import { assessmentTypeLabels, splitHomepageAssessmentInput, homepageAssessmentBody, homepageAssessmentSource, homepageAssessmentGuide, normalizeAssessmentPreview, type AssessmentInputType, recognizedAssessmentType, assessmentPreviewReady, assessmentPreviewMessage } from '../services/assessmentInput'
import { AGENTS, getAgent, isAgentAvailable, type AgentId } from '../agents'
import { useToast } from '../composables/useToast'
import iconStarUrl from '../../assets/btn-icon-star.png'
import iconMikeUrl from '../../assets/icon-mike.png'
import iconUpArrowUrl from '../../assets/icon-up-arrow.png'
import iconIntelligentUrl from '../../assets/icon-intelligent.png'
import iconInnovationUrl from '../../assets/icon-innovation.png'

type UploadedFileItem = {
  name: string
  size: number
  type: string
  status: 'queued' | 'parsing' | 'parsed' | 'saved' | 'error'
  url?: string
  fileId?: string
  parsedAt?: string
  lastModified?: number
  error?: string
  errorCode?: string
}

const props = withDefaults(defineProps<{ activeAgent?: AgentId }>(), { activeAgent: null })

const emit = defineEmits<{
  navigate: [page: string, taskId?: string, initialRun?: ContentAssessmentRunResponse]
  'select-agent': [agent: AgentId]
}>()

const toast = useToast()
const activeAgentDefinition = computed(() => getAgent(props.activeAgent))
const heroTitle = computed(() => activeAgentDefinition.value?.name || '面向科研场景的智能问答与专业能力服务')
const heroDescription = computed(() => activeAgentDefinition.value?.description || '可直接输入研究问题，也可选择智能体调用专业选题能力。')
const composerPlaceholder = computed(() => props.activeAgent === 'assessment' ? '在上方写下论文、项目或指南，下方会出现填写引导。也可直接粘贴正文，或点击左上角上传材料。' : getAgent(props.activeAgent)?.placeholder || '输入研究方向或问题，例如：生成式人工智能如何重塑科研知识发现模式？')
const modelLabel = computed(() => (props.activeAgent === 'assessment' ? '可信评估模型' : '可信选题模型'))
const AGENT_ICON_URLS: Record<string, string> = {
  topic: iconIntelligentUrl,
  assessment: iconInnovationUrl,
}
function agentIconUrl(id: string) {
  return AGENT_ICON_URLS[id] || iconInnovationUrl
}

const form = reactive({ topic: '' })
const selectedAssessmentType = ref<AssessmentInputType | 'auto'>('auto')
const assessmentTypeText = ref('')
const isUploadActive = ref(false)
let uploadMotionTimer: number | undefined
const validationErrors = reactive({ topic: '', files: '' })
const timeRange = ref('all')
const outputPreference = ref<OutputPreference>('balanced')
const assessmentType = ref<SelectedAssessmentType | 'auto'>('auto')
const nineDraft = reactive(emptyNineDraft())
const composerInputRef = ref<HTMLTextAreaElement | null>(null)
const assessmentFeedback = ref('')
const assessmentNeedsSupplement = ref(false)
const assessmentReady = ref(false)
const assessmentSubmitLabel = computed(() => props.activeAgent !== 'assessment' ? '发送' : assessmentNeedsSupplement.value ? '补充并继续' : '开始评估')
const pendingPreviews = ref(0)
const isExtracting = computed(() => pendingPreviews.value > 0)
const submitError = ref('')
const visibleCases = computed(() => props.activeAgent === 'topic' ? TOPIC_CASES : props.activeAgent === 'assessment' ? ASSESSMENT_CASES : PUBLIC_CASES)
const assessmentCaseGroups = computed(() => [
  { key: 'paper', label: '论文案例', items: ASSESSMENT_CASES.filter(sample => sample.category.startsWith('论文 ·')) },
  { key: 'project', label: '项目案例', items: ASSESSMENT_CASES.filter(sample => sample.category.startsWith('项目 ·')) },
  { key: 'guideline', label: '指南案例', items: ASSESSMENT_CASES.filter(sample => sample.category.startsWith('指南 ·')) },
])
const selectedAssessmentScenarioId = ref('')
let previewVersion = 0
let extractedSourceSignature = ''
let assessmentStartPromise: Promise<void> | null = null
let disposed = false
const isLoading = ref(false)
const isPageLoading = ref(true)
const isParsing = ref(false)
const isDragOver = ref(false)
const uploadedFiles = ref<UploadedFileItem[]>([])
const fileParser = new FileParserService()
const maxFileSize = 50 * 1024 * 1024
const maxFileCount = 10
const allowedTypes = ['.pdf', '.docx', '.xlsx', '.csv', '.pptx', '.txt', '.md']
let pendingAuthenticatedAction: (() => void) | null = null
const showSecurityWarning = ref(false)
let pendingUploadAction: { type: string; files?: File[] } | null = null
let skipSecurityCheck = false
let keepUploadedFilesForTask = false
let dragDepth = 0
let pageReadyTimer: number | undefined
const showRateLimitWarning = ref(false)
const rateLimitMessage = ref('当前正在执行的任务太多啦，稍后再试')
const quotaAgent = ref<'topic' | 'assessment'>('assessment')
const fileInputRef = ref<HTMLInputElement | null>(null)
type Reminder = { key: string; taskId: string; title: string; agent: string; statusLabel: string; updatedAt: string; kind: 'completed' | 'attention' | 'failed' }
// 首页暂不展示任务提醒，也不启动对应的数据请求和轮询。
const showTaskReminders = false
const reminders = ref<Reminder[]>([])
const authenticated = ref(false)
let reminderTimer: number | undefined
const reminderStorageKey = () => {
  const user = (getAuthManager() as { getUserInfo?: () => { username?: string } | null }).getUserInfo?.()
  return `nstl_task_reminders_read:${user?.username || 'current'}`
}

const healthState = ref<'pending' | 'ok' | 'error'>('pending')
const healthLabel = computed(() => (healthState.value === 'ok' ? '服务正常' : healthState.value === 'error' ? '服务异常' : '检测中'))
const healthClass = computed(() => `hl-status--${healthState.value}`)
const healthTitle = computed(() => (healthState.value === 'ok' ? '后端服务连接正常' : healthState.value === 'error' ? '无法连接后端服务' : '正在检测后端服务'))

function validateTopic(): boolean {
  const t = form.topic
  if (!t || t.trim().length === 0) { validationErrors.topic = '请输入研究主题'; return false }
  if (t.trim().length < 5) { validationErrors.topic = '研究主题至少需要5个字符'; return false }
  validationErrors.topic = ''
  return true
}

function validateFiles(): boolean {
  const errFiles = uploadedFiles.value.filter(f => f.status === 'error')
  if (errFiles.length > 0) {
    validationErrors.files = errFiles.map(f => `${f.name}：${f.error || '上传失败'}`).join('；')
    return false
  }
  validationErrors.files = ''
  return true
}

function onTopicInput(event?: Event) {
  if ((event as InputEvent | undefined)?.isComposing) return
  if (props.activeAgent === 'assessment') {
    const input = splitHomepageAssessmentInput(form.topic)
    if (input.type) {
      selectedAssessmentType.value = input.type
      assessmentTypeText.value = assessmentTypeLabels[input.type]
      const guided = homepageAssessmentGuide(input.text, input.type)
      form.topic = guided.text
      void nextTick(() => { if (guided.cursor !== undefined) composerInputRef.value?.setSelectionRange(guided.cursor, guided.cursor) })
    }
  }
  invalidateAssessmentSource()
  validationErrors.topic = ''
}

function selectAssessmentType(type: AssessmentInputType) {
  if (isLoading.value || selectedAssessmentType.value === type) return
  selectedAssessmentType.value = type
  assessmentTypeText.value = assessmentTypeLabels[type]
  const guided = homepageAssessmentGuide(form.topic, type)
  form.topic = guided.text
  void nextTick(() => { if (guided.cursor !== undefined) composerInputRef.value?.setSelectionRange(guided.cursor, guided.cursor) })
  invalidateAssessmentSource()
  validationErrors.topic = ''
}

function onAssessmentTypeInput(event?: Event) {
  if ((event as InputEvent | undefined)?.isComposing) return
  const input = splitHomepageAssessmentInput(assessmentTypeText.value.trim())
  const type = input.type
  selectedAssessmentType.value = type || 'auto'
  if (type) form.topic = homepageAssessmentGuide(form.topic, type).text
  invalidateAssessmentSource()
  assessmentNeedsSupplement.value = false
  validationErrors.topic = ''
}

function focusAssessmentBody(event: KeyboardEvent) {
  if (event.isComposing) return
  composerInputRef.value?.focus()
  const firstLine = form.topic.indexOf('\n')
  const cursor = firstLine < 0 ? form.topic.length : firstLine
  composerInputRef.value?.setSelectionRange(cursor, cursor)
}

function handleUploadClick() {
  if (uploadMotionTimer !== undefined) window.clearTimeout(uploadMotionTimer)
  isUploadActive.value = true
  uploadMotionTimer = window.setTimeout(() => {
    isUploadActive.value = false
    uploadMotionTimer = undefined
  }, 1100)
  triggerFileInput()
}

function assessmentMaterialSource() {
  return homepageAssessmentSource(form.topic, recognizedAssessmentType(selectedAssessmentType.value) ? selectedAssessmentType.value : undefined)
}

function invalidateAssessmentSource() {
  previewVersion++
  extractedSourceSignature = ''
  selectedAssessmentScenarioId.value = ''
  submitError.value = ''
  assessmentReady.value = false
  assessmentType.value = 'auto'
  assessmentFeedback.value = ''
}

async function checkHealth() {
  try {
    const response = await fetch('/system/health')
    healthState.value = response.ok ? 'ok' : 'error'
  } catch {
    healthState.value = 'error'
  }
}

async function ensureAuthenticated(actionName = '操作', continuation?: () => void) {
  try {
    await getAuthManager().requirePlatformLogin()
    return true
  } catch {
    pendingAuthenticatedAction = continuation || null
    getAuthManager().showLoginModal()
    toast.show(`请先登录后再${actionName}`, 'info')
    return false
  }
}

function showSecurityWarningModal(actionType: string, files?: File[]) {
  pendingUploadAction = { type: actionType, files }
  showSecurityWarning.value = true
  trackEvent('content_assessment_home_security_warning_open', '安全提示', {
    action: actionType,
    file_count: files?.length ?? 0,
  })
}

function confirmSecurityWarning() {
  const action = pendingUploadAction
  pendingUploadAction = null
  showSecurityWarning.value = false
  trackOutcome('content_assessment_home_security_warning', '安全提示确认', 'success', {
    action: action?.type || '',
    file_count: action?.files?.length ?? 0,
  })
  if (action?.type === 'click') {
    skipSecurityCheck = true
    setTimeout(() => {
      fileInputRef.value?.click()
      skipSecurityCheck = false
    }, 0)
  } else if (action?.type === 'drop' && action.files?.length) {
    void processFiles(action.files)
  }
}

function cancelSecurityWarning() {
  trackOutcome('content_assessment_home_security_warning', '安全提示取消', 'cancelled', {
    action: pendingUploadAction?.type || '',
    file_count: pendingUploadAction?.files?.length ?? 0,
  })
  showSecurityWarning.value = false
  pendingUploadAction = null
}

function triggerFileInput() {
  if (showSecurityWarning.value || skipSecurityCheck || isParsing.value || isLoading.value || disposed) return
  trackEvent('content_assessment_home_upload_click', '上传区点击', {
    file_count: uploadedFiles.value.length,
  })
  void ensureAuthenticated('上传文件', triggerFileInput).then((isLoggedIn) => {
    if (!isLoggedIn) return
    if (uploadedFiles.value.length >= maxFileCount) {
      trackOutcome('content_assessment_home_upload_click', '上传区点击', 'blocked', { reason: 'max_file_count' })
      toast.show(`最多上传${maxFileCount}个文件`, 'info')
      return
    }
    showSecurityWarningModal('click')
  })
}

function handleDragEnter() {
  dragDepth += 1
  isDragOver.value = true
}

function handleDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) isDragOver.value = false
}

function handleDrop(event: DragEvent) {
  dragDepth = 0
  isDragOver.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  trackEvent('content_assessment_home_upload_drop', '拖放上传', { file_count: files.length })
  if (!files.length) return
  requestDroppedUpload(files)
}

function requestDroppedUpload(files: File[]) {
  void ensureAuthenticated('上传文件', () => requestDroppedUpload(files)).then((isLoggedIn) => {
    if (!isLoggedIn || showSecurityWarning.value) return
    if (uploadedFiles.value.length >= maxFileCount) {
      trackOutcome('content_assessment_home_upload_drop', '拖放上传', 'blocked', { reason: 'max_file_count', file_count: files.length })
      toast.show(`最多上传${maxFileCount}个文件`, 'info')
      return
    }
    showSecurityWarningModal('drop', files)
  })
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  trackEvent('content_assessment_home_file_select', '选择文件', { file_count: files.length })
  void ensureAuthenticated('上传文件').then((ok) => {
    if (ok) void processFiles(files)
    input.value = ''
  })
}

function reminderStatus(status: string): { label: string; kind: Reminder['kind'] } | null {
  if (status === 'completed') return { label: '报告已完成', kind: 'completed' }
  if (status === 'failed' || status === 'time_out') return { label: status === 'time_out' ? '任务超时' : '任务失败', kind: 'failed' }
  if (['awaiting_clarification', 'awaiting_confirmation', 'awaiting_quality_continue', 'awaiting_review', 'awaiting_correction', 'model_unavailable'].includes(status)) return { label: '等待补充或复核', kind: 'attention' }
  return null
}

function relativeTime(value: string) {
  const diff = Math.max(0, Date.now() - new Date(value || Date.now()).getTime())
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  return `${Math.floor(hours / 24)}天前`
}

async function loadReminders() {
  if (!showTaskReminders) return
  authenticated.value = getAuthManager().isAuthenticated()
  if (!authenticated.value) { reminders.value = []; return }
  try {
    // Keep the homepage compatible with lightweight embedded/test clients that
    // only implement nine-field preview; reminders are simply unavailable there.
    const listTasks = (contentAssessmentApi as typeof contentAssessmentApi & {
      listAllTasks?: (params: { status: string; page: number; pageSize: number }) => Promise<{ items?: any[] }>
    }).listAllTasks
    if (!listTasks) { reminders.value = []; return }
    const result = await listTasks({ status: 'all', page: 1, pageSize: 50 })
    let stored: unknown = []
    try { stored = JSON.parse(localStorage.getItem(reminderStorageKey()) || '[]') } catch { stored = [] }
    const read = new Set<string>(Array.isArray(stored) ? stored.map(String) : [])
    reminders.value = (result.items || []).map((task: any) => {
      const state = reminderStatus(String(task.status || ''))
      if (!state) return null
      const taskId = String(task.task_id || task.id || '')
      return { key: `${taskId}:${task.status}:${task.updated_at || ''}`, taskId, title: task.title || '未命名任务', agent: task.task_type || '创新性评估', statusLabel: state.label, updatedAt: task.updated_at || task.completed_at || task.created_at || '', kind: state.kind }
    }).filter((item: Reminder | null): item is Reminder => Boolean(item && item.taskId && !read.has(item.key))).slice(0, 8)
  } catch { /* 提醒服务异常不应阻断首页输入 */ }
}

function markAllRemindersRead() {
  let stored: unknown = []
  try { stored = JSON.parse(localStorage.getItem(reminderStorageKey()) || '[]') } catch { stored = [] }
  const current = new Set<string>(Array.isArray(stored) ? stored.map(String) : [])
  reminders.value.forEach(item => current.add(item.key))
  localStorage.setItem(reminderStorageKey(), JSON.stringify(Array.from(current).slice(-200)))
  reminders.value = []
}

function openReminder(item: Reminder) {
  let stored: unknown = []
  try { stored = JSON.parse(localStorage.getItem(reminderStorageKey()) || '[]') } catch { stored = [] }
  const current = new Set<string>(Array.isArray(stored) ? stored.map(String) : [])
  current.add(item.key)
  localStorage.setItem(reminderStorageKey(), JSON.stringify(Array.from(current).slice(-200)))
  emit('navigate', item.agent === '智能选题' ? 'topic-task-detail' : 'task-detail', item.taskId)
}

function validateFile(file: File): boolean {
  if (file.size === 0) { toast.error(`"${file.name}" 为空文件`); return false }
  if (file.size > maxFileSize) { toast.error(`"${file.name}" 超过50MB限制`); return false }
  const ext = '.' + (file.name.split('.').pop()?.toLowerCase() || '')
  if (ext === '.doc') { toast.error(`"${file.name}" 暂不支持 DOC，请先另存为 DOCX`); return false }
  if (!allowedTypes.includes(ext)) { toast.error(`"${file.name}" 格式不支持，请上传 PDF、DOCX、XLSX、CSV、PPTX、TXT 或 Markdown`); return false }
  const sig = `${file.name}_${file.size}_${(file as any).lastModified || ''}`
  if (uploadedFiles.value.some(f => `${f.name}_${f.size}_${f.lastModified || ''}` === sig)) {
    toast.show(`"${file.name}" 已存在`, 'info')
    return false
  }
  return true
}

function getFileType(fileName: string) {
  return fileParser.getFileType(fileName)
}

async function processFiles(files: File[]) {
  if (isParsing.value || isLoading.value || disposed) return
  const remaining = maxFileCount - uploadedFiles.value.length
  if (remaining <= 0) { toast.show(`最多上传${maxFileCount}个文件`, 'info'); return }
  const valid = files.filter(validateFile)
  if (!valid.length) return
  const toUpload = valid.length > remaining ? valid.slice(0, remaining) : valid
  if (valid.length > remaining) toast.show(`本次最多可添加${remaining}个文件`, 'info')
  await uploadFilesToServer(toUpload)
}

async function uploadFilesToServer(files: File[]) {
  if (!files?.length) return
  invalidateAssessmentSource()
  isParsing.value = true
  try {
    for (const file of files) {
      if (disposed) break
      try {
        uploadedFiles.value.push({
          name: file.name,
          size: file.size,
          type: getFileType(file.name),
          status: 'parsing',
          url: '',
          lastModified: (file as any).lastModified,
        })
        const result = await uploadFile(file)
        if (disposed) {
          if (result.fileId) void fileParser.deleteParsedFile(result.fileId).catch(() => {})
          break
        }
        const idx = uploadedFiles.value.findIndex(f => f.name === file.name && f.lastModified === file.lastModified && f.status === 'parsing')
        if (idx !== -1) {
          uploadedFiles.value[idx].status = 'saved'
          uploadedFiles.value[idx].fileId = result.fileId || ''
          uploadedFiles.value[idx].url = result.url || ''
          uploadedFiles.value[idx].parsedAt = new Date().toISOString()
        } else if (result.fileId) void fileParser.deleteParsedFile(result.fileId).catch(() => {})
      } catch (err: any) {
        const idx = uploadedFiles.value.findIndex(f => f.name === file.name)
        const errorCode = extractUploadErrorCode(err)
        const friendlyError = normalizeFileParseError(err)
        if (idx !== -1) {
          uploadedFiles.value[idx].status = 'error'
          uploadedFiles.value[idx].error = friendlyError
          uploadedFiles.value[idx].errorCode = errorCode
        }
        if (errorCode !== 'sensitive_content_detected') toast.error(friendlyError)
        trackOutcome('content_assessment_home_file_parse', '文件解析', 'failure', {
          file_type: getFileType(file.name),
          file_size: file.size,
          error_code: errorCode,
          error_kind: errorKind(err),
        })
      }
    }
    const saved = uploadedFiles.value.filter(f => f.status === 'saved')
    trackOutcome('content_assessment_home_file_parse', '文件上传解析', 'success', {
      file_count: saved.length,
      file_types: Array.from(new Set(saved.map(f => f.type))).join(','),
    })
  } finally {
    isParsing.value = false
  }
}

function normalizeFileParseError(error: unknown): string {
  return getUploadErrorInfo(error).message
}

function extractUploadErrorCode(error: unknown): string {
  return getUploadErrorInfo(error).code
}

function removeFile(idx: number) {
  const item = uploadedFiles.value[idx]
  void ensureAuthenticated('删除文件').then((isLoggedIn) => {
    if (!isLoggedIn || disposed) return
    const currentIndex = uploadedFiles.value.indexOf(item)
    if (currentIndex < 0) return
    const removed = uploadedFiles.value[currentIndex]
    uploadedFiles.value.splice(currentIndex, 1)
    invalidateAssessmentSource()
    trackOutcome('content_assessment_home_file_remove', '移除文件', 'success', {
      file_type: removed?.type || '',
      file_count: uploadedFiles.value.length,
    })
    if (removed?.fileId) fileParser.deleteParsedFile(removed.fileId).catch(() => {})
    if (validationErrors.files) validateFiles()
  })
}

function getFileIcon(t: string): string {
  const m: Record<string, string> = { pdf: 'picture_as_pdf', txt: 'text_snippet', md: 'description' }
  return m[t] || 'insert_drive_file'
}

function getFileIconColor(t: string): string {
  const m: Record<string, string> = { pdf: '#ef4444', txt: '#6b7280', md: '#10b981' }
  return m[t] || '#6b7280'
}

function selectAgent(agent: AgentId) {
  emit('select-agent', agent)
  trackEvent('content_assessment_home_agent_select', '选择智能体', { agent })
}

async function handleSubmit() {
  if (isLoading.value || isParsing.value || isExtracting.value) return
  if (!props.activeAgent) {
    toast.show('请选择智能体后发送', 'info')
    return
  }
  if (props.activeAgent === 'assessment') {
    await startAssessmentFromComposer()
    return
  }
  await createTask()
}

function fileReferences(): UploadedFileRef[] {
  return uploadedFiles.value.filter(file => file.status === 'saved' && file.fileId).map(file => ({ file_id: file.fileId!, name: file.name, type: file.type, size: file.size, url: file.url || '', parsed_at: file.parsedAt || '' }))
}

function assessmentSourceSignature() {
  const files = fileReferences().map(file => [file.file_id, file.name, file.size, file.parsed_at].join(':')).sort()
  return JSON.stringify([assessmentMaterialSource(), files])
}

async function startAssessmentFromComposer() {
  if (assessmentStartPromise) return assessmentStartPromise
  assessmentStartPromise = runAssessmentStartFlow().finally(() => { assessmentStartPromise = null })
  return assessmentStartPromise
}

async function runAssessmentStartFlow() {
  if (isLoading.value || isParsing.value || isExtracting.value || disposed) return
  if ((!homepageAssessmentBody(form.topic) && !fileReferences().length) || !validateFiles()) {
    if (!homepageAssessmentBody(form.topic) && !fileReferences().length) validationErrors.topic = '请填写评估内容或上传材料，仅填写类型还不能开始评估。'
    return
  }
  const signature = assessmentSourceSignature()
  if (extractedSourceSignature !== signature || !assessmentReady.value) {
    const ready = await extractNineFields()
    if (ready === null || disposed || assessmentSourceSignature() !== signature) return
    if (!ready) {
      await nextTick()
      composerInputRef.value?.focus()
      return
    }
  }
  await createTask()
}

async function extractNineFields(): Promise<boolean | null> {
  if (isParsing.value || disposed) return null
  if (!await ensureAuthenticated('开始内容评估', startAssessmentFromComposer)) return null
  const signature = assessmentSourceSignature()
  const version = ++previewVersion
  pendingPreviews.value++
  assessmentFeedback.value = '正在识别材料类型与研究内容…'
  try {
    const sourceText = assessmentMaterialSource()
    const requestedType = recognizedAssessmentType(selectedAssessmentType.value) ? selectedAssessmentType.value : splitHomepageAssessmentInput(form.topic).type || 'auto'
    const result = normalizeAssessmentPreview(await contentAssessmentApi.previewNineFields(sourceText, fileReferences(), requestedType), sourceText)
    if (disposed || version !== previewVersion || props.activeAgent !== 'assessment' || assessmentSourceSignature() !== signature) return null
    mergeNineDraft(nineDraft, result.nine_fields, new Set())
    assessmentType.value = recognizedAssessmentType(result.resolved_artifact_type) ? result.resolved_artifact_type : 'auto'
    assessmentFeedback.value = assessmentPreviewMessage(result, sourceText)
    assessmentReady.value = assessmentPreviewReady(result)
    if (requestedType !== 'auto' && recognizedAssessmentType(result.resolved_artifact_type) && result.resolved_artifact_type !== requestedType) {
      assessmentReady.value = false
      assessmentNeedsSupplement.value = false
      extractedSourceSignature = ''
      assessmentFeedback.value = '本次识别的材料类型与填写类型不一致，请重试；文字和材料已保留。'
      return false
    }
    assessmentNeedsSupplement.value = !assessmentReady.value && !result.extraction_failed
    extractedSourceSignature = result.extraction_failed ? '' : signature
    return assessmentReady.value
  } catch (error: unknown) {
    if (!disposed && version === previewVersion && assessmentSourceSignature() === signature) {
      assessmentFeedback.value = error instanceof Error && error.name === 'AssessmentServiceVersionError'
        ? error.message : '材料识别暂未完成，请重试，或在输入框中粘贴材料正文。'
      assessmentReady.value = false
    }
    return false
  } finally { pendingPreviews.value-- }
}
async function createTask() {
  if (isLoading.value || isParsing.value || isExtracting.value || !props.activeAgent) return
  const agent = props.activeAgent
  if (!isAgentAvailable(agent)) return
  if (agent === 'assessment' && (!assessmentReady.value || extractedSourceSignature !== assessmentSourceSignature())) return
  if (agent === 'topic' && !validateTopic()) return
  if (!validateFiles() || !await ensureAuthenticated('创建任务', createTask)) return
  if (disposed || isLoading.value || isParsing.value || isExtracting.value || props.activeAgent !== agent) return
  if (agent === 'assessment' && (!assessmentReady.value || extractedSourceSignature !== assessmentSourceSignature())) return
  isLoading.value = true
  submitError.value = ''
  const submitPayload = { agent, input_len: form.topic.trim().length, file_count: fileReferences().length }
  trackEvent('content_assessment_home_generate_click', '开始生成', submitPayload)
  try {
    if (agent === 'topic') {
      const result = await runTopicSelection({ user_input: form.topic.trim(), year_range: topicYearRange(timeRange.value), candidate_count: 5, output_preference: outputPreference.value, uploaded_files: fileReferences() })
      keepUploadedFilesForTask = true
      emit('navigate', 'topic-task-running', result.run_id, result as unknown as ContentAssessmentRunResponse)
    } else {
      const result = await runContentAssessment({
        user_input: assessmentMaterialSource() || nineDraft.research_direction.trim(),
        nine_fields: serializeNineDraft(nineDraft),
        artifact_type: assessmentType.value,
        uploaded_files: fileReferences(),
        scenario_id: selectedAssessmentScenarioId.value || undefined,
      })
      keepUploadedFilesForTask = true
      emit('navigate', 'task-running', result.run_id, result)
    }
  } catch (err: any) {
    if (err instanceof ApiError && err.status === 429) {
      quotaAgent.value = agent
      rateLimitMessage.value = err.message
      showRateLimitWarning.value = true
    } else if (err instanceof ApiError && err.status === 401) {
      getAuthManager().showLoginModal()
    } else {
      submitError.value = err?.message || '创建任务失败，请重试'
      toast.error(submitError.value)
    }
    trackOutcome('content_assessment_home_generate', '创建任务', 'failure', { ...submitPayload, error_kind: errorKind(err) })
  } finally { isLoading.value = false }
}

function openQuotaHistory(status: string) {
  sessionStorage.setItem('homepage_history_filter', JSON.stringify({ agent: quotaAgent.value, status }))
  showRateLimitWarning.value = false
  emit('navigate', 'tasks')
}
function fillCase(sample: HomepageCase) {
  if (isLoading.value) return
  invalidateAssessmentSource()
  const input = sample.agent === 'assessment' ? splitHomepageAssessmentInput(sample.input) : { text: sample.input, type: undefined }
  form.topic = input.text
  selectedAssessmentType.value = input.type || 'auto'
  assessmentTypeText.value = input.type ? assessmentTypeLabels[input.type] : ''
  selectedAssessmentScenarioId.value = sample.agent === 'assessment' ? sample.scenarioId || '' : ''
  validationErrors.topic = ''
  assessmentFeedback.value = ''
  if (sample.agent === 'assessment') {
    assessmentType.value = 'auto'
    mergeNineDraft(nineDraft, {}, new Set())
    assessmentNeedsSupplement.value = false
  }
  emit('select-agent', sample.agent)
  trackEvent('content_assessment_home_sample_click', '填充案例', { sample_id: sample.id })
}

function confirmRateLimitWarning() {
  trackOutcome('content_assessment_home_rate_limit_warning', '频控提示确认', 'success')
  showRateLimitWarning.value = false
}

function cancelRateLimitWarning() {
  trackOutcome('content_assessment_home_rate_limit_warning', '频控提示取消', 'cancelled')
  showRateLimitWarning.value = false
}

watch(() => props.activeAgent, (agent) => {
  previewVersion++
  extractedSourceSignature = ''
  submitError.value = ''
  assessmentReady.value = false
  assessmentFeedback.value = ''
  assessmentType.value = 'auto'
  if (agent === 'assessment') {
    const input = splitHomepageAssessmentInput(form.topic)
    if (input.type) {
      selectedAssessmentType.value = input.type
      assessmentTypeText.value = assessmentTypeLabels[input.type]
    }
    form.topic = input.text
  }
  if (agent !== 'assessment') {
    selectedAssessmentScenarioId.value = ''
    assessmentNeedsSupplement.value = false
  }
})

function handleSecKeydown(e: KeyboardEvent) {
  if (!showSecurityWarning.value) return
  if (e.key === 'Escape') cancelSecurityWarning()
  else if (e.key === 'Enter') confirmSecurityWarning()
}

function handleRateKeydown(e: KeyboardEvent) {
  if (!showRateLimitWarning.value) return
  if (e.key === 'Escape') cancelRateLimitWarning()
  else if (e.key === 'Enter') confirmRateLimitWarning()
}

function handleAuthSuccess() {
  authenticated.value = true
  const action = pendingAuthenticatedAction
  pendingAuthenticatedAction = null
  if (action) window.setTimeout(action, 0)
  void loadReminders()
  if (showTaskReminders && reminderTimer === undefined) reminderTimer = window.setInterval(() => void loadReminders(), 30000)
}

function handleAuthLogout() {
  authenticated.value = false
  reminders.value = []
  pendingAuthenticatedAction = null
}

onMounted(() => {
  document.addEventListener('keydown', handleSecKeydown)
  document.addEventListener('keydown', handleRateKeydown)
  window.addEventListener('authSuccess', handleAuthSuccess)
  window.addEventListener('authLogout', handleAuthLogout)
  authenticated.value = getAuthManager().isAuthenticated()
  if (showTaskReminders && authenticated.value) {
    void loadReminders()
    reminderTimer = window.setInterval(() => void loadReminders(), 30000)
  }
  const initialization = Promise.allSettled([
    checkHealth(),
    getAuthManager().checkAuth(),
  ])
  pageReadyTimer = window.setTimeout(() => {
    isPageLoading.value = false
  }, 3000)
  void initialization.finally(() => {
    authenticated.value = getAuthManager().isAuthenticated()
    if (showTaskReminders && authenticated.value) {
      void loadReminders()
      if (reminderTimer === undefined) reminderTimer = window.setInterval(() => void loadReminders(), 30000)
    }
    if (pageReadyTimer !== undefined) window.clearTimeout(pageReadyTimer)
    pageReadyTimer = undefined
    isPageLoading.value = false
  })
})

onBeforeUnmount(() => {
  disposed = true
  if (uploadMotionTimer !== undefined) window.clearTimeout(uploadMotionTimer)
  previewVersion++
  if (pageReadyTimer !== undefined) window.clearTimeout(pageReadyTimer)
  if (!keepUploadedFilesForTask) {
    for (const file of uploadedFiles.value) {
      if (file.fileId) void fileParser.deleteParsedFile(file.fileId)
    }
  }
  document.removeEventListener('keydown', handleSecKeydown)
  document.removeEventListener('keydown', handleRateKeydown)
  window.removeEventListener('authSuccess', handleAuthSuccess)
  window.removeEventListener('authLogout', handleAuthLogout)
  if (reminderTimer !== undefined) window.clearInterval(reminderTimer)
})
</script>
