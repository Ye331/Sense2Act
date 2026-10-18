# 后端架构与接入准备度检查

本文件保留初次检查的复现证据。后续修复已完成，当前功能与验收条件见 [user-stories.md](user-stories.md)，接入契约变更见 [api-design.md](api-design.md) §11；最终验证记录见本文末尾。

检查日期：2026-10-14。范围：本仓库的架构说明、接口契约、Java 实现、数据库迁移、部署配置、现有测试及独立数据库上的 HTTP 复现。没有修改业务代码。

## 结论

整体分层、模块职责和外部服务调用方向符合项目的平台型单体架构，主要业务接口已实现，正常链路能够跑通。但部分硬性不变量没有真正强制，异常批次会出现成功响应与实际存储不一致，跨域前端和容器配置也有接入缺口。当前适合开展受控联调，不能认定已经全面做好接入准备。

| 检查项 | 判断 | 依据 |
| --- | --- | --- |
| 平台与外部算法/Agent 的职责边界 | 基本符合 | 爬虫、检测算法、LLM/Agent loop 没有落入本后端；内部 HTTP 接口接收产物。embedding 是架构已登记的例外。 |
| 分层、数据库与迁移 | 基本符合 | api/ingest → domain → common；PostgreSQL/pgvector、MyBatis、7 个 Flyway 迁移。模块边界测试验证分层，领域之间允许协作。 |
| 正常业务闭环 | 已跑通 | 预先建立合法信息源后，实际执行文档 → 信号 → 调查 → 证据 → 报告 → 反馈 → 看板/图谱。 |
| 状态、预算、证据链不变量 | 不完全符合 | 下列预算溢出、无证据报告、缺字段证据和终态问题修改均有 HTTP 复现。 |
| 外部服务接入 | 接口主体具备，仍有缺口 | 三组内部 API 存在；规则参数下发缺失，批量写入异常行为不可靠，任务故障恢复需要补充约定。 |
| 前端接入 | 同源可联调，跨域尚未就绪 | 查询、分页、JWT、SSE、Markdown 导出具备；浏览器跨域预检实测 401，OpenAPI 没有认证定义。 |
| 容器部署 | 配置传递需要修补 | .env 中多个运行参数没有传入 API 容器。 |

## 接入前优先修复：已复现或直接验证的问题

### 1. P1：预算整数溢出，接受超预算写入

位置：`src/main/java/com/sense2act/backend/domain/investigation/InvestigationService.java:244`。

`long newUsed = inv.getTokenUsed() + usage` 的加法先按 int 运算，再赋值给 long。实测在已消耗 1 token、预算 60000 的调查中，上报 `token_usage=2147483647` 返回 HTTP 200。随后 `token_used=-2147483648`、`cost_estimate=-64424.5094`，违反预算和费用不变量。

修复：在加法前提升为 long，校验预算和 INT 存储范围；用锁或条件更新保证预算检查与写入的一致性。回归测试应断言异常输入返回 42201 且调查和步骤完全不变。

### 2. P1：批次中的数据库错误导致整批回滚，却返回接收成功

位置：`src/main/java/com/sense2act/backend/ingest/DocumentIngestService.java:71`、`:97`、`:208`。

整个批次共用一个事务，逐条捕获异常不能恢复 PostgreSQL 已失败的事务。实测依次提交正常条目、超出 NUMERIC(18,2) 范围的金额、正常条目：HTTP 200 返回 `accepted=1, failed=2` 和 document_id，但查询该批次实际为 0 条。第一条被回滚，第三条也没有被接收。

这直接违背“单条失败不阻塞批次”的规则；并发去重撞唯一约束也可能进入同一失败路径。修复应采用每条独立事务/保存点，或保证可预知错误先验证并以 ON CONFLICT 处理重复，最终统计仅计入已提交记录。需要增加金额越界和并发重复接入测试。

### 3. P1：无证据结论能形成已发布报告

位置：`src/main/java/com/sense2act/backend/domain/report/ReportService.java:199`、`:213`。

只校验证据引用存在，没有要求每条 claim 至少引用一条证据。实测 `nature=fact, confidence=0.99, evidence_ids=[]` 的报告提交成功，随后 complete 也成功，调查进入 completed，报告进入 published。

修复：按项目“每个结论都挂证据”的规则验证每条 claim 的非空证据列表，再校验登记状态及调查归属。保留 fact/inference/speculation 原样输出。

### 4. P1：引用本库文档的证据可以没有发布时间和快照

位置：`src/main/java/com/sense2act/backend/domain/investigation/EvidenceService.java:91`。

doc_id 变体直接复制文档字段，没有验证文档是否有发布时间及可读取快照。实测引用不含 publish_date/raw_html 的合法文档，登记接口返回 HTTP 200，证据中没有 published_at、snapshot_key。

修复：证据登记时确保必要字段及快照实际存在；缺失时明确拒绝或按已约定方案补齐。仅有 content_hash 不能替代原文快照。

### 5. P1（跨域前端场景）：浏览器预检被拒绝

位置：`src/main/java/com/sense2act/backend/config/SecurityConfig.java:40`、`:60`；`scripts/deploy/nginx.conf.example`。

仓库没有 CORS 配置，代理示例也没有同源前端托管或 CORS 处理。实测从 `http://localhost:5173` 发往文档接口、带 Authorization 的 OPTIONS 预检返回 HTTP 401/40101，没有 Access-Control-Allow-Origin。开发服务器或独立前端域名直接访问将失败。

修复选择：明确前端同源代理方案，或配置允许的前端 Origin、方法和请求头，并让合法预检在认证前完成。SSE 和文件下载也应从实际浏览器验证。若采用同源反代，此问题不阻塞该部署方式。

### 6. P1：部署文档中的 .env 参数没有传入 API 容器

位置：`docker-compose.yml:26`；`.env.example:36`。

检查合并后的生产 compose 配置，API environment 只有数据库、JWT、内部 key、embedding endpoint/key、快照、profile 和 TZ 等项。没有 SEED_USERS_ENABLED、SEED_*_PASSWORD、INVESTIGATION_CREATED_TTL、TOKEN_UNIT_PRICE、INTERNAL_RATE_LIMIT_PER_MINUTE，也没有 EMBEDDING_TIMEOUT。

因此按 README 在 .env 改种子密码/关闭种子、调整超时或限流，并不会改变容器应用的行为；新数据库仍会按默认种子密码创建账号。修复应显式映射这些变量，并在部署检查中验证实际容器配置。已有账号的密码不会因为重新设置种子参数而更新，需要另外安排修改。

### 7. P1（规则管理闭环）：检测服务拿不到平台管理的规则参数

位置：`src/main/java/com/sense2act/backend/domain/signal/SignalService.java:77`、`:105`、`:132`；`src/main/java/com/sense2act/backend/config/SecurityConfig.java:54`。

架构 §4.6 说 signal_rules.params 经 detection-queue 下发，但当前响应只有 document、org_stats、profiles，没有启用规则及 id/version/params/weight。持 X-Internal-Key 请求 /admin/signal-rules 实测 401，管理面要求 admin JWT。

基础检测可以使用服务自己的固定配置接入，但平台启停规则、新版本生效、命中版本留痕没有完整的数据获取路径。应以增量可选字段或专用内部只读接口补齐；不要把管理员账号作为机器服务的默认接入凭据。同步澄清 api-design 与 architecture 的描述差异。

### 8. P2：调查停止后仍能修改问题

位置：`src/main/java/com/sense2act/backend/domain/investigation/InvestigationService.java:373`。

updateQuestion 没有检查所属调查状态。实测调查 stop 成功后 PATCH 问题，仍返回 HTTP 200 并改为 clarified。延迟到达的 Agent 写入会改变已结束调查的判断材料。

修复：统一问题、步骤、证据、报告的可写状态规则，校验与更新在同一受保护事务内完成，终态拒绝写入。补充 stopped/completed/failed 的回归测试。

### 9. P2：检测队列金额类型偏离契约

位置：`src/main/java/com/sense2act/backend/domain/signal/SignalService.java:114`。

其他接口的金额为字符串，但 detection-queue 将 BigDecimal 放入 Map 后按 JSON number 输出。实测 amount 为 `3250000.00` 的文档在该接口输出数字 `3250000.00`，而 api-design §9.2 定义为字符串。严格类型的服务 DTO、前端生成类型及金额精度处理会受影响。

修复：确定并统一金额类型；已冻结接口须按契约变更流程处理，不直接静默改变消费者看到的类型。

### 10. P2：推荐演示入口不能从空库独立启动

位置：`scripts/demo_s7_full_chain.py:52`、`:54`。

空库实测脚本退出码 1，源创建 HTTP 400。脚本提交 type=gov，后端只接受 web_page/api/rss；cron 为五段 `0 6 * * *`，后端要求 Spring 六段式。分别验证两个输入均返回 40001。预先建立合法源后，同一脚本正常完成全链路。

修复：同步脚本与真实接口枚举/cron 格式，并把“空数据库一键演示”纳入验收。

## 需要补充验证的可靠性问题：代码推导，未做故障注入

1. **SSE 补发与实时广播可能跳过历史事件。** 两种 stream 都先注册连接再查询历史，实时事件若先把 lastSeq/lastId 提升到较大值，随后发送历史时会把更小但未发过的事件丢弃。位置：InvestigationSseService.java:44、:87；GlobalNotificationService.java:48、:125。去重高水位不能替代回放与实时切换的顺序协调。建议增加可控制时序的并发测试，采用回放期间缓存实时事件或从已发送游标统一有序拉取。
2. **步骤写入与停止/报告提交未共享调查锁。** appendStep 先读状态与预算，再分配 MAX(seq)+1、更新余额，后续更新没有状态条件。单 Agent 顺序调用能跑通，但与用户停止并发或请求重试重叠时，可能出现终态追加步骤、seq 唯一冲突或记账覆盖。位置：InvestigationService.java:231、:247；InvestigationStepMapper.java:13。调查 start 的全局 advisory lock 已存在，不能据此认定每个调查的其他写入也受保护。
3. **全局通知日志与业务提交不是一个原子事务。** Events.java:18 在 afterCommit 发布，GlobalNotificationService.java:104 才插 global_events。业务提交后进程退出或事件插入失败的窗口没有持久补偿；现有 global_events 回放只能回放已经成功记录的事件。可让事件记录与业务同事务提交，再在提交后广播，无需先引入消息队列。
4. **Agent 崩溃后的恢复约定不充分。** 实现允许按 status 查询 investigating/reporting，有 context 和 steps 可读，但没有 worker 标识、租约、心跳或 investigating 的超时回收；只清理 created。单服务可以自行恢复已有调查，但多 worker 的恢复占用与重复写入约定不完整。若真实 Agent 要长期运行，应先明确单 worker 恢复方案；多 worker 再补租约和写入幂等。

## 前端与外部团队联调注意事项

- OpenAPI 基线及接口主体存在，但实测规范没有 securitySchemes 和 security 定义，Swagger 无法完整描述 JWT/X-Internal-Key 认证；Map 类型也使若干嵌套字段缺乏明确 schema。应补认证定义及核心 DTO，减少人工猜测字段。
- 金额按照最终冻结契约处理；分页为 data.items/total/page/page_size，调查详情为 data.investigation + data.questions，前端应以实际响应和契约为准。
- EventSource 使用已支持的 ?token=；验证 token 过期、断线重连和浏览器刷新后的状态恢复。快照/导出要处理文件响应，不能统一当 JSON 信封解析。
- PDF 是 backlog 已明确保留的占位：202 返回随机 task_id，没有实际生成或任务查询流程。原型前端应只开放 Markdown，或明确显示 PDF 尚不可用，不能一直轮询这个 task_id。此项属于既定范围限制，不要求为比赛补完整 PDF 系统。
- 当前单实例、全员同流是已声明的原型取舍，不能按多租户或多实例能力对外承诺。

## 接入放行条件

1. 修复预算、异常批次、证据链和终态写入，针对本报告的复现增加回归测试。
2. 补齐 compose 参数映射，明确前端 Origin/代理方式及检测规则获取契约。
3. 验证空库演示和正常链路，使用真实浏览器验证登录、列表筛选、调查时间线、证据快照与 Markdown 下载。
4. 三个真实外部服务各完成正常流程、重复请求、错误输入、超时/重启恢复、限流退避联调。
5. 对 SSE 回放竞争和并发写入完成专项验证，再宣称断线不丢事件及可靠恢复。

本次没有前端仓库或真实爬虫/检测/Agent 服务可用于端到端联调；完成后端检查不等同于这些集成已经验收。

## 检查证据

- 独立测试数据库使用 18080 的本机接口，不连接项目已有数据。
- HTTP 补查结果：`target/readiness-probe-results.json`；复现脚本：`target/readiness-probe.py`（两者均为忽略的检查产物，清理 target 会删除）。
- 现有测试：20 个测试套件、101 项测试，failures=0、errors=0、skipped=0。包括 OpenAPI 基线、模块边界、角色认证、采集、调查、证据/报告、SSE、反馈/回测及语义检索。
- 首次 Maven verify 的测试阶段全部通过，打包阶段在 Windows 绑定目录中重命名 JAR 失败。当时补查应用仍占用该产物；关闭补查应用后，单独执行 Maven package（跳过已完成的测试），结果 BUILD SUCCESS。因此测试与打包均已分别确认通过，但不能将首次 verify 描述为整体成功。
- 测试日志另有 Testcontainers 数据库结束后旧 Spring 上下文的定时任务连接错误，以及 Surefire 退出等待超时。这没有计为测试失败，但测试资源生命周期需要整理；不能据此推断生产数据库故障。

## 修复后复核

本次确认的 10 项缺口均已修复：预算用 long 校验并串行记账；文档逐条事务且并发去重；报告强制每条结论有证据；本库证据强制发布时间和可读取快照；新增可配置 CORS；补齐 compose 环境变量；检测队列及专用内部接口下发启用规则；终态问题写入拒绝；金额字符串契约统一；空库演示源配置、原文及结论样本同步修复。

并发调查写入与 SSE 顺序问题也已修复，全局通知日志在业务事务中写入。单 Agent 恢复增加内部 steps 查询、context 全量问题及可选 request_id；多 worker 租约仍不在单实例原型范围内。测试上下文在套件结束后关闭，SSE 连接随应用关闭完成，客户端断开不再尝试写 JSON 错误。

最终各套件报告共 114 项测试全部通过（含 13 项新增专项）；受最后改动影响的 23 项重跑通过并打包成功。更新后的 OpenAPI 基线另行比较通过。prod profile 独立空库的演示连续两次及 S3/S4/S5/S6 种子链路通过，29 项运行检查通过，生成 34 篇文档和 3 份已发布报告。检查日志见 target/acceptance/（运行产物）。

结论更新为：后端已具备本项目单实例原型的基础接入条件，适合交给前端和三个外部服务开展真实联调；真实 UI 和外部服务的上线验收仍未在本仓库执行。用户故事及完整验收条件见 user-stories.md。
