export class ApiError extends Error {
  constructor(public status: number, public detail: any, message: string) { super(message); this.name = 'ApiError' }
}
export async function readApiError(response: Response): Promise<ApiError> {
  const text = await response.text()
  let detail: any = text
  try { const body = JSON.parse(text); detail = body.detail || body.error || body } catch {}
  let message = typeof detail === 'string' ? detail : detail.message || ''
  if (Array.isArray(detail)) message = detail.map(item => String(item.msg || '')).join('；')
  if (response.status === 429 && typeof detail === 'object' && detail.running != null) {
    const agent = detail.agent === 'topic' ? '智能选题' : '内容评估'
    message = `${agent}当前执行 ${detail.running} 个、排队 ${detail.queued || 0} 个（用户上限 ${detail.limit || 5} 个）。${message}`
  }
  return new ApiError(response.status, detail, message || `请求失败：${response.status}`)
}
