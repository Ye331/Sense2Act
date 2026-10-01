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

Java 21 + Spring Boot 3（Web、Security + JWT、Validation），MyBatis-Plus（ORM），pgvector 官方 Java 客户端，Flyway 迁移，PostgreSQL 16（pgvector）。定时任务用 Spring Scheduling。快照存本地磁盘（可换 MinIO）。Redis 可选，多实例时才需要（SSE 广播、缓存），单机原型不用装。后端不调用 LLM。

## 目录结构（规划）

```
Backend/
├── AGENT.md
├── docs/
├── pom.xml
├── src/main/java/com/sense2act/backend/
│   ├── BackendApplication.java
│   ├── config/        # 安全、OpenAPI、调度线程池
│   ├── common/        # 统一响应包、错误码、ULID、分页
│   ├── api/           # Controller（用户接口 + /internal）
│   ├── service/       # 业务逻辑
│   ├── domain/        # 实体 + Repository
│   ├── ingest/        # 文档接入内部 API（去重/快照/机构归一/org_stats）
│   ├── signals/       # 信号通道 + 检测内部 API
│   ├── agent/         # 调查生命周期（状态校验/留痕/预算）
│   ├── sse/           # SSE 推送（内存广播，多实例换 Redis）
│   └── scheduler/     # 超时清理、回测数据集导出
├── src/main/resources/
│   ├── application.yml
│   └── db/migration/  # Flyway SQL
├── scripts/           # 示例数据
└── src/test/
```
