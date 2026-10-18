# 信号输出字段与 Agent 对接格式

> 2026-10-17 更新:平台链路已改为经后端中转——`python -m bridge` 把检测结果作为信号命中推给后端,Agent 从后端调查 context 装配信封,字段映射见 `../../docs/integration.md`。本文的四字段提交格式保留给离线批量检测与新闻 `TaskClient` 提交;第 5 节中的本地任务库/worker 已移除。

本文采用两种类型：`tender` 与 `rumor`。
`tender` 表示招投标信号。
`rumor` 表示新闻与线索信号。

## 1. 对接流程

```text
检测模块判断需要调查
  → 提交调查所需字段
  → 后端保存、去重、创建任务
  → 适配器转换输入
  → Agent 调查
```

两个检测模块分别判断是否提交。
两个模块共用任务入口。

## 2. 统一提交外层

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `signal_id` | string | 是 | 本次信号结果唯一 ID |
| `company_id` | string | 是 | 监测企业 ID，提交时统一转成字符串 |
| `kind` | string | 是 | `tender` 或 `rumor` |
| `payload` | object | 是 | 对应类型的调查载荷 |

同一结果重试时，`signal_id` 不变。
重要更新生成新的 `signal_id`。
任务去重使用 `company_id + signal_id`。
项目 ID 与信号 ID 分别保留。
数据质量说明在内部保存；影响调查的限制随载荷传递。

## 3. 招投标模块提交格式

提交载荷使用单层结构，减少包装。

Agent 字段改名由适配器完成。

下例金额与分数仅展示格式。
运行时填写实际数据。

```json
{
  "signal_id": "本次信号结果唯一ID",
  "company_id": "监测企业ID",
  "kind": "tender",
  "payload": {
    "project_id": "原公告ID",
    "procurement_project_id": "归并后的采购项目ID",
    "title": "实际项目标题",
    "buyer": "实际采购单位",
    "publish_date": "2026-10-03",
    "amount_yuan": 6270000,
    "project_summary": "实际项目摘要；正文缺失时明确说明",
    "source_url": "实际原公告链接",
    "signal_reason": "实际调查原因；包含各指标依据、历史截止日及缺失说明",
    "relevance_score": 0.65,
    "amount_score": 0.8,
    "novelty_score": 0.3,
    "frequency_score": 0.6,
    "amount_reliable": true,
    "novelty_reliable": true,
    "frequency_reliable": true
  }
}
```

### 3.1 保留字段

路径前缀：`payload`。

| 字段 | 类型 | 用途 |
|---|---|---|
| `project_id` | string | 原公告 ID，保留 Demo 原有含义 |
| `procurement_project_id` | string | 归并后的项目 ID，用于调查关联 |
| `title` | string | 项目标题 |
| `buyer` | string | 标准化采购单位 |
| `publish_date` | string | 公告日期，`YYYY-MM-DD` |
| `amount_yuan` | number/null | 实际金额，未知填 `null` |
| `project_summary` | string | 项目摘要及正文质量说明 |
| `source_url` | string | 原公告链接 |
| `signal_reason` | string | 调查原因、各指标依据及缺失说明 |
| `relevance_score` | number/null | 企业相关性 |
| `amount_score` | number/null | 金额变化得分 |
| `novelty_score` | number/null | 采购新颖性得分 |
| `frequency_score` | number/null | 频率变化得分，不参与 Priority 时仍可参考 |
| `amount_reliable` | boolean | 金额依据是否可用 |
| `novelty_reliable` | boolean | 新颖性依据是否可用 |
| `frequency_reliable` | boolean | 频率依据是否可用 |
| `category` | string | 可选，项目领域 |

分数未知时填 `null`，不能填为零。
不可用指标的可靠性设为 `false`。
相关性评分不使用历史可靠性标记。
正文不足与指标不可用原因写入摘要或 `signal_reason`。

### 3.2 依据如何保留

`signal_reason` 保留实际检测解释。
金额解释包含历史截止日、中位数、金额倍数与样本数。
新颖性解释包含历史截止日、样本数、最大相似度与对照项目。
有助于查证的对照项目链接保留在解释中。
频率解释包含统计窗口、近期项目数、历史均值与比值。
解释不补造事实。
正式调查只使用真实历史。

### 3.3 仅内部保存的字段

- 独立历史明细对象：关键依据已放入 `signal_reason`。
- Priority、排名、Top-K、权重、阈值与模型配置。
- 文件名、工作表、行号、首次公告与归并方法。
- 全局历史总量与模拟计数。

原有 `project`、`scores`、`historical_context` 等导出结构继续保留。
单条提交时，提取同名字段放入 `payload`。
内部记录不因接口精简而删除。

## 4. 新闻模块提交格式

新闻载荷对齐现有 `RumorSignal`。
正文不足时明确标注摘要。
提交前应已有需要调查的判断。

```json
{
  "signal_id": "本次信号结果唯一ID",
  "company_id": "监测企业ID",
  "kind": "rumor",
  "payload": {
    "subject_name": "主要监测主体",
    "subject_credit_code": "",
    "summary": "新闻事件或待核实线索描述",
    "source": "主要来源名称或域名",
    "signal_time": "2026-10-03T10:00:00+08:00",
    "watch_focus": "调查原因与具体待查问题",
    "clues": [
      {
        "clue_id": "文章或线索唯一ID",
        "source": "原始发布来源",
        "platform": "toutiao",
        "title": "新闻标题",
        "content": "正文或明确标注的摘要",
        "url": "原文链接",
        "published_at": "2026-10-03T09:00:00+08:00",
        "author_type": "媒体"
      }
    ]
  }
}
```

### 4.1 新闻主体字段

路径前缀：`payload`。

| 字段 | 类型 | Agent 模型必填 | 项目要求 |
|---|---|---|---|
| `subject_name` | string | 是 | 明确主要调查主体 |
| `subject_credit_code` | string | 否 | 企业统一社会信用代码，未知填空字符串 |
| `summary` | string | 是 | 描述事件或待核实说法 |
| `source` | string | 是 | 主要来源 |
| `signal_time` | string | 是 | 约定为本模块识别信号的时间，带时区 |
| `watch_focus` | string | 否 | 本项目要求填写调查原因与问题 |
| `clues` | array[object] | 否 | 自动提交要求至少一条可追溯线索 |

原文发布时间使用 `clues[].published_at`。
代码目前只将这些时间字段定义为字符串。
提交前校验带时区时间与 `YYYY-MM-DD` 日期格式。

### 4.2 线索字段

路径前缀：`payload.clues[]`。

| 字段 | 类型 | Agent 模型必填 | 项目要求 |
|---|---|---|---|
| `clue_id` | string | 是 | 线索唯一 ID |
| `source` | string | 是 | 原始发布来源 |
| `platform` | string | 否 | 采集平台，模型默认 `other` |
| `title` | string | 否 | 原文标题 |
| `content` | string | 是 | 正文或标明为摘要的文本 |
| `url` | string | 否 | 本项目要求可追溯原文链接 |
| `published_at` | string | 否 | 原文时间，未知填空字符串 |
| `author_type` | string | 否 | 官方、媒体、自媒体等；未知传“未知” |

默认值不表示真实作者身份。
来源身份应根据实际信息填写。
同一通稿转载不算独立证据。

新闻检测分数与详细质量字段另行保存。
现有 Agent 不接收额外字段。
需要传入这些字段时，由 Agent 组扩展模型。

### 4.3 新闻有效期与公共任务层

新闻服务根据原文时间和配置的 `lookbackHours` 提供独立 `expires_at` 调度参数，不放入四字段提交 JSON 或 Agent 载荷，不用 `signal_time` 代替。

- local：`SignalTrigger.submit(signal, expires_at=...)` 保存有效期，worker 领取前检查；过期标为 `expired`，不执行调查、不计每日额度。
- HTTP：通过 `X-Signal-Expires-At` 请求头传递，值为带时区时间。本服务的 `/api/v1/investigations/submit` 已支持该头；外部后端需确认接收并在派发前检查，再启用 `TASK_SUBMIT_EXPIRY_SUPPORTED=1`。未确认时不自动提交新闻。
- 只有日期的报道按 `calendarTimezone` 的日界计算窗口，原文日期仍只保留 `YYYY-MM-DD`；这是调度规则，不是估算报道时刻。

同一 ID 重试不改变原始载荷和有效期。新闻检测、公共任务登记、worker 调查分别执行，不同时调用旧 runner。

## 5. 代码放置位置

正式代码放在 `E:\projects\智能监测\ai-service\`。
采用以下轻量结构；接口、业务编排、检测规则和外部调用分别维护。

```text
ai-service/
├── application.py             应用启动与服务装配
├── core/
│   ├── config.py              配置加载
│   └── semantic_encoder.py    语义模型与向量缓存
├── api/
│   ├── tender_controller.py   招投标 HTTP 请求与异常处理
│   ├── news_controller.py     新闻检测与提交
│   └── investigation_controller.py  本地调查提交与状态查询
├── signals/
│   ├── tender/
│   │   ├── schemas.py         输入、输出模型
│   │   ├── loader.py          文件读取
│   │   ├── normalization.py   数据清洗
│   │   ├── project_identity.py
│   │   ├── signal_detector.py
│   │   ├── signal_explainer.py
│   │   ├── service.py         检测与提交编排
│   │   └── cli.py             文件批处理入口
│   ├── news/
│   │   ├── signal_detector.py 新闻筛选与检测入口
│   │   ├── event_identity.py  同主体、规则与动作归组
│   │   ├── signal_explainer.py 规则依据与内部信号
│   │   ├── service.py         检测与公共任务提交
│   │   ├── submission.py      news_signal.v1 转统一 rumor 载荷
│   │   ├── schema.py          内部新闻模型
│   │   ├── config.json        企业、业务词与时间窗
│   │   ├── cli.py             采集 JSON 检测与提交重试
│   │   └── core/              复用的匹配、规则与状态引擎
│   └── common/
│       ├── schemas.py         共用提交格式与载荷校验
│       ├── task_client.py     后端任务客户端
│       └── trigger.py         任务存储、领取、触发与提交模式选择
├── integration/
│   └── sense2act_runner.py    Sense2act 输入、预算与结果适配
├── workers/
│   └── investigation.py      本地后台调查执行与任务恢复
├── tests/
└── config.yaml
```

新闻检测代码已迁入 `signals/news/`，仅通过公共入口提交调查任务。
Agent 适配和本地后台调查执行代码已分别放在 `integration/` 和 `workers/`。
local 模式由本地任务库和 worker 管理任务、去重、排队与状态；backend 模式计划由 Java `back-end/` 管理这些职责。
公告与新闻采集由 `crawler/` 维护。
展示由 `front-end/` 维护。
