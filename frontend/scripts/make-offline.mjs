// chain-offline 构建后处理:vite 的 html 插件对 IIFE 输出仍写 type="module",
// 而 ES module 在 file:// 下被 CORS 拦截;去掉 module/crossorigin 使离线版可双击打开。
import { readFileSync, writeFileSync } from 'node:fs'

const path = 'dist/chain.html'
let html = readFileSync(path, 'utf8')
html = html.replace(/<script type="module" crossorigin/g, '<script defer')
if (html.includes('type="module"')) throw new Error('chain.html 仍含 module script,离线化失败')
writeFileSync(path, html)
console.log('[make-offline] chain.html 已离线化(经典 script,CSS 随 IIFE 内联于 JS)')
