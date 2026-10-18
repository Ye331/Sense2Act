# 调查 Agent worker:领取后端调查,跑 S1-S7,留痕/证据/报告回写
FROM python:3.11-slim
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_INDEX_URL=${PIP_INDEX_URL} TZ=Asia/Shanghai
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "sense2act", "worker"]
