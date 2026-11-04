<template>
  <HomeLanding
    :active-agent="activeAgent"
    @select-agent="emit('select-agent', $event)"
    @navigate="forwardNavigate"
  />
</template>

<script setup lang="ts">
import HomeLanding from './HomeLanding.vue'
import type { ContentAssessmentRunResponse, ContentAssessmentTaskDetail } from '../services/contentAssessmentUiApi'
import type { AgentId } from '../agents'

defineProps<{ activeAgent: AgentId }>()
const emit = defineEmits<{
  'select-agent': [agent: AgentId]
  navigate: [page: string, taskId?: string, payload?: ContentAssessmentRunResponse | ContentAssessmentTaskDetail]
}>()

function forwardNavigate(page: string, taskId?: string, payload?: ContentAssessmentRunResponse | ContentAssessmentTaskDetail) {
  emit('navigate', page, taskId, payload)
}
</script>
