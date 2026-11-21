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
import { formatDateTime } from '../lib/dates'
import { JOB_KIND_LABELS, JOB_STATUS_LABELS, isJobActive } from '../lib/ingestion'
import type { ImportJob, JobKind, JobStatus } from '../lib/types'
import { useResource } from '../lib/useResource'

const PAGE_SIZE = 20

function groupJobsByVersion(jobs: ImportJob[]) {
  const groups = new Map<string, ImportJob[]>()
  for (const job of jobs) {
    const group = groups.get(job.version_id)
    if (group) group.push(job)
    else groups.set(job.version_id, [job])
  }
  return [...groups.values()]
}

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
        <div className="page-heading-text">
          <span className="page-kicker">Import Pipeline</span>
          <h1>导入任务</h1>
          <p>同一页内按文档版本汇总处理阶段，可查看校验或重试失败的任务。</p>
        </div>
        <Link className="btn btn-primary" to="/admin/imports/new">
          <Plus size={15} aria-hidden="true" />
          导入文档
        </Link>
      </header>

      <form className="admin-filter-form" onSubmit={filter} aria-label="任务筛选">
        <fieldset className="admin-filters jobs-filters">
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
            <div className="import-list-heading" aria-hidden="true">
              <span>序号</span>
              <span>文件 / 制度</span>
              <span>处理阶段</span>
            </div>
            <ol className="import-list" role="list" aria-label="导入任务列表">
              {groupJobsByVersion(data.items).map((jobs, index) => {
                const [latest] = jobs
                return (
                  <li key={latest.version_id} className="import-row">
                    <span className="import-row-index" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <div className="import-file">
                      <strong>{latest.file_name}</strong>
                      <small>{latest.document_title}</small>
                    </div>
                    {/* 接口按时间倒序返回，组内按处理顺序展示。 */}
                    <ol className="import-stages" role="list">
                      {jobs
                        .slice()
                        .reverse()
                        .map((job) => (
                          <li key={job.id} data-status={job.status}>
                            <div className="import-stage">
                              <div>
                                <strong>{JOB_KIND_LABELS[job.kind]}</strong>
                                {job.ingest && <small>{job.ingest}</small>}
                              </div>
                              <JobStatusTag status={job.status} />
                              {(job.stage ||
                                job.progress_total !== null ||
                                job.status === 'running') && <JobProgress job={job} />}
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
                            </div>
                            <small className="import-stage-meta">
                              <time dateTime={job.created_at}>
                                {formatDateTime(job.created_at)}
                              </time>
                              <span>
                                尝试 {job.attempts} / {job.max_attempts}
                              </span>
                            </small>
                            {job.error_message && (
                              <span className="job-error">{job.error_message}</span>
                            )}
                          </li>
                        ))}
                    </ol>
                  </li>
                )
              })}
            </ol>
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
