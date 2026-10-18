# SignalX 前端

React 19 + TypeScript + Vite 工作台，直连 Sense2Act 后端（REST + SSE）。接口契约见 `../Backend/docs/api-design.md`。

## 运行

```bash
npm install
npm run dev        # http://127.0.0.1:5173，/api 代理到 BACKEND_URL（默认 http://127.0.0.1:8080）
npm run build      # 类型检查 + 产物
```

后端在 IDEA 或 `docker compose` 里跑在 8080 即可；同源代理，无需配置 CORS。全栈容器里由 nginx 提供同样的代理（`nginx.conf`，SSE 关缓冲）。

## 结构

```
src/api/client.ts            唯一的后端客户端：{code,message,data} 解包、JWT、401 回登录、快照/Markdown 文本响应、SSE
src/services/investigations.ts  视图模型：investigation + signal + steps + report 拼成调查任务；步骤文案
src/pages/                   页面（总览、信号、调查详情/实时时间线、报告、文档、事件图谱、信息源、系统管理）
src/components/              通用组件
```

## 数据流

- 登录：`POST /auth/token`（邮箱 + 密码），token 存 `localStorage`，失效（401）回到登录页。
- 全局通知：`GET /stream?token=` 订阅 `signal_created / report_ready / investigation_completed / source_degraded`，到达即刷新数据。
- 调查详情：进行中的调查订阅 `GET /investigations/{id}/stream?token=`，Agent 每上报一条步骤，时间线实时更新；断线由 EventSource 带 `Last-Event-ID` 自动补发。
- 步骤按 `cursor` 翻页读全；报告按 `investigation_id` 查询（一调查一报告）。
- 角色：系统管理与信息源写操作仅 admin 可见；后端独立鉴权。
