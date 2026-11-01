import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  RotateCw,
  Search,
  UsersRound,
  X,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Notice } from '../components/Notice'
import { errorMessage } from '../lib/api'
import type { User, UserListQuery, UserPage } from '../lib/types'

const initialFilters = {
  q: '',
  role: '' as '' | User['role'],
  status: '' as '' | User['status'],
  page_size: 20,
}
const initialQuery = { page: 1, page_size: 20 }

function dateTime(value: string | null) {
  if (!value) return '尚无记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '暂无记录'
    : new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date)
}

function Status({ value }: { value: User['status'] }) {
  return (
    <span className={`user-status user-status-${value}`}>
      {value === 'active' ? '已启用' : '已禁用'}
    </span>
  )
}

export function AdminUsersPage() {
  const { client, user } = useAuth()
  const [filters, setFilters] = useState(initialFilters)
  const [query, setQuery] = useState<UserListQuery>(initialQuery)
  const [revision, setRevision] = useState(0)
  const [result, setResult] = useState<UserPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [detail, setDetail] = useState<User | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<unknown>(null)
  const [detailRevision, setDetailRevision] = useState(0)
  const [target, setTarget] = useState<User | null>(null)
  const [updating, setUpdating] = useState(false)
  const [updateError, setUpdateError] = useState<unknown>(null)
  const [notice, setNotice] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const confirmationTrigger = useRef<HTMLButtonElement | null>(null)
  const listHeading = useRef<HTMLHeadingElement>(null)
  const pages = result ? Math.max(1, Math.ceil(result.total / result.page_size)) : 1
  const nextStatus = target?.status === 'active' ? 'disabled' : 'active'
  const action = nextStatus === 'disabled' ? '禁用' : '启用'

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    setResult(null)
    client
      .listUsers(query)
      .then((value) => {
        if (!active) return
        const lastPage = Math.max(1, Math.ceil(value.total / value.page_size))
        if (value.page > lastPage) setQuery({ ...query, page: lastPage })
        else setResult(value)
      })
      .catch((caught) => {
        if (active) setError(caught)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [client, query, revision])

  useEffect(() => {
    let active = true
    setDetail(null)
    setDetailError(null)
    setDetailLoading(Boolean(detailId))
    if (detailId)
      client
        .getUser(detailId)
        .then((value) => {
          if (active) setDetail(value)
        })
        .catch((caught) => {
          if (active) setDetailError(caught)
        })
        .finally(() => {
          if (active) setDetailLoading(false)
        })
    return () => {
      active = false
    }
  }, [client, detailId, revision, detailRevision])

  useEffect(() => {
    if (target) dialog.current?.showModal()
  }, [target])

  function search(event: FormEvent) {
    event.preventDefault()
    setQuery({
      page: 1,
      page_size: filters.page_size,
      q: filters.q.trim(),
      role: filters.role || undefined,
      status: filters.status || undefined,
    })
    setDetailId(null)
    setNotice('')
  }

  function confirmStatus(value: User, trigger: HTMLButtonElement) {
    confirmationTrigger.current = trigger
    setTarget(value)
    setUpdateError(null)
  }

  function closeConfirmation(returnToList = false) {
    dialog.current?.close()
    setTarget(null)
    const trigger = confirmationTrigger.current
    const focusTarget = !returnToList && trigger?.isConnected ? trigger : listHeading.current
    focusTarget?.focus()
  }

  async function updateStatus() {
    if (!target || target.role === 'admin' || updating) return
    setUpdating(true)
    setUpdateError(null)
    try {
      await client.setUserStatus(target.id, nextStatus)
      setNotice(`已${action}用户 ${target.username}。`)
      closeConfirmation(true)
      setRevision((value) => value + 1)
    } catch (caught) {
      setUpdateError(caught)
    } finally {
      setUpdating(false)
    }
  }

  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">// USER ADMINISTRATION</span>
          <h1>用户管理</h1>
          <p>查询账号资料，管理普通用户的启用状态。</p>
        </div>
        <span className="page-index">ADMIN / 01</span>
      </header>

      <form className="admin-filter-form" onSubmit={search} aria-label="用户筛选">
        <fieldset className="admin-filters" disabled={updating}>
          <div className="admin-search">
            <label htmlFor="admin-search">用户名或邮箱搜索</label>
            <div className="input-wrap">
              <Search size={15} aria-hidden="true" />
              <input
                id="admin-search"
                type="search"
                maxLength={254}
                value={filters.q}
                placeholder="输入用户名或邮箱"
                onChange={(event) => setFilters({ ...filters, q: event.target.value })}
              />
            </div>
          </div>
          <div>
            <label htmlFor="admin-role">账号角色</label>
            <select
              id="admin-role"
              value={filters.role}
              onChange={(event) =>
                setFilters({ ...filters, role: event.target.value as typeof filters.role })
              }
            >
              <option value="">全部角色</option>
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
          </div>
          <div>
            <label htmlFor="admin-status">账号状态</label>
            <select
              id="admin-status"
              value={filters.status}
              onChange={(event) =>
                setFilters({ ...filters, status: event.target.value as typeof filters.status })
              }
            >
              <option value="">全部状态</option>
              <option value="active">已启用</option>
              <option value="disabled">已禁用</option>
            </select>
          </div>
          <div>
            <label htmlFor="admin-page-size">每页条数</label>
            <select
              id="admin-page-size"
              value={filters.page_size}
              onChange={(event) =>
                setFilters({ ...filters, page_size: Number(event.target.value) })
              }
            >
              {[10, 20, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {size} 条
                </option>
              ))}
            </select>
          </div>
          <div className="admin-filter-actions">
            <button className="cut-button button-dark" type="submit">
              查询
            </button>
            <button
              className="text-button"
              type="button"
              onClick={() => {
                setFilters(initialFilters)
                setQuery({ ...initialQuery })
                setDetailId(null)
                setNotice('')
              }}
            >
              重置
            </button>
          </div>
        </fieldset>
      </form>

      {notice && <Notice kind="success">{notice}</Notice>}
      <section className="admin-list" aria-labelledby="user-list-heading">
        <div className="admin-section-heading">
          <h2 id="user-list-heading" ref={listHeading} tabIndex={-1}>
            <UsersRound size={17} aria-hidden="true" />
            账号列表
          </h2>
          <button
            type="button"
            className="text-button"
            disabled={loading || updating}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RotateCw size={13} aria-hidden="true" />
            刷新列表
          </button>
        </div>
        {loading && (
          <div className="admin-state" role="status">
            <LoaderCircle size={18} className="spinner" />
            正在加载用户…
          </div>
        )}
        {Boolean(error) && (
          <>
            <Notice kind="error" error={error}>
              {errorMessage(error)}
            </Notice>
            <button
              type="button"
              className="text-button admin-retry"
              onClick={() => setRevision((value) => value + 1)}
            >
              重试加载
            </button>
          </>
        )}
        {result && (
          <>
            <div className="admin-table-wrap" tabIndex={0} aria-label="用户列表，可横向滚动">
              <table className="admin-table">
                <caption className="sr-only">用户列表</caption>
                <thead>
                  <tr>
                    <th scope="col">用户名 / 邮箱</th>
                    <th scope="col">角色</th>
                    <th scope="col">状态</th>
                    <th scope="col">注册时间</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((value) => (
                    <tr key={value.id} aria-selected={value.id === detailId}>
                      <td>
                        <strong>{value.username}</strong>
                        {value.id === user?.id && <span className="admin-current">当前账号</span>}
                        <small>{value.email}</small>
                      </td>
                      <td>{value.role === 'admin' ? '管理员' : '普通用户'}</td>
                      <td>
                        <Status value={value.status} />
                      </td>
                      <td>{dateTime(value.created_at)}</td>
                      <td>
                        <div className="admin-row-actions">
                          <button
                            type="button"
                            className="text-button"
                            disabled={updating}
                            onClick={() => setDetailId(value.id)}
                          >
                            查看详情
                          </button>
                          {value.role === 'user' ? (
                            <button
                              type="button"
                              className={`text-button ${value.status === 'active' ? 'admin-disable' : ''}`}
                              disabled={updating}
                              onClick={(event) => confirmStatus(value, event.currentTarget)}
                            >
                              {value.status === 'active' ? '禁用' : '启用'}
                            </button>
                          ) : (
                            <span className="admin-protected">管理员账号受保护</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!result.items.length && <div className="admin-state">没有符合条件的用户。</div>}
            <div className="admin-pagination">
              <span>共 {result.total} 位用户</span>
              <div>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="上一页"
                  disabled={updating || result.page <= 1}
                  onClick={() => setQuery({ ...query, page: result.page - 1 })}
                >
                  <ChevronLeft size={16} />
                </button>
                <span>
                  第 {result.page} / {pages} 页
                </span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="下一页"
                  disabled={updating || result.page >= pages}
                  onClick={() => setQuery({ ...query, page: result.page + 1 })}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {detailId && (
        <section className="admin-detail" aria-labelledby="user-detail-heading">
          <div className="admin-section-heading">
            <h2 id="user-detail-heading">用户详情</h2>
            <button
              type="button"
              className="icon-button"
              aria-label="关闭详情"
              disabled={updating}
              onClick={() => setDetailId(null)}
            >
              <X size={16} />
            </button>
          </div>
          {detailLoading && (
            <div className="admin-state" role="status">
              <LoaderCircle size={18} className="spinner" />
              正在加载详情…
            </div>
          )}
          {Boolean(detailError) && (
            <>
              <Notice kind="error" error={detailError}>
                {errorMessage(detailError)}
              </Notice>
              <button
                type="button"
                className="text-button admin-retry"
                onClick={() => setDetailRevision((value) => value + 1)}
              >
                重试详情
              </button>
            </>
          )}
          {detail && (
            <>
              <dl className="admin-detail-grid">
                <div>
                  <dt>用户名</dt>
                  <dd>{detail.username}</dd>
                </div>
                <div>
                  <dt>邮箱</dt>
                  <dd>{detail.email}</dd>
                </div>
                <div>
                  <dt>账号角色</dt>
                  <dd>{detail.role === 'admin' ? '管理员' : '普通用户'}</dd>
                </div>
                <div>
                  <dt>账号状态</dt>
                  <dd>
                    <Status value={detail.status} />
                  </dd>
                </div>
                <div>
                  <dt>注册时间</dt>
                  <dd>{dateTime(detail.created_at)}</dd>
                </div>
                <div>
                  <dt>最近登录</dt>
                  <dd>{dateTime(detail.last_login_at)}</dd>
                </div>
                <div>
                  <dt>更新时间</dt>
                  <dd>{dateTime(detail.updated_at)}</dd>
                </div>
                <div>
                  <dt>账号 ID</dt>
                  <dd className="admin-detail-id">{detail.id}</dd>
                </div>
              </dl>
              {detail.role === 'user' ? (
                <button
                  type="button"
                  className="cut-button button-dark"
                  disabled={updating}
                  onClick={(event) => confirmStatus(detail, event.currentTarget)}
                >
                  {detail.status === 'active' ? '禁用' : '启用'}
                </button>
              ) : (
                <p className="profile-tip">管理员账号受保护，无法通过此页面修改状态。</p>
              )}
            </>
          )}
        </section>
      )}

      {target && (
        <dialog
          ref={dialog}
          className="admin-confirm"
          aria-labelledby="admin-confirm-title"
          onCancel={(event) => {
            event.preventDefault()
            if (!updating) closeConfirmation()
          }}
        >
          <span className="eyebrow">// ACCOUNT STATUS</span>
          <h2 id="admin-confirm-title">确认{action}此用户？</h2>
          <p className="admin-confirm-name">{target.username}</p>
          <p>
            {nextStatus === 'disabled'
              ? '禁用后，该用户将无法登录，已有登录会失效。'
              : '启用后，该用户可以重新登录，旧登录不会恢复。'}
          </p>
          {Boolean(updateError) && (
            <Notice kind="error" error={updateError}>
              {errorMessage(updateError)}
            </Notice>
          )}
          <div className="profile-actions">
            <button
              type="button"
              className="cut-button button-dark"
              disabled={updating}
              onClick={() => void updateStatus()}
            >
              {updating && <LoaderCircle size={14} className="spinner" />}
              {updating ? '正在更新…' : `确认${action}`}
            </button>
            <button
              type="button"
              className="cut-button"
              disabled={updating}
              onClick={() => closeConfirmation()}
            >
              取消
            </button>
          </div>
        </dialog>
      )}
    </>
  )
}
