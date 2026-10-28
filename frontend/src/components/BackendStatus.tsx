import { useEffect, useState } from 'react'

export function BackendStatus() {
  const [online, setOnline] = useState<boolean | null>(null)
  useEffect(() => {
    let active = true
    let controller: AbortController | undefined
    async function check() {
      controller?.abort()
      controller = new AbortController()
      const timeout = setTimeout(() => controller?.abort(), 8000)
      try {
        const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
        const response = await fetch(`${base}/ready`, {
          signal: controller.signal,
          credentials: 'omit',
          cache: 'no-store',
        })
        if (active) setOnline(response.ok)
      } catch {
        if (active) setOnline(false)
      } finally {
        clearTimeout(timeout)
      }
    }
    void check()
    const timer = setInterval(() => void check(), 60_000)
    return () => {
      active = false
      clearInterval(timer)
      controller?.abort()
    }
  }, [])
  return (
    <span className="status-tag" data-online={online === true}>
      <span className="status-dot" aria-hidden="true" />
      {online === null ? '连接服务中' : online ? '服务已就绪' : '服务暂不可用'}
    </span>
  )
}
