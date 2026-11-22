// 离线版壳:极简顶栏(确定性/离线标志+导入)+ 工作台本体。
// 不挂 api/client(无登录墙),打包为独立 bundle 供答辩离线演示。
import { ChainPage } from './pages/ChainPage'

export function ChainStandaloneApp() {
  return <div className="app-shell chain-standalone">
    <main className="main-content" style={{ display: 'flex', flexDirection: 'column' }}>
      <ChainPage />
    </main>
  </div>
}
