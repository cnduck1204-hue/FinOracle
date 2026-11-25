import { useEffect, useState, type FormEvent } from 'react'
import {
  ArrowUpRight,
  BookOpen,
  ExternalLink,
  Layers,
  LoaderCircle,
  Pencil,
  Plus,
  RotateCw,
  Search,
  Star,
  Tag,
  Upload,
} from 'lucide-react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { Dialog } from '../components/Dialog'
import { Field } from '../components/IngestionForms'
import {
  LoadFailure,
  LoadingState,
  Pagination,
  ValidityTag,
  VersionStatusTag,
} from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { SaveFavoriteDialog, type SaveFavoriteTarget } from '../components/SaveFavoriteDialog'
import { Select } from '../components/Select'
import { errorMessage } from '../lib/api'
import { formatDate, formatDateTime } from '../lib/dates'
import {
  VALIDITY_LABELS,
  articlePath,
  isVersionBusy,
  pageLabel,
  parseTagInput,
  versionName,
} from '../lib/ingestion'
import type {
  DocumentSummary,
  FavoriteSourceMatch,
  LibraryDocumentSummary,
  ValidityStatus,
} from '../lib/types'
import { useResource } from '../lib/useResource'

const PAGE_SIZE = 15
const CHUNK_PAGE_SIZE = 15

type EditableDocument = {
  id: string
  title: string
  issuer: string | null
  category?: string | null
  tags?: string[]
}

function EditDocumentDialog({
  document,
  onClose,
  onSaved,
}: {
  document: EditableDocument
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
    if (!title.trim() || busy) return
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
    <Dialog title="编辑制度信息与标签" kicker="Knowledge Base" busy={busy} onClose={onClose}>
      <form className="auth-form" onSubmit={(event) => void save(event)}>
        <Field id="lib-edit-title" label="制度标题" required value={title} onChange={setTitle} />
        <Field id="lib-edit-issuer" label="发文机构" value={issuer} onChange={setIssuer} />
        <Field
          id="lib-edit-category"
          label="主题分类"
          placeholder="例如：银行监管、证券法规、保险制度"
          value={category}
          onChange={setCategory}
        />
        <Field
          id="lib-edit-tags"
          label="知识点标签（逗号分隔）"
          placeholder="例如：资本充足率, 投资者适当性"
          value={tagsText}
          onChange={setTagsText}
        />
        {Boolean(error) && (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        )}
        <div className="profile-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" type="submit" disabled={busy || !title.trim()}>
            {busy && <LoaderCircle size={14} className="spinner" />}
            保存
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function ClauseReaderDialog({
  document,
  onClose,
}: {
  document: LibraryDocumentSummary
  onClose: () => void
}) {
  const { client } = useAuth()
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [savedMap, setSavedMap] = useState<Record<string, FavoriteSourceMatch>>({})
  const [favBusyKey, setFavBusyKey] = useState<string | null>(null)
  const [saveTarget, setSaveTarget] = useState<SaveFavoriteTarget | null>(null)
  const [favError, setFavError] = useState<unknown>(null)

  const { data, error, loading, reload } = useResource(
    () =>
      client.listLibraryChunks(document.id, {
        page,
        page_size: CHUNK_PAGE_SIZE,
        q: query || undefined,
      }),
    [client, document.id, page, query],
  )

  useEffect(() => {
    const items = data?.items ?? []
    if (!items.length) {
      setSavedMap({})
      return
    }
    const controller = new AbortController()
    client
      .lookupFavorites(
        items.map((item) => `chunk:${item.id}`),
        controller.signal,
      )
      .then((res) => {
        if (!controller.signal.aborted) setSavedMap(res.items)
      })
      .catch(() => {
        // Non-blocking
      })
    return () => controller.abort()
  }, [client, data])

  async function handleUnfavorite(favoriteId: string, sourceKey: string) {
    if (favBusyKey) return
    setFavBusyKey(sourceKey)
    setFavError(null)
    try {
      await client.deleteFavorite(favoriteId)
      setSavedMap((previous) => {
        const next = { ...previous }
        delete next[sourceKey]
        return next
      })
    } catch (caught) {
      setFavError(caught)
    } finally {
      setFavBusyKey(null)
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    setPage(1)
    setQuery(input.trim())
  }

  return (
    <Dialog
      title={document.title}
      kicker={document.category || 'Published Regulation'}
      busy={false}
      onClose={onClose}
      wide
    >
      <div className="lib-reader">
        <div className="fav-preview-meta">
          {document.issuer && <span>{document.issuer}</span>}
          {document.version.document_number && <span>{document.version.document_number}</span>}
          {document.version.effective_date && (
            <span>生效：{formatDate(document.version.effective_date)}</span>
          )}
          <ValidityTag status={document.version.validity_status} />
          {document.version.source_url && (
            <a
              className="chat-source-link"
              href={document.version.source_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              官方原文 <ExternalLink size={12} aria-hidden="true" />
            </a>
          )}
        </div>

        <form className="lib-reader-search" onSubmit={submitSearch}>
          <div className="input-wrap">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={input}
              placeholder="在本制度条文中搜索关键词或条号…"
              onChange={(event) => setInput(event.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-sm btn-primary">
            定位条文
          </button>
          {query && (
            <button
              type="button"
              className="text-button"
              onClick={() => {
                setInput('')
                setQuery('')
                setPage(1)
              }}
            >
              清除
            </button>
          )}
        </form>

        {Boolean(favError) && (
          <Notice kind="error" error={favError}>
            {errorMessage(favError)}
          </Notice>
        )}

        {loading && <LoadingState>正在读取条文…</LoadingState>}
        {Boolean(error) && <LoadFailure error={error} onRetry={reload} />}

        {data && (
          <>
            {data.items.length === 0 ? (
              <div className="admin-state">没有找到匹配的条文内容。</div>
            ) : (
              <ol className="lib-clause-list">
                {data.items.map((chunk) => {
                  const key = `chunk:${chunk.id}`
                  const saved = savedMap[key]
                  return (
                    <li key={chunk.id} className="lib-clause">
                      <div className="lib-clause-head">
                        <div>
                          <strong>{articlePath(chunk)}</strong>
                          <small>{pageLabel(chunk.page_start, chunk.page_end)}</small>
                        </div>
                        <button
                          type="button"
                          className={`fav-action-btn${saved ? ' saved' : ''}`}
                          disabled={favBusyKey === key}
                          aria-pressed={Boolean(saved)}
                          onClick={() =>
                            saved
                              ? void handleUnfavorite(saved.id, key)
                              : setSaveTarget({
                                  kind: 'chunk',
                                  chunkId: chunk.id,
                                  defaultTitle: `${document.title} · ${articlePath(chunk)}`,
                                })
                          }
                        >
                          <Star
                            size={12}
                            fill={saved ? 'currentColor' : 'none'}
                            aria-hidden="true"
                          />
                          {saved ? '已收藏' : '收藏条文'}
                        </button>
                      </div>
                      <p className="lib-clause-content">{chunk.content}</p>
                    </li>
                  )
                })}
              </ol>
            )}
            <Pagination
              total={data.total}
              page={data.page}
              pageSize={data.page_size}
              unit="条内容"
              onPage={setPage}
            />
          </>
        )}

        <div className="profile-actions">
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>

      {saveTarget && (
        <SaveFavoriteDialog
          target={saveTarget}
          onClose={() => setSaveTarget(null)}
          onSaved={(saved) => {
            if (saved.source_key) {
              setSavedMap((previous) => ({
                ...previous,
                [saved.source_key!]: { id: saved.id, folder_id: saved.folder_id },
              }))
            }
            setSaveTarget(null)
          }}
        />
      )}
    </Dialog>
  )
}

export function LibraryPage() {
  const { client, user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const view = isAdmin && params.get('view') === 'manage' ? 'manage' : 'published'
  const q = params.get('q') ?? ''
  const category = params.get('category') ?? ''
  const tag = params.get('tag') ?? ''
  const validity = (params.get('validity_status') ?? '') as ValidityStatus | ''
  const published = params.get('published') ?? ''
  const hideRepealed = params.get('include_repealed') === 'false'
  const page = Math.max(1, Number(params.get('page')) || 1)

  const [text, setText] = useState(q)
  const [reading, setReading] = useState<LibraryDocumentSummary | null>(null)
  const [editing, setEditing] = useState<EditableDocument | null>(null)
  const [readingLoadingId, setReadingLoadingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)

  useEffect(() => {
    setText(q)
  }, [q])

  const facets = useResource(() => client.getLibraryFacets(), [client])
  const publishedList = useResource(
    () =>
      view === 'published'
        ? client.listLibraryDocuments({
            page,
            page_size: PAGE_SIZE,
            q: q || undefined,
            category: category || undefined,
            tag: tag || undefined,
            validity_status: validity || undefined,
            include_repealed: !hideRepealed,
          })
        : Promise.resolve(null),
    [client, view, page, q, category, tag, validity, hideRepealed],
  )
  const manageList = useResource(
    () =>
      view === 'manage'
        ? client.listDocuments({
            page,
            page_size: PAGE_SIZE,
            q: q || undefined,
            published: published ? published === 'true' : undefined,
            category: category || undefined,
            tag: tag || undefined,
          })
        : Promise.resolve(null),
    [client, view, page, q, published, category, tag],
    (value) =>
      Boolean(
        value?.items.some(
          (item) => item.latest_version && isVersionBusy(item.latest_version.status),
        ),
      ),
  )

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params)
    next.delete('page')
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next)
  }

  function switchView(nextView: 'published' | 'manage') {
    const next = new URLSearchParams()
    if (nextView === 'manage') next.set('view', 'manage')
    if (q) next.set('q', q)
    if (category) next.set('category', category)
    if (tag) next.set('tag', tag)
    setParams(next)
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    updateParams({ q: text.trim() || null })
  }

  function goTo(nextPage: number) {
    const next = new URLSearchParams(params)
    next.set('page', String(nextPage))
    setParams(next)
  }

  function reloadAll() {
    facets.reload()
    if (view === 'manage') manageList.reload()
    else publishedList.reload()
  }

  async function openManageDocReader(doc: DocumentSummary) {
    if (!doc.published_version || readingLoadingId) return
    setReadingLoadingId(doc.id)
    setActionError(null)
    try {
      const detail = await client.getLibraryDocument(doc.id)
      setReading(detail)
    } catch (caught) {
      setActionError(caught)
    } finally {
      setReadingLoadingId(null)
    }
  }

  const hasFilters = Boolean(q || category || tag || validity || hideRepealed || published)

  return (
    <>
      <header className="page-heading">
        <div className="page-heading-text">
          <span className="page-kicker">Knowledge Base</span>
          <h1>制度库</h1>
          <p>
            {isAdmin
              ? '统一管理与浏览金融制度知识库。已发布版本进入智能问答与全文检索，支持按主题分类与标签维护。'
              : '金融制度知识库：按主题分类、知识点标签与效力状态浏览已发布制度，支持逐条阅读与一键收藏。'}
          </p>
        </div>
        <div className="lib-heading-right">
          {facets.data && (
            <span className="lib-total-badge">
              {facets.data.total_documents} 份制度 · {facets.data.total_chunks} 条知识点
            </span>
          )}
          {isAdmin && (
            <Link className="btn btn-primary" to="/admin/imports/new">
              <Plus size={15} aria-hidden="true" />
              导入文档
            </Link>
          )}
        </div>
      </header>

      <section className="lib-filters-card" aria-label="制度库筛选">
        {isAdmin && (
          <div className="lib-view-bar">
            <div className="lib-view-tabs" role="tablist" aria-label="知识库视图">
              <button
                type="button"
                role="tab"
                aria-selected={view === 'published'}
                className={`lib-view-tab${view === 'published' ? ' active' : ''}`}
                onClick={() => switchView('published')}
              >
                <BookOpen size={14} aria-hidden="true" />
                已发布知识库
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === 'manage'}
                className={`lib-view-tab${view === 'manage' ? ' active' : ''}`}
                onClick={() => switchView('manage')}
              >
                <Layers size={14} aria-hidden="true" />
                全部制度（含草稿 / 处理中）
              </button>
            </div>
            <button type="button" className="text-button" onClick={reloadAll}>
              <RotateCw size={13} aria-hidden="true" />
              刷新
            </button>
          </div>
        )}

        <form className="lib-search-row" onSubmit={submitSearch}>
          <div className="input-wrap lib-search-input">
            <Search size={15} aria-hidden="true" />
            <input
              type="search"
              value={text}
              maxLength={255}
              placeholder="搜索制度标题、文号、发文机构或标签…"
              onChange={(event) => setText(event.target.value)}
            />
          </div>

          {view === 'published' ? (
            <>
              <Select
                id="lib-validity"
                value={validity}
                options={[
                  { value: '', label: '全部效力状态' },
                  ...Object.entries(VALIDITY_LABELS).map(([value, label]) => ({ value, label })),
                ]}
                onChange={(value) => updateParams({ validity_status: value || null })}
              />
              <label className="lib-toggle">
                <input
                  type="checkbox"
                  checked={hideRepealed}
                  onChange={(event) =>
                    updateParams({ include_repealed: event.target.checked ? 'false' : null })
                  }
                />
                <span>过滤已废止</span>
              </label>
            </>
          ) : (
            <Select
              id="lib-published-filter"
              value={published}
              options={[
                { value: '', label: '全部发布情况' },
                { value: 'true', label: '已有发布版本' },
                { value: 'false', label: '尚未发布' },
              ]}
              onChange={(value) => updateParams({ published: value || null })}
            />
          )}

          <button type="submit" className="btn btn-primary btn-sm">
            筛选
          </button>
          {hasFilters && (
            <button
              type="button"
              className="text-button text-button-muted"
              onClick={() => setParams(view === 'manage' ? { view: 'manage' } : {})}
            >
              重置
            </button>
          )}
        </form>

        {facets.data && facets.data.categories.length > 0 && (
          <div className="lib-facet-row" role="group" aria-label="按主题分类筛选">
            <span className="lib-facet-label">分类</span>
            <button
              type="button"
              className={`lib-chip${!category ? ' active' : ''}`}
              onClick={() => updateParams({ category: null })}
            >
              全部
            </button>
            {facets.data.categories.map((item) => (
              <button
                key={item.name}
                type="button"
                className={`lib-chip${category === item.name ? ' active' : ''}`}
                onClick={() =>
                  updateParams({ category: category === item.name ? null : item.name })
                }
              >
                {item.name}
                <small>{item.count}</small>
              </button>
            ))}
          </div>
        )}

        {facets.data && facets.data.tags.length > 0 && (
          <div className="lib-facet-row" role="group" aria-label="按知识点标签筛选">
            <span className="lib-facet-label">
              <Tag size={12} aria-hidden="true" />
              标签
            </span>
            {facets.data.tags.slice(0, 24).map((item) => (
              <button
                key={item.name}
                type="button"
                className={`lib-chip lib-chip-tag${tag === item.name ? ' active' : ''}`}
                onClick={() => updateParams({ tag: tag === item.name ? null : item.name })}
              >
                #{item.name}
                <small>{item.count}</small>
              </button>
            ))}
          </div>
        )}
      </section>

      {Boolean(actionError) && (
        <Notice kind="error" error={actionError}>
          {errorMessage(actionError)}
        </Notice>
      )}

      {view === 'published' ? (
        <>
          {publishedList.loading && <LoadingState>正在加载制度库…</LoadingState>}
          {Boolean(publishedList.error) && (
            <LoadFailure error={publishedList.error} onRetry={publishedList.reload} />
          )}
          {publishedList.data && (
            <section className="lib-list-section" aria-label="已发布制度列表">
              {publishedList.data.items.length === 0 ? (
                <div className="fav-empty">
                  <div className="empty-symbol">
                    <BookOpen size={22} strokeWidth={1.6} aria-hidden="true" />
                  </div>
                  <h3>{hasFilters ? '没有符合条件的制度' : '知识库中暂无已发布制度'}</h3>
                  <p>
                    {hasFilters
                      ? '可以放宽分类、标签或效力状态条件后重试。'
                      : isAdmin
                        ? '点击右上角「导入文档」上传并发布制度后，会立即进入知识库。'
                        : '管理员发布金融制度后，会在这里按分类与标签展示。'}
                  </p>
                </div>
              ) : (
                <ol className="lib-cards">
                  {publishedList.data.items.map((doc, index) => (
                    <li key={doc.id} className="lib-card">
                      <div className="lib-card-head">
                        <span className="search-rank">
                          {String(
                            (publishedList.data!.page - 1) * publishedList.data!.page_size +
                              index +
                              1,
                          ).padStart(2, '0')}
                        </span>
                        <div className="lib-card-title">
                          <h2>{doc.title}</h2>
                          <div className="lib-card-sub">
                            {doc.category && <span className="lib-cat-pill">{doc.category}</span>}
                            {doc.issuer && <span>{doc.issuer}</span>}
                            {doc.version.document_number && (
                              <span>{doc.version.document_number}</span>
                            )}
                            {doc.version.version_note && <span>{doc.version.version_note}</span>}
                          </div>
                        </div>
                        <ValidityTag status={doc.version.validity_status} />
                      </div>

                      {doc.tags.length > 0 && (
                        <div className="lib-card-tags">
                          {doc.tags.map((item) => (
                            <button
                              key={item}
                              type="button"
                              className={`lib-tag-pill${tag === item ? ' active' : ''}`}
                              onClick={() => updateParams({ tag: tag === item ? null : item })}
                            >
                              #{item}
                            </button>
                          ))}
                        </div>
                      )}

                      <div className="lib-card-foot">
                        <div className="lib-card-meta">
                          <span>{doc.version.chunk_count} 条切片</span>
                          {doc.version.effective_date && (
                            <span>生效日期：{formatDate(doc.version.effective_date)}</span>
                          )}
                          {doc.version.source_url && (
                            <a
                              href={doc.version.source_url}
                              target="_blank"
                              rel="noreferrer noopener"
                            >
                              发布依据 <ExternalLink size={11} aria-hidden="true" />
                            </a>
                          )}
                        </div>
                        <div className="lib-card-actions">
                          {isAdmin && (
                            <>
                              <button
                                type="button"
                                className="text-button"
                                onClick={() => setEditing(doc)}
                              >
                                <Pencil size={12} aria-hidden="true" />
                                分类标签
                              </button>
                              <Link
                                className="text-button"
                                to={`/admin/documents/${doc.id}/versions`}
                              >
                                版本管理
                              </Link>
                            </>
                          )}
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() =>
                              navigate(
                                `/search?document_id=${encodeURIComponent(doc.id)}&title=${encodeURIComponent(doc.title)}`,
                              )
                            }
                          >
                            限定检索
                            <ArrowUpRight size={13} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            onClick={() => setReading(doc)}
                          >
                            <BookOpen size={13} aria-hidden="true" />
                            阅读条文
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <Pagination
                total={publishedList.data.total}
                page={publishedList.data.page}
                pageSize={publishedList.data.page_size}
                unit="份制度"
                onPage={goTo}
              />
            </section>
          )}
        </>
      ) : (
        <>
          {manageList.loading && <LoadingState>正在加载全部制度…</LoadingState>}
          {Boolean(manageList.error) && (
            <LoadFailure error={manageList.error} onRetry={manageList.reload} />
          )}
          {manageList.data && (
            <section className="lib-list-section" aria-label="全部制度管理列表">
              {manageList.data.items.length === 0 ? (
                <div className="fav-empty">
                  <div className="empty-symbol">
                    <Layers size={22} strokeWidth={1.6} aria-hidden="true" />
                  </div>
                  <h3>{hasFilters ? '没有符合条件的制度' : '还没有导入任何制度'}</h3>
                  <p>点击右上角「导入文档」开始构建金融制度知识库。</p>
                </div>
              ) : (
                <ol className="lib-cards">
                  {manageList.data.items.map((doc, index) => (
                    <li key={doc.id} className="lib-card">
                      <div className="lib-card-head">
                        <span className="search-rank">
                          {String(
                            (manageList.data!.page - 1) * manageList.data!.page_size + index + 1,
                          ).padStart(2, '0')}
                        </span>
                        <div className="lib-card-title">
                          <h2>{doc.title}</h2>
                          <div className="lib-card-sub">
                            {doc.category && <span className="lib-cat-pill">{doc.category}</span>}
                            <span>{doc.issuer || '未填写发文机构'}</span>
                            <span>{doc.version_count} 个版本</span>
                            <span>更新于 {formatDateTime(doc.updated_at)}</span>
                          </div>
                        </div>
                        <div className="lib-manage-statuses">
                          {doc.published_version ? (
                            <ValidityTag status={doc.published_version.validity_status} />
                          ) : (
                            <span className="tag tag-neutral">未发布</span>
                          )}
                          {doc.latest_version && (
                            <VersionStatusTag status={doc.latest_version.status} />
                          )}
                        </div>
                      </div>

                      {doc.tags && doc.tags.length > 0 && (
                        <div className="lib-card-tags">
                          {doc.tags.map((item) => (
                            <button
                              key={item}
                              type="button"
                              className={`lib-tag-pill${tag === item ? ' active' : ''}`}
                              onClick={() => updateParams({ tag: tag === item ? null : item })}
                            >
                              #{item}
                            </button>
                          ))}
                        </div>
                      )}

                      <div className="lib-card-foot">
                        <div className="lib-card-meta">
                          <span>
                            当前发布：
                            {doc.published_version
                              ? versionName(doc.published_version)
                              : '尚未发布'}
                          </span>
                        </div>
                        <div className="lib-card-actions">
                          <button
                            type="button"
                            className="text-button"
                            onClick={() => setEditing(doc)}
                          >
                            <Pencil size={12} aria-hidden="true" />
                            分类标签
                          </button>
                          <Link
                            className="text-button"
                            to={`/admin/imports/new?document_id=${doc.id}`}
                          >
                            <Upload size={12} aria-hidden="true" />
                            上传新版本
                          </Link>
                          {doc.published_version && (
                            <button
                              type="button"
                              className="btn btn-sm"
                              disabled={readingLoadingId === doc.id}
                              onClick={() => void openManageDocReader(doc)}
                            >
                              <BookOpen size={13} aria-hidden="true" />
                              阅读条文
                            </button>
                          )}
                          <Link
                            className="btn btn-sm btn-primary"
                            to={`/admin/documents/${doc.id}/versions`}
                          >
                            版本管理
                          </Link>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <Pagination
                total={manageList.data.total}
                page={manageList.data.page}
                pageSize={manageList.data.page_size}
                unit="份制度"
                onPage={goTo}
              />
            </section>
          )}
        </>
      )}

      {reading && <ClauseReaderDialog document={reading} onClose={() => setReading(null)} />}
      {editing && (
        <EditDocumentDialog
          document={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            reloadAll()
          }}
        />
      )}
    </>
  )
}
