<template>
  <ul v-if="items.length" class="literature-references" aria-label="相关文献">
    <li v-for="(item, index) in items" :key="item.evidence_id || index">
      <div class="reference-line">
      <span v-if="item.year" class="reference-year">{{ item.year }}</span>
      <a v-if="resolveEvidenceUrl(item)" :href="resolveEvidenceUrl(item)" target="_blank" rel="noopener noreferrer">{{ bibliographyText(item.title) }}</a>
      <span v-else>{{ bibliographyText(item.title) }}</span>
      <span v-if="item.authors?.length" class="reference-authors">{{ item.authors.slice(0, 2).join('、') }}{{ item.authors.length > 2 ? ' 等' : '' }}</span>
      <button v-if="showAbstract && bibliographyText(item.abstract)" type="button" class="reference-abstract-toggle" :aria-expanded="Boolean(expanded[item.evidence_id || index])" :aria-controls="`${instanceId}-abstract-${index}`" @click="expanded[item.evidence_id || index] = !expanded[item.evidence_id || index]">{{ expanded[item.evidence_id || index] ? '收起摘要' : '摘要' }}<span aria-hidden="true">{{ expanded[item.evidence_id || index] ? '⌃' : '⌄' }}</span></button>
      </div>
      <p v-if="showAbstract && expanded[item.evidence_id || index]" :id="`${instanceId}-abstract-${index}`" class="reference-abstract">{{ bibliographyText(item.abstract) }}</p>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { reactive, useId } from 'vue'
import { resolveEvidenceUrl } from '../services/evidenceLinks'
import { bibliographyText, type LiteratureEvidence } from '../services/literaturePresentation'
defineProps<{ items: LiteratureEvidence[]; showAbstract?: boolean }>()
const instanceId = useId()
const expanded = reactive<Record<string, boolean>>({})
</script>
<style scoped>
.literature-references{margin:10px 0 4px;padding:0;list-style:none;text-align:left}
.literature-references li{min-width:0;padding:7px 0;font-size:14px;line-height:1.65;color:#34465d}
.reference-line{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;min-width:0}
.reference-line>a{flex:0 1 auto}
.reference-abstract-toggle{display:inline-flex;align-items:center;gap:4px;flex:none;border:0;padding:0;background:none;color:#2465ae;font:inherit;font-size:13px;cursor:pointer}
.reference-abstract-toggle:hover{text-decoration:underline}.reference-abstract-toggle:focus-visible{outline:2px solid #2465ae;outline-offset:3px}
.reference-abstract{margin:8px 0 4px;color:#475569;font-size:15px;line-height:1.85;overflow-wrap:anywhere}
.reference-year{flex:none;color:#718096;font-variant-numeric:tabular-nums}
.literature-references a{min-width:0;color:#2465ae;text-decoration:none;overflow-wrap:anywhere}
.literature-references a:hover{text-decoration:underline}.literature-references a:focus-visible{outline:2px solid #2465ae;outline-offset:3px}
.reference-authors{flex:none;max-width:30%;color:#788494;font-size:13px;overflow-wrap:anywhere}
@media(max-width:600px){.reference-line{display:block}.reference-year{margin-right:8px}.reference-authors{margin-left:8px;max-width:none;font-size:13px}.reference-abstract-toggle{margin-left:8px}}
</style>
