<template>
  <article class="panel question-card">
    <div class="question-card-header">
      <div>
        <div class="question-rank">推荐 {{ question.rank }}</div>
        <h3>{{ question.title }}</h3>
      </div>
    </div>
    <p class="question-text">{{ question.question }}</p>
    <p class="question-reason">{{ question.recommendation_reason }}</p>
    <div class="next-steps">
      <strong>下一步建议</strong>
      <ul>
        <li v-for="step in question.next_steps" :key="step">{{ step }}</li>
      </ul>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { FinalQuestion } from '../services/contentAssessmentUiApi'

defineProps<{ question: FinalQuestion }>()
</script>
