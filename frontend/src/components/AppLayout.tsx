import { useEffect, useRef, useState } from 'react'
import {
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  History,
  Menu,
  MessageSquare,
  Plus,
  Search,
  Star,
} from 'lucide-react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BackendStatus } from './BackendStatus'
import { Brand } from './Brand'
import { Notice } from './Notice'

const links = [
  { path: '/chat', label: '智能问答', icon: MessageSquare },
  { path: '/library', label: '制度库', icon: BookOpen, soon: true },
  { path: '/search', label: '全文检索', icon: Search, soon: true },
  { path: '/history', label: '会话历史', icon: History, soon: true },
  { path: '/favorites', label: '我的收藏', icon: Star, soon: true },
]

export function AppLayout() {
  const { user, connectionWarning } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const navigation = useRef<HTMLElement>(null)
  const location = useLocation()
  useEffect(() => {
    if (!menuOpen) return
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    navigation.current?.querySelector<HTMLAnchorElement>('a')?.focus()
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', close)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', close)
      previousFocus?.focus()
    }
  }, [menuOpen])
  useEffect(() => {
    const media = window.matchMedia('(max-width: 860px)')
    const resized = () => {
      if (!media.matches) setMenuOpen(false)
    }
    media.addEventListener('change', resized)
    return () => media.removeEventListener('change', resized)
  }, [])
  const pageTitle =
    location.pathname === '/account'
      ? '我的账号'
      : links.find((link) => link.path === location.pathname)?.label || '工作台'
  if (!user) return null
  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭导航"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        ref={navigation}
        className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}
        aria-label="主导航"
        id="main-navigation"
      >
        <Brand compact />
        <Link
          className="cut-button button-primary new-question"
          to="/chat"
          onClick={() => setMenuOpen(false)}
        >
          <Plus size={16} strokeWidth={1.6} />
          整理一个问题
          <ArrowUpRight size={14} style={{ marginLeft: 'auto' }} />
        </Link>
        <div className="nav-heading">WORKSPACE / 工作空间</div>
        <nav className="nav-list">
          {links.map(({ path, label, icon: Icon, soon }) => (
            <NavLink
              key={path}
              to={path}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              onClick={() => setMenuOpen(false)}
            >
              <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
              {label}
              {soon && <small>准备中</small>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-information">
          <span className="eyebrow">
            FROM QUESTION
            <br />
            TO ORIGINAL SOURCE.
          </span>
          <p>
            从理解制度开始，
            <br />
            让每一次判断有据可循。
          </p>
        </div>
        <div className="sidebar-footer">
          <Link to="/account" className="account-link" onClick={() => setMenuOpen(false)}>
            <span className="avatar" aria-hidden="true">
              {user.username[0].toUpperCase()}
            </span>
            <div>
              <strong title={user.username}>{user.username}</strong>
              <small>个人资料与登录会话</small>
            </div>
            <ArrowUpRight size={16} />
          </Link>
        </div>
      </aside>
      <header className="app-topbar">
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
          <Link to="/chat">工作台</Link>
          <ChevronRight size={12} />
          <span>{pageTitle}</span>
        </div>
        <div className="topbar-right">
          <span className="topbar-date">
            {new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
              .format(new Date())
              .replaceAll('/', '.')}
          </span>
          <BackendStatus />
          <Link to="/account" className="avatar avatar-small" aria-label="打开我的账号">
            {user.username[0].toUpperCase()}
          </Link>
        </div>
      </header>
      <main className="app-main" id="main-content">
        <div className="page-content">
          {connectionWarning && <Notice>{connectionWarning}</Notice>}
          <Outlet />
        </div>
        <footer className="app-footer">
          <span>FinOracle · 金融制度知识工作台</span>
          <span>KNOWLEDGE WITH CONTEXT</span>
        </footer>
      </main>
    </>
  )
}
