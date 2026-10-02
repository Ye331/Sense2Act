# -*- coding: utf-8 -*-
"""
E3-7 演示链路③(调查):模拟调查 Agent 走完调查生命周期——
领取 created → start(CAS 领取)→ context 一次取齐 → 两轮 steps/问题留痕 → 保持 investigating。
幂等:已 start 且已有步骤的调查直接跳过,不重复灌。

用法:
  python scripts/seed_s3.py                 # 链路①(若未灌)
  python scripts/seed_s4_signals.py         # 链路②(产 0.92 超阈值信号 → 自动建 created 调查)
  python scripts/seed_s5_investigation.py   # 默认 http://127.0.0.1:8080
  BASE=http://host:port INTERNAL_API_KEY=xxx python scripts/seed_s5_investigation.py

种子效果:1 条 investigating 调查,含 2 轮 steps(questions/tool/reflection)与 2 个问题(1 clarified / 1 open),
token_used>0、cost_estimate 有值;可用 curl -N 实时观察事件流(见脚本末尾输出)。
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


def step(inv_id, event, round_no, payload, token_usage=None):
    body = {"event": event, "round": round_no, "payload": payload}
    if token_usage is not None:
        body["token_usage"] = token_usage
    r = call("POST", "/api/v1/internal/investigations/%s/steps" % inv_id, body, internal=True)
    d = r["data"]
    print("  step seq=%s %-18s round=%s%s" % (
        d["seq"], event, d["round"], "  token_usage=%s" % token_usage if token_usage else ""))
    return d


def main():
    print("BASE =", BASE)
    token = login()

    # 1) 领取:created 列表按 created_at asc(链路② 的 0.92 信号会自动建 created 调查)
    r = call("GET", "/api/v1/internal/investigations?status=created", internal=True)
    items = r["data"]["items"]
    if items:
        inv = items[0]
        inv_id = inv["id"]
        print("领取: %s ← 信号 %s(created, max_rounds=%s, token_budget=%s)"
              % (inv_id, inv["signal_id"], inv["max_rounds"], inv["token_budget"]))
    else:
        # 没有待领:若已有 seeded 的 investigating 调查 → 幂等跳过,否则提示先跑链路②
        r = call("GET", "/api/v1/investigations?status=investigating&page_size=5", token=token)
        for it in r["data"]["items"]:
            r2 = call("GET", "/api/v1/investigations/%s/steps?limit=1" % it["id"], token=token)
            if r2["data"]["items"]:
                print("已 seeded: %s(investigating, steps>0),跳过。"
                      "事件流: curl -N '%s/api/v1/investigations/%s/stream?token=%s'"
                      % (it["id"], BASE, it["id"], token))
                return
        print("没有 created 调查。先跑 python scripts/seed_s4_signals.py(0.92 信号自动开调查)。")
        return

    # 2) start:CAS 领取 created → investigating(留 investigation_started 步骤)
    r = call("POST", "/api/v1/internal/investigations/%s/start" % inv_id, None, internal=True)
    print("start: status=%s started_at=%s" % (r["data"]["status"], r["data"]["started_at"]))

    # 3) context:一次给齐信号 hits + 文档全文 + open 问题 + 证据 + 剩余预算
    ctx = call("GET", "/api/v1/internal/investigations/%s/context" % inv_id, internal=True)["data"]
    doc = ctx["signal"].get("document") or {}
    print("context: 文档《%s》 hits=%s 预算剩余=%s"
          % (doc.get("title"), len(ctx["signal"]["hits"]), ctx["budget_remaining"]))

    # 4) 第一轮:提两个问题 + 一次工具调用
    step(inv_id, "round_started", 1, {"round": 1})
    step(inv_id, "questions_generated", 1, {"questions": [
        "该机构近半年同类项目采购金额基线是多少?",
        "本项目预算相对基线的偏离幅度是否显著?",
    ]})
    q_ids = []
    for text in ["该机构近半年同类项目采购金额基线是多少?", "本项目预算相对基线的偏离幅度是否显著?"]:
        r = call("POST", "/api/v1/internal/questions",
                 {"investigation_id": inv_id, "text": text, "raised_in_round": 1}, internal=True)
        q_ids.append(r["data"]["id"])
        print("  问题 %s open: %s" % (r["data"]["id"], text))
    step(inv_id, "tool_selected", 1, {"tool": "doc_search",
                                      "args": {"keyword": "采购", "org": doc.get("org_name")}})
    step(inv_id, "tool_completed", 1,
         {"tool": "doc_search", "ok": True, "latency_ms": 210, "result_summary": "命中近半年同类公告 5 篇,均值 82 万"},
         token_usage=1800)
    step(inv_id, "reflection_updated", 1,
         {"clarified": 0, "new_questions": 1, "judgment": "基线数据初步拿到,需再核对预算口径"})

    # 5) 第二轮:补一次工具调用,把问题一 clarified(E4 起才可引用证据,evidence_ids 留空)
    step(inv_id, "round_started", 2, {"round": 2})
    step(inv_id, "tool_selected", 2, {"tool": "org_stats_read", "args": {"category": doc.get("category")}})
    step(inv_id, "tool_completed", 2,
         {"tool": "org_stats_read", "ok": True, "latency_ms": 90,
          "result_summary": "样本 12,均值 86 万,p95 210 万;本次 325 万超 p95"},
         token_usage=2200)
    r = call("PATCH", "/api/v1/internal/questions/%s" % q_ids[0],
             {"status": "clarified", "answer_summary": "近半年同类 5 篇,均值 82-86 万,p95 210 万;本次 325 万显著超基线"},
             internal=True)
    print("  问题 %s → %s" % (q_ids[0], r["data"]["status"]))
    step(inv_id, "reflection_updated", 2,
         {"clarified": 1, "new_questions": 0, "judgment": "金额异常成立,证据链待 E4 登记"})

    # 6) 汇总
    r = call("GET", "/api/v1/investigations/%s" % inv_id, token=token)
    d = r["data"]["investigation"]
    print("汇总: status=%s current_round=%s token_used=%s cost_estimate=%s"
          % (d["status"], d["current_round"], d["token_used"], d["cost_estimate"]))
    print("      问题: " + " / ".join(
        "%s=%s" % (q["id"], q["status"]) for q in r["data"]["questions"]))
    print("实时观察: curl -N '%s/api/v1/investigations/%s/stream?token=%s'" % (BASE, inv_id, token))
    print("断线续传: 上面命令加 -H 'Last-Event-ID: <最后收到的 seq>' 重连只补漏。")


if __name__ == "__main__":
    main()
