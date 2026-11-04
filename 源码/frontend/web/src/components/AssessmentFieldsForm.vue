<template>
  <section class="assessment-fields">
    <p>已提取的信息可直接核对；只需补充标记的缺项。关键技术与技术路线可择一提供。</p>
    <label v-for="field in visibleFields" :key="field.key">
      <span>{{ field.label }} <b v-if="missing.some(item => item.key === field.key)">需要补充</b></span>
      <textarea :value="draft[field.key]" rows="3" :aria-invalid="missing.some(item => item.key === field.key)" @input="draft[field.key] = ($event.target as HTMLTextAreaElement).value" />
    </label>
    <details><summary>其他已提取信息</summary><label v-for="field in otherFields" :key="field.key"><span>{{ field.label }}</span><textarea v-model="draft[field.key]" rows="2" /></label></details>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { NINE_FIELDS, missingNineFields, requiredNineFieldKeys, type NineDraft, type SelectedAssessmentType } from '../services/homepageDraft'
const props = defineProps<{ draft: NineDraft; artifactType: SelectedAssessmentType }>()
const missing = computed(() => missingNineFields(props.draft, props.artifactType))
const visibleFields = computed(() => NINE_FIELDS.filter(f => requiredNineFieldKeys(props.artifactType).includes(f.key) || (f.key === 'technical_route' && props.artifactType !== 'guideline')))
const otherFields = computed(() => NINE_FIELDS.filter(f => !visibleFields.value.includes(f)))
</script>
<style scoped>
.assessment-fields{display:grid;gap:12px}.assessment-fields p{font-size:13px;line-height:1.6;color:#526478}.assessment-fields label{display:grid;gap:6px;font-size:14px}.assessment-fields textarea{box-sizing:border-box;width:100%;font:inherit;line-height:1.6;padding:8px;border:1px solid #bbc8d7;border-radius:4px;resize:vertical}.assessment-fields b{color:#b42318;font-size:12px}.assessment-fields details{display:grid;padding:10px 0}.assessment-fields details label{margin-top:10px}
</style>
