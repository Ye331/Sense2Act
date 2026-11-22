# -*- coding: utf-8 -*-
"""报告与 chain_web 共用的结论展示投影。

输入只是当前 graph、claim 快照和假设成员表；本模块不读 Markdown、
不访问网络、不写台账，也不重算 chosen/runner-up。它只把已有
三态和结构化推理桥投影为“当前判断/依据/缺口/改变条件/
下一步”，避免报告与网页各自拼出两套结论。
"""
from __future__ import annotations

from typing import Iterable


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _next_step(text: str, gap: object) -> str:
    """把待证 claim 翻译成可执行核查项；匹配不到时只消费
    gap.next，绝不自造“已支持”之类事实。"""
    if "投标业绩" in text:
        return "核查同类项目投标业绩"
    if "经营范围" in text or "品类" in text or "标的物资" in text:
        return "核对标的物资详细清单与本公司经营范围，确认品类匹配"
    if "资质" in text or "交付资源" in text or "投标文件" in text:
        return "核查投标资质与交付资源，确认能否按截止时间完成投标"
    if "处罚" in text or "投诉" in text or "违规" in text:
        return f"通过独立合规记录核验：{text}"
    if isinstance(gap, dict):
        nxt = gap.get("next")
        if isinstance(nxt, str) and nxt.strip():
            return f"{nxt.strip()}：{text}"
    return f"补证：{text}"


def _summary(label: str, chosen: bool, support_n: int, pending_n: int,
             contra_n: int, targeted: list[dict]) -> str:
    if chosen and targeted:
        text = str(targeted[0].get("inference_text") or "").strip()
        if "可投标" in label or "商机" in label:
            # 配置桥已给出证据与缺口；这里只把行动边界说清，
            # 不从旧 report.md 拷贝自然语言。
            text = text.replace("项目与截止时间", "公开招标与截止时间")
            text = text.replace("业务匹配", "品类匹配")
            marker = "因此目前只能得出条件性的潜在机会。"
            if marker in text:
                text = text.replace(marker, "目前只能视为潜在机会，不能表述为可执行商机。")
        if text:
            return text
    role = "当前主结论" if chosen else "当前次选假设"
    tail = f"，并有 {contra_n} 项矛盾事实" if contra_n else ""
    return (f"{label}为{role}：{support_n} 项事实已证实，"
            f"{pending_n} 项仍待补证{tail}。")


def build_conclusion_views(
        graph: dict,
        claims: list[dict],
        hypotheses: list[dict],
        *,
        excluded_claim_ids: Iterable[str] = (),
        source_section_id: str,
        snapshot_version: str,
) -> list[dict]:
    """构建确定性结论视图。

    hypotheses 每项需含 hypothesis_id/label/description/state/score/
    chosen/member_ids。未知字段忽略；返回顺序与输入一致。
    """
    claim_by = {c.get("claim_id"): c for c in claims if c.get("claim_id")}
    excluded = set(excluded_claim_ids)
    inferences = [i for i in (graph.get("inferences") or [])
                  if i.get("fingerprint_verified", True)]

    prepared: list[dict] = []
    for hyp in hypotheses:
        hid = hyp["hypothesis_id"]
        targeted = [i for i in inferences
                    if hid in (i.get("target_hypothesis_ids") or [])]
        member_ids = _unique(hyp.get("member_ids") or [])
        basis_ids = _unique(cid for i in targeted
                            for cid in (i.get("basis_claim_ids") or []))
        relevant_ids = _unique([*member_ids, *basis_ids])
        factual_ids = [cid for cid in relevant_ids
                       if cid in claim_by and cid not in excluded]
        supporting = [cid for cid in factual_ids
                      if claim_by[cid].get("status") == "已证实"]
        pending = [cid for cid in factual_ids
                   if claim_by[cid].get("status") == "待证实"]
        contradicted = [cid for cid in factual_ids
                        if claim_by[cid].get("status") == "矛盾"]
        chosen = bool(hyp.get("chosen"))
        label = str(hyp.get("label") or hid)
        steps = [_next_step(str(claim_by[cid].get("text") or
                                claim_by[cid].get("label") or cid),
                            claim_by[cid].get("gap")) for cid in pending]
        if chosen and ("可投标" in label or "商机" in label):
            steps.insert(0, "核查同类项目投标业绩")
        conditions = _unique(str(i.get("condition_or_gap") or "").strip()
                             for i in targeted)
        if supporting:
            conditions.append("若当前支撑材料被更正、撤销或证实失实，相关判断应回落。")
        prepared.append({
            "hypothesis_id": hid,
            "label": label,
            "description": str(hyp.get("description") or ""),
            "state": str(hyp.get("state") or ""),
            "chosen": chosen,
            "score": hyp.get("score", 0),
            "current_judgment": _summary(
                label, chosen, len(supporting), len(pending),
                len(contradicted), targeted),
            "supporting_claim_ids": supporting,
            "pending_claim_ids": pending,
            "contradicted_claim_ids": contradicted,
            "change_conditions": _unique(conditions),
            "next_steps": _unique(steps),
            "source_section_id": source_section_id,
            "snapshot_version": snapshot_version,
        })

    for view in prepared:
        alternatives = []
        for other in prepared:
            if other["hypothesis_id"] == view["hypothesis_id"]:
                continue
            support_n = len(other["supporting_claim_ids"])
            pending_n = len(other["pending_claim_ids"])
            reason = (f"已证实 {support_n} 项，仍有 {pending_n} 项待补证，"
                      f"当前状态为{other['state'] or '未定'}。")
            alternatives.append({
                "hypothesis_id": other["hypothesis_id"],
                "label": other["label"],
                "state": other["state"],
                "reason": reason,
            })
        view["alternatives"] = alternatives
    return prepared
