import { useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { errorMessage } from '../lib/api'
import {
  DEFAULT_PARSE_OPTIONS,
  validateParseOptions,
  versionName,
  versionPermissions,
} from '../lib/ingestion'
import type { DocumentVersion, ParseOptions, VersionBrief } from '../lib/types'
import { ConfirmDialog, Dialog } from './IngestionUi'
import { ParseOptionFields } from './IngestionForms'
import { Notice } from './Notice'

type Action = 'approve' | 'publish' | 'withdraw' | 'remove' | 'reparse'

export function VersionActions({
  version,
  replaces,
  onChanged,
  onError,
  onDeleted,
}: {
  version: DocumentVersion
  /** 同一制度当前的发布版本，用于在发布前说明影响范围。 */
  replaces: VersionBrief | null
  onChanged: (message: string) => void
  onError: (error: unknown) => void
  onDeleted?: () => void
}) {
  const { client } = useAuth()
  const [action, setAction] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [options, setOptions] = useState<ParseOptions>({
    ...DEFAULT_PARSE_OPTIONS,
    ...version.parse_options,
  })
  const can = versionPermissions(version)
  const name = versionName(version)

  function open(next: Action) {
    setError(null)
    setAction(next)
  }

  async function run(work: () => Promise<unknown>, message: string, deleted = false) {
    setBusy(true)
    setError(null)
    try {
      await work()
      setAction(null)
      if (deleted && onDeleted) onDeleted()
      else onChanged(message)
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function retry() {
    if (!version.latest_job) return
    setRetrying(true)
    try {
      await client.retryJob(version.latest_job.id)
      onChanged('任务已重新排队。')
    } catch (caught) {
      onError(caught)
    } finally {
      setRetrying(false)
    }
  }

  const invalidOptions = validateParseOptions(options)
  const replacingAnother = replaces && replaces.id !== version.id
  const dialogProps = {
    busy,
    error,
    target: name,
    onClose: () => setAction(null),
  }
  return (
    <>
      <div className="version-actions">
        {can.publish && (
          <button
            type="button"
            className="cut-button button-primary"
            onClick={() => open('publish')}
          >
            发布
          </button>
        )}
        {can.approve && (
          <button type="button" className="cut-button button-dark" onClick={() => open('approve')}>
            {version.status === 'archived' ? '重新上线' : '批准向量化'}
          </button>
        )}
        {can.retry && (
          <button
            type="button"
            className="cut-button button-dark"
            disabled={retrying}
            onClick={() => void retry()}
          >
            {retrying && <LoaderCircle size={14} className="spinner" />}
            重试任务
          </button>
        )}
        {can.reparse && (
          <button type="button" className="text-button" onClick={() => open('reparse')}>
            重新解析
          </button>
        )}
        {can.withdraw && (
          <button
            type="button"
            className="text-button admin-disable"
            onClick={() => open('withdraw')}
          >
            下架
          </button>
        )}
        {can.remove && (
          <button
            type="button"
            className="text-button admin-disable"
            onClick={() => open('remove')}
          >
            删除
          </button>
        )}
      </div>

      {action === 'publish' && (
        <ConfirmDialog
          {...dialogProps}
          eyebrow="PUBLISH VERSION"
          title="确认发布此版本？"
          impact={
            replacingAnother
              ? `发布后，用户将检索到此版本；当前发布的“${versionName(replaces)}”会被归档，不再进入检索。`
              : '发布后，所有用户都能检索到此版本的条文。'
          }
          confirmLabel={busy ? '正在发布…' : '确认发布'}
          onConfirm={() => void run(() => client.publishVersion(version.id), `已发布“${name}”。`)}
        />
      )}
      {action === 'approve' && (
        <ConfirmDialog
          {...dialogProps}
          eyebrow="APPROVE VERSION"
          title={version.status === 'archived' ? '确认重新上线？' : '确认批准向量化？'}
          impact={
            version.parse_status === 'needs_ocr'
              ? '此版本还有未识别的图片内容，批准后这些内容不会进入检索。建议先换 OCR 方式重新解析。'
              : '将创建向量化任务，完成后进入“待发布”，仍需发布才能被检索。'
          }
          confirmLabel={busy ? '正在提交…' : '确认批准'}
          onConfirm={() => void run(() => client.approveVersion(version.id), '已创建向量化任务。')}
        />
      )}
      {action === 'withdraw' && (
        <ConfirmDialog
          {...dialogProps}
          eyebrow="WITHDRAW VERSION"
          title="确认下架此版本？"
          impact="下架后用户不能再检索到这些条文。切片和原件会保留，可以重新上线。"
          confirmLabel={busy ? '正在下架…' : '确认下架'}
          onConfirm={() => void run(() => client.withdrawVersion(version.id), `已下架“${name}”。`)}
        />
      )}
      {action === 'remove' && (
        <ConfirmDialog
          {...dialogProps}
          eyebrow="DELETE VERSION"
          title="确认删除此版本？"
          impact={`将永久删除原件 ${version.file_name}、全部切片和向量，无法恢复。`}
          confirmLabel={busy ? '正在删除…' : '确认删除'}
          onConfirm={() =>
            void run(() => client.deleteVersion(version.id), `已删除“${name}”。`, true)
          }
        />
      )}
      {action === 'reparse' && (
        <Dialog
          eyebrow="REPARSE VERSION"
          title="重新解析此版本"
          busy={busy}
          wide
          onClose={() => setAction(null)}
        >
          <p>
            完成后会替换现有切片
            {version.status === 'ready' ? '，并需要重新向量化后才能发布' : ''}。
          </p>
          <ParseOptionFields idPrefix="reparse" value={options} onChange={setOptions} />
          {invalidOptions && <Notice kind="error">{invalidOptions}</Notice>}
          {Boolean(error) && (
            <Notice kind="error" error={error}>
              {errorMessage(error)}
            </Notice>
          )}
          <div className="profile-actions">
            <button
              type="button"
              className="cut-button button-dark"
              disabled={busy || Boolean(invalidOptions)}
              onClick={() =>
                void run(() => client.reparseVersion(version.id, options), '已创建解析任务。')
              }
            >
              {busy && <LoaderCircle size={14} className="spinner" />}
              开始解析
            </button>
            <button
              type="button"
              className="cut-button"
              disabled={busy}
              onClick={() => setAction(null)}
            >
              取消
            </button>
          </div>
        </Dialog>
      )}
    </>
  )
}
