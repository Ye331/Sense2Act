# -*- coding: utf-8 -*-
"""
E1-7 演示链路②(语义):D12 约定的 embedding 端点的本地演示替身。

契约(与 architecture.md D12 一致):
  POST /embed  {"inputs": ["文本", ...]}  →  {"embeddings": [[512 维 float], ...]}
  可选 Authorization: Bearer <key> —— 本替身不校验。

算法:中文取 2 字滑窗、英文/数字取词,token → md5 定长哈希到 512 维中的某维并 +1,
L2 归一化。同领域文档共享子词 → 向量相近,足以演示"语义检索/RRF 融合"链路。
确定性:同文本永远同向量。

⚠️ 仅用于本地演示,不是真实语义模型。生产接 TEI / BGE / text-embedding 服务,
改 compose 的 EMBEDDING_ENDPOINT 即可,后端代码零改动(D12 边界)。

用法:
  python scripts/embedding_stub.py            # 0.0.0.0:8901
  PORT=9001 python scripts/embedding_stub.py
compose 里配:EMBEDDING_ENDPOINT=http://host.docker.internal:8901/embed
自测:curl -X POST http://127.0.0.1:8901/embed -d '{"inputs":["你好"]}'
"""
import hashlib
import json
import math
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

DIM = 512
PORT = int(os.environ.get("PORT", "8901"))

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
CJK = re.compile(r"[一-鿿]")


def tokens(text):
    """中文 2 字滑窗 + ASCII 词。滑窗让'数据中台/数据治理'共享'数据'语汇。"""
    out = []
    runs = []
    cur = ""
    for ch in text:
        if CJK.match(ch):
            cur += ch
        else:
            if cur:
                runs.append(cur)
                cur = ""
    if cur:
        runs.append(cur)
    for run in runs:
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    out.extend(t.lower() for t in TOKEN_RE.findall(text))
    return out


def embed(text):
    v = [0.0] * DIM
    for t in tokens(text):
        d = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16) % DIM
        v[d] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norm, 6) for x in v]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") not in ("/embed", ""):
            self.send_error(404)
            return
        try:
            print("[stub] headers: TE=%r CL=%r" % (self.headers.get("Transfer-Encoding"),
                                                   self.headers.get("Content-Length")))
            body = json.loads(self.read_body())
            inputs = body.get("inputs")
            if not isinstance(inputs, list):
                raise ValueError("inputs must be a list")
            resp = json.dumps({"embeddings": [embed(str(s)) for s in inputs]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        except Exception as e:  # noqa: BLE001
            resp = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    def read_body(self):
        """http.server 不解 chunked,而 Java RestClient 恰好发 chunked —— 两种都读。"""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            data = b""
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()   # 尾部 CRLF
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()       # 块后 CRLF
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def log_message(self, fmt, *args):  # 安静一点,只记一行摘要
        print("[stub] %s" % (fmt % args))


if __name__ == "__main__":
    print("embedding stub(仅演示)listening on 0.0.0.0:%d  POST /embed  dim=%d" % (PORT, DIM))
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
