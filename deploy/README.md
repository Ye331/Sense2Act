# 服务器部署

一台 Linux 服务器跑全栈，宿主机 nginx 终止 TLS，整站反代给前端容器，前端容器再把 `/api` 转给后端。数据库、语义模型、采集、检测、调查都在 Docker 里。

```
浏览器 ──https──▶ 宿主机 nginx(443) ──▶ frontend 容器(127.0.0.1:8088) ──/api──▶ api ──▶ postgres
                                                                      ▲
                               collector / signal-worker / agent ─────┘(容器内网,X-Internal-Key)
```

## 前置

- Docker Engine 24+ 与 Docker Compose v2.24+（覆盖文件用了 `!reset` / `!override`）
- 4 核 8G 起步：语义模型（bge-small-zh，CPU）常驻约 1G，后端约 0.5G
- 一个解析到服务器的域名，以及 Let's Encrypt 证书（`certbot certonly --nginx -d <域名>`）

## 步骤

```bash
git clone <仓库地址> sense2act && cd sense2act
cp deploy/prod.env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # 依次生成 POSTGRES_PASSWORD / JWT_SECRET / INTERNAL_API_KEY 填进 .env
vi .env                                                        # 再填三个种子账号密码

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps                                              # api 显示 healthy 即后端就绪

sudo cp deploy/nginx.conf.example /etc/nginx/conf.d/sense2act.conf   # 改 server_name 与证书路径
sudo nginx -t && sudo systemctl reload nginx
```

打开 `https://<域名>`，用 `admin@sense2act.local` 与 `.env` 里的 `SEED_ADMIN_PASSWORD` 登录。然后在「信息源管理」建信息源（如 `ccgp`，采集配置见 [collector/README.md](../collector/README.md)），采集服务按运行计划自动抓取。

## 更新

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

数据库迁移（Flyway）在后端启动时自动执行。数据在具名卷 `pgdata`、`snapshots`、`agentdata`、`collectordata`、`newsdata` 里，重建容器不丢。

## 要点

- **密钥**：`docker-compose.prod.yml` 要求 `POSTGRES_PASSWORD`、`JWT_SECRET`、`INTERNAL_API_KEY` 和三个种子密码都显式提供，留空直接拒绝启动；不要沿用根目录 `.env.example` 里的本地默认值。
- **端口**：postgres 不发布端口；后端只绑 `127.0.0.1:8080`（排障用）；对外只有宿主机 nginx 的 80/443。
- **反代头**：宿主机 nginx 必须带 `X-Forwarded-Proto` / `X-Forwarded-Port`（示例里已有）。后端靠它们认出浏览器的 https 来源，缺了登录等 POST 会被当成跨域拒掉（403）。
- **SSE**：调查实时时间线和全局通知走 SSE，宿主机 nginx 要关缓冲、拉长读超时（示例里已有）。
- **真实模型**：`.env` 设 `AGENT_MODE=live` 与 `DEEPSEEK_API_KEY` 后 `docker compose ... up -d agent`。
- **备份**：`docker compose exec postgres pg_dump -U sense2act sense2act > backup.sql`；文档快照在 `snapshots` 卷。

只部署后端、前端另放其他域名时，参考 `Backend/scripts/deploy/nginx.conf.example`，并在 `.env` 设置 `CORS_ALLOWED_ORIGINS` 为前端来源。
