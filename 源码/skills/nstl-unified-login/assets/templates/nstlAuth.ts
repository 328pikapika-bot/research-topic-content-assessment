export interface NstlUserInfo {
  userId: string
  username: string
  email?: string
}

interface StoredSession {
  user: NstlUserInfo
  session: {
    loginTime: number
    expiresAt: number
  }
}

interface AuthConfig {
  loginServiceUrl: string
  userInfoEndpoint: string
  sessionTimeout: number
  storageKey: string
  trustedOrigins: string[]
  loginPollInterval: number
  loginPollTimeout: number
  userInfoRequestTimeout: number
}

interface WxLoginResponse {
  code?: string | number | null
  msg?: string
  [key: string]: unknown
}

const DEFAULT_AUTH_CONFIG: AuthConfig = {
  loginServiceUrl: 'https://login.nstl.gov.cn',
  userInfoEndpoint: '/api/service/nstl/web/vilidate/isLogin',
  sessionTimeout: 24 * 60 * 60 * 1000,
  storageKey: 'nstl_user_session',
  trustedOrigins: ['https://login.nstl.gov.cn', 'https://www.nstl.gov.cn'],
  loginPollInterval: 2000,
  loginPollTimeout: 5 * 60 * 1000,
  userInfoRequestTimeout: 5000,
}

class AuthManager {
  private config: AuthConfig
  private modal: HTMLDivElement | null = null
  private iframe: HTMLIFrameElement | null = null
  private pollTimer = 0
  private pollStartedAt = 0
  private pollInFlight = false
  private readonly boundWxRegist = this.handleWxRegist.bind(this)
  private readonly boundInitWeichatLogin = this.handleInitWeichatLogin.bind(this)

  constructor(config: Partial<AuthConfig> = {}) {
    this.config = { ...DEFAULT_AUTH_CONFIG, ...config }
    this.installNstlParentCallbacks()
  }

  async checkAuth(): Promise<boolean> {
    const session = this.getSession()
    if (session && !this.isSessionExpired(session) && isRealUserId(session.user.userId)) {
      const user = await this.fetchUserInfo()
      if (user && isRealUserId(user.userId)) {
        this.createSession(user)
        return true
      }
      this.clearSession()
      window.dispatchEvent(new CustomEvent('authLogout'))
      return false
    }

    const user = await this.fetchUserInfo()
    if (user && isRealUserId(user.userId)) {
      this.createSession(user)
      return true
    }
    return false
  }

  async fetchUserInfo(): Promise<NstlUserInfo | null> {
    const endpoint = this.config.userInfoEndpoint
    const separator = endpoint.includes('?') ? '&' : '?'
    const url = `${endpoint}${separator}t=${Date.now()}`
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), this.config.userInfoRequestTimeout)

    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Cache-Control': 'no-cache',
          Pragma: 'no-cache',
        },
        signal: controller.signal,
      })

      if (!response.ok) return null
      const result = await response.json()
      return normalizeNstlUserInfoResult(result)
    } catch {
      return null
    } finally {
      window.clearTimeout(timeout)
    }
  }

  showLoginModal() {
    if (this.modal) return

    const overlay = document.createElement('div')
    overlay.style.cssText = `
      position: fixed;
      inset: 0;
      z-index: 10000;
      background: rgba(0, 0, 0, 0.42);
    `

    const iframe = document.createElement('iframe')
    iframe.src = this.config.loginServiceUrl
    iframe.style.cssText = `
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      border: 0;
      background: #fff;
    `

    overlay.appendChild(iframe)
    document.body.appendChild(overlay)
    document.body.style.overflow = 'hidden'
    this.modal = overlay
    this.iframe = iframe
    this.startLoginPolling()
  }

  hideLoginModal() {
    this.stopLoginPolling()
    this.modal?.remove()
    this.modal = null
    this.iframe = null
    document.body.style.overflow = ''
  }

  getUserInfo(): NstlUserInfo | null {
    return this.getSession()?.user ?? null
  }

  isAuthenticated(): boolean {
    const session = this.getSession()
    return Boolean(session && !this.isSessionExpired(session) && isRealUserId(session.user.userId))
  }

  async requireUserScope(): Promise<string> {
    const authed = await this.checkAuth()
    if (!authed) {
      this.showLoginModal()
      throw new Error('请先登录 NSTL 账号')
    }

    const userId = normalizeRealUserId(this.getUserInfo()?.userId)
    if (!userId) {
      this.clearSession()
      this.showLoginModal()
      throw new Error('未获取到真实 NSTL 用户')
    }
    return userId
  }

  logout() {
    this.clearSession()
    this.hideLoginModal()
    window.dispatchEvent(new CustomEvent('authLogout'))
  }

  destroy() {
    this.stopLoginPolling()
    this.removeNstlParentCallbacks()
    this.hideLoginModal()
  }

  private startLoginPolling() {
    this.stopLoginPolling()
    this.pollStartedAt = Date.now()
    this.pollTimer = window.setInterval(() => {
      void this.pollLoginStatus()
    }, this.config.loginPollInterval)
    void this.pollLoginStatus()
  }

  private stopLoginPolling() {
    if (this.pollTimer) {
      window.clearInterval(this.pollTimer)
      this.pollTimer = 0
    }
    this.pollInFlight = false
  }

  private async pollLoginStatus() {
    if (this.pollInFlight || !this.modal) return
    if (Date.now() - this.pollStartedAt > this.config.loginPollTimeout) {
      this.stopLoginPolling()
      return
    }

    this.pollInFlight = true
    try {
      const user = await this.fetchUserInfo()
      if (user && isRealUserId(user.userId)) {
        this.completeLogin(user)
      }
    } finally {
      this.pollInFlight = false
    }
  }

  private completeLogin(user: NstlUserInfo) {
    this.createSession(user)
    this.hideLoginModal()
    window.dispatchEvent(new CustomEvent('authSuccess', { detail: { userInfo: user } }))
  }

  private installNstlParentCallbacks() {
    window.wxRegist = this.boundWxRegist
    window.WxRegist = this.boundWxRegist
    window.initWeichatLogin = this.boundInitWeichatLogin
  }

  private removeNstlParentCallbacks() {
    if (window.wxRegist === this.boundWxRegist) delete window.wxRegist
    if (window.WxRegist === this.boundWxRegist) delete window.WxRegist
    if (window.initWeichatLogin === this.boundInitWeichatLogin) delete window.initWeichatLogin
  }

  private handleInitWeichatLogin() {
    if (!this.iframe) return
    this.iframe.src = this.config.loginServiceUrl
    this.startLoginPolling()
  }

  private handleWxRegist(response: WxLoginResponse) {
    const code = response?.code == null ? '' : String(response.code)

    if (code === '1' || code === '') {
      alert('微信账号未绑定 NSTL 账号，请使用短信登录或先完成绑定')
      this.handleInitWeichatLogin()
      return
    }

    if (code === '2') {
      alert(response.msg || '微信登录失败，请重新扫码')
      this.handleInitWeichatLogin()
      return
    }

    void this.pollLoginStatus()
  }

  private createSession(user: NstlUserInfo) {
    const now = Date.now()
    const session: StoredSession = {
      user,
      session: {
        loginTime: now,
        expiresAt: now + this.config.sessionTimeout,
      },
    }
    localStorage.setItem(this.config.storageKey, JSON.stringify(session))
  }

  private getSession(): StoredSession | null {
    try {
      const raw = localStorage.getItem(this.config.storageKey)
      return raw ? JSON.parse(raw) as StoredSession : null
    } catch {
      return null
    }
  }

  private clearSession() {
    localStorage.removeItem(this.config.storageKey)
  }

  private isSessionExpired(session: StoredSession) {
    return Date.now() > session.session.expiresAt
  }
}

let authManager: AuthManager | null = null

export function createAuthManager(config: Partial<AuthConfig> = {}) {
  if (!authManager) {
    authManager = new AuthManager(config)
    window.authManager = authManager
  }
  return authManager
}

export function getAuthManager() {
  return authManager ?? createAuthManager()
}

export function getCurrentUser() {
  return getAuthManager().getUserInfo()
}

export function getUserScope() {
  const userId = normalizeRealUserId(getCurrentUser()?.userId)
  if (!userId) {
    throw new Error('未获取到真实 NSTL 用户')
  }
  return userId
}

/**
 * 强制鉴权 —— 用户相关操作前必须调用
 * 未登录或非真实用户时抛出异常并触发登录弹窗
 */
export async function requireUserScope(): Promise<string> {
  return getAuthManager().requireUserScope()
}

function isRealUserId(userId: string | undefined | null): userId is string {
  return Boolean(normalizeRealUserId(userId))
}

function normalizeRealUserId(userId: string | undefined | null) {
  if (!userId) return ''
  const normalized = userId.trim()
  const lower = normalized.toLowerCase()
  const blocked = new Set(['', 'default', 'unknown', 'mock_user_id', 'test_user', 'test_account'])
  if (blocked.has(lower)) return ''
  if (
    lower.startsWith('nstl_user_')
    || lower.startsWith('mock_')
    || lower.startsWith('fake_')
    || lower.startsWith('test_')
  ) {
    return ''
  }
  return normalized
}

function normalizeNstlUserInfoResult(result: unknown): NstlUserInfo | null {
  if (!result || typeof result !== 'object') return null

  const response = result as { code?: unknown; data?: unknown; user?: unknown; userInfo?: unknown }
  if (response.code !== 0) return null

  const data = pickObject(response.data)
  const nestedUser = pickObject(data?.userInfo)
    ?? pickObject(data?.user)
    ?? pickObject(response.userInfo)
    ?? pickObject(response.user)
  const payload = nestedUser ?? data
  if (!payload) return null

  const userId = firstStringValue(payload, ['userId', 'user_id', 'id', 'uid', 'loginId', 'account'])
  if (!userId) return null

  const username = firstStringValue(payload, ['userName', 'username', 'name', 'realName', 'nickName', 'displayName']) || userId
  const email = firstStringValue(payload, ['email', 'mail']) || ''

  return { userId, username, email }
}

function pickObject(value: unknown) {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function firstStringValue(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  }
  return ''
}

declare global {
  interface Window {
    authManager?: AuthManager
    wxRegist?: (response: WxLoginResponse) => void
    WxRegist?: (response: WxLoginResponse) => void
    initWeichatLogin?: () => void
  }
}
