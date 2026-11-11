import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ExternalLink, FileSearch, LoaderCircle, Search } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { ValidityTag } from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { errorMessage } from '../lib/api'
import { articlePath, formatDate, pageLabel } from '../lib/ingestion'
import { useResource } from '../lib/useResource'
import type { SearchHit, SearchOutcome } from '../lib/types'

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

function Result({ hit, index }: { hit: SearchHit; index: number }) {
  const { document, version } = hit
  const channels = [
    hit.dense_rank !== null && `语义第 ${hit.dense_rank}`,
    hit.keyword_rank !== null && `关键词第 ${hit.keyword_rank}`,
    hit.visual_rank !== null && `视觉第 ${hit.visual_rank}`,
  ].filter(Boolean)
  return (
    <li className="search-result">
      <div className="search-result-head">
        <span className="search-rank">{String(index + 1).padStart(2, '0')}</span>
        <div>
          <h3>{document.title}</h3>
          <p>{hit.has_image ? '页面图片' : articlePath(hit)}</p>
        </div>
        <ValidityTag status={version.validity_status} />
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
      <span className="search-channels">{channels.join(' · ')}</span>
    </li>
  )
}

export function SearchPage() {
  const { client } = useAuth()
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(8)
  const pipelines = useResource(() => client.listPipelines(), [client])
  const [chosen, setChosen] = useState('')
  const [outcome, setOutcome] = useState<SearchOutcome | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const latest = useRef(0)
  const options = pipelines.data ?? []
  const selected = options.find((item) => item.name === chosen) ?? options.find((p) => p.default)
  const usedLabel = options.find((item) => item.name === outcome?.pipeline)?.label

  async function run() {
    const text = query.trim()
    if (!text) return
    const request = ++latest.current
    setSearching(true)
    setError(null)
    try {
      const result = await client.search({ query: text, top_k: topK, pipeline: selected?.name })
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
        <div>
          <span className="eyebrow">// FULL-TEXT SEARCH</span>
          <h1>全文检索</h1>
          <p>在已发布的制度中定位条文，结果保留版本、条号和页码。</p>
        </div>
        <span className="page-index">SEARCH / 01</span>
      </header>

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
                <select
                  id="search-pipeline"
                  value={selected.name}
                  onChange={(event) => setChosen(event.target.value)}
                >
                  {options.map((item) => (
                    <option key={item.name} value={item.name} disabled={!item.available}>
                      {item.label}
                      {item.available ? '' : '（不可用）'}
                    </option>
                  ))}
                </select>
              </>
            )}
            <label htmlFor="search-top-k">返回条数</label>
            <select
              id="search-top-k"
              value={topK}
              onChange={(event) => setTopK(Number(event.target.value))}
            >
              {[5, 8, 12, 20].map((value) => (
                <option key={value} value={value}>
                  {value} 条
                </option>
              ))}
            </select>
          </div>
          <button
            className="cut-button button-primary"
            type="submit"
            disabled={searching || !query.trim()}
          >
            {searching ? (
              <LoaderCircle size={14} className="spinner" />
            ) : (
              <Search size={14} aria-hidden="true" />
            )}
            {searching ? '正在检索…' : '开始检索'}
          </button>
        </div>
      </form>
      <p className="search-hint">
        {options.length > 1 && selected && <>{selected.description} </>}
        Enter 检索，Shift + Enter 换行。未发布的草稿不会出现在结果中。
      </p>

      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}

      {outcome && (
        <section className="admin-list" aria-labelledby="search-results-heading">
          <div className="admin-section-heading">
            <h2 id="search-results-heading">
              <FileSearch size={17} aria-hidden="true" />
              检索结果
            </h2>
            <span className="search-count" role="status">
              {usedLabel && `${usedLabel} · `}“{outcome.query}” 找到 {outcome.results.length} 条
            </span>
          </div>
          {outcome.results.length ? (
            <ol className="search-results">
              {outcome.results.map((hit, index) => (
                <Result key={hit.chunk_id} hit={hit} index={index} />
              ))}
            </ol>
          ) : (
            <div className="admin-state">
              没有找到相关条文。可以换一种说法，或确认相关制度已经发布
              {selected && !selected.default ? '，并已建立该方案的索引' : ''}。
            </div>
          )}
        </section>
      )}
    </>
  )
}
