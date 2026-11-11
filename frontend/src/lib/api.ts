import type {
  DocumentChunk,
  DocumentListQuery,
  DocumentSummary,
  DocumentVersion,
  ImportJob,
  JobListQuery,
  Page,
  ParseOptions,
  ProfileInput,
  RegisterInput,
  SearchInput,
  SearchOutcome,
  SessionSnapshot,
  TokenPair,
  User,
  UserListQuery,
  UserPage,
  VersionMetadata,
} from './types'
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

function hasId(value: unknown): boolean {
  return Boolean(value) && typeof (value as { id?: unknown }).id === 'string'
}

function validatePage<T>(
  value: unknown,
  isItem: (item: unknown) => boolean,
  what: string,
): Page<T> {
  const page = value as Partial<Page<T>> | null
  if (
    !page ||
    !Array.isArray(page.items) ||
    !page.items.every(isItem) ||
    !Number.isSafeInteger(page.total) ||
    !Number.isSafeInteger(page.page) ||
    !Number.isSafeInteger(page.page_size) ||
    page.page_size! < 1
  )
    throw new ApiError(`${what}未能正确返回，请稍后重试。`, 502, 'INVALID_RESPONSE')
  return page as Page<T>
}

function validateRecord<T>(value: unknown, what: string): T {
  if (!hasId(value))
    throw new ApiError(`${what}未能正确返回，请稍后重试。`, 502, 'INVALID_RESPONSE')
  return value as T
}

const pageParams = (query: { page?: number; page_size?: number }) =>
  new URLSearchParams({
    page: String(query.page ?? 1),
    page_size: String(query.page_size ?? 20),
  })
const versionPath = (id: string) => `/admin/versions/${encodeURIComponent(id)}`
const documentPath = (id: string) => `/admin/documents/${encodeURIComponent(id)}`

const DEFAULT_TIMEOUT_MS = 15_000
// 文件写入存储后接口才返回，大文件需要更久；略短于 Nginx 对 admin 路径的 180 秒。
const UPLOAD_TIMEOUT_MS = 170_000
// 发布与下架要等向量库清理完成，读取原文要等对象存储开始返回。
const SLOW_ADMIN_TIMEOUT_MS = 60_000

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

  private async fetchResponse(
    path: string,
    init: RequestInit = {},
    timeoutMs = DEFAULT_TIMEOUT_MS,
  ): Promise<Response> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
      return await this.fetcher(this.baseUrl + path, {
        ...init,
        credentials: 'omit',
        cache: 'no-store',
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          // FormData 的 Content-Type 由浏览器生成，带 multipart 分隔符。
          ...(init.body && !(init.body instanceof FormData)
            ? { 'Content-Type': 'application/json' }
            : {}),
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

  private async protectedResponse(
    path: string,
    init: RequestInit,
    generation: number,
    timeoutMs?: number,
  ): Promise<Response> {
    if (!this.accessToken) await this.refresh()
    this.assertCurrent(generation)
    const usedToken = this.accessToken
    let response = await this.fetchResponse(
      path,
      { ...init, headers: { ...init.headers, Authorization: `Bearer ${usedToken}` } },
      timeoutMs,
    )
    this.assertCurrent(generation)
    if (response.status === 401) {
      // A late 401 may refer to the previous access token: reuse the new one.
      if (this.accessToken === usedToken) await this.refresh()
      this.assertCurrent(generation)
      response = await this.fetchResponse(
        path,
        { ...init, headers: { ...init.headers, Authorization: `Bearer ${this.accessToken}` } },
        timeoutMs,
      )
    }
    return response
  }

  private async readProtected<T>(response: Response, generation: number): Promise<T> {
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

  private async protectedRequest<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs?: number,
  ): Promise<T> {
    const generation = this.generation
    const response = await this.protectedResponse(path, init, generation, timeoutMs)
    return this.readProtected<T>(response, generation)
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

  async updateProfile(input: Partial<ProfileInput>): Promise<User> {
    const generation = this.generation
    const user = await this.protectedRequest<unknown>('/users/me', {
      method: 'PATCH',
      body: JSON.stringify(input),
    })
    this.assertCurrent(generation)
    if (!isUser(user))
      throw new ApiError('账号资料未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    this.publish({ user })
    return user
  }

  async listUsers(query: UserListQuery = {}): Promise<UserPage> {
    const params = new URLSearchParams({
      page: String(query.page ?? 1),
      page_size: String(query.page_size ?? 20),
    })
    if (query.q?.trim()) params.set('q', query.q.trim())
    if (query.role) params.set('role', query.role)
    if (query.status) params.set('status', query.status)
    const result = await this.protectedRequest<Partial<UserPage> | null>(`/admin/users?${params}`)
    if (
      !result ||
      !Array.isArray(result.items) ||
      !result.items.every(isUser) ||
      !Number.isSafeInteger(result.total) ||
      result.total! < 0 ||
      !Number.isSafeInteger(result.page) ||
      result.page! < 1 ||
      !Number.isSafeInteger(result.page_size) ||
      result.page_size! < 1 ||
      result.page_size! > 100
    )
      throw new ApiError('用户列表未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    return result as UserPage
  }

  async getUser(id: string): Promise<User> {
    const result = await this.protectedRequest<unknown>(`/admin/users/${encodeURIComponent(id)}`)
    if (!isUser(result))
      throw new ApiError('用户资料未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    return result
  }

  async setUserStatus(id: string, status: User['status']): Promise<User> {
    const result = await this.protectedRequest<unknown>(
      `/admin/users/${encodeURIComponent(id)}/status`,
      {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      },
    )
    if (!isUser(result))
      throw new ApiError('用户资料未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    return result
  }

  async search(input: SearchInput): Promise<SearchOutcome> {
    const result = await this.protectedRequest<SearchOutcome | null>('/search', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    if (!result || !Array.isArray(result.results))
      throw new ApiError('检索结果未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    return result
  }

  async listDocuments(query: DocumentListQuery = {}): Promise<Page<DocumentSummary>> {
    const params = pageParams(query)
    if (query.q?.trim()) params.set('q', query.q.trim())
    if (query.published !== undefined) params.set('published', String(query.published))
    const result = await this.protectedRequest<unknown>(`/admin/documents?${params}`)
    return validatePage(result, hasId, '制度列表')
  }

  async getDocument(id: string): Promise<DocumentSummary> {
    return validateRecord(await this.protectedRequest<unknown>(documentPath(id)), '制度信息')
  }

  async updateDocument(
    id: string,
    input: { title?: string; issuer?: string | null },
  ): Promise<DocumentSummary> {
    const result = await this.protectedRequest<unknown>(documentPath(id), {
      method: 'PATCH',
      body: JSON.stringify(input),
    })
    return validateRecord(result, '制度信息')
  }

  async listVersions(documentId: string): Promise<DocumentVersion[]> {
    const result = await this.protectedRequest<unknown>(`${documentPath(documentId)}/versions`)
    if (!Array.isArray(result) || !result.every(hasId))
      throw new ApiError('版本列表未能正确返回，请稍后重试。', 502, 'INVALID_RESPONSE')
    return result as DocumentVersion[]
  }

  async getVersion(id: string): Promise<DocumentVersion> {
    return validateRecord(await this.protectedRequest<unknown>(versionPath(id)), '版本信息')
  }

  async updateVersion(id: string, input: Partial<VersionMetadata>): Promise<DocumentVersion> {
    const result = await this.protectedRequest<unknown>(versionPath(id), {
      method: 'PATCH',
      body: JSON.stringify(input),
    })
    return validateRecord(result, '版本信息')
  }

  async listChunks(
    versionId: string,
    query: { page?: number; page_size?: number } = {},
  ): Promise<Page<DocumentChunk>> {
    const result = await this.protectedRequest<unknown>(
      `${versionPath(versionId)}/chunks?${pageParams(query)}`,
    )
    return validatePage(
      result,
      (item) => typeof (item as { id?: unknown } | null)?.id === 'number',
      '切片列表',
    )
  }

  /** 原文接口需要鉴权，不能直接作为 iframe 地址，所以先取回 Blob。 */
  async fetchVersionFile(id: string, variant: 'original' | 'pdf'): Promise<Blob> {
    const generation = this.generation
    const response = await this.protectedResponse(
      `${versionPath(id)}/file?variant=${variant}`,
      {},
      generation,
      SLOW_ADMIN_TIMEOUT_MS,
    )
    if (!response.ok) await this.readProtected<never>(response, generation)
    const blob = await response.blob()
    this.assertCurrent(generation)
    return blob
  }

  async createImport(form: FormData): Promise<ImportJob> {
    const result = await this.protectedRequest<unknown>(
      '/admin/imports',
      { method: 'POST', body: form },
      UPLOAD_TIMEOUT_MS,
    )
    return validateRecord(result, '导入任务')
  }

  async listJobs(query: JobListQuery = {}): Promise<Page<ImportJob>> {
    const params = pageParams(query)
    if (query.status) params.set('status', query.status)
    if (query.kind) params.set('kind', query.kind)
    if (query.document_id) params.set('document_id', query.document_id)
    const result = await this.protectedRequest<unknown>(`/admin/imports?${params}`)
    return validatePage(result, hasId, '任务列表')
  }

  async getJob(id: string): Promise<ImportJob> {
    const result = await this.protectedRequest<unknown>(`/admin/imports/${encodeURIComponent(id)}`)
    return validateRecord(result, '任务信息')
  }

  async retryJob(id: string): Promise<ImportJob> {
    const result = await this.protectedRequest<unknown>(
      `/admin/imports/${encodeURIComponent(id)}/retry`,
      { method: 'POST' },
    )
    return validateRecord(result, '任务信息')
  }

  async reparseVersion(id: string, options?: ParseOptions): Promise<ImportJob> {
    const result = await this.protectedRequest<unknown>(`${versionPath(id)}/reparse`, {
      method: 'POST',
      ...(options ? { body: JSON.stringify(options) } : {}),
    })
    return validateRecord(result, '任务信息')
  }

  async approveVersion(id: string): Promise<ImportJob> {
    const result = await this.protectedRequest<unknown>(`${versionPath(id)}/approve`, {
      method: 'POST',
    })
    return validateRecord(result, '任务信息')
  }

  async publishVersion(id: string): Promise<DocumentVersion> {
    const result = await this.protectedRequest<unknown>(
      `${versionPath(id)}/publish`,
      { method: 'POST' },
      SLOW_ADMIN_TIMEOUT_MS,
    )
    return validateRecord(result, '版本信息')
  }

  async withdrawVersion(id: string): Promise<DocumentVersion> {
    const result = await this.protectedRequest<unknown>(
      `${versionPath(id)}/withdraw`,
      { method: 'POST' },
      SLOW_ADMIN_TIMEOUT_MS,
    )
    return validateRecord(result, '版本信息')
  }

  async deleteVersion(id: string): Promise<void> {
    await this.protectedRequest<void>(versionPath(id), { method: 'DELETE' }, SLOW_ADMIN_TIMEOUT_MS)
  }

  async logout(): Promise<void> {
    const generation = this.generation
    // Wait for an already-running refresh so logout uses its current access token.
    if (this.refreshFlight) await this.refreshFlight
    await this.protectedRequest<void>('/auth/logout', {
      method: 'POST',
    })
    this.assertCurrent(generation)
    this.forgetSession('已退出登录。')
  }

  dispose() {
    this.generation++
    this.lease.release()
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作暂时无法完成，请稍后重试。'
}
