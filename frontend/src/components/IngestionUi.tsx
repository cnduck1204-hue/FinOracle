import type { ReactNode } from 'react'
import { ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react'
import { errorMessage } from '../lib/api'
import {
  JOB_STATUS_LABELS,
  VALIDITY_LABELS,
  VERSION_STATUS_LABELS,
  jobPercent,
  jobProgressText,
  jobTone,
  validityTone,
  versionTone,
  type Tone,
} from '../lib/ingestion'
import type { ImportJob, JobStatus, ValidityStatus, VersionStatus } from '../lib/types'
import { Dialog } from './Dialog'
import { Notice } from './Notice'

export function Tag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`tag tag-${tone}`}>{children}</span>
}

export function VersionStatusTag({ status }: { status: VersionStatus }) {
  return <Tag tone={versionTone(status)}>{VERSION_STATUS_LABELS[status]}</Tag>
}

export function JobStatusTag({ status }: { status: JobStatus }) {
  return <Tag tone={jobTone(status)}>{JOB_STATUS_LABELS[status]}</Tag>
}

export function ValidityTag({ status }: { status: ValidityStatus }) {
  return <Tag tone={validityTone(status)}>{VALIDITY_LABELS[status]}</Tag>
}

const progressSegments = Array.from({ length: 10 }, (_, index) => index)

export function JobProgress({ job }: { job: ImportJob }) {
  const percent = jobPercent(job)
  const running = job.status === 'running'
  const filled = percent === null ? 0 : Math.floor(percent / 10)
  return (
    <div className="job-progress">
      <span>{jobProgressText(job)}</span>
      {(percent !== null || running) && (
        <div
          className="job-progress-bar"
          data-indeterminate={percent === null || undefined}
          data-running={running || undefined}
          role="progressbar"
          aria-label="处理进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
        >
          {progressSegments.map((segment) => (
            <span key={segment} data-filled={segment < filled || undefined} aria-hidden="true" />
          ))}
        </div>
      )}
    </div>
  )
}

export function Pagination({
  total,
  page,
  pageSize,
  unit,
  onPage,
  disabled = false,
}: {
  total: number
  page: number
  pageSize: number
  unit: string
  onPage: (page: number) => void
  disabled?: boolean
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="admin-pagination">
      <span>
        共 {total} {unit}
      </span>
      <div>
        <button
          type="button"
          className="icon-button"
          aria-label="上一页"
          disabled={disabled || page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span className="page-count">
          <span className="sr-only">
            第 {page} 页，共 {pages} 页
          </span>
          <span aria-hidden="true">
            {String(page).padStart(2, '0')} / {String(pages).padStart(3, '0')}
          </span>
        </span>
        <button
          type="button"
          className="icon-button"
          aria-label="下一页"
          disabled={disabled || page >= pages}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <div className="admin-state" role="status">
      <LoaderCircle size={18} className="spinner" />
      {children}
    </div>
  )
}

export function LoadFailure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div style={{ padding: '16px 18px' }}>
      <Notice kind="error" error={error}>
        {errorMessage(error)}
      </Notice>
      <button type="button" className="text-button admin-retry" onClick={onRetry}>
        重试加载
      </button>
    </div>
  )
}

export function ConfirmDialog({
  title,
  target,
  impact,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  title: string
  target: string
  impact: ReactNode
  confirmLabel: string
  busy: boolean
  error: unknown
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog title={title} busy={busy} onClose={onClose}>
      <p className="admin-confirm-name">{target}</p>
      <p>{impact}</p>
      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}
      <div className="profile-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={onConfirm}>
          {busy && <LoaderCircle size={14} className="spinner" />}
          {confirmLabel}
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onClose}>
          取消
        </button>
      </div>
    </Dialog>
  )
}
