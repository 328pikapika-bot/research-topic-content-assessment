/**
 * Platform demo authentication.
 *
 * The compatibility filename is kept because older UI modules import it.
 * No NSTL login, iframe, token, or redirect is used by this implementation.
 */
export interface PlatformUser {
  userId: string
  username: string
}

export type NstlUserInfo = PlatformUser

const USERNAME = 'admin'
const PASSWORD = (import.meta.env.VITE_NSTL_DEMO_PASSWORD || "")
const USER_SCOPE = 'platform_admin'
const SESSION_KEY = 'science_literature_platform_session'

export interface PlatformSession {
  user: PlatformUser
  loggedAt: number
}

class PlatformAuthManager {
  private modal: HTMLDivElement | null = null

  private readSession(): PlatformSession | null {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as PlatformSession
      return parsed?.user?.userId ? parsed : null
    } catch {
      return null
    }
  }

  private writeSession() {
    const session: PlatformSession = {
      user: { userId: USER_SCOPE, username: USERNAME },
      loggedAt: Date.now(),
    }
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session))
    window.dispatchEvent(new CustomEvent('authSuccess', { detail: { userInfo: session.user } }))
  }

  async login(username: string, password: string): Promise<boolean> {
    if (username.trim() !== USERNAME || password !== PASSWORD) return false
    this.writeSession()
    this.hideLoginModal()
    return true
  }

  async checkAuth(): Promise<boolean> {
    return Boolean(this.readSession())
  }

  async requireUserScope(): Promise<string> {
    if (!(await this.checkAuth())) throw new Error('请先登录平台账号')
    return USER_SCOPE
  }

  async requirePlatformLogin(): Promise<string> {
    return this.requireUserScope()
  }

  getUserInfo(): PlatformUser | null { return this.readSession()?.user || null }
  isAuthenticated(): boolean { return Boolean(this.readSession()) }
  getUserScope(): string { return USER_SCOPE }
  isLocalFallbackEnabled(): boolean { return false }

  showLoginModal() {
    if (this.modal && document.body.contains(this.modal)) {
      this.modal.querySelector<HTMLInputElement>('input[name="platform-username"]')?.focus()
      return
    }

    const overlay = document.createElement('div')
    overlay.className = 'platform-login-overlay'
    const modal = document.createElement('div')
    modal.className = 'platform-login-modal'
    modal.setAttribute('role', 'dialog')
    modal.setAttribute('aria-modal', 'true')
    modal.setAttribute('aria-labelledby', 'platform-login-title')
    const closeButton = document.createElement('button')
    closeButton.className = 'platform-login-close'; closeButton.type = 'button'; closeButton.setAttribute('aria-label', '关闭'); closeButton.textContent = '×'
    const mark = document.createElement('div')
    mark.className = 'platform-login-mark'; mark.setAttribute('aria-hidden', 'true'); mark.textContent = '文'
    const title = document.createElement('h2')
    title.id = 'platform-login-title'; title.textContent = '登录科技文献智能服务平台'
    const subtitle = document.createElement('p')
    subtitle.className = 'platform-login-subtitle'; subtitle.textContent = '登录后使用智能体、上传材料和历史任务功能'
    const form = document.createElement('form')
    form.className = 'platform-login-form'
    const createField = (labelText: string, name: string, type = 'text', placeholder: string) => {
      const label = document.createElement('label'); label.textContent = labelText
      const input = document.createElement('input'); input.name = name; input.type = type; input.autocomplete = type === 'password' ? 'current-password' : 'username'; input.placeholder = placeholder; input.value = type === 'password' ? PASSWORD : USERNAME
      label.appendChild(input); return label
    }
    const error = document.createElement('p'); error.className = 'platform-login-error'; error.setAttribute('role', 'alert')
    const submit = document.createElement('button'); submit.className = 'platform-login-submit'; submit.type = 'submit'; submit.textContent = '登录'
    form.append(createField('账号', 'platform-username', 'text', '请输入账号'), createField('密码', 'platform-password', 'password', '请输入密码'), error, submit)
    modal.append(closeButton, mark, title, subtitle, form)
    overlay.appendChild(modal)
    document.body.appendChild(overlay)
    this.modal = overlay

    const close = () => this.hideLoginModal()
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || (event.target as Element).closest('.platform-login-close')) close()
    })
    form.addEventListener('submit', async (event) => {
      event.preventDefault()
      const username = form.elements.namedItem('platform-username') as HTMLInputElement
      const password = form.elements.namedItem('platform-password') as HTMLInputElement
      const ok = await this.login(username.value, password.value)
      if (!ok && error) error.textContent = '账号或密码错误，请使用平台演示账号登录'
    })
    overlay.addEventListener('keydown', (event) => {
      if ((event as KeyboardEvent).key === 'Escape') close()
    })
    modal.querySelector<HTMLInputElement>('input[name="platform-username"]')?.focus()
  }

  hideLoginModal() { this.modal?.remove(); this.modal = null }

  logout() {
    sessionStorage.removeItem(SESSION_KEY)
    this.hideLoginModal()
    window.dispatchEvent(new CustomEvent('authLogout'))
  }
}

let authManager: PlatformAuthManager | null = null
export function createAuthManager(_config: Record<string, unknown> = {}) { if (!authManager) authManager = new PlatformAuthManager(); return authManager }
export function getAuthManager() { return authManager ?? createAuthManager() }
export function getCurrentUser() { return getAuthManager().getUserInfo() }
export function getBusinessUserScope() { return USER_SCOPE }
export async function requireUserScope() { return getAuthManager().requireUserScope() }
export async function requirePlatformLogin() { return getAuthManager().requirePlatformLogin() }
