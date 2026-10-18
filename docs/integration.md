# 组件接入映射

后端契约（路径、状态、错误码）以 [Backend/docs/api-design.md](../Backend/docs/api-design.md) 为准；本文只记录**跨组件的字段约定**：谁写、谁读、怎么换算。

## 0. 采集 → 文档（collector）

`collector` 领取 `GET /internal/ingest-queue` 的到期源，按 `adapter` 抓取后推 `POST /internal/documents`。适配器条目 → 文档：

| 适配器字段 | 文档字段 | 说明 |
| --- | --- | --- |
| `url` / `title` | `url` / `title` | 后端按 URL 归一与内容哈希去重 |
| `published_at`（UTC） | `publish_date` | 换算为北京日历日 |
| `buyer`（列表自带）或详情页"采购人" | `org_name` | 后端归一成 `org_id` |
| 详情页预算 | `amount` | 万元/亿元换算为元，两位小数 |
| 详情页投标截止 | `deadline` | 正文条款优先，页首摘要表兜底 |
| `province` 或源配置 `region` | `region` | |
| 源配置 `category` | `category` | 后端按"机构 + 品目"维护金额基线 |
| 详情正文（取不到则标题 + 结构化后缀） | `content_text` | |
| 详情页原文 | `raw_html` | 即文档快照；新闻检测从这里读原文发布时间 |
| `platform`、`source`、`stage`、`author_type` 等 | 其余键进 `raw` | 新闻检测用 `raw.platform` / `raw.source_name` / `raw.author_type` |

## 1. 文档 → 检测（signal-service/bridge）

`GET /internal/detection-queue` 的每条 `document` 转成检测输入 `Announcement`：

| 后端字段 | 检测字段 | 说明 |
| --- | --- | --- |
| `id` | `id` / `project_id` | 信号回推时作为 `document.id` |
| `org_name` | `buyer` | 同单位历史按 `org_id` 取 |
| `org_id` | — | `GET /documents?org_id=&date_to=检测日-1`，近 730 天，严格早于检测日 |
| `amount`（小数字符串） | `amount`（元） | |
| `publish_date` | `publish_date` / 检测日 | 按发布日分批，每批独立 Top-K |
| `content_text` 前 800 字 | `project_summary` | 历史文档只有标题 |
| `profiles`（关注画像名） | `company_profile` | 无画像时用 `SIGNAL_COMPANY_PROFILE` / `config.yaml bridge.company_profile` |

只有 `doc_type=announcement` 参与招投标检测，`doc_type=news` 走新闻检测（见第 7 节），政策文档不判定；本批全部文档最后统一 `detection-scan-complete`。

## 2. 检测 → 信号命中（hits）

入选当日 Top-K（`is_signal`）的记录回推 `POST /internal/signals`，`score = priority_score`。只上报依据可靠的指标：

| 检测指标 | `rule_type` | `weight` | `detail` 关键字段 |
| --- | --- | --- | --- |
| 金额 | `amount_anomaly` | 0.5 | `ratio`、`historical_median`、`samples` |
| 新颖性 | `novelty` | 0.5 | `max_similarity`、`most_similar_title/url`、`samples` |
| 频率 | `frequency_burst` | 0（仅参考） | `recent_count`、`historical_average`、`ratio`、`window_days` |
| 相关性 | `semantic_match` | 0（门槛） | `threshold`、`profile` |

**每个 `detail` 都带 `score`、`reliable`、`explanation`**——这是与 Agent 的约定：Agent 把它们还原为 `TenderSignal` 的检测器，检测解释成为调查的第一批证据。`rule_id/rule_version` 取自队列下发的启用规则（`novelty` 暂无对应规则类型，留空）。

## 3. 调查 context → 调查信封（agent/sense2act/bridge/envelope.py）

| context | TenderSignal |
| --- | --- |
| `signal.document.id / title / org_name / amount / publish_date / category / url / content_text` | `project_id / project_name / purchaser / amount_yuan / published_date / domain / source_url / project_summary` |
| `hits[amount_anomaly]` | `amount_anomaly` + `amount_history` |
| `hits[frequency_burst]` | `frequency_anomaly` + `frequency_history` |
| `hits[novelty]` | `novelty_anomaly` + `novelty_history` |
| `hits[semantic_match]` | `relevance`，`detail.profile` → `company_profile` |
| `investigation.max_rounds` | `--max-rounds` |

## 4. 台账留痕 → 后端时间线（agent/sense2act/bridge/reporter.py）

挂在台账唯一写入口 `ledger.log` 之后，实时翻译；每个 step 的 `request_id = {event_id}:dl{决策行号}:{类型}`，重试不重复记账。

| 台账事件 | 后端写入 |
| --- | --- |
| 新 claim（S1b 拆解 / S5 派生） | `POST /internal/questions` + `questions_generated` |
| 每轮第一条决策 | `round_started`（台账第 0 轮 S1 并入后端第 1 轮） |
| 决策单（S2） | `tool_selected`：`tool`、`args.query`（检索词）、`target_question_id` |
| 入账 `new_evidence`（S4/S5） | 逐条登记证据 + `tool_completed`：`evidence_ids`、`result_summary` |
| 判决（S6）到终态 | `PATCH question → clarified`（附支持证据）+ `reflection_updated` |
| 交付前仍待证实 | `PATCH question → unresolved`（Open Questions） |

token：`DeepSeekLLM` 记录每次调用的 `total_tokens`，随下一条 step 上报；后端是预算权威。

| 后端拒绝 | Agent 行为 |
| --- | --- |
| `42201`（超轮次/预算） | 转为 Agent 自己的预算护栏，优雅停止，按现有台账出部分报告 |
| `40901`（用户停止/终态） | 立即中止，不再写入 |
| Agent 重启 | `reporting` 只补 `complete`；`investigating` 如实标 `failed` |

## 5. 证据登记

| 台账证据 | 登记方式 | `source_type` |
| --- | --- | --- |
| `platform=signal_layer`（信封基础字段 / 检测解释） | `doc_id` = 信号文档（后端回填 URL、发布时间、快照） | `internal_stat` |
| 有 URL 与发布时间的外部证据 | `url/title/excerpt/published_at` + `snapshot` = Agent 读到的原文 | ccgp/天津政采/乙方宝/ggzy → `announcement`；头条/央视/南周/搜狗微信 → `news`；官网 → `web` |
| 缺 URL 或发布时间 | 不登记（时间线如实标注"未入证据库"），不进报告引用 | — |

无时区的发布时刻按 +08:00 补齐。

## 6. 台账终态 → 报告（agent/sense2act/bridge/report.py）

| claim 状态 | `nature` | `confidence` | `evidence_ids` |
| --- | --- | --- | --- |
| 已证实 | `fact` | 0.9 | 裁定"支持"的证据 |
| 矛盾 | `fact`（文本前缀"查证不成立"） | 0.85 | 裁定"矛盾"的证据 |
| 待证实，有支持证据 | `inference` | 0.6 | 支持证据 |
| 待证实，无支持证据 | `speculation` | 0.3 | 兜底引用信封基础字段证据 |

- 标题：`项目名：主导假设`；摘要：S7「结论」节 + 状态分布 + 停止原因。
- 行动建议：S7 recommendations，首条 `high`，其余 `medium`。
- 事件图谱：事件=项目（`procurement`）；主体=采购单位（org/participant）、项目（project/object）。

## 7. 新闻文档 → 新闻信号 → rumor 信封（signal-service/bridge/news.py → agent）

新闻文档 → 新闻检测输入（`signals/news` 的 ContentItem）：

| 后端 | 检测输入 | 说明 |
| --- | --- | --- |
| `id` | `contentId` / `dedupKey` | 线索 id 即平台文档 id |
| `title` / `content_text` | `title` / `summary`（前 300 字）/ `content` | |
| `raw.platform` | `platform` | 来源可信度按平台取 |
| `raw.source_name`（无则 `raw.site`、URL 域名） | `ext.sourceName` | 独立来源计数，决定多来源共振加分 |
| `GET /documents/{id}/snapshot` | `ext.publication` | 原文发布时间只认快照里的发布字段；读不到的只观察、不立项 |

监测主体、业务词、事件规则族在 `signals/news/platform.json`（`SIGNAL_NEWS_CONFIG` 可换，`off` 关闭）。同一事件的多篇报道归成一个告警，达到调查资格（`agentTriggerScore`）的推一条信号：

| 信号字段 | 值 |
| --- | --- |
| `document.id` | 事件首条报道的文档 |
| `detector` | `signal-service/news-v0.1` |
| `score` | 引擎分 ÷ 红色阈值（3.0），封顶 1；调查门槛 1.5 约对应 0.5 |
| `hits[0].rule_type` | `news_event`（`rule_id` 留空） |
| `hits[0].detail` | `score`、`reliable`、`explanation`（与招投标命中同约定），外加 `subject_name`、`event_types`、`signal_type`、`priority`、`raw_score`、`document_ids`（同事件全部报道） |
| `hits[0].detail.rumor` | RumorSignal 载荷：`subject_name`、`summary`、`source`、`signal_time`、`watch_focus`、`clues[]`（每条线索带原文、URL、原文发布时间、来源） |

Agent 侧（`sense2act/bridge/envelope.py`）：带 `news_event` 命中的信号装配成 `kind=rumor` 信封，`detail.rumor` 原样作为 RumorSignal；replay 模式回放 `examples/ai_model_release`。报告（`report.py`）标题为"主体·首条报道标题:主导假设"，事件图谱的事件类型为 `news`，主体为监测对象（org / participant）。

