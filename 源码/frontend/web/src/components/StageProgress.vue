<template>
  <section class="panel">
    <div class="panel-header">
      <h2>阶段进度</h2>
      <span class="hint">共 {{ stages.length }} 个阶段</span>
    </div>
    <ol class="stage-list">
      <li v-for="stage in stages" :key="stage.stage_key" :class="['stage-item', `stage-${stage.status}`]">
        <div class="stage-name">{{ stage.stage_name }}</div>
        <div class="stage-status">{{ statusLabel(stage.status) }}</div>
        <p class="stage-summary">{{ stage.summary || '等待执行' }}</p>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import type { StageTraceItem } from '../services/contentAssessmentUiApi'

defineProps<{ stages: StageTraceItem[] }>()

function statusLabel(status: string) {
  if (status === 'completed') return '已完成'
  if (status === 'running') return '进行中'
  if (status === 'failed') return '失败'
  if (status === 'skipped') return '已跳过'
  return '未开始'
}
</script>
