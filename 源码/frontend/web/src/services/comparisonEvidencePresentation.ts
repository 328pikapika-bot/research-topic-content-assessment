import type { Claim, ClaimEvidenceLink, EvidenceRecord } from './contentAssessmentApi'

type EvidenceChunk = Record<string, unknown>

const comparisonFields = [
  ['object', '对象'], ['method', '方法'], ['data', '数据'], ['baseline', '基线'],
  ['metric', '指标'], ['time', '时间'], ['validation', '验证条件'],
] as const

const coverageLabels: Record<string, string> = {
  covered: '已对应', partial: '部分对应',
  conflict: '存在差异', not_comparable: '口径不可比',
}

export function comparisonFieldSummary(link: ClaimEvidenceLink): string {
  const assessed = comparisonFields.filter(([key]) => Boolean(coverageLabels[link.field_matrix?.[key] || '']))
  if (!assessed.length) return ''
  return `已核对 ${assessed.map(([, label]) => label).join('、')} ${assessed.length} 项；其他项目不计为已核对。`
}

function specificReasons(values: string[]): string[] {
  return [...new Set(values.flatMap(value => comparisonReaderText(value).split(/[；;\n]+/)).map(value => value.trim()).filter(Boolean))]
}

/** Remove known audit output, never broad matches against scientific limitations. */
export function comparisonReaderText(value: unknown): string {
  return String(value || '').replace(/证据\s*E-[A-Z]+-[\w-]+/g, '该研究')
    .split(/\r?\n[\t ]*(?:\r?\n)*/).map(paragraph => paragraph.split(/(?<=[。！？；;])/).map(part => part
      .replace(/^\s*证据等级\s*[A-DＡ-Ｄ]\s*且为(?:摘要|题录|部分文本)[，,]/, '')
      .replace(/[，,](?:无法|不能)直接(?:支持|证实)主张[^。！？；;]*/g, '')
    ).filter(part => {
    const text = part.trim().replace(/[。！？；;]+$/, '')
    return text && ![
      /^字段覆盖\s*\d+\s*\/\s*\d+$/, /^关键词交集\s*\d+\s*项$/,
      /^(?:缺少|缺失|未提取到?|未识别到?)(?:对象|研究对象|方法|数据|基线|指标|时间|验证条件)字段$/,
      /^(?:对象|方法|数据|基线|指标|时间|验证条件)字段不一致$/,
      /^缺少可重新打开的全文字符锚点$/, /^已按字段矩阵完成同口径比对$/,
      /^缺失字段不能替代统一验证条件$/,
      /^摘要或题录支持方向性判断，但不能替代全文实验数据和统一验证条件$/,
      /^(?:[A-DＡ-Ｄ]级)?(?:摘要|题录|部分文本|摘要或部分文本).*(?:方向性判断|全文核验|证据等级)/,
      /^(?:该)?文献为(?:摘要|题录|部分文本)依据[，,].*(?:无法确认|未能确认|全文核验)/,
      /^(?:证据|文献)(?:为|仅为)(?:综述性摘要|摘要|题录|部分文本)[，,].*(?:未提供|无法|不能|方向性支持)/,
      /^(?:仅|只)(?:提供|形成)(?:方向性|部分)支持$/,
      /^没有经过全文锚点和字段级可比性校验的证据，不能独立判断先进性$/,
      /^先进性判断不从创新性标签或关键词相似度推导$/,
      /^(?:(?:当前|现有|本次|相关)?证据|(?:该)?文献摘要|摘要|题录|全文)(?:中)?(?:尚未|未|没有)(?:覆盖|涉及|提及|提供|报告)/,
      /^(?:该)?文献(?:尚未|未)(?:涉及|提及).*(?:主张|方法|指标|证据|验证)/,
      /^(?:主张|方法|支持关系|矛盾关系|支持或矛盾|是否支持|是否矛盾|支持程度)[：:]?(?:尚|暂)?(?:无法|不能|难以)判断$/,
      /^(?:无法|不能|尚不能|尚无法)判断(?:主张|方法|支持|矛盾|是否支持|是否矛盾|支持或矛盾).*$/,
      /^(?:支持|矛盾)(?:或|与|、|\/)(?:支持|矛盾)(?:关系)?[：:]?(?:尚|暂)?(?:无法|不能)判断$/,
      /^(?:暂无|无|未提供|尚未提供|未提取到)(?:共同点|差异|比较结果|适用条件)?$/,
    ].some(pattern => pattern.test(text))
  }).join('').trim()).filter(Boolean).join('\n\n')
}

interface ReaderComparisonParagraph {
  text: string
  material_quote?: string
  literature_quote?: string
  material_quotes?: string[]
  literature_quotes?: string[]
}

export interface ReaderComparison {
  claim_id: string
  evidence_id: string
  similarity?: ReaderComparisonParagraph[]
  difference?: ReaderComparisonParagraph[]
  conditions?: ReaderComparisonParagraph[]
}

// Audit availability is not a research finding. Do not strip individual negative
// words or rewrite an uncertain saved sentence as a positive conclusion.
const diagnosticComparison = /字段(?:覆盖|缺失)|关键词交集|全文(?:字符)?锚点|证据等级|[ABCD]级摘要|(?:摘要|题录|证据)[^。！？]{0,45}(?:无法确认|不能确认|未涉及|未提及|未覆盖|不能支撑|无法支持)|(?:无法|不能|尚不能)(?:确认|判断)(?:全文|证据|主张|方法|支持|矛盾|目标|比较)|(?:无法|不能|尚不能)(?:直接)?(?:支撑|支持|比较)(?:该|本|当前|主张|目标|全文|性能高低)|(?:文献|摘要|证据|来源)[^。！？]{0,24}(?:未涉及|未提及|未提供)|(?:但|也)未(?:涉及|比较)/

export function readerComparisonFor(state: Record<string, any>, link: ClaimEvidenceLink): ReaderComparison | undefined {
  // A published snapshot never borrows mutable runtime prose.
  const records = state.report_snapshot ? state.report_snapshot.audit_data?.reader_comparisons
    : state.output_json?.reader_comparisons
  const record = records?.[link.claim_id]?.[link.evidence_id]
  return record?.claim_id === link.claim_id && record?.evidence_id === link.evidence_id ? record : undefined
}

export function comparisonResearchAnalysis(link: ClaimEvidenceLink & { similarity?: string }, reader?: ReaderComparison) {
  const seen = new Set<string>()
  const unique = (values: unknown[]) => values.flatMap(value => String(value || '').split(/\r?\n[\t ]*(?:\r?\n)*/))
    .map(paragraph => paragraph.split(/(?<=[。！？；;])/)
    .filter(part => !diagnosticComparison.test(part)).map(comparisonReaderText)
    .filter(part => !/^(?:双方|两者|二者|材料与文献)?(?:的)?研究(?:对象|问题|方法|条件)(?:不同|有差异)[。；;]?\s*$/.test(part.trim()))
    .filter(part => {
      const key = part.replace(/[\s。！？；;]+/g, '')
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    }).reduce((text, part) => text + (text && !/[。！？；;]$/.test(text) ? '；' : '') + part, '').trim().replace(/[；;]$/, ''))
    .filter(Boolean).join('\n\n')
  const validReader = reader?.claim_id === link.claim_id && reader.evidence_id === link.evidence_id ? reader : undefined
  const readerTexts = (key: 'similarity' | 'difference' | 'conditions') => (validReader?.[key] || [])
    .filter(row => (row.material_quote?.trim() || row.material_quotes?.some(quote => quote.trim()))
      && (row.literature_quote?.trim() || row.literature_quotes?.some(quote => quote.trim()))).map(row => row.text)
  const similarity = unique(validReader ? readerTexts('similarity') : [link.similarity])
  const difference = unique(validReader ? readerTexts('difference') : [link.difference])
  // Neither uncertainty nor incomparability_reasons has the semantics of a
  // two-sided comparison condition. Legacy sources remain available verbatim.
  const conditions = unique(validReader ? readerTexts('conditions') : [])
  return { similarity, difference, conditions }
}

export function comparisonEvidenceDetail(
  link: ClaimEvidenceLink,
  records: EvidenceRecord[],
  chunks: EvidenceChunk[],
  claim?: Claim,
) {
  const record = records.find(item => item.evidence_id === link.evidence_id)
  const allowedChunkIds = link.evidence_chunk_ids || []
  const chunk = chunks.find(item => item.evidence_id === link.evidence_id
    && (!allowedChunkIds.length || allowedChunkIds.includes(String(item.chunk_id || '')))
    && (!Array.isArray(item.claim_ids) || !item.claim_ids.length || item.claim_ids.includes(link.claim_id)))
  const rawLocation = chunk?.locator
  const locator = typeof rawLocation === 'string' ? rawLocation
    : rawLocation && typeof rawLocation === 'object' ? String((rawLocation as Record<string, unknown>).display || '') : ''
  const section = String(chunk?.section_path || chunk?.section_ref || '').trim()
  const location = locator || String(chunk?.page_ref || chunk?.paragraph_ref || record?.page_ref || record?.paragraph_ref || '').trim()
    || (section && section !== record?.query_group ? section : '')
  const verifiedFulltext = Boolean(record?.fulltext_available && record.anchor_verified && chunk?.reopenable === true)
  const fulltextExcerpt = String(chunk?.text || chunk?.excerpt || '').trim()
  const abstract = String(record?.abstract || '').trim()
  const savedExcerpt = String(record?.excerpt || '').trim()
  const excerpt = verifiedFulltext && fulltextExcerpt ? fulltextExcerpt : abstract || savedExcerpt || fulltextExcerpt
  const sourceKind = verifiedFulltext && fulltextExcerpt ? '正文片段'
    : abstract ? '文献摘要' : savedExcerpt || fulltextExcerpt ? '文献片段' : '文献信息'
  const matrix = link.field_matrix || {}
  const fields = comparisonFields.filter(([key]) => Boolean(coverageLabels[matrix[key] || ''])).map(([key, label]) => ({
    key, label, status: coverageLabels[matrix[key]],
    claimValue: claim?.claim_fields?.[key] || '',
    evidenceValue: record?.comparison_fields?.[key] || '',
  })).filter(field => field.claimValue && field.evidenceValue)
  const difference = specificReasons(String(link.difference || '').split('；')).join('；')
  return {
    title: record?.title || '该历史记录未保留关联文献题名',
    authors: record?.authors?.length ? record.authors.slice(0, 4).join('、') : '',
    year: record?.year ? String(record.year) : '',
    sourceKind,
    location: verifiedFulltext ? location : '',
    excerpt: excerpt.slice(0, 900),
    fields,
    differences: specificReasons(link.incomparability_reasons || []),
    difference,
    uncertainty: comparisonReaderText(link.uncertainty),
    hasRecord: Boolean(record),
  }
}
