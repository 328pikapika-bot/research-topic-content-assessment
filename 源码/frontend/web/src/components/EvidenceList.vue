<template>
  <section class="panel">
    <div class="panel-header">
      <h2>证据与来源</h2>
      <span class="hint">检索结果</span>
    </div>
    <div v-if="items.length" class="evidence-list">
      <article v-for="item in items" :key="item.evidence_id" class="evidence-item">
        <h4>{{ item.title }}</h4>
        <div class="evidence-meta">{{ item.venue }} · {{ item.year }}</div>
        <p>{{ item.support_summary }}</p>
      </article>
    </div>
    <p v-else class="empty-text">当前没有可展示的证据。</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { EvidenceItem } from '../services/contentAssessmentUiApi'

const props = defineProps<{ retrievalResults: Record<string, EvidenceItem[]> }>()

const items = computed(() => Object.values(props.retrievalResults).flat())
</script>
