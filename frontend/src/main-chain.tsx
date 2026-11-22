// 证据链工作台离线入口:双击 chain.html 即用(无登录、无后端、无 API 调用)。
// 与主应用共享 ChainPage 组件;数据一律本地导入 --format web 出件。
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './design.css'
import './brand.css'
import { ChainStandaloneApp } from './ChainStandaloneApp'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ChainStandaloneApp />
  </StrictMode>,
)
