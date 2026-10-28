import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function Notice({
  children,
  kind = 'info',
  error,
}: {
  children: ReactNode
  kind?: 'info' | 'error' | 'success'
  error?: unknown
}) {
  return (
    <div className={`notice notice-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
      {error instanceof ApiError && error.requestId && (
        <details>
          <summary>查看问题编号</summary>
          <code>{error.requestId}</code>
        </details>
      )}
    </div>
  )
}
