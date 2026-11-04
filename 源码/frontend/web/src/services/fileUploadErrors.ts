export interface UploadErrorInfo {
  code: string
  label: string
  message: string
}

export class FileUploadError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message)
    this.name = 'FileUploadError'
  }
}

export function getUploadErrorInfo(error: unknown): UploadErrorInfo {
  const payload = error && typeof error === 'object' ? error as Record<string, unknown> : {}
  const detail = payload.detail && typeof payload.detail === 'object'
    ? payload.detail as Record<string, unknown> : {}
  const status = Number(payload.status || 0)
  const code = String(detail.code || payload.code || (status === 401 ? 'login_required' : error instanceof TypeError ? 'network_error' : ''))
  let message = String(detail.message || (typeof payload.detail === 'string' ? payload.detail : '') || payload.message || '')
  let label = '上传失败'
  if (code === 'sensitive_content_detected') {
    label = '敏感词拦截'
    message ||= '文件未通过敏感词检测，请检查文件名和正文后重新上传。'
  } else if (code === 'sensitive_check_failed') {
    label = '安全校验不可用'
    message ||= '文件安全校验服务暂不可用，请稍后重试或联系管理员。'
  } else if (code === 'storage_failed') {
    label = '保存失败'
    message ||= '文件存储服务暂不可用，请稍后重试。'
  } else if (code === 'login_required' || status === 401) {
    label = '需要登录'
    message ||= '请先登录平台账号后重新上传。'
  } else if (code === 'unsupported_file_type') {
    label = '格式不支持'
    if (!message) message = '暂不支持图片和 DOC，请上传 PDF、DOCX、XLSX、CSV、PPTX、TXT 或 Markdown。'
  } else if (code === 'file_count_exceeded') {
    label = '文件数量超限'
    message ||= '最多上传 10 个文件，请先移除已有材料。'
  } else if (code === 'file_too_large') {
    label = '文件过大'
    message ||= '单个文件不能超过 50MB，请压缩或拆分后重新上传。'
  } else if (code === 'empty_file') {
    label = '空文件'
    message ||= '文件内容为空，请重新选择文件上传。'
  } else if (code === 'network_error') {
    label = '连接失败'
    message = '无法连接上传服务，请检查网络或确认平台服务已启动。'
  } else if (code && code !== 'upload_failed') {
    label = '解析失败'
  }
  message ||= typeof error === 'string' && error ? error : '文件上传失败，请稍后重试。'
  return { code, label, message }
}

export async function readUploadError(response: Response): Promise<FileUploadError> {
  let payload: Record<string, unknown> = {}
  try { payload = await response.json() } catch { /* A proxy may return HTML instead of JSON. */ }
  const info = getUploadErrorInfo({ ...payload, status: response.status })
  return new FileUploadError(info.code, info.message, response.status)
}
