import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 /api 反代到后端（含 SSE），浏览器同源访问，无需配置 CORS。
// 保留原始 Host（不 changeOrigin）：后端按 Host 与 Origin 判同源，改写 Host 会让登录等 POST 被当成跨域拒掉。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: { proxy: { '/api': { target: env.BACKEND_URL || 'http://127.0.0.1:8080' } } },
  }
})
