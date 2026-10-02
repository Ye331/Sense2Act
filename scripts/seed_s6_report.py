# -*- coding: utf-8 -*-
"""
E4-7 演示链路③收尾(报告):在 seed_s5 留下的 investigating 调查上,模拟调查 Agent 完成收尾——
登记 3 条证据(本库文档变体)→ 问题带证据引用 clarified → 提交 ReportDraft
(3 结论覆盖 fact/inference/speculation、2 条建议、图谱 1 事件 + 3 主体 + 2 关系)→ complete(信号 → confirmed)。
幂等:调查已 completed 且有报告 → 直接跳过并给出查看命令。

用法:
  python scripts/seed_s3.py                  # 链路①(若未灌)
  python scripts/seed_s4_signals.py          # 链路②(0.92 信号自动建 created 调查)
  python scripts/seed_s5_investigation.py    # 链路③(领取 + 两轮留痕,保持 investigating)
  python scripts/seed_s6_report.py           # 默认 http://127.0.0.1:8080
  BASE=http://host:port INTERNAL_API_KEY=xxx python scripts/seed_s6_report.py
"""
import json
import os
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8080").rstrip("/")
INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "demo-internal-key-2026")
ADMIN = ("admin@sense2act.local", os.environ.get("SEED_ADMIN_PASSWORD", "admin123"))

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


def find_doc(token, doc_type, exclude):
    """从本库挑一篇指定 doc_type 的文档做证据;没有就退而求其次挑任意一篇别的。"""
    r = call("GET", "/api/v1/documents?doc_type=%s&page_size=10" % doc_type, token=token)
    for it in r["data"]["items"]:
        if it["id"] != exclude:
            return it
    r = call("GET", "/api/v1/documents?page_size=20", token=token)
    for it in r["data"]["items"]:
        if it["id"] != exclude:
            return it
    return None


def register_evidence(inv_id, source_type, doc_id, excerpt):
    r = call("POST", "/api/v1/internal/investigations/%s/evidences" % inv_id,
             {"source_type": source_type, "doc_id": doc_id, "excerpt": excerpt}, internal=True)
    d = r["data"]
    print("  证据 %s %s ← 《%s》" % (d["id"], source_type, d["title"]))
    return d


def main():
    print("BASE =", BASE)
    token = login()

    # 1) 找链路③的调查:investigating 且已有留痕
    inv_id = None
    r = call("GET", "/api/v1/investigations?status=investigating&page_size=10", token=token)
    for it in r["data"]["items"]:
        r2 = call("GET", "/api/v1/investigations/%s/steps?limit=1" % it["id"], token=token)
        if r2["data"]["items"]:
            inv_id = it["id"]
            break
    if inv_id is None:
        # 幂等:已有 completed + 报告 → 跳过
        r = call("GET", "/api/v1/reports?page_size=5", token=token)
        if r["data"]["items"]:
            rep = r["data"]["items"][0]
            print("已 seeded: 报告 %s(%s,调查 %s)。跳过。" % (rep["id"], rep["status"], rep["investigation_id"]))
            print("查看: curl -s '%s/api/v1/reports/%s?...' | jq" % (BASE, rep["id"]))
            return
        print("没有 investigating 且有留痕的调查。先跑 python scripts/seed_s5_investigation.py。")
        return

    ctx = call("GET", "/api/v1/internal/investigations/%s/context" % inv_id, internal=True)["data"]
    doc = ctx["signal"].get("document") or {}
    print("调查: %s 文档《%s》 token_used=%s/%s" % (inv_id, doc.get("title"),
                                                    ctx["investigation"]["token_used"],
                                                    ctx["investigation"]["token_budget"]))

    # 2) 登记 3 条证据(本库变体:回填 url/title/published_at/hash)
    print("登记证据:")
    ev1 = register_evidence(inv_id, "announcement", doc["id"],
                            "本次招标公告:预算 325 万,采购 AI 辅助诊断系统,含三年维保")
    pol = find_doc(token, "policy", doc["id"])
    ev2 = register_evidence(inv_id, "policy", pol["id"],
                            "《医院信息化建设规划(2024-2027)》:分三期建设,本期为第三期")
    news = find_doc(token, "news", doc["id"])
    ev3 = register_evidence(inv_id, "news", news["id"],
                            "行业报道:同市另有 2 家医院近期启动同类 AI 采购立项")

    # 3) 把 open 问题带证据引用收敛
    for q in ctx["open_questions"]:
        r = call("PATCH", "/api/v1/internal/questions/%s" % q["id"],
                 {"status": "clarified",
                  "answer_summary": "近半年同类均值 82-86 万,p95 210 万;本次 325 万显著超基线",
                  "evidence_ids": [ev1["id"]]}, internal=True)
        print("  问题 %s → %s(证据 %s)" % (q["id"], r["data"]["status"], ev1["id"]))

    # 4) 提交 ReportDraft:3 结论(三种 nature)/ 2 建议 / 图谱 1 事件 + 3 主体 + 2 关系
    draft = {
        "title": "某三甲医院大额AI辅助诊断招标:规划三期建设而非孤立采购",
        "summary": "该招标并非孤立事件:属于医院两年前规划的第三期建设;同市另有 2 家医院启动类似项目,"
                   "可能是新一轮建设潮的开始。",
        "claims": [
            {"text": "本次采购属于《医院信息化建设规划(2024-2027)》第三期建设计划",
             "nature": "fact", "confidence": 0.95, "evidence_ids": [ev2["id"], ev1["id"]]},
            {"text": "本次预算约为近半年同类项目均值的 4 倍,显著超出 p95 基线",
             "nature": "inference", "confidence": 0.8, "evidence_ids": [ev1["id"]]},
            {"text": "同市另有 2 家医院启动类似项目,可能是一轮建设潮的开始",
             "nature": "speculation", "confidence": 0.6, "evidence_ids": [ev3["id"]]},
        ],
        "action_suggestions": [
            {"text": "评估参与方式:独立投标或联合既往合作方", "priority": "high"},
            {"text": "跟踪同市另外两家医院的立项进展,预判建设潮规模", "priority": "medium"},
        ],
        "event_extraction": {
            "event": {"title": "某市医疗信息化三期建设潮", "type": "construction_wave",
                      "start_date": "2024-03-15"},
            "entities": [
                {"name": "某三甲医院", "type": "org", "role": "participant"},
                {"name": "医院信息化建设规划(2024-2027)", "type": "policy", "role": "scope"},
                {"name": "华东/某省某市", "type": "region", "role": "scope"},
            ],
            "relations": [
                {"source": "entity:0", "target": "event", "relation": "参与"},
                {"source": "entity:1", "target": "event", "relation": "佐证"},
            ],
        },
        "token_usage": 4000,
    }
    r = call("POST", "/api/v1/internal/investigations/%s/report" % inv_id, draft, internal=True)
    rep = r["data"]
    print("报告: %s status=%s event_id=%s" % (rep["id"], rep["status"], rep["event_id"]))
    print("      meta=%s" % json.dumps(rep["meta"], ensure_ascii=False))
    print("      disclaimer=%s" % rep["disclaimer"])

    # 5) complete:调查 → completed,报告 → published,信号 → confirmed(D6)
    r = call("POST", "/api/v1/internal/investigations/%s/complete" % inv_id, None, internal=True)
    print("complete: status=%s finished_at=%s" % (r["data"]["status"], r["data"]["finished_at"]))
    print("          信号 %s → confirmed" % ctx["investigation"]["signal_id"])

    # 6) 报告页 + 导出
    rep2 = call("GET", "/api/v1/reports/%s" % rep["id"], token=token)["data"]
    print("报告页: %s 条结论 / %s 条建议 / status=%s"
          % (len(rep2["claims"]), len(rep2["action_suggestions"]), rep2["status"]))
    req = urllib.request.Request(BASE + "/api/v1/reports/%s/export?format=md" % rep["id"])
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req) as resp:
        md = resp.read().decode("utf-8")
    out = "%s.md" % rep["id"]
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print("导出: md 已存 %s(%d 字节);pdf: curl -s -H 'Authorization: Bearer <token>' "
          "'%s/api/v1/reports/%s/export?format=pdf'" % (out, len(md.encode("utf-8")), BASE, rep["id"]))
    print("回看: curl -s -H 'Authorization: Bearer <token>' %s/api/v1/reports/%s | jq" % (BASE, rep["id"]))


if __name__ == "__main__":
    main()
