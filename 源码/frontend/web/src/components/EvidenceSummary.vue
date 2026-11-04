<template>
  <section v-if="items.length" class="evidence-summary" aria-label="对应结论的证据依据">
    <article v-for="item in items.slice(0, 3)" :key="item.key">
      <p><b>{{ item.relation }} · {{ item.scope }}</b></p>
      <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a>
      <strong v-else>{{ item.title }}</strong>
      <small>{{ item.source }}<template v-if="item.byline"> · {{ item.byline }}</template></small>
      <details v-if="item.excerpt"><summary>查看原文依据与位置</summary><p>{{ item.locator }}</p><blockquote>{{ item.excerpt }}</blockquote><p>{{ item.limitation }}</p></details>
    </article>
    <details v-if="items.length > 3" class="evidence-summary__more"><summary>查看更多相关来源（{{ Math.min(items.length, 5) - 3 }} 篇）</summary>
      <article v-for="item in items.slice(3, 5)" :key="item.key"><p><b>{{ item.relation }} · {{ item.scope }}</b></p><a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a><strong v-else>{{ item.title }}</strong><small>{{ item.source }} · {{ item.byline }}</small><p>{{ item.locator }}</p><blockquote>{{ item.excerpt }}</blockquote><p>{{ item.limitation }}</p></article>
    </details>
  </section>
</template>

<script setup lang="ts">
import type { EvidenceSummaryItem } from '../services/evidenceSummary'
defineProps<{ items: EvidenceSummaryItem[] }>()
</script>

<style scoped>
.evidence-summary { display: grid; gap: 10px; margin-top: 12px; min-width: 0; }
.evidence-summary article { padding: 12px; border: 1px solid #dce5ee; border-radius: 8px; background: #f8fafc; min-width: 0; overflow-wrap: anywhere; }
.evidence-summary p { margin: 6px 0; font-size: 13px; line-height: 1.6; }
.evidence-summary small { display: block; margin-top: 6px; color: #64748b; }
.evidence-summary a, .evidence-summary strong { font-size: 14px; line-height: 1.6; }
.evidence-summary summary { cursor: pointer; color: #245d92; margin-top: 8px; font-size: 13px; }
.evidence-summary blockquote { margin: 8px 0; border-left: 3px solid #9bbbd8; padding: 8px 12px; white-space: pre-wrap; font-size: 13px; line-height: 1.7; }
.evidence-summary__more article { margin-top: 10px; }
</style>
