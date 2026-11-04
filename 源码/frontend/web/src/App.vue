<template>
  <div class="app-shell">
    <AppSidebar
      :active-agent="activeAgent"
      :variant="route === 'home' ? 'home' : 'default'"
      @select-agent="onSelectAgent"
      @new-chat="onNewChat"
      @open-task="onOpenTask"
      @open-tasks="onViewAllTasks"
    />
    <main class="app-main">
      <WorkspaceHeader
        v-if="route !== 'home'"
        :context="workspaceContext"
        :show-back="route === 'tasks' || route === 'task-running' || route === 'task-detail' || route === 'topic-task-running' || route === 'topic-task-detail'"
        :back-label="workspaceBackLabel"
        :task-status="taskWorkspace.status"
        :is-running="taskWorkspace.running"
        @back="onWorkspaceBack"
        @command="onWorkspaceCommand"
      />
      <template v-if="route === 'home'">
        <ResearchWorkbench :key="'workbench-' + homeKey" :active-agent="activeAgent" @select-agent="onSelectAgent" @navigate="onNavigate" />
      </template>
      <TaskHistoryPage v-else-if="route === 'tasks'" @navigate="onNavigate" />
      <TaskDetailPage
        v-else-if="route === 'task-running'"
        :task-id="selectedTaskId"
        mode="running"
        :initial-run="selectedInitialRun"
        :workspace-command="workspaceCommand"
        @workspace-meta="onTaskWorkspaceMeta"
        @navigate="onNavigate"
      />
      <TopicTaskDetailPage
        v-else-if="route === 'topic-task-running'"
        :task-id="selectedTaskId"
        mode="running"
        :initial-run="selectedInitialTopicRun"
        :workspace-command="workspaceCommand"
        @workspace-meta="onTaskWorkspaceMeta"
        @navigate="onNavigate"
      />
      <TopicTaskDetailPage
        v-else-if="route === 'topic-task-detail'"
        :task-id="selectedTaskId"
        mode="detail"
        :initial-run="selectedInitialTopicRun"
        :workspace-command="workspaceCommand"
        @workspace-meta="onTaskWorkspaceMeta"
        @navigate="onNavigate"
      />
      <TaskDetailPage
        v-else-if="route === 'task-detail'"
        :task-id="selectedTaskId"
        mode="detail"
        :from-task-list="fromTaskList"
        :initial-detail="selectedInitialDetail"
        :workspace-command="workspaceCommand"
        @workspace-meta="onTaskWorkspaceMeta"
        @navigate="onNavigate"
      />
    </main>
  </div>
  <ToastMessage />
  <ConfirmDialog />
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import WorkspaceHeader from './components/WorkspaceHeader.vue'
import ResearchWorkbench from './pages/ResearchWorkbench.vue'
import TaskHistoryPage from './pages/TaskHistoryPage.vue'
import TaskDetailPage from './pages/TaskDetailPage.vue'
import TopicTaskDetailPage from './pages/TopicTaskDetailPage.vue'
import ToastMessage from './components/ToastMessage.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { getAgent } from './agents'
import type { ContentAssessmentRunResponse, ContentAssessmentTaskDetail } from './services/contentAssessmentUiApi'
import { trackPage, type ContentAssessmentPage } from './tracker/contentAssessmentTracker'

const route = ref('home')
const activeAgent = ref<'assessment' | 'topic' | null>(null)
const homeKey = ref(0)
const selectedTaskId = ref('')
const fromTaskList = ref(false)
const selectedInitialRun = ref<ContentAssessmentRunResponse | null>(null)
const selectedInitialDetail = ref<ContentAssessmentTaskDetail | null>(null)
const selectedInitialTopicRun = ref<any>(null)
const workspaceCommand = ref<{ type: 'clarify' | 'continue' | 'retry' | 'stop' | 'download'; key: number } | null>(null)
const taskWorkspace = ref({ status: '', running: false })
const navigationSessionKey = 'content_assessment_active_view'
const restorableRoutes = new Set(['tasks', 'task-running', 'task-detail', 'topic-task-running', 'topic-task-detail'])
const workspaceContext = computed(() => {
  if (route.value === 'tasks') return '历史任务'
  if (route.value === 'task-running') return '任务执行'
  if (route.value === 'task-detail') return '任务详情'
  if (route.value === 'topic-task-running') return '选题任务执行'
  if (route.value === 'topic-task-detail') return '选题任务详情'
  return getAgent(activeAgent.value)?.name || '科研智能服务工作台 / 新建对话'
})
const workspaceBackLabel = computed(() => route.value === 'tasks' || !fromTaskList.value ? '返回首页' : '返回历史任务')
function pageToTrack(page: string, taskId?: string): ContentAssessmentPage {
  if (page === 'tasks') return 'tasks'
  if (page === 'task-running') return 'task_running'
  if (page === 'task-detail') return taskId?.startsWith('sample_') ? 'sample_detail' : 'task_detail'
  if (page === 'topic-task-running') return 'task_running'
  if (page === 'topic-task-detail') return 'task_detail'
  return 'home'
}

function onNavigate(page: string, taskId?: string, payload?: ContentAssessmentRunResponse | ContentAssessmentTaskDetail | null) {
  if (taskId) selectedTaskId.value = taskId
  if (page === 'home' || page === 'tasks') selectedTaskId.value = ''
  if (page === 'topic-task-running' || page === 'topic-task-detail') activeAgent.value = 'topic'
  if (page === 'task-running' || page === 'task-detail') activeAgent.value = 'assessment'
  selectedInitialRun.value = page === 'task-running' ? (payload as ContentAssessmentRunResponse | null) ?? null : null
  selectedInitialDetail.value = page === 'task-detail' ? (payload as ContentAssessmentTaskDetail | null) ?? null : null
  selectedInitialTopicRun.value = page === 'topic-task-running' ? (payload as any) ?? null : null
  fromTaskList.value = (page === 'task-detail' || page === 'topic-task-detail') && route.value === 'tasks'
  route.value = page
  if (restorableRoutes.has(page)) sessionStorage.setItem(navigationSessionKey, JSON.stringify({ page, taskId: selectedTaskId.value }))
  else sessionStorage.removeItem(navigationSessionKey)
  void nextTick(() => window.scrollTo({ top: 0, left: 0, behavior: 'auto' }))
  if (!['task-running', 'task-detail', 'topic-task-running', 'topic-task-detail'].includes(page)) taskWorkspace.value = { status: '', running: false }
  trackPage(pageToTrack(page, taskId), {
    route: page,
    task_id: selectedTaskId.value || '',
  })
}

function onWorkspaceBack() {
  if (route.value === 'tasks') { onNavigate('home'); return }
  if (fromTaskList.value) { onNavigate('tasks'); return }
  onNavigate('home')
}
function onWorkspaceCommand(type: 'clarify' | 'continue' | 'retry' | 'stop' | 'download') {
  workspaceCommand.value = { type, key: Date.now() }
}
function onTaskWorkspaceMeta(meta: { status: string; running: boolean }) { taskWorkspace.value = meta }

function onSelectAgent(agent: 'assessment' | 'topic' | null) {
  activeAgent.value = agent
  if (route.value !== 'home') onNavigate('home')
}

function onNewChat() {
  activeAgent.value = null
  homeKey.value++
  onNavigate('home')
}

function onOpenTask(taskId: string, taskType?: string) {
  const normalizedTaskId = String(taskId || '').trim()
  if (!normalizedTaskId) return
  selectedInitialRun.value = null
  selectedInitialDetail.value = null
  taskWorkspace.value = { status: '', running: false }
  onNavigate(taskType === '智能选题' ? 'topic-task-detail' : 'task-detail', normalizedTaskId)
}

function onViewAllTasks() {
  onNavigate('tasks')
}

onMounted(() => {
  try {
    const saved = JSON.parse(sessionStorage.getItem(navigationSessionKey) || '{}') as { page?: string; taskId?: string }
    if (saved.page && restorableRoutes.has(saved.page) && (saved.page === 'tasks' || saved.taskId)) {
      route.value = saved.page
      selectedTaskId.value = saved.taskId || ''
      if (saved.page.startsWith('topic-')) activeAgent.value = 'topic'
      else if (saved.page.startsWith('task-')) activeAgent.value = 'assessment'
    }
  } catch {
    sessionStorage.removeItem(navigationSessionKey)
  }
  trackPage(pageToTrack(route.value, selectedTaskId.value), {
    route: route.value,
    task_id: selectedTaskId.value || '',
    restored: false,
  })
})
</script>
