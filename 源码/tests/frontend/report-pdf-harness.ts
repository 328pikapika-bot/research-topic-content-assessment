// Test-only entry: the actual production component and authenticated fetch.
import { createApp, h, ref } from 'vue'
import ReportPdfPreview from '../../frontend/web/src/components/ReportPdfPreview.vue'
import { getAuthManager } from '../../frontend/web/src/services/nstlAuth'
const taskId = ref('reference-qa')
const visible = ref(true)
const pages = ref(0)
createApp({ setup: () => () => h('main', { style: 'padding:16px;font-family:sans-serif' }, [
  h('h1', '报告阅读器隔离验收'),
  h('p', '仅使用模拟报告，未连接生产任务。'),
  h('button', { onClick: () => getAuthManager().showLoginModal() }, '登录演示账号'),
  h('button', { onClick: () => { taskId.value = taskId.value === 'reference-qa' ? 'reference-error' : 'reference-qa' } }, '切换正常／错误报告'),
  h('button', { onClick: () => { visible.value = !visible.value } }, '显示／释放阅读器'),
  h('p', { id: 'verified-pages' }, `已渲染 ${pages.value} 页`),
  visible.value ? h(ReportPdfPreview, { taskId: taskId.value, title: '微流控平台方法学验证模拟评估内容评估报告', onLoaded: (count: number) => { pages.value = count }, onDownload: () => {} }) : null,
]) }).mount('#app')
