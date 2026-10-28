import { LoaderCircle, RotateCw, WifiOff } from 'lucide-react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth/AuthProvider'
import { AppLayout } from './components/AppLayout'
import { Brand } from './components/Brand'
import { AccountPage } from './pages/AccountPage'
import { AuthPage } from './pages/AuthPage'
import { ComingSoonPage } from './pages/ComingSoonPage'
import { WorkspacePage } from './pages/WorkspacePage'

function RequireSession() {
  const { user, hasSession } = useAuth()
  const location = useLocation()
  return user && hasSession ? (
    <Outlet />
  ) : (
    <Navigate to="/login" replace state={{ from: location.pathname }} />
  )
}

export function App() {
  const { boot, bootError, retry, returnToLogin } = useAuth()
  if (boot !== 'ready')
    return (
      <main className="recovery-page">
        <Brand />
        {boot === 'loading' ? (
          <>
            <LoaderCircle size={24} className="spinner" />
            <span className="eyebrow">正在恢复你的工作空间</span>
          </>
        ) : (
          <>
            <WifiOff size={30} strokeWidth={1.4} />
            <h1>连接暂时中断。</h1>
            <p>{bootError} 我们保留了你的登录信息，可以稍后重试。</p>
            <button className="cut-button button-dark" onClick={retry}>
              <RotateCw size={15} />
              重新连接
            </button>
            <button className="text-button" onClick={returnToLogin}>
              返回登录
            </button>
          </>
        )}
      </main>
    )
  return (
    <Routes>
      <Route path="/login" element={<AuthPage key="login" mode="login" />} />
      <Route path="/register" element={<AuthPage key="register" mode="register" />} />
      <Route element={<RequireSession />}>
        <Route element={<AppLayout />}>
          <Route path="/chat" element={<WorkspacePage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/library" element={<ComingSoonPage module="library" />} />
          <Route path="/search" element={<ComingSoonPage module="search" />} />
          <Route path="/history" element={<ComingSoonPage module="history" />} />
          <Route path="/favorites" element={<ComingSoonPage module="favorites" />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  )
}
