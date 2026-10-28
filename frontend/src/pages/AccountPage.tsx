import { useEffect, useRef, useState } from 'react'
import { Check, Copy, Info, LoaderCircle, LogOut, ShieldCheck, UserRound, X } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Notice } from '../components/Notice'
import { errorMessage } from '../lib/api'

function dateTime(value: string | number | null) {
  if (value === null) return '尚无记录'
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

function LogoutAllDialog({
  pending,
  error,
  onClose,
  onConfirm,
}: {
  pending: boolean
  error: unknown
  onClose: () => void
  onConfirm: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    dialog.current?.showModal()
    return () => dialog.current?.close()
  }, [])
  return (
    <dialog
      className="confirm-dialog"
      ref={dialog}
      aria-labelledby="logout-all-title"
      aria-describedby="logout-all-description"
      onCancel={(event) => {
        if (pending) event.preventDefault()
        else onClose()
      }}
    >
      <div className="dialog-header">
        <span className="eyebrow">// SESSION MANAGEMENT</span>
        <button
          className="icon-button"
          aria-label="关闭确认窗口"
          disabled={pending}
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      <div className="dialog-body">
        <h2 id="logout-all-title">退出全部登录会话？</h2>
        <p id="logout-all-description">
          这会退出该账号在所有设备上的登录，包括当前页面。账号和个人资料会保留，下次使用时需要重新登录。
        </p>
      </div>
      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}
      <div className="dialog-actions">
        <button
          autoFocus
          className="cut-button button-secondary"
          disabled={pending}
          onClick={onClose}
        >
          暂不退出
        </button>
        <button className="cut-button button-dark" disabled={pending} onClick={onConfirm}>
          {pending && <LoaderCircle size={14} className="spinner" />}
          {pending ? '正在退出' : '确认全部退出'}
        </button>
      </div>
    </dialog>
  )
}

export function AccountPage() {
  const { user, client, expiresAt } = useAuth()
  const [confirmAll, setConfirmAll] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  if (!user) return null

  async function logout(all: boolean) {
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await client.logout(all)
    } catch (caught) {
      setError(caught)
    } finally {
      setPending(false)
    }
  }
  async function copyId() {
    try {
      await navigator.clipboard.writeText(user!.id)
      setCopied(true)
      setCopyError('')
    } catch {
      setCopyError('未能复制，请选中账号 ID 后手动复制。')
    }
  }

  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">// YOUR ACCOUNT</span>
          <h1>我的账号</h1>
          <p>查看你的个人资料，管理账号登录状态。</p>
        </div>
        <span className="page-index">ACCOUNT / 01</span>
      </header>
      <div className="account-grid">
        <section className="profile-section" aria-labelledby="profile-heading">
          <h2 id="profile-heading">
            <UserRound size={17} strokeWidth={1.6} />
            个人资料
          </h2>
          <dl>
            <div className="profile-row">
              <dt>
                <small>01</small>用户名
              </dt>
              <dd>{user.username}</dd>
            </div>
            <div className="profile-row">
              <dt>
                <small>02</small>邮箱
              </dt>
              <dd>{user.email}</dd>
            </div>
            <div className="profile-row">
              <dt>
                <small>03</small>账号角色
              </dt>
              <dd>{user.role === 'admin' ? '管理员' : '普通用户'}</dd>
            </div>
            <div className="profile-row">
              <dt>
                <small>04</small>账号状态
              </dt>
              <dd>
                <Check size={12} style={{ marginRight: 5 }} aria-hidden="true" />
                {user.status === 'active' ? '已启用' : '已禁用'}
              </dd>
            </div>
            <div className="profile-row">
              <dt>
                <small>05</small>注册时间
              </dt>
              <dd>{dateTime(user.created_at)}</dd>
            </div>
            <div className="profile-row">
              <dt>
                <small>06</small>账号 ID
              </dt>
              <dd className="profile-id">
                <span>{user.id}</span>
                <button
                  className="icon-button"
                  aria-label={copied ? '已复制账号 ID' : '复制账号 ID'}
                  onClick={() => void copyId()}
                >
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
              </dd>
            </div>
          </dl>
          {copyError && (
            <p className="field-error" role="status">
              {copyError}
            </p>
          )}
          <p className="profile-tip">
            <Info size={13} aria-hidden="true" />
            个人资料暂为只读。资料编辑功能开放后，可在这里更新用户名和邮箱。
          </p>
        </section>
        <section className="session-panel" aria-labelledby="session-heading">
          <span className="eyebrow">// VERIFIED SESSION</span>
          <h2 id="session-heading">你已登录 FinOracle。</h2>
          <p>
            此标签页已连接你的个人账号。
            <br />
            使用期间会自动续期，保持当前登录。
          </p>
          <dl>
            <div>
              <dt>最近登录</dt>
              <dd>{dateTime(user.last_login_at)}</dd>
            </div>
            <div>
              <dt>本次连接有效至</dt>
              <dd>{dateTime(expiresAt)}</dd>
            </div>
            <div>
              <dt>账号验证</dt>
              <dd>
                <ShieldCheck size={10} style={{ marginRight: 5 }} aria-hidden="true" />
                已通过
              </dd>
            </div>
          </dl>
          <button
            className="cut-button button-primary"
            disabled={pending}
            onClick={() => void logout(false)}
          >
            {pending && !confirmAll ? (
              <LoaderCircle size={14} className="spinner" />
            ) : (
              <LogOut size={14} />
            )}
            退出当前会话
          </button>
          <button
            className="cut-button session-secondary"
            disabled={pending}
            onClick={() => {
              setError(null)
              setConfirmAll(true)
            }}
          >
            退出全部会话
          </button>
          {Boolean(error) && !confirmAll && (
            <Notice kind="error" error={error}>
              {errorMessage(error)}
            </Notice>
          )}
        </section>
      </div>
      <div className="account-footnote">
        <ShieldCheck size={20} strokeWidth={1.4} aria-hidden="true" />
        <div>
          <h3>让账号始终由你掌控</h3>
          <p>
            在公共设备上使用后，建议退出当前会话。若需要同时退出其他设备，可选择「退出全部会话」。
          </p>
        </div>
      </div>
      {confirmAll && (
        <LogoutAllDialog
          pending={pending}
          error={error}
          onClose={() => {
            if (!pending) {
              setConfirmAll(false)
              setError(null)
            }
          }}
          onConfirm={() => void logout(true)}
        />
      )}
    </>
  )
}
