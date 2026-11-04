export interface GenerationRecovery {
  enabled?: boolean
  phase?: string
  stage?: string
  next_retry_at?: string | null
  category?: string
}

export function recoveryLabel(record?: GenerationRecovery | null): string {
  if (!record?.enabled) return ''
  if (record.phase === 'running') return record.category ? '正在自动修复' : ''
  if (record.phase === 'waiting_system') return '等待系统处理'
  return '等待服务恢复'
}

export function recoveryNotice(record?: GenerationRecovery | null) {
  const title = recoveryLabel(record)
  if (!title) return null
  const next = record?.next_retry_at ? new Date(record.next_retry_at) : null
  const scheduled = next && Number.isFinite(next.getTime()) ? `系统将于${next.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}自动继续。` : ''
  return {
    title,
    description: record?.phase === 'waiting_system'
      ? '已完成的分析与报告段落已保存，等待系统修复后自动继续。'
      : record?.phase === 'running'
        ? '正在继续未完成的分析与报告校核，已通过的内容将保留。'
        : '生成服务暂不可用，已有分析已保存，无需手动重试。',
    reference: scheduled,
    syncing: false,
  }
}
