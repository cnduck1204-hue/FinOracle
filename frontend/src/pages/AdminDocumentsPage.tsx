import { useEffect, useState, type FormEvent } from 'react'
import { FileStack, Plus, RotateCw, Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import {
  LoadFailure,
  LoadingState,
  Pagination,
  ValidityTag,
  VersionStatusTag,
} from '../components/IngestionUi'
import { formatDateTime } from '../lib/dates'
import { isVersionBusy, versionName } from '../lib/ingestion'
import { useResource } from '../lib/useResource'

const PAGE_SIZE = 20

export function AdminDocumentsPage() {
  const { client } = useAuth()
  // 筛选放在网址中，从版本页返回时恢复原来的列表位置。
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const published = params.get('published') ?? ''
  const page = Math.max(1, Number(params.get('page')) || 1)
  const [text, setText] = useState(q)
  const [publishedFilter, setPublishedFilter] = useState(published)
  useEffect(() => {
    setText(q)
    setPublishedFilter(published)
  }, [q, published])

  const { data, error, loading, reload } = useResource(
    () =>
      client.listDocuments({
        page,
        page_size: PAGE_SIZE,
        q: q || undefined,
        published: published ? published === 'true' : undefined,
      }),
    [client, q, published, page],
    (value) =>
      value.items.some((item) => item.latest_version && isVersionBusy(item.latest_version.status)),
  )

  function search(event: FormEvent) {
    event.preventDefault()
    const next = new URLSearchParams()
    if (text.trim()) next.set('q', text.trim())
    if (publishedFilter) next.set('published', publishedFilter)
    setParams(next)
  }

  function goTo(nextPage: number) {
    const next = new URLSearchParams(params)
    next.set('page', String(nextPage))
    setParams(next)
  }

  return (
    <>
      <header className="page-heading">
        <div className="page-heading-text">
          <span className="page-kicker">Document Administration</span>
          <h1>制度文档</h1>
          <p>管理金融制度与历史版本。只有已发布的版本才会进入问答与全文检索。</p>
        </div>
        <Link className="btn btn-primary" to="/admin/imports/new">
          <Plus size={15} aria-hidden="true" />
          导入文档
        </Link>
      </header>

      <form className="admin-filter-form" onSubmit={search} aria-label="制度筛选">
        <fieldset className="admin-filters ingest-filters">
          <div className="admin-search">
            <label htmlFor="document-search">标题、发文机构或外部编号</label>
            <div className="input-wrap">
              <Search size={15} aria-hidden="true" />
              <input
                id="document-search"
                type="search"
                maxLength={255}
                value={text}
                placeholder="输入关键词"
                onChange={(event) => setText(event.target.value)}
              />
            </div>
          </div>
          <div>
            <label htmlFor="document-published">发布情况</label>
            <select
              id="document-published"
              value={publishedFilter}
              onChange={(event) => setPublishedFilter(event.target.value)}
            >
              <option value="">全部</option>
              <option value="true">已有发布版本</option>
              <option value="false">尚未发布</option>
            </select>
          </div>
          <div className="admin-filter-actions">
            <button className="btn btn-primary" type="submit">
              查询
            </button>
            <button
              type="button"
              className="text-button text-button-muted"
              onClick={() => setParams({})}
            >
              重置
            </button>
          </div>
        </fieldset>
      </form>

      <section className="admin-list" aria-labelledby="document-list-heading">
        <div className="admin-section-heading">
          <h2 id="document-list-heading">
            <FileStack size={17} aria-hidden="true" />
            制度列表
          </h2>
          <button type="button" className="text-button" onClick={reload}>
            <RotateCw size={13} aria-hidden="true" />
            刷新列表
          </button>
        </div>
        {loading && <LoadingState>正在加载制度…</LoadingState>}
        {Boolean(error) && <LoadFailure error={error} onRetry={reload} />}
        {data && (
          <>
            <div className="admin-table-wrap" tabIndex={0} aria-label="制度列表，可横向滚动">
              <table className="admin-table document-table">
                <caption className="sr-only">制度列表</caption>
                <thead>
                  <tr>
                    <th scope="col" className="row-index">
                      序号
                    </th>
                    <th scope="col">制度 / 发文机构</th>
                    <th scope="col">当前发布版本</th>
                    <th scope="col">最新版本</th>
                    <th scope="col">版本数</th>
                    <th scope="col">更新时间</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item, index) => (
                    <tr key={item.id}>
                      <td className="row-index">
                        {String((data.page - 1) * data.page_size + index + 1).padStart(2, '0')}
                      </td>
                      <td>
                        <strong>{item.title}</strong>
                        <small>{item.issuer || '未填写发文机构'}</small>
                      </td>
                      <td>
                        {item.published_version ? (
                          <>
                            <span>{versionName(item.published_version)}</span>
                            <div style={{ marginTop: 4 }}>
                              <ValidityTag status={item.published_version.validity_status} />
                            </div>
                          </>
                        ) : (
                          <small>尚未发布</small>
                        )}
                      </td>
                      <td>
                        {item.latest_version ? (
                          <VersionStatusTag status={item.latest_version.status} />
                        ) : (
                          <small>没有版本</small>
                        )}
                      </td>
                      <td>{item.version_count}</td>
                      <td>{formatDateTime(item.updated_at)}</td>
                      <td>
                        <div className="admin-row-actions">
                          <Link className="text-button" to={`/admin/documents/${item.id}/versions`}>
                            版本管理
                          </Link>
                          <Link
                            className="text-button"
                            to={`/admin/imports/new?document_id=${item.id}`}
                          >
                            上传新版本
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.items.length && (
              <div className="admin-state">
                {q || published ? '没有符合条件的制度。' : '还没有制度，先导入一份文档吧。'}
              </div>
            )}
            <Pagination
              total={data.total}
              page={data.page}
              pageSize={data.page_size}
              unit="份制度"
              onPage={goTo}
            />
          </>
        )}
      </section>
    </>
  )
}
