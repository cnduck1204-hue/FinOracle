import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { normalizeApiPrefix } from './scripts/nginx-config.ts'

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const apiPrefix = normalizeApiPrefix(environment.VITE_API_BASE_URL || '/api/v1')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        [apiPrefix]: {
          target: environment.API_PROXY_TARGET || 'http://127.0.0.1:8001',
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'node',
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
    },
  }
})
