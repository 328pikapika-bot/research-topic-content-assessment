<template>
  <section ref="viewer" class="report-pdf-viewer" :class="{ 'report-pdf-viewer--expanded': expanded }" aria-label="正式报告 PDF 阅读器">
    <header class="report-pdf-toolbar">
      <div class="report-pdf-title"><span>正式报告预览</span><h2>{{ title }}</h2></div>
      <div class="report-pdf-controls">
        <button type="button" :disabled="pageNumber <= 1 || !pageCount" @click="goToPage(pageNumber - 1)" aria-label="上一页">‹</button>
        <label>第 <input v-model.number="pageNumber" type="number" min="1" :max="pageCount || 1" @change="goToPage(pageNumber)" @keydown.enter="goToPage(pageNumber)" aria-label="报告页码" /> / {{ pageCount || '—' }} 页</label>
        <button type="button" :disabled="pageNumber >= pageCount || !pageCount" @click="goToPage(pageNumber + 1)" aria-label="下一页">›</button>
        <button type="button" :disabled="zoom <= .5 || !pageCount" @click="zoom = Math.max(.5, zoom - .25)" aria-label="缩小">−</button>
        <span>{{ Math.round(zoom * 100) }}%</span>
        <button type="button" :disabled="zoom >= 2 || !pageCount" @click="zoom = Math.min(2, zoom + .25)" aria-label="放大">＋</button>
        <button type="button" @click="zoom = 1">适应宽度</button>
        <button type="button" :disabled="!outline.length" :aria-expanded="showOutline" @click="showOutline = !showOutline">目录</button>
        <button type="button" :disabled="!pageCount" @click="fullscreen">{{ expanded || nativeFullscreen ? '退出全屏' : '全屏' }}</button>
        <button type="button" :disabled="loading" @click="load">重新加载</button>
        <button type="button" :disabled="downloading" @click="$emit('download')">{{ downloading ? '正在生成 PDF…' : '下载 PDF 报告' }}</button>
      </div>
    </header>
    <div v-if="loading" class="report-pdf-message" role="status">正在生成并加载正式报告...</div>
    <div v-else-if="error" class="report-pdf-message report-pdf-message--error" role="alert"><p>{{ error }}</p><button type="button" @click="load">重试</button><p>可使用上方“下载 Word 报告”入口获取报告。</p></div>
    <div v-else class="report-pdf-body">
      <aside v-if="outline.length" v-show="showOutline" aria-label="报告目录" class="report-pdf-outline"><h3>目录</h3><button v-for="(item, index) in outline" :key="index" type="button" :style="{ paddingLeft: `${12 + item.depth * 12}px` }" @click="goToDestination(item.dest)">{{ item.title }}</button></aside>
      <div ref="scroller" class="report-pdf-scroll" @scroll.passive="updateCurrentPage">
        <article v-for="number in pageCount" :key="number" :ref="element => setPageElement(number, element)" class="report-pdf-page" :style="{ width: `${pageWidth}px`, height: `${pageHeight}px` }" :aria-label="`报告第 ${number} 页`">
          <canvas :aria-label="`第 ${number} 页`" />
          <div class="textLayer" />
          <div class="report-pdf-link-layer" />
        </article>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type PDFDocumentLoadingTask, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/web/pdf_viewer.css'
import { fetchContentAssessmentReportPdf } from '../services/contentAssessmentUiApi'

GlobalWorkerOptions.workerSrc = workerUrl
const props = defineProps<{ taskId: string; title: string; version?: string; downloading?: boolean; loadPdf?: (taskId: string, signal?: AbortSignal) => Promise<Blob> }>()
const emit = defineEmits<{ download: []; loaded: [pages: number] }>()
const viewer = ref<HTMLElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const pageCount = ref(0)
const pageNumber = ref(1)
const pageWidth = ref(0)
const pageHeight = ref(0)
const zoom = ref(1)
const showOutline = ref(window.innerWidth > 760)
const expanded = ref(false)
const nativeFullscreen = ref(false)
const outline = ref<Array<{ title: string; dest: unknown; depth: number }>>([])
const elements = new Map<number, HTMLElement>()
const renderedPages = new Set<number>()
const wantedPages = new Set<number>()
let document: PDFDocumentProxy | null = null
let loadedDocument: PDFDocumentProxy | null = null
let loadingTask: PDFDocumentLoadingTask | null = null
let abortController: AbortController | null = null
let renderTask: RenderTask | null = null
let textLayer: TextLayer | null = null
let revision = 0
let drawRevision = 0
let rendering = false
let frame = 0
let basePageWidth = 0
let basePageHeight = 0
let resizeObserver: ResizeObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | undefined
let lastWidth = 0
let previousBodyOverflow = ''

function setPageElement(number: number, element: unknown) {
  if (element instanceof HTMLElement) elements.set(number, element)
  else elements.delete(number)
}
function cancelDrawing() {
  drawRevision++
  renderTask?.cancel(); renderTask = null
  textLayer?.cancel(); textLayer = null
  rendering = false
  renderedPages.clear(); wantedPages.clear()
  for (const element of elements.values()) clearPage(element)
  if (frame) cancelAnimationFrame(frame)
  frame = 0
}
function clearPage(element: HTMLElement) {
  const canvas = element.querySelector('canvas')
  if (canvas) { canvas.width = 0; canvas.height = 0 }
  element.querySelector('.textLayer')?.replaceChildren()
  element.querySelector('.report-pdf-link-layer')?.replaceChildren()
}
function updatePageSize() {
  if (!scroller.value || !basePageWidth) return
  pageWidth.value = Math.min(Math.max(220, scroller.value.clientWidth - 32), 1000) * zoom.value
  pageHeight.value = pageWidth.value * basePageHeight / basePageWidth
}
function refreshVisiblePages() {
  const scroll = scroller.value
  if (!document || !scroll || !pageCount.value) return
  const top = scroll.scrollTop
  const bottom = top + scroll.clientHeight
  const visible: number[] = []
  for (const [number, element] of elements) {
    if (element.offsetTop + element.offsetHeight >= top && element.offsetTop <= bottom) visible.push(number)
  }
  const next = new Set<number>()
  for (const number of visible.length ? visible : [pageNumber.value]) {
    for (let nearby = Math.max(1, number - 1); nearby <= Math.min(pageCount.value, number + 1); nearby++) next.add(nearby)
  }
  wantedPages.clear()
  for (const number of next) wantedPages.add(number)
  for (const number of renderedPages) {
    if (next.has(number)) continue
    const element = elements.get(number)
    if (element) clearPage(element)
    renderedPages.delete(number)
  }
  void renderVisiblePages()
}
function scheduleVisiblePages() {
  if (frame) return
  frame = requestAnimationFrame(() => { frame = 0; refreshVisiblePages() })
}
async function dispose() {
  cancelDrawing()
  abortController?.abort(); abortController = null
  const oldTask = loadingTask; loadingTask = null
  document = null
  loadedDocument = null
  if (oldTask) await oldTask.destroy()
}
async function load() {
  const current = ++revision
  await dispose()
  if (current !== revision) return
  loading.value = true; error.value = ''; pageCount.value = 0; outline.value = []
  abortController = new AbortController()
  try {
    const blob = await (props.loadPdf || fetchContentAssessmentReportPdf)(props.taskId, abortController.signal)
    if (current !== revision) return
    const bytes = new Uint8Array(await blob.arrayBuffer())
    if (current !== revision) return
    loadingTask = getDocument({ data: bytes, isEvalSupported: false })
    const pdf = await loadingTask.promise
    if (current !== revision) return
    document = pdf
    const firstPage = await pdf.getPage(1)
    if (current !== revision) return
    const firstViewport = firstPage.getViewport({ scale: 1 })
    basePageWidth = firstViewport.width; basePageHeight = firstViewport.height
    firstPage.cleanup()
    pageCount.value = pdf.numPages; pageNumber.value = 1
    const flatten = (items: any[], depth = 0): Array<{ title: string; dest: unknown; depth: number }> => items.flatMap(item => [{ title: item.title, dest: item.dest, depth }, ...flatten(item.items || [], depth + 1)])
    const bookmarks = await pdf.getOutline()
    if (current !== revision) return
    outline.value = flatten(bookmarks || [])
    loading.value = false
    await nextTick()
    updatePageSize()
    refreshVisiblePages()
  } catch (cause) {
    if (current !== revision || (cause instanceof Error && cause.name === 'AbortError')) return
    error.value = cause instanceof Error ? cause.message : '报告预览加载失败'
  } finally {
    if (current === revision) loading.value = false
  }
}
async function renderVisiblePages() {
  const pdf = document
  if (!pdf || rendering) return
  rendering = true
  const current = drawRevision
  try {
    while (current === drawRevision && pdf === document) {
      const number = [...wantedPages].find(item => !renderedPages.has(item))
      if (number === undefined) break
      const page = await pdf.getPage(number)
      if (current !== drawRevision) return
      const element = elements.get(number)
      if (!element) continue
      const base = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: pageWidth.value / base.width })
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
      const canvas = element.querySelector('canvas')!
      const context = canvas.getContext('2d')!
      element.style.width = `${viewport.width}px`; element.style.height = `${viewport.height}px`
      element.style.setProperty('--scale-factor', String(viewport.scale))
      canvas.width = Math.ceil(viewport.width * pixelRatio); canvas.height = Math.ceil(viewport.height * pixelRatio)
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
      renderTask = page.render({ canvasContext: context, viewport, transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0] })
      await renderTask.promise
      if (current !== drawRevision) return
      const textContainer = element.querySelector<HTMLElement>('.textLayer')!
      textContainer.replaceChildren()
      const content = await page.getTextContent()
      if (current !== drawRevision) return
      textLayer = new TextLayer({ textContentSource: content, container: textContainer, viewport })
      await textLayer.render()
      if (current !== drawRevision) return
      const linkContainer = element.querySelector<HTMLElement>('.report-pdf-link-layer')!
      linkContainer.replaceChildren()
      const annotations = await page.getAnnotations()
      if (current !== drawRevision) return
      for (const annotation of annotations) {
        if (annotation.subtype !== 'Link' || !annotation.rect) continue
        const [x1, y1, x2, y2] = viewport.convertToViewportRectangle(annotation.rect)
        const link = window.document.createElement('a')
        link.style.left = `${Math.min(x1, x2)}px`; link.style.top = `${Math.min(y1, y2)}px`
        link.style.width = `${Math.abs(x2 - x1)}px`; link.style.height = `${Math.abs(y2 - y1)}px`
        const candidateUrl = String(annotation.url || annotation.unsafeUrl || '').trim()
        if (/^https?:\/\//i.test(candidateUrl)) {
          // PDF.js exposes normal URI actions as url and some Writer-generated
          // PDFs as unsafeUrl. Both are accepted only after the same protocol
          // check; internal destinations continue to use the local jump path.
          link.href = candidateUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', '打开引用来源')
        } else if (annotation.dest) {
          link.href = '#'; link.setAttribute('aria-label', '跳转到报告目录目标')
          link.addEventListener('click', event => { event.preventDefault(); void goToDestination(annotation.dest) })
        } else continue
        linkContainer.append(link)
      }
      page.cleanup()
      if (wantedPages.has(number)) renderedPages.add(number)
      else clearPage(element)
      if (number === 1 && loadedDocument !== pdf) {
        loadedDocument = pdf
        emit('loaded', pdf.numPages)
      }
    }
  } catch (cause) {
    if (current === drawRevision && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) error.value = cause instanceof Error ? cause.message : '报告页面渲染失败'
  } finally {
    if (current === drawRevision) rendering = false
  }
}
function goToPage(number: number) {
  pageNumber.value = Math.max(1, Math.min(pageCount.value, Math.trunc(Number(number)) || 1))
  const element = elements.get(pageNumber.value)
  if (element && scroller.value) {
    scroller.value.scrollTo({ top: element.offsetTop - 12 })
    refreshVisiblePages()
  }
}
async function goToDestination(destination: unknown) {
  const pdf = document
  if (!pdf) return
  const dest = typeof destination === 'string' ? await pdf.getDestination(destination) : destination
  if (pdf !== document) return
  if (!Array.isArray(dest) || !dest.length) return
  const index = typeof dest[0] === 'number' ? dest[0] : await pdf.getPageIndex(dest[0])
  if (pdf !== document) return
  goToPage(index + 1)
}
function updateCurrentPage() {
  if (!scroller.value) return
  const top = scroller.value.getBoundingClientRect().top
  for (const [number, element] of elements) {
    const rect = element.getBoundingClientRect()
    if (rect.bottom > top + 60) { pageNumber.value = number; break }
  }
  scheduleVisiblePages()
}
async function fullscreen() {
  if (expanded.value) { setExpanded(false); return }
  try {
    if (window.document.fullscreenElement) await window.document.exitFullscreen()
    else {
      await viewer.value?.requestFullscreen()
      if (!window.document.fullscreenElement) setExpanded(true)
    }
  } catch { setExpanded(true) }
}
function setExpanded(value: boolean) {
  if (value) {
    previousBodyOverflow = window.document.body.style.overflow
    window.document.body.style.overflow = 'hidden'
  } else if (expanded.value) window.document.body.style.overflow = previousBodyOverflow
  expanded.value = value
}
function onFullscreenChange() { nativeFullscreen.value = window.document.fullscreenElement === viewer.value }
function onEscape(event: KeyboardEvent) { if (event.key === 'Escape' && expanded.value) setExpanded(false) }
function relayoutPages() {
  cancelDrawing()
  updatePageSize()
  nextTick(() => refreshVisiblePages())
}
watch(zoom, relayoutPages)
watch(showOutline, async () => { await nextTick(); relayoutPages() })
watch(() => [props.taskId, props.version, props.loadPdf], () => { void load() })
onMounted(() => {
  window.document.addEventListener('fullscreenchange', onFullscreenChange)
  window.document.addEventListener('keydown', onEscape)
  resizeObserver = new ResizeObserver(entries => {
    const width = Math.round(entries[0].contentRect.width)
    if (width === lastWidth) return
    lastWidth = width
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(relayoutPages, 150)
  })
  if (viewer.value) resizeObserver.observe(viewer.value)
  void load()
})
onBeforeUnmount(() => {
  revision++
  setExpanded(false)
  window.document.removeEventListener('fullscreenchange', onFullscreenChange)
  window.document.removeEventListener('keydown', onEscape)
  resizeObserver?.disconnect(); clearTimeout(resizeTimer); void dispose(); elements.clear()
})
</script>

<style>
.report-pdf-viewer { border: 1px solid #dbe2ec; background: #fff; color: #334155; min-width: 0; }
.report-pdf-toolbar { padding: 16px 20px; border-bottom: 1px solid #dbe2ec; display: flex; gap: 18px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.report-pdf-title span { color: #0d9488; font-size: 11px; }
.report-pdf-title h2 { font-size: 17px; margin: 5px 0 0; }
.report-pdf-controls { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; font-size: 12px; }
.report-pdf-controls button, .report-pdf-message button { padding: 6px 9px; background: #fff; border: 1px solid #dbe2ec; border-radius: 4px; cursor: pointer; color: #334155; }
.report-pdf-controls button:disabled { opacity: .4; cursor: default; }
.report-pdf-controls input { width: 48px; padding: 5px 2px; border: 1px solid #dbe2ec; color: #334155; }
.report-pdf-message { display: grid; justify-items: center; align-content: center; min-height: 300px; gap: 10px; padding: 20px; }
.report-pdf-message--error { color: #a63d32; }
.report-pdf-body { display: flex; min-width: 0; }
.report-pdf-outline { width: 210px; flex: 0 0 210px; max-height: 80vh; overflow: auto; padding: 12px 0; border-right: 1px solid #dbe2ec; }
.report-pdf-outline h3 { padding: 0 14px; font-size: 14px; }
.report-pdf-outline button { display: block; width: 100%; text-align: left; border: 0; padding: 8px 12px; background: transparent; font-size: 12px; color: #475569; cursor: pointer; line-height: 1.5; }
.report-pdf-outline button:hover { background: #eef5ff; color: #1e3a8a; }
.report-pdf-scroll { position: relative; flex: 1; min-width: 0; height: 80vh; min-height: 520px; overflow: auto; padding: 16px; background: #e9edf2; }
.report-pdf-page { position: relative; margin: 0 auto 16px; width: 100%; min-height: 100px; background: #fff; box-shadow: 0 2px 8px #0f17231a; content-visibility: auto; contain-intrinsic-size: 1000px 1400px; }
.report-pdf-page canvas { display: block; }
.report-pdf-link-layer { position: absolute; inset: 0; pointer-events: none; z-index: 2; }
.report-pdf-link-layer a { position: absolute; pointer-events: auto; cursor: pointer; }
.report-pdf-link-layer a:hover { background: #2563eb22; outline: 1px solid #2563eb; }
.report-pdf-viewer:fullscreen { overflow: auto; }
.report-pdf-viewer:fullscreen .report-pdf-scroll { height: calc(100vh - 100px); }
.report-pdf-viewer--expanded { position: fixed; inset: 0; z-index: 10000; display: flex; flex-direction: column; border: 0; }
.report-pdf-viewer--expanded .report-pdf-body { flex: 1; min-height: 0; overflow: hidden; }
.report-pdf-viewer--expanded .report-pdf-scroll { height: auto; min-height: 0; }
.report-pdf-viewer--expanded .report-pdf-outline { max-height: 100%; }
@media (max-width: 760px) { .report-pdf-body { flex-direction: column; } .report-pdf-outline { width: 100%; flex-basis: auto; max-height: 220px; border-right: 0; border-bottom: 1px solid #dbe2ec; } .report-pdf-toolbar { padding: 12px; } .report-pdf-scroll { flex: auto; height: 72vh; padding: 10px; } }
</style>
