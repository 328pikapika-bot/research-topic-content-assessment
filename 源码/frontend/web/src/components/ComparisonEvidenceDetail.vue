<template>
  <ResultDisclosure v-if="hasUsefulDetail" class="comparison-evidence-detail" label="查看原文与指标" collapse-label="收起原文与指标">
    <div class="comparison-evidence-detail__body">
      <div v-if="detail.hasRecord" class="comparison-evidence-detail__source">
        <a v-if="url" :href="url" target="_blank" rel="noopener noreferrer">{{ detail.title }}</a>
        <strong v-else>{{ detail.title }}</strong>
        <small v-if="detail.authors || detail.year">{{ [detail.authors, detail.year].filter(Boolean).join(' · ') }}</small>
        <span>{{ detail.sourceKind }}</span>
      </div>
      <p v-if="detail.location">原文位置：{{ detail.location }}</p>
      <blockquote v-if="detail.excerpt">{{ detail.excerpt }}</blockquote>
      <div v-if="detail.fields.length" class="comparison-evidence-detail__fields">
        <h5>方法与指标对照</h5>
        <ul><li v-for="field in detail.fields" :key="field.key"><div><span>{{ field.label }}</span></div><small v-if="field.claimValue && field.evidenceValue">本研究：{{ field.claimValue }}<br>文献：{{ field.evidenceValue }}</small></li></ul>
      </div>
      <p v-if="showAnalysis && analysis.difference">研究差异：{{ analysis.difference }}</p>
      <p v-if="showAnalysis && analysis.conditions">比较条件：{{ analysis.conditions }}</p>
    </div>
  </ResultDisclosure>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ResultDisclosure from './ResultDisclosure.vue'
import type { Claim, ClaimEvidenceLink, EvidenceRecord } from '../services/contentAssessmentApi'
import { comparisonEvidenceDetail, comparisonResearchAnalysis, type ReaderComparison } from '../services/comparisonEvidencePresentation'
import { resolveEvidenceUrl } from '../services/evidenceLinks'

const props = withDefaults(defineProps<{
  link: ClaimEvidenceLink
  records: EvidenceRecord[]
  chunks: Record<string, unknown>[]
  claim?: Claim
  showAnalysis?: boolean
  readerComparison?: ReaderComparison
}>(), { showAnalysis: true })

const detail = computed(() => comparisonEvidenceDetail(props.link, props.records, props.chunks, props.claim))
const analysis = computed(() => comparisonResearchAnalysis(props.link, props.readerComparison))
const hasUsefulDetail = computed(() => Boolean(detail.value.excerpt || detail.value.fields.length || detail.value.location
  || (props.showAnalysis && (analysis.value.difference || analysis.value.conditions))))
const url = computed(() => {
  const record = props.records.find(item => item.evidence_id === props.link.evidence_id)
  return record ? resolveEvidenceUrl(record) : ''
})
</script>

<style scoped>
.comparison-evidence-detail { margin-top: 12px; min-width: 0; color: #46566b; font-size: 15px; }
.comparison-evidence-detail__body { display: grid; gap: 10px; margin-top: 12px; padding: 14px; border-left: 3px solid #68a9bd; background: #f7fafc; min-width: 0; }
.comparison-evidence-detail__source { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; min-width: 0; }
.comparison-evidence-detail__source a, .comparison-evidence-detail__source strong { color: #245d92; overflow-wrap: anywhere; }
.comparison-evidence-detail__source small { color: #66768c; }
.comparison-evidence-detail__source span { color: #147c72; font-weight: 600; }
.comparison-evidence-detail p { margin: 0; line-height: 1.7; overflow-wrap: anywhere; }
.comparison-evidence-detail blockquote { margin: 0; padding: 8px 12px; border-left: 2px solid #a9c6d0; background: #fff; white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; }
.comparison-evidence-detail__fields h5 { margin: 0 0 8px; font-size: 14px; }
.comparison-evidence-detail__fields ul { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: 6px; margin: 0; padding: 0; list-style: none; }
.comparison-evidence-detail__fields li { display: grid; gap: 5px; padding: 8px; background: #fff; border: 1px solid #e2e9ee; min-width: 0; }
.comparison-evidence-detail__fields li div { display: flex; justify-content: space-between; gap: 8px; }
.comparison-evidence-detail__fields b { color: #344e6b; font-weight: 600; }
.comparison-evidence-detail__fields small { color: #66768c; line-height: 1.5; overflow-wrap: anywhere; }
</style>
