<template>
  <section class="task-history-workspace">
    <header class="workspace-page-heading history-page-heading">
      <div>
        <span class="workspace-page-heading__eyebrow">TASK ARCHIVE</span>
        <h1>历史任务</h1>
        <p>集中查看两类智能体的执行进度、阶段产物与最终报告。</p>
      </div>
      <button class="workspace-action" type="button" :disabled="clearing || loading || Boolean(deletingId) || !statistics.total" @click="clearHistory"><span class="material-icons-outlined" aria-hidden="true">delete_sweep</span>{{ clearing ? (cleanupConfirmed ? '正在清理' : '确认清理') : '清空历史' }}</button>
    </header>

    <section class="history-overview" aria-label="任务概览">
      <div class="history-overview__lead">
        <span class="material-icons-outlined" aria-hidden="true">auto_awesome_motion</span>
        <div><strong>{{ statistics.total }}</strong><small>全部任务</small></div>
      </div>
      <div><i class="is-running"></i><strong>{{ overview.running }}</strong><span>进行中</span></div>
      <div><i class="is-completed"></i><strong>{{ overview.completed }}</strong><span>已完成</span></div>
      <div><i class="is-attention"></i><strong>{{ overview.attention }}</strong><span>待处理</span></div>
      <div><i class="is-stopped"></i><strong>{{ overview.stopped }}</strong><span>已停止</span></div>
    </section>

    <section class="history-toolbar" aria-label="任务筛选">
      <label class="history-toolbar__search">
        <span class="material-icons-outlined" aria-hidden="true">search</span>
        <input v-model="searchQuery" type="search" placeholder="搜索任务主题或摘要" aria-label="搜索任务主题或摘要" />
      </label>
      <label class="history-toolbar__filter">
        <span>智能体</span>
        <select v-model="agentFilter" aria-label="按智能体筛选">
          <option value="all">全部</option>
          <option value="topic">智能选题</option>
          <option value="assessment">创新性评估</option>
        </select>
      </label>
      <label class="history-toolbar__filter">
        <span>状态</span>
        <select v-model="statusFilter" aria-label="按任务状态筛选">
          <option value="all">全部</option>
          <option value="completed">已完成</option>
          <option value="running">进行中</option>
          <option value="queued">排队中</option>
          <option value="attention">待处理</option>
          <option value="awaiting_confirmation">待确认</option>
          <option value="awaiting_review">待复核</option>
          <option value="awaiting_correction">待补正</option>
          <option value="superseded">已进入补正版本</option>
          <option value="awaiting_quality_continue">待继续生成</option>
          <option value="model_unavailable">模型不可用</option>
          <option value="failed">失败</option>
          <option value="time_out">超时</option>
          <option value="cancelled">已停止</option>
        </select>
      </label>
      <label class="history-toolbar__filter">
        <span>时间</span>
        <select v-model="timeFilter" aria-label="按创建时间筛选">
          <option value="all">全部</option>
          <option value="7">近 7 天</option>
          <option value="30">近 30 天</option>
          <option value="90">近 90 天</option>
        </select>
      </label>
      <span class="history-toolbar__count">{{ total }} 个结果</span>
    </section>

    <section class="history-list-card" aria-live="polite">
      <div v-if="loading" class="history-skeleton" aria-label="正在加载历史任务">
        <div v-for="index in 5" :key="index" class="history-skeleton__row"><i></i><span></span><span></span><em></em></div>
      </div>
      <div v-else-if="loadError" class="workspace-empty workspace-empty--large workspace-empty--error">
        <span class="workspace-state-visual workspace-state-visual--error" aria-hidden="true"><span class="material-icons-outlined">cloud_off</span></span>
        <strong>历史任务暂时无法加载</strong>
        <span>{{ loadError }}</span>
        <button type="button" class="workspace-action" @click="loadTasks"><span class="material-icons-outlined" aria-hidden="true">refresh</span>重新加载</button>
      </div>
      <div v-else-if="!filteredTasks.length" class="workspace-empty workspace-empty--large">
        <span class="workspace-state-visual" aria-hidden="true"><span class="material-icons-outlined">manage_search</span></span>
        <strong>{{ statistics.total ? '没有符合条件的任务' : '还没有历史任务' }}</strong>
        <button v-if="!statistics.total" type="button" class="workspace-action workspace-action--primary" @click="emit('navigate', 'home')">创建第一个任务</button>
      </div>
      <div v-else class="history-table" role="list">
        <article
          v-for="task in filteredTasks"
          :key="taskId(task)"
          class="history-row"
          role="listitem"
          tabindex="0"
          @click="openTask(task)"
          @keydown.enter="openTask(task)"
          @keydown.space.prevent="openTask(task)"
        >
          <span class="history-row__agent-icon" :class="isTopic(task) ? 'is-topic' : 'is-assessment'" aria-hidden="true">
            <span class="material-icons-outlined">{{ isTopic(task) ? 'lightbulb' : 'fact_check' }}</span>
          </span>
          <div class="history-row__title">
            <span class="history-row__agent">{{ task.task_type || '创新性评估' }}</span>
            <strong v-overflow-title>{{ task.title || task.summary || '未命名任务' }}</strong>
            <p>{{ safeTaskSummary(task) }}</p>
          </div>
          <div class="history-row__progress">
            <div><span>{{ stageLabel(task) }}</span><b>{{ progressLabel(task) }}</b></div>
            <i><em :style="{ width: `${progressPercent(task)}%` }"></em></i>
          </div>
          <div class="history-row__meta"><span>创建时间</span><strong>{{ formatDateTime(task.created_at) }}</strong></div>
          <span class="workspace-status" :class="`workspace-status--${task.status}`">{{ recoveryLabel(task.recovery) || statusLabel(task.status) }}</span>
          <div class="history-row__actions">
            <button type="button" class="history-row__open" aria-label="查看任务" title="查看任务" @click.stop="openTask(task)"><span class="material-icons-outlined" aria-hidden="true">arrow_forward</span></button>
            <button type="button" class="history-row__delete" aria-label="删除任务" :title="canDelete(task) ? '删除任务' : '未结束任务不会被删除'" :disabled="Boolean(deletingId) || clearing || !canDelete(task)" @click.stop="removeTask(task)"><span class="material-icons-outlined" aria-hidden="true">delete_outline</span></button>
          </div>
        </article>
      </div>
    </section>
    <nav v-if="total > pageSize" class="history-pagination" aria-label="历史分页">
      <button type="button" class="workspace-action" :disabled="loading || page <= 1" aria-label="上一页" title="上一页" @click="page--"><span class="material-icons-outlined">chevron_left</span></button>
      <span>第 {{ page }} / {{ Math.max(1, Math.ceil(total / pageSize)) }} 页</span>
      <button type="button" class="workspace-action" :disabled="loading || page >= Math.ceil(total / pageSize)" aria-label="下一页" title="下一页" @click="page++"><span class="material-icons-outlined">chevron_right</span></button>
    </nav>
  </section>
</template>

<script setup lang="ts">
import { recoveryLabel } from '../services/generationRecovery'
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { deleteContentAssessmentTask } from '../services/contentAssessmentUiApi'
import { deleteTopicSelectionTask } from '../services/topicSelectionApi'
import { listAllTasks, previewHistoryCleanup, clearTaskHistory, type TaskStatistics } from '../services/contentAssessmentApi'
import { confirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { getAuthManager } from '../services/nstlAuth'

const emit = defineEmits<{ navigate: [page: string, taskId?: string] }>()
const { success, error } = useToast()
const loading = ref(false)
const loadError = ref('')
const deletingId = ref('')
const clearing = ref(false)
const cleanupConfirmed = ref(false)
const tasks = ref<any[]>([])
const searchQuery = ref('')
const statusFilter = ref('all')
const agentFilter = ref('all')
const timeFilter = ref('all')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const statistics = ref<TaskStatistics>({ total: 0, statuses: {}, agents: {} })
let loadVersion = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined

const attentionStatuses = ['awaiting_clarification', 'awaiting_confirmation', 'awaiting_quality_continue', 'awaiting_review', 'awaiting_correction', 'superseded', 'model_unavailable', 'failed', 'time_out']
const runningStatuses = ['queued', 'running', 'processing']
const overview = computed(() => ({
  running: statistics.value.status_groups?.running ?? runningStatuses.reduce((count, status) => count + (statistics.value.statuses[status] || 0), 0),
  completed: statistics.value.status_groups?.completed ?? (statistics.value.statuses.completed || 0),
  attention: statistics.value.status_groups?.attention ?? Object.entries(statistics.value.statuses).reduce((count, [status, value]) => count + (runningStatuses.includes(status) || status === 'completed' || status === 'cancelled' ? 0 : Number(value || 0)), 0),
  stopped: statistics.value.status_groups?.stopped ?? (statistics.value.statuses.cancelled || 0),
  topic: statistics.value.agents.topic?.total || 0,
  assessment: statistics.value.agents.assessment?.total || 0,
}))
const filteredTasks = computed(() => tasks.value)

function isTopic(task: any) { return task?.task_type === '智能选题' }
function taskId(task: any) { return String(task?.task_id || task?.id || '') }
function canDelete(task: any) { return ['completed', 'failed', 'cancelled', 'superseded', 'model_unavailable', 'time_out', 'blocked'].includes(task.status) }
function statusLabel(status: string) { return ({ completed: '已完成', running: '进行中', processing: '进行中', queued: '排队中', failed: '失败', time_out: '超时', cancelled: '已停止', awaiting_confirmation: '待确认', awaiting_clarification: '待确认', awaiting_quality_continue: '待继续生成', awaiting_review: '待复核', awaiting_correction: '待补正', superseded: '已进入补正版本', model_unavailable: '模型不可用', blocked: '待处理' } as Record<string, string>)[status] || '待处理' }
function progressValues(task: any) { const total = Math.max(1, Number(task.total_steps || (isTopic(task) ? 9 : 8))); return { completed: Math.min(Number(task.completed_steps || 0), total), total } }
function progressLabel(task: any) { return task.status === 'completed' ? '5/5' : recoveryLabel(task.recovery) || statusLabel(task.status) }
function progressPercent(task: any) { const value = progressValues(task); return task.status === 'completed' ? 100 : Math.round(value.completed / value.total * 100) }
function stageLabel(task: any) {
  if (task.status === 'completed') return '五步已完成'
  if (task.status === 'superseded') return '原任务保留，后续在补正版执行'
  const key = String(task.current_stage || '')
  return ({
    input_modeling: '意图识别', theme_decomposition: '意图识别', retrieval_evidence: '文献检索', landscape_trend: '趋势与交叉空白', crosslink_gap: '趋势与交叉空白', candidate_generation: '候选生成', scoring_ranking: '候选生成', quality_gate: '报告', report_publish: '报告',
    document_intake: '材料与要点', claim_extraction: '材料与要点', retrieval: '要点检索', comparison: '相关研究分析', assessment: '创新性与趋势', landscape: '创新性与趋势', report: '报告',
    completed: '整理任务结果',
  } as Record<string, string>)[key] || '等待执行'
}
function safeTaskSummary(task: any) {
  if (isTopic(task)) {
    if (task.status === 'superseded') return '原任务已进入关联补正版，可打开任务查看补正原因和新版本。'
    if (task.status === 'awaiting_correction') return '等待根据补正意见重新执行，当前任务未完成，不会自动重跑。'
    const evidence = String(task.summary || '').match(/形成\s*(\d+)\s*条[^，。]*证据/)
    const candidates = String(task.summary || '').match(/(?:输出|形成)\s*(\d+)\s*个候选选题/)
    const facts = [evidence ? `${evidence[1]} 条证据` : '', candidates ? `${candidates[1]} 个候选方向` : ''].filter(Boolean).join('、')
    return `围绕研究问题开展选题分析${facts ? `，形成 ${facts}` : ''}，可查看推荐结论、文献依据与报告。`
  }
  const summary = String(task.summary || '').trim()
  if (!summary) return '围绕评估材料、研究要点与文献证据开展创新性评估。'
  return summary.split(/(?<=[。；;])/).filter((part) => !/(?:评分|得分|优先建议|推荐结论)/.test(part)).join('').trim().replace(/主张/g, '要点') || '围绕评估材料、研究要点与文献证据开展创新性评估。'
}
function formatDateTime(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }
function openTask(task: any) { const id = taskId(task); if (id) emit('navigate', isTopic(task) ? 'topic-task-detail' : 'task-detail', id) }
async function loadTasks() {
  if (!getAuthManager().isAuthenticated()) { loadVersion++; tasks.value = []; total.value = 0; loading.value = false; getAuthManager().showLoginModal(); return }
  const version = ++loadVersion
  loading.value = true
  loadError.value = ''
  try {
    const result = await listAllTasks({ agent: agentFilter.value, status: statusFilter.value, search: searchQuery.value.trim(), days: timeFilter.value === 'all' ? undefined : Number(timeFilter.value), page: page.value, pageSize })
    if (version !== loadVersion) return
    tasks.value = result.items
    total.value = result.total
    statistics.value = result.statistics
  }
  catch (err: any) { if (version === loadVersion) { tasks.value = []; total.value = 0; loadError.value = err?.message || '请检查网络连接后重试。'; error(loadError.value) } }
  finally { if (version === loadVersion) loading.value = false }
}
async function removeTask(task: any) {
  const id = taskId(task)
  if (!id || !canDelete(task) || clearing.value || deletingId.value || !(await confirm(`确认删除“${task.title || '该任务'}”吗？此操作无法恢复。`, '删除'))) return
  deletingId.value = id
  try { if (isTopic(task)) await deleteTopicSelectionTask(id); else await deleteContentAssessmentTask(id); success('任务已删除'); if (tasks.value.length === 1 && page.value > 1) page.value--; else await loadTasks() }
  catch (err: any) { error(err?.message || '删除任务失败，请稍后重试') }
  finally { deletingId.value = '' }
}
async function clearHistory() {
  if (clearing.value || deletingId.value) return
  clearing.value = true
  try {
    const preview = await previewHistoryCleanup()
    if (!preview.eligible_count) { success('当前没有可清理的已结束任务；正在执行或等待确认的任务已保留'); return }
    if (!(await confirm(`将删除当前用户的智能选题和创新性评估历史，共 ${preview.eligible_count} 条。正在运行或尚未结束的 ${preview.skipped_count} 条任务会保留。此操作无法恢复，且不受当前筛选条件影响。`, '清空历史'))) return
    cleanupConfirmed.value = true
    const result = await clearTaskHistory()
    success(`已清理 ${result.deleted_count} 条历史，保留 ${result.skipped_count} 条未结束或状态已变化的任务`)
    if (page.value !== 1) page.value = 1
    else await loadTasks()
  } catch (err: any) { error(err?.message || '清理历史失败，请稍后重试') }
  finally { clearing.value = false; cleanupConfirmed.value = false }
}
watch([agentFilter, statusFilter, timeFilter], () => { loadVersion++; if (page.value !== 1) page.value = 1; else void loadTasks() })
watch(page, () => { void loadTasks() })
watch(searchQuery, () => { loadVersion++; clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (page.value !== 1) page.value = 1; else void loadTasks() }, 250) })
onMounted(() => {
  try {
    const filter = JSON.parse(sessionStorage.getItem('homepage_history_filter') || '{}')
    sessionStorage.removeItem('homepage_history_filter')
    if (['topic', 'assessment'].includes(filter.agent)) agentFilter.value = filter.agent
    if (['running', 'queued'].includes(filter.status)) statusFilter.value = filter.status
  } catch {}
  void loadTasks()
  window.addEventListener('authSuccess', loadTasks)
})
onBeforeUnmount(() => { loadVersion++; clearTimeout(searchTimer); window.removeEventListener('authSuccess', loadTasks) })
</script>
