import { useState, type FormEvent, type HTMLInputAutoCompleteAttribute } from 'react'
import {
  ArrowUpRight,
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
import { useAuth } from '../auth/AuthProvider'
import { ApiError, errorMessage } from '../lib/api'
import { validateRegistration, type FieldErrors } from '../lib/validation'
import { Dialog } from './Dialog'
import { Notice } from './Notice'

function Field({
  id,
  label,
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
      </div>
      <div className="input-wrap">
        <Icon size={15} strokeWidth={1.7} aria-hidden="true" />
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
            {visible ? <EyeOff size={15} /> : <Eye size={15} />}
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

export function AuthDialog({
  initialMode,
  onClose,
  onAuthenticated,
}: {
  initialMode: 'login' | 'register'
  onClose: () => void
  onAuthenticated: () => void
}) {
  const { client, notice } = useAuth()
  const [mode, setMode] = useState(initialMode)
  const [registered, setRegistered] = useState(false)
  const [identifier, setIdentifier] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [error, setError] = useState<unknown>(null)
  const registering = mode === 'register'

  function selectMode(next: typeof mode) {
    setMode(next)
    setPassword('')
    setConfirmation('')
    setFieldErrors({})
    setError(null)
    setRegistered(false)
  }

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
        selectMode('login')
        setIdentifier(created.username)
        setRegistered(true)
      } else {
        await client.login(identifier.trim(), password)
        onAuthenticated()
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
    <Dialog
      kicker={registering ? 'Account Registration' : 'Account Access'}
      title={registering ? '建立你的知识空间。' : '欢迎回来。'}
      busy={pending}
      onClose={onClose}
      closeLabel="关闭登录弹窗"
    >
      <p>{registering ? '创建 FinOracle 账号。' : '登录 FinOracle，继续使用制度问答。'}</p>
      <div className="auth-tabs" role="group" aria-label="账号操作">
        <button
          className="auth-tab"
          type="button"
          aria-pressed={!registering}
          disabled={pending}
          onClick={() => selectMode('login')}
        >
          账号登录
        </button>
        <button
          className="auth-tab"
          type="button"
          aria-pressed={registering}
          disabled={pending}
          onClick={() => selectMode('register')}
        >
          注册账号
        </button>
      </div>
      {error ? (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      ) : registered ? (
        <Notice kind="success">
          <Check size={13} aria-hidden="true" /> 账号已创建，请使用新账号登录。
        </Notice>
      ) : notice ? (
        <Notice kind={notice.startsWith('已退出') ? 'success' : 'info'}>{notice}</Notice>
      ) : null}
      <form className="auth-form" onSubmit={submit} noValidate aria-busy={pending}>
        {registering ? (
          <>
            <Field
              id="username"
              label="用户名"
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
              value={email}
              onChange={setEmail}
              placeholder="name@example.com"
              icon={Mail}
              type="email"
              autoComplete="email"
              error={fieldErrors.email}
            />
            <Field
              id="password"
              label="密码"
              value={password}
              onChange={setPassword}
              placeholder="至少 8 位，包含字母和数字"
              icon={KeyRound}
              type="password"
              autoComplete="new-password"
              maxLength={128}
              error={fieldErrors.password}
            />
            <Field
              id="confirm_password"
              label="确认密码"
              value={confirmation}
              onChange={setConfirmation}
              placeholder="再次输入密码"
              icon={KeyRound}
              type="password"
              autoComplete="new-password"
              maxLength={128}
              error={fieldErrors.confirm_password}
            />
          </>
        ) : (
          <>
            <Field
              id="identifier"
              label="用户名或邮箱"
              value={identifier}
              onChange={setIdentifier}
              placeholder="输入用户名或邮箱"
              icon={UserRound}
              autoComplete="username"
              error={fieldErrors.identifier}
            />
            <Field
              id="password"
              label="密码"
              value={password}
              onChange={setPassword}
              placeholder="输入你的密码"
              icon={KeyRound}
              type="password"
              autoComplete="current-password"
              maxLength={128}
              error={fieldErrors.password}
            />
          </>
        )}
        <button
          className="btn btn-primary btn-lg auth-submit arrow-trigger"
          type="submit"
          disabled={pending}
        >
          {pending ? (
            <>
              <LoaderCircle size={16} className="spinner" />
              {registering ? '正在创建…' : '正在登录…'}
            </>
          ) : (
            <>
              {registering ? '创建账号' : '进入工作台'}
              <ArrowUpRight size={15} aria-hidden="true" />
            </>
          )}
        </button>
      </form>
      <p className="auth-footnote">
        <ShieldCheck size={14} aria-hidden="true" />
        登录仅在当前标签页有效。使用公共设备后请退出。
      </p>
    </Dialog>
  )
}
