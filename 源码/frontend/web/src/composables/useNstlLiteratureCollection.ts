import { computed, ref, watch, type Ref } from 'vue'
import {
  addNstlCollection,
  deleteNstlCollection,
  getNstlCollectionIds,
  type EvidenceItem,
} from '../services/contentAssessmentUiApi'
import { confirm } from './useConfirm'
import { useToast } from './useToast'
import { errorKind, trackEvent, trackOutcome } from '../tracker/contentAssessmentTracker'

const NSTL_COLLECTION_CACHE_KEY = 'nstl_collections'
const MAX_LITERATURE_SELECTION = 10

export function useNstlLiteratureCollection(evidenceItems: Ref<EvidenceItem[]>) {
  const literatureSelectionMode = ref(false)
  const selectedLiterature = ref<Record<string, boolean>>({})
  const literatureCollectLoading = ref(false)
  const literatureCollectNotice = ref('')
  const literatureCollectingKeys = ref<Set<string>>(new Set())
  const literatureCollectionIds = ref<Set<string>>(new Set(readCachedCollectionIds()))

  const selectedLiteratureCount = computed(() => (
    evidenceItems.value.filter((item) => selectedLiterature.value[item.evidence_id]).length
  ))
  const allLiteratureSelected = computed(() => (
    evidenceItems.value.length > 0 && selectedLiteratureCount.value === Math.min(evidenceItems.value.length, MAX_LITERATURE_SELECTION)
  ))
  const selectedLiteraturePaperIds = computed(() => uniqueIds(
    evidenceItems.value
      .filter((item) => selectedLiterature.value[item.evidence_id])
      .map((item) => literaturePaperId(item))
      .filter(Boolean),
  ))

  function enterLiteratureSelectionMode() {
    literatureSelectionMode.value = true
    trackEvent('content_assessment_literature_batch_mode', '进入批量收藏', {
      literature_count: evidenceItems.value.length,
    })
  }

  function cancelLiteratureSelection() {
    if (literatureSelectionMode.value) {
      trackOutcome('content_assessment_literature_batch_mode', '取消批量收藏', 'cancelled', {
        selected_count: selectedLiteratureCount.value,
      })
    }
    clearLiteratureSelection()
  }

  function clearLiteratureSelection() {
    literatureSelectionMode.value = false
    selectedLiterature.value = {}
  }

  function toggleLiteratureSelectAll(event: Event) {
    const checked = (event.target as HTMLInputElement).checked
    selectedLiterature.value = checked
      ? Object.fromEntries(evidenceItems.value.slice(0, MAX_LITERATURE_SELECTION).map((item) => [item.evidence_id, true]))
      : {}
    trackEvent('content_assessment_literature_select_all', checked ? '全选文献' : '取消全选文献', {
      selected_count: checked ? Math.min(evidenceItems.value.length, MAX_LITERATURE_SELECTION) : 0,
    })
  }

  function toggleLiteratureSelection(evidenceId: string) {
    if (!selectedLiterature.value[evidenceId] && selectedLiteratureCount.value >= MAX_LITERATURE_SELECTION) {
      useToast().error(`最多选择${MAX_LITERATURE_SELECTION}条`)
      return
    }
    selectedLiterature.value = {
      ...selectedLiterature.value,
      [evidenceId]: !selectedLiterature.value[evidenceId],
    }
    trackEvent('content_assessment_literature_select_one', '选择文献', {
      selected: Boolean(selectedLiterature.value[evidenceId]),
      selected_count: selectedLiteratureCount.value,
    })
  }

  async function refreshLiteratureCollections() {
    try {
      const ids = await getNstlCollectionIds()
      literatureCollectionIds.value = new Set(ids)
      writeCachedCollectionIds(ids)
      trackOutcome('content_assessment_literature_collection_sync', '同步收藏状态', 'success', {
        collection_count: ids.length,
      })
    } catch {
      literatureCollectionIds.value = new Set(readCachedCollectionIds())
      trackOutcome('content_assessment_literature_collection_sync', '同步收藏状态', 'failure')
    }
  }

  async function batchCollectLiterature() {
    if (!selectedLiteratureCount.value) {
      trackOutcome('content_assessment_literature_batch_collect', '批量收藏', 'blocked', { reason: 'empty_selection' })
      useToast().error('请先选择文献')
      return
    }
    if (!selectedLiteraturePaperIds.value.length) {
      trackOutcome('content_assessment_literature_batch_collect', '批量收藏', 'blocked', {
        reason: 'missing_paper_id',
        selected_count: selectedLiteratureCount.value,
      })
      useToast().error('所选文献缺少可收藏的 NSTL 文献ID')
      return
    }
    if (!await confirm('是否添加收藏？')) {
      trackOutcome('content_assessment_literature_batch_collect', '批量收藏', 'cancelled', {
        selected_count: selectedLiteratureCount.value,
      })
      return
    }

    literatureCollectLoading.value = true
    try {
      await addNstlCollection(selectedLiteraturePaperIds.value)
      mergeCollectionIds(selectedLiteraturePaperIds.value)
      useToast().success('收藏成功')
      showLiteratureNotice('收藏成功')
      trackOutcome('content_assessment_literature_batch_collect', '批量收藏', 'success', {
        selected_count: selectedLiteratureCount.value,
        paper_count: selectedLiteraturePaperIds.value.length,
      })
      clearLiteratureSelection()
    } catch (error) {
      trackOutcome('content_assessment_literature_batch_collect', '批量收藏', 'failure', {
        selected_count: selectedLiteratureCount.value,
        error_kind: errorKind(error),
      })
      const message = errorMessage(error, '收藏失败')
      useToast().error(message)
      showLiteratureNotice(message)
    } finally {
      literatureCollectLoading.value = false
    }
  }

  async function toggleLiteratureCollection(item: EvidenceItem) {
    const paperId = literaturePaperId(item)
    if (!paperId) {
      trackOutcome('content_assessment_literature_collect_one', '收藏文献', 'blocked', { reason: 'missing_paper_id' })
      useToast().error('当前文献缺少可收藏的 NSTL 文献ID')
      return
    }

    const removing = literatureCollectionIds.value.has(paperId)
    if (!await confirm(removing ? '是否删除收藏？' : '是否添加收藏？')) {
      trackOutcome('content_assessment_literature_collect_one', removing ? '取消收藏文献' : '收藏文献', 'cancelled')
      return
    }

    addCollectingKey(item.evidence_id)
    try {
      if (removing) {
        await deleteNstlCollection(paperId)
        removeCollectionId(paperId)
        useToast().success('取消收藏成功')
        showLiteratureNotice('取消收藏成功')
        trackOutcome('content_assessment_literature_collect_one', '取消收藏文献', 'success')
      } else {
        await addNstlCollection(paperId)
        mergeCollectionIds([paperId])
        useToast().success('收藏成功')
        showLiteratureNotice('收藏成功')
        trackOutcome('content_assessment_literature_collect_one', '收藏文献', 'success')
      }
    } catch (error) {
      trackOutcome('content_assessment_literature_collect_one', removing ? '取消收藏文献' : '收藏文献', 'failure', {
        error_kind: errorKind(error),
      })
      const message = errorMessage(error, removing ? '取消收藏失败' : '收藏失败')
      useToast().error(message)
      showLiteratureNotice(message)
    } finally {
      removeCollectingKey(item.evidence_id)
    }
  }

  function isLiteratureCollected(item: EvidenceItem) {
    const paperId = literaturePaperId(item)
    return Boolean(paperId && literatureCollectionIds.value.has(paperId))
  }

  function isLiteratureCollecting(item: EvidenceItem) {
    return literatureCollectingKeys.value.has(item.evidence_id)
  }

  function canCollectLiterature(item: EvidenceItem) {
    return Boolean(literaturePaperId(item))
  }

  function mergeCollectionIds(ids: string[]) {
    const merged = uniqueIds([...literatureCollectionIds.value, ...ids])
    literatureCollectionIds.value = new Set(merged)
    writeCachedCollectionIds(merged)
  }

  function removeCollectionId(id: string) {
    const next = [...literatureCollectionIds.value].filter((item) => item !== id)
    literatureCollectionIds.value = new Set(next)
    writeCachedCollectionIds(next)
  }

  function showLiteratureNotice(message: string) {
    literatureCollectNotice.value = message
    window.setTimeout(() => {
      if (literatureCollectNotice.value === message) literatureCollectNotice.value = ''
    }, 2600)
  }

  function addCollectingKey(key: string) {
    literatureCollectingKeys.value = new Set([...literatureCollectingKeys.value, key])
  }

  function removeCollectingKey(key: string) {
    literatureCollectingKeys.value = new Set([...literatureCollectingKeys.value].filter((item) => item !== key))
  }

  watch(
    evidenceItems,
    (items) => {
      const validKeys = new Set(items.map((item) => item.evidence_id))
      selectedLiterature.value = Object.fromEntries(
        Object.entries(selectedLiterature.value).filter(([key]) => validKeys.has(key)),
      )
      if (!items.length) cancelLiteratureSelection()
    },
    { deep: true },
  )

  return {
    literatureSelectionMode,
    selectedLiterature,
    selectedLiteratureCount,
    allLiteratureSelected,
    literatureCollectLoading,
    literatureCollectNotice,
    enterLiteratureSelectionMode,
    cancelLiteratureSelection,
    toggleLiteratureSelectAll,
    toggleLiteratureSelection,
    batchCollectLiterature,
    refreshLiteratureCollections,
    toggleLiteratureCollection,
    isLiteratureCollected,
    isLiteratureCollecting,
    canCollectLiterature,
    maxLiteratureSelection: MAX_LITERATURE_SELECTION,
  }
}

function literaturePaperId(item: EvidenceItem): string {
  const direct = String(item.paper_id || '').trim()
  if (direct) return direct

  const url = item.nstl_url || item.url || ''
  if (!url) return ''

  try {
    const parsed = new URL(url, window.location.origin)
    return (
      parsed.searchParams.get('id')
      || parsed.searchParams.get('paper_id')
      || parsed.searchParams.get('paperId')
      || ''
    )
  } catch {
    const match = url.match(/[?&](?:id|paper_id|paperId)=([^&#]+)/)
    return match ? decodeURIComponent(match[1]) : ''
  }
}

function readCachedCollectionIds(): string[] {
  try {
    const raw = localStorage.getItem(NSTL_COLLECTION_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Array<string | { id?: unknown }>
    if (!Array.isArray(parsed)) return []
    return uniqueIds(parsed.map((item) => (
      typeof item === 'string' ? item : String(item?.id || '')
    )))
  } catch {
    return []
  }
}

function writeCachedCollectionIds(ids: string[]) {
  localStorage.setItem(
    NSTL_COLLECTION_CACHE_KEY,
    JSON.stringify(uniqueIds(ids).map((id) => ({ id }))),
  )
}

function uniqueIds(ids: string[]) {
  return Array.from(new Set(ids.map((id) => String(id).trim()).filter(Boolean)))
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}
