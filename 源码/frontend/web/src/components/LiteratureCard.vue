<template>
  <article :class="['research-literature-card', { 'research-literature-card--row': layout === 'row' }]">
    <div v-if="layout !== 'row'" class="research-literature-card__meta"><span><b v-if="item.year" class="research-literature-card__year">{{ item.year }}</b><template v-if="venue"><span v-if="item.year"> · </span>{{ venue }}</template></span><a v-if="url" :href="url" target="_blank" rel="noopener noreferrer" class="research-literature-card__source">文献详情 <span aria-hidden="true">↗</span></a></div>
    <h5><a v-if="url" :href="url" target="_blank" rel="noopener noreferrer">{{ title }}</a><span v-else>{{ title }}</span></h5>
    <div v-if="layout === 'row'" class="research-literature-card__row-meta"><b v-if="item.year" class="research-literature-card__year">{{ item.year }}</b><span v-if="authors">{{ authors }}</span><span v-if="venue">{{ venue }}</span><span class="research-literature-card__scope">{{ evidenceStatus }}</span><button type="button" class="research-literature-card__toggle" :aria-expanded="expanded" :aria-controls="detailId" @click="expanded = !expanded">{{ toggleLabel }}<span class="material-icons-outlined research-literature-card__chevron" aria-hidden="true">expand_more</span></button></div>
    <p v-if="authors && layout !== 'row'" class="research-literature-card__authors">{{ authors }}</p>
    <div v-if="layout !== 'row'" class="research-literature-card__context"><span class="research-literature-card__scope">{{ evidenceStatus }}</span><span v-if="disciplines.length">{{ disciplines.join('、') }}</span></div>
    <button v-if="layout !== 'row'" type="button" class="research-literature-card__toggle research-literature-card__standalone-toggle" :aria-expanded="expanded" :aria-controls="detailId" @click="expanded = !expanded">{{ toggleLabel }}<span class="material-icons-outlined research-literature-card__chevron" aria-hidden="true">expand_more</span></button>
      <div v-show="expanded" :id="detailId" class="research-literature-card__detail-body">
        <section v-if="item.abstract || item.excerpt"><p>{{ item.abstract || item.excerpt }}</p></section>
        <section v-if="readableChunks.length"><h6>可定位正文</h6><blockquote v-for="chunk in readableChunks" :key="chunk.chunk_id"><small>{{ chunkLocation(chunk) }}</small><p>{{ chunk.text || chunk.excerpt }}</p></blockquote></section>
        <p v-if="!item.abstract && !item.excerpt && !readableChunks.length">当前记录包含题名、来源和出版信息。</p>
      </div>
  </article>
</template>
<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { resolveEvidenceUrl } from '../services/evidenceLinks'
import { bibliographyText, chunkLocation, readableLiteratureChunks, literatureEvidenceStatus, type LiteratureEvidence, type LiteratureChunk } from '../services/literaturePresentation'
const props = withDefaults(defineProps<{ item: LiteratureEvidence; chunks?: LiteratureChunk[]; layout?: 'card' | 'row' }>(), { layout: 'card' })
const expanded = ref(false)
const detailId = useId()
const toggleLabel = computed(() => `${expanded.value ? '收起' : '查看'}${props.item.abstract || props.item.excerpt ? '摘要' : '题录'}`)
const url = computed(() => resolveEvidenceUrl(props.item))
const title = computed(() => bibliographyText(props.item.title))
const venue = computed(() => ['literature', 'unknown'].includes((props.item.venue || '').toLowerCase()) ? '' : bibliographyText(props.item.venue))
const authors = computed(() => (props.item.authors || []).slice(0, 3).join('、'))
const disciplines = computed<string[]>(() => props.item.discipline_tags || [])
const readableChunks = computed(() => readableLiteratureChunks(props.item, props.chunks || []))
const evidenceStatus = computed(() => literatureEvidenceStatus(props.item, props.chunks || []))
</script>
<style scoped>
.research-literature-card{min-width:0;padding:17px 20px;border:1px solid #e3e8ef;border-radius:10px;background:#fff;color:#243448;text-align:left;line-height:1.65;overflow-wrap:anywhere;box-shadow:0 2px 5px #24344803}
.research-literature-card__meta{display:flex;align-items:baseline;justify-content:space-between;gap:16px;color:#6c7889;font-size:13px}.research-literature-card__meta>span{min-width:0}
.research-literature-card__year{font-weight:600;color:#47596e;font-variant-numeric:tabular-nums}
.research-literature-card__context{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:7px;color:#788596;font-size:13px}.research-literature-card__scope{color:#647a86}
.research-literature-card h5{margin:7px 0 5px;font-size:17px;line-height:1.6;font-weight:600;text-align:left}
.research-literature-card h5 a{color:#245c99;text-decoration:none}.research-literature-card h5 a:hover{text-decoration:underline}
.research-literature-card__authors{margin:4px 0;color:#627389;font-size:14px}
.research-literature-card__toggle{display:inline-flex;align-items:center;gap:3px;flex:none;padding:0;border:0;background:transparent;color:#2465ae;cursor:pointer;font:inherit;font-size:14px;line-height:24px;white-space:nowrap}.research-literature-card__toggle:hover{color:#174c87}.research-literature-card__toggle:focus-visible{outline:2px solid #2465ae;outline-offset:3px}.research-literature-card__standalone-toggle{margin-top:6px}.research-literature-card__chevron{font-size:18px;line-height:1;transition:transform .15s}.research-literature-card__toggle[aria-expanded="true"] .research-literature-card__chevron{transform:rotate(180deg)}
.research-literature-card__detail-body{margin-top:6px;padding:0;text-align:left;color:#52657b}
.research-literature-card__detail-body h6{margin:8px 0 4px;color:#344a64;font-size:14px}
.research-literature-card__detail-body p{margin:4px 0 10px;white-space:pre-wrap;font-size:15px;line-height:1.75;text-align:left}
.research-literature-card blockquote{margin:10px 0;padding:10px 13px;border-left:3px solid #9fb9cc;background:#f7fafc}
.research-literature-card blockquote small{display:block;color:#627389;font-size:12px}
.research-literature-card__source{flex:none;color:#2465ae;font-size:13px;text-decoration:none}.research-literature-card__source:hover{text-decoration:underline}.research-literature-card a:focus-visible,.research-literature-card summary:focus-visible{outline:2px solid #2465ae;outline-offset:3px}
@media(max-width:600px){.research-literature-card{padding:14px}.research-literature-card__meta{gap:10px}.research-literature-card h5{font-size:16px}}
.research-literature-card--row{box-sizing:border-box;width:100%;padding:12px 0;border:0;border-bottom:1px solid #e9edf3;border-radius:0;box-shadow:none;background:transparent}
.research-literature-card--row h5{margin:0 0 3px;font-size:16px;font-weight:500;line-height:1.6}
.research-literature-card__row-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 12px;color:#718094;font-size:13px;line-height:1.7}
@media(max-width:600px){.research-literature-card--row{padding:12px 0}.research-literature-card__row-meta{gap:2px 10px}}
</style>
