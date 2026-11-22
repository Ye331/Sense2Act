"""Delta report 数据层(方案二,2026-10-22):跨运行时点 diff 纯函数。

零 LLM、零写台账:以"上次结算 ts"为锚,从 claim_transitions 时点重算各
claim 的锚时状态,与当前态比对,产出修正/新增/不变三段+假设触及清单。
与 ESS 的 as_of 时点重算同一公理(P5 可重放)——修正的根基不是"记得上次",
是账本本来就带历史;快照文件因此不需要(transitions 自带 old/new/cause/
evidence_id/ts 全历史)。

时点锚用 ts 不用 round:round 每 run 从 0 重来(investigation 主循环),
跨 run 交叠不可比;ts 精确到秒且单调,续查注回原事件(同 db)下唯一。
round 全局续接是另一独立议题(P2 配套项,预算语义需单独拍板),本模块
不依赖它。

呈现层挂载(finalize 尾部+报告头部)归后续批次;本模块只供数据与锚点
旁车读写原语,不 import 渲染、不 import credibility。
"""
from __future__ import annotations

import json
from pathlib import Path

ANCHOR_FILE = "last_finalize.json"


def _severity(old_status: str, new_status: str) -> int:
    """修正条严重度序(呈现排序用):矛盾(翻转)>回退>翻正>其他。

    与 claim_transitions.cause 三分类互补:cause 是成因归因,严重度是
    呈现排序——翻转(任意态->矛盾)最紧急;已证实->待证实(回退)次之
    (此前作为依据的结论不再成立);->已证实(翻正/澄清)再次。
    """
    if new_status == "矛盾":
        return 0
    if old_status == "已证实" and new_status == "待证实":
        return 1
    if new_status == "已证实":
        return 2
    return 3


def compute_delta(ledger, event_id: str, anchor_ts: str) -> dict:
    """时点 diff(纯读):anchor_ts 之后台账发生了什么。

    修正条=锚时态(锚前最后一条迁移的 new_status;无迁移即出生态
    "待证实")与当前态的净差异——锚后折腾又回原态的 claim 计入不受影响
    (净口径);新增条=created_at 晚于锚(P2 起写入,含派生条,带
    derived_from 谱系)。每条修正带最后一条触发迁移的原因四元组
    (cause/evidence_id/round/ts)+证据标量(谱系根/平台/发布时间)。
    """
    claims = [dict(r) for r in ledger.all_claims(event_id)]
    trans = sorted(
        (dict(r) for r in ledger.transitions(event_id)),
        key=lambda t: (t["ts"], t["id"]))
    pre: dict[str, dict] = {}          # claim_id -> 锚前最后一条迁移
    post: dict[str, list[dict]] = {}   # claim_id -> 锚后迁移(时间序)
    for t in trans:
        if t["ts"] <= anchor_ts:
            pre[t["claim_id"]] = t
        else:
            post.setdefault(t["claim_id"], []).append(t)

    modified: list[dict] = []
    added: list[dict] = []
    for c in claims:
        if c["created_at"] and c["created_at"] > anchor_ts:
            added.append({
                "claim_id": c["claim_id"], "draft_id": c["draft_id"],
                "text": c["text"], "status": c["status"],
                "derived_from": c["parent_claim_id"] or "",
            })
            continue
        at_anchor = (pre[c["claim_id"]]["new_status"]
                     if c["claim_id"] in pre else "待证实")
        if at_anchor == c["status"]:
            continue
        last = post[c["claim_id"]][-1]
        m = {
            "claim_id": c["claim_id"], "draft_id": c["draft_id"],
            "text": c["text"], "old_status": at_anchor,
            "new_status": c["status"],
            "cause": last["cause"], "evidence_id": last["evidence_id"],
            "round": last["round"], "ts": last["ts"],
            "n_transitions": len(post[c["claim_id"]]),
        }
        if last["evidence_id"]:
            ev = ledger.conn.execute(
                "SELECT lineage_root, platform, published_at FROM evidence"
                " WHERE evidence_id=?", (last["evidence_id"],)).fetchone()
            m["evidence"] = dict(ev) if ev else None
        else:
            m["evidence"] = None
        modified.append(m)
    modified.sort(key=lambda m: (_severity(m["old_status"], m["new_status"]),
                                 m["claim_id"]))
    added.sort(key=lambda a: a["claim_id"])

    touched = sorted({m["claim_id"] for m in modified}
                     | {a["claim_id"] for a in added})
    hyp_touched: dict[str, list[str]] = {}
    for cid in touched:
        for r in ledger.conn.execute(
                "SELECT hypothesis_id FROM hypothesis_claims WHERE claim_id=?",
                (cid,)):
            hyp_touched.setdefault(r["hypothesis_id"], []).append(cid)

    return {
        "event_id": event_id,
        "anchor_ts": anchor_ts,
        "total": len(claims),
        "modified": modified,
        "added": added,
        "unchanged_count": len(claims) - len(modified) - len(added),
        "hypotheses_touched": hyp_touched,
    }


def read_anchor(out_dir: str | Path) -> str | None:
    """读上次结算锚(runs/<event>/last_finalize.json);缺失=None(首次运行)。"""
    p = Path(out_dir) / ANCHOR_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ts = data.get("last_finalize_ts")
    return ts if isinstance(ts, str) and ts else None


def write_anchor(out_dir: str | Path, ts: str) -> None:
    """写结算锚(finalize 时调用;呈现层批次接线,本模块只供原语)。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ANCHOR_FILE).write_text(
        json.dumps({"last_finalize_ts": ts}, ensure_ascii=False, indent=2),
        encoding="utf-8")
