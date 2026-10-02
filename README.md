# Sense2Act Backend

招投标商机发现的后端。链路：采集招标公告，产出信号，触发 Agent 调查，生成带证据链的报告，用户反馈再迭代信号规则。

## 范围

本仓库只做后端平台：数据存储、采集、API、调查的状态管理与留痕。

爬虫、信号发现和 agent loop 是三个独立的外部服务，不在这里实现。它们通过内部 HTTP API 接入，方向单向：它们调后端。后端不调用它们，也不关心它们的实现。外部服务没接入时后端照常运行，只是没有新文档、没有新信号，调查停在 created。

| 环节 | 本仓库 | 外部服务 |
| --- | --- | --- |
| 采集 | 源配置管理、文档接入 API（去重/快照/机构归一/基线） | 爬虫服务 |
| 信号 | 基线统计、信号落库、人工决策、自动触发 | 信号发现服务 |
| 调查 | 状态机、留痕、SSE、预算记账 | Agent 调查服务 |
| 报告 | 存储、查询、导出 | 报告生成（随 Agent 服务） |
| 反馈 | 反馈记录、回测数据集、规则版本管理 | 回测评估 |

## 文档

- [docs/architecture.md](docs/architecture.md) 架构与模块设计
- [docs/api-design.md](docs/api-design.md) API 定义，含外部服务的接入契约（内部 API）
- [docs/data-model.md](docs/data-model.md) 数据库表设计
- [docs/backlog.md](docs/backlog.md) 敏捷计划：史诗、用户故事与验收标准、DoD、冲刺安排
- AGENT.md 给 AI 编码助手的说明

## 技术栈

Java 21 + Spring Boot 3（Web、Security + JWT、Validation、Actuator），MyBatis-Plus（ORM），Flyway 迁移，PostgreSQL 16（pgvector 镜像），springdoc-openapi，Testcontainers 集成测试。定时任务用 Spring Scheduling。快照存本地磁盘（可换 MinIO）。Redis 可选，多实例时才需要（SSE 广播、缓存），单机原型不用装。后端不调用 LLM。

## 快速开始（本地）

前置：JDK 21、Maven 3.9+、Docker Desktop 已启动。

```bash
# 1. 环境变量（本地默认值即可跑）
cp .env.example .env

# 2a. 全栈容器方式：postgres + api 一键起
docker compose up --build
# 2b. 或只起数据库，应用在 IDEA 里跑（本地 profile，默认连 localhost:5432）
docker compose up -d postgres

# 3. 验证
curl http://127.0.0.1:8080/actuator/health          # {"status":"UP"}
curl -X POST http://127.0.0.1:8080/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@sense2act.local","password":"admin123"}'
# swagger: http://127.0.0.1:8080/swagger-ui.html
```

首次启动自动建表（Flyway）并幂等补种三个角色账号：`admin@sense2act.local / admin123`、`analyst@sense2act.local / analyst123`、`viewer@sense2act.local / viewer123`（密码用 `SEED_*` 环境变量改）。

## 演示数据

```bash
# S3 演示链路①(E1-7):空库灌 10 个源 + 32 条文档(落库 30 篇,含 2 条重复),可重复执行
# 二跑全计 duplicates,不新增 —— 幂等即验收
python scripts/seed_s3.py

# 语义检索演示(可选):先起 D12 embedding 演示端点,再让 api 容器知道它
python scripts/embedding_stub.py        # 0.0.0.0:8901,POST /embed,512 维,确定性哈希,仅演示
# .env 加 EMBEDDING_ENDPOINT=http://host.docker.internal:8901/embed 后
# docker compose up -d --build 重建 api;不配则语义自动降级关键词,接口照常 200

# S2 采集链路演示:建源 → 领取 → 推文档(含重复/坏条目)→ 回报 → 手动触发
python scripts/demo-s2.py

# S4 演示链路②(E2-8):拉检测队列 → 推 4 条信号 → 回报扫描完成,可重复执行
# 其中 score=0.92 的一条超默认阈值 0.85,自动开调查(investigating);二跑 signal_id 不变 —— 幂等即验收
python scripts/seed_s4_signals.py

# S5 演示链路③(E3-7):模拟调查 Agent 领取 → start → context → 两轮 steps/questions 留痕
# 种下 1 条 investigating 调查(2 轮步骤 + 2 个问题,token_used>0);已 seeded 则跳过
python scripts/seed_s5_investigation.py
```

调查时间线实时观察(契约 §7;EventSource 设不了 Authorization 头,JWT 走 `?token=`):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/api/v1/auth/token -H 'Content-Type: application/json' \
  -d '{"email":"analyst@sense2act.local","password":"analyst123"}' | sed 's/.*"token":"\([^"]*\)".*/\1/')
INV=$(curl -s "http://127.0.0.1:8080/api/v1/investigations?status=investigating" \
  -H "Authorization: Bearer $TOKEN" | sed 's/.*"items":\[{"id":"\([^"]*\)".*/\1/')
# 连接即补发全部留痕步骤(id=seq),25s 心跳;断线后带 -H 'Last-Event-ID: <最后 seq>' 重连只补漏
curl -N "http://127.0.0.1:8080/api/v1/investigations/$INV/stream?token=$TOKEN"
```

灌完后检索示例（`documents`/`organizations` 的 GET 同时接受 JWT 与 `X-Internal-Key`，见 api-design §1）：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/api/v1/auth/token -H 'Content-Type: application/json' \
  -d '{"email":"analyst@sense2act.local","password":"analyst123"}' | sed 's/.*"token":"\([^"]*\)".*/\1/')
curl -s "http://127.0.0.1:8080/api/v1/documents?keyword=采购&region=华东&amount_gte=1000000" \
  -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8080/api/v1/documents?semantic=数据中台" -H "Authorization: Bearer $TOKEN"
```

## 测试

```bash
mvn -B -ntp verify        # 单元 + Testcontainers 集成测试（需 Docker）
```

改了接口后重新生成 OpenAPI 基线并提交（CI 在 PR 上跑 oasdiff 破坏性变更检查）：

```bash
mvn test -Dtest=OpenApiBaselineTest -DupdateOpenapi=true
```

## 服务器部署

```bash
git pull
cp .env.example .env    # 必填强值：JWT_SECRET（≥32 字符）、INTERNAL_API_KEY、POSTGRES_PASSWORD；改种子密码
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

要点：API 容器只绑 `127.0.0.1:8080`（默认 `API_BIND`），外网经 nginx 反代（配置示例 `scripts/deploy/nginx.conf.example`，SSE 已关缓冲）；postgres 不发布端口；数据与快照在具名卷 `pgdata`、`snapshots` 里，容器重建不丢。`INTERNAL_API_KEY` 不配置时 `/api/v1/internal/*` 整组 403，外部服务接入时再配。

## 目录结构

```
Backend/
├── AGENT.md                # 给 AI 编码助手的说明
├── docs/                   # architecture / api-design / data-model / backlog
│   └── openapi/baseline.json   # 契约基线（oasdiff 闸门用）
├── .github/workflows/ci.yml   # 构建+测试；PR 上契约破坏性检查
├── scripts/                # 种子与演示脚本：seed_s3（灌库）、embedding_stub（D12 演示端点）、demo-s2
│   └── deploy/             # nginx 配置示例等
├── docker-compose.yml          # 基础编排（本地 up 即用）
├── docker-compose.override.yml # 本地自动叠加：postgres 绑 127.0.0.1:5432
├── docker-compose.prod.yml     # 服务器叠加：日志轮转
├── Dockerfile              # 多阶段构建，非 root 运行
├── pom.xml
└── src/
    ├── main/java/com/sense2act/backend/
    │   ├── BackendApplication.java
    │   ├── common/     # 统一响应包、错误码、异常处理、前缀 ULID、分页
    │   ├── config/     # 安全（JWT/X-Internal-Key/pbkdf2）、OpenAPI、种子账号
    │   ├── api/        # Controller（auth、users；随史诗增加）
    │   └── domain/     # 实体 + Mapper（按领域分包：user、ingest、signal…）
    ├── main/resources/
    │   ├── application{,-local,-prod}.yml
    │   └── db/migration/   # Flyway SQL，按史诗递增
    └── test/java/      # 单元测试 + Testcontainers 集成测试
```
