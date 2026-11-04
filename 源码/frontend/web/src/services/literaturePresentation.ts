export interface LiteratureEvidence {
  authors?: string[]
  evidence_id?: string; title?: string; year?: number | null; venue?: string
  abstract?: string; excerpt?: string; discipline_tags?: string[]
  fulltext_indexed?: boolean; has_fulltext?: boolean
  nstl_url?: string; source_url?: string; url?: string; doi?: string; paper_id?: string
}
export interface LiteratureChunk {
  chunk_id?: string; evidence_id?: string; text?: string; excerpt?: string
  section_path?: string; section_ref?: string; page_ref?: string
  locator?: unknown; char_start?: number | null; char_end?: number | null; reopenable?: boolean
}
export function bibliographyText(value?: string): string {
  return String(value || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').replace(/<[^>]*>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").trim()
}
export function chunkLocation(chunk: LiteratureChunk): string {
  const direct = chunk.section_path || chunk.section_ref || chunk.page_ref
  if (direct?.trim()) return direct.trim()
  if (typeof chunk.locator === 'string' && chunk.locator.trim()) return chunk.locator.trim()
  if (chunk.locator && typeof chunk.locator === 'object') {
    const locator = chunk.locator as Record<string, unknown>
    const parts = [['page', '页'], ['section', '章节'], ['paragraph', '段落']]
      .flatMap(([key, label]) => typeof locator[key] === 'string' || typeof locator[key] === 'number' ? [`${label} ${locator[key]}`] : [])
    if (parts.length) return parts.join(' · ')
  }
  return Number.isInteger(chunk.char_start) && Number.isInteger(chunk.char_end) && chunk.char_end! > chunk.char_start!
    ? `正文字符 ${chunk.char_start}–${chunk.char_end}` : ''
}
export function readableLiteratureChunks(item: LiteratureEvidence, chunks: LiteratureChunk[]): LiteratureChunk[] {
  return chunks.filter(chunk => !!item.evidence_id && chunk.evidence_id === item.evidence_id
    && !!(chunk.text || chunk.excerpt)?.trim() && chunk.reopenable !== false && !!chunkLocation(chunk))
}
export function literatureEvidenceStatus(item: LiteratureEvidence, chunks: LiteratureChunk[]): string {
  return readableLiteratureChunks(item, chunks).length ? '可定位全文片段'
    : (item.abstract || item.excerpt)?.trim() ? '摘要' : '题录'
}
