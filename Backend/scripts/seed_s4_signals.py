# -*- coding: utf-8 -*-
"""
E2-8 演示链路②(信号):模拟信号发现服务,拉检测队列 → 对链路①文档推命中 → 回报扫描完成。
幂等:同 document + detector + hits 重复推返回已有 signal_id 不新建;扫描标记重复回报无害。

用法:
  python scripts/seed_s3.py                 # 先灌链路①(若未灌)
  python scripts/seed_s4_signals.py         # 默认 http://127.0.0.1:8080
  BASE=http://host:port INTERNAL_API_KEY=xxx python scripts/seed_s4_signals.py

种子效果:4 条信号,其中"AI辅助诊断系统采购"score=0.92 超默认阈值 0.85 → 自动开调查(investigating)。
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8080").rstrip("/")
INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "demo-internal-key-2026")
ADMIN = ("admin@sense2act.local", os.environ.get("SEED_ADMIN_PASSWORD", "admin123"))
DETECTOR = "demo-detector-v1"

# 绕过系统代理,本地直连
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def call(method, path, body=None, token=None, internal=False):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if internal:
        req.add_header("X-Internal-Key", INTERNAL_KEY)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login():
    r = call("POST", "/api/v1/auth/token", {"email": ADMIN[0], "password": ADMIN[1]})
    return r["data"]["token"]


# ---------- 信号样本:对链路①文档生成命中(1 条超阈值自动开调查) ----------

SIGNALS = [
    {   # score 0.92 ≥ 0.85 → 自动开调查
        "keyword": "AI辅助诊断系统采购项目",
        "score": 0.92,
        "hits": [
            {"rule_type": "amount_anomaly", "rule_id": None, "rule_version": None, "weight": 0.6,
             "detail": {"z": 5.9, "baseline_mean": 820000, "amount": 3250000}},
            {"rule_type": "semantic_match", "rule_id": None, "rule_version": None, "weight": 0.4,
             "detail": {"similarity": 0.91, "profile": "医疗AI"}},
        ],
    },
    {
        "keyword": "数据中台建设采购",
        "score": 0.78,
        "hits": [
            {"rule_type": "semantic_match", "rule_id": None, "rule_version": None, "weight": 1.0,
             "detail": {"similarity": 0.78, "profile": "数据平台"}},
        ],
    },
    {
        "keyword": "科研大数据平台采购",
        "score": 0.66,
        "hits": [
            {"rule_type": "frequency_burst", "rule_id": None, "rule_version": None, "weight": 0.5,
             "detail": {"freq_30d": 3, "baseline_mean_30d": 0.8}},
            {"rule_type": "semantic_match", "rule_id": None, "rule_version": None, "weight": 0.5,
             "detail": {"similarity": 0.72, "profile": "数据平台"}},
        ],
    },
    {
        "keyword": "网络安全等级保护整改",
        "score": 0.54,
        "hits": [
            {"rule_type": "semantic_match", "rule_id": None, "rule_version": None, "weight": 1.0,
             "detail": {"similarity": 0.61, "profile": "医疗网络安全"}},
        ],
    },
]


def find_doc_ids_from_queue():
    """检测队列:未扫描文档一次给齐(文档+基线+画像)。返回 {keyword 片段: doc_id} 与队列原文。"""
    r = call("GET", "/api/v1/internal/detection-queue?limit=100", internal=True)
    items = r["data"]["items"]
    found = {}
    for item in items:
        title = item["document"]["title"] or ""
        for spec in SIGNALS:
            if spec["keyword"] in title:
                found[spec["keyword"]] = item["document"]["id"]
    return found, items


def find_doc_ids_by_keyword(token, missing):
    """队列已扫过的文档走文档检索兜底(GET /documents 双认证,内部 key 可用)。"""
    found = {}
    for kw in missing:
        r = call("GET", "/api/v1/documents?keyword=%s&page=1&page_size=1" % urllib.request.quote(kw), internal=True)
        items = r["data"]["items"]
        if items:
            found[kw] = items[0]["id"]
    return found


def main():
    print("BASE =", BASE)
    token = login()

    queue_ids, queue_items = find_doc_ids_from_queue()
    print("检测队列: 待扫描文档 %d 篇,内嵌启用画像 %s"
          % (len(queue_items), queue_items[0]["profiles"] if queue_items else "—(空队列)"))
    missing = [s["keyword"] for s in SIGNALS if s["keyword"] not in queue_ids]
    if missing:
        queue_ids.update(find_doc_ids_by_keyword(token, missing))

    pushed = []
    for spec in SIGNALS:
        doc_id = queue_ids.get(spec["keyword"])
        if not doc_id:
            print("  [跳过] 找不到文档:%s(先跑 scripts/seed_s3.py)" % spec["keyword"])
            continue
        r = call("POST", "/api/v1/internal/signals",
                 {"document": {"id": doc_id}, "hits": spec["hits"],
                  "score": spec["score"], "detector": DETECTOR}, internal=True)
        d = r["data"]
        mark = "→ 自动开调查" if d["auto_investigated"] else ""
        print("  信号 %s score=%.2f status=%s %s"
              % (d["signal_id"], spec["score"], d["status"], mark))
        pushed.append(doc_id)

    if pushed:
        r = call("POST", "/api/v1/internal/detection-scan-complete",
                 {"document_ids": pushed, "detector": DETECTOR}, internal=True)
        print("扫描完成回报: updated=%d(重复回报幂等,再跑一次为 0)" % r["data"]["updated"])
    else:
        print("无文档需要回报扫描(队列此前已扫完)。")

    r = call("GET", "/api/v1/signals?page=1&page_size=100", token=token)
    data = r["data"]
    by_status = {}
    for s in data["items"]:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    print("信号总数: %d,按状态: %s" % (data["total"], json.dumps(by_status, ensure_ascii=False)))
    r = call("GET", "/api/v1/signals?status=investigating&page=1&page_size=10", token=token)
    for s in r["data"]["items"]:
        print("  调查中: %s ← %s (score=%s)" % (s["id"], s["document"]["title"], s["score"]))
    print("幂等验证: 再跑一次,4 条 signal_id 应保持不变,scan updated=0。")


if __name__ == "__main__":
    main()
