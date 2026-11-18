<template>
  <span class="inline-citations"><template v-for="(part, index) in parts" :key="index"><span v-if="part.number" class="citation"><a v-if="part.url" :href="part.url" :title="part.title" :aria-label="`文献${part.number}：${part.title}`" target="_blank" rel="noopener noreferrer">{{ part.text }}</a><template v-else>{{ part.text }}</template></span><template v-else>{{ part.text }}</template></template></span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { resolveEvidenceUrl } from '../services/evidenceLinks'
import { inlineLiteratureText } from '../services/literaturePresentation'
import { citationTextParts } from '../services/topicPagePresentation'
const props = defineProps<{ text?: unknown; sources: Record<number, Record<string, any>> }>()
const parts = computed(() => citationTextParts(inlineLiteratureText(props.text)).map(part => {
  const source = part.number ? props.sources[part.number] : undefined
  return { ...part, url: source ? resolveEvidenceUrl(source) : '', title: String(source?.title || '') }
}))
</script>
<style scoped>
.inline-citations{white-space:pre-wrap;overflow-wrap:anywhere}.inline-citations .citation{font-size:1em;line-height:inherit;vertical-align:baseline;white-space:nowrap}.inline-citations a{font-size:inherit;line-height:inherit;color:#2465ae;text-decoration:none;font-weight:500}.inline-citations a:hover{text-decoration:underline}.inline-citations a:focus-visible{outline:2px solid #2465ae;outline-offset:2px}
</style>
