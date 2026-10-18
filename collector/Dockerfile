# 采集 worker:领取到期信息源 → 适配器抓取 → 批量推文档 → 回报运行结果
# 适配器复用 agent 的 sense2act 包:compose 以 additional_contexts 传入 agent 目录(见根目录 docker-compose.yml)
FROM python:3.11-slim
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_INDEX_URL=${PIP_INDEX_URL} TZ=Asia/Shanghai PYTHONPATH=/app/agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=agent sense2act /app/agent/sense2act
COPY collector /app/collector
CMD ["python", "-m", "collector"]
