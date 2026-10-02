# -*- coding: utf-8 -*-
"""S2 冲刺验收 demo:建源 → 领取 → 推文档(含重复/坏条目)→ 回报 → 手动触发 → 查库。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
KEY = "demo-internal-key-2026"

# 本机联调必须绕过系统代理,否则请求被代理改写导致间歇性 401
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def call(method, path, body=None, token=None, key=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if key:
        req.add_header("X-Internal-Key", key)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        out = e.read().decode("utf-8")
        print(f"  [debug] {method} {path} -> {e.code} {out[:200]}")
        print(f"  [debug] 实发请求头: {dict(req.header_items())}")
        return json.loads(out)


def show(title, obj):
    print("== " + title)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


token = call("POST", "/auth/token", {"email": "admin@sense2act.local", "password": "admin123"})["data"]["token"]
print("== 1) admin 登录 ok")

src = call("POST", "/sources", {
    "name": "demo-中国政府采购网-医疗",
    "type": "web_page",
    "url": "http://www.ccgp.gov.cn/",
    "adapter": "ccgp",
    "schedule_cron": "0 0 6 * * *",
    "config": {"list_selector": ".list li", "max_pages": 3},
}, token=token)
src_id = src["data"]["id"]
show("2) 建源: " + src_id, src["data"])

queue = call("GET", "/internal/ingest-queue", key=KEY)
show("3) 队队领取(应含新源)", [i["source_id"] for i in queue["data"]["items"]])

batch = {"source_id": src_id, "items": [
    {"url": "http://demo.example/notice/1001", "doc_type": "announcement",
     "title": "某市人民医院AI辅助诊断系统采购公告", "org_name": "（某市）人民医院",
     "amount": "3250000.00", "publish_date": "2026-09-24", "deadline": "2026-10-15",
     "region": "华东/某省", "category": "医疗信息化",
     "content_text": "本项目采购AI辅助诊断系统一套……", "raw_html": "<html>公告原文A</html>"},
    {"url": "http://demo.example/notice/1002/", "doc_type": "announcement",
     "title": "某市人民医院CT设备维保服务公告", "org_name": "  (某市)人民医院  ",
     "amount": "820000.00", "publish_date": "2026-09-26", "category": "医疗信息化",
     "content_text": "维保服务……", "raw_html": "<html>公告原文B</html>"},
    {"url": "http://demo.example/notice/1001#detail", "doc_type": "announcement",
     "title": "某市人民医院AI辅助诊断系统采购公告", "publish_date": "2026-09-24",
     "content_text": "本项目采购AI辅助诊断系统一套……"},
    {"url": "http://demo.example/notice/1003", "doc_type": "announcement",
     "title": "某市人民医院AI辅助诊断系统采购公告", "publish_date": "2026-09-24",
     "content_text": "本项目采购AI辅助诊断系统一套……"},
    {"url": "http://demo.example/notice/1004", "doc_type": "tender", "title": "坏类型", "content_text": "x"},
    {"url": "http://demo.example/notice/1005", "doc_type": "announcement", "title": "坏金额",
     "amount": "abc", "content_text": "y"},
]}
show("4) 推 6 条(2好/1URL重复/1内容重复/1坏类型/1坏金额)", call("POST", "/internal/documents", batch, key=KEY)["data"])

repush = {"source_id": src_id, "items": batch["items"][:2]}
show("5) 重推前 2 条(应全 duplicates)", call("POST", "/internal/documents", repush, key=KEY)["data"])

run = call("POST", "/internal/ingest-runs", {
    "source_id": src_id, "ok": True,
    "stats": {"fetched": 6, "accepted": 2, "duplicates": 2, "failed": 2},
    "errors": [
        {"url": "http://demo.example/notice/1004", "stage": "normalize", "error": "unknown doc_type"},
        {"url": "http://demo.example/notice/1005", "stage": "normalize", "error": "amount not numeric"},
    ],
}, key=KEY)
show("6) 回报运行", run["data"])

queue2 = call("GET", "/internal/ingest-queue", key=KEY)
show("7) 回报后队列(6点cron,今天已跑,应不含)", [i["source_id"] for i in queue2["data"]["items"]])

call("POST", f"/sources/{src_id}/run", token=token)
queue3 = call("GET", "/internal/ingest-queue", key=KEY)
show("8) 手动触发后队列(D2 立即可领)", [i["source_id"] for i in queue3["data"]["items"]])

show("9) 删除有文档的源(应 40901)", call("DELETE", f"/sources/{src_id}", token=token))
