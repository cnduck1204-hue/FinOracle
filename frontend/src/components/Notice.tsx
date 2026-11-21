import type { ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'
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
  const Icon = kind === 'error' ? AlertCircle : kind === 'success' ? CheckCircle2 : Info
  return (
    <div className={`notice notice-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <Icon size={16} className="notice-icon" aria-hidden="true" />
      <div className="notice-body">
        {children}
        {error instanceof ApiError && error.requestId && (
          <details>
            <summary>查看问题编号</summary>
            <code>{error.requestId}</code>
          </details>
        )}
      </div>
    </div>
  )
}
