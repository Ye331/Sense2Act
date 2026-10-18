"""全链路演示:文档入库 → 信号检测 → 自动立项 → Agent 调查 → 报告。

    python scripts/demo.py                       # 默认连 http://127.0.0.1:8080
    python scripts/demo.py --threshold 0.5       # 自动立项阈值(演示调低,真实环境按策略)

以爬虫的身份经 /internal/documents 推送「XX市第一人民医院」的 4 条历史公告 + 1 条大额 AI 招标,
随后只观察:检测、立项、调查、报告都由各服务自己完成。重复运行幂等(文档按 URL/内容去重)。
只用标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORG = "XX市第一人民医院"
TARGET_URL = "https://ccgp.example.gov.cn/hosp1/medai-2026-001.htm"
DOCS = [
    ("2024-11-12", "XX市第一人民医院门诊信息系统运维服务采购项目", "650000.00", "hosp1/opd-ops-2024.htm",
     "门诊信息系统年度运维服务,含挂号、收费、医生工作站模块的日常维护。"),
    ("2025-03-18", "XX市第一人民医院医学影像存储与传输系统(PACS)升级项目", "1200000.00", "hosp1/pacs-2025.htm",
     "PACS 存储扩容与影像调阅性能升级,对接院内 HIS/RIS。"),
    ("2025-08-26", "XX市第一人民医院网络安全等级保护建设项目", "900000.00", "hosp1/mlps-2025.htm",
     "按等保 2.0 三级要求开展网络安全建设与测评整改。"),
    ("2026-02-10", "XX市第一人民医院护理信息系统扩容采购项目", "780000.00", "hosp1/nis-2026.htm",
     "护理信息系统病区扩容及移动护理终端采购。"),
    ("2026-09-20", "XX市第一人民医院AI辅助诊断系统采购项目", "4800000.00", "hosp1/medai-2026-001.htm",
     "XX市第一人民医院公开招标采购AI辅助诊断系统,预算480万元,含影像辅助诊断与临床决策支持模块,"
     "要求与院内PACS、电子病历系统对接,项目编号 MEDAI-2026-001。"),
]


class Api:
    def __init__(self, base: str, internal_key: str):
        self.base, self.key, self.token = base.rstrip("/") + "/api/v1", internal_key, None

    def call(self, method: str, path: str, body=None, internal=False):
        headers = {"Content-Type": "application/json"}
        if internal:
            headers["X-Internal-Key"] = self.key
        elif self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        url = self.base + urllib.parse.quote(path, safe="/?=&")
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read() or b"{}")
        if payload.get("code") != 0:
            raise RuntimeError(f"{method} {path} -> [{payload.get('code')}] {payload.get('message')}")
        return payload["data"]


def wait_for(description: str, probe, timeout: float, interval: float = 3):
    print(f"… {description}", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = probe()
        except (OSError, RuntimeError):
            value = None
        if value:
            return value
        time.sleep(interval)
    raise SystemExit(f"超时:{description}(检查对应服务日志:docker compose logs <service>)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("BASE", "http://127.0.0.1:8080"))
    parser.add_argument("--internal-key", default=os.getenv("INTERNAL_API_KEY", "local-dev-internal-key"))
    parser.add_argument("--admin-password", default=os.getenv("SEED_ADMIN_PASSWORD", "admin123"))
    parser.add_argument("--threshold", type=float, default=0.5, help="自动立项阈值")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    api = Api(args.base, args.internal_key)

    wait_for("等待后端就绪", lambda: urllib.request.urlopen(args.base + "/actuator/health", timeout=5).status == 200, 180)
    api.token = api.call("POST", "/auth/token", {"email": "admin@sense2act.local", "password": args.admin_password})["token"]

    policy = api.call("GET", "/admin/investigation-policies")
    policy.pop("updated_at", None)
    policy["auto_investigate_threshold"] = args.threshold
    api.call("PUT", "/admin/investigation-policies", policy)
    if not any(p["name"] == "医疗AI辅助诊断" for p in api.call("GET", "/admin/watch-profiles")):
        api.call("POST", "/admin/watch-profiles", {"name": "医疗AI辅助诊断", "note": "影像 AI、临床决策支持"})
    print(f"✓ 管理配置:自动立项阈值 {args.threshold},关注画像「医疗AI辅助诊断」")

    sources = api.call("GET", "/sources?page_size=100")["items"]
    source = next((s for s in sources if s["name"] == "演示-医院招标公告"), None) or api.call("POST", "/sources", {
        "name": "演示-医院招标公告", "type": "web_page", "url": "https://ccgp.example.gov.cn/hosp1/",
        "adapter": "demo", "enabled": True})
    items = [{"url": "https://ccgp.example.gov.cn/" + path, "doc_type": "announcement", "title": title, "org_name": ORG,
              "amount": amount, "publish_date": day, "region": "华东/某省XX市", "category": "医疗信息化", "content_text": text,
              "raw_html": f"<html><head><meta charset='utf-8'><title>{title}</title></head><body><h1>{title}</h1>"
                          f"<p>采购人:{ORG}</p><p>预算金额:{amount} 元</p><p>发布日期:{day}</p><p>{text}</p></body></html>"}
             for day, title, amount, path, text in DOCS]
    pushed = api.call("POST", "/internal/documents", {"source_id": source["id"], "items": items}, internal=True)
    api.call("POST", "/internal/ingest-runs", {"source_id": source["id"], "ok": True, "stats": {
        "fetched": len(items), "accepted": pushed["accepted"], "duplicates": pushed["duplicates"], "failed": pushed["failed"]},
        "errors": []}, internal=True)
    print(f"✓ 文档入库:新增 {pushed['accepted']} 条,重复 {pushed['duplicates']} 条")

    target = next(d for d in api.call("GET", f"/documents?keyword=AI辅助诊断系统&page_size=20")["items"] if d["url"] == TARGET_URL)

    def signal():
        rows = api.call("GET", f"/signals?org_id={target['org']['id']}&page_size=50")["items"]
        return next((s for s in rows if s["document"]["id"] == target["id"]), None)
    found = wait_for("等待 signal-worker 检测(首次加载语义模型需十几秒)", signal, 300)
    rules = "、".join(h["rule_type"] for h in found["hits"])
    print(f"✓ 信号 {found['id']}:评分 {found['score']},命中 {rules},状态 {found['status']}")
    if not found["investigation_id"]:
        print(f"  评分未达阈值 {args.threshold},信号待人工研判:可在前端「商机信号」页发起调查。")
        return

    inv_id = found["investigation_id"]
    seen = set()

    def finished():
        detail = api.call("GET", f"/investigations/{inv_id}")["investigation"]
        if detail["status"] != "created" and "started" not in seen:
            seen.add("started")
            print(f"✓ Agent 已领取调查 {inv_id}")
        return detail if detail["status"] in ("completed", "failed", "stopped") else None
    result = wait_for(f"等待 Agent 完成调查(前端 http://127.0.0.1:5173/#/tasks/{inv_id} 可实时观看)", finished, 900)
    steps = api.call("GET", f"/investigations/{inv_id}/steps?limit=200")["items"]
    print(f"✓ 调查 {result['status']}:{result['current_round']} 轮,{len(steps)} 条留痕,Token {result['token_used']}")
    if result["status"] != "completed":
        raise SystemExit(f"调查未完成:{result.get('error')}")
    report = api.call("GET", "/reports/" + api.call("GET", f"/reports?investigation_id={inv_id}")["items"][0]["id"])
    print(f"✓ 报告《{report['title']}》({report['status']})")
    for claim in report["claims"]:
        print(f"   [{claim['nature']}] {claim['text']}  ← {len(claim['evidences'])} 条证据")
    print(f"\n前端查看:http://127.0.0.1:5173/#/reports/{inv_id}")


if __name__ == "__main__":
    main()
