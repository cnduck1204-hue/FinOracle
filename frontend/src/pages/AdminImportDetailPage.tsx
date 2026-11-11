import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  ExternalLink,
  LoaderCircle,
  Pencil,
} from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { MetadataFields } from '../components/IngestionForms'
import {
  Dialog,
  JobProgress,
  JobStatusTag,
  LoadFailure,
  LoadingState,
  Pagination,
  Tag,
  ValidityTag,
  VersionStatusTag,
} from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { VersionActions } from '../components/VersionActions'
import { errorMessage } from '../lib/api'
import {
  CHUNKER_OPTIONS,
  JOB_KIND_LABELS,
  PARSE_STATUS_LABELS,
  articlePath,
  formatBytes,
  formatDate,
  formatDateTime,
  isJobActive,
  isVersionBusy,
  metadataForm,
  metadataPatch,
  pageLabel,
  pipelineSteps,
  previewKind,
  validateMetadata,
} from '../lib/ingestion'
import type { DocumentVersion } from '../lib/types'
import { useResource } from '../lib/useResource'

const CHUNK_PAGE_SIZE = 20

function Pipeline({ version }: { version: DocumentVersion }) {
  return (
    <ol className="pipeline" aria-label="处理流程">
      {pipelineSteps(version).map((step) => (
        <li key={step.label} className={`pipeline-step pipeline-${step.state}`}>
          <i aria-hidden="true" />
          <span>{step.label}</span>
          {step.state === 'failed' && <small>失败</small>}
          {step.state === 'current' && <small>当前</small>}
        </li>
      ))}
    </ol>
  )
}

function SourcePreview({ version, page }: { version: DocumentVersion; page: number | null }) {
  const { client } = useAuth()
  const kind = previewKind(version)
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!kind) return
    let active = true
    let created: string | null = null
    setUrl(null)
    setError(null)
    client
      .fetchVersionFile(version.id, kind === 'pdf' ? 'pdf' : 'original')
      .then((blob) => {
        if (!active) return
        created = URL.createObjectURL(blob)
        setUrl(created)
      })
      .catch((caught) => {
        if (active) setError(caught)
      })
    return () => {
      active = false
      if (created) URL.revokeObjectURL(created)
    }
  }, [client, version.id, version.has_pdf, kind, attempt])

  if (!kind)
    return (
      <div className="admin-state source-empty">
        {version.file_type.toUpperCase()} 格式没有分页，无法在页面中预览，可以下载原件对照。
      </div>
    )
  if (error) return <LoadFailure error={error} onRetry={() => setAttempt((value) => value + 1)} />
  if (!url) return <LoadingState>正在读取原文…</LoadingState>
  return kind === 'pdf' ? (
    <>
      <iframe
        key={page ?? 0}
        className="source-frame"
        title={`原文预览：${version.file_name}`}
        src={page ? `${url}#page=${page}` : url}
      />
      <a className="text-button source-open" href={url} target="_blank" rel="noreferrer">
        <ExternalLink size={12} aria-hidden="true" />
        在新标签页打开 PDF
      </a>
    </>
  ) : (
    <img className="source-image" src={url} alt={`原文预览：${version.file_name}`} />
  )
}

function EditMetadata({
  version,
  onClose,
  onSaved,
}: {
  version: DocumentVersion
  onClose: () => void
  onSaved: () => void
}) {
  const { client } = useAuth()
  const [form, setForm] = useState(() => metadataForm(version))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState('')

  async function save(event: FormEvent) {
    event.preventDefault()
    const invalid = validateMetadata(form)
    setProblem(invalid ?? '')
    if (invalid) return
    const patch = metadataPatch(version, form)
    if (!Object.keys(patch).length) {
      onClose()
      return
    }
    setBusy(true)
    setError(null)
    try {
      await client.updateVersion(version.id, patch)
      onSaved()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog eyebrow="VERSION METADATA" title="修改登记信息" busy={busy} wide onClose={onClose}>
      <form onSubmit={(event) => void save(event)} noValidate>
        <MetadataFields idPrefix="edit" value={form} onChange={setForm} />
        {problem && <Notice kind="error">{problem}</Notice>}
        {Boolean(error) && (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        )}
        <div className="profile-actions">
          <button className="cut-button button-dark" type="submit" disabled={busy}>
            {busy && <LoaderCircle size={14} className="spinner" />}
            保存
          </button>
          <button type="button" className="cut-button" disabled={busy} onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function AdminImportDetailPage() {
  const { client } = useAuth()
  const navigate = useNavigate()
  const { jobId = '' } = useParams()
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState<unknown>(null)
  const [editing, setEditing] = useState(false)
  const [chunkPage, setChunkPage] = useState(1)
  const [sourcePage, setSourcePage] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const sourcePane = useRef<HTMLElement>(null)

  const { data, error, loading, reload } = useResource(
    async () => {
      const job = await client.getJob(jobId)
      const [version, document] = await Promise.all([
        client.getVersion(job.version_id),
        client.getDocument(job.document_id),
      ])
      return { job, version, document }
    },
    [client, jobId],
    // 解析成功后会自动转入向量化，因此按版本状态而不是这一个任务决定是否继续刷新。
    (value) =>
      isVersionBusy(value.version.status) ||
      isJobActive((value.version.latest_job ?? value.job).status),
  )
  const version = data?.version
  const chunks = useResource(
    () =>
      version
        ? client.listChunks(version.id, { page: chunkPage, page_size: CHUNK_PAGE_SIZE })
        : Promise.resolve(null),
    [client, version?.id, version?.status, version?.chunk_count, chunkPage],
  )
  useEffect(() => {
    setChunkPage(1)
    setSourcePage(null)
    setNotice('')
    setActionError(null)
  }, [jobId])

  function changed(message: string) {
    setNotice(message)
    setActionError(null)
    reload()
  }

  async function downloadOriginal(target: DocumentVersion) {
    setDownloading(true)
    setActionError(null)
    try {
      const blob = await client.fetchVersionFile(target.id, 'original')
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = target.file_name
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (caught) {
      setActionError(caught)
    } finally {
      setDownloading(false)
    }
  }

  if (loading) return <LoadingState>正在加载任务…</LoadingState>
  if (!data || !version)
    return (
      <>
        <Link className="back-link" to="/admin/imports">
          <ArrowLeft size={14} aria-hidden="true" />
          返回导入任务
        </Link>
        <LoadFailure error={error} onRetry={reload} />
      </>
    )

  const job = version.latest_job ?? data.job
  const mismatch =
    version.expected_articles !== null &&
    version.parse_status !== null &&
    version.expected_articles !== version.chunk_count
  const warnings = version.parse_warnings ?? []
  const stats = version.parse_stats ?? {}
  const chunker = String(stats.chunker ?? version.parse_options.chunker ?? '')
  const usedOcr = Number(stats.ocr_targets ?? 0) > 0
  return (
    <>
      <Link className="back-link" to="/admin/imports">
        <ArrowLeft size={14} aria-hidden="true" />
        返回导入任务
      </Link>
      <header className="page-heading">
        <div>
          <span className="eyebrow">// PARSE REVIEW</span>
          <h1>{data.document.title}</h1>
          <p>
            {version.file_name} · {formatBytes(version.size_bytes)}
          </p>
        </div>
        <div className="heading-actions">
          <VersionStatusTag status={version.status} />
          <ValidityTag status={version.validity_status} />
          <Link className="text-button" to={`/admin/documents/${version.document_id}/versions`}>
            版本管理
          </Link>
        </div>
      </header>

      <Pipeline version={version} />

      {notice && <Notice kind="success">{notice}</Notice>}
      {Boolean(actionError) && (
        <Notice kind="error" error={actionError}>
          {errorMessage(actionError)}
        </Notice>
      )}

      <section className="review-panel" aria-label="当前任务">
        <div className="review-job">
          <JobStatusTag status={job.status} />
          <strong>{JOB_KIND_LABELS[job.kind]}任务</strong>
          <JobProgress job={job} />
          <small>
            尝试 {job.attempts} / {job.max_attempts} · 更新于 {formatDateTime(job.updated_at)}
          </small>
        </div>
        <VersionActions
          version={version}
          replaces={data.document.published_version}
          onChanged={changed}
          onError={setActionError}
          onDeleted={() => navigate(`/admin/documents/${version.document_id}/versions`)}
        />
      </section>
      {job.status === 'failed' && (
        <Notice kind="error">
          失败原因：{job.error_message || '未知错误'}
          {job.error_code && <code className="job-code">{job.error_code}</code>}
        </Notice>
      )}
      {version.parse_status === 'needs_ocr' && (
        <div className="review-warning">
          <AlertTriangle size={15} aria-hidden="true" />
          有图片内容没有识别，默认不会自动批准。建议换一种 OCR 方式重新解析。
        </div>
      )}
      {mismatch && (
        <div className="review-warning">
          <AlertTriangle size={15} aria-hidden="true" />
          登记应有 {version.expected_articles} 条，实际切出 {version.chunk_count}
          个切片。切片可能包含标题或被拆分的长条文，请对照原文核对。
        </div>
      )}
      {warnings.map((warning) => (
        <div className="review-warning" key={warning}>
          <AlertTriangle size={15} aria-hidden="true" />
          {warning}
        </div>
      ))}

      <section className="admin-detail">
        <div className="admin-section-heading">
          <h2>解析与登记信息</h2>
          <button type="button" className="text-button" onClick={() => setEditing(true)}>
            <Pencil size={13} aria-hidden="true" />
            修改登记信息
          </button>
        </div>
        <dl className="review-facts">
          <div>
            <dt>解析结果</dt>
            <dd>{version.parse_status ? PARSE_STATUS_LABELS[version.parse_status] : '尚未完成'}</dd>
          </div>
          <div>
            <dt>页数 / 切片</dt>
            <dd>
              {version.page_count ?? '无分页'} / {version.chunk_count}
            </dd>
          </div>
          <div>
            <dt>切片方式</dt>
            <dd>
              {CHUNKER_OPTIONS.find((option) => option.value === chunker)?.label ||
                chunker ||
                '未知'}
            </dd>
          </div>
          <div>
            <dt>OCR</dt>
            <dd>{usedOcr ? String(stats.ocr_provider ?? '已使用') : '未使用'}</dd>
          </div>
          <div>
            <dt>向量模型</dt>
            <dd>{version.index_model ?? '尚未向量化'}</dd>
          </div>
          <div>
            <dt>文号</dt>
            <dd>{version.document_number || '未填写'}</dd>
          </div>
          <div>
            <dt>版本说明</dt>
            <dd>{version.version_note || '未填写'}</dd>
          </div>
          <div>
            <dt>发布 / 生效日期</dt>
            <dd>
              {formatDate(version.published_date)} / {formatDate(version.effective_date)}
            </dd>
          </div>
        </dl>
      </section>

      <div className="review-columns">
        <section aria-labelledby="chunk-heading">
          <div className="admin-section-heading">
            <h2 id="chunk-heading">切片（{version.chunk_count}）</h2>
          </div>
          {chunks.loading && <LoadingState>正在加载切片…</LoadingState>}
          {Boolean(chunks.error) && <LoadFailure error={chunks.error} onRetry={chunks.reload} />}
          {chunks.data && (
            <>
              <ol className="chunk-list">
                {chunks.data.items.map((chunk) => (
                  <li key={chunk.id} className="chunk-card">
                    <div className="chunk-head">
                      <strong>{articlePath(chunk)}</strong>
                      {chunk.page_start !== null ? (
                        <button
                          type="button"
                          className="text-button"
                          aria-label={`在原文中查看${pageLabel(chunk.page_start, chunk.page_end)}`}
                          onClick={() => {
                            setSourcePage(chunk.page_start)
                            // 窄屏时原文在切片下方；双栏时原文已吸顶，nearest 不会滚动。
                            sourcePane.current?.scrollIntoView({ block: 'nearest' })
                          }}
                        >
                          {pageLabel(chunk.page_start, chunk.page_end)}
                        </button>
                      ) : (
                        <small>{pageLabel(null, null)}</small>
                      )}
                    </div>
                    <p>{chunk.content}</p>
                    <div className="chunk-tags">
                      {chunk.part_count > 1 && (
                        <Tag tone="neutral">
                          拆分 {chunk.part} / {chunk.part_count}
                        </Tag>
                      )}
                      {chunk.sources.includes('ocr') && <Tag tone="warn">OCR 识别，请核对</Tag>}
                    </div>
                  </li>
                ))}
              </ol>
              {!chunks.data.items.length && (
                <div className="admin-state">
                  {isVersionBusy(version.status) ? '解析完成后显示切片。' : '没有切片。'}
                </div>
              )}
              {chunks.data.total > CHUNK_PAGE_SIZE && (
                <Pagination
                  total={chunks.data.total}
                  page={chunks.data.page}
                  pageSize={chunks.data.page_size}
                  unit="个切片"
                  onPage={setChunkPage}
                />
              )}
            </>
          )}
        </section>
        <section ref={sourcePane} className="source-pane" aria-labelledby="source-heading">
          <div className="admin-section-heading">
            <h2 id="source-heading">原文对照</h2>
            <button
              type="button"
              className="text-button"
              disabled={downloading}
              onClick={() => void downloadOriginal(version)}
            >
              <Download size={13} aria-hidden="true" />
              下载原件
            </button>
          </div>
          <SourcePreview version={version} page={sourcePage} />
        </section>
      </div>

      {editing && (
        <EditMetadata
          version={version}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            changed('登记信息已更新。')
          }}
        />
      )}
    </>
  )
}
