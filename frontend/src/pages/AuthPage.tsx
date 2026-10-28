import { useState, type FormEvent, type HTMLInputAutoCompleteAttribute } from 'react'
import {
  ArrowRight,
  Check,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Mail,
  ShieldCheck,
  UserRound,
  type LucideIcon,
} from 'lucide-react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BackendStatus } from '../components/BackendStatus'
import { Brand } from '../components/Brand'
import { Notice } from '../components/Notice'
import { OracleGraphic } from '../components/OracleGraphic'
import { ApiError, errorMessage } from '../lib/api'
import { validateRegistration, type FieldErrors } from '../lib/validation'

function Field({
  id,
  label,
  meta,
  value,
  onChange,
  placeholder,
  error,
  icon: Icon,
  type = 'text',
  autoComplete,
  maxLength = 254,
}: {
  id: string
  label: string
  meta: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  error?: string
  icon: LucideIcon
  type?: string
  autoComplete?: HTMLInputAutoCompleteAttribute
  maxLength?: number
}) {
  const [visible, setVisible] = useState(false)
  const isPassword = type === 'password'
  return (
    <div className="form-field">
      <div className="field-label">
        <label htmlFor={id}>{label}</label>
        <small aria-hidden="true">{meta}</small>
      </div>
      <div className="input-wrap">
        <Icon size={15} strokeWidth={1.5} aria-hidden="true" />
        <input
          id={id}
          name={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          type={isPassword && visible ? 'text' : type}
          autoComplete={autoComplete}
          maxLength={maxLength}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          spellCheck={false}
        />
        {isPassword && (
          <button
            type="button"
            className="icon-button"
            aria-label={visible ? `隐藏${label}` : `显示${label}`}
            onClick={() => setVisible((state) => !state)}
          >
            {visible ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </div>
      {error && (
        <span className="field-error" id={`${id}-error`}>
          {error}
        </span>
      )}
    </div>
  )
}

export function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { client, user, notice } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const state = location.state as { registeredUsername?: string; from?: string } | null
  const [identifier, setIdentifier] = useState(state?.registeredUsername || '')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [error, setError] = useState<unknown>(null)
  const registering = mode === 'register'
  const safeDestination = [
    '/chat',
    '/account',
    '/library',
    '/search',
    '/history',
    '/favorites',
  ].includes(state?.from || '')
    ? state!.from!
    : '/chat'
  if (user) return <Navigate to={safeDestination} replace />

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending) return
    const errors: FieldErrors = registering
      ? validateRegistration({ username, email, password, confirm_password: confirmation })
      : {
          ...(identifier.trim().length < 3
            ? { identifier: '请输入用户名或邮箱，至少 3 个字符。' }
            : {}),
          ...(!password ? { password: '请输入密码。' } : {}),
        }
    setFieldErrors(errors)
    setError(null)
    if (Object.keys(errors).length) {
      document.getElementById(Object.keys(errors)[0])?.focus()
      return
    }
    setPending(true)
    try {
      if (registering) {
        const created = await client.register({
          username: username.trim(),
          email: email.trim(),
          password,
          confirm_password: confirmation,
        })
        navigate('/login', { replace: true, state: { registeredUsername: created.username } })
      } else {
        await client.login(identifier.trim(), password)
        navigate(safeDestination, { replace: true })
      }
    } catch (caught) {
      setError(caught)
      if (caught instanceof ApiError && caught.code === 'USER_USERNAME_EXISTS')
        setFieldErrors({ username: '该用户名已被使用，请换一个。' })
      if (caught instanceof ApiError && caught.code === 'USER_EMAIL_EXISTS')
        setFieldErrors({ email: '该邮箱已注册，可以直接登录。' })
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="auth-frame">
      <header className="auth-header">
        <Brand />
        <div className="auth-header-right">
          <span className="eyebrow">KNOWLEDGE · WITH CONTEXT</span>
          <BackendStatus />
        </div>
      </header>
      <main className="auth-content" id="main-content">
        <section className="auth-story" aria-label="关于 FinOracle">
          <span className="eyebrow">FINANCIAL KNOWLEDGE / FINORACLE</span>
          <h1>
            从问题出发，
            <br />
            回到<em>制度原文。</em>
          </h1>
          <p className="auth-story-lead">
            让制度成为可理解、可核查的知识。
            <br />
            为每一次判断，找到清晰的依据。
          </p>
          <OracleGraphic />
          <div className="story-steps">
            {[
              ['01', '提出问题', '从真实业务场景开始'],
              ['02', '理解制度', '梳理要求与适用边界'],
              ['03', '回溯原文', '让每一条依据可核查'],
            ].map(([number, title, description]) => (
              <div className="story-step" key={number}>
                <span>{number} /</span>
                <strong>{title}</strong>
                <small>{description}</small>
              </div>
            ))}
          </div>
          <span className="auth-story-index">FROM QUESTION TO SOURCE</span>
        </section>
        <section className="auth-form-side" aria-labelledby="auth-title">
          <div className="auth-form-container">
            <span className="eyebrow">
              // {registering ? 'CREATE YOUR ACCOUNT' : 'WELCOME BACK'}
            </span>
            <h2 id="auth-title">{registering ? '建立你的知识空间。' : '欢迎回来。'}</h2>
            <p className="auth-description">
              {registering
                ? '创建个人账号，从这里开始探索金融制度。'
                : '登录 FinOracle，继续你的制度探索。'}
            </p>
            <nav className="auth-tabs" aria-label="账号操作">
              <Link
                className="auth-tab"
                to="/login"
                aria-current={!registering ? 'page' : undefined}
                onClick={(event) => {
                  if (pending) event.preventDefault()
                }}
              >
                账号登录<span>01</span>
              </Link>
              <Link
                className="auth-tab"
                to="/register"
                aria-current={registering ? 'page' : undefined}
                onClick={(event) => {
                  if (pending) event.preventDefault()
                }}
              >
                注册账号<span>02</span>
              </Link>
            </nav>
            {error ? (
              <Notice kind="error" error={error}>
                {errorMessage(error)}
              </Notice>
            ) : !registering && state?.registeredUsername ? (
              <Notice kind="success">
                <Check size={12} aria-hidden="true" /> 账号已创建，请使用新账号登录。
              </Notice>
            ) : notice ? (
              <Notice kind={notice.startsWith('已退出') ? 'success' : 'info'}>{notice}</Notice>
            ) : null}
            <form
              className={`auth-form ${registering ? 'auth-form-register' : ''}`}
              onSubmit={submit}
              noValidate
              aria-busy={pending}
            >
              {registering ? (
                <>
                  <Field
                    id="username"
                    label="用户名"
                    meta="USERNAME"
                    value={username}
                    onChange={setUsername}
                    placeholder="3–32 位字母、数字或下划线"
                    icon={UserRound}
                    autoComplete="username"
                    maxLength={32}
                    error={fieldErrors.username}
                  />
                  <Field
                    id="email"
                    label="邮箱"
                    meta="EMAIL ADDRESS"
                    value={email}
                    onChange={setEmail}
                    placeholder="输入你的邮箱地址"
                    icon={Mail}
                    type="email"
                    autoComplete="email"
                    error={fieldErrors.email}
                  />
                </>
              ) : (
                <Field
                  id="identifier"
                  label="用户名或邮箱"
                  meta="ACCOUNT"
                  value={identifier}
                  onChange={setIdentifier}
                  placeholder="输入用户名或邮箱"
                  icon={UserRound}
                  autoComplete="username"
                  error={fieldErrors.identifier}
                />
              )}
              <Field
                id="password"
                label="密码"
                meta="PASSWORD"
                value={password}
                onChange={setPassword}
                placeholder={registering ? '至少 8 位，包含英文字母和数字' : '输入你的密码'}
                icon={KeyRound}
                type="password"
                autoComplete={registering ? 'new-password' : 'current-password'}
                maxLength={128}
                error={fieldErrors.password}
              />
              {registering && (
                <Field
                  id="confirm_password"
                  label="确认密码"
                  meta="CONFIRM PASSWORD"
                  value={confirmation}
                  onChange={setConfirmation}
                  placeholder="再次输入密码"
                  icon={ShieldCheck}
                  type="password"
                  autoComplete="new-password"
                  maxLength={128}
                  error={fieldErrors.confirm_password}
                />
              )}
              <button
                type="submit"
                className="cut-button button-primary auth-submit"
                disabled={pending}
              >
                <span>
                  {pending
                    ? registering
                      ? '正在创建账号'
                      : '正在验证账号'
                    : registering
                      ? '创建账号'
                      : '进入工作台'}
                </span>
                {pending ? (
                  <LoaderCircle className="spinner" size={18} aria-hidden="true" />
                ) : (
                  <ArrowRight size={18} aria-hidden="true" />
                )}
              </button>
            </form>
            <div className="auth-privacy">
              <ShieldCheck size={13} strokeWidth={1.5} aria-hidden="true" />
              <span>
                {registering
                  ? '注册将创建普通用户账号，成功后返回登录。'
                  : '登录在当前标签页保留，公共设备使用后请主动退出。'}
              </span>
            </div>
          </div>
        </section>
      </main>
      <footer className="auth-footer">
        <span>FinOracle / 让知识有迹可循</span>
        <span>QUESTION. UNDERSTAND. VERIFY.</span>
      </footer>
    </div>
  )
}
