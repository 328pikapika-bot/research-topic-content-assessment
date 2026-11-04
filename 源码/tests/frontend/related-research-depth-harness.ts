import { createApp, h } from 'vue'
import TaskDetailPage from '../../frontend/web/src/pages/TaskDetailPage.vue'
import '../../frontend/web/src/styles/workspace-modern.css'

createApp({
  setup: () => () => h('main', { style: 'max-width:1200px;margin:auto;padding:16px;font-family:"Noto Sans SC","Microsoft YaHei",sans-serif' }, [
    h('p', { style: 'font-size:13px;color:#63758a;margin:0 0 16px' }, '单要点本地预览 · 使用已有材料与保存的模型响应，未修改历史任务或报告'),
    h(TaskDetailPage, { taskId: 'related-research-preview' }),
  ]),
}).mount('#app')
