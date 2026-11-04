import { createApp, h, ref } from 'vue'
import TopicTaskDetailPage from '../../frontend/web/src/pages/TopicTaskDetailPage.vue'
import TaskHistoryPage from '../../frontend/web/src/pages/TaskHistoryPage.vue'
import ConfirmDialog from '../../frontend/web/src/components/ConfirmDialog.vue'
import ToastMessage from '../../frontend/web/src/components/ToastMessage.vue'
import { overflowTitle } from '../../frontend/web/src/directives/overflowTitle'
import '../../frontend/web/src/styles/fonts.css'
import '../../frontend/web/src/styles/main.css'
import '../../frontend/web/src/styles/workspace-modern.css'
const mode = ref('topic')
createApp({ setup: () => () => h('main', [
  h('nav', { style: 'padding:12px;display:flex;gap:12px' }, [h('button', { onClick: () => { mode.value = 'topic' } }, '选题隔离验收'), h('button', { onClick: () => { mode.value = 'history' } }, '历史隔离验收')]),
  mode.value === 'topic' ? h(TopicTaskDetailPage, { taskId: 'reader-qa' }) : h(TaskHistoryPage),
  h(ConfirmDialog), h(ToastMessage),
]) }).directive('overflow-title', overflowTitle).mount('#app')
