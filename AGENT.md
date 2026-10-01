# AGENT.md

给 AI 编码助手的说明。人看的文档在 docs/，冲突时以本文件和 docs/api-design.md 为准。

## 项目

招投标商机发现的后端平台。链路：采集招标公告 → 信号 → 调查 → 报告 → 反馈。Java 21 + Spring Boot 3 + PostgreSQL(pgvector)，Flyway 管迁移，springdoc 出接口文档，ORM 用 MyBatis-Plus。

## 最重要的边界

爬虫、信号发现和 agent loop 不在本仓库实现。它们是外部服务，经 `/api/v1/internal/*` 接入。所以：

- 不要在这里写抓取解析、检测算法、LLM 调用、agent 循环、提示词。
- 后端没有工具层。不要建 ToolRegistry、工具执行接口或工具目录；工具是外部 Agent 服务自己的概念，它直接用 /documents、/organizations 查数据。
- 后端不调用外部服务，方向只有一个：外部服务调后端。没有插件机制，不要引入环境变量加载研究代码的设计。
- 外部服务没接入时系统要能正常跑：没有新文档、没有新信号，调查停在 created，其他功能不受影响。

## 硬性规则

- 所有写接口在 API 层校验不变量：调查状态机流转、轮次上限（max_rounds）、预算上限（token_budget）、报告 claim 只能引用已登记的证据。非法写入返回 40901 或 42201。
- 证据必须带来源 URL、发布时间、获取时间，外部内容抓快照、算 SHA-256。
- claim 的 nature 只能是 fact / inference / speculation，API 响应必须原样带出，不许丢。
- 统一响应包 `{code, message, data}`；分页 `{items, total, page, page_size}`；错误码表见 docs/api-design.md §1。
- ID 用带前缀 ULID；金额字符串小数；时间 ISO 8601 带时区。
- 用户接口 JWT，内部接口 `X-Internal-Key`。
- 采集按 content_hash 和归一化 URL 去重，单条失败不阻塞批次。
- 报告响应固定带 disclaimer："本报告由AI生成，结论不替代人的自主判断"。

## 内部 API 是外部团队的契约

`docs/api-design.md §9` 的路径、字段名、状态值不能随意改，改了要当破坏性变更处理。核心几组：

- 爬虫：`GET /internal/ingest-queue`（到期源+配置）→ `POST /internal/documents`（批量推送结构化文档+raw_html）→ `POST /internal/ingest-runs`（运行报告）
- 检测：`GET /internal/detection-queue`（响应内嵌 org_stats 和画像）、`POST /internal/signals`（幂等）、`POST /internal/detection-scan-complete`
- 调查：领取 created → `start`（CAS）→ `context` → `steps`/`questions`/`evidences` → `report` → `complete`/`fail`；查数据直接用 /documents、/organizations
- SSE 事件在写库时由后端自动发布，事件 id 等于 step 的 seq，支持 Last-Event-ID 补发。

## 开发方式

按 docs/backlog.md 执行：故事带验收标准，完成以它的 DoD 为准，冲刺安排和 M1–M5 映射都在那里，本文件不重复。表结构见 docs/data-model.md（按史诗分批建迁移）。

`scripts/` 里要有示例数据：一条大额医疗 AI 招标引发调查的完整链路样本（文档、信号、调查步骤、证据、报告、事件图谱），外部服务没接入时靠它演示全部页面。种子链路按史诗逐步补齐，是每个史诗的硬性故事，不是收尾装饰。
