"""报告结论研判说明 · I2 定标探针(20261109 施工单 I2-0;照 _s7_probe 先例)。

背景:用户命题「结论过于机械」(2026-11-08)→ 11-09 三轮拍板=「判据×本案
具体化」定为 I2 职责——独立研判释义行(种子表 description 直出)已撤回
(description 是常量,每案同句,说破出来仍机械)。实现层=分权终点形态:
素材代码算(I2-1 verdict_brief)→措辞 LLM 写(I2-2 prompt 结论节扩展)→
机器验票(I2-3 五闸+段落形态,拒收粒度=单句撤/整段回落零占位)。
载体=串讲段吸收(P 波结论节串讲,live 产物;不新增独立段)。

探针读数(定标口径):
  ①干跑(默认,零 LLM 零费用):
    a) 素材包自校验:verdict_brief 现算逐字段断言(忻州 r2 实锚=标签剥
       括号/判据字段/反向状态/落选一句话在;t1 键缺席照 amount_history
       null 实账);
    b) 旧台账基线:改前 prompt 产物逐节扫——结论节串讲提取+合格判据
       (判据术语原文整句引用)命中=0 预期(旧产物无研判说明);
    c) 五闸自校验样例(好例零命中/坏例分类命中,零 LLM 先验证闸代码)。
  ②--llm 真模型跑:生产同判据构造结论节 context(M1 三档滤+claim_input_
    row+verdict_brief=与 finalize 环①/轮间钩子同源),draft 结论节→提取
    →五闸验票→合格判据读数;--rounds N 双轮对照(照 _fusion_probe 先例)。
  ③合格判据(拍板 5):首段必须出现判据的具体化(数值对齐/量级判断),
    禁引用判据术语原文——机器代理=判据原文长片段(≥12 字)出现在串讲=
    FAIL;具体化软指标词命中数=人工目检读数(措辞开放,宁窄勿宽)。

纯只读:主库走临时目录复制(原库零触碰);不写台账、不改金标、零渲染产物。

用法:
  PYTHONUTF8=1 python tests/_insight_probe.py
      默认干跑:忻州 r1-r3 素材包自校验+旧台账基线+五闸自校验,零 LLM。
  PYTHONUTF8=1 python tests/_insight_probe.py --db <主库> --event <id> --llm [--rounds 2]
      真模型跑(DEEPSEEK_API_KEY;温度 0)→研判说明定标读数→阈值写回
      施工单 §九实录。
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
    _narr_extract, _norm_num, _numeric_bad, _anchor_bad, _quoted_unknown,
    _upgrade_missing, _verdict_form_bad, claim_input_row, verdict_brief,
    verdict_gate_material, verdict_gates_apply)
from sense2act.store.db import connect   # noqa: E402
from sense2act.store.ledger import LedgerRepository   # noqa: E402

# 默认靶=bench 忻州职院 r1-r3(素材包自校验三形态:站住/打折/窗口)
_DEFAULT_DBS = [str(ROOT / "data" / "rerecord" / f"tender_xinzhou_college_r{i}.db")
                for i in (1, 2, 3)]

# 五闸自校验样例(好例=过/坏例=命中;零 LLM 先验证闸代码再定标,照
# _s7_probe/_fusion_probe 闸自校验先例)。
_GATE_SAMPLES = [
    # (闸, 期望, 位置参数…)
    ("anchor", [], "公告已发布[E1],投标截止在即。", {"E1", "1"}),
    ("anchor", ["[99]"], "公示名单见[99]。", {"E1", "1"}),
    ("numeric", [], "这笔预算 99.99 万,与该院约 100 万的量级一致。",
     {"99.99", "100"}),
    ("numeric", ["12345"], "涉及金额 12345 元。", {"99.99"}),
    ("upgrade", "", "资质匹配尚未查实,暂不能排除。", True),
    ("upgrade", "待证实未带口径(禁升格)", "资质匹配已确认无误。", True),
    ("upgrade", "", "资质匹配已确认无误。", False),   # 无待证实素材=闸不触发
    ("form", "", "这笔预算 99.99 万,与该院近两年约 100 万的采购量级一致,"
                "属常规大额采购。",),
    ("form", "分号连排罗列", "标的匹配尚未查实;资质不符尚未查实;业绩空白"
                            "尚未查实。",),
    ("form", "分号连排罗列", "标的匹配尚未查实；资质不符尚未查实；"
                            "业绩空白尚未查实。",),   # 全角分号(码点明写)
    ("form", "列表形态(段落混入结构行)", "研判如下。\n- 甲已查证\n- 乙未查实",),
    ("form", "序号堆叠", "研判如下。\n1. 甲\n2. 乙",),
    ("quoted", [], "研判更支持「常规采购」,量级一致。", ["常规采购", "本案预算"]),
    ("quoted", ["不存在实体"], "证据指向「不存在实体」。", ["常规采购"]),
]

# 忻州 r2 素材包实锚断言(11-08/09 I1 实账:领先=正常大额采购(真实商机),
# 反向=c12 排斥条款挂竞争假设;amount_history null=t1 键缺席)
_R2_EXPECT = {
    "label": "正常大额采购",
    "criteria_has": "金额",
    "t1_absent": True,
    "contra_not_none": True,
    "runner_present": True,
}


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


def _event_of(db_path: Path) -> str:
    """db 文件名→事件 id 形态(tender_xinzhou_college_r1→evt_ 前缀;显式
    --event 覆盖)。"""
    return "evt_" + db_path.stem


def _criteria_quoted(narr: str, criteria: str, min_len: int = 12) -> str:
    """合格判据机器代理(拍板 5):判据术语原文长片段出现在串讲=术语复读
    (不合格);返回命中片段(空=过)。具体化=判据词被本案数值/事实改写,
    措辞开放交人工目检(宁窄勿宽:短片段/单词重合不算)。"""
    c = (criteria or "").strip()
    if len(c) < min_len:
        return ""
    for i in range(0, len(c) - min_len + 1):
        seg = c[i:i + min_len]
        if seg in (narr or ""):
            return seg
    return ""


def _gate_selfcheck(rules: dict) -> int:
    """五闸自校验(零 LLM):好例零命中/坏例按类命中。"""
    ok = True
    print("=== 五闸自校验样例 ===")
    for sample in _GATE_SAMPLES:
        gate, want = sample[0], sample[1]
        if gate == "anchor":
            got = _anchor_bad(sample[2], sample[3])
        elif gate == "numeric":
            got = _numeric_bad(sample[2], {_norm_num(x) for x in sample[3]})
        elif gate == "upgrade":
            got = _upgrade_missing(sample[2], sample[3])
        elif gate == "form":
            got = _verdict_form_bad(sample[2])
        else:
            got = _quoted_unknown(sample[2], sample[3])
        mark = "OK" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"- [{mark}] {gate}: 期望={want!r} 实得={got!r}")
    return 0 if ok else 1


def _dry_run(dbs: list[str], event: str | None, rules: dict) -> int:
    """干跑:①素材包自校验 ②旧台账基线 ③五闸自校验;零 LLM。"""
    rc = _gate_selfcheck(rules)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for src in dbs:
            p = Path(src)
            if not p.exists():
                print(f"[探针] 跳过不存在的 db: {src}")
                continue
            eid = event or _event_of(p)
            led = _open_ledger(p, tmp)
            try:
                gists = led.claim_gists(eid)
                brief = verdict_brief(led, eid, rules, gists)
                print(f"=== 素材包自校验 {p.name} ({eid}) ===")
                if not brief:
                    print("- [WARN] 空素材包(无主导假设)——I2 研判说明不适用")
                    continue
                for k, v in brief.items():
                    print(f"- {k}: {v}")
                if eid.endswith("_r2"):
                    bad = []
                    if brief.get("label") != _R2_EXPECT["label"]:
                        bad.append(f"label={brief.get('label')!r}"
                                   f"(期望剥括号 {_R2_EXPECT['label']!r})")
                    if _R2_EXPECT["criteria_has"] not in \
                            (brief.get("criteria") or ""):
                        bad.append("criteria 缺判据关键词")
                    if _R2_EXPECT["t1_absent"] and "t1_baseline" in brief:
                        bad.append("t1_baseline 应缺席(amount_history null)")
                    if _R2_EXPECT["contra_not_none"] and \
                            not brief.get("contra_state"):
                        bad.append("contra_state 缺(r2 有反向材料)")
                    if _R2_EXPECT["runner_present"] and \
                            "runners_up_reason" not in brief:
                        bad.append("runners_up_reason 缺(r2 有居次假设)")
                    if bad:
                        rc = 1
                        for b in bad:
                            print(f"- [FAIL] {b}")
                    else:
                        print("- [OK] r2 实锚逐字段断言全过")
                # 旧台账基线:结论节串讲提取+合格判据(命中=0 预期)
                for sec in led.get_sections(eid):
                    if not (sec["kind"] == "conclusion" or sec["ord"] == 3):
                        continue   # 结论节=ord 3(finalize 同判据;kind 存 standard)
                    narr = _narr_extract(sec["draft_text"] or "", rules)
                    hit = _criteria_quoted(narr, brief.get("criteria") or "")
                    tag = "OK" if not hit else "FAIL"
                    if hit:
                        rc = 1
                    print(f"- [{tag}] 旧产物结论节:串讲提取="
                          f"{'在(' + str(len(narr)) + '字)' if narr else '无(纯卡)'}"
                          f";判据术语原文引用命中={hit!r}")
            finally:
                led.conn.close()
    print("[探针] 干跑结束(--llm 真模型定标;KEY 需在 .env;阈值写回施工单 §九)")
    return rc


def _refs_of(led: LedgerRepository, claim_id: str, topk: int = 5) -> list[str]:
    rows = led.evidence_for_claim(claim_id)
    strong = [e for e in rows if e["ruling"] in ("支持", "矛盾", "时效刷新")]
    weak = [e for e in rows if e["ruling"] is None or e["ruling"] == "无关"]
    return [e["evidence_id"].rsplit(":", 1)[-1] for e in (strong + weak)[:topk]]


def _build_context(led: LedgerRepository, event_id: str, sec, rules: dict,
                   gists: dict) -> dict:
    """结论节起草输入(生产 finalize 环① 同判据:M1 三档滤+claim_input_row
    +verdict_brief 素材包)。"""
    claims = led.claims_for_section(sec["section_id"])
    payload = []
    for c in claims:
        if claim_tier(c["text"] or "", rules):
            continue
        payload.append(claim_input_row(c, _refs_of(led, c["claim_id"]),
                                       rules, gists))
    ctx = {"event_id": event_id, "title": sec["title"], "kind": "conclusion",
           "claims": payload,
           "standings": led.hypothesis_standings(event_id),
           "envelope_label": ""}
    brief = verdict_brief(led, event_id, rules, gists)
    if brief:
        ctx["verdict_brief"] = brief
    return ctx


def _llm_run(led: LedgerRepository, event_id: str, rules: dict,
             rounds: int) -> int:
    """真模型双轮定标:结论节 draft→提取→五闸→合格判据读数。"""
    from sense2act.llm import DeepSeekLLM, call_structured
    from sense2act.stages.d_hooks import DraftOutput
    from sense2act.prompts import S7_DRAFT_PROMPT
    llm = DeepSeekLLM()   # 消费 .env DEEPSEEK_MODEL=runtime 冒烟位
    gists = led.claim_gists(event_id)
    sec = next((s for s in led.get_sections(event_id)
                if s["kind"] == "conclusion" or s["ord"] == 3), None)
    if sec is None:
        print("[探针] 无结论节")
        return 2
    rounds_out: list[dict] = []
    for rd in range(max(rounds, 1)):
        ctx = _build_context(led, event_id, sec, rules, gists)
        brief = ctx.get("verdict_brief") or {}
        out = call_structured(
            llm, stage="s7_draft", system=S7_DRAFT_PROMPT,
            user=json.dumps(ctx, ensure_ascii=False),
            model_cls=DraftOutput, retry=1)
        text = out.markdown
        narr = _narr_extract(text, rules)
        mat = verdict_gate_material(led, event_id, sec["section_id"],
                                    rules, gists, brief)
        body, reasons = verdict_gates_apply(narr, **mat) if narr else ("", [])
        quoted = _criteria_quoted(narr, brief.get("criteria") or "")
        rounds_out.append({"text": text, "narr": narr, "body": body,
                           "reasons": reasons, "quoted": quoted})
        print(f"=== 第 {rd + 1} 轮 ===")
    # 读数汇总
    for rd, r in enumerate(rounds_out):
        gate = "PASS" if (r["body"] and not r["quoted"]) else "FAIL"
        print(f"- [R{rd + 1} {gate}] 串讲提取={'在' if r['narr'] else '无'}"
              f";验票={'过' if r['body'] else '拒(' + '、'.join(r['reasons']) + ')'}"
              f";判据术语原文引用={r['quoted']!r}")
    if len(rounds_out) >= 2:
        flips = 0
        for key in ("narr", "body"):
            a = bool(rounds_out[0][key])
            b = bool(rounds_out[1][key])
            if a != b:
                flips += 1
        same = rounds_out[0]["body"] == rounds_out[1]["body"]
        print(f"=== 两轮通过性翻转:{flips};验票后文本逐字节一致={same} ===")
    print("=== 逐条判读表(合格判据=判据被本案数值/事实具体化,人工目检) ===")
    for rd, r in enumerate(rounds_out):
        print(f"--- R{rd + 1} 结论节串讲 ---")
        print(r["narr"] or "(无)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="报告结论研判说明 I2 定标探针")
    ap.add_argument("--db", nargs="+", default=_DEFAULT_DBS)
    ap.add_argument("--event", default="", help="事件 id(缺省=按 db 文件名推导)")
    ap.add_argument("--llm", action="store_true", help="真模型跑(需 KEY)")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    rules = load_present_rules(ROOT / "data" / "config")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if not args.llm:
            return _dry_run(args.db, args.event or None, rules)
        led = None
        eid = args.event
        for src in args.db:
            p = Path(src)
            if p.exists() and led is None:
                led = _open_ledger(p, tmp)
                eid = eid or _event_of(p)
        if led is None:
            print(f"[探针] 无可用 db: {args.db}")
            return 2
        try:
            return _llm_run(led, eid, rules, args.rounds)
        finally:
            led.conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
