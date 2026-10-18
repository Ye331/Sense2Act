"""金标逐条核对定位表(诊断工具,对齐 _inspect.py / _render_brief.py 先例;只读)。

用法:
    python tests/_review_golden.py <staging_case_dir>

为案例目录下 golden.json 的每条 claim 打印:系统终态 / 应然终态 / 核对源与
实际值 / 系统判决依据。核对源=envelope.json、_detail_fields.json、
_detail_body.txt、runs/<event_id>/report.md 附录 B。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent


def _find_body_hit(body: str, needle: str, span: int = 46) -> str:
    """正文里找值,返回上下文片段(核对该值确在公告原文中)。"""
    i = body.find(needle)
    if i < 0:
        return ""
    lo = max(0, i - span // 2)
    return "…" + body[lo:i + len(needle) + span // 2].replace("\n", " ") + "…"


def pointers(text: str, env: dict, fields: dict, body: str,
             s1a: dict | None = None, audit_rel: str = "") -> list[str]:
    """按 claim 文本判定核对源(确定性关键词→字段映射,值实测填入)。"""
    t = env.get("tender") or {}
    s1a = s1a or {}
    audit_ptr = f"解读台账 {audit_rel} → decision_log 中 kind='其他' 的 s1a 条目"
    out: list[str] = []
    rules = [
        ("kind", f"envelope.kind = {env.get('kind')}"),
        ("正文未抓取", "envelope.tender.contentSourceNote = "
                       f"{(t.get('contentSourceNote') or '(空)')!r} | "
                       "projectSummary 内含「正文未抓取」字样"),
        ("项目编号", f"envelope.tender.projectId = {t.get('projectId')}"),
        ("采购方", f"envelope.tender.purchaser = {t.get('purchaser')}"),
        ("预算", f"envelope.tender.amountYuan = {t.get('amountYuan')} "
                 f"| _detail_fields.budget_yuan = {fields.get('budget_yuan')}"),
        ("发布日期", f"envelope.tender.publishedDate = {t.get('publishedDate')}"),
        ("项目名称", f"envelope.tender.projectName = {(t.get('projectName') or '')[:60]}…"),
        ("来源链接", f"envelope.tender.sourceUrl = {t.get('sourceUrl')} "
                     "(该 URL 在 collection_raw.json 中状态 200)"),
        ("来源 URL", f"envelope.tender.sourceUrl = {t.get('sourceUrl')} "
                     "(该 URL 在 collection_raw.json 中状态 200)"),
        ("投标截止", f"_detail_fields.deadline = {fields.get('deadline')}"),
        ("代理机构", f"_detail_fields.agency = {fields.get('agency')}"),
        ("获取招标文件", f"_detail_fields.signup_deadline = {fields.get('signup_deadline')}"),
        ("联系人", f"_detail_fields.contact = {fields.get('contact')} | phone = {fields.get('phone')}"),
        ("假设种子", f"{audit_ptr} → selectedSeedLabels = "
                     f"{s1a.get('selectedSeedLabels')}"),
        ("定性", f"{audit_ptr} → signalType = {s1a.get('signalType')!r};"
                 f"reason 首 60 字 = {(s1a.get('reason') or '')[:60]}…"),
        ("解读", f"{audit_ptr} → signalType = {s1a.get('signalType')!r}"),
        ("基缺口", f"{audit_ptr} → basisGaps = "
                   f"{[(g[:36] + '…') for g in (s1a.get('basisGaps') or [])]}"),
        ("历史", "envelope.tender.amountHistory/noveltyHistory/frequencyHistory "
                 f"= {t.get('amountHistory')}/{t.get('noveltyHistory')}/{t.get('frequencyHistory')}"),
        ("异常", f"envelope.tender.amountAnomaly/... = {t.get('amountAnomaly')}/"
                 f"{t.get('frequencyAnomaly')}/{t.get('noveltyAnomaly')}"),
    ]
    for kw, ptr in rules:
        if kw in text and ptr not in out:
            out.append(ptr)
    # 正文值核验(字段值应在公告正文中出现)
    for fname in ("projectId", "amountYuan", "deadline", "agency", "signup_deadline"):
        v = fields.get({"projectId": "project_no", "amountYuan": "budget_yuan"}.get(fname, fname))
        if v and str(v) in text and str(v) not in " ".join(out):
            hit = _find_body_hit(body, str(v))
            if hit:
                out.append(f"正文原文命中:{hit}")
    return out or ["(未识别核对源,请看 envelope.json 全文)"]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    d = Path(sys.argv[1])
    golden = json.loads((d / "golden.json").read_text(encoding="utf-8"))
    env = json.loads((d / "envelope.json").read_text(encoding="utf-8"))
    fields = json.loads((d / "_detail_fields.json").read_text(encoding="utf-8"))
    body = (d / "_detail_body.txt").read_text(encoding="utf-8")
    # 系统判决(报告附录 B)
    event_id = golden["event_id"]
    rpt = ROOT / "runs" / event_id / "report.md"
    verdict_lines: dict[str, str] = {}
    if rpt.exists():
        for line in rpt.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*(\S+)\s+.*?\|\s*(已证实|矛盾|待证实)\s*\|(.*)\|\s*$", line)
            if m:
                verdict_lines[m.group(1)] = f"系统判「{m.group(2)}」|{m.group(3)}"

    # 解读台账(S1a)实测值 + 真实路径
    audit = ROOT / "runs" / event_id / "audit.json"
    s1a = None
    if audit.exists():
        a = json.loads(audit.read_text(encoding="utf-8"))
        hits = [r["payload"]["s1a"] for r in a.get("decision_log", [])
                if isinstance(r.get("payload"), dict) and "s1a" in r["payload"]]
        s1a = hits[0] if hits else None
    audit_rel = f"runs/{event_id}/audit.json"

    lines = [f"# {golden['case_id']} 金标逐条核对定位表", "",
             f"核对源文件:envelope.json / _detail_fields.json / _detail_body.txt / "
             f"{audit_rel} / runs/{event_id}/report.md(附录 B)", ""]
    for c in golden["claims"]:
        did = c["draft_id"]
        rv = c.get("review", {})
        mark = "一致" if rv.get("agree_with_system") else "**改判**"
        lines.append(f"## [{did}] {c['golden_status']}({mark})")
        lines.append(f"- claim:{c['text']}")
        for p in pointers(c["text"], env, fields, body, s1a, audit_rel):
            lines.append(f"  - 核对源:{p}")
        if did in verdict_lines:
            lines.append(f"  - 系统判决:{verdict_lines[did]}")
        lines.append("")
    out = d / "golden_evidence_map.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {out}({len(golden['claims'])} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
