import { useState, type FormEvent } from 'react'
import { Check, Copy, Info, LoaderCircle, LogOut, Mail, ShieldCheck, UserRound } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Notice } from '../components/Notice'
import { ApiError, errorMessage } from '../lib/api'
import { formatDateTime } from '../lib/dates'
import type { ProfileInput } from '../lib/types'
import { validateProfile, type FieldErrors } from '../lib/validation'

export function AccountPage() {
  const { user, expiresAt, client } = useAuth()
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
        <div className="page-heading-text">
          <span className="page-kicker">Account & Session</span>
          <h1>我的账号</h1>
          <p>查看和修改你的个人资料，管理当前标签页的登录会话。</p>
        </div>
      </header>
      <div className="account-grid">
        <section className="profile-section" aria-labelledby="profile-heading">
          <div className="profile-heading">
            <h2 id="profile-heading">
              <UserRound size={17} strokeWidth={1.7} aria-hidden="true" />
              个人资料
            </h2>
            {!editing && (
              <button type="button" className="btn btn-sm" disabled={pending} onClick={editProfile}>
                编辑资料
              </button>
            )}
          </div>
          <form onSubmit={(event) => void saveProfile(event)} noValidate>
            <dl>
              <div className="profile-row">
                <dt>{editing ? <label htmlFor="profile-username">用户名</label> : '用户名'}</dt>
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
                <dt>{editing ? <label htmlFor="profile-email">邮箱</label> : '邮箱'}</dt>
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
                <dt>账号角色</dt>
                <dd>{user.role === 'admin' ? '管理员' : '普通用户'}</dd>
              </div>
              <div className="profile-row">
                <dt>账号状态</dt>
                <dd>
                  <span className={`tag ${user.status === 'active' ? 'tag-good' : 'tag-bad'}`}>
                    {user.status === 'active' ? '已启用' : '已禁用'}
                  </span>
                </dd>
              </div>
              <div className="profile-row">
                <dt>注册时间</dt>
                <dd>{formatDateTime(user.created_at)}</dd>
              </div>
              <div className="profile-row">
                <dt>账号 ID</dt>
                <dd className="profile-id">
                  <span>{user.id}</span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={copied ? '已复制账号 ID' : '复制账号 ID'}
                    onClick={() => void copyId()}
                  >
                    {copied ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                </dd>
              </div>
            </dl>
            {editing && (
              <div className="profile-actions">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={profilePending || pending || !changed}
                >
                  {profilePending && <LoaderCircle size={14} className="spinner" />}
                  {profilePending ? '正在保存…' : '保存修改'}
                </button>
                <button
                  type="button"
                  className="btn"
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
              <div style={{ marginTop: 14 }}>
                <Notice kind="error" error={profileError}>
                  {errorMessage(profileError)}
                </Notice>
              </div>
            )}
            {saved && (
              <div style={{ marginTop: 14 }}>
                <Notice kind="success">个人资料已更新。</Notice>
              </div>
            )}
          </form>
          {copyError && (
            <p className="field-error" role="status">
              {copyError}
            </p>
          )}
          <p className="profile-tip">
            <Info size={14} aria-hidden="true" />
            可修改用户名和邮箱，更新后请使用新的用户名或邮箱登录。
          </p>
        </section>

        <div className="account-side-stack">
          <section className="session-panel" aria-labelledby="session-heading">
            <span className="session-badge">
              <ShieldCheck size={12} aria-hidden="true" />
              已验证会话
            </span>
            <h2 id="session-heading">你已登录 FinOracle。</h2>
            <p>此标签页已连接你的个人账号。使用期间会自动续期，保持当前登录状态。</p>
            <dl>
              <div>
                <dt>最近登录</dt>
                <dd>{formatDateTime(user.last_login_at)}</dd>
              </div>
              <div>
                <dt>本次连接有效至</dt>
                <dd>{formatDateTime(expiresAt)}</dd>
              </div>
              <div>
                <dt>账号验证</dt>
                <dd>
                  <ShieldCheck size={12} aria-hidden="true" />
                  已通过
                </dd>
              </div>
            </dl>
            <button
              className="btn session-logout-btn"
              disabled={pending || profilePending}
              onClick={() => void logout()}
            >
              {pending ? <LoaderCircle size={15} className="spinner" /> : <LogOut size={15} />}
              退出登录
            </button>
            {Boolean(error) && (
              <Notice kind="error" error={error}>
                {errorMessage(error)}
              </Notice>
            )}
          </section>
          <div className="account-footnote">
            <ShieldCheck size={18} strokeWidth={1.6} aria-hidden="true" />
            <div>
              <h3>让账号始终由你掌控</h3>
              <p>在公共设备上使用后，请主动退出登录，保护你的账号信息。</p>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
