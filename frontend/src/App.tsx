import { LoaderCircle, RotateCw, ShieldCheck, WifiOff } from 'lucide-react'
import { lazy, Suspense } from 'react'
import { Link, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth/AuthProvider'
import { Brand } from './components/Brand'
import { AccountPage } from './pages/AccountPage'
import { AdminDocumentVersionsPage } from './pages/AdminDocumentVersionsPage'
import { AdminImportDetailPage } from './pages/AdminImportDetailPage'
import { AdminImportNewPage } from './pages/AdminImportNewPage'
import { AdminImportsPage } from './pages/AdminImportsPage'
import { AdminUsersPage } from './pages/AdminUsersPage'
import { FavoritesPage } from './pages/FavoritesPage'
import { LibraryPage } from './pages/LibraryPage'
import { SearchPage } from './pages/SearchPage'

const WorkspacePage = lazy(() =>
  import('./pages/WorkspacePage').then(({ WorkspacePage }) => ({ default: WorkspacePage })),
)

function WorkspaceRoute() {
  return (
    <Suspense
      fallback={
        <main className="recovery-page">
          <Brand />
          <p role="status">正在加载工作台…</p>
        </main>
      }
    >
      <WorkspacePage />
    </Suspense>
  )
}

function RequireSession() {
  const { user, hasSession } = useAuth()
  const location = useLocation()
  return user && hasSession ? (
    <Outlet />
  ) : (
    <Navigate
      to="/chat"
      replace
      state={{
        authMode: 'login',
        from: { pathname: location.pathname, search: location.search, hash: location.hash },
      }}
    />
  )
}

function RequireAdmin() {
  const { user } = useAuth()
  return user?.role === 'admin' ? (
    <Outlet />
  ) : (
    <section className="empty-page">
      <div className="hatch-bar" aria-hidden="true" />
      <div className="empty-symbol">
        <ShieldCheck size={26} strokeWidth={1.6} aria-hidden="true" />
      </div>
      <h1>没有访问权限</h1>
      <p>此页面仅向管理员开放。</p>
      <Link to="/chat" className="btn btn-primary">
        返回工作台
      </Link>
    </section>
  )
}

export function App() {
  const { user, boot, bootError, retry, returnToLogin } = useAuth()
  if (boot !== 'ready')
    return (
      <main className="recovery-page">
        <Brand />
        {boot === 'loading' ? (
          <>
            <LoaderCircle size={24} className="spinner" />
            <span className="recovery-label">正在恢复你的工作空间…</span>
          </>
        ) : (
          <>
            <WifiOff size={28} strokeWidth={1.5} />
            <h1>连接暂时中断。</h1>
            <p>{bootError} 我们保留了你的登录信息，可以稍后重试。</p>
            <button className="btn btn-primary" onClick={retry}>
              <RotateCw size={15} />
              重新连接
            </button>
            <Link to="/login" replace className="text-button" onClick={returnToLogin}>
              返回登录
            </Link>
          </>
        )}
      </main>
    )
  return (
    <Routes>
      <Route
        path="/login"
        element={<Navigate to="/chat" replace state={user ? null : { authMode: 'login' }} />}
      />
      <Route
        path="/register"
        element={<Navigate to="/chat" replace state={user ? null : { authMode: 'register' }} />}
      />
      <Route element={<WorkspaceRoute />}>
        <Route path="/chat" element={null} />
        <Route element={<RequireSession />}>
          <Route path="/history" element={null} />
          <Route path="/account" element={<AccountPage />} />
          <Route element={<RequireAdmin />}>
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route
              path="/admin/documents"
              element={<Navigate to="/library?view=manage" replace />}
            />
            <Route
              path="/admin/documents/:documentId/versions"
              element={<AdminDocumentVersionsPage />}
            />
            <Route path="/admin/imports" element={<AdminImportsPage />} />
            <Route path="/admin/imports/new" element={<AdminImportNewPage />} />
            <Route path="/admin/imports/:jobId" element={<AdminImportDetailPage />} />
          </Route>
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/favorites" element={<FavoritesPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  )
}
