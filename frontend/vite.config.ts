import { defineConfig, loadEnv, type UserConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 /api 反代到后端（含 SSE），浏览器同源访问，无需配置 CORS。
// 保留原始 Host（不 changeOrigin）：后端按 Host 与 Origin 判同源，改写 Host 会让登录等 POST 被当成跨域拒掉。
// base './' + 双入口：chain.html 为证据链工作台离线版（无登录壳），build 后双击即可用。
// --mode chain-offline：单入口 IIFE 构建（ES module 在 file:// 下被 CORS 拦，IIFE 经典 script 不受限），
//   覆盖 dist/chain.html 为可双击离线版；index.html 主产物不受影响。
export default defineConfig(({ mode }): UserConfig => {
  const env = loadEnv(mode, '.', '')
  if (mode === 'chain-offline') {
    return {
      plugins: [react()],
      base: './',
      build: {
        outDir: 'dist',
        emptyOutDir: false,
        rollupOptions: {
          input: { chain: 'chain.html' },
          output: { format: 'iife', inlineDynamicImports: true, entryFileNames: 'assets/chain-offline.js' },
        },
      },
    }
  }
  return {
    plugins: [react()],
    base: './',
    server: { proxy: { '/api': { target: env.BACKEND_URL || 'http://127.0.0.1:8080' } } },
    build: {
      rollupOptions: {
        input: { main: 'index.html', chain: 'chain.html' },
      },
    },
  }
})
