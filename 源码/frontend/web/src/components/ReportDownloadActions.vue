<template>
  <div class="report-download-actions" role="group" aria-label="下载正式报告">
    <button
      v-for="format in formats" :key="format.key" type="button"
      :class="['report-download-button', `report-download-button--${format.key}`]"
      :disabled="disabled || isLoading(format.key)" :aria-busy="isLoading(format.key)"
      @click="$emit('download', format.key)"
    >
      <svg v-if="isLoading(format.key)" class="report-download-spinner" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" opacity=".25" />
        <path d="M12 3a9 9 0 0 1 9 9" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
      <svg v-else viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
        <path d="M14 3v6h6M12 11v6m-3-3 3 3 3-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span>{{ isLoading(format.key) ? `正在生成 ${format.label}…` : `下载 ${format.label} 报告` }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ disabled?: boolean; docxLoading?: boolean; pdfLoading?: boolean; mdLoading?: boolean }>()
defineEmits<{ download: [format: 'docx' | 'pdf' | 'md'] }>()
const formats = [{ key: 'md', label: 'Markdown' }, { key: 'pdf', label: 'PDF' }, { key: 'docx', label: 'Word' }] as const
function isLoading(format: 'docx' | 'pdf' | 'md') { return format === 'docx' ? props.docxLoading : format === 'pdf' ? props.pdfLoading : props.mdLoading }
</script>

<style scoped>
.report-download-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.report-download-button { display: inline-flex; align-items: center; justify-content: center; gap: 9px; min-height: 42px; padding: 10px 15px; border: 1px solid; border-radius: 7px; font-family: inherit; font-size: 13px; font-weight: 600; line-height: 20px; white-space: nowrap; cursor: pointer; transition: background .15s, border-color .15s; }
.report-download-button svg { width: 22px; height: 22px; flex-shrink: 0; }
.report-download-button--docx { color: #174a8b; background: #eff6ff; border-color: #b6cdec; }
.report-download-button--docx:hover:not(:disabled) { background: #e1edff; border-color: #6899d7; }
.report-download-button--pdf { color: #a12e31; background: #fff3f3; border-color: #e8babc; }
.report-download-button--pdf:hover:not(:disabled) { background: #ffe6e7; border-color: #d2787c; }
.report-download-button:focus-visible { outline: 2px solid #5264d0; outline-offset: 3px; }
.report-download-button:disabled { opacity: .6; cursor: default; }
.report-download-spinner { animation: report-download-spin .8s linear infinite; }
@keyframes report-download-spin { to { transform: rotate(360deg); } }
@media (max-width: 600px) { .report-download-actions { width: 100%; } .report-download-button { flex: 1; padding-right: 10px; padding-left: 10px; } }
@media (prefers-reduced-motion: reduce) { .report-download-spinner { animation: none; } }
</style>
