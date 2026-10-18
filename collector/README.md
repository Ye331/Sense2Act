# collector

Sense2Act 的常驻采集服务：按前端「信息源管理」里配置的运行计划，抓取招投标公告和新闻，推送给后端入库。入库之后的检测、立项、调查都由其他服务接手。整体架构见 [../README.md](../README.md)，接口契约见 [Backend/docs/api-design.md](../Backend/docs/api-design.md) §9.1。

## 工作方式

每轮（默认 60 秒）：

1. `GET /internal/ingest-queue` 领取到期信息源。是否到期由后端按 `schedule_cron` 和 `last_run_at` 判定，前端点「运行」即置为到期。
2. 按源的 `adapter` 选适配器抓列表。适配器复用 `agent/sense2act/crawler/adapters/`，与 Agent 查证用的是同一套代码。
3. 对未推送过的新条目逐条补抓详情页，取正文、预算、截止日、采购人，详情原文作为快照。两次请求之间至少间隔 `COLLECTOR_REQUEST_INTERVAL` 秒。
4. `POST /internal/documents` 分批推送。后端负责 URL 归一、去重、机构归一和快照落盘。
5. `POST /internal/ingest-runs` 回报本轮结果。后端据此更新源健康度：连续失败 3 次降级，10 次标记故障。

失败不伪装：列表被拦截、页面改版、全部超时都如实回报失败，不会当成"没有新公告"。单条详情失败只记一条错误，下一轮重试；连续 3 次失败后按列表级（标题 + 阶段等结构化字段、无快照）入库。

## 适配器与采集配置

| `adapter` | 来源 | 文档类型 | 补抓详情 |
| --- | --- | --- | --- |
| `ccgp` | 中国政府采购网（地方 + 中央公开招标公告） | announcement | 是 |
| `tianjin_zfcg` | 天津市政府采购网首页 | announcement | 是 |
| `yifangbao` | 乙方宝（聚合源） | announcement | 否（详情在登录墙后） |
| `toutiao` / `south_weekend` / `cctv_news` | 新闻检索 | news | 是 |
| `sogou_wechat` | 搜狗微信 | news | 否（微信正文属坚硬平台，只收摘要） |

未注册的 `adapter`（例如 demo 脚本建的 `demo` 源）会被跳过，不回报，留给其他采集服务处理。

信息源的 `config`（前端表单里的「采集配置」）支持这些键，都可省略：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `max_items` | 30 | 每轮最多处理的新条目数，也是详情补抓的上限 |
| `keywords` / `exclude_keywords` | 空 | 标题包含任一关键词才收 / 包含即丢 |
| `stages` | 空 | 招标阶段白名单，如 `["招标采购", "采购意向"]` |
| `category` | 空 | 统一打的品目。后端按"机构 + 品目"维护金额基线 |
| `region` | 空 | 列表不带地域时的兜底，如天津政采填 `天津市` |
| `pages` | 2 | ccgp 每个栏目回溯的页数（每页 20 条） |
| `channels` | 空 | 乙方宝 zbzt 频道路径，如 `["/zbzt/41/3/"]` |
| `queries` / `per_query` | 空 / 10 | 新闻检索词（检索型新闻源必填）/ 每个词取几条 |
| `doc_type` / `detail` | 按适配器 | 覆盖默认的文档类型 / 是否补抓详情 |

示例：只收医院相关的公开招标公告，品目记为医疗设备：

```json
{"keywords": ["医院"], "category": "医疗设备", "max_items": 30}
```

## 运行

全栈容器里已包含（`docker compose up -d collector`）。单独运行需要 agent 的代码在 `PYTHONPATH` 上：

```powershell
pip install -r requirements.txt
$env:PYTHONPATH = '..\agent'
$env:INTERNAL_API_KEY = 'local-dev-internal-key'   # 与后端一致
python -m collector            # 常驻；加 --once 只跑一轮
```

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8080` | 后端地址 |
| `INTERNAL_API_KEY` | — | 必填，与后端一致 |
| `COLLECTOR_POLL_SECONDS` | 60 | 轮询间隔 |
| `COLLECTOR_REQUEST_INTERVAL` | 1 | 对源站两次请求的最小间隔（秒） |
| `COLLECTOR_STATE` | `data/state.json` | 本地状态：每个源已推送的 URL 与详情失败次数。丢失无害，入库仍由后端判重 |

## 测试

```powershell
$env:PYTHONPATH = '..\agent'; $env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -p "test_*.py"
```

`tests/test_collector.py` 用假后端和按 URL 路由的假页面校验：文档字段映射、跨轮去重、详情失败重试与降级入库、列表被拦或改版如实回报、检索型新闻源合并。

## 红线

与 agent 爬虫框架一致：不做验证码对抗、签名逆向、代理池；遇到拦截页如实回报，不重试绕过；页面改版回报 `parser_changed`。
