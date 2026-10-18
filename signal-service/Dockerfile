# 信号服务:检测 worker(python -m bridge)与向量端点(uvicorn application:app)共用一个镜像
FROM python:3.11-slim
ARG PIP_INDEX_URL=https://pypi.org/simple
# 国内网络可在 .env 设 HF_ENDPOINT=https://hf-mirror.com
ARG HF_ENDPOINT=https://huggingface.co
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_INDEX_URL=${PIP_INDEX_URL} TZ=Asia/Shanghai
WORKDIR /app
# CPU 版 torch 单独装,避免拉 CUDA 依赖
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 语义模型随镜像固化(版本见 config.yaml embedding.revision),运行时不联网
RUN HF_ENDPOINT=${HF_ENDPOINT} python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5', revision='7999e1d3359715c523056ef9478215996d62a620', local_dir='/app/.models/bge-small-zh-v1.5')"
COPY . .
EXPOSE 8001
CMD ["uvicorn", "application:app", "--host", "0.0.0.0", "--port", "8001"]
