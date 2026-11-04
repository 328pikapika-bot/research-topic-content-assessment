import dns from 'node:dns'
import fs from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(__dirname, '..')
const certPath = path.join(webRoot, 'certs', 'www.nstl.gov.cn.pfx')

const listenHost = process.env.NSTL_PROXY_LISTEN_HOST || '0.0.0.0'
const listenPort = Number(process.env.NSTL_PROXY_LISTEN_PORT || 443)
const targetHost = process.env.NSTL_PROXY_TARGET_HOST || 'www.nstl.gov.cn'
const targetIp = process.env.NSTL_PROXY_TARGET_IP || '127.0.0.1'
const localFrontendPort = Number(process.env.NSTL_PROXY_LOCAL_FRONTEND_PORT || process.env.CONTENT_ASSESSMENT_LOCAL_FRONTEND_PORT || 18093)
const localFrontendHost = process.env.NSTL_PROXY_LOCAL_FRONTEND_HOST || '127.0.0.1'
const localFrontendServerName = process.env.NSTL_PROXY_LOCAL_FRONTEND_SERVER_NAME || 'www.nstl.gov.cn'
const localFrontendProtocol = process.env.NSTL_PROXY_LOCAL_FRONTEND_PROTOCOL || 'http'
const agentPublicPath = normalizePath(process.env.NSTL_PROXY_AGENT_PUBLIC_PATH || '/ai/agent/content_assessment')
const agentApiPath = normalizePath(process.env.NSTL_PROXY_AGENT_API_PATH || '/api/service/nstl/ai/agent/content-assessment')
const passphrase = process.env.NSTL_PROXY_CERT_PASSPHRASE || 'content-assessment-local'

if (!fs.existsSync(certPath)) {
  console.error(`[nstl-www-proxy] Missing certificate: ${certPath}`)
  process.exit(1)
}

const server = https.createServer({
  pfx: fs.readFileSync(certPath),
  passphrase,
}, (clientReq, clientRes) => {
  const requestOrigin = getAllowedOrigin(clientReq.headers.origin)
  if (clientReq.method === 'OPTIONS') {
    writeCorsPreflight(clientReq, clientRes, requestOrigin)
    return
  }

  if (isAgentRootWithoutTrailingSlash(clientReq.url)) {
    const requestUrl = new URL(clientReq.url || '/', 'https://www.nstl.gov.cn')
    requestUrl.pathname = `${agentPublicPath}/`
    clientRes.writeHead(308, { location: `${requestUrl.pathname}${requestUrl.search}` })
    clientRes.end()
    return
  }

  if (shouldProxyToLocalFrontend(clientReq.url)) {
    proxyToLocalFrontend(clientReq, clientRes)
    return
  }

  const headers = buildUpstreamHeaders(clientReq.headers, targetHost)

  const upstreamReq = https.request({
    host: targetHost,
    servername: targetHost,
    method: clientReq.method,
    path: clientReq.url,
    headers,
    rejectUnauthorized: false,
    lookup(hostname, options, callback) {
      if (hostname === targetHost) {
        callback(null, targetIp, 4)
        return
      }
      dns.lookup(hostname, options, callback)
    },
  }, (upstreamRes) => {
    const headers = { ...upstreamRes.headers }
    delete headers['content-security-policy']
    delete headers['content-security-policy-report-only']
    delete headers['x-frame-options']
    applyCorsHeaders(headers, requestOrigin)
    clientRes.writeHead(upstreamRes.statusCode || 502, upstreamRes.statusMessage, headers)
    upstreamRes.pipe(clientRes)
  })

  upstreamReq.on('error', (error) => {
    console.error('[nstl-www-proxy] upstream error:', error.message)
    if (!clientRes.headersSent) {
      clientRes.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
    }
    clientRes.end(`NSTL proxy upstream error: ${error.message}`)
  })

  clientReq.pipe(upstreamReq)
})

function proxyToLocalFrontend(clientReq, clientRes) {
  const headers = {
    ...clientReq.headers,
    host: `${localFrontendServerName}:${localFrontendPort}`,
  }

  const requestClient = localFrontendProtocol === 'https' ? https : http
  const requestOptions = {
    host: localFrontendHost,
    port: localFrontendPort,
    method: clientReq.method,
    path: clientReq.url,
    headers,
  }

  if (localFrontendProtocol === 'https') {
    requestOptions.servername = localFrontendServerName
    requestOptions.rejectUnauthorized = false
  }

  const upstreamReq = requestClient.request(requestOptions, (upstreamRes) => {
    const headers = { ...upstreamRes.headers }
    clientRes.writeHead(upstreamRes.statusCode || 502, upstreamRes.statusMessage, headers)
    upstreamRes.pipe(clientRes)
  })

  upstreamReq.on('error', (error) => {
    console.error('[nstl-www-proxy] local frontend error:', error.message)
    if (!clientRes.headersSent) {
      clientRes.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
    }
    clientRes.end(`Local frontend proxy error: ${error.message}`)
  })

  clientReq.pipe(upstreamReq)
}

function buildUpstreamHeaders(headers, host) {
  const nextHeaders = {
    ...headers,
    host,
  }
  const origin = rewriteOrigin(headers.origin)
  const referer = rewriteReferer(headers.referer)
  if (origin) nextHeaders.origin = origin
  if (referer) nextHeaders.referer = referer
  return nextHeaders
}

function shouldProxyToLocalFrontend(value) {
  const pathname = getPathname(value)
  return (
    pathname === agentPublicPath
    || pathname.startsWith(`${agentPublicPath}/`)
    || pathname === agentApiPath
    || pathname.startsWith(`${agentApiPath}/`)
  )
}

function isAgentRootWithoutTrailingSlash(value) {
  return getPathname(value) === agentPublicPath
}

function getPathname(value) {
  try {
    return new URL(value, 'https://www.nstl.gov.cn').pathname
  } catch {
    return '/'
  }
}

function normalizePath(value) {
  const normalized = `/${String(value || '').replace(/^\/+|\/+$/g, '')}`
  return normalized === '/' ? '/ai/agent/content_assessment' : normalized
}

function writeCorsPreflight(clientReq, clientRes, requestOrigin) {
  const headers = {
    'access-control-allow-origin': requestOrigin || 'https://www.nstl.gov.cn',
    'access-control-allow-credentials': 'true',
    'access-control-allow-methods': clientReq.headers['access-control-request-method'] || 'GET,POST,OPTIONS',
    'access-control-allow-headers': clientReq.headers['access-control-request-headers'] || 'content-type,x-requested-with,cache-control,pragma',
    'access-control-max-age': '600',
    'vary': 'Origin, Access-Control-Request-Method, Access-Control-Request-Headers',
  }
  clientRes.writeHead(204, headers)
  clientRes.end()
}

function applyCorsHeaders(headers, requestOrigin) {
  if (!requestOrigin) return
  headers['access-control-allow-origin'] = requestOrigin
  headers['access-control-allow-credentials'] = 'true'
  headers['vary'] = mergeVary(headers.vary, 'Origin')
}

function getAllowedOrigin(value) {
  if (typeof value !== 'string') return ''
  if (/^https:\/\/www\.nstl\.gov\.cn$/i.test(value)) return value
  if (/^https:\/\/www\.nstl\.gov\.cn:18093$/i.test(value)) return value
  return ''
}

function mergeVary(current, next) {
  const values = String(current || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  if (!values.some((item) => item.toLowerCase() === next.toLowerCase())) values.push(next)
  return values.join(', ')
}

server.on('error', (error) => {
  console.error('[nstl-www-proxy] server error:', error.message)
  if (error.code === 'EACCES') {
    console.error('[nstl-www-proxy] Port 443 usually requires Administrator privileges on Windows.')
  }
  process.exit(1)
})

server.listen(listenPort, listenHost, () => {
  console.log(`[nstl-www-proxy] https://www.nstl.gov.cn:${listenPort} -> https://${targetHost} (${targetIp})`)
})

function rewriteOrigin(value) {
  if (typeof value !== 'string') return value
  return value.replace(/^https:\/\/www\.nstl\.gov\.cn(?::18093)?$/i, 'https://www.nstl.gov.cn')
}

function rewriteReferer(value) {
  if (typeof value !== 'string') return value
  return value.replace(/^https:\/\/www\.nstl\.gov\.cn(?::18093)?\//i, 'https://www.nstl.gov.cn/')
}
