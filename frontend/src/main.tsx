import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { AuthProvider } from './auth/AuthProvider'
import { Brand } from './components/Brand'
import './styles.css'

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Avoid logging account or token-bearing state. */
  }
  render() {
    if (this.state.failed)
      return (
        <main className="recovery-page">
          <Brand />
          <h1>页面暂时未能加载。</h1>
          <p>请重新载入页面后再试。你的账号资料不会受到影响。</p>
          <button className="cut-button button-dark" onClick={() => window.location.reload()}>
            重新载入
          </button>
        </main>
      )
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
)
