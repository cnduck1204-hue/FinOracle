import type { RegisterInput, SessionSnapshot, TokenPair, User } from './types'
import { browserLockRequest, TabLease, type LockRequest } from './tab-lease'

export const SESSION_KEY = 'finoracle.refresh.v1'
type TokenStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
type Fetcher = typeof fetch

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status = 0,
    public readonly code = 'NETWORK_ERROR',
    public readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function isUser(value: unknown): value is User {
  if (!value || typeof value !== 'object') return false
  const user = value as Partial<User>
  return (
    typeof user.id === 'string' &&
    typeof user.username === 'string' &&
    typeof user.email === 'string' &&
    (user.role === 'user' || user.role === 'admin') &&
    (user.status === 'active' || user.status === 'disabled') &&
    typeof user.created_at === 'string' &&
    typeof user.updated_at === 'string'
  )
}

function validatePair(value: unknown): TokenPair {
  const pair = value as Partial<TokenPair> | null
  if (
    !pair ||
    !isUser(pair.user) ||
    typeof pair.access_token !== 'string' ||
    !pair.access_token ||
    typeof pair.refresh_token !== 'string' ||
    !pair.refresh_token ||
    pair.token_type !== 'bearer' ||
    typeof pair.expires_in !== 'number' ||
    !Number.isInteger(pair.expires_in) ||
    pair.expires_in <= 0
  ) {
    throw new ApiError('服务返回的数据不完整，请稍后重试。', 502, 'INVALID_RESPONSE')
  }
  return pair as TokenPair
}

export class AuthClient {
  private readonly fetcher: Fetcher
  private readonly storage?: TokenStorage
  private readonly baseUrl: string
  private accessToken: string | null = null
  private refreshToken: string | null = null
  private generation = 0
  private refreshFlight: Promise<TokenPair> | null = null
  private listeners = new Set<() => void>()
  private snapshot: SessionSnapshot
  private readonly lease: TabLease
  private owner: string | null = null

  constructor(
    options: {
      fetcher?: Fetcher
      storage?: TokenStorage
      baseUrl?: string
      lockRequest?: LockRequest
    } = {},
  ) {
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis)
    this.lease = new TabLease(options.lockRequest ?? browserLockRequest())
    // Older browsers without ownership locks use memory-only sessions.
    this.storage =
      typeof window !== 'undefined' && !this.lease.enabled ? undefined : options.storage
    this.baseUrl = (options.baseUrl ?? '/api/v1').replace(/\/$/, '')
    try {
      this.refreshToken = this.storage?.getItem(SESSION_KEY) || null
      this.owner = this.storage?.getItem(`${SESSION_KEY}.owner`) || null
    } catch {
      /* Storage can be disabled by browser privacy settings. */
    }
    this.snapshot = {
      user: null,
      expiresAt: null,
      hasSession: Boolean(this.refreshToken),
      notice: null,
    }
  }

  getSnapshot = (): SessionSnapshot => this.snapshot
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private publish(update: Partial<SessionSnapshot>) {
    this.snapshot = { ...this.snapshot, ...update }
    this.listeners.forEach((listener) => listener())
  }

  forgetSession = (notice: string | null = null): void => {
    this.generation++
    this.lease.release()
    this.owner = null
    this.accessToken = this.refreshToken = null
    this.refreshFlight = null
    try {
      this.storage?.removeItem(SESSION_KEY)
      this.storage?.removeItem(`${SESSION_KEY}.owner`)
    } catch {
      /* Memory is still cleared. */
    }
    this.publish({ user: null, hasSession: false, expiresAt: null, notice })
  }

  private assertCurrent(generation: number) {
    if (generation !== this.generation) throw new ApiError('操作已取消。', 409, 'REQUEST_CANCELLED')
  }

  private newOwner() {
    return (
      globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
    )
  }

  private async claimOwnership(generation: number) {
    this.assertCurrent(generation)
    this.owner ??= this.newOwner()
    const available = await this.lease.claim(this.owner)
    this.assertCurrent(generation)
    if (!available) {
      this.forgetSession('此登录正在另一个标签页使用，请在本页重新登录。')
      throw new ApiError('请在本页重新登录。', 401, 'SESSION_IN_ANOTHER_TAB')
    }
  }

  private accept(pair: TokenPair, generation: number) {
    this.assertCurrent(generation)
    this.accessToken = pair.access_token
    this.refreshToken = pair.refresh_token
    // Only the refresh token is persisted, and only for this tab. Never log tokens.
    try {
      this.storage?.setItem(SESSION_KEY, pair.refresh_token)
      if (this.owner) this.storage?.setItem(`${SESSION_KEY}.owner`, this.owner)
    } catch {
      /* Continue as a memory-only session. */
    }
    this.publish({
      user: pair.user,
      hasSession: true,
      expiresAt: Date.now() + pair.expires_in * 1000,
      notice: null,
    })
  }

  private async fetchResponse(path: string, init: RequestInit = {}): Promise<Response> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15_000)
    try {
      return await this.fetcher(this.baseUrl + path, {
        ...init,
        credentials: 'omit',
        cache: 'no-store',
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          ...(init.body ? { 'Content-Type': 'application/json' } : {}),
          ...init.headers,
        },
      })
    } catch {
      throw new ApiError(
        controller.signal.aborted
          ? '连接等待超时，请稍后重试。'
          : '暂时无法连接服务，请检查网络后重试。',
      )
    } finally {
      clearTimeout(timeout)
    }
  }

  private async readResponse<T>(response: Response): Promise<T> {
    if (response.status === 204) return undefined as T
    let value: unknown
    try {
      value = await response.json()
    } catch {
      throw new ApiError(
        '服务暂时无法响应，请稍后重试。',
        response.status || 502,
        'INVALID_RESPONSE',
      )
    }
    if (!response.ok) {
      const body = value as { message?: unknown; code?: unknown; request_id?: unknown } | null
      throw new ApiError(
        typeof body?.message === 'string' ? body.message : '操作暂时无法完成，请稍后重试。',
        response.status,
        typeof body?.code === 'string' ? body.code : 'SERVICE_ERROR',
        typeof body?.request_id === 'string'
          ? body.request_id
          : (response.headers.get('X-Request-ID') ?? undefined),
      )
    }
    return value as T
  }

  async login(identifier: string, password: string): Promise<User> {
    const generation = ++this.generation
    this.lease.release()
    this.owner = this.newOwner()
    this.refreshFlight = null
    const response = await this.fetchResponse('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ identifier, password }),
    })
    const pair = validatePair(await this.readResponse(response))
    if (this.lease.enabled) await this.claimOwnership(generation)
    this.accept(pair, generation)
    return pair.user
  }

  async register(input: RegisterInput): Promise<User> {
    const response = await this.fetchResponse('/auth/register', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    const user = await this.readResponse<unknown>(response)
    if (!isUser(user))
      throw new ApiError('账号资料未能正确返回，请尝试登录。', 502, 'INVALID_RESPONSE')
    return user
  }

  async refresh(): Promise<TokenPair> {
    if (this.refreshFlight) return this.refreshFlight
    const generation = this.generation
    const refreshToken = this.refreshToken
    if (!refreshToken) throw new ApiError('请先登录。', 401, 'AUTH_REQUIRED')
    const flight = (async () => {
      try {
        if (this.lease.enabled) await this.claimOwnership(generation)
        const response = await this.fetchResponse('/auth/refresh', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
        const pair = validatePair(await this.readResponse(response))
        this.accept(pair, generation)
        return pair
      } catch (error) {
        // Transient failures must not pretend the session was revoked.
        if (
          generation === this.generation &&
          error instanceof ApiError &&
          (error.status === 401 || error.code === 'USER_DISABLED')
        ) {
          this.forgetSession(error.status === 401 ? '登录已失效，请重新登录。' : error.message)
        }
        throw error
      }
    })()
    this.refreshFlight = flight
    try {
      return await flight
    } finally {
      if (this.refreshFlight === flight) this.refreshFlight = null
    }
  }

  private async protectedRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
    const generation = this.generation
    if (!this.accessToken) await this.refresh()
    this.assertCurrent(generation)
    const usedToken = this.accessToken
    let response = await this.fetchResponse(path, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${usedToken}` },
    })
    this.assertCurrent(generation)
    if (response.status === 401) {
      // A late 401 may refer to the previous access token: reuse the new one.
      if (this.accessToken === usedToken) await this.refresh()
      this.assertCurrent(generation)
      response = await this.fetchResponse(path, {
        ...init,
        headers: { ...init.headers, Authorization: `Bearer ${this.accessToken}` },
      })
    }
    try {
      const result = await this.readResponse<T>(response)
      this.assertCurrent(generation)
      return result
    } catch (error) {
      if (
        generation === this.generation &&
        error instanceof ApiError &&
        (error.status === 401 || error.code === 'USER_DISABLED')
      ) {
        this.forgetSession(error.status === 401 ? '登录已失效，请重新登录。' : error.message)
      }
      throw error
    }
  }

  async getProfile(): Promise<User> {
    const generation = this.generation
    const user = await this.protectedRequest<unknown>('/users/me')
    // Recheck at the public method boundary, after its final await.
    this.assertCurrent(generation)
    if (!isUser(user))
      throw new ApiError('账号资料未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    this.publish({ user })
    return user
  }

  async logout(allSessions = false): Promise<void> {
    const generation = this.generation
    // Wait for an already-running refresh so logout uses its current access token.
    if (this.refreshFlight) await this.refreshFlight
    await this.protectedRequest<void>(allSessions ? '/auth/logout-all' : '/auth/logout', {
      method: 'POST',
    })
    this.assertCurrent(generation)
    this.forgetSession(allSessions ? '已退出全部会话。' : '已退出当前会话。')
  }

  dispose() {
    this.generation++
    this.lease.release()
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作暂时无法完成，请稍后重试。'
}
