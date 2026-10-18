# signal-service

Sense2Act 的信号服务：招投标与新闻信号检测，并通过 `bridge/` 接入后端。整体架构见 [../README.md](../README.md)，字段映射见 [../docs/integration.md](../docs/integration.md)。

## 组成

| 入口 | 作用 |
| --- | --- |
| `python -m bridge` | **平台检测 worker**：轮询后端 `detection-queue`。招投标公告按单位取历史跑检测，入选 Top-K 的回推 `POST /internal/signals`；新闻文档（`doc_type=news`）走新闻事件检测，达到调查资格的推 `news_event` 信号。达阈值后端自动立项调查，本批标记扫描完成 |
| `uvicorn application:app --port 8001` | HTTP 服务：`POST /embed`（后端语义检索的向量端点）、`/api/v1/signals/tender/detect`、`/api/v1/signals/news/detect`（离线批量分析） |
| `python -m signals.tender.cli` / `python -m signals.news.cli` | 文件批处理 |

招投标检测：公告校验、清洗、采购项目归并、相关性门槛（默认 0.60）、金额与新颖性（权重各 0.5，最少 3 条同单位历史）、频率参考、每日 Top-K（默认 10）。依据不足的指标不补零、不伪造命中。

调查不在本服务：信号进入后端后由后端立项，`agent` 的 worker 领取执行。

## 安装与启动

Python 3.11：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

语义模型 `BAAI/bge-small-zh-v1.5`（512 维）默认读 `.models/bge-small-zh-v1.5`，不自动下载；`SIGNAL_MODEL_PATH` 可指定目录。下载：

```powershell
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5', revision='7999e1d3359715c523056ef9478215996d62a620', local_dir='.models/bge-small-zh-v1.5')"
```

Docker 镜像构建时已固化模型（见 `Dockerfile`）。

```powershell
$env:INTERNAL_API_KEY = 'local-dev-internal-key'   # 与后端一致
$env:BACKEND_URL = 'http://127.0.0.1:8080'
.\.venv\Scripts\python.exe -m bridge                                        # 检测 worker
.\.venv\Scripts\python.exe -m uvicorn application:app --host 127.0.0.1 --port 8001   # /embed 等
```

关注画像取自后端 `detection-queue.profiles`（管理端「关注画像」）；没有画像时用 `SIGNAL_COMPANY_PROFILE` 或 `config.yaml` 的 `bridge.company_profile`。语义模型不可用时 worker 不标记扫描，文档留在队列里等恢复。

## 离线批量检测

`POST /api/v1/signals/tender/detect` 与 `python -m signals.tender.cli detect --request request.json` 对一批公告做检测，响应含 `records`、`signals`、`normalization`。请求包含 `company_id`、`company_profile`、`detection_date`、`history_start_date`、`history`、`current`；历史严格早于检测日，金额单位为元，未知值用 `null`。文件支持 JSON、CSV、XLS、XLSX。字段说明见 [docs/信号输出字段与Agent对接格式.md](docs/信号输出字段与Agent对接格式.md)。

## 新闻监测

代码位于 `signals/news/`：`publication.py` 读取原文，`relevance.py` 判断上下文关联，`signal_detector.py` 筛选，`event_identity.py` 归组，`service.py` 提交，`submission.py` 转换字段；`core/` 为复用的内部引擎。

默认配置 `signals/news/config.json` 为手机行业。换主题修改企业、业务词和规则，并使用新的 `--data-dir`。`monitor.settings.agentAutoTrigger=false` 固定关闭旧 runner；分数和时间窗由新闻模块判断，不改招标 Top-K。

采集端提供 ContentItem / Evidence 数组，包含标识、标题、来源、正文或摘要、原文 URL。批量入口复用现有 `crawler_tool` 搜索与 RSS 解析；公开订阅只用于找链接，时间仍从原文读取。原文依据位于 `ext.publication` / `publication`，保留原值、精度、定位和响应记录；无法读取时留在观察区。主体与业务可在标题、正文首段或相邻段落关联，跨远距离的无关提及不算关联。

API 输入为 `company_id` 与 `items`。`company_id` 必须使用项目企业 ID，不推断与配置的 `enterpriseId` 相同。识别时间进入 `signal_time`，原文时间进入 `clues[].published_at`；原文只有日期时保留日期，不补时刻。摘要明确标注，未知作者身份使用“未知”。

文件检测与重试：

```text
python -m signals.news.cli detect --input crawler-news.json --company-id <项目企业ID>
python -m signals.news.cli retry --company-id <项目企业ID>
```

真实批量采集先启动已有爬虫 API，再执行：

```text
python -m signals.news.cli crawl --crawler-root ../crawler --data-dir runs/news-new --completed-signals previous-signals.json
```

`--data-dir` 使用全新目录；`search.queries/platforms/feeds` 配置来源。默认只发现信号，正式提交增加 `--submit --company-id <项目企业ID>`。文件输入增加 `--read-originals` 可补读原文。`--completed-signals` 只读导入同一监测企业已完成的调查，按信号 ID 和原文 URL 拦截重复提交；重试命令也可使用此参数。

按企业隔离保存于 `outputs/news/company_<企业ID哈希>/`；批量只检测则保存于指定目录。`ticks/<轮次>/` 内：`input.json` 原始输入，`enriched.json` 原文补读结果，`screening.json` 筛选原因，`signals.json` 内部信号，`summary.json` 数量和历史去重统计；搜索、订阅与原文响应各存 JSONL。正式提交另存 `submission.json` 回执。每轮新建文件，不覆盖旧输出。

只检测使用 `NewsSignalDiscovery.run(items)`；`NewsService.detect(items, company_id)` 检测后经 `TaskClient` 提交合格信号（`TASK_SUBMIT_URL` 未配置时为 `not_submitted`，信号保持待提交）。成功提交不重复重交，失败重试间隔至少 60 秒，ID、载荷和有效期保持不变。

**平台接入**：新闻以 `doc_type=news` 文档进入平台（采集服务的新闻源），由 `python -m bridge` 的新闻分支（`bridge/news.py`）检测：原文发布时间从文档快照读取，同一事件的多篇报道归组，多来源共振评分；达到调查资格的告警推成一条信号，锚在首条报道上，命中 `rule_type=news_event`，`detail.rumor` 即 Agent 的 RumorSignal 载荷。平台监测配置是 `signals/news/platform.json`（医疗 AI 竞品与行业主体，回看 180 天），`SIGNAL_NEWS_CONFIG` 可换成别的配置、设 `off` 关闭；检测状态在 `SIGNAL_NEWS_DATA`（默认 `outputs/news-bridge`，容器里挂了卷）。字段映射见 [../docs/integration.md](../docs/integration.md) 第 7 节。

`TASK_SUBMIT_URL` 是不经平台、独立部署时的提交通道，平台内不使用。有效期经 `X-Signal-Expires-At` 传递，确认接收方支持后再设 `TASK_SUBMIT_EXPIRY_SUPPORTED=1`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install "pytest>=8,<9"
$env:PYTHONUTF8 = '1'      # Windows 默认 GBK 读不了测试夹具
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖招标与新闻检测、原文时间、上下文关联、历史调查去重，以及 `tests/test_bridge.py`（假后端校验 检测队列 → 信号 → 扫描完成 与 `/embed` 线约）、`tests/test_bridge_news.py`（新闻文档 → 快照读原文时间 → 事件归组 → `news_event` 信号，只推一次）。单元测试用可控向量，不加载语义模型。

已知待修正项：招标去重未充分区分项目编号冲突。
