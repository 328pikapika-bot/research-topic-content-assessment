import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const localNstlHost = 'www.nstl.gov.cn'
const agentPublicBase = '/ai/agent/content_assessment/'
const agentApiPrefix = '/api/service/nstl/ai/agent/content-assessment'

function normalizePublicBase(value = agentPublicBase) {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '/') return '/'
  if (trimmed === './') return './'
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}/`
}

function stripAgentApiPrefix(value: string, prefix: string) {
  return value.replace(new RegExp(`^${prefix}`), '') || '/'
}

export default defineConfig(({ mode }) => {
  const useNstlLocalHost = mode === 'nstl-local'
  const backendTarget = process.env.VITE_PROXY_BACKEND || 'http://127.0.0.1:17641'
  // The local HTTPS gateway terminates TLS on 443. Keep Vite on loopback HTTP
  // so its own upstream requests cannot follow the www.nstl.gov.cn hosts entry.
  const nstlWebTarget = process.env.VITE_NSTL_WEB_TARGET || 'https://127.0.0.1'
  const publicBase = normalizePublicBase(process.env.VITE_PUBLIC_BASE)

  return {
    plugins: [vue()],
    base: publicBase,
    optimizeDeps: { exclude: ['pdfjs-dist/build/pdf.worker.min.mjs?url'] },
    build: {
      sourcemap: false,
      rollupOptions: {
        output: {
          assetFileNames: asset => asset.name?.endsWith('pdf.worker.min.mjs')
            ? 'assets/pdf.worker.min-[hash].js'
            : 'assets/[name]-[hash][extname]',
        },
      },
    },
    server: {
      host: '0.0.0.0',
      port: 18093,
      strictPort: true,
    allowedHosts: useNstlLocalHost ? [localNstlHost] : ['www.nstl.gov.cn', 'localhost', '127.0.0.1', '127.0.0.1'],
      hmr: useNstlLocalHost ? false : undefined,
      proxy: {
        [`${agentApiPrefix}/`]: {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (value) => stripAgentApiPrefix(value, agentApiPrefix),
        },
        '/api/service/nstl/web/': {
          target: nstlWebTarget,
          changeOrigin: true,
          secure: false,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              proxyReq.setHeader('Host', 'www.nstl.gov.cn')
            })
          },
        },
        '/api/service/nstl/ai/': {
          target: 'https://ai.nstl.gov.cn',
          changeOrigin: true,
          secure: false,
        },
        '/v1': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/system': {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
