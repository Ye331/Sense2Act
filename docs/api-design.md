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

导出：md 直接返回；pdf 异步生成（202 + task_id）。

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

回测：`POST /admin/backtests` 建任务（规则 + 参数网格 + 日期区间），后端导出数据集，评估在外部执行后写回 result。

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

`GET /internal/detection-queue?limit=50`，一次给齐检测要用的全部输入：

```json
{ "items": [ {
  "document": {
    "id": "doc_01H...", "doc_type": "announcement", "title": "...",
    "org_name": "某市人民医院", "amount": "3250000.00", "publish_date": "2026-09-24",
    "region": "...", "category": "医疗信息化", "content_text": "..." },
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

### 9.3 Agent 调查服务

| 接口 | 说明 |
| --- | --- |
| `GET /internal/investigations?status=created` | 领取任务 |
| `POST /internal/investigations/{id}/start` | CAS 占用，被占返回 40901 |
| `GET /internal/investigations/{id}/context` | 信号和文档上下文、问题清单、证据摘要、预算余量 |
| `POST /internal/investigations/{id}/steps` | `{event, round, payload, token_usage}`，超轮次返回 42201 |
| `POST /internal/questions` | `{investigation_id, text, raised_in_round}` |
| `PATCH /internal/questions/{id}` | `{status, answer_summary, evidence_ids}` |
| `POST /internal/investigations/{id}/evidences` | 登记自取的证据：引本库文档传 `doc_id`，外部内容传 url/title/excerpt，后端抓快照算 hash |
| `POST /internal/investigations/{id}/report` | 提交 ReportDraft，claim 引用未登记证据返回 42201 |
| `POST /internal/investigations/{id}/complete` | 报告提交后收尾 |
| `POST /internal/investigations/{id}/fail` | `{error}` |

后端没有工具的概念。Agent 服务查数据用 `/documents`、`/organizations`（X-Internal-Key），把这些接口和它自己接的外部源封装成它自己的工具；工具调用记录作为 steps（type=tool_call）上报，产出经 evidences 接口登记。

落地进度（S5）：领取 / start / context / steps / questions 已交付；evidences 登记与 report / complete / fail 属 E4（S6）。steps 的事件名即 §7 词表——后端派生事件（investigation_started / budget_update / investigation_stopped）由对应接口产生，Agent 直报 40001。

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
