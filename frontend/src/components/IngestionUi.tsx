import { useEffect, useRef, type ReactNode } from 'react'
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
import { Notice } from './Notice'

export function Tag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`state-tag state-tag-${tone}`}>{children}</span>
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

export function JobProgress({ job }: { job: ImportJob }) {
  const percent = jobPercent(job)
  const running = job.status === 'running' || job.status === 'queued'
  return (
    <div className="job-progress">
      <span>{jobProgressText(job)}</span>
      {running && (
        <div
          className={`job-bar ${percent === null ? 'job-bar-unknown' : ''}`}
          role="progressbar"
          aria-label="处理进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
        >
          <i style={percent === null ? undefined : { width: `${percent}%` }} />
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
}: {
  total: number
  page: number
  pageSize: number
  unit: string
  onPage: (page: number) => void
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
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          第 {page} / {pages} 页
        </span>
        <button
          type="button"
          className="icon-button"
          aria-label="下一页"
          disabled={page >= pages}
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
    <>
      <Notice kind="error" error={error}>
        {errorMessage(error)}
      </Notice>
      <button type="button" className="text-button admin-retry" onClick={onRetry}>
        重试加载
      </button>
    </>
  )
}

/** 模态对话框；使用原生 dialog，关闭后浏览器把焦点还给触发按钮。 */
export function Dialog({
  eyebrow,
  title,
  busy,
  onClose,
  children,
  wide,
}: {
  eyebrow: string
  title: string
  busy: boolean
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return (
    <dialog
      ref={dialog}
      className={`admin-confirm ${wide ? 'admin-confirm-wide' : ''}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) onClose()
      }}
    >
      <span className="eyebrow">// {eyebrow}</span>
      <h2>{title}</h2>
      {children}
    </dialog>
  )
}

export function ConfirmDialog({
  eyebrow,
  title,
  target,
  impact,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  eyebrow: string
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
    <Dialog eyebrow={eyebrow} title={title} busy={busy} onClose={onClose}>
      <p className="admin-confirm-name">{target}</p>
      <p>{impact}</p>
      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}
      <div className="profile-actions">
        <button
          type="button"
          className="cut-button button-dark"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy && <LoaderCircle size={14} className="spinner" />}
          {confirmLabel}
        </button>
        <button type="button" className="cut-button" disabled={busy} onClick={onClose}>
          取消
        </button>
      </div>
    </Dialog>
  )
}
