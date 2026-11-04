// Local product entry using the updated backend; authentication remains unchanged.
import base from '../../frontend/web/vite.config.ts'

export default (environment) => {
  const config = base(environment)
  const proxy = { ...config.server.proxy }
  for (const key of ['/api/service/nstl/ai/agent/content-assessment/', '/v1', '/system']) {
    proxy[key] = { ...proxy[key], target: process.env.VITE_PROXY_BACKEND || 'http://127.0.0.1:17641' }
  }
  return { ...config, server: { ...config.server, host: '127.0.0.1', port: 18118, proxy } }
}
