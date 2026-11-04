<template>
  <section v-if="notice" class="stage-run-notice" aria-label="当前执行进度">
    <span class="stage-run-notice__pulse" aria-hidden="true"></span>
    <div><strong>{{ notice.title }}</strong><p>{{ notice.description }}</p><small>{{ notice.syncing ? '正在同步进度 · ' : '' }}{{ notice.reference }}</small></div>
  </section>
</template>
<script setup lang="ts">
import type { stageRunPresentation } from '../services/userSteps'
defineProps<{ notice: ReturnType<typeof stageRunPresentation> }>()
</script>
<style scoped>
.stage-run-notice{display:flex;align-items:flex-start;gap:12px;margin:16px 0;padding:16px 20px;background:#f2f6fc;border:1px solid #e0e8f4;border-radius:10px;color:#304463;min-width:0}.stage-run-notice>div{min-width:0}.stage-run-notice strong{font-size:15px;font-weight:600;line-height:1.7;font-variant-numeric:tabular-nums}.stage-run-notice p{margin:4px 0;font-size:14px;line-height:1.7}.stage-run-notice small{font-size:13px;line-height:1.7;color:#67758b}.stage-run-notice__pulse{flex:none;width:8px;height:8px;margin-top:9px;border-radius:50%;background:#5576bd;animation:stage-pulse 1.5s ease-in-out infinite}@keyframes stage-pulse{50%{opacity:.35}}@media(prefers-reduced-motion:reduce){.stage-run-notice__pulse{animation:none}}@media(max-width:480px){.stage-run-notice{padding:12px;gap:9px}}
</style>
