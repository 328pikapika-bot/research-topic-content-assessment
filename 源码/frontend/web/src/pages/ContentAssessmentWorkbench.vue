<template>
  <main class="workspace">
    <header class="topbar">
      <div><strong>创新性评估智能体</strong><span>项目材料主张、证据与创新性评估报告</span></div>
      <button type="button" class="text-button" @click="$emit('navigate', 'tasks')">任务历史</button>
    </header>
    <section class="layout">
      <form class="intake" @submit.prevent="startAssessment">
        <h1>新建创新性评估</h1>
        <label>评估标题<input v-model.trim="title" maxlength="300" placeholder="例如：多器官芯片互作及培养系统研制与应用" /></label>
        <label>评估说明<div class="assessment-input-area"><span class="assessment-type-hint">（论文／项目／指南）</span><textarea ref="inputRef" v-model="input" @input="onGuidedInput" @compositionend="onGuidedInput" rows="8" maxlength="12000" :placeholder="assessmentPlaceholder" /><span v-if="inputFeedback" class="hint assessment-input-feedback" role="status">{{ inputFeedback }}</span></div></label>
        <p class="hint auto-note">材料类型、文件角色、评估范围和报告档位将由系统根据上传内容自动识别；只有识别结果存在冲突时才会请求确认。</p>
        <div class="range-row">
          <label>检索起始年份<input v-model.number="yearStart" type="number" min="1900" max="2100" /></label>
          <label>检索结束年份<input v-model.number="yearEnd" type="number" min="1900" max="2100" /></label>
        </div>
        <div class="materials-head"><h2>评估材料</h2><label class="upload"><input type="file" accept=".pdf,.docx,.xlsx,.csv,.pptx,.txt,.md,.png,.jpg,.jpeg" multiple @change="chooseFiles" />添加材料</label></div>
        <p class="hint">系统会自动识别每份材料的用途，并据此建立主张、证据和检索范围。</p>
        <div v-if="materials.length" class="material-list">
          <article v-for="material in materials" :key="material.localId">
            <div><strong>{{ material.file.name }}</strong><small>{{ Math.ceil(material.file.size / 1024) }} KB</small></div>
            <span :class="['file-status', material.status]">{{ statusText(material) }}</span>
            <button type="button" class="icon-button" title="移除材料" @click="removeMaterial(material.localId)">×</button>
            <p v-if="material.error" class="material-error" role="alert">{{ material.error }}</p>
          </article>
        </div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button class="primary" type="submit" :disabled="submitting || (!assessmentSourceText(input) && !materials.length)">{{ submitting ? '正在识别材料' : needsSupplement ? '补充并继续' : '开始评估' }}</button>
      </form>
      <aside class="guide">
        <h2>评估流程</h2>
        <ol><li v-for="stage in stages" :key="stage[0]"><strong>{{ stage[0] }}</strong><span>{{ stage[1] }}</span></li></ol>
        <div class="notice"><strong>结论边界</strong><p>没有可定位的独立外部证据时，系统只会给出探索性判断，不会将检索未命中表述为绝对原创。</p></div>
      </aside>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref, reactive, watch, nextTick } from 'vue'
import { assessmentInputStarter, assessmentGuidedInput, assessmentSourceText, declaredAssessmentType, normalizeAssessmentPreview, type AssessmentInputType, assessmentPlaceholder, assessmentPreviewMessage, assessmentPreviewReady } from '../services/assessmentInput'
import { emptyNineDraft, mergeNineDraft, serializeNineDraft } from '../services/homepageDraft'
import { getUploadErrorInfo } from '../services/fileUploadErrors'
import { runContentAssessment, uploadMaterial, previewNineFields, type UploadedFileRef } from '../services/contentAssessmentApi'

const emit = defineEmits<{ navigate: [page: string, taskId?: string, payload?: unknown] }>()
const title = ref('')
const input = ref(assessmentInputStarter)
const inputRef = ref<HTMLTextAreaElement | null>(null)
let guidedType: AssessmentInputType | undefined
function onGuidedInput(event: Event) {
  if ((event as InputEvent).isComposing) return
  const guided = assessmentGuidedInput(input.value, guidedType)
  guidedType = guided.type
  if (guided.text !== input.value) {
    input.value = guided.text
    void nextTick(() => { if (guided.cursor !== undefined) inputRef.value?.setSelectionRange(guided.cursor, guided.cursor) })
  }
}
const inputFeedback = ref('')
const needsSupplement = ref(false)
const yearStart = ref(2020)
const yearEnd = ref(new Date().getFullYear())
const submitting = ref(false)
const error = ref('')
const fieldDraft = reactive(emptyNineDraft())
let inputVersion = 0
type Material = { localId: string; file: File; status: 'pending' | 'uploading' | 'ready' | 'failed'; uploaded?: UploadedFileRef; error?: string; errorCode?: string }
const materials = ref<Material[]>([])
watch([input, () => materials.value.map(m => m.localId).join(',')], () => { inputVersion++; inputFeedback.value = '' }, { flush: 'sync' })
const stages = [['01', '材料与要点'], ['02', '要点检索'], ['03', '相关研究分析'], ['04', '创新性与趋势'], ['05', '报告']]

function chooseFiles(event: Event) {
  const target = event.target as HTMLInputElement
  for (const file of Array.from(target.files || [])) {
    if (materials.value.length >= 10) { error.value = '一次最多可提交 10 份材料。'; break }
    materials.value.push({ localId: `${file.name}-${file.lastModified}-${Math.random()}`, file, status: 'pending' })
  }
  target.value = ''
}
function removeMaterial(id: string) { materials.value = materials.value.filter((item) => item.localId !== id) }
function statusText(item: Material) { return item.status === 'ready' ? '已解析' : item.status === 'uploading' ? '解析中' : item.status === 'failed' ? getUploadErrorInfo({ code: item.errorCode }).label : '待上传' }

async function startAssessment() {
  if (submitting.value) return
  error.value = ''
  if (yearStart.value > yearEnd.value) { error.value = '检索年份的起始值不能晚于结束值。'; return }
  submitting.value = true
  const version = inputVersion
  const sourceText = assessmentSourceText(input.value)
  let identifying = false
  try {
    for (const material of materials.value) {
      if (material.uploaded) continue
      material.status = 'uploading'
      material.error = ''
      material.errorCode = ''
      try { material.uploaded = await uploadMaterial(material.file); material.status = 'ready' }
      catch (reason) { const info = getUploadErrorInfo(reason); material.status = 'failed'; material.error = info.message; material.errorCode = info.code; throw reason }
    }
    const uploaded = materials.value.map((item) => item.uploaded).filter(Boolean) as UploadedFileRef[]
    if (version !== inputVersion) return
    identifying = true
    const preview = normalizeAssessmentPreview(await previewNineFields(sourceText, uploaded, declaredAssessmentType(sourceText) || 'auto'), sourceText)
    if (version !== inputVersion) return
    inputFeedback.value = assessmentPreviewMessage(preview, sourceText)
    needsSupplement.value = !assessmentPreviewReady(preview) && !preview.extraction_failed
    if (!assessmentPreviewReady(preview)) return
    identifying = false
    mergeNineDraft(fieldDraft, preview.nine_fields, new Set())
    const run = await runContentAssessment({ user_input: sourceText || title.value.trim() || uploaded[0]?.name || '上传材料评估', assessment_title: title.value, year_range: [yearStart.value, yearEnd.value], uploaded_files: uploaded, artifact_type: preview.resolved_artifact_type as 'paper' | 'project' | 'guideline', nine_fields: serializeNineDraft(fieldDraft) })
    emit('navigate', 'task-running', run.run_id, run)
  } catch (reason) {
    if (version !== inputVersion) return
    if (identifying) inputFeedback.value = reason instanceof Error && reason.name === 'AssessmentServiceVersionError'
      ? reason.message : '材料识别暂未完成，请重试，或在输入框中粘贴材料正文。'
    else error.value = reason instanceof Error ? reason.message : '创建创新性评估任务失败。'
  } finally { submitting.value = false }
}
</script>

<style scoped>
.assessment-type-hint{display:block;margin:8px 10px 0;color:#9aa4b2;font-size:13px;font-weight:400;line-height:1.5}

.assessment-input-area{border:1px solid #b8c4d2;border-radius:4px;background:#fff;overflow:hidden}.intake .assessment-input-area textarea{display:block;width:100%;box-sizing:border-box;border:0;border-radius:0}.assessment-input-area:focus-within{outline:2px solid #80b7e4;outline-offset:1px}.assessment-input-feedback{display:block;margin:0 10px 10px;font-weight:400;overflow-wrap:anywhere}

.file-status{overflow-wrap:anywhere;line-height:1.4}
.material-error{grid-column:1/-1;color:#b42318;font-size:12px;line-height:1.5;margin:0;overflow-wrap:anywhere}
.workspace{min-height:100vh;background:#f6f8fb;color:#172033}.topbar{height:58px;background:#fff;border-bottom:1px solid #dfe5ed;display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100vw - 1280px)/2));gap:16px}.topbar strong{font-size:17px}.topbar span{font-size:13px;color:#627083;margin-left:14px}.text-button,.icon-button{border:0;background:transparent;color:#0f766e;cursor:pointer;font:inherit}.layout{max-width:1180px;margin:0 auto;padding:32px 24px;display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:28px}.intake,.guide{background:#fff;border:1px solid #dfe5ed;border-radius:6px;padding:28px}.intake h1{font-size:22px;margin:0 0 24px}.intake h2,.guide h2{font-size:16px;margin:0}.intake>label,.range-row label{display:grid;gap:7px;font-size:14px;font-weight:600;margin-bottom:18px}.intake input,.intake textarea,.intake select{font:inherit;border:1px solid #b8c4d2;border-radius:4px;padding:10px;background:#fff}.intake textarea{resize:vertical;line-height:1.55}.range-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.mode-panel{border-top:1px solid #dfe5ed;border-bottom:1px solid #dfe5ed;padding:18px 0 4px;margin:4px 0 18px}.mode-panel-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}.mode-panel-head span{font-size:12px;color:#627083}.materials-head{display:flex;justify-content:space-between;align-items:center;margin-top:6px}.upload{font-size:13px;color:#0f766e;cursor:pointer}.upload input{display:none}.hint{font-size:13px;line-height:1.5;color:#627083}.material-list{display:grid;gap:8px}.material-list article{display:grid;grid-template-columns:minmax(0,1fr) 132px 60px 28px;gap:10px;align-items:center;border:1px solid #dfe5ed;padding:10px;border-radius:4px}.material-list strong,.material-list small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.material-list small,.file-status{font-size:12px;color:#627083}.file-status.ready{color:#047857}.file-status.failed,.error{color:#b42318}.primary{margin-top:22px;border:0;border-radius:4px;background:#0f766e;color:#fff;font:inherit;padding:11px 18px;cursor:pointer}.primary:disabled{opacity:.55;cursor:wait}.guide ol{padding:0;margin:18px 0;list-style:none;display:grid;gap:14px}.guide li{display:grid;grid-template-columns:42px 1fr;gap:10px;font-size:14px}.guide li strong{font-size:12px;color:#0f766e}.guide li span{line-height:1.4}.notice{border-top:1px solid #dfe5ed;padding-top:18px;font-size:13px;line-height:1.55;color:#45556c}.notice p{margin-bottom:0}@media(max-width:820px){.layout{grid-template-columns:1fr;padding:18px}.guide{order:-1}.topbar span{display:none}.material-list article{grid-template-columns:minmax(0,1fr) 112px 50px 24px}}
 .section-options{display:none}.auto-note{padding:12px 0;border-top:1px solid #dfe5ed;border-bottom:1px solid #dfe5ed}.material-list article{grid-template-columns:minmax(0,1fr) 60px 28px}@media(max-width:820px){.material-list article{grid-template-columns:minmax(0,1fr) 50px 24px}}
</style>
