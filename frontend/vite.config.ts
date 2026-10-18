import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 /api 反代到后端（含 SSE），浏览器同源访问，无需配置 CORS。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: { proxy: { '/api': { target: env.BACKEND_URL || 'http://127.0.0.1:8080', changeOrigin: true } } },
  }
})
