"""S7 报告文风线 · P 波定标探针(照 _pc_probe/_fusion_probe 先例)。

背景:P 波=prompt 口径根治(闸2)——S7_DRAFT_PROMPT 串讲化+背景节定标(P1)/
claim 输入侧剥壳直陈(P2)/待证实合并概括禁分号连排(P3)/结论节首段 80-150
字串讲(P4)/建议 rationale 三步归纳(P5)。机器闸在此做**定标仪器+测试锁同
源**(闸函数=s7_report._ban_scan/_narr_ok/_narr_extract/_bg_subject_ok/
_rationale_ok,不上渲染路径——live 节文靠 prompt 口径,渲染层 M1 机械转写
兜底,增强不阻塞=失败不伪装)。

探针读数(定标口径):
  ①干跑(默认,零 LLM 零费用):对既有台账 sections 逐节跑禁词扫=旧产物
    (改前 prompt 的真实产出)病根清单基线——各节禁词命中分布+背景节主语
    违例+结论节串讲提取演示;P 波改造效果用离线重渲染后的台账对照读。
  ②--llm 真模型跑:按生产同判据构造各节 context(M1 三档滤+claim_input_row
    剥壳/gist 附带=与 finalize/钩子五处同源),draft 各节+recommend,输出
    过机器闸→通过率/命中分布/逐条判读表;--rounds N 双轮对照(温度 0 仍非
    确定,看输出稳定性,照 _fusion_probe 先例)。
  ③--llm 同时=runtime 模型冒烟:DeepSeekLLM 消费 .env DEEPSEEK_MODEL
    (deepseek-v4-pro),调用成功=模型名合法实锤。

纯只读:主库走临时目录复制(原库零触碰);不写台账、不改金标、零渲染产物。

用法:
  PYTHONUTF8=1 python tests/_s7_probe.py --db data/rerecord/tender_xinzhou_college_r1.db --event evt_tender_xinzhou_college_r1
      默认干跑:旧台账病根清单基线+闸自校验,零 LLM。
  PYTHONUTF8=1 python tests/_s7_probe.py --db <主库...> --event <id> --llm [--rounds 2] [--sections 主体与事件背景,...]
      真模型跑(DEEPSEEK_API_KEY;温度 0)→P1-P5 定标读数→prompt 冻结。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT   # noqa: E402
from sense2act.chain_graph import claim_tier, load_present_rules   # noqa: E402
from sense2act.stages.s7_report import (   # noqa: E402
    RecommendationsResult, _ban_scan, _bg_subject_ok, _narr_extract,
    _narr_ok, _rationale_hints, _rationale_ok, claim_input_row)
from sense2act.store.db import connect   # noqa: E402
from sense2act.store.ledger import LedgerRepository   # noqa: E402

# 闸自校验样例(阈值边界;好例=过/坏例=命中类别)——零 LLM,先验证闸代码
# 与禁词表一致再定标(照 _fusion_probe 闸自校验先例)。
_GATE_SAMPLES = [
    # (闸, text, 期望命中/""=过)
    ("ban", "大连市公安局45232元采购公告已发布,投标截止2026-11-13。", []),
    ("ban", "信封载明:项目编号XX,预算金额若干。",
     ["信封", "信封载明", "信封载明:"]),   # 壳表同族多形态各自命中(如实列出)
    ("ban", "解读将本信号定性为商机,理由为检测器命中。",
     ["解读", "解读将本信号", "解读将本信号定性"]),
    ("ban", "amountHistory 显示近期频次异常。", ["amountHistory"]),
    ("ban", "依据 C3、C5 的记载,事实成立。", ["C3", "C5"]),
    ("ban", "(h1)主导假设成立,建议投标。", ["(hN)"]),
    ("ban", "轮次预算已用尽,证据通道已穷尽。", ["轮次"]),
    ("ban", "以下事项目前尚未核实:A;B;C。", ["以下事项目前尚未核实"]),
    ("narr", "", "空串讲"),
    ("narr", "太短", "串讲过短(2 字)"),
    ("narr", "字" * 80, ""),
    ("narr", "字" * 230, "串讲过长(230 字)"),
    ("bg", "信封显示本次采购预算若干。", "背景节以「信封」作主语起头"),
    ("bg", "本项目为大连市公安局服装采购,预算金额45232元。", ""),
    ("rat", "依据 c1、c2;(h3)主导,建议投标。", "rationale 含 claim id(c1)"),
    ("rat", "公告真实性已查证;当前处于投标准备窗口,建议评估参与。", ""),
    # 提取:旧台账模板产物(粗体主导行开头)=空串讲;带串讲普通段=提取;
    # 旧 prompt 状态说明段(无研判词)=不提取(忻州 r1 重渲染实抓的误提取
    # 实例——形态过窗+零禁词,靠研判词特征第四道守门拒收)
    ("extract", "### 结论\n\n**主导假设:H1**(得分 3)\n\n- 支撑:x", ""),
    ("extract", "### 结论\n\n关于本公司能否在截止前完成投标文件编制与递交,"
               "目前尚未核实。现有证据尚不足以确认本公司具备相应资质与资源"
               "条件,该断言仍处于待证实状态,本节不作事实认定。\n\n- x", ""),
    ("extract", "### 结论\n\n当前证据结构更支持「正常采购」:三处独立公开来源"
               "分别印证了公告真实性与采购方同类记录,而「异常输送」所需的"
               "资金流向证据尚未查实,两组假设共享同一批公告事实。\n\n"
               "- 支撑结构:x",
     "当前证据结构更支持「正常采购」:三处独立公开来源分别印证了公告真实性"
     "与采购方同类记录,而「异常输送」所需的资金流向证据尚未查实,两组假设"
     "共享同一批公告事实。"),
]


def _open_ledger(db_path: Path, tmp: Path) -> LedgerRepository:
    """台账零侵入:复制(含 -wal/-shm)到临时目录再打开(照 _render_report)。"""
    local = tmp / db_path.name
    shutil.copy2(db_path, local)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, tmp / side.name)
    conn = connect(str(local))
    return LedgerRepository(conn, ROOT / "data" / "config")


def _refs_of(led: LedgerRepository, claim_id: str, topk: int = 5) -> list[str]:
    """证据引用优先级(生产 _refs 同判据:已裁定优先)。"""
    rows = led.evidence_for_claim(claim_id)
    strong = [e for e in rows if e["ruling"] in ("支持", "矛盾", "时效刷新")]
    weak = [e for e in rows if e["ruling"] is None or e["ruling"] == "无关"]
    return [e["evidence_id"].rsplit(":", 1)[-1] for e in (strong + weak)[:topk]]


def _dry_run(led: LedgerRepository, event_id: str, rules: dict) -> int:
    """干跑:旧台账(改前 prompt 的真实产物)病根清单基线+闸自校验,零 LLM。"""
    ok = True
    print("=== 闸自校验样例 ===")
    for gate, text, want in _GATE_SAMPLES:
        if gate == "ban":
            got = _ban_scan(text, rules)
        elif gate == "narr":
            got = _narr_ok(text)
        elif gate == "bg":
            got = _bg_subject_ok(text)
        elif gate == "rat":
            got = _rationale_ok(text)
        else:
            got = _narr_extract(text, rules)
        mark = "OK" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"- [{mark}] {gate}: 期望={want!r} 实得={got!r} | {text[:26]}")
    if not ok:
        print("[探针] 闸自校验未过——闸代码与禁词表不符,先修再定标")
        return 1

    print("=== 旧台账病根清单基线(逐节禁词扫) ===")
    total_hits: dict[str, int] = {}
    bad_secs = 0
    for sec in led.get_sections(event_id):
        text = sec["draft_text"] or ""
        if not text.strip():
            continue
        hits = _ban_scan(text, rules)
        for h in hits:
            total_hits[h] = total_hits.get(h, 0) + 1
        bg = _bg_subject_ok(text) if sec["kind"] == "standard" and \
            (sec["ord"] or 0) == 0 else ""
        narr = _narr_extract(text, rules) if sec["kind"] == "conclusion" else ""
        if hits or bg:
            bad_secs += 1
            print(f"- [{sec['title']}] 禁词 {len(hits)} 命中"
                  f"{'; 主语违例:' + bg if bg else ''}")
            for w in sorted(set(hits)):
                print(f"    {w} ×{hits.count(w)}")
        if sec["kind"] == "conclusion":
            print(f"  [结论节] 串讲提取={'在(' + str(len(narr)) + '字)' if narr else '无(纯卡)'}")
    n_secs = len([s for s in led.get_sections(event_id)
                  if (s["draft_text"] or "").strip()])
    print(f"=== 汇总:{bad_secs}/{n_secs} 节带命中;词频 "
          f"{dict(sorted(total_hits.items(), key=lambda kv: -kv[1]))} ===")
    print("[探针] 干跑结束(--llm 真模型定标;KEY 需在 .env)")
    return 0


def _build_context(led: LedgerRepository, event_id: str, sec, rules: dict,
                   gists: dict) -> dict:
    """起草输入(生产 finalize 环①/钩子 _context 同判据:M1 三档滤+
    claim_input_row 剥壳/gist 附带)。"""
    claims = led.claims_for_section(sec["section_id"])
    payload = []
    for c in claims:
        if claim_tier(c["text"] or "", rules):
            continue
        payload.append(claim_input_row(c, _refs_of(led, c["claim_id"]),
                                       rules, gists))
    kind = "conclusion" if sec["ord"] == 3 else sec["kind"]
    return {"event_id": event_id, "title": sec["title"], "kind": kind,
            "claims": payload, "standings": led.hypothesis_standings(event_id),
            "envelope_label": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description="S7 报告文风线 P 波定标探针")
    ap.add_argument("--db", nargs="+", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--llm", action="store_true", help="真模型跑(需 KEY)")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--sections", default="",
                    help="只跑指定节(逗号分标题含匹配;缺省=全部标准节+结论)")
    args = ap.parse_args()

    rules = load_present_rules(ROOT / "data" / "config")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        led = None
        for src in args.db:
            p = Path(src)
            if p.exists() and led is None:
                led = _open_ledger(p, tmp)
        if led is None:
            print(f"[探针] 无可用 db: {args.db}")
            return 2
        try:
            if not args.llm:
                return _dry_run(led, args.event, rules)

            from sense2act.llm import DeepSeekLLM
            from sense2act.stages.d_hooks import DraftOutput
            from sense2act.prompts import (S7_DRAFT_PROMPT,
                                           S7_RECOMMEND_PROMPT)
            llm = DeepSeekLLM()   # 消费 .env DEEPSEEK_MODEL=runtime 冒烟位
            from sense2act.llm import call_structured

            gists = led.claim_gists(args.event)
            secs = [s for s in led.get_sections(args.event)
                    if (s["draft_text"] or "").strip()
                    or s["kind"] == "conclusion"]
            if args.sections:
                keys = [k.strip() for k in args.sections.split(",")]
                secs = [s for s in secs
                        if any(k in (s["title"] or "") for k in keys)]
            rounds_out: list[list[dict]] = []
            for rd in range(max(args.rounds, 1)):
                rows: list[dict] = []
                for sec in secs:
                    ctx = _build_context(led, args.event, sec, rules, gists)
                    if sec["kind"] == "standard" and sec["ord"] == 5:
                        continue                     # 建议节走 recommend 槽
                    out = call_structured(
                        llm, stage="s7_draft", system=S7_DRAFT_PROMPT,
                        user=json.dumps(ctx, ensure_ascii=False),
                        model_cls=DraftOutput, retry=1)
                    text = out.markdown
                    hits = _ban_scan(text, rules)
                    bg = _bg_subject_ok(text) if sec["kind"] == "standard" \
                        and (sec["ord"] or 0) == 0 else ""
                    narr = _narr_extract(text, rules) \
                        if sec["kind"] == "conclusion" else ""
                    narr_bad = _narr_ok(narr) if narr else (
                        "缺串讲" if sec["kind"] == "conclusion" else "")
                    rows.append({
                        "sec": sec["title"], "text": text,
                        "hits": hits, "bg": bg,
                        "narr": narr, "narr_bad": narr_bad,
                        "gate": "PASS" if not (hits or bg or narr_bad)
                        else "FAIL"})
                # 建议槽(P5 rationale 闸+句式形似度)
                rec_claims = [claim_input_row(r, [], rules, gists)
                              for r in led.all_claims(args.event)]
                rec_ctx = {"event": "", "standings":
                           led.hypothesis_standings(args.event),
                           "claims": rec_claims, "watch_focus": []}
                rout = call_structured(
                    llm, stage="s7_recommend", system=S7_RECOMMEND_PROMPT,
                    user=json.dumps(rec_ctx, ensure_ascii=False),
                    model_cls=RecommendationsResult, retry=1)
                for it in rout.items:
                    rat_bad = _rationale_ok(it.rationale)
                    rows.append({
                        "sec": f"[建议] {it.action[:20]}", "text":
                        f"rationale: {it.rationale}", "hits": [], "bg": "",
                        "narr": "", "narr_bad": "",
                        "gate": "PASS" if not rat_bad else f"FAIL[{rat_bad}]",
                        "hints": _rationale_hints(it.rationale)})
                rounds_out.append(rows)
                print(f"=== 第 {rd + 1} 轮:槽位 {len(rows)} ===")

            # 读数汇总
            n_pass = sum(1 for rows in rounds_out for r in rows
                         if r["gate"] == "PASS")
            n_all = sum(len(r) for r in rounds_out)
            print(f"=== 机器闸通过率:{n_pass}/{n_all} ===")
            for rows in rounds_out:
                for r in rows:
                    tag = r["gate"]
                    extra = (f"; 句式形似 {r.get('hints', 0)}/6"
                             if "hints" in r else
                             (f"; 串讲 {len(r['narr'])} 字" if r["narr"] else ""))
                    print(f"- [{tag}] {r['sec']}"
                          f"{': ' + '、'.join(sorted(set(r['hits']))) if r['hits'] else ''}"
                          f"{'; ' + r['bg'] if r['bg'] else ''}"
                          f"{'; ' + r['narr_bad'] if r['narr_bad'] else ''}"
                          f"{extra}")
            if len(rounds_out) >= 2:
                flip = 0
                for i in range(min(len(r) for r in rounds_out)):
                    a = rounds_out[0][i]["gate"] == "PASS"
                    b = rounds_out[1][i]["gate"] == "PASS"
                    if a != b:
                        flip += 1
                print(f"=== 两轮通过性翻转:{flip} 槽 ===")
            print("=== 逐条判读表(防「过了闸但串讲失真」假阴性) ===")
            for rd, rows in enumerate(rounds_out):
                for r in rows:
                    print(f"--- R{rd + 1} {r['sec']} ---")
                    print(r["text"])
            return 0
        finally:
            if led is not None:
                led.conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
