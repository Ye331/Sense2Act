# -*- coding: utf-8 -*-
"""
E5-6 演示链路⑤(闭环):源 → 文档 → 信号 → 调查 → 报告 → 反馈,一条龙可重复执行。
比赛评审演示用:每次运行以时间戳为键新建一条独立链路实例(不依赖此前种子,也不与它们纠缠),
结束打印看板数字、报告详情、事件图谱与规则版本,展示"反馈闭得上环"。

用法:
  python scripts/demo_s7_full_chain.py              # 默认 http://127.0.0.1:8080
  BASE=http://host:port INTERNAL_API_KEY=xxx python scripts/demo_s7_full_chain.py
"""
import datetime
import json
import os
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8080").rstrip("/")
INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "demo-internal-key-2026")
ADMIN = ("admin@sense2act.local", os.environ.get("SEED_ADMIN_PASSWORD", "admin123"))
ANALYST = ("analyst@sense2act.local", "analyst123")

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


def login(pair):
    r = call("POST", "/api/v1/auth/token", {"email": pair[0], "password": pair[1]})
    return r["data"]["token"]


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    print("BASE =", BASE, " 实例键 =", ts)
    admin = login(ADMIN)
    analyst = login(ANALYST)

    # ① 源:挑一个启用的信息源(链路①种子过;没有就现场建一个)
    r = call("GET", "/api/v1/sources?page_size=50", token=admin)
    source = next((s for s in r["data"]["items"] if s["enabled"]), None)
    if source is None:
        r = call("POST", "/api/v1/sources", {"name": "演示源-" + ts, "type": "gov",
                                             "url": "http://demo.example/" + ts,
                                             "adapter": "ccgp", "schedule_cron": "0 6 * * *",
                                             "config": {}, "enabled": True}, token=admin)
        source = r["data"]
    print("① 源   %s %s" % (source["id"], source["name"]))

    # ② 文档:模拟爬虫按该源推送解析结果(公告 + 政策背景各一篇)
    r = call("POST", "/api/v1/internal/documents", {
        "source_id": source["id"],
        "items": [
            {"url": "http://demo.example/%s/annc" % ts, "doc_type": "announcement",
             "title": "某市第三人民医院区域影像云平台采购公告(演示%s)" % ts,
             "org_name": "某市第三人民医院", "amount": "3600000.00",
             "publish_date": "2026-09-28", "region": "华东/某省某市", "category": "医疗信息化",
             "content_text": "拟建设区域影像云平台并接入基层医疗机构,预算 360 万元,含三年运维。"},
            {"url": "http://demo.example/%s/policy" % ts, "doc_type": "policy",
             "title": "某市医学影像数字化三年行动计划(演示%s)" % ts,
             "publish_date": "2026-03-05", "region": "华东/某省某市", "category": "医疗信息化",
             "content_text": "三年内实现区域内影像检查结果互认,基层医疗机构影像设备与云平台分批建设。"},
        ]}, internal=True)
    doc_annc, doc_policy = r["data"]["document_ids"]
    print("② 文档 accepted=%d:公告 %s / 政策 %s" % (r["data"]["accepted"], doc_annc, doc_policy))

    # ③ 信号:模拟信号发现服务回推(hits 引用种子规则 rule_seed01 v1,E2-6 的版本口径)
    r = call("POST", "/api/v1/internal/signals", {
        "document": {"id": doc_annc}, "score": 0.62, "detector": "demo-detector",
        "hits": [{"rule_id": "rule_seed01", "rule_version": 1, "rule_type": "amount_anomaly",
                  "weight": 0.8, "detail": {"z": 4.4, "multiplier": 3.9}}]}, internal=True)
    sig = r["data"]["signal_id"]
    print("③ 信号 %s score=0.62(未过 0.85 阈值,人工决定开调查)" % sig)

    # ④ 调查:analyst 手动开调查 → Agent 领取、留痕两步、引政策文档登记证据
    r = call("POST", "/api/v1/signals/%s/investigate" % sig, {}, token=analyst)
    inv = r["data"]["investigation_id"]
    call("POST", "/api/v1/internal/investigations/%s/start" % inv, internal=True)
    call("POST", "/api/v1/internal/investigations/%s/steps" % inv,
         {"event": "round_started", "round": 1, "payload": {"round": 1}}, internal=True)
    call("POST", "/api/v1/internal/investigations/%s/steps" % inv,
         {"event": "tool_completed", "round": 1, "token_usage": 2200,
          "payload": {"tool": "doc_search", "ok": True, "latency_ms": 950}}, internal=True)
    r = call("POST", "/api/v1/internal/investigations/%s/evidences" % inv,
             {"source_type": "policy", "doc_id": doc_policy,
              "excerpt": "三年行动计划:基层影像设备与云平台分批建设,本次采购为第二批"},
             internal=True)
    ev = r["data"]["id"]
    print("④ 调查 %s:两轮留痕 + 证据 %s(引本库政策文档)" % (inv, ev))

    # ⑤ 报告:ReportDraft(2 结论 2 建议 1 事件 2 主体 1 关系)→ complete → published + 信号 confirmed
    r = call("POST", "/api/v1/internal/investigations/%s/report" % inv, {
        "title": "某市三院区域影像云采购:三年行动计划的第二批落地(演示%s)" % ts,
        "summary": "非孤立采购:政策文件明确分批建设节奏,本期为第二批,建议跟踪第三批。",
        "claims": [
            {"text": "本次采购属于《医学影像数字化三年行动计划》分批建设的第二批",
             "nature": "fact", "confidence": 0.92, "evidence_ids": [ev]},
            {"text": "预算约为同类项目均值的 3.9 倍,与区域平台级建设定位相符",
             "nature": "inference", "confidence": 0.75}],
        "action_suggestions": [
            {"text": "评估以联合体形式参与区域平台级标段", "priority": "high"},
            {"text": "跟踪第三批(基层机构)立项窗口", "priority": "medium"}],
        "event_extraction": {
            "event": {"title": "某市医学影像数字化三年建设", "type": "construction_wave",
                      "start_date": "2026-03-05"},
            "entities": [
                {"name": "某市第三人民医院", "type": "org", "role": "participant"},
                {"name": "某市医学影像数字化三年行动计划", "type": "policy", "role": "scope"}],
            "relations": [{"source": "entity:0", "target": "event", "relation": "参与"}]}},
        internal=True)
    rep = r["data"]
    call("POST", "/api/v1/internal/investigations/%s/complete" % inv, internal=True)
    print("⑤ 报告 %s:meta=%s → complete(信号 confirmed、报告 published)"
          % (rep["id"], json.dumps(rep["meta"], ensure_ascii=False)))

    # ⑥ 反馈:analyst 采纳建议 + 写评语(E5-1);随后看板/图谱/规则一眼收口
    r = call("POST", "/api/v1/reports/%s/feedback" % rep["id"], {
        "adopted_suggestion_ids": [rep["action_suggestions"][0]["id"]],
        "comment": "结论与政策口径一致,已转商务评估联合体"}, token=analyst)
    print("⑥ 反馈 adopted=%s comment=%s flag=%s"
          % (r["data"]["newly_adopted"], r["data"]["comment_recorded"], r["data"]["flagged"]))

    print("—— 闭环验证 ——")
    r = call("GET", "/api/v1/dashboard/summary", token=analyst)["data"]
    print("看板: 文档 %d(+%d 今日) 信号 %d  调查 %d  报告 %d"
          % (r["documents"]["total"], r["documents"]["today"], r["signals"]["total"],
             r["investigations"]["total"], r["reports"]["total"]))
    print("      信号 by_status=%s" % json.dumps(r["signals"]["by_status"], ensure_ascii=False))
    r = call("GET", "/api/v1/events?page_size=5", token=analyst)["data"]
    evt = r["items"][0]
    g = call("GET", "/api/v1/events/%s/graph" % evt["id"], token=analyst)["data"]
    print("图谱: %s『%s』 节点 %d / 边 %d / 关联报告 %d"
          % (evt["id"], evt["title"], len(g["nodes"]), len(g["edges"]), len(g["reports"])))
    rules = call("GET", "/api/v1/admin/signal-rules", token=admin)["data"]
    print("规则: %d 个版本行,启用中 %d 条(hits 引用的 rule_seed01 v1 即出处于此)"
          % (len(rules), sum(1 for x in rules if x["enabled"])))
    print("回放完成。SSE 实时通知可另开: curl -N \"%s/api/v1/stream?token=<jwt>\"" % BASE)


if __name__ == "__main__":
    main()
