// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { AuthClient, ApiError, SESSION_KEY } from './api'

const user = {
  id: 'user-id',
  username: 'alice',
  email: 'alice@example.com',
  role: 'user',
  status: 'active',
  last_login_at: null,
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
}
const pair = (suffix = '1') => ({
  access_token: `access-${suffix}`,
  refresh_token: `refresh-${suffix}`,
  expires_in: 1800,
  token_type: 'bearer',
  user,
})
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
function storage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
    removeItem: (key: string) => {
      values.delete(key)
    },
  }
}

describe('authentication client', () => {
  it('serializes refresh when two protected requests have no access token', async () => {
    const saved = storage()
    saved.setItem(SESSION_KEY, 'refresh-0')
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith('/auth/refresh')) {
        await new Promise((resolve) => setTimeout(resolve, 5))
        return json(pair())
      }
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer access-1')
      return json(user)
    })
    const client = new AuthClient({ fetcher, storage: saved })
    const results = await Promise.all([client.getProfile(), client.getProfile()])
    expect(results.map((value) => value.username)).toEqual(['alice', 'alice'])
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh')),
    ).toHaveLength(1)
    expect(saved.getItem(SESSION_KEY)).toBe('refresh-1')
  })

  it('does not rotate twice when a delayed old-token 401 arrives after another refresh', async () => {
    let release: (() => void) | undefined
    let oldReads = 0
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/auth/login')) return json(pair('old'))
      if (path.endsWith('/auth/refresh')) return json(pair('new'))
      if (new Headers(init?.headers).get('Authorization') === 'Bearer access-old') {
        oldReads++
        if (oldReads === 2)
          await new Promise<void>((resolve) => {
            release = resolve
          })
        return json({ code: 'AUTH_INVALID_TOKEN', message: '令牌无效', request_id: 'id' }, 401)
      }
      return json(user)
    })
    const client = new AuthClient({ fetcher, storage: storage() })
    await client.login('alice', 'Password123')
    const first = client.getProfile()
    const delayed = client.getProfile()
    await first
    release?.()
    await delayed
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh')),
    ).toHaveLength(1)
  })

  it('preserves the session on a temporary refresh failure', async () => {
    const saved = storage()
    saved.setItem(SESSION_KEY, 'refresh-old')
    const client = new AuthClient({
      storage: saved,
      fetcher: vi.fn(async () =>
        json(
          {
            code: 'AUTH_SERVICE_UNAVAILABLE',
            message: '认证服务暂时不可用',
            request_id: 'trace-1',
          },
          503,
        ),
      ),
    })
    await expect(client.getProfile()).rejects.toMatchObject({ status: 503, requestId: 'trace-1' })
    expect(saved.getItem(SESSION_KEY)).toBe('refresh-old')
  })

  it('clears an expired or revoked refresh session', async () => {
    const saved = storage()
    saved.setItem(SESSION_KEY, 'refresh-old')
    const client = new AuthClient({
      storage: saved,
      fetcher: vi.fn(async () =>
        json({ code: 'AUTH_INVALID_TOKEN', message: '登录已失效', request_id: 'trace' }, 401),
      ),
    })
    await expect(client.getProfile()).rejects.toBeInstanceOf(ApiError)
    expect(client.getSnapshot().hasSession).toBe(false)
    expect(saved.getItem(SESSION_KEY)).toBeNull()
  })

  it('keeps the account signed in if logout fails', async () => {
    const saved = storage()
    const fetcher = vi.fn(async (input: string | URL | Request) =>
      String(input).endsWith('/auth/login')
        ? json(pair())
        : json({ code: 'AUTH_SERVICE_UNAVAILABLE', message: '退出失败', request_id: 'trace' }, 503),
    )
    const client = new AuthClient({ storage: saved, fetcher })
    await client.login('alice', 'Password123')
    await expect(client.logout(true)).rejects.toMatchObject({ status: 503 })
    expect(client.getSnapshot().user?.username).toBe('alice')
    expect(saved.getItem(SESSION_KEY)).toBe('refresh-1')
  })

  it('ignores a login response from an abandoned session', async () => {
    let finish: ((value: Response) => void) | undefined
    const saved = storage()
    const client = new AuthClient({
      storage: saved,
      fetcher: vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve
          }),
      ),
    })
    const pending = client.login('alice', 'Password123')
    client.forgetSession()
    finish?.(json(pair()))
    await expect(pending).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' })
    expect(saved.getItem(SESSION_KEY)).toBeNull()
  })

  it('retries a protected request at most once', async () => {
    const saved = storage()
    const fetcher = vi.fn(async (input: string | URL | Request) =>
      String(input).endsWith('/auth/login') || String(input).endsWith('/auth/refresh')
        ? json(pair())
        : json({ code: 'AUTH_INVALID_TOKEN', message: '登录已失效', request_id: 'trace' }, 401),
    )
    const client = new AuthClient({ storage: saved, fetcher })
    await client.login('alice', 'Password123')
    await expect(client.getProfile()).rejects.toMatchObject({ status: 401 })
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/users/me'))).toHaveLength(2)
  })

  it('does not restore tokens when an abandoned refresh eventually completes', async () => {
    const saved = storage()
    saved.setItem(SESSION_KEY, 'refresh-old')
    let finish: ((response: Response) => void) | undefined
    const client = new AuthClient({
      storage: saved,
      fetcher: vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve
          }),
      ),
    })
    const pending = client.refresh()
    client.forgetSession()
    finish?.(json(pair('late')))
    await expect(pending).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' })
    expect(client.getSnapshot().hasSession).toBe(false)
    expect(saved.getItem(SESSION_KEY)).toBeNull()
  })

  it('waits for an active refresh before sending logout', async () => {
    let finish: ((response: Response) => void) | undefined
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith('/auth/login')) return json(pair('old'))
      if (String(input).endsWith('/auth/refresh'))
        return new Promise<Response>((resolve) => {
          finish = resolve
        })
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer access-new')
      return new Response(null, { status: 204 })
    })
    const client = new AuthClient({ fetcher, storage: storage() })
    await client.login('alice', 'Password123')
    const refreshing = client.refresh()
    const loggingOut = client.logout()
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/auth/logout'))).toBe(false)
    finish?.(json(pair('new')))
    await refreshing
    await loggingOut
    expect(client.getSnapshot().hasSession).toBe(false)
  })

  it('never accepts malformed token responses', async () => {
    const client = new AuthClient({
      storage: storage(),
      fetcher: vi.fn(async () => json({ ...pair(), refresh_token: '' })),
    })
    await expect(client.login('alice', 'Password123')).rejects.toMatchObject({ status: 502 })
    expect(client.getSnapshot().user).toBeNull()
  })

  it('preserves password whitespace and supports blocked browser storage', async () => {
    const blocked = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
      removeItem: () => {
        throw new Error('blocked')
      },
    }
    const client = new AuthClient({
      storage: blocked,
      fetcher: vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
        expect(JSON.parse(String(init?.body)).password).toBe(' Password123 ')
        return json(pair())
      }),
    })
    await client.login('alice', ' Password123 ')
    expect(client.getSnapshot().user?.username).toBe('alice')
    client.forgetSession()
    expect(client.getSnapshot().hasSession).toBe(false)
  })

  it('prevents copied tab credentials from rotating a live session', async () => {
    const held = new Set<string>()
    const lockRequest = async (name: string, callback: (available: boolean) => Promise<void>) => {
      if (held.has(name)) {
        await callback(false)
        return
      }
      held.add(name)
      try {
        await callback(true)
      } finally {
        held.delete(name)
      }
    }
    const saved = storage()
    const fetcher = vi.fn(async (input: string | URL | Request) =>
      String(input).endsWith('/users/me') ? json(user) : json(pair()),
    )
    const original = new AuthClient({ storage: saved, fetcher, lockRequest })
    await original.login('alice', 'Password123')
    const copiedStorage = storage()
    copiedStorage.setItem(SESSION_KEY, saved.getItem(SESSION_KEY)!)
    copiedStorage.setItem(`${SESSION_KEY}.owner`, saved.getItem(`${SESSION_KEY}.owner`)!)
    const copied = new AuthClient({ storage: copiedStorage, fetcher, lockRequest })
    await expect(copied.getProfile()).rejects.toMatchObject({ code: 'SESSION_IN_ANOTHER_TAB' })
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).endsWith('/auth/refresh')),
    ).toHaveLength(0)
    expect(original.getSnapshot().hasSession).toBe(true)
    expect(copied.getSnapshot().hasSession).toBe(false)
    original.forgetSession()
  })

  it('does not republish a profile after a concurrent successful logout', async () => {
    let finishProfile!: (response: Response) => void
    let finishLogout!: (response: Response) => void
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/login')) return json(pair())
      if (String(input).endsWith('/users/me'))
        return new Promise<Response>((resolve) => {
          finishProfile = resolve
        })
      return new Promise<Response>((resolve) => {
        finishLogout = resolve
      })
    })
    const client = new AuthClient({ fetcher, storage: storage() })
    await client.login('alice', 'Password123')
    const profile = client.getProfile()
    const logout = client.logout()
    finishProfile(json(user))
    queueMicrotask(() =>
      queueMicrotask(() => queueMicrotask(() => finishLogout(new Response(null, { status: 204 })))),
    )
    await Promise.allSettled([profile, logout])
    expect(client.getSnapshot().user).toBeNull()
    expect(client.getSnapshot().hasSession).toBe(false)
  })
})
