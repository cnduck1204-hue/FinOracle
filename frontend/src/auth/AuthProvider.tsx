import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { ApiError, AuthClient, errorMessage } from '../lib/api'
import type { SessionSnapshot } from '../lib/types'

let tabStorage: Storage | undefined
try {
  tabStorage = window.sessionStorage
} catch {
  /* Use in-memory authentication if browser storage is unavailable. */
}
export const api = new AuthClient({
  storage: tabStorage,
  baseUrl: import.meta.env.VITE_API_BASE_URL || '/api/v1',
})
if (import.meta.hot) import.meta.hot.dispose(() => api.dispose())

interface AuthState extends SessionSnapshot {
  client: AuthClient
  boot: 'loading' | 'ready' | 'error'
  bootError: string | null
  connectionWarning: string | null
  retry: () => void
  returnToLogin: () => void
}
const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({
  children,
  client = api,
}: {
  children: ReactNode
  client?: AuthClient
}) {
  const snapshot = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const [boot, setBoot] = useState<AuthState['boot']>('loading')
  const [bootError, setBootError] = useState<string | null>(null)
  const [connectionWarning, setConnectionWarning] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    let active = true
    if (!client.getSnapshot().hasSession) {
      setBoot('ready')
      return
    }
    setBoot('loading')
    client
      .getProfile()
      .then(() => {
        if (active) {
          setBoot('ready')
          setBootError(null)
        }
      })
      .catch((error) => {
        if (!active) return
        if (
          !client.getSnapshot().hasSession ||
          (error instanceof ApiError && error.code === 'REQUEST_CANCELLED')
        )
          setBoot('ready')
        else {
          setBoot('error')
          setBootError(errorMessage(error))
        }
      })
    return () => {
      active = false
    }
  }, [client, retryCount])

  useEffect(() => {
    if (!snapshot.user) {
      setConnectionWarning(null)
      return
    }
    let active = true
    const recover = (forceProfile = false) => {
      const current = client.getSnapshot()
      if (!current.user) return
      if (!forceProfile && (!current.expiresAt || current.expiresAt - Date.now() > 60_000)) return
      const operation = forceProfile ? client.getProfile() : client.refresh()
      operation
        .then(() => {
          if (active) setConnectionWarning(null)
        })
        .catch((error) => {
          if (
            active &&
            client.getSnapshot().hasSession &&
            !(error instanceof ApiError && error.code === 'REQUEST_CANCELLED')
          )
            setConnectionWarning('连接暂时中断，正在尝试恢复。你可以稍后重试操作。')
        })
    }
    const timer = setInterval(() => recover(), 20_000)
    const visible = () => {
      if (document.visibilityState === 'visible') recover(true)
    }
    document.addEventListener('visibilitychange', visible)
    return () => {
      active = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [client, Boolean(snapshot.user)])

  const retry = useCallback(() => setRetryCount((value) => value + 1), [])
  const returnToLogin = useCallback(() => {
    client.forgetSession()
    setBoot('ready')
    setBootError(null)
  }, [client])

  return (
    <AuthContext.Provider
      value={{ ...snapshot, client, boot, bootError, connectionWarning, retry, returnToLogin }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('Authentication context is missing')
  return context
}
