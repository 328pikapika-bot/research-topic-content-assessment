export interface GenerationRecovery {
  enabled?: boolean
  phase?: string
  stage?: string
  next_retry_at?: string | null
  category?: string
}

export function recoveryLabel(record?: GenerationRecovery | null, status?: string): string {
  if (status && !['queued', 'running', 'processing', 'model_unavailable'].includes(status)) return ''
  if (!record?.enabled) return ''
  if (record.phase === 'running') return record.category ? '正在自动修复' : ''
  if (record.phase === 'waiting_system') return record.category === 'content' ? '内容校核待修复' : '执行异常待恢复'
  return '等待服务恢复'
}

export function recoveryNotice(record?: GenerationRecovery | null, status?: string) {
  const title = recoveryLabel(record, status)
  if (!title) return null
  const next = record?.next_retry_at ? new Date(record.next_retry_at) : null
  const scheduled = next && Number.isFinite(next.getTime()) ? `系统将于${next.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit' })}自动继续（北京时间）。` : ''
  const stage = ({ crosslink_gap: '研究机会分析', report_publish: '选题报告生成', candidate_generation: '候选选题生成', quality_gate: '报告质量检查' } as Record<string, string>)[record?.stage || ''] || '当前分析'
  const check = record?.stage === 'crosslink_gap' ? '方法与引用原文的对应关系' : record?.stage === 'report_publish' ? '报告正文或文献引用' : '内容或来源'
  return {
    title,
    description: record?.phase === 'waiting_system'
      ? record.category === 'content'
        ? `${stage}已暂停：${check}尚未通过校核，已完成的结果已保存。可在下方补充材料或说明后继续，也可点击“重新校核并继续”。`
        : `${stage}遇到执行故障，当前已暂停，已完成的分析已保存。可点击“重新校核并继续”，或补充说明后继续。`
      : record?.phase === 'running'
        ? '正在继续未完成的分析与报告校核，已通过的内容将保留。'
        : record?.category === 'checkpoint' ? '本轮执行已保存最新进度，系统将从未完成阶段继续。' : '生成服务暂不可用，已有分析已保存，无需手动重试。',
    reference: scheduled,
    syncing: false,
  }
}
