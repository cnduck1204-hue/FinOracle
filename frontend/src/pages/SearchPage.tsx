import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ExternalLink, FileSearch, LoaderCircle, Search, Star, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { ValidityTag } from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { SaveFavoriteDialog, type SaveFavoriteTarget } from '../components/SaveFavoriteDialog'
import { Select } from '../components/Select'
import { errorMessage } from '../lib/api'
import { formatDate } from '../lib/dates'
import { articlePath, pageLabel } from '../lib/ingestion'
import { useResource } from '../lib/useResource'
import type { FavoriteSourceMatch, SearchHit, SearchOutcome } from '../lib/types'

const MAX_QUERY = 1000

function PageImage({ hit }: { hit: SearchHit }) {
  const { client } = useAuth()
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true
    let created: string | null = null
    setUrl(null)
    setFailed(false)
    client
      .fetchSearchImage(hit.chunk_id)
      .then((blob) => {
        if (!active) return
        created = URL.createObjectURL(blob)
        setUrl(created)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
      if (created) URL.revokeObjectURL(created)
    }
  }, [client, hit.chunk_id])
  if (failed) return <p className="search-content">页面图片暂时无法读取。</p>
  if (!url) return <div className="search-page-image search-page-loading">正在读取页面…</div>
  return (
    <a href={url} target="_blank" rel="noreferrer" aria-label="在新标签页查看页面原图">
      <img
        className="search-page-image"
        src={url}
        alt={`${hit.document.title} ${pageLabel(hit.page_start, hit.page_end)}`}
      />
    </a>
  )
}

function Result({
  hit,
  index,
  saved,
  busy,
  onFavorite,
  onUnfavorite,
}: {
  hit: SearchHit
  index: number
  saved: FavoriteSourceMatch | undefined
  busy: boolean
  onFavorite: (hit: SearchHit) => void
  onUnfavorite: (favoriteId: string, sourceKey: string) => void
}) {
  const { document, version } = hit
  const channels = [
    hit.dense_rank !== null && `语义第 ${hit.dense_rank}`,
    hit.keyword_rank !== null && `关键词第 ${hit.keyword_rank}`,
    hit.visual_rank !== null && `视觉第 ${hit.visual_rank}`,
  ].filter(Boolean)
  const sourceKey = `chunk:${hit.chunk_id}`
  return (
    <li className="search-result">
      <div className="search-result-head">
        <span className="search-rank">{String(index + 1).padStart(2, '0')}</span>
        <div className="search-result-title">
          <h3>{document.title}</h3>
          <p>{hit.has_image ? '页面图片' : articlePath(hit)}</p>
        </div>
        <div className="search-result-right">
          <ValidityTag status={version.validity_status} />
          <button
            type="button"
            className={`fav-action-btn${saved ? ' saved' : ''}`}
            disabled={busy}
            aria-pressed={Boolean(saved)}
            onClick={() => (saved ? onUnfavorite(saved.id, sourceKey) : onFavorite(hit))}
          >
            <Star size={13} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />
            {saved ? '已收藏' : '收藏'}
          </button>
        </div>
      </div>
      {hit.has_image ? <PageImage hit={hit} /> : <p className="search-content">{hit.content}</p>}
      <dl className="search-citation">
        <div>
          <dt>版本</dt>
          <dd>{version.version_note || '未填写版本说明'}</dd>
        </div>
        {version.document_number && (
          <div>
            <dt>文号</dt>
            <dd>{version.document_number}</dd>
          </div>
        )}
        {document.issuer && (
          <div>
            <dt>发文机构</dt>
            <dd>{document.issuer}</dd>
          </div>
        )}
        {version.effective_date && (
          <div>
            <dt>生效</dt>
            <dd>{formatDate(version.effective_date)}</dd>
          </div>
        )}
        <div>
          <dt>页码</dt>
          <dd>{pageLabel(hit.page_start, hit.page_end)}</dd>
        </div>
        {hit.modality === 'table' && (
          <div>
            <dt>内容类型</dt>
            <dd>表格</dd>
          </div>
        )}
        {version.source_url && (
          <div>
            <dt>来源</dt>
            <dd>
              <a href={version.source_url} target="_blank" rel="noreferrer noopener">
                查看发布依据 <ExternalLink size={11} aria-hidden="true" />
              </a>
            </dd>
          </div>
        )}
      </dl>
      {channels.length > 0 && <span className="search-channels">{channels.join(' · ')}</span>}
    </li>
  )
}

export function SearchPage() {
  const { client } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const scopedDocId = searchParams.get('document_id')
  const scopedDocTitle = searchParams.get('title')
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(8)
  const [includeRepealed, setIncludeRepealed] = useState(false)
  const pipelines = useResource(() => client.listPipelines(), [client])
  const [chosen, setChosen] = useState('')
  const [outcome, setOutcome] = useState<SearchOutcome | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [savedMap, setSavedMap] = useState<Record<string, FavoriteSourceMatch>>({})
  const [saveTarget, setSaveTarget] = useState<SaveFavoriteTarget | null>(null)
  const [favBusyKey, setFavBusyKey] = useState<string | null>(null)
  const latest = useRef(0)
  const options = pipelines.data ?? []
  const selected = options.find((item) => item.name === chosen) ?? options.find((p) => p.default)
  const usedLabel = options.find((item) => item.name === outcome?.pipeline)?.label

  useEffect(() => {
    const hits = outcome?.results ?? []
    if (!hits.length) {
      setSavedMap({})
      return
    }
    const controller = new AbortController()
    const keys = hits.map((hit) => `chunk:${hit.chunk_id}`)
    client
      .lookupFavorites(keys, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) setSavedMap(res.items)
      })
      .catch(() => {
        // Non-blocking: search results remain usable even if lookup fails.
      })
    return () => controller.abort()
  }, [client, outcome])

  async function handleUnfavorite(favoriteId: string, sourceKey: string) {
    if (favBusyKey) return
    setFavBusyKey(sourceKey)
    try {
      await client.deleteFavorite(favoriteId)
      setSavedMap((previous) => {
        const next = { ...previous }
        delete next[sourceKey]
        return next
      })
    } catch (caught) {
      setError(caught)
    } finally {
      setFavBusyKey(null)
    }
  }

  async function run() {
    const text = query.trim()
    if (!text) return
    const request = ++latest.current
    setSearching(true)
    setError(null)
    try {
      const result = await client.search({
        query: text,
        top_k: topK,
        pipeline: selected?.name,
        include_repealed: includeRepealed,
        ...(scopedDocId ? { document_ids: [scopedDocId] } : {}),
      })
      if (request === latest.current) setOutcome(result)
    } catch (caught) {
      if (request === latest.current) {
        setError(caught)
        setOutcome(null)
      }
    } finally {
      if (request === latest.current) setSearching(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // 输入法选词时的回车不触发检索。
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void run()
    }
  }

  return (
    <>
      <header className="page-heading">
        <div className="page-heading-text">
          <span className="page-kicker">Full-Text Search</span>
          <h1>全文检索</h1>
          <p>在已发布的制度中精确定位条文，默认过滤已废止制度并优先返回现行有效条文。</p>
        </div>
      </header>

      {scopedDocId && (
        <div className="search-scope-banner">
          <span>当前限定制度：{scopedDocTitle || scopedDocId}</span>
          <button type="button" className="text-button" onClick={() => setSearchParams({})}>
            <X size={13} aria-hidden="true" />
            清除限定
          </button>
        </div>
      )}

      <form className="composer search-composer" onSubmit={submit} role="search">
        <div className="composer-top">
          <label htmlFor="search-query">检索问题或关键词</label>
          <span>
            {query.length} / {MAX_QUERY}
          </span>
        </div>
        <textarea
          id="search-query"
          rows={2}
          maxLength={MAX_QUERY}
          value={query}
          placeholder="例如：理财产品销售有哪些要求"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-bottom">
          <div className="composer-scope">
            {options.length > 1 && selected && (
              <>
                <label htmlFor="search-pipeline">检索方案</label>
                <Select
                  id="search-pipeline"
                  size="sm"
                  inline
                  value={selected.name}
                  options={options.map((item) => ({
                    value: item.name,
                    label: `${item.label}${item.available ? '' : '（不可用）'}`,
                    disabled: !item.available,
                  }))}
                  onChange={setChosen}
                />
              </>
            )}
            <label htmlFor="search-top-k">返回条数</label>
            <Select
              id="search-top-k"
              size="sm"
              inline
              value={String(topK)}
              options={[5, 8, 12, 20].map((value) => ({
                value: String(value),
                label: `${value} 条`,
              }))}
              onChange={(next) => setTopK(Number(next))}
            />
            <label className="lib-toggle search-repealed-toggle">
              <input
                type="checkbox"
                checked={includeRepealed}
                onChange={(event) => setIncludeRepealed(event.target.checked)}
              />
              <span>包含已废止</span>
            </label>
          </div>
          <button className="btn btn-primary" type="submit" disabled={searching || !query.trim()}>
            {searching ? (
              <LoaderCircle size={15} className="spinner" />
            ) : (
              <Search size={15} aria-hidden="true" />
            )}
            {searching ? '正在检索…' : '开始检索'}
          </button>
        </div>
      </form>

      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}

      {outcome && (
        <section className="search-results-section" aria-labelledby="search-results-heading">
          <div className="search-summary">
            <h2 id="search-results-heading">
              <FileSearch size={17} aria-hidden="true" />
              检索结果（{outcome.results.length}）
            </h2>
            {usedLabel && <small>检索方案：{usedLabel}</small>}
          </div>
          {outcome.results.length ? (
            <ol className="search-list">
              {outcome.results.map((hit, index) => {
                const sourceKey = `chunk:${hit.chunk_id}`
                return (
                  <Result
                    key={hit.chunk_id}
                    hit={hit}
                    index={index}
                    saved={savedMap[sourceKey]}
                    busy={favBusyKey === sourceKey}
                    onFavorite={(targetHit) =>
                      setSaveTarget({
                        kind: 'chunk',
                        chunkId: targetHit.chunk_id,
                        defaultTitle: `${targetHit.document.title} · ${articlePath(targetHit)}`,
                      })
                    }
                    onUnfavorite={(favoriteId, key) => void handleUnfavorite(favoriteId, key)}
                  />
                )
              })}
            </ol>
          ) : (
            <div className="surface-card admin-state">
              没有在已发布的制度中找到匹配条文，请换一个关键词再试。
            </div>
          )}
        </section>
      )}

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
    </>
  )
}
