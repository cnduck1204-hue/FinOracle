import { useState, type FormEvent } from 'react'
import { Check, Copy, Info, LoaderCircle, LogOut, Mail, ShieldCheck, UserRound } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Notice } from '../components/Notice'
import { ApiError, errorMessage } from '../lib/api'
import type { ProfileInput } from '../lib/types'
import { validateProfile, type FieldErrors } from '../lib/validation'

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

export function AccountPage() {
  const { user, client, expiresAt } = useAuth()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const [editing, setEditing] = useState(false)
  const [profile, setProfile] = useState<ProfileInput>({ username: '', email: '' })
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [profilePending, setProfilePending] = useState(false)
  const [profileError, setProfileError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  if (!user) return null
  const changed =
    profile.username.trim() !== user.username ||
    profile.email.trim().toLowerCase() !== user.email.toLowerCase()

  function editProfile() {
    setProfile({ username: user!.username, email: user!.email })
    setFieldErrors({})
    setProfileError(null)
    setSaved(false)
    setEditing(true)
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault()
    if (profilePending || pending || !changed || !user) return
    const errors = validateProfile(profile)
    setFieldErrors(errors)
    setProfileError(null)
    if (Object.keys(errors).length) {
      document.getElementById(`profile-${Object.keys(errors)[0]}`)?.focus()
      return
    }
    const input: Partial<ProfileInput> = {}
    if (profile.username.trim() !== user.username) input.username = profile.username.trim()
    if (profile.email.trim().toLowerCase() !== user.email.toLowerCase())
      input.email = profile.email.trim()
    setProfilePending(true)
    try {
      await client.updateProfile(input)
      setEditing(false)
      setSaved(true)
    } catch (caught) {
      setProfileError(caught)
      if (caught instanceof ApiError && caught.code === 'USER_USERNAME_EXISTS')
        setFieldErrors({ username: '该用户名已被使用，请换一个。' })
      if (caught instanceof ApiError && caught.code === 'USER_EMAIL_EXISTS')
        setFieldErrors({ email: '该邮箱已被使用，请换一个。' })
    } finally {
      setProfilePending(false)
    }
  }

  async function logout() {
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await client.logout()
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
          <p>查看和修改你的个人资料，管理账号登录状态。</p>
        </div>
        <span className="page-index">ACCOUNT / 01</span>
      </header>
      <div className="account-grid">
        <section className="profile-section" aria-labelledby="profile-heading">
          <div className="profile-heading">
            <h2 id="profile-heading">
              <UserRound size={17} strokeWidth={1.6} />
              个人资料
            </h2>
            {!editing && (
              <button type="button" className="cut-button" disabled={pending} onClick={editProfile}>
                编辑资料
              </button>
            )}
          </div>
          <form onSubmit={(event) => void saveProfile(event)} noValidate>
            <dl>
              <div className="profile-row">
                <dt>
                  <small>01</small>
                  {editing ? <label htmlFor="profile-username">用户名</label> : '用户名'}
                </dt>
                <dd className={editing ? 'form-field' : undefined}>
                  {editing ? (
                    <>
                      <div className="input-wrap">
                        <UserRound size={15} aria-hidden="true" />
                        <input
                          id="profile-username"
                          name="username"
                          autoComplete="username"
                          maxLength={32}
                          value={profile.username}
                          onChange={(event) =>
                            setProfile({ ...profile, username: event.target.value })
                          }
                          disabled={profilePending}
                          aria-invalid={Boolean(fieldErrors.username)}
                          aria-describedby={
                            fieldErrors.username ? 'profile-username-error' : undefined
                          }
                          autoFocus
                        />
                      </div>
                      {fieldErrors.username && (
                        <span className="field-error" id="profile-username-error">
                          {fieldErrors.username}
                        </span>
                      )}
                    </>
                  ) : (
                    user.username
                  )}
                </dd>
              </div>
              <div className="profile-row">
                <dt>
                  <small>02</small>
                  {editing ? <label htmlFor="profile-email">邮箱</label> : '邮箱'}
                </dt>
                <dd className={editing ? 'form-field' : undefined}>
                  {editing ? (
                    <>
                      <div className="input-wrap">
                        <Mail size={15} aria-hidden="true" />
                        <input
                          id="profile-email"
                          name="email"
                          type="email"
                          autoComplete="email"
                          maxLength={254}
                          value={profile.email}
                          onChange={(event) =>
                            setProfile({ ...profile, email: event.target.value })
                          }
                          disabled={profilePending}
                          aria-invalid={Boolean(fieldErrors.email)}
                          aria-describedby={fieldErrors.email ? 'profile-email-error' : undefined}
                        />
                      </div>
                      {fieldErrors.email && (
                        <span className="field-error" id="profile-email-error">
                          {fieldErrors.email}
                        </span>
                      )}
                    </>
                  ) : (
                    user.email
                  )}
                </dd>
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
                    type="button"
                    className="icon-button"
                    aria-label={copied ? '已复制账号 ID' : '复制账号 ID'}
                    onClick={() => void copyId()}
                  >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                  </button>
                </dd>
              </div>
            </dl>
            {editing && (
              <div className="profile-actions">
                <button
                  type="submit"
                  className="cut-button button-primary"
                  disabled={profilePending || pending || !changed}
                >
                  {profilePending && <LoaderCircle size={14} className="spinner" />}
                  {profilePending ? '正在保存…' : '保存修改'}
                </button>
                <button
                  type="button"
                  className="cut-button"
                  disabled={profilePending}
                  onClick={() => {
                    setEditing(false)
                    setProfileError(null)
                    setFieldErrors({})
                  }}
                >
                  取消
                </button>
              </div>
            )}
            {Boolean(profileError) && (
              <Notice kind="error" error={profileError}>
                {errorMessage(profileError)}
              </Notice>
            )}
            {saved && <Notice kind="success">个人资料已更新。</Notice>}
          </form>
          {copyError && (
            <p className="field-error" role="status">
              {copyError}
            </p>
          )}
          <p className="profile-tip">
            <Info size={13} aria-hidden="true" />
            可修改用户名和邮箱，更新后请使用新的用户名或邮箱登录。
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
            disabled={pending || profilePending}
            onClick={() => void logout()}
          >
            {pending ? <LoaderCircle size={14} className="spinner" /> : <LogOut size={14} />}
            退出登录
          </button>
          {Boolean(error) && (
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
          <p>在公共设备上使用后，请退出登录，保护你的账号信息。</p>
        </div>
      </div>
    </>
  )
}
