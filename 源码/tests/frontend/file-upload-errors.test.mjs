import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from '../../frontend/web/node_modules/typescript/lib/typescript.js'

const source = await readFile(new URL('../../frontend/web/src/services/fileUploadErrors.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
const { FileUploadError, getUploadErrorInfo, readUploadError } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('distinguishes safety, parsing, storage and login failures', () => {
  for (const [code, label] of [
    ['sensitive_content_detected', '敏感词拦截'],
    ['sensitive_check_failed', '安全校验不可用'],
    ['docx_parse_failed', '解析失败'],
    ['storage_failed', '保存失败'],
    ['login_required', '需要登录'],
  ]) {
    const info = getUploadErrorInfo({ detail: { code, message: 'specific reason' } })
    assert.equal(info.label, label)
    assert.equal(info.message, 'specific reason')
  }
})

test('typed upload error retains server code and reason', async () => {
  const response = new Response(JSON.stringify({ detail: { code: 'sensitive_check_failed', message: '服务未配置' } }), { status: 503 })
  const error = await readUploadError(response)
  assert.ok(error instanceof Error)
  assert.ok(error instanceof FileUploadError)
  assert.equal(error.status, 503)
  assert.equal(getUploadErrorInfo(error).label, '安全校验不可用')
  assert.equal(error.message, '服务未配置')
})

test('handles login, non-JSON proxy and network errors', async () => {
  const login = await readUploadError(new Response(JSON.stringify({ detail: '请先登录平台账号' }), { status: 401 }))
  assert.equal(login.code, 'login_required')
  const proxy = await readUploadError(new Response('<html>Bad Gateway</html>', { status: 502 }))
  assert.equal(getUploadErrorInfo(proxy).label, '上传失败')
  assert.ok(!proxy.message.includes('<html>'))
  const network = getUploadErrorInfo(new TypeError('Failed to fetch'))
  assert.equal(network.label, '连接失败')
  assert.equal(getUploadErrorInfo({ code: network.code }).label, '连接失败')
})
