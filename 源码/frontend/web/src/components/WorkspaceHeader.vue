<template>
  <header class="workspace-header">
    <div class="workspace-header__location">
      <span class="workspace-header__platform">科技文献智能服务平台</span>
      <span class="workspace-header__separator">/</span>
      <span class="workspace-header__page">{{ context }}</span>
    </div>

    <div class="workspace-header__actions">
      <span v-if="taskStatus" class="workspace-status" :class="`workspace-status--${taskStatus}`">{{ statusLabel(taskStatus) }}</span>
      <button v-if="taskStatus === 'awaiting_clarification'" type="button" class="workspace-header__button workspace-header__button--primary" @click="emit('command', 'clarify')"><span class="material-icons-outlined" aria-hidden="true">fact_check</span>确认边界</button>
      <button v-else-if="taskStatus === 'awaiting_quality_continue'" type="button" class="workspace-header__button workspace-header__button--primary" @click="emit('command', 'continue')"><span class="material-icons-outlined" aria-hidden="true">play_arrow</span>继续生成</button>
      <button v-else-if="taskStatus === 'failed' || taskStatus === 'time_out'" type="button" class="workspace-header__button workspace-header__button--primary" @click="emit('command', 'retry')"><span class="material-icons-outlined" aria-hidden="true">refresh</span>重试任务</button>
      <button v-else-if="taskStatus === 'completed'" type="button" class="workspace-header__button workspace-header__button--primary" @click="emit('command', 'download')"><span class="material-icons-outlined" aria-hidden="true">download</span>下载报告</button>
      <button v-else-if="isRunning" type="button" class="workspace-header__button workspace-header__button--danger" @click="emit('command', 'stop')"><span class="material-icons-outlined" aria-hidden="true">stop_circle</span>停止任务</button>
      <button v-if="showBack" type="button" class="workspace-header__button" @click="emit('back')">
        <span class="material-icons-outlined" aria-hidden="true">arrow_back</span>
        {{ backLabel }}
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  context: string
  showBack?: boolean
  backLabel?: string
  taskStatus?: string
  isRunning?: boolean
}>(), { showBack: false, backLabel: '返回上一层', taskStatus: '', isRunning: false })

const emit = defineEmits<{
  back: []
  command: [command: 'clarify' | 'continue' | 'retry' | 'stop' | 'download']
}>()

function statusLabel(status: string) {
  return ({ completed: '已完成', running: '进行中', queued: '排队中', failed: '失败', time_out: '超时', cancelled: '已停止', awaiting_confirmation: '待确认', awaiting_clarification: '待确认', awaiting_quality_continue: '待继续生成', awaiting_review: '待复核', awaiting_correction: '待补正', model_unavailable: '模型不可用' } as Record<string, string>)[status] || '处理中'
}
</script>
