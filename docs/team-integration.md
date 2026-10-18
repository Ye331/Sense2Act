# Sense2Act 团队对接指南

契约版本：2026-10-14 接入修复。平台采用单实例后端，爬虫、信号检测和 Agent 服务通过 HTTP 接入，前端通过 REST + SSE 接入。无需共享数据库、共享 Java 代码或把算法合并进后端仓库。

## 1. 后端负责人先提供统一联调环境

优先部署一套所有成员能访问的联调后端。服务器/域名用于长期联调；同一开发局域网也可以共享一台电脑。各自运行后端适合个人开发，但不同电脑的数据不会自动互通。

当前修复仍在本地工作区，尚未提交或推送；团队拉仓库前应先将修复、迁移及 OpenAPI 基线纳入同一代码版本，再部署该版本。本文和调用集合可以直接通过团队仓库或文件传送交付，本机文件链接不能作为成员电脑上的访问地址。

负责人向成员提供以下信息：

| 信息 | 示例/说明 |
| --- | --- |
| 服务根地址 | `http://后端电脑的局域网IP:8080` 或实际 HTTPS 域名；根地址不含 `/api/v1` |
| REST 基础路径 | `服务根地址/api/v1` |
| 健康检查 | `服务根地址/actuator/health`，应返回 UP |
| Swagger | `服务根地址/swagger-ui.html` |
| OpenAPI | `服务根地址/v3/api-docs`；离线规范为 `docs/openapi/baseline.json` |
| 前端测试账号 | analyst/viewer；管理页面另由负责人提供 admin 权限 |
| 服务认证 key | X-Internal-Key，仅交给爬虫、检测、Agent 服务接入方 |
| 部署版本及联系人 | 记录代码版本、部署时间、联调问题负责人 |

### 共享环境启动示例

在 Backend 目录准备 `.env.integration`，按 `.env.example` 填配置。首次可复制模板；文件已存在则编辑已有文件。该环境文件已被 Git 忽略，不随对接材料分发。

```powershell
Copy-Item .env.example .env.integration
# 编辑：JWT_SECRET（至少32字节）、POSTGRES_PASSWORD、INTERNAL_API_KEY、SEED_*_PASSWORD
docker compose --env-file .env.integration -p sense2act-integration -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

独立 compose 项目名使联调卷与默认项目分开；主机 8080 端口应可用。不要对已有环境直接运行会覆盖配置文件的复制命令。

- 局域网直连：在联调环境文件中设置 `API_BIND=0.0.0.0`，允许团队网段访问主机 TCP 8080，成员使用这台电脑的实际局域网 IP。
- 服务器部署：API 保持默认本机绑定，经 nginx 提供实际域名/TLS；配置参考 `scripts/deploy/nginx.conf.example`，SSE 保持关闭缓冲。
- 前端跨域：配置 `CORS_ALLOWED_ORIGINS` 为实际前端来源，包含协议、主机和端口，逗号分隔。例如 `http://localhost:5173,http://前端电脑IP:5173`。浏览器页面使用 HTTPS 时，直接访问的后端也应使用 HTTPS。
- 种子密码只影响首次创建账号；已有数据库中的账号密码不会因修改环境变量而更新。

特别注意：成员电脑上的 `localhost` 是成员自己的电脑，不是后端负责人的电脑。当前没有为本仓库建立长期运行的共享服务，以上地址需负责人实际部署后填写。

## 2. 各成员的职责与认证

| 接入角色 | 认证 | 主要工作与调用顺序 |
| --- | --- | --- |
| 前端 | 登录获取 JWT；Authorization: Bearer | 登录 → 看板/文档/信号 → 人工开调查 → 时间线/报告/证据 → 反馈；SSE 更新界面 |
| 爬虫 | X-Internal-Key | ingest-queue 读取到期源 → documents 提交解析结果及原文 → ingest-runs 回报统计/错误 |
| 信号检测 | X-Internal-Key | detection-queue 获取文档/基线/画像/启用规则 → signals 推命中 → detection-scan-complete 标记扫描完成 |
| Agent | X-Internal-Key | 查询 created → start → context → 问题/steps/证据 → report → complete 或 fail |
| 后端负责人 | admin JWT / 部署配置 | 配置真实信息源、规则、画像和调查策略；部署、数据迁移、接口契约与验收 |

前端无需内部 key。三个机器服务无需管理员账号；查询文档和机构也可使用内部 key。当前机器服务共用一个配置 key 和默认 600 次/分钟额度，应根据总轮询量安排调用。

## 3. 给成员的文件

- `team-integration.postman_collection.json`：39 个分角色调用示例，含变量自动保存。
- `team-integration.postman_environment.json`：导入后选择该环境，填真实 base_url 和本人获授权的凭据。密码、key、token 默认空白。
- `api-design.md`：正式字段、状态、错误码和内部服务契约。
- `user-stories.md`：功能、职责和验收条件。
- `openapi/baseline.json`：可导入接口工具或用于生成客户端类型的离线规范。

集合和环境模板可直接导入 Postman。也可使用支持 OpenAPI 的其他调试工具，导入 baseline.json。联调后的环境可能包含真实密码/token，分享时使用空白模板。

### 集合变量

| 变量 | 如何填写/获得 |
| --- | --- |
| base_url | 实际服务根地址，无末尾 `/api/v1`，建议去掉末尾 `/` |
| login_email / login_password | 前端/业务测试账号；默认邮箱 analyst@sense2act.local，密码由负责人提供 |
| admin_email / admin_password | 仅后端负责人准备数据时填写 |
| internal_api_key | 三个服务接入方填写获授权的 key |
| jwt_token / admin_token | 登录请求成功后自动保存 |
| source_id / document_id / signal_id / investigation_id | 创建或领取后自动保存，可改为本次联调对象 |
| question_id / evidence_id / report_id | 相应写入成功后自动保存 |
| run_id | 准备数据时自动生成，区分联调实例 |
| rule_id / rule_version | 读取启用规则时更新；真实检测算法应使用实际规则版本 |

不需要手工构造实体 ID。集合中的 `team-demo` adapter、example.com 地址和信号计算值只是接口样本；接入真实爬虫时，负责人应配置该爬虫实际支持的 adapter 和信息源。

## 4. 第一次联调：先走一条最小闭环

不要直接运行整集合：页面示例包含尚未产生的 ID，SSE 请求是持续连接。按以下顺序手动发送，成功后变量会自动更新：

1. 后端负责人：检查健康 → 管理员登录 → 建立联调信息源。
2. 前端同学：业务用户登录，确认 `/users/me` 和看板能读。
3. 爬虫同学：读取到期源 → 提交带 `publish_date` 和 `raw_html` 的文档 → 回报抓取结果。重复同一文档应计为 duplicates。
4. 检测同学：读取启用规则 → 待读文档 → 推送信号 → 标记扫描完成。
5. 前端同学：执行“人工开启本次信号调查”。样本 score=0.60，在默认阈值 0.85 下等待人工处理。若平台策略改变导致返回 auto_investigated=true，则直接进入 Agent 阶段，不再重复开调查。
6. Agent 同学：查询待领取调查 → start → context → 记录第一轮开始 → 创建问题 → 记录提问事件 → 上报工具步骤 → 登记证据 → 回答问题 → 提交报告 → complete。
7. 前端同学：查报告、读证据快照、下载 Markdown、提交反馈，确认看板/图谱变化。
8. 另开调查 SSE 或全局 SSE，验证实时变化和断线重连。开始观察实时过程时，应在 Agent 上报前建立连接；连接后也会补发历史事件。

验收结果：调查 completed、报告 published、信号 confirmed，报告所有结论有证据，快照可读取，反馈可写，看板数字一致。联调完成后，负责人可用 admin 将测试源 PATCH 为 enabled=false。

也可以先由负责人运行 `scripts/demo_s7_full_chain.py` 生成完整页面样本，再让前端接页面。运行脚本时设置 BASE、INTERNAL_API_KEY、SEED_ADMIN_PASSWORD、SEED_ANALYST_PASSWORD 为联调环境值。

## 5. 前端接入要点

- 所有页面统一配置后端基础地址，使用 `/api/v1` 前缀。
- JSON 正常响应为 `{code,message,data}`，成功 code=0；分页从 data.items/total/page/page_size 读取。
- 金额是小数字符串；publish_date 是 YYYY-MM-DD，时间戳为带时区 ISO 8601。
- 调查详情从 data.investigation 和 data.questions 读取。
- EventSource 用 `/api/v1/stream?token=JWT` 或 `/api/v1/investigations/ID/stream?token=JWT`；监听命名事件，连接重试带 Last-Event-ID，按事件 id 去重。JWT 失效后需重新登录/取得 token 并重建连接。
- 全局流目前全员共享；具体调查页面使用对应 investigation_id 的调查流。页面刷新时结合列表/详情恢复显示。
- 快照和 Markdown 导出是文件响应，使用带 JWT 的 fetch 读取 Blob/文本，不当 JSON 解析；直接打开不带认证的受保护 URL 会收到 401。
- PDF 目前是占位，不做持续轮询下载；页面开放 Markdown。

## 6. 三个外部服务的接入要点

### 爬虫

后端不抓取公告、不解析页面。爬虫按下发 adapter/config 自行采集，推送结构化字段与原文。保留准确的发布时间和 raw_html，否则文档可以入库，但无法作为完整的本库证据登记。处理 accepted/duplicates/failed 三类结果；成功 IDs 只表示真实提交的数据。

### 检测

读取队列中的 org_stats、profiles、rules，算法在检测服务内实现。后端存储 hits/score/detector，不替检测服务计算得分。rule_id/rule_version 用实际读取版本；仅信号落库不会替服务完成扫描标记，处理后还需调用 detection-scan-complete。

### Agent

队列查询不是占用，必须 start 成功后执行。工具属于 Agent 自己，查本库文档/机构直接用内部 key；LLM、提示词、工具执行循环不放进后端。

- 上报 steps 才形成时间线/SSE。创建/修改问题保存问题清单；要实时呈现提问或反思，还应上报 questions_generated/reflection_updated 等步骤。
- Agent 可直接上报 round_started/questions_generated/tool_selected/tool_completed/reflection_updated。启动、预算、报告就绪和终态事件由后端派生，不能伪造。
- 每次上报真实 token_usage，使用稳定 request_id；超时后重试相同步骤保留同 key/内容，避免重复记账。同 key 改内容返回 40901。
- 每条报告 claim 必须有本调查已登记的 evidence_ids；nature 只用 fact/inference/speculation。报告 meta 以步骤累计为准。
- 用户停止后终止执行，迟到写入会被 40901 拒绝。
- 重启先查 investigating/reporting、context、内部 steps。reporting 说明报告已提交，可执行收尾；终态不再写入。
- 当前采用单个 Agent 工作进程，可在其内部管理并发任务；不让多个进程同时接管同一调查。手工集合和真实 Agent 也应避免同时执行同一任务。

## 7. 出错时团队怎么协作

| 响应 | 接入方下一步 |
| --- | --- |
| 40001 | 核对参数名、枚举、格式及必填字段，修正后再发 |
| 40101 | 核对 JWT 是否有效或 key 是否匹配；JWT 失效重新登录 |
| 40301 | 核对角色权限；内部接口整组禁用时由负责人配置 INTERNAL_API_KEY |
| 40401 | 核对实体 ID、联调环境及快照文件 |
| 40901 | 重新读取状态；start 时 created 可在并发额度释放后重试，已 investigating 不重复领取 |
| 42201 | 查看预算/轮次、证据完整性/归属，修正业务输入或结束调查 |
| 42901 | 遵循 Retry-After，降低轮询频率并退避 |
| 50001 | 交给后端负责人定位，提供可复现请求和日志时间 |

反馈问题时提供：部署版本、时间（含时区）、角色、请求方法/路径、去掉认证信息后的请求体、HTTP 状态/业务 code、相关实体 ID、期望与实际行为。不在问题截图或公共记录中附 JWT/内部 key。

后端升级按 api-design.md §11 通知接入方，再同步 OpenAPI、集合与部署版本。尤其同步本轮修复：检测金额为字符串、每条结论有证据、本库证据有发布时间和原文快照、终态拒绝新写入。
