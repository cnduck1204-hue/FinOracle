import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, Menu, Plus } from 'lucide-react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BackendStatus } from './BackendStatus'
import { Brand } from './Brand'
import { Notice } from './Notice'

const primaryLinks = [
  { path: '/search', label: '全文检索', number: '02' },
  { path: '/library', label: '制度库', number: '03' },
  { path: '/favorites', label: '我的收藏', number: '04' },
]

const adminLinks = [
  { path: '/admin/imports', label: '导入任务', number: '06' },
  { path: '/admin/users', label: '用户管理', number: '07' },
]

const allLinks = [...primaryLinks, ...adminLinks]

type WorkspaceLayout = {
  toolbar?: { title: ReactNode; actions: ReactNode }
  sessions: (closeMenu: () => void) => ReactNode
  onNewSession: () => void
  sessionControlsDisabled: boolean
}

export function AppLayout({
  children,
  workspace: { toolbar, sessions, onNewSession, sessionControlsDisabled },
}: {
  children: ReactNode
  workspace: WorkspaceLayout
}) {
  const { user, notice, connectionWarning } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const closeMenu = () => setMenuOpen(false)
  const navigation = useRef<HTMLElement>(null)
  const location = useLocation()

  useEffect(() => {
    if (!menuOpen) return
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    navigation.current?.querySelector<HTMLAnchorElement>('a')?.focus()
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('dialog[open], :is(:popover-open)')) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('keydown', close)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', close)
      previousFocus?.focus()
    }
  }, [menuOpen])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 900px)')
    const resized = () => {
      if (!media.matches) setMenuOpen(false)
    }
    media.addEventListener('change', resized)
    return () => media.removeEventListener('change', resized)
  }, [])

  const pageTitle =
    location.pathname === '/account'
      ? '我的账号'
      : allLinks.find(
          (link) =>
            location.pathname === link.path || location.pathname.startsWith(`${link.path}/`),
        )?.label || '工作台'
  const warning = user ? connectionWarning : notice
  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      {menuOpen && (
        <button className="sidebar-backdrop" aria-label="关闭导航" onClick={closeMenu} />
      )}
      <aside
        ref={navigation}
        className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}
        aria-label="主导航"
        id="main-navigation"
      >
        <div className="sidebar-brand">
          <Brand compact onDark />
        </div>
        <div className="nav-scroll">
          <nav className="nav-list">
            <button
              className="btn btn-primary nav-new-session"
              type="button"
              disabled={sessionControlsDisabled}
              onClick={() => {
                onNewSession()
                closeMenu()
              }}
            >
              <span className="nav-index" aria-hidden="true">
                01
              </span>
              新会话
              <Plus size={16} aria-hidden="true" />
            </button>
            {primaryLinks.map(({ path, label, number }) => (
              <NavLink
                key={path}
                to={path}
                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                onClick={closeMenu}
              >
                <span className="nav-index" aria-hidden="true">
                  {number}
                </span>
                {label}
              </NavLink>
            ))}
          </nav>
          <section className="sidebar-sessions" aria-label="会话">
            {user && sessions(closeMenu)}
            <NavLink to="/history" className="nav-item sidebar-sessions-all" onClick={closeMenu}>
              <span className="nav-index" aria-hidden="true">
                05
              </span>
              查看全部
              <ChevronRight size={14} aria-hidden="true" />
            </NavLink>
          </section>
          {user?.role === 'admin' && (
            <div className="nav-group">
              <div className="nav-heading">知识治理</div>
              <nav className="nav-list">
                {adminLinks.map(({ path, label, number }) => (
                  <NavLink
                    key={path}
                    to={path}
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                    onClick={closeMenu}
                  >
                    <span className="nav-index" aria-hidden="true">
                      {number}
                    </span>
                    {label}
                  </NavLink>
                ))}
              </nav>
            </div>
          )}
        </div>
        {user ? (
          <div className="sidebar-footer">
            <Link to="/account" className="account-link" onClick={closeMenu}>
              <span className="avatar" aria-hidden="true">
                {user.username[0].toUpperCase()}
              </span>
              <div className="account-link-meta">
                <strong title={user.username}>{user.username}</strong>
                <small>{user.role === 'admin' ? '管理员账号' : '个人资料与会话'}</small>
              </div>
              <ChevronRight size={15} aria-hidden="true" />
            </Link>
          </div>
        ) : (
          <div className="sidebar-footer">
            <div className="sidebar-guest-meta" aria-hidden="true">
              <strong>FINORACLE</strong>
              <span>POLICY // RAG</span>
            </div>
          </div>
        )}
      </aside>
      <header className={`app-topbar${toolbar ? ' app-topbar-workspace' : ''}`}>
        <div className="breadcrumbs">
          <button
            className="icon-button menu-button"
            aria-label="打开导航"
            aria-expanded={menuOpen}
            aria-controls="main-navigation"
            onClick={() => setMenuOpen((value) => !value)}
          >
            <Menu size={19} />
          </button>
          {toolbar ? (
            toolbar.title
          ) : (
            <>
              <Link to="/chat">工作台</Link>
              <ChevronRight size={13} aria-hidden="true" />
              <span>{pageTitle}</span>
            </>
          )}
        </div>
        <div className="topbar-right">
          {toolbar?.actions}
          <BackendStatus />
          {user ? (
            <Link to="/account" className="avatar avatar-small" aria-label="打开我的账号">
              {user.username[0].toUpperCase()}
            </Link>
          ) : (
            <Link
              to="/chat"
              replace
              state={{ authMode: 'login' }}
              className="btn btn-primary btn-sm"
            >
              登录
            </Link>
          )}
        </div>
      </header>
      <main className={`app-main${toolbar ? ' app-main-workspace' : ''}`} id="main-content">
        <div className={`page-content${toolbar ? ' page-content-workspace' : ''}`}>
          {warning && <Notice>{warning}</Notice>}
          {children}
        </div>
      </main>
    </>
  )
}
