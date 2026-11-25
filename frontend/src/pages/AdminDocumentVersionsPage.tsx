import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Layers, LoaderCircle, Pencil, Plus, RotateCw } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { Field } from '../components/IngestionForms'
import { Dialog } from '../components/Dialog'
import {
  JobProgress,
  JobStatusTag,
  LoadFailure,
  LoadingState,
  ValidityTag,
  VersionStatusTag,
} from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { VersionActions } from '../components/VersionActions'
import { VersionPipelines } from '../components/VersionPipelines'
import { errorMessage } from '../lib/api'
import { formatDateTime } from '../lib/dates'
import {
  PARSE_STATUS_LABELS,
  formatBytes,
  isJobActive,
  isVersionBusy,
  parseTagInput,
  versionName,
} from '../lib/ingestion'
import type { DocumentSummary } from '../lib/types'
import { useResource } from '../lib/useResource'

function EditDocument({
  document,
  onClose,
  onSaved,
}: {
  document: DocumentSummary
  onClose: () => void
  onSaved: () => void
}) {
  const { client } = useAuth()
  const [title, setTitle] = useState(document.title)
  const [issuer, setIssuer] = useState(document.issuer ?? '')
  const [category, setCategory] = useState(document.category ?? '')
  const [tagsText, setTagsText] = useState((document.tags ?? []).join(', '))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await client.updateDocument(document.id, {
        title: title.trim(),
        issuer: issuer.trim() || null,
        category: category.trim() || null,
        tags: parseTagInput(tagsText),
      })
      onSaved()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog title="修改制度信息" busy={busy} onClose={onClose}>
      <form className="auth-form" onSubmit={(event) => void save(event)}>
        <Field id="edit-title" label="制度标题" required value={title} onChange={setTitle} />
        <Field id="edit-issuer" label="发文机构" value={issuer} onChange={setIssuer} />
        <Field
          id="edit-category"
          label="主题分类"
          placeholder="例如：银行监管、证券法规、保险制度"
          value={category}
          onChange={setCategory}
        />
        <Field
          id="edit-tags"
          label="知识点标签（逗号分隔）"
          placeholder="例如：资本充足率, 合规风控"
          value={tagsText}
          onChange={setTagsText}
        />
        {Boolean(error) && (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        )}
        <div className="profile-actions">
          <button className="btn btn-primary" type="submit" disabled={busy || !title.trim()}>
            {busy && <LoaderCircle size={14} className="spinner" />}
            保存
          </button>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function AdminDocumentVersionsPage() {
  const { client } = useAuth()
  const { documentId = '' } = useParams()
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState<unknown>(null)
  const [editing, setEditing] = useState(false)
  const { data, error, loading, reload } = useResource(
    async () => {
      const [document, versions] = await Promise.all([
        client.getDocument(documentId),
        client.listVersions(documentId),
      ])
      return { document, versions }
    },
    [client, documentId],
    (value) =>
      value.versions.some(
        (version) =>
          isVersionBusy(version.status) ||
          (version.latest_job && isJobActive(version.latest_job.status)),
      ),
  )
  useEffect(() => {
    setNotice('')
    setActionError(null)
  }, [documentId])

  function changed(message: string) {
    setNotice(message)
    setActionError(null)
    reload()
  }

  return (
    <>
      <Link className="back-link" to="/library?view=manage">
        <ArrowLeft size={14} aria-hidden="true" />
        返回制度库
      </Link>
      {loading && <LoadingState>正在加载版本…</LoadingState>}
      {Boolean(error) && !data && <LoadFailure error={error} onRetry={reload} />}
      {data && (
        <>
          <header className="page-heading">
            <div className="page-heading-text">
              <span className="page-kicker">{data.document.category || 'Document Versions'}</span>
              <h1>{data.document.title}</h1>
              <p>{data.document.issuer || '未填写发文机构'}</p>
              {data.document.tags && data.document.tags.length > 0 && (
                <div className="lib-admin-tags" style={{ marginTop: 8 }}>
                  {data.document.tags.map((tag) => (
                    <span key={tag} className="lib-tag-pill">
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="heading-actions">
              <button type="button" className="btn" onClick={() => setEditing(true)}>
                <Pencil size={14} aria-hidden="true" />
                修改信息
              </button>
              <Link
                className="btn btn-primary"
                to={`/admin/imports/new?document_id=${data.document.id}`}
              >
                <Plus size={15} aria-hidden="true" />
                上传新版本
              </Link>
            </div>
          </header>

          {notice && <Notice kind="success">{notice}</Notice>}
          {Boolean(actionError) && (
            <Notice kind="error" error={actionError}>
              {errorMessage(actionError)}
            </Notice>
          )}

          <section className="admin-list" aria-labelledby="version-list-heading">
            <div className="admin-section-heading">
              <h2 id="version-list-heading">
                <Layers size={17} aria-hidden="true" />
                版本（{data.versions.length}）
              </h2>
              <button type="button" className="text-button" onClick={reload}>
                <RotateCw size={13} aria-hidden="true" />
                刷新
              </button>
            </div>
            <ol className="version-list" role="list">
              {data.versions.map((version, index) => (
                <li key={version.id} className="version-card">
                  <div className="version-card-head">
                    <div>
                      <h3>
                        <span className="version-index" aria-hidden="true">
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        {versionName(version)}
                      </h3>
                      <small>
                        {version.file_name} · {formatBytes(version.size_bytes)}
                      </small>
                    </div>
                    <div className="version-tags">
                      <VersionStatusTag status={version.status} />
                      <ValidityTag status={version.validity_status} />
                    </div>
                  </div>
                  <dl className="version-facts">
                    <div>
                      <dt>文号</dt>
                      <dd>{version.document_number || '未填写'}</dd>
                    </div>
                    <div>
                      <dt>解析结果</dt>
                      <dd>
                        {version.parse_status
                          ? `${PARSE_STATUS_LABELS[version.parse_status] ?? version.parse_status} · ${version.chunk_count} 个切片`
                          : '尚未完成'}
                      </dd>
                    </div>
                    <div>
                      <dt>上传时间</dt>
                      <dd>{formatDateTime(version.created_at)}</dd>
                    </div>
                    <div>
                      <dt>发布时间</dt>
                      <dd>{formatDateTime(version.published_at)}</dd>
                    </div>
                  </dl>
                  {version.latest_job && (
                    <div className="version-job">
                      <JobStatusTag status={version.latest_job.status} />
                      <JobProgress job={version.latest_job} />
                      {version.latest_job.error_message && (
                        <span className="job-error">{version.latest_job.error_message}</span>
                      )}
                    </div>
                  )}
                  <VersionPipelines
                    version={version}
                    onChanged={changed}
                    onError={setActionError}
                  />
                  <div className="version-card-foot">
                    {version.latest_job && (
                      <Link className="text-button" to={`/admin/imports/${version.latest_job.id}`}>
                        解析校验
                      </Link>
                    )}
                    <VersionActions
                      version={version}
                      replaces={data.document.published_version}
                      onChanged={changed}
                      onError={setActionError}
                      onDeleted={() => changed('版本已删除。')}
                    />
                  </div>
                </li>
              ))}
            </ol>
            {!data.versions.length && <div className="admin-state">这份制度还没有版本。</div>}
          </section>

          {editing && (
            <EditDocument
              document={data.document}
              onClose={() => setEditing(false)}
              onSaved={() => {
                setEditing(false)
                changed('制度信息已更新。')
              }}
            />
          )}
        </>
      )}
    </>
  )
}
