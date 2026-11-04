import { createApp, h, ref } from 'vue'
import TaskDetailPage from '../../frontend/web/src/pages/TaskDetailPage.vue'
import TopicTaskDetailPage from '../../frontend/web/src/pages/TopicTaskDetailPage.vue'
import ReportPdfPreview from '../../frontend/web/src/components/ReportPdfPreview.vue'
import { fetchTopicReportPdf } from '../../frontend/web/src/services/topicSelectionApi'
import { fetchContentAssessmentReportPdf } from '../../frontend/web/src/services/contentAssessmentUiApi'
import { getAuthManager } from '../../frontend/web/src/services/nstlAuth'
import '../../frontend/web/src/styles/workspace-modern.css'
const query = new URLSearchParams(location.search)
const mode = ref(query.has('topic') ? 'topic' : query.has('assessment-reader') ? 'assessment-reader' : 'assessment')
const taskId = ref('reading-qa')
const revision = ref(1)
createApp({ setup: () => () => new URLSearchParams(location.search).has('mobile-shell')
  ? h('iframe', { title: '390px 移动端验收', src: location.pathname + '?mobile=1' + (query.has('topic') ? '&topic=1' : query.has('assessment-reader') ? '&assessment-reader=1' : ''), style: 'width:390px;height:844px;border:1px solid #ddd;display:block;margin:16px auto' })
  : h('main', { style: 'padding:16px;font-family:sans-serif;max-width:1200px;margin:auto' }, [
  h('h1', '报告阅读与折叠隔离验收'),
  h('button', { onClick: () => getAuthManager().showLoginModal() }, '登录演示账号'),
  h('button', { onClick: () => { mode.value = 'assessment' } }, '评估任务详情'),
  h('button', { onClick: () => { mode.value = 'topic' } }, '选题报告预览'),
  h('button', { onClick: () => { mode.value = 'topic-detail' } }, '选题任务详情'),
  ...[['未上传', ''], ['已上传', '-upload'], ['解析失败', '-failed'], ['历史信息不完整', '-legacy']].map(([label, suffix]) => h('button', { onClick: () => { taskId.value = `reading-qa${suffix}` } }, label)),
  h('button', { onClick: () => { revision.value++ } }, '刷新页面数据'),
  h('button', { onClick: () => { taskId.value = taskId.value === 'reading-qa' ? 'reading-qa-2' : 'reading-qa' } }, '切换任务'),
  mode.value === 'assessment' ? h(TaskDetailPage, { taskId: taskId.value, workspaceCommand: { type: 'retry', key: revision.value } })
    : mode.value === 'topic-detail' ? h(TopicTaskDetailPage, { taskId: taskId.value })
      : h(ReportPdfPreview, { taskId: taskId.value, title: mode.value === 'assessment-reader' ? '评估参考文献链接验收' : '选题参考文献链接验收', loadPdf: mode.value === 'assessment-reader' ? fetchContentAssessmentReportPdf : fetchTopicReportPdf }),
]) }).mount('#app')
