<template>
  <header class="platform-header" :class="{ 'platform-header--task': variant === 'task' }" ref="headerRef">
    <div class="platform-header__inner">
      <div class="platform-header__brand-group">
        <button v-if="variant === 'task'" class="platform-header__back" type="button" aria-label="返回首页" @click="emit('goHome')">
          <span class="material-icons-outlined" aria-hidden="true">arrow_back</span>
        </button>
        <div class="platform-mark" aria-hidden="true"><span class="material-icons-outlined">menu_book</span></div>
        <div class="platform-header__titles">
          <span class="platform-header__title">科技文献智能服务平台</span>
          <span v-if="variant === 'task'" class="platform-header__context">创新性评估智能体</span>
        </div>
        <div class="platform-history-wrap">
          <button class="platform-history-link" type="button" aria-label="历史任务" title="历史任务" :aria-expanded="historyOpen" @click="handleHistoryToggle">
            <span class="material-icons-outlined" aria-hidden="true">history</span><span>历史任务</span>
            <span class="material-icons-outlined platform-history-chevron" aria-hidden="true">{{ historyOpen ? 'expand_less' : 'expand_more' }}</span>
          </button>
          <div v-if="historyOpen" class="platform-history-menu" role="menu">
            <div class="platform-history-menu__head"><strong>历史任务</strong><button type="button" aria-label="关闭" @click="historyOpen = false">×</button></div>
            <div v-if="historyLoading" class="platform-history-menu__empty">正在加载任务...</div>
            <template v-else-if="historyItems.length">
              <button v-for="task in historyItems" :key="task.task_id || task.id" type="button" class="platform-history-item" role="menuitem" @click="selectHistoryTask(task)">
                <span class="platform-history-item__title">{{ task.title || task.topic || '未命名任务' }}</span>
                <span class="platform-history-item__meta"><span>{{ statusLabel(task.status) }}</span><span>{{ formatDate(task.created_at) }}</span></span>
              </button>
              <button type="button" class="platform-history-menu__all" @click="openAllHistory">查看全部历史任务<span class="material-icons-outlined">arrow_forward</span></button>
            </template>
            <div v-else class="platform-history-menu__empty"><span class="material-icons-outlined">history</span><span>暂无历史任务</span></div>
          </div>
        </div>
      </div>
      <div class="platform-header__actions">
        <template v-if="canUseAuthenticatedActions">
          <span class="platform-user-chip"><span class="platform-user-dot"></span>{{ userName || 'admin' }}</span>
          <button class="platform-logout" type="button" @click="handleLogout">退出登录</button>
        </template>
        <button v-else class="platform-login-button" type="button" @click="handleLogin">登录</button>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { listContentAssessmentTasks } from '../services/contentAssessmentUiApi'
import { getAuthManager, getCurrentUser, type PlatformUser } from '../services/nstlAuth'
import { trackEvent, trackOutcome } from '../tracker/contentAssessmentTracker'

const props = withDefaults(defineProps<{ isAuthenticated?: boolean; variant?: 'home' | 'task' }>(), { isAuthenticated: false, variant: 'home' })
const emit = defineEmits<{ viewTaskHistory: []; viewTask: [taskId: string]; goHome: [] }>()
const userName = ref('')
const localAuthenticated = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyItems = ref<any[]>([])
const headerRef = ref<HTMLElement | null>(null)
let pendingHistoryAction: (() => void) | null = null
const canUseAuthenticatedActions = computed(() => props.isAuthenticated || localAuthenticated.value)

function updateCurrentUser(user: PlatformUser | null = getCurrentUser()) { userName.value = user?.username || user?.userId || ''; localAuthenticated.value = getAuthManager().isAuthenticated() }
function handleAuthSuccess(event: Event) {
  updateCurrentUser((event as CustomEvent<{ userInfo?: PlatformUser | null }>).detail?.userInfo || getCurrentUser())
  const action = pendingHistoryAction
  pendingHistoryAction = null
  if (action) window.setTimeout(action, 0)
}
function handleAuthLogout() { userName.value = ''; localAuthenticated.value = false; historyOpen.value = false; historyItems.value = [] }
function handleLogin() { getAuthManager().showLoginModal() }
function handleLogout() { getAuthManager().logout() }
async function handleHistoryToggle() {
  if (!canUseAuthenticatedActions.value) {
    trackOutcome('platform_header_task_history', '历史任务', 'blocked', { reason: 'unauthenticated' })
    pendingHistoryAction = () => { void openHistoryMenu() }
    getAuthManager().showLoginModal()
    return
  }
  if (historyOpen.value) { historyOpen.value = false; return }
  await openHistoryMenu()
}
async function openHistoryMenu() {
  historyOpen.value = true
  trackEvent('platform_header_task_history', '历史任务')
  historyLoading.value = true
  try {
    const result = await listContentAssessmentTasks({ page: 1, pageSize: 6 })
    historyItems.value = result.items || []
  } catch {
    historyItems.value = []
  } finally { historyLoading.value = false }
}
function selectHistoryTask(task: any) {
  const taskId = task?.task_id || task?.id
  if (!taskId) return
  historyOpen.value = false
  emit('viewTask', taskId)
}
function openAllHistory() { historyOpen.value = false; emit('viewTaskHistory') }
function formatDate(value: string) { return value ? new Date(value).toLocaleDateString('zh-CN') : '' }
function statusLabel(value: string) { return ({ completed: '已完成', running: '进行中', queued: '排队中', failed: '失败', time_out: '超时', cancelled: '已取消', awaiting_clarification: '待澄清', awaiting_quality_continue: '待继续生成' } as Record<string, string>)[value] || '未知' }
function handleDocumentClick(event: MouseEvent) { if (historyOpen.value && !headerRef.value?.contains(event.target as Node)) historyOpen.value = false }
function handleKeydown(event: KeyboardEvent) { if (event.key === 'Escape') historyOpen.value = false }
onMounted(() => { updateCurrentUser(); document.addEventListener('click', handleDocumentClick); document.addEventListener('keydown', handleKeydown); window.addEventListener('authSuccess', handleAuthSuccess); window.addEventListener('authLogout', handleAuthLogout) })
onBeforeUnmount(() => { document.removeEventListener('click', handleDocumentClick); document.removeEventListener('keydown', handleKeydown); window.removeEventListener('authSuccess', handleAuthSuccess); window.removeEventListener('authLogout', handleAuthLogout) })
</script>
