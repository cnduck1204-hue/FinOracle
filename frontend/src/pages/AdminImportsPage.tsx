import { useEffect, useState, type FormEvent } from 'react'
import { ListChecks, Plus, RotateCw } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import {
  JobProgress,
  JobStatusTag,
  LoadFailure,
  LoadingState,
  Pagination,
} from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import { errorMessage } from '../lib/api'
import { JOB_KIND_LABELS, JOB_STATUS_LABELS, formatDateTime, isJobActive } from '../lib/ingestion'
import type { JobKind, JobStatus } from '../lib/types'
import { useResource } from '../lib/useResource'

const PAGE_SIZE = 20

export function AdminImportsPage() {
  const { client } = useAuth()
  const [params, setParams] = useSearchParams()
  const status = (params.get('status') ?? '') as JobStatus | ''
  const kind = (params.get('kind') ?? '') as JobKind | ''
  const page = Math.max(1, Number(params.get('page')) || 1)
  const [statusFilter, setStatusFilter] = useState(status)
  const [kindFilter, setKindFilter] = useState(kind)
  const [notice, setNotice] = useState('')
  const [retryError, setRetryError] = useState<unknown>(null)
  const [retrying, setRetrying] = useState<string | null>(null)
  useEffect(() => {
    setStatusFilter(status)
    setKindFilter(kind)
  }, [status, kind])

  const { data, error, loading, reload } = useResource(
    () =>
      client.listJobs({
        page,
        page_size: PAGE_SIZE,
        status: status || undefined,
        kind: kind || undefined,
      }),
    [client, status, kind, page],
    (value) => value.items.some((job) => isJobActive(job.status)),
  )

  function filter(event: FormEvent) {
    event.preventDefault()
    const next = new URLSearchParams()
    if (statusFilter) next.set('status', statusFilter)
    if (kindFilter) next.set('kind', kindFilter)
    setParams(next)
  }

  function goTo(nextPage: number) {
    const next = new URLSearchParams(params)
    next.set('page', String(nextPage))
    setParams(next)
  }

  async function retry(id: string, name: string) {
    setRetrying(id)
    setRetryError(null)
    setNotice('')
    try {
      await client.retryJob(id)
      setNotice(`${name} 已重新排队。`)
      reload()
    } catch (caught) {
      setRetryError(caught)
    } finally {
      setRetrying(null)
    }
  }

  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">// IMPORT JOBS</span>
          <h1>导入任务</h1>
          <p>跟踪解析和向量化进度，失败的任务可以直接重试。</p>
        </div>
        <Link className="cut-button button-primary" to="/admin/imports/new">
          <Plus size={15} aria-hidden="true" />
          导入文档
        </Link>
      </header>

      <form className="admin-filter-form" onSubmit={filter} aria-label="任务筛选">
        <fieldset className="admin-filters ingest-filters ingest-filters-compact">
          <div>
            <label htmlFor="job-status">任务状态</label>
            <select
              id="job-status"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as JobStatus | '')}
            >
              <option value="">全部状态</option>
              {(Object.keys(JOB_STATUS_LABELS) as JobStatus[]).map((value) => (
                <option key={value} value={value}>
                  {JOB_STATUS_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="job-kind">任务类型</label>
            <select
              id="job-kind"
              value={kindFilter}
              onChange={(event) => setKindFilter(event.target.value as JobKind | '')}
            >
              <option value="">全部类型</option>
              <option value="parse">{JOB_KIND_LABELS.parse}</option>
              <option value="index">{JOB_KIND_LABELS.index}</option>
              <option value="pipeline">{JOB_KIND_LABELS.pipeline}</option>
            </select>
          </div>
          <div className="admin-filter-actions">
            <button className="cut-button button-dark" type="submit">
              查询
            </button>
            <button type="button" className="text-button" onClick={() => setParams({})}>
              重置
            </button>
          </div>
        </fieldset>
      </form>

      {notice && <Notice kind="success">{notice}</Notice>}
      {Boolean(retryError) && (
        <Notice kind="error" error={retryError}>
          {errorMessage(retryError)}
        </Notice>
      )}

      <section className="admin-list" aria-labelledby="job-list-heading">
        <div className="admin-section-heading">
          <h2 id="job-list-heading">
            <ListChecks size={17} aria-hidden="true" />
            任务列表
          </h2>
          <button type="button" className="text-button" onClick={reload}>
            <RotateCw size={13} aria-hidden="true" />
            刷新列表
          </button>
        </div>
        {loading && <LoadingState>正在加载任务…</LoadingState>}
        {Boolean(error) && !data && <LoadFailure error={error} onRetry={reload} />}
        {data && (
          <>
            <div className="admin-table-wrap" tabIndex={0} aria-label="任务列表，可横向滚动">
              <table className="admin-table">
                <caption className="sr-only">导入任务列表</caption>
                <thead>
                  <tr>
                    <th scope="col">文件 / 制度</th>
                    <th scope="col">类型</th>
                    <th scope="col">状态</th>
                    <th scope="col">阶段与进度</th>
                    <th scope="col">尝试</th>
                    <th scope="col">创建时间</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <strong>{job.file_name}</strong>
                        <small>{job.document_title}</small>
                      </td>
                      <td>{JOB_KIND_LABELS[job.kind]}</td>
                      <td>
                        <JobStatusTag status={job.status} />
                      </td>
                      <td>
                        <JobProgress job={job} />
                        {job.error_message && (
                          <span className="job-error">{job.error_message}</span>
                        )}
                      </td>
                      <td>
                        {job.attempts} / {job.max_attempts}
                      </td>
                      <td>{formatDateTime(job.created_at)}</td>
                      <td>
                        <div className="admin-row-actions">
                          <Link className="text-button" to={`/admin/imports/${job.id}`}>
                            查看校验
                          </Link>
                          {job.status === 'failed' && (
                            <button
                              type="button"
                              className="text-button"
                              disabled={retrying === job.id}
                              onClick={() => void retry(job.id, job.file_name)}
                            >
                              重试
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.items.length && <div className="admin-state">没有符合条件的任务。</div>}
            <Pagination
              total={data.total}
              page={data.page}
              pageSize={data.page_size}
              unit="个任务"
              onPage={goTo}
            />
          </>
        )}
      </section>
    </>
  )
}
