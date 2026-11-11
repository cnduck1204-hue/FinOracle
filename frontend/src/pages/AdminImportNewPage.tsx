import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from 'react'
import { ArrowLeft, FileUp, LoaderCircle, X } from 'lucide-react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { Field, MetadataFields, ParseOptionFields } from '../components/IngestionForms'
import { LoadFailure, LoadingState } from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { errorMessage } from '../lib/api'
import {
  ACCEPTED_TYPES,
  DEFAULT_PARSE_OPTIONS,
  EMPTY_METADATA,
  buildImportForm,
  formatBytes,
  validateMetadata,
  validateParseOptions,
  validateUpload,
} from '../lib/ingestion'
import type { DocumentSummary, ParseOptions } from '../lib/types'
import { useResource } from '../lib/useResource'

const ACCEPT = ACCEPTED_TYPES.map((type) => `.${type}`).join(',')

export function AdminImportNewPage() {
  const { client } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const documentId = params.get('document_id') ?? ''
  const existing = useResource<DocumentSummary | null>(
    () => (documentId ? client.getDocument(documentId) : Promise.resolve(null)),
    [client, documentId],
  )
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [title, setTitle] = useState('')
  const [issuer, setIssuer] = useState('')
  const [metadata, setMetadata] = useState(EMPTY_METADATA)
  const [options, setOptions] = useState<ParseOptions>(DEFAULT_PARSE_OPTIONS)
  const [problem, setProblem] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const input = useRef<HTMLInputElement>(null)

  function choose(candidate: File | undefined) {
    if (!candidate) return
    const invalid = validateUpload(candidate)
    setFileError(invalid ?? '')
    setFile(invalid ? null : candidate)
  }

  function onSelect(event: ChangeEvent<HTMLInputElement>) {
    choose(event.target.files?.[0])
    event.target.value = ''
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    choose(event.dataTransfer.files[0])
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const invalid =
      (!file && '请先选择文件。') ||
      (!documentId && !title.trim() && '新建制度时需要填写标题。') ||
      validateMetadata(metadata) ||
      validateParseOptions(options)
    if (invalid || !file) {
      setProblem(invalid || '')
      return
    }
    setProblem('')
    setSubmitting(true)
    try {
      const job = await client.createImport(
        buildImportForm({
          file,
          documentId: documentId || undefined,
          title,
          issuer,
          metadata,
          options,
        }),
      )
      navigate(`/admin/imports/${job.id}`)
    } catch (caught) {
      setError(caught)
      setSubmitting(false)
    }
  }

  return (
    <>
      <Link
        className="back-link"
        to={documentId ? `/admin/documents/${documentId}/versions` : '/admin/imports'}
      >
        <ArrowLeft size={14} aria-hidden="true" />
        {documentId ? '返回版本管理' : '返回导入任务'}
      </Link>
      <header className="page-heading">
        <div>
          <span className="eyebrow">// IMPORT DOCUMENT</span>
          <h1>{documentId ? '上传新版本' : '导入文档'}</h1>
          <p>上传后在后台解析。发布前，用户不会检索到这份文档。</p>
        </div>
        <span className="page-index">IMPORT / 01</span>
      </header>

      {existing.loading && <LoadingState>正在读取制度…</LoadingState>}
      {Boolean(existing.error) && <LoadFailure error={existing.error} onRetry={existing.reload} />}
      {!existing.loading && !existing.error && (
        <form className="ingest-form" onSubmit={(event) => void submit(event)} noValidate>
          <fieldset disabled={submitting}>
            <section className="ingest-section">
              <h2>
                <span>01</span>选择文件
              </h2>
              <label
                className={`drop-zone ${dragging ? 'drop-zone-active' : ''}`}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                <input
                  ref={input}
                  className="sr-only"
                  type="file"
                  accept={ACCEPT}
                  aria-label="选择要导入的文件"
                  onChange={onSelect}
                />
                <FileUp size={26} strokeWidth={1.3} aria-hidden="true" />
                <strong>点击选择，或把文件拖到这里</strong>
                <small>PDF、Word、RTF、ODT、TXT、Markdown 或图片，单个不超过 50 MB</small>
              </label>
              {file && (
                <div className="picked-file">
                  <span>
                    {file.name} · {formatBytes(file.size)}
                  </span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="移除文件"
                    onClick={() => setFile(null)}
                  >
                    <X size={15} />
                  </button>
                </div>
              )}
              {fileError && <Notice kind="error">{fileError}</Notice>}
            </section>

            <section className="ingest-section">
              <h2>
                <span>02</span>制度信息
              </h2>
              {existing.data ? (
                <p className="ingest-existing">
                  为 <strong>{existing.data.title}</strong>{' '}
                  创建新版本。已发布的版本在新版本发布前不受影响。
                </p>
              ) : (
                <div className="ingest-grid">
                  <Field
                    id="import-title"
                    label="制度标题"
                    required
                    value={title}
                    onChange={setTitle}
                  />
                  <Field id="import-issuer" label="发文机构" value={issuer} onChange={setIssuer} />
                </div>
              )}
            </section>

            <section className="ingest-section">
              <h2>
                <span>03</span>版本登记信息
              </h2>
              <MetadataFields idPrefix="import" value={metadata} onChange={setMetadata} />
            </section>

            <details className="ingest-section ingest-advanced">
              <summary>
                <span>04</span>解析参数（通常不用修改）
              </summary>
              <ParseOptionFields idPrefix="import" value={options} onChange={setOptions} />
            </details>

            {problem && <Notice kind="error">{problem}</Notice>}
            {Boolean(error) && (
              <Notice kind="error" error={error}>
                {errorMessage(error)}
              </Notice>
            )}
            <div className="profile-actions">
              <button className="cut-button button-primary" type="submit">
                {submitting && <LoaderCircle size={14} className="spinner" />}
                {submitting ? '正在上传…' : '开始导入'}
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </>
  )
}
