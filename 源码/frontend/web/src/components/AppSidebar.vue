<template>
  <button
    class="app-sidebar__mobile-trigger"
    type="button"
    aria-label="打开平台导航"
    aria-controls="platform-sidebar"
    :aria-expanded="mobileOpen"
    @click="mobileOpen = true"
  >
    <span class="material-icons-outlined" aria-hidden="true">menu</span>
  </button>
  <div
    class="app-sidebar__mobile-backdrop"
    :class="{ 'is-visible': mobileOpen }"
    aria-hidden="true"
    @click="mobileOpen = false"
  ></div>
  <aside
    id="platform-sidebar"
    class="app-sidebar app-sidebar--modern"
    :class="{ 'is-collapsed': collapsed, 'is-mobile-open': mobileOpen }"
    :aria-hidden="viewportMode === 'mobile' && !mobileOpen ? 'true' : undefined"
    :inert="viewportMode === 'mobile' && !mobileOpen"
    aria-label="平台主导航"
  >
    <div class="app-sidebar__brand">
      <span class="app-sidebar__logo-wrap">
        <img class="app-sidebar__logo" :src="logoUrl" alt="科技文献智能服务平台" width="227" height="30" />
      </span>
      <button
        class="app-sidebar__collapse"
        type="button"
        :title="collapseLabel"
        :aria-label="collapseLabel"
        :aria-expanded="!collapsed"
        @click="toggleSidebar"
      >
        <img :src="iconExpandUrl" alt="" aria-hidden="true" width="16" height="16" />
      </button>
    </div>

    <button class="app-sidebar__new-chat" type="button" @click="emitAndClose('new-chat')">
      <span class="material-icons-outlined" aria-hidden="true">add</span>
      <span>新建对话</span>
    </button>

    <div class="app-sidebar__rail-divider" aria-hidden="true"></div>

    <p class="app-sidebar__nav-title">智能体</p>
    <nav class="app-sidebar__nav">
      <button class="app-sidebar__nav-btn" :class="{ active: activeAgent === 'topic' }" type="button" :title="collapsed ? '智能选题智能体' : undefined" @click="selectAgent('topic')">
        <span class="app-sidebar__ico">
          <img :src="iconIntelligentUrl" alt="" aria-hidden="true" width="21" height="21" />
        </span>
        <span>智能选题智能体</span>
        <small class="is-ready">可用</small>
      </button>
      <button class="app-sidebar__nav-btn" :class="{ active: activeAgent === 'assessment' }" type="button" :title="collapsed ? '创新性评估智能体' : undefined" @click="selectAgent('assessment')">
        <span class="app-sidebar__ico">
          <img :src="iconInnovationUrl" alt="" aria-hidden="true" width="18" height="18" />
        </span>
        <span>创新性评估智能体</span>
        <small class="is-ready">可用</small>
      </button>
    </nav>

    <section class="app-sidebar__history-panel" aria-label="历史任务">
      <button type="button" class="app-sidebar__history-toggle" :aria-expanded="historyOpen" @click="toggleHistory">
        <span class="app-sidebar__history-toggle-label"><span class="material-icons-outlined" aria-hidden="true">history</span>历史任务</span>
        <span class="material-icons-outlined app-sidebar__chevron" aria-hidden="true">{{ historyOpen ? 'expand_less' : 'expand_more' }}</span>
      </button>
      <div v-if="historyOpen" class="app-sidebar__history">
        <div v-if="historyLoading" class="app-sidebar__empty">正在加载任务...</div>
        <template v-else-if="historyItems.length">
          <button v-for="task in historyItems" :key="task.task_id || task.id" type="button" class="app-sidebar__history-item" @click="openTask(task)">
            <span class="app-sidebar__history-title">{{ task.title || '未命名任务' }}</span>
            <span class="app-sidebar__history-meta">
              <span class="app-sidebar__agent-tag" :class="task.task_type === '智能选题' ? 'app-sidebar__agent-tag--topic' : 'app-sidebar__agent-tag--assessment'">{{ task.task_type || '创新性评估' }}</span>{{ recoveryLabel(task.recovery) || statusLabel(task.status) }} · {{ formatDate(task.created_at) }}
            </span>
          </button>
        </template>
        <div v-else class="app-sidebar__empty">暂无历史任务</div>
        <button type="button" class="app-sidebar__all" @click="openAllTasks">查看全部历史任务 <span class="material-icons-outlined" aria-hidden="true">arrow_forward</span></button>
      </div>
    </section>

    <div class="app-sidebar__spacer"></div>
    <div class="app-sidebar__footer">
      <template v-if="authenticated">
        <span class="app-sidebar__user">
          <span class="app-sidebar__user-dot"></span>
          <span class="app-sidebar__avatar" aria-hidden="true">{{ (userName || 'admin').slice(0, 1).toUpperCase() }}</span>
          <span class="app-sidebar__user-name">{{ userName || 'admin' }}</span>
        </span>
        <button class="app-sidebar__logout" type="button" @click="logout">退出登录</button>
      </template>
      <button v-else class="app-sidebar__login" type="button" @click="showLogin">登录平台</button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { recoveryLabel } from '../services/generationRecovery'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listContentAssessmentTasks, type ContentAssessmentTaskHistoryItem } from '../services/contentAssessmentUiApi'
import { listTopicSelectionTasks } from '../services/topicSelectionApi'
import { getAuthManager, getCurrentUser } from '../services/nstlAuth'
import logoUrl from '../../assets/LOGO.png'
import iconIntelligentUrl from '../../assets/icon-intelligent.png'
import iconInnovationUrl from '../../assets/icon-innovation.png'
import iconExpandUrl from '../../assets/icon-expand.png'

type AgentId = 'assessment' | 'topic'

const props = withDefaults(defineProps<{ activeAgent: AgentId | null; variant?: 'default' | 'home' }>(), { variant: 'default' })
const emit = defineEmits<{
  'select-agent': [agent: AgentId]
  'new-chat': []
  'open-task': [taskId: string, taskType?: string]
  'open-tasks': []
}>()

const collapsed = ref(false)
const mobileOpen = ref(false)
const viewportMode = ref<'desktop' | 'tablet' | 'mobile'>('desktop')
const collapseLabel = computed(() => viewportMode.value === 'mobile' ? '关闭平台导航' : collapsed.value ? '展开侧边栏' : '收起侧边栏')

const userName = ref('')
const authenticated = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyItems = ref<any[]>([])
let pendingHistoryOpen = false

function getViewportMode(): 'desktop' | 'tablet' | 'mobile' {
  if (window.innerWidth < 768) return 'mobile'
  if (window.innerWidth < 1200) return 'tablet'
  return 'desktop'
}

function syncResponsiveState() {
  const nextMode = getViewportMode()
  if (nextMode === viewportMode.value) return
  viewportMode.value = nextMode
  mobileOpen.value = false
  collapsed.value = nextMode === 'tablet'
}

function toggleSidebar() {
  if (viewportMode.value === 'mobile') {
    mobileOpen.value = false
    return
  }
  collapsed.value = !collapsed.value
}

function closeMobileSidebar() {
  if (viewportMode.value === 'mobile') mobileOpen.value = false
}

function emitAndClose(event: 'new-chat') {
  emit(event)
  closeMobileSidebar()
}

function selectAgent(agent: AgentId) {
  emit('select-agent', agent)
  closeMobileSidebar()
}

function syncUser() {
  const user = getCurrentUser()
  userName.value = user?.username || ''
  authenticated.value = getAuthManager().isAuthenticated()
}

function statusLabel(status: string) {
  return ({ completed: '已完成', running: '进行中', queued: '排队中', failed: '失败', time_out: '超时', cancelled: '已停止', awaiting_confirmation: '待确认', awaiting_clarification: '待确认', awaiting_quality_continue: '待继续生成', awaiting_review: '待复核', awaiting_correction: '待补正', model_unavailable: '模型不可用' } as Record<string, string>)[status] || '处理中'
}

function formatDate(value?: string) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : ''
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const pageSize = props.variant === 'home' ? 20 : 6
    const [assessment, topic] = await Promise.all([listContentAssessmentTasks({ page: 1, pageSize }), listTopicSelectionTasks({ page: 1, pageSize })])
    historyItems.value = [...(assessment.items || []), ...(topic.items || [])].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0, pageSize)
  } catch {
    historyItems.value = []
  } finally {
    historyLoading.value = false
  }
}

function toggleHistory() {
  if (!getAuthManager().isAuthenticated()) {
    pendingHistoryOpen = true
    getAuthManager().showLoginModal()
    return
  }
  historyOpen.value = !historyOpen.value
  if (historyOpen.value) void loadHistory()
}

function openTask(task: ContentAssessmentTaskHistoryItem) {
  const taskId = String(task?.task_id || task?.id || '').trim()
  if (!taskId) return
  emit('open-task', taskId, task.task_type)
  closeMobileSidebar()
}

function openAllTasks() {
  emit('open-tasks')
  closeMobileSidebar()
}

function showLogin() {
  getAuthManager().showLoginModal()
  closeMobileSidebar()
}

function logout() {
  getAuthManager().logout()
  closeMobileSidebar()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && mobileOpen.value) mobileOpen.value = false
}

watch(mobileOpen, (open) => {
  document.body.classList.toggle('app-sidebar-drawer-open', open && viewportMode.value === 'mobile')
})

function handleAuthSuccess() {
  syncUser()
  pendingHistoryOpen = false
  historyOpen.value = true
  void loadHistory()
}

function handleAuthLogout() {
  syncUser()
  historyOpen.value = false
  historyItems.value = []
}

onMounted(() => {
  viewportMode.value = getViewportMode()
  collapsed.value = viewportMode.value === 'tablet'
  syncUser()
  if (authenticated.value) {
    historyOpen.value = true
    void loadHistory()
  }
  window.addEventListener('authSuccess', handleAuthSuccess)
  window.addEventListener('authLogout', handleAuthLogout)
  window.addEventListener('resize', syncResponsiveState)
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.body.classList.remove('app-sidebar-drawer-open')
  window.removeEventListener('authSuccess', handleAuthSuccess)
  window.removeEventListener('authLogout', handleAuthLogout)
  window.removeEventListener('resize', syncResponsiveState)
  document.removeEventListener('keydown', handleKeydown)
})
</script>
