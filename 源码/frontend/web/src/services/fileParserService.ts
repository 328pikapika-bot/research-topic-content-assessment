import { requirePlatformLogin } from './nstlAuth'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/service/nstl/ai/agent/content-assessment').replace(/\/$/, '')

export interface ParsedFile {
  fileId: string
  name: string
  type: string
  content: string
  size: number
  parsedAt: string
  url?: string
}

export interface ParsedFileMeta {
  fileId: string
  name: string
  type: string
  content?: string
  size: number
  parsedAt: string
  url?: string
}

export class FileParserService {
  private parsedFilesKey = 'content-assessment_parsed_files'

  async parseFile(file: File): Promise<ParsedFile> {
    const fileType = this.getFileType(file.name)
    const fileId = this.generateFileId()

    let content = ''
    if (fileType === 'pdf') {
      content = await this.parsePDF(file)
    } else if (fileType === 'txt' || fileType === 'md') {
      content = await this.parseText(file)
    } else {
      throw new Error(`不支持的文件类型: ${fileType}`)
    }

    return {
      fileId,
      name: file.name,
      type: fileType,
      content,
      size: file.size,
      parsedAt: new Date().toISOString(),
    }
  }

  private async parsePDF(file: File): Promise<string> {
    const pdfjsLib = (window as any).pdfjsLib
    if (!pdfjsLib) throw new Error('PDF.js库未加载')

    const arrayBuffer = await file.arrayBuffer()
    const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise

    let fullText = ''
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i)
      const textContent = await page.getTextContent()
      const pageText = textContent.items.map((item: any) => item.str).join(' ')
      fullText += pageText + '\n\n'
    }

    return this.cleanText(fullText)
  }

  private parseText(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const content = e.target?.result as string
        resolve(this.cleanText(content))
      }
      reader.onerror = () => reject(new Error('文件读取失败'))
      reader.readAsText(file, 'UTF-8')
    })
  }

  private cleanText(text: string): string {
    if (!text) return ''
    return text
      .replace(/​/g, '')
      .replace(/-\n/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n[ \t]+/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  getFileType(fileName: string): string {
    const ext = fileName.split('.').pop()?.toLowerCase() || ''
    if (ext === 'pdf') return 'pdf'
    if (ext === 'txt') return 'txt'
    if (ext === 'md' || ext === 'markdown') return 'md'
    return ext
  }

  private generateFileId(): string {
    return `file_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
  }

  async saveParsedFiles(parsedFiles: ParsedFileMeta[]): Promise<void> {
    const userScope = await requirePlatformLogin()
    const response = await fetch(`${API_BASE}/v1/parsed-files/batch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-User-Scope': userScope,
      },
      body: JSON.stringify({
        files: parsedFiles.map((f) => ({
          fileId: f.fileId,
          name: f.name,
          type: f.type,
          content: f.content || '',
          size: f.size,
          parsedAt: f.parsedAt,
          url: f.url,
        })),
      }),
    })

    if (!response.ok) {
      const detail = await response.text()
      throw new Error(parseParsedFileError(detail) || '后端存储服务暂时不可用，请稍后重试或联系管理员。')
    }

    const result = await response.json()
    console.log(`[OK] 文件已保存到后端: ${result.saved}/${result.total}`)
    try {
      const metadataOnly = parsedFiles.map((f) => ({
        fileId: f.fileId,
        name: f.name,
        type: f.type,
        size: f.size,
        parsedAt: f.parsedAt,
        content: '',
      }))
      localStorage.setItem(this.parsedFilesKey, JSON.stringify(metadataOnly))
    } catch { /* ignore */ }
  }

  async getParsedFiles(): Promise<ParsedFile[]> {
    try {
      const userScope = await requirePlatformLogin()
      const response = await fetch(`${API_BASE}/v1/parsed-files?include_content=true`, {
        headers: { 'X-User-Scope': userScope },
      })
      if (response.ok) {
        const result = await response.json()
        return result.data.map((f: any) => ({
          fileId: f.file_id,
          name: f.name,
          type: f.type,
          content: f.content,
          size: f.size,
          parsedAt: f.parsed_at,
          url: f.url,
        }))
      }
    } catch (err) {
      console.warn('后端加载失败:', err)
    }

    try {
      const data = localStorage.getItem(this.parsedFilesKey)
      return data ? JSON.parse(data) : []
    } catch {
      return []
    }
  }

  async clearParsedFiles(): Promise<void> {
    try {
      const userScope = await requirePlatformLogin()
      await fetch(`${API_BASE}/v1/parsed-files`, {
        method: 'DELETE',
        headers: { 'X-User-Scope': userScope },
      })
    } catch { /* ignore */ }
    localStorage.removeItem(this.parsedFilesKey)
  }

  async deleteParsedFile(fileId: string): Promise<void> {
    if (!fileId) return
    try {
      const userScope = await requirePlatformLogin()
      await fetch(`${API_BASE}/v1/parsed-files/${encodeURIComponent(fileId)}`, {
        method: 'DELETE',
        headers: { 'X-User-Scope': userScope },
      })
    } catch { /* ignore */ }
  }
}

function parseParsedFileError(detail: string): string {
  if (!detail) return ''
  try {
    const parsed = JSON.parse(detail) as { detail?: unknown; message?: unknown }
    const message = parsed.detail ?? parsed.message
    if (typeof message === 'string') return message
    if (message && typeof message === 'object') {
      return String((message as any).message || (message as any).msg || '')
    }
    return ''
  } catch {
    return detail
  }
}
