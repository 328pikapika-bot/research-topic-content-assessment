import { requirePlatformLogin } from './nstlAuth'
import { readUploadError } from './fileUploadErrors'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/service/nstl/ai/agent/content-assessment').replace(/\/$/, '')

export interface FileUploadResult {
  file: File
  url: string | null
  fileId: string | null
}

export async function uploadFile(file: File): Promise<FileUploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  const userScope = await requirePlatformLogin()

  const response = await fetch(`${API_BASE}/v1/files/upload`, {
    method: 'POST',
    headers: {
      'X-User-Scope': userScope,
    },
    body: formData,
  })

  if (!response.ok) {
    throw await readUploadError(response)
  }

  const result = await response.json()
  const data = result.data || result
  return {
    file,
    url: data.url || data.fileUrl || null,
    fileId: data.file_id || data.fileId || data.id || null,
  }
}

export interface CreateRunPayload {
  topic: string
  structured: {
    research_type: string
    report_length: string
    year_range: [number, number]
    language_pref: string
    uploaded_files: Array<{
      file_id: string
      name: string
      url: string | null
      type: string
      size: number
    }>
  }
}

export async function createRun(threadId: string, payload: CreateRunPayload): Promise<Record<string, unknown>> {
  const userScope = await requirePlatformLogin()
  const body = {
    input: [
      {
        type: 'message',
        role: 'user',
        content: [
          { type: 'input_text', text: payload.topic },
          { type: 'input_json', json: payload.structured },
        ],
      },
    ],
  }

  const resp = await fetch(`${API_BASE}/v1/threads/${threadId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Scope': userScope,
    },
    body: JSON.stringify(body),
  })

  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}
