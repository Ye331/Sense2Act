# API 设计

Base URL：`/api/v1`。REST + SSE，JSON。接口文档由 springdoc-openapi 生成（`/swagger-ui.html`）。

## 1. 约定

响应统一包一层：

```json
{ "code": 0, "message": "ok", "data": {} }
```

分页：请求 `?page=1&page_size=20`（上限 100），响应 data 为 `{ "items": [], "total": 137, "page": 1, "page_size": 20 }`。

时间用 ISO 8601 带时区；金额用字符串小数（元）；ID 用带前缀的 ULID（如 `doc_01H...`）。

认证：用户接口用 JWT Bearer；内部接口（§9）用 `X-Internal-Key`；文档、机构这类数据查询接口两种都接受，外部服务读数据用内部 key。角色：admin / analyst / viewer，viewer 只读。

错误码：

| code | HTTP | 含义 |
| --- | --- | --- |
| 0 | 200/201 | 成功 |
| 40001 | 400 | 参数错误 |
| 40101 | 401 | 未认证 |
| 40301 | 403 | 无权限 |
| 40401 | 404 | 不存在 |
| 40901 | 409 | 状态冲突（如信号已在调查中、调查已被占用） |
| 42201 | 422 | 业务规则拒绝（如超轮次、超预算、证据未登记） |
| 42901 | 429 | 限流 |
| 50001 | 500 | 服务器错误 |

## 2. 接口总览

| 模块 | 接口 |
| --- | --- |
| 认证 | `POST /auth/token`、`GET /users/me` |
| 信息源 | `GET/POST /sources`、`PATCH/DELETE /sources/{id}`、`POST /sources/{id}/run`（admin） |
| 文档 | `GET /documents`、`GET /documents/{id}`、`GET /documents/{id}/snapshot` |
| 信号 | `GET /signals`、`GET /signals/{id}`、`POST /signals/{id}/investigate`、`PATCH /signals/{id}/status` |
| 调查 | `GET /investigations`、`GET /investigations/{id}`、`GET /investigations/{id}/steps`、`GET /investigations/{id}/stream`（SSE）、`POST /investigations/{id}/stop` |
| 机构 | `GET /organizations/{id}`（基本信息 + 关联采购/中标记录） |
| 证据 | `GET /evidences/{id}`、`GET /evidences/{id}/snapshot` |
| 事件图谱 | `GET /events`、`GET /events/{id}`、`GET /events/{id}/graph` |
| 报告 | `GET /reports`、`GET /reports/{id}`、`POST /reports/{id}/feedback`、`GET /reports/{id}/export?format=md|pdf` |
| 管理 | `GET/POST/PATCH /admin/signal-rules`、`GET/PUT /admin/investigation-policies`、`GET/POST /admin/backtests`（admin） |
| 看板 | `GET /dashboard/summary`、`GET /stream`（全局通知 SSE） |
| 内部 | `/internal/*`，外部服务（爬虫/信号/Agent）专用，见 §9 |

下面只展开关键接口的请求/响应结构，其余看 OpenAPI。

## 3. 文档

`GET /documents` 筛选参数：`keyword`（全文）、`semantic`（向量检索）、`doc_type`（announcement/policy/news）、`org_id`、`region`、`category`、`amount_gte/amount_lte`、`date_from/date_to`、`sort`。

```json
{
  "id": "doc_01H...",
  "doc_type": "announcement",
  "title": "某市人民医院AI辅助诊断系统采购项目公开招标公告",
  "org": { "id": "org_01H...", "name": "某市人民医院" },
  "amount": "3250000.00",
  "publish_date": "2026-09-24",
  "deadline": "2026-10-15",
  "region": "华东/某省某市",
  "category": "医疗信息化",
  "url": "http://...",
  "signal_count": 1
}
```

详情另含 `content_text`、`raw`（原始字段）、`related_signals`。`/snapshot` 返回原始页面快照。

## 4. 信号

`GET /signals` 筛选：`status`（pending/investigating/confirmed/dismissed）、`rule_type`、`score_gte`、`org_id`、日期。

```json
{
  "id": "sig_01H...",
  "document": { "id": "doc_01H...", "title": "...", "amount": "3250000.00", "publish_date": "2026-09-24" },
  "org": { "id": "org_01H...", "name": "某市人民医院" },
  "hits": [
    { "rule_id": "rule_01H...", "rule_version": 7, "rule_type": "amount_anomaly", "weight": 0.6,
      "detail": { "amount": 3250000, "baseline_mean": 820000, "baseline_std": 410000, "z": 5.93, "samples": 23 } },
    { "rule_id": "rule_02H...", "rule_version": 3, "rule_type": "semantic_match", "weight": 0.4,
      "detail": { "similarity": 0.86, "profile": "医疗AI" } }
  ],
  "score": 0.91,
  "status": "pending",
  "investigation_id": null
}
```

`POST /signals/{id}/investigate`：为信号建调查。已在调查中返回 40901，已忽略返回 42201。

`PATCH /signals/{id}/status`：`{ "status": "confirmed" | "dismissed", "reason": "..." }`。dismiss 必填 reason，写进 feedback_events。

## 5. 调查

`GET /investigations` 筛选：`signal_id`、`status`（created/investigating/reporting/completed/failed/stopped）、日期。

详情含当前问题清单和判断：

```json
{ "questions": [
  { "id": "q_01H...", "text": "该医院两年前是否发布过信息化建设规划文件？", "status": "clarified",
    "answer_summary": "存在《医院信息化建设规划(2024-2027)》", "evidence_ids": ["ev_01H..."] },
  { "id": "q_02H...", "text": "同市其他医院是否有同类AI采购动向？", "status": "open" } ] }
```

`GET /investigations/{id}/steps?cursor=` 游标分页，step 类型：question / tool_select / tool_call / reflection / status_change。响应 `{items, next_cursor}`：cursor 为上次读到的 seq，返回严格更大的批次，读完 next_cursor 省略。

```json
{ "seq": 11, "round": 3, "type": "tool_call",
  "content": {
    "tool": "tender_search",
    "args": { "semantic": "AI辅助诊断 采购", "region": "某省某市", "date_from": "2025-09-01" },
    "target_question_id": "q_02H...",
    "result_summary": "命中 3 条：同市第二人民医院(9/12)、第三人民医院(9/18)…",
    "evidence_ids": ["ev_05H...", "ev_06H..."],
    "latency_ms": 1420, "ok": true } }
```

`POST /investigations/{id}/stop`（admin/analyst）：investigating / reporting → stopped，记 finished_at；信号回退 pending（可再人工决策；同一信号不可再开新调查，investigation_id 留作追溯）。

调查由外部 Agent 服务经内部 API 领取执行（§9.3）。服务没接入时调查停在 created（超时后端标 failed），SSE 没有事件，其余功能不受影响。

## 6. 报告

```json
{
  "id": "rep_01H...",
  "investigation_id": "inv_01H...",
  "event_id": "evt_01H...",
  "title": "某市人民医院大额AI辅助诊断招标：规划三期建设而非孤立采购",
  "summary": "该招标并非孤立事件：属于医院两年前规划的第三期建设；同市另有2家医院启动类似项目。",
  "claims": [
    { "id": "cl_01H...", "text": "本次采购属于《医院信息化建设规划(2024-2027)》第三期建设计划",
      "nature": "fact", "confidence": 0.95,
      "evidences": [ { "id": "ev_01H...", "title": "《医院信息化建设规划》", "source_type": "policy" } ] },
    { "id": "cl_03H...", "text": "同市另有2家医院启动类似项目，可能是一轮建设潮的开始",
      "nature": "speculation", "confidence": 0.6,
      "evidences": [ { "id": "ev_05H...", "title": "同市第二人民医院AI采购公告", "source_type": "announcement" } ] }
  ],
  "action_suggestions": [
    { "id": "as_01H...", "text": "评估参与方式：独立投标或联合既往合作方", "priority": "high" } ],
  "meta": { "rounds": 5, "tool_calls": 9, "token_used": 41200, "cost_estimate": "1.84" },
  "disclaimer": "本报告由AI生成，结论不替代人的自主判断"
}
```

`POST /reports/{id}/feedback`：`{ "adopted_suggestion_ids": [], "comment": "...", "flag": "false_positive"? }`。

落地进度（S7，2026-10-02）：反馈接口已交付 —— 报告须 published（否则 40901）；三要素至少其一（40001），flag 仅支持 false_positive（40001）；建议必须属于本报告（42201），重复采纳幂等（已回填的不再重复写事件）；comment/flag 落 feedback_events（user 取自 JWT，action 词表增补 comment）；响应 data 为 `{ report_id, newly_adopted, comment_recorded, flagged }`。`GET /dashboard/summary`、`GET /events`（分页/详情/图）、`GET /admin/signal-rules`、`GET /stream` 同冲刺交付，形状见对应节。

导出：md 直接返回。pdf 不做真排版（OQ5 已决，见 backlog）：接口保留 202 + task_id 占位契约，但不会生成文件，也没有查询任务的接口；需要 PDF 时在前端报告页点「打印 / 存为 PDF」，由浏览器生成（打印样式只保留报告内容）。

落地进度（S6）：`GET /reports`（investigation_id / date_from / date_to 筛选，分页）与详情已交付，详情即上方形状（另带 signal_id / status / created_at 增量字段）；`GET /evidences/{id}` 与 `/snapshot` 已交付；`GET /reports/{id}/export?format=md|pdf` 已交付（md 同步回 text/markdown，pdf 回 202 + task_id 占位；OQ5 已决不做真排版，见上）。反馈接口 `POST /reports/{id}/feedback` 属 E5（S7）。

## 7. SSE 事件协议

`GET /investigations/{id}/stream`：调查进度流。`id` 字段等于 step 的 seq，客户端重连带 `Last-Event-ID`，服务端从 steps 表补发（含 start / budget_update 等派生步骤，重连不丢状态事件）。25 秒一次心跳注释。JWT 可经 `?token=` 传（同全局流）。

| event | data 要点 |
| --- | --- |
| investigation_started | investigation_id、max_rounds、token_budget |
| round_started | round |
| questions_generated | questions 列表 |
| tool_selected | round、tool、args、target_question_id |
| tool_completed | round、tool、ok、latency_ms、result_summary、evidence_ids |
| reflection_updated | clarified、new_questions、judgment |
| budget_update | token_used、token_budget |
| report_ready | report_id |
| investigation_completed | status、rounds、token_used |
| investigation_failed | error、last_round |
| investigation_stopped | status、last_round（stop 接口派生，E3 增补） |

```text
event: tool_completed
id: 11
data: {"round": 3, "tool": "tender_search", "ok": true, "latency_ms": 1420, "evidence_ids": ["ev_05H..."]}
```

`GET /stream`：全局通知（signal_created / investigation_completed / report_ready / source_degraded），按用户过滤。JWT 可经 `?token=` 传（EventSource 不支持自定义 header）。

落地进度（S7，2026-10-02）：已交付。事件先落 global_events 表再广播，SSE 的 id 即该表主键，断线重连带 Last-Event-ID 从表补发（可追溯，不丢已发事件）；25s 心跳注释；无效 token 40101。signal_created 的 data 为 `{ signal_id, document_id, title, org_id, score }`；report_ready / investigation_completed 附 investigation_id。按用户过滤：当前系统无按用户的数据隔离，全员同流；引入私有画像后在此分流。

## 8. 管理接口

信号规则（版本化，PATCH 自动生成新版本并停用旧版）：

```json
{ "id": "rule_01H...", "name": "采购金额异常", "type": "amount_anomaly", "version": 7, "enabled": true,
  "params": { "k": 2.0, "min_samples": 5, "window_days": 730 },
  "backtest": { "precision": 0.72, "recall": 0.61, "evaluated_at": "2026-09-20" } }
```

调查策略（GET/PUT）：`{ "auto_investigate_threshold": 0.85, "max_concurrent_investigations": 3, "default_max_rounds": 8, "default_token_budget": 60000 }`

PUT 全量必填（缺字段 40001），即时生效于后续自动触发；max_concurrent_investigations 在调查 start 时生效（决策 D14）。

关注画像（D13，GET/POST/DELETE /admin/watch-profiles，2026-10-02 新增，纯增量）：

```json
// POST 请求
{ "name": "医疗AI", "note": "影像 AI、辅助诊断相关" }
// 响应 data
{ "id": "wpr_01H...", "name": "医疗AI", "enabled": true, "note": "...", "created_at": "..." }
```

重名 40901；删除后不再内嵌进 detection-queue 的 profiles；GET 列表按 name 升序。/admin/** 整组仅 admin（GET 也是，analyst 40301）。

信号规则落地进度（S7，2026-10-02）：`GET/POST/PATCH /admin/signal-rules` 已交付。GET 返回全部版本行（name 升序、version 降序）；POST 新建 version=1 enabled（type 词表 amount_anomaly / frequency_burst / semantic_match / composite，weight 0~1，重名启用中 40901）；PATCH 按 id 合并最新版本字段生成 version+1 新行并停用全部旧行（同 id 只一个 enabled，部分唯一索引兜底；PATCH 空体 40001，id 不存在 40401；enabled 缺省为新版生效，显式 false 则全停）。weight 按列精度三位小数出串（"0.800"）。种子预置 rule_seed01..03（金额异常 / 频率突增 / 语义匹配）version=1。

回测：`POST /admin/backtests` 建任务（规则 + 参数网格 + 日期区间），后端导出数据集，评估在外部执行后写回 result。

回测落地进度（S8，2026-10-02）：`GET/POST /admin/backtests`、`GET /{id}`、`GET /{id}/dataset`、`PUT /{id}/result` 已交付 —— 建任务 queued（规则 40401 / 日期 40001 闸门）；导数据集即转 running（三段：documents 按 publish_date、signals 按 created_at、feedback_events 取信号级标注，各段上限 2000）；写回 done/failed（done 必带 result，终态 40901）。内部接口限流（E5-5）：`/api/v1/internal/*` 按 X-Internal-Key 令牌桶（Bucket4j，阈值 INTERNAL_RATE_LIMIT_PER_MINUTE 默认 600/分钟，0 关闭），超限 42901 + Retry-After。

## 9. 内部接口（外部服务接入）

只在内网开放，`X-Internal-Key` 认证（服务端配置 `INTERNAL_API_KEY`，不配置则整组 403）。调用方向只有外部到后端。这节是三个外部服务（爬虫、信号发现、Agent 调查）与本后端的全部契约。

### 9.1 爬虫服务

`GET /internal/ingest-queue`：返回当前到期的信息源（按 sources 的 cron 和 last_run_at 判定，判定逻辑在后端），含该源全部配置。重复领取无害，入库去重兜底。

```json
{ "items": [ { "source_id": "src_01H...", "name": "中国政府采购网-医疗设备", "adapter": "ccgp",
    "url": "http://...", "config": { "list_selector": "...", "detail_selector": "..." },
    "last_run_at": "2026-09-25T06:00:00+08:00" } ] }
```

`POST /internal/documents`：批量推送解析好的文档。后端做 URL 归一、content_hash 去重、raw_html 存快照、机构名归一（org_name 进来，org_id 出去）、入库、重算 org_stats。

```json
{ "source_id": "src_01H...",
  "items": [ {
    "url": "http://.../notice/123456.htm",
    "doc_type": "announcement",
    "title": "某市人民医院AI辅助诊断系统采购项目公开招标公告",
    "org_name": "某市人民医院",
    "amount": "3250000.00",
    "publish_date": "2026-09-24",
    "deadline": "2026-10-15",
    "region": "华东/某省某市",
    "category": "医疗信息化",
    "content_text": "……",
    "raw_html": "<html>……</html>"
  } ] }
// 响应 data：{ "accepted": 24, "duplicates": 6, "failed": 0, "document_ids": ["doc_..."] }
```

`POST /internal/ingest-runs`：上报一次运行的结果，后端更新 source 的 last_run_status 和 health。

```json
{ "source_id": "src_01H...", "ok": true,
  "stats": { "fetched": 30, "accepted": 24, "duplicates": 6, "failed": 0 },
  "errors": [ { "url": "...", "stage": "fetch", "error": "timeout" } ] }
```

### 9.2 信号发现服务

`GET /internal/detection-queue?limit=50`，一次给齐检测要用的全部输入。2026-10-14 新增 `rules`：启用规则的 id/version/type/params/weight/enabled；也可调用 `GET /internal/signal-rules` 单独读取启用规则（data 为数组，X-Internal-Key 认证，无需管理员凭据）。document.amount 固定为小数字符串：

```json
{ "items": [ {
  "document": {
    "id": "doc_01H...", "doc_type": "announcement", "title": "...",
    "org_name": "某市人民医院", "amount": "3250000.00", "publish_date": "2026-09-24",
    "region": "...", "category": "医疗信息化", "content_text": "...",
    "org_id": "org_01H...", "url": "http://..." },
  "org_stats": { "category": "医疗信息化", "sample_count": 23, "amount_mean": 820000,
                 "amount_std": 410000, "amount_p95": 1900000, "freq_mean_30d": 0.4 },
  "profiles": ["医疗AI", "数据平台"] } ] }
```

`POST /internal/signals`，推送命中。幂等：同 document + detector + hits 重复推返回已有 signal_id。rule_id / rule_version 可空，没注册规则就只留 rule_type 和 detector 标识。

```json
// 请求
{ "document": { "id": "doc_01H..." },
  "hits": [ { "rule_type": "amount_anomaly", "rule_id": "rule_01H...", "rule_version": 7,
              "weight": 0.6, "detail": { "z": 5.93 } } ],
  "score": 0.91,
  "detector": "signal-service-v0.3" }
// 响应 data
{ "signal_id": "sig_01H...", "status": "pending", "auto_investigated": false }
```

`POST /internal/detection-scan-complete`：`{ "document_ids": [], "detector": "..." }`，标记已扫描无命中。

新闻信号（2026-10-18）：`doc_type=news` 的文档由检测服务做新闻事件检测，信号同样以文档为锚（事件首条报道），`detector` 为 `signal-service/news-v0.1`，命中 `rule_type=news_event`，`detail` 除 `score/reliable/explanation` 外带 `rumor`（Agent 装配 rumor 信封的完整载荷）与 `document_ids`（同事件全部报道）。hits 原样落库，后端不区分信号来源。

### 9.3 Agent 调查服务

| 接口 | 说明 |
| --- | --- |
| `GET /internal/investigations?status=created` | 领取任务 |
| `POST /internal/investigations/{id}/start` | CAS 占用，被占返回 40901 |
| `GET /internal/investigations/{id}/context` | 信号和文档上下文、问题清单、证据摘要、预算余量 |
| `POST /internal/investigations/{id}/steps` | `{event, round, payload, token_usage, request_id?}`，超轮次/预算返回 42201 |
| `GET /internal/investigations/{id}/steps?cursor=&limit=` | 与用户侧 steps 相同的游标格式，供 Agent 恢复已提交进度 |
| `POST /internal/questions` | `{investigation_id, text, raised_in_round}` |
| `PATCH /internal/questions/{id}` | `{status, answer_summary, evidence_ids}` |
| `POST /internal/investigations/{id}/evidences` | 登记自取的证据：引本库文档传 `doc_id`，外部内容传 url/title/excerpt，后端抓快照算 hash；可选 `snapshot` 传 Agent 采集时读到的原文，后端以它为快照（不回源） |
| `POST /internal/investigations/{id}/report` | 提交 ReportDraft，claim 引用未登记证据返回 42201 |
| `POST /internal/investigations/{id}/complete` | 报告提交后收尾 |
| `POST /internal/investigations/{id}/fail` | `{error}` |

后端没有工具的概念。Agent 服务查数据用 `/documents`、`/organizations`（X-Internal-Key），把这些接口和它自己接的外部源封装成它自己的工具；工具调用记录作为 steps（type=tool_call）上报，产出经 evidences 接口登记。

落地进度（S6）：§9.3 表内接口全部交付。evidences 登记两变体——引本库文档传 doc_id（回填 url/title/published_at），外部内容传 url/title/excerpt/published_at（后端 5s 超时抓快照、共 3 次尝试、SHA-256 入库，失败整条拒收 42201，D4）；report 提交时 claims 证据引用整份校验（42201），调查转 reporting 并留 report_ready 步骤；complete 要求已有报告（否则 42201）并置信号 confirmed（D6）；fail 任意非终态可调，信号回退 pending。一调查一报告（investigation_id 唯一），重复提交 40901。ReportDraft 的 token_usage 仅作对账参考：预算与 meta 一律以 steps 累计为准（D5）。steps 的事件名即 §7 词表——后端派生事件（investigation_started / budget_update / investigation_stopped / report_ready / investigation_completed / investigation_failed）由对应接口产生，Agent 直报 40001。

ReportDraft：

```json
{
  "title": "...", "summary": "...", "event_id": null,
  "claims": [ { "text": "...", "nature": "fact", "confidence": 0.95, "evidence_ids": ["ev_01H..."] } ],
  "action_suggestions": [ { "text": "...", "priority": "high" } ],
  "event_extraction": {
    "event": { "title": "...", "type": "construction_wave", "start_date": "2024-03-15" },
    "entities": [ { "name": "某市人民医院", "type": "org", "role": "participant" } ],
    "relations": [ { "source": "entity:0", "target": "event", "relation": "参与" } ] },
  "token_usage": 41200
}
```

### 9.4 约定

2026-10-14 接入修复约定：

- `steps.request_id` 为可选 1..100 字符稳定重试标识，同调查、同内容重试返回原 seq，不重复写步骤或累加预算；同 key 不同内容返回 40901。steps 查询新增可选 request_id 字段。
- context 新增全量 `questions`，保留既有 `open_questions`。单 Agent 服务重启后先查询 investigating/reporting、context 与内部 steps；已提交报告只执行收尾，已终态任务不再写入。不支持多个 worker 同时接管一个调查，租约机制属后续范围。
- 所有调查写入在同一调查行锁下校验状态和预算；问题只在 investigating 可修改，终态新写入返回 40901。
- 每条 report claim 的 evidence_ids 必须非空且引用本调查已登记证据；缺失/空列表返回 42201。本库证据的文档必须有 publish_date 和可读取快照，否则登记返回 42201。
- 全局事件日志与业务同事务提交，提交后广播；两个 SSE 流按持久游标有序补发。

- 写接口都做不变量校验（状态机、轮次、预算、证据引用），非法写入 40901 / 42201。
- SSE 由后端在写库时自动发布，外部服务不推也不能推。
- 限流按 key 统计。

## 10. Webhook（预留）

企业对接用，原型期不做。

## 11. 变更记录

§9 是三个外部团队的接入契约：路径、字段、状态值的任何改动都是破坏性变更，合码前在此登记并通知外部团队；仅新增可选字段除外。

| 日期 | 变更 | 说明 |
| --- | --- | --- |
| 2026-09-25 | 初稿 | 全部接口首次定义 |
| 2026-10-02 | 增补 | §7 新增可选事件 investigation_stopped（stop 接口派生）；§5 明确 steps 响应 {items, next_cursor} 形状与 stop 的信号回退语义。均为新增/澄清，非破坏性 |
| 2026-10-02 | 增补 | S6 交付：§9.3 evidences / report / complete / fail 落地（含义见表，无形状变更）；§6 报告查询 / 证据查询 / 导出三组只读接口与详情增量字段（signal_id/status/created_at）落地。均为新增，非破坏性 |
| 2026-10-02 | 增补 | S7 交付：§6 反馈接口落地（feedback_events 的 action 词表增补 comment，纯增量）；§7 全局流 GET /stream、§8 信号规则 GET/POST/PATCH、看板 GET /dashboard/summary、事件图谱 GET /events* 落地。均为新增，非破坏性 |
| 2026-10-02 | 增补 | S8 交付：§8 回测细化为 GET/POST /admin/backtests + /{id}/dataset + /{id}/result（新增，非破坏性）；内部接口超限 42901（限流为既有错误码的新触发点，对外部服务是新增保护非契约变更） |
| 2026-10-14 | 接入修复，消费者需同步 | detection-queue.document.amount 从实现中的 JSON number 修正为契约定义的小数字符串；报告每条 claim 强制非空 evidence_ids；本库证据强制发布时间/可读取快照；终态问题写入返回 40901。按实际消费行为视为兼容性变更，合码/部署前接入方需同步 |
| 2026-10-14 | 增量 | detection-queue 新增 rules；新增 GET /internal/signal-rules 与内部 steps 查询；steps 新增可选 request_id；context 新增 questions；OpenAPI 补认证声明与检测文档 DTO。既有路径/状态值保留 |
| 2026-10-17 | 增量 | 全链路接入：detection-queue.document 新增 org_id、url（检测服务取同单位历史、回溯原文）；evidences 新增可选 snapshot（Agent 所见原文即快照，反爬/限时页不再因回源失败被拒）。均为新增可选字段，非破坏性 |
| 2026-10-18 | 增量 | 新闻信号接入：§9.2 新增命中类型 `news_event`（detail.rumor 为 RumorSignal 载荷），沿用既有推送接口与幂等语义；信号幂等唯一键改为 (document_id, detector, md5(hits))（V9），修复命中超过约 2.7KB 时写入 500。无路径/字段/状态值变更，非破坏性 |
