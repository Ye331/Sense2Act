# 敏捷开发计划

文档分工，一处一个事实：契约看 [api-design.md](api-design.md)，表结构看 [data-model.md](data-model.md)，设计与取舍看 [architecture.md](architecture.md)，**"做什么、什么算完、什么顺序"只看本文件**。冲突时以 AGENT.md 和 api-design.md 为准。

## 1. 角色与价值

| 角色 | 是谁 | 关心什么 |
| --- | --- | --- |
| admin | 平台管理员 | 信息源、规则、调查策略、回测 |
| analyst | 分析师 | 信号决策、调查过程、报告与证据链 |
| viewer | 只读用户 | 报告、看板 |
| 外部服务 | 爬虫 / 信号发现 / Agent 调查 | §9 契约冻结、随时可独立联调 |

产品一句话：让分析师在噪音里先人一步看到值得深挖的招标信号，并拿到每条结论都有证据支撑的报告。

## 2. 就绪（DoR）与完成（DoD）定义

**故事进入冲刺前（DoR）**：

1. 一句话用户价值（作为 X，我要 Y，以便 Z）。
2. 验收标准可测，能直接翻译成测试用例。
3. 尺寸 ≤ L，超过就拆。
4. 依赖已就绪，或明确写了占位方案。
5. 契约涉及面已标注：沿用 / 新增可选字段（非破坏性）/ 待变更（破坏性，走变更流程）。

**故事算"完成"（DoD）**：

1. 验收标准全绿；无游离 TODO——要么做完，要么拆回 backlog。
2. 不变量在 API 层强制且有自动化测试证明：状态机/轮次/预算/证据引用 → 40901/42201，参数问题 → 40001。
3. 遵守 AGENT.md 硬性规则：统一响应包、错误码表、带前缀 ULID、金额字符串小数、ISO 8601 带时区、快照 SHA-256、报告 disclaimer、claim 的 nature 原样带出。
4. Flyway 可从空库一键重建；单测 + 集成测试通过，CI 绿，已合入主干。
5. swagger 所见即 api-design.md 所写；契约改动先在 api-design「变更记录」登记再合码。
6. 可演示：本故事产物有种子数据或驱动脚本覆盖，不依赖任何外部服务。

## 3. 优先级与尺寸

优先级 MoSCoW：**M** 必须（缺了链路断）/ **S** 应该（缺了体验残）/ **C** 可以（时间紧先砍，砍掉不 demo 不受损）。尺寸：**S** ≤ 半天 / **M** ≤ 2 天 / **L** ≤ 3 天。估算只用 S/M/L，不引入故事点。

## 4. 史诗与用户故事

### E0 走通骨架（S1）

> 目标：一条命令起环境，登录能调通接口，CI 挡住坏提交。后面所有故事踩在这套地基上。

#### E0-1 一键起环境 · M · M
作为开发者，我要 docker-compose 一条命令起 api + postgres（pgvector），以便任何人环境一致。

- [x] `docker-compose up` 后 `/actuator/health` 返回 200
- [x] Flyway 首个迁移自动执行：建 pgvector 扩展
- [x] 快照目录挂卷，容器重建不丢数据
- [x] 环境差异全走环境变量：`.env.example` 提交、`.env` 进 .gitignore（DB 口令、JWT 密钥、INTERNAL_API_KEY、快照目录、时区）
- [x] Dockerfile 多阶段构建（构建期 JDK、运行期 JRE），S1 就有，不后补；devtools 只挂 local profile，不进生产镜像

#### E0-2 CI 流水线 · M · S
作为开发者，我要 push 自动跑构建和测试，以便坏提交进不了主干。

- [x] push / PR 触发 build + test，失败阻止合入
- [ ] 提交 openapi.json 基线，PR 跑 oasdiff 破坏性变更检测；红了必须先走 api-design §11 变更流程再合码
- [x] README 写明本地一键跑测试的命令

#### E0-3 响应与错误框架 · M · M
作为用户，我要所有接口返回统一结构，以便前端和外部服务统一处理。

- [x] 成功 `{code:0, message, data}`；分页 `{items, total, page, page_size}`
- [x] 全局异常处理覆盖错误码表（api-design §1），未知异常落 50001
- [x] `page_size` > 100 拒绝并返回 40001（决策 D1）
- [x] 带前缀 ULID 生成器就位，新实体一律使用

#### E0-4 认证与角色 · M · M
作为用户，我要用账号换 JWT 调接口，以便按角色控制权限。

- [x] `POST /auth/token` 签发 JWT，`GET /users/me` 返回当前用户
- [x] 种子预置 admin / analyst / viewer 各一个，密码 pbkdf2 哈希（迭代数按 OWASP 建议，D11）
- [x] 无 token 或坏 token → 40101；viewer 执行写操作 → 40301
- [x] 内部接口认证框架就位：`INTERNAL_API_KEY` 未配置时 `/internal/*` 整组 403（首个内部接口在 E1-2 验证）

#### E0-5 服务器部署基线 · S · M
作为演示者，我要骨架在 S1 就真部署到服务器一次，以便部署风险最早暴露，而不是最后才踩坑。

- [x] `docker-compose.prod.yml`：restart: unless-stopped；postgres 端口不暴露公网；快照目录挂卷
- [x] 反向代理（nginx/caddy）配置放 `scripts/deploy/`：TLS、SSE 关缓冲（proxy_buffering off、读超时拉长）
- [ ] 服务器上 INTERNAL_API_KEY 必须已配置（未配置整组 403 是兜底，不是默认态）
- [x] 部署步骤写进 README（git pull → compose up -d --build），可重复执行；服务器未就绪则顺延，不晚于 S8

#### E0-6 模块边界验证 · C · S
作为开发者，我要 CI 强制校验模块边界，以便后端长歪（跨模块乱引用内部类）时第一时间被拦。

- [ ] 用 Spring Modulith 的测试支持或 ArchUnit 写边界验证测试，进 CI
- [ ] 在 E1–E5 模块长全后（≥S6）启用；豁免走白名单并记录原因

### E1 采集与文档库（S2–S3）

> 目标：文档进得来、查得出。爬虫三接口是本史诗对外部团队的第一组契约交付。

#### E1-1 信息源管理 · M · M
作为 admin，我要增删改查信息源并配置采集计划，以便爬虫服务按计划领取。

- [x] `GET/POST/PATCH/DELETE /sources` + `POST /sources/{id}/run`（仅 admin，越权 40301）
- [x] config（JSONB）原样存取；分页符合约定
- [x] `run` 后该源立即出现在 ingest-queue 结果中（决策 D2）

#### E1-2 到期源下发 · M · M · 依赖 E1-1
作为爬虫服务，我要领取当前到期的源及其全部配置，以便抓取解析。

- [x] 到期判定只按 `schedule_cron` + `last_run_at`，在后端完成
- [x] 响应含 adapter / url / config / last_run_at，与 api-design §9.1 一致
- [x] 领取不产生副作用、不加锁：重复领取同一结果（去重兜底在入库）
- [x] `last_run_at` 只由 ingest-runs 更新，领取不更新

#### E1-3 批量推送文档 · M · L · 依赖 E1-2
作为爬虫服务，我要把解析好的文档批量推给后端，以便进入统一信息库。

- [x] content_hash 与归一化 URL 双重去重，重复计入 duplicates，不算失败
- [x] 单条失败跳过并计入 failed，不阻塞批次其余条目
- [x] org_name 归一（空白/全半角/别名）后 upsert organizations，响应条目带 org_id（org_id 落 documents 表并经测试断言；§9.1 契约的响应形状无逐条字段，以契约为准）
- [x] raw_html 落快照记 snapshot_key；新文档 `signal_scanned=false`
- [x] 入库后同步重算该 org × category 的 org_stats
- [x] 响应 `{accepted, duplicates, failed, document_ids}` 与契约一致

#### E1-4 运行报告与源健康 · M · S · 依赖 E1-3
作为 admin，我要看到每次采集的成败统计，以便坏源自动降级暴露。

- [x] `POST /internal/ingest-runs` 更新 last_run_status；错误逐条入 ingest_errors（stage 枚举校验）
- [x] 连续失败自动降级、成功恢复（阈值见决策 D3）
- [x] 降级时发全局 SSE `source_degraded`（事件流本身在 E2-7 落地，此处只发事件：事务提交后发 Spring 事件，测试已断言 degraded/down 各触发、恢复不触发）

#### E1-5 文档与机构查询 · M · L · 依赖 E1-3
作为 analyst，我要多条件检索文档、看原文快照和采购单位画像，以便判断线索价值。

- [x] `GET /documents` 支持契约全部筛选（keyword / doc_type / org_id / region / category / amount 区间 / 日期区间 / sort），默认按 publish_date desc（keyword 为多词 AND 子串匹配，适配中文；region 层级前缀；sort 白名单 publish_date/-publish_date/amount/-amount/created_at/-created_at）
- [x] 详情含 content_text、raw、related_signals；`/snapshot` 返回原始页面（signal_count=0、related_signals=[] 为占位，E2-2 落地后接真值）
- [x] `GET /organizations/{id}`：基本信息 + org_stats + 近期文档（近期 10 篇，按 publish_date desc）
- [x] 列表页字段与 api-design §3 示例一致（§1 的双认证同步落地：GET/HEAD 两接口 JWT 任意角色或 X-Internal-Key 均可，见 SemanticSearchTest/DocumentApiTest）

#### E1-6 语义检索 · S · M · 依赖开放问题 OQ1 的决策
作为 analyst，我要用自然语言找相似文档，以便关键词漏掉的能被语义兜住。

- [x] `semantic` 参数走 pgvector HNSW 检索；与 keyword 同给时用 RRF 融合两路排名（score = Σ 1/(k+rank)，k=60），纯 SQL 实现零新依赖（HNSW 索引 V2 已建，余弦 `<=>`；语义态下相关度排序覆盖 sort 参数）
- [x] embedding 来源按决策执行；文档无 embedding 时不出现在语义结果中，不报错（D12：入库后经 EMBEDDING_ENDPOINT 尽力补算，查询同端点实时算；端点未配/失败/维度不符 → 静默降级关键词路径，semantic 单给时语义文本按关键词兜底，绝不 5xx）

#### E1-7 演示链路①（采集） · M · S
作为演示者，我要空库一键灌入源和文档样本，以便外部服务不接入也能演示采集到检索。

- [x] `scripts/` 提供 ≥10 个源 + ≥30 条文档（含重复、多机构、多类别）与推送脚本（seed_s3.py：10 源 + 32 条，落库 30 篇——批内自带 URL 归一重复与 content_hash 重复各 1；另附 embedding_stub.py 演示 D12 端点）
- [x] 脚本可重复执行，幂等（源按名字去重；文档靠后端 URL/content_hash 去重，二跑全计 duplicates，2026-10-02 本地实测通过）

### E2 信号通道（S4，余项 S5/S8）

> 目标：信号亮得起。检测算法在外部，后端把输入喂到位、把结果接得住、把闸门开对。

#### E2-1 检测队列与扫描标记 · M · M · 依赖 E1-3
作为信号发现服务，我要一次拿齐检测所需的全部输入，以便不再查任何别的接口。

- [x] `GET /internal/detection-queue?limit=` 只返回 `signal_scanned=false` 的文档，默认 50、上限 100（决策 D9）
- [x] 响应内嵌 document + org_stats + profiles，结构按契约
- [x] `POST /internal/detection-scan-complete` 批量置 `signal_scanned=true`，重复调用幂等

#### E2-2 信号落库与查询 · M · M · 依赖 E2-1
作为信号发现服务，我要推送命中结果并保证幂等；作为 analyst，我要查询信号列表。

- [x] `POST /internal/signals`：同 document + detector + hits 重复推返回已有 signal_id，不新建不报错（幂等写入用 `ON CONFLICT DO NOTHING` 而非捕获唯一冲突——PG 里约束异常会打断事务，回读兜底走不通；幂等预查的序列化须保留 null 值键，与 JsonbTypeHandler 同语义，Spring 全局 non_null 会丢键导致永不匹配）
- [x] hits / score / detector 原样落库，后端不重算 score
- [x] 落库即发全局 SSE `signal_created`（事务提交后 afterCommit 发 Spring 事件；/stream 事件流本身在 E2-7 落地）
- [x] `GET /signals` 支持契约全部筛选（status / rule_type / score_gte / org_id / 日期），详情含 document 摘要与 hits
- [x] SSE 事件在数据库事务提交后发布（afterCommit），避免"事件先到、库里还没有"的竞态

#### E2-3 调查策略管理 · M · S
作为 admin，我要配置自动调查阈值和默认预算，以便控制成本闸门。

- [x] `GET/PUT /admin/investigation-policies`（仅 admin，PUT 全量必填），字段按 api-design §8
- [x] 种子带默认值；PUT 即时生效于后续自动触发
- [x] watch_profiles（关注画像）按 D13 单独建表：`GET/POST/DELETE /admin/watch-profiles`（/admin/** 整组仅 admin），重名 40901，新增/删除实时反映到 detection-queue 内嵌 profiles

#### E2-4 自动触发 · M · M · 依赖 E2-2、E2-3
作为 analyst，我要高分信号自动开调查，以便不手工漏掉重点。

- [x] score ≥ auto_investigate_threshold 时建 Investigation（created）并回填 investigation_id，signal 状态 pending → investigating（CAS）
- [x] 一信号至多一调查（唯一约束兜底并发）
- [x] 未达阈值留 pending 等人工决策
- [x] 并发上限语义按 D14 执行（created 排队不限量，start 时才挡——档位在 E3-1 落地）

#### E2-6 规则版本管理 · S · M
作为 admin，我要版本化管理信号规则并记录回测指标，以便反馈迭代有落点。

- [ ] `GET/POST/PATCH /admin/signal-rules`；PATCH 生成新版本并停用旧版，同 id 只有一个版本 enabled
- [ ] 种子预置 3 条默认规则 version=1（金额异常 / 频率突增 / 语义匹配），参数按 data-model §4

#### E2-7 全局通知流 · S · M
作为 analyst，我要一条全局 SSE 流收通知，以便不刷页面也知道有新信号。

- [ ] `GET /stream`：signal_created / investigation_completed / report_ready / source_degraded 四类事件
- [ ] JWT 经 `?token=` 传递（EventSource 不支持自定义 header）；无效 token → 40101
- [ ] 25 秒心跳注释；断线重连不丢已发事件（事件可追溯）

#### E2-8 演示链路②（信号） · M · S · 依赖 E2-4、E1-7
作为演示者，我要模拟检测脚本和信号样本，以便演示信号到自动开调查的跳转。

- [x] 脚本：拉 detection-queue → 对样本文档推 signals → 报 scan-complete，可反复执行（seed_s4_signals.py；文档定位队列优先、已扫描的走 /documents 关键词兜底，二跑 signal_id 不变）
- [x] 种子：对链路①文档生成 4 条信号，其中"AI辅助诊断系统采购"score=0.92 超默认阈值已自动开调查（2026-10-02 本地实测通过）

### E3 调查生命周期（S5）

> 目标：调查跑得动。后端不跑 agent loop，只做状态机、留痕、预算和推送；演示用脚本扮演 Agent 服务。

#### E3-1 领取与占用 · M · M · 依赖 E2-4
作为 Agent 调查服务，我要领取 created 状态的调查并独占开始，以便多个实例互不冲突。

- [x] `GET /internal/investigations?status=created` 按 created_at asc 返回
- [x] `POST /internal/investigations/{id}/start` 用 CAS 抢占：并发仅一个成功，其余 40901；记 started_at
- [x] 非 created 状态 start → 40901
- [x] D14 并发上限在 start 时挡；"先计数后 CAS"在 READ COMMITTED 下有竞态，领取前先过 `pg_advisory_xact_lock` 独木桥（`@Update` 声明，`@Select`+void 会触发 MyBatis 的 void 结果映射异常）
- [x] start 本身落一条 `investigation_started` 步骤（round=0），SSE 补发从领取开始完整可追溯

#### E3-2 调查查询与上下文 · M · M · 依赖 E3-1
作为 Agent 服务，我要一次拿齐上下文；作为 analyst，我要在用户接口里看调查列表和详情。

- [x] `GET /internal/investigations/{id}/context`：信号 hits + 文档 + 问题清单（open）+ 证据摘要 + 预算余量
- [x] `GET /investigations` 支持 signal_id / status / 日期筛选；详情含当前问题清单（api-design §5 结构）
- [x] evidences 表随 V4 先行建表（E4-1 只加登记 API）：E3-4 的引用校验需要真实目标表，不是游离占位

#### E3-3 步骤留痕与进度流 · M · L · 依赖 E3-1
作为 Agent 服务，我要上报步骤；作为 analyst，我要实时看到调查时间线。

- [x] `POST /internal/investigations/{id}/steps`：seq 在调查内单调唯一，写库即发 SSE，事件 id = seq
- [x] steps 的 event 名映射 api-design §7 的 SSE 事件表（question / tool_select / tool_call / reflection / status_change）
- [x] `GET /investigations/{id}/stream` 支持 Last-Event-ID 从 steps 表补发；25 秒心跳
- [x] `GET /investigations/{id}/steps` 游标分页
- [x] SSE 发布在事务提交后执行（同 E2-2 的 afterCommit 约束）
- [x] 超出 max_rounds 的步骤写入 → 42201
- [x] 事件名词表闸门：Agent 只可上报 round_started / questions_generated / tool_selected / tool_completed / reflection_updated；investigation_started / stopped / budget_update 由后端派生，completed / failed 属 E4-3，直报一律 40001
- [x] SSE 流的 JWT 走 `?token=`（EventSource 设不了 Authorization 头）：SseTokenParamFilter 在认证链前折叠成标准头，仅对 GET 流路径生效

#### E3-4 问题管理 · M · S · 依赖 E3-1
作为 Agent 服务，我要登记和回答问题，以便"为什么查这个"可追溯。

- [x] `POST /internal/questions` 挂调查与轮次；`PATCH /internal/questions/{id}` 更新状态 / answer_summary / evidence_ids
- [x] 状态枚举 open / clarified / unresolved / abandoned，非法值 40001
- [x] evidence_ids 引用未登记证据 → 42201（校验含归属：必须属于本调查）
- [x] 坑位：wrapper `.set()` 写 jsonb 列不吃实体上的 @TableField typeHandler，必须显式传 mapping 串

#### E3-5 预算记账 · M · M · 依赖 E3-3
作为 admin，我要 token 预算被强制执行，以便成本不失控。

- [x] steps 的 token_usage 累加进 token_used，发 SSE `budget_update`
- [x] 超出 token_budget 后的 steps 写入 → 42201；报告提交与收尾接口不受限
- [x] cost_estimate 按配置的单价折算累计（`TOKEN_UNIT_PRICE`，默认 0.00003 元/token；恰好用满 = 预算允许，超出才拒）

#### E3-6 停止与超时清理 · M · M · 依赖 E3-1
作为 analyst，我要能停掉跑偏的调查；作为 admin，我要无人领取的调查自动清理。

- [x] `POST /investigations/{id}/stop`：investigating / reporting → stopped，记 finished_at，发 SSE
- [x] 定时任务：created 超过 TTL（决策 D7）→ failed（error 注明超时未领取），任务幂等
- [x] 状态机全集由测试覆盖：合法转移放行，非法转移 40901
- [x] stop / TTL 清理后信号回退 pending（可再人工决策）；同一信号不可再开新调查（investigations.signal_id 唯一），investigation_id 留作追溯

#### E3-7 演示链路③（调查） · M · S · 依赖 E3-3、E2-8
作为演示者，我要模拟 Agent 驱动脚本，以便演示时间线实时滚动。

- [x] 脚本 v1：领取 → start → context → 按 round 推 steps / questions（暂不含报告）
- [x] 种子：一条 investigating 状态的调查，含完整 steps 与 questions，可续看时间线

### E4 证据与报告（S6）

> 目标：报告立得住。每条结论挂得上证据，事实与推测分开，图谱随报告一起落库。

#### E4-1 证据登记 · M · M · 依赖 E3-1
作为 Agent 服务，我要登记调查中取得的证据，以便报告结论可回溯到原文。

- [x] 引本库文档传 doc_id：自动回填 url / title / published_at，excerpt 仍必填（hash/快照键也一并复用本库文档）
- [x] 外部内容传 url / title / excerpt：后端抓快照、算 SHA-256，fetched_at 取服务端时间
- [x] 快照抓取带超时与有限重试（如 2 次），仍失败整条拒收 42201，由 Agent 服务换源重试（决策 D4）——5s 超时、共 3 次尝试、>2MB 拒收
- [x] published_at、url、fetched_at 缺一不可入库（外部变体 published_at 缺失 40001；本库变体从 publish_date 回填）

#### E4-2 报告提交与图谱落库 · M · L · 依赖 E3-3、E4-1
作为 Agent 服务，我要提交完整 ReportDraft，以便调查产出结构化结论。

- [x] claims ≥1；nature ∈ fact / inference / speculation，非法值 40001；confidence ∈ [0,1]
- [x] claim 引用未登记或不属于本调查的证据 → 42201，整份拒收
- [x] event_extraction：relations 的 source/target 必须指向本次声明的 entity 或 event，否则 40001；校验通过落四张图谱表（主体按 name 幂等复用，PG 约束不炸事务用 ON CONFLICT DO NOTHING）
- [x] meta（轮次/工具数/token/费用）由后端从 steps 统计填充，不采信上报值（决策 D5；draft.token_usage 仅对账参考不入账）
- [x] 落库发 SSE report_ready；报告响应固定带 disclaimer（report_ready 落为 status_change 步骤,可随流补发;一调查一报告,重复提交 40901）

#### E4-3 收尾 · M · S · 依赖 E4-2
作为 Agent 服务，我要在报告提交后收尾或失败退出，以便状态机闭环。

- [x] `complete`：有已提交报告才允许，否则 42201；置 completed、finished_at，signal → confirmed（决策 D6；报告随收尾转 published）
- [x] `fail`：非终态均可，置 failed 并记 error（信号回退 pending,同 stop/TTL 口径）
- [x] 两者均发对应 SSE（investigation_completed / investigation_failed）

#### E4-4 报告查询 · M · M · 依赖 E4-2
作为 analyst，我要查报告全文，以便逐条核证据。

- [x] `GET /reports` 列表（investigation_id / 日期筛选）；详情结构 = api-design §6 示例
- [x] claims 带证据摘要（id / title / source_type），nature 原样带出
- [x] disclaimer 固定文案逐字一致（"本报告由AI生成，结论不替代人的自主判断"）

#### E4-5 证据查询 · S · S · 依赖 E4-1
作为 analyst，我要查证据详情和快照，以便核验结论来源。

- [x] `GET /evidences/{id}` 返回全字段；`/snapshot` 返回抓取的原始内容（无快照/文件缺失 40401）

#### E4-6 报告导出 · S · M · 依赖 E4-4
作为 analyst，我要导出报告，以便线下传阅。

- [x] `format=md` 同步返回，模板含 disclaimer、claims（按 nature 分节）与证据清单
- [x] `format=pdf` 返回 202 + task_id，轮询接口占位（C 级，真排版 S8 视情况，见 OQ5）

#### E4-7 演示链路④（报告） · M · M · 依赖 E4-2、E3-7
作为演示者，我要完整报告样本，以便演示证据链和"事实/推测"分离。

- [x] 种子：3 条 claims 覆盖三种 nature、≥2 条行动建议、图谱 1 事件 3 实体，挂在链路③调查上直至 completed（seed_s6_report.py,幂等）
- [x] 模拟 Agent 脚本 v2：在 v1 基础上补 evidences → report → complete 全程（含问题带证据 clarified、md 导出落盘）

### E5 反馈与看板（S7–S8）

> 目标：反馈闭得上环。用户操作沉淀为标注，标注喂养回测，回测产出新规则版本。

#### E5-1 报告反馈 · M · M · 依赖 E4-4
作为 analyst，我要对报告采纳建议或标误报，以便好信号被学习、坏信号被纠偏。

- [ ] `POST /reports/{id}/feedback`：adopted_suggestion_ids 回填 action_suggestions.adopted_at
- [ ] comment / flag（false_positive）写 feedback_events，user 取自 JWT
- [ ] target_type 覆盖 report / suggestion，与信号级反馈（E2-5）共用一张表

#### E5-2 看板 · S · M
作为 viewer，我要一个总览数字，以便快速判断系统活跃度和信号质量。

- [ ] `GET /dashboard/summary`：文档 / 信号（按状态）/ 调查（按状态）/ 报告计数 + 今日增量
- [ ] 数字与库内一致（测试用种子数据断言）

#### E5-3 图谱查询 · S · M · 依赖 E4-2
作为 analyst，我要浏览事件图谱，以便发现跨项目的关联。

- [ ] `GET /events` 分页；`/{id}` 详情含参与实体；`/{id}/graph` 返回节点 + 边 + 关联报告

#### E5-4 回测任务 · C · M · 依赖 E2-6、E5-1
作为 admin，我要发起规则回测并拿到数据集，以便评估在外部跑。

- [ ] `POST /admin/backtests`：rule_id + param_grid + 日期区间，status=queued
- [ ] 后端导出数据集：区间内文档 + signals + feedback_events 标注
- [ ] 状态机 queued / running / done / failed；result（JSONB）支持外部写回

#### E5-5 内部接口限流 · C · S
作为 admin，我要内部接口按 key 限流，以防某个外部服务拖垮后端。

- [ ] `/internal/*` 按 X-Internal-Key 滑窗计数，超阈值 42901（阈值可配；实现选型 Bucket4j，单机内存即可）

#### E5-6 演示链路⑤（闭环） · M · S · 依赖 E5-1
作为演示者，我要一键回放全链路，以便比赛评审看到完整闭环。

- [ ] 全链路脚本：源 → 文档 → 信号 → 调查 → 报告 → 反馈，一条龙可重复执行
- [ ] 种子补反馈事件；README 增加演示路径说明

## 5. 冲刺计划

参数假设：**1 周一个冲刺，容量约 1.5–2 人（含 AI 辅助）**。计划会上可调参数，优先级不动。每个冲刺周五 review，demo 一律用种子数据和脚本，不依赖外部服务。超载先砍 C、再顺延进顺延桶，DoD 不打折。

| 冲刺 | 目标 | 故事 | review 演示 | 顺延桶 |
| --- | --- | --- | --- | --- |
| S1 | 走通骨架 | E0-1..5 | compose up → swagger → 登录 → 示例接口看响应包与错误码；E0-5 在服务器重复跑通一遍 | E0-5（服务器未就绪时顺延，不晚于 S8） |
| S2 | 数据进得来 | E1-1..4 | 建源 → 领取 → 脚本推文档（含重复/坏条目）→ 查库验证去重与 org_stats | — |
| S3 | 数据查得出 | E1-5、E1-6、E1-7 | 空库灌种子 → 筛选检索 → 详情 → 快照 → 机构画像 | E1-6（若 OQ1 未决） |
| S4 | 信号亮得起 | E2-1..5、E2-8 | 模拟检测脚本推信号 → SSE 弹出 → 超阈值自动开调查 → 忽略一条并填理由 | E2-6、E2-7 |
| S5 | 调查跑得动 | E3-1..7 | 模拟 Agent 脚本驱动调查，时间线 SSE 实时滚动；断线重连补发；超预算写入被拒 | E3-4（并入 S6 首日） |
| S6 | 报告立得住 | E4-1..7 | 提交带证据报告 → 报告页逐条核证 → 导出 md；伪造未登记证据整份被拒 | E4-6 |
| S7 | 反馈闭得上环 | E5-1..3、E5-6 + 顺延项 | 采纳建议/标误报 → 看板数字变化 → 全链路回放 | — |
| S8 | 缓冲与联调 | E5-4、E5-5、E0-6、pdf 占位、外部服务联调、缺陷修复、演示彩排 | 比赛口径全链路彩排 | — |

S8 是显式缓冲：只排 C 级故事、联调与打磨，不加新范围。

## 6. 里程碑映射

对外仍可用里程碑沟通，与史诗一一对应，不在两处重复维护细节：

| 里程碑 | 史诗 | 冲刺 | 可演示状态 |
| --- | --- | --- | --- |
| M0 走通骨架 | E0 | S1 | 环境、认证、CI、首次服务器部署 |
| M1 数据底座 | E1 | S2–S3 | 采集接入 + 文档检索 |
| M2 信号通道 | E2 | S4 | 检测三接口 + 信号决策 + 自动触发 |
| M3 调查生命周期 | E3 | S5 | 状态机 + 留痕 + SSE + 预算 |
| M4 证据与报告 | E4 | S6 | 证据链 + 报告 + 导出 |
| M5 反馈与看板 | E5 | S7–S8 | 反馈闭环 + 看板 + 回测 |
| R 外部服务（并行） | — | S2 起随时联调 | 契约冻结，种子数据 + swagger 即联调环境 |

## 7. 开发过程

- **节奏**：周一 30 分钟计划会（按优先级拉故事、拆 ≤1 天的任务）；每天 10 分钟同步；周五 review（demo）+ retro（留 1 条可执行改进进下冲刺）。
- **容量**：连续两个冲刺校准一次；超载按"砍 C → 顺延"处理，不压 DoD。
- **需求变更**：新想法一律先进 backlog 排序，不打断当前冲刺；阻塞级缺陷例外。
- **契约变更**：api-design §9 的任何改动都是破坏性变更——先登记其「变更记录」、通知外部团队、review 会宣布，再动代码；仅新增可选字段除外。
- **与外部团队**：S2 起后端即提供种子数据 + swagger 作为联调环境；联调问题按缺陷进当前冲刺。

## 8. 决策记录（本计划补充的，评审可推翻）

| # | 决策 | 落点 |
| --- | --- | --- |
| D1 | `page_size` > 100 返回 40001 而非截断 | E0-3 |
| D2 | `POST /sources/{id}/run` 即置为到期，下轮 queue 立即可领 | E1-1 |
| D3 | 源健康降级阈值：连续 3 次失败 → degraded，10 次 → down，成功即恢复 | E1-4 |
| D4 | 证据快照抓取失败整条拒收（42201），由 Agent 换源重试 | E4-1 |
| D5 | 报告 meta 由后端从 steps 统计，不采信 Agent 上报 | E4-2 |
| D6 | `complete` 时 signal → confirmed | E4-3 |
| D7 | created 调查超时 TTL 默认 60 分钟，可配 | E3-6 |
| D8 | 20 张表按史诗分批建迁移，而非一次建齐——每次迁移可评审、可回滚 | 全局 |
| D9 | detection-queue limit 默认 50、上限 100 | E2-1 |
| D10 | ORM 选 MyBatis-Plus（2026-10-01 拍板，OQ4 关闭，此后不再更换） | 全局 |
| D11 | 密码哈希选 pbkdf2（Spring Security 内置，迭代数按 OWASP 建议），不引 BouncyCastle | E0-4 |
| D12 | embedding 全部由外部计算：文档向量入库后由后端经 EMBEDDING_ENDPOINT 补算，查询向量检索时同端点实时算（2026-10-02 拍板，OQ1 关闭）。后端不含模型/提示词，仅一次无状态数据面出站调用，登记为架构边界的例外 | E1-6 |
| D13 | watch_profiles 单独建表（不放 investigation_policies 的 JSONB）：id/name 唯一/enabled，admin 经 /admin/watch-profiles 管理，detection-queue 内嵌输出启用中的画像名（2026-10-02 拍板，OQ2 关闭，未采"存 policies JSONB"建议——独立表可查可索引） | E2-3 |
| D14 | max_concurrent_investigations 在 start 时生效：created 排队不限量；超出并发上限 start 返回 40901，Agent 稍后重试，与"被占用"同错误码（2026-10-02 拍板，OQ3 关闭，采建议） | E2-4 |

## 9. 开放问题（带决策时点）

| # | 问题 | 建议 | 决策时点 |
| --- | --- | --- | --- |
| ~~OQ1~~ | ~~documents.embedding 由谁计算？~~ **已决（D12）**：外部算——后端配置 EMBEDDING_ENDPOINT，入库补算 + 查询实时算；端点不可用时文档不进语义结果、不报错（关键词兜底） | D12 | 已关闭 |
| ~~OQ2~~ | ~~detection-queue 里的 profiles（关注画像）存在哪？~~ **已决（D13）**：单独建 watch_profiles 表 | D13 | 已关闭 |
| ~~OQ3~~ | ~~max_concurrent_investigations 的语义？~~ **已决（D14）**：start 时挡，超出返回 40901；created 排队不限 | D14 | 已关闭 |
| OQ5 | pdf 导出的真实度 | 原型期 202 + 轮询占位；真排版视 S8 余量 | S6 计划会 |

## 10. 风险

| 风险 | 应对 |
| --- | --- |
| 三个外部服务进度不可控 | 契约已冻结；每冲刺 demo 只靠种子和脚本；联调窗口在 S8 留足 |
| SSE 补发、CAS 抢占、批量去重的并发正确性 | 不做纸上设计，直接以集成测试锁行为（DoD #4） |
| 语义检索质量不达预期 | E1-6 是 S 级，关键词全文兜底；OQ1 未决不影响主线 |
| 比赛导向的范围蔓延 | MoSCoW 明示砍序；每冲刺 demo 优先于一切加需求 |
| 单点人力 | 故事 ≤2 天为主、文档即契约、AI 助手按 AGENT.md 约束工作 |

## 11. 非目标

原型期不做（详见 AGENT.md 边界）：多实例与 Redis/ShedLock、Webhook、MinIO 迁移、大表分区、LLM 调用、抓取解析与检测算法与 agent loop（外部服务）、pdf 真排版（S8 视余量）。
