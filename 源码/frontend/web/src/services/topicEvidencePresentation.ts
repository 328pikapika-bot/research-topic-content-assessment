// Display adaptation only: stored evidence and recommendation levels stay intact.
export function topicReaderText(value: unknown): string {
  return String(value ?? '')
    .replace(/(?:[，；、·]\s*)?全文覆盖\s*[：:]?\s*\d+\s*篇(?:\s*(?:[（(]\s*\d+\s*段[）)]|[、，·]?\s*\d+\s*段))?/g, '')
    .replace(/摘要\s*\d+\s*篇?\s*[、，·]\s*全文\s*\d+\s*篇?/g, '检索样本')
    .replace(/(?:[，；、·]\s*)?全文\s*\d+\s*(?:篇|段)(?:\s*[（(]\s*\d+\s*段[）)])?/g, '')
    .replace(/待补证据的探索方向|待补证据方向|待补证据/g, '探索方向')
    .replace(/全文不可用/g, '当前依据有限')
    .replace(/返回\s*\d+\s*(?:个)?候选(?:片段|分块)?[^。\n]*?最终采用\s*\d+\s*段/g, '已完成相关文献依据筛选')
    .replace(/[，；、]\s*([。；])/g, '$1')
    .replace(/^[：:，；、。\s]+$/, '')
    .trim()
}
