"""评测层(P1-5):确定性打分——加权一致率 + S6 成本预算,零 LLM 评委。

分权纪律的评测版:所有指标从台账与 result.json 确定性计算,评测器自身
不调用任何模型。对原始台账零侵入:取数前把 db(含 -wal/-shm)复制到临时
目录再打开(只读语义 + WAL 数据完整性,不触碰原库)。

金标对齐:draft_id 主对齐带双闸——谓词类型闸(载明型与实质断言不互配,防伪
过度断言)+文本校验下限(draft_id_text_floor:同类型但文本低于下限仍是跨 run
撞号伪配对——金标编号继承自建标 run,id 相同≠同断言),均拆开并回池参与文本
次对齐;对不上的显式上报——unmatched_golden 计漏报,system_extra 只报告不计分
(S5 派生 claim 是合法行为,计入分母会制造伪过度断言),不静默丢弃。

错误三分类与加权一致率(三档/案例间可比,分母只依赖金标规模与权重):
    漏报   = 金标终态 / 系统待证实(或金标未对齐到系统 claim)   权重 W.miss
    过度断言 = 金标待证实 / 系统终态(含已证实与矛盾)            权重 W.over
    反转   = 终态极性相反(已证实 <-> 矛盾)                      权重 W.flip
    A_w    = 1 - 加权误差 / (N * max(W))

variant 三档(粗/中/细)经 S1B_PROMPT 粒度指令位实现(P1-6,2026-10-03);
非法档位显式报错,不静默忽略。注意:回放案例的 responses 是冻结录制件
(按 stage 顺序消费,与档位无关),离线对比三档得分预期一致——真实分档
差异需按档位 live 运行后再评测(sweep 留档)。
"""
from __future__ import annotations

import difflib
import json
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .run_meta import align_from_meta, read_run_meta

REPO_ROOT = Path(__file__).resolve().parent.parent

# 三态极性:终态带方向,待证实为 0( NEI 对应物,落代码侧非模型意见 )
POLARITY = {"已证实": 1, "矛盾": -1, "待证实": 0}
CONSISTENT = "一致"
MISS = "漏报"
OVER = "过度断言"
FLIP = "反转"
_FLIP_CAUSES = ("矛盾翻转", "时效翻转", "状态刷新")

# subprocess 退出后的案例运行状态;后三类不打分,报告显式列 BLOCKED 行
_SCOREABLE = ("ok", "budget_stop")
_UNRUN = ("disabled",)


# ---------------------------------------------------------------- 配置载体

@dataclass
class Weights:
    """错误权重(不写死代码常量,进 eval/cases/suite.json)。"""

    miss: int = 1
    over: int = 2
    flip: int = 2

    @property
    def w_max(self) -> int:
        return max(self.miss, self.over, self.flip)

    def of(self, err: str) -> int:
        return {MISS: self.miss, OVER: self.over, FLIP: self.flip}.get(err, 0)

    def as_dict(self) -> dict:
        return {"miss": self.miss, "over": self.over, "flip": self.flip}


@dataclass
class AlignConfig:
    text_sim_threshold: float = 0.60
    suspicious_threshold: float = 0.45   # 低于此值的 text_sim 配对在明细里标可疑
    # draft_id 配对的文本校验下限:id 撞号+文本低于此值=跨 run 伪配对,拆开回池。
    # 定标依据(2026-10-04 分诊,sweep3d 16 run 全配对样本):语义错位对全部 <0.30,
    # 语义真配对最低 0.3171;0.30-0.45 灰色带字面相似度无判别力,留待要素化金标。
    draft_id_text_floor: float = 0.30


@dataclass
class Budget:
    s6_opinion_max: int = 40
    llm_calls_max: int = 60

    def as_dict(self) -> dict:
        return {"s6_opinion_max": self.s6_opinion_max,
                "llm_calls_max": self.llm_calls_max}


# ------------------------------------------------------ 文本归一 / 相似度 / 对齐

def normalize_text(text: str) -> str:
    """NFKC + casefold + 去全部空白(与 S6 验票的归一口径同向)。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").casefold())


def sim_ratio(a: str, b: str) -> float:
    """difflib 序列相似度,中文直接可用,零额外依赖。"""
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# 载明型谓词标记(2026-10-04 谓词类型校验):含这些标记的 claim 断言的是
# "语料/信封怎么说"(第一方自证,corpus_bound 一类),而非世界实情——与实质
# 断言不可互相对齐,否则产生伪过度断言(实测:"线索载明联合研发"配金标
# "构成协同关系" -> 金标待证实 vs 系统已证实)。校准:冻结中档金标 17 条
# 全部分类正确(14 载明=True / 3 实质=False);系统侧优先用 corpus_bound
# 旗标(S1b 模型标注/代码判决),标记词兜底。
REPORTING_MARKERS = ("载明", "标注", "标题", "正文", "记载", "写明", "署名",
                     "报道", "引述", "信封", "线索", "解读", "摘要", "来源")


def _is_reporting(text: str, corpus_bound: object = None) -> bool:
    if bool(corpus_bound):
        return True
    t = normalize_text(text)
    return any(m in t for m in REPORTING_MARKERS)


def classify(golden_status: str, system_status: str | None) -> str:
    """三分类判定(极性表覆盖 3x3 全组合;system=None 即金标未对齐)。"""
    if system_status is None:
        return MISS
    gp, sp = POLARITY[golden_status], POLARITY[system_status]
    if gp == 0 and sp == 0:
        return CONSISTENT
    if gp != 0 and sp == 0:
        return MISS
    if gp == 0 and sp != 0:
        return OVER
    if gp != sp:
        return FLIP
    return CONSISTENT


def align(golden: list[dict], system: list[dict], cfg: AlignConfig) -> dict:
    """金标 <-> 系统 claim 对齐:①draft_id 精确(带文本下限闸);②文本相似度贪心一对一(带谓词类型闸)。

    ①主对齐闸=draft_id_text_floor(2026-10-04,id 撞号+文本几乎无关=跨 run
    伪配对,拆开回池;定标:语义错位对全部 <0.30,语义真配对最低 0.3171)。
    ②次对齐闸=谓词类型:载明型(信封/语料自证)与实质断言不互相对齐——
    文本撞车会产生伪过度断言;系统侧以 corpus_bound 旗标优先、标记词兜底,
    金标侧用标记词(或自带 corpus_bound 字段)。两闸互补:floor 管"内容不同
    撞同号",类型闸管"内容相似但认识论地位不同"。
    返回 {pairs, unmatched_golden, system_extra, type_rejected}。
    """
    sys_by_id = {s["draft_id"]: s for s in system}
    used: dict[str, str] = {}
    pairs: list[dict] = []
    type_rejected: list[dict] = []   # 谓词类型闸拒绝(载明型 x 实质型)
    id_split: list[dict] = []        # 文本下限闸拆开的撞号对(跨 run 编号巧合)
    rejected_pairs: set[tuple[str, str]] = set()   # 已拒(g,s)不再复议,防主次双记
    matched_golden: set[str] = set()

    for g in golden:                                   # ① 主对齐(类型闸+文本下限闸)
        s = sys_by_id.get(g["draft_id"])
        if s is None:
            continue
        if _is_reporting(g["text"], g.get("corpus_bound")) != \
                _is_reporting(s["text"], s.get("corpus_bound")):
            # 主对齐同过谓词类型闸(2026-10-05 复发修复):同 draft_id 但认识论
            # 地位相异=编号巧合,不配(sweep4 实测:金标实质 c14 撞系统载明 c14
            # -> 伪过度断言;类型闸曾只在次对齐,主对齐漏防)。
            type_rejected.append({"golden": g["draft_id"], "system": s["draft_id"],
                                  "sim": 1.0, "method": "draft_id"})
            rejected_pairs.add((g["draft_id"], s["draft_id"]))
            continue
        r = sim_ratio(g["text"], s["text"])
        if r < cfg.draft_id_text_floor:
            # 跨 run 撞号伪配对:id 相同但文本几乎无关(金标编号继承自建标
            # run,新 run 编号独立)。拆开回池;次对齐阈值更高,同对不可能复活。
            id_split.append({"golden": g["draft_id"], "system": s["draft_id"],
                             "sim": round(r, 4), "method": "draft_id"})
            rejected_pairs.add((g["draft_id"], s["draft_id"]))
            continue
        pairs.append({"golden": g, "system": s, "method": "draft_id", "sim": 1.0})
        used[s["draft_id"]] = g["draft_id"]
        matched_golden.add(g["draft_id"])

    # ①' 要素匹配通道(方向 B:金标稳健性,2026-10-06):金标条目可声明
    # elements(跨 run 稳定的实体词表),系统 claim 归一化后**全覆盖**即配——
    # 判定基于断言要素而非措辞,治字面相似度的灰色带(同义改写/长文本稀释
    # 过不了 0.60 阈值但语义等价的条目)。仍过谓词类型闸;要素多者优先
    # (更具体的断言先占),贪心一对一防多对一;无 elements 的条目不受影响
    # (渐进迁移)。
    elem_cands: list[tuple[int, str, str, dict, dict]] = []
    for g in golden:
        els = g.get("elements") or []
        if not els or g["draft_id"] in matched_golden:
            continue
        g_rep = _is_reporting(g["text"], g.get("corpus_bound"))
        for s in system:
            if s["draft_id"] in used or (g["draft_id"], s["draft_id"]) in rejected_pairs:
                continue
            nt = normalize_text(s["text"])
            if all(normalize_text(e) in nt for e in els):
                if g_rep != _is_reporting(s["text"], s.get("corpus_bound")):
                    type_rejected.append({"golden": g["draft_id"],
                                          "system": s["draft_id"],
                                          "sim": None})
                    rejected_pairs.add((g["draft_id"], s["draft_id"]))
                    continue
                elem_cands.append((len(els), g["draft_id"], s["draft_id"], g, s))
    elem_cands.sort(key=lambda x: (-x[0], x[1], x[2]))
    for _, _gid, _sid, g, s in elem_cands:
        if g["draft_id"] in matched_golden or s["draft_id"] in used:
            continue
        pairs.append({"golden": g, "system": s, "method": "elements", "sim": None})
        used[s["draft_id"]] = g["draft_id"]
        matched_golden.add(g["draft_id"])

    cands: list[tuple[float, str, str, dict, dict]] = []   # ② 次对齐
    for g in golden:
        if g["draft_id"] in matched_golden:
            continue
        g_rep = _is_reporting(g["text"], g.get("corpus_bound"))
        for s in system:
            if s["draft_id"] in used:
                continue
            if (g["draft_id"], s["draft_id"]) in rejected_pairs:
                continue
            r = sim_ratio(g["text"], s["text"])
            if r >= cfg.text_sim_threshold:
                s_rep = _is_reporting(s["text"], s.get("corpus_bound"))
                if g_rep != s_rep:
                    type_rejected.append({"golden": g["draft_id"],
                                          "system": s["draft_id"],
                                          "sim": round(r, 4)})
                    continue
                cands.append((r, g["draft_id"], s["draft_id"], g, s))
    cands.sort(key=lambda x: (-x[0], x[1], x[2]))      # 高分先占;并列按 id 稳定序
    for r, _gid, _sid, g, s in cands:
        if g["draft_id"] in matched_golden or s["draft_id"] in used:
            continue
        pairs.append({"golden": g, "system": s, "method": "text_sim",
                      "sim": round(r, 4)})
        used[s["draft_id"]] = g["draft_id"]
        matched_golden.add(g["draft_id"])

    unmatched = [g for g in golden if g["draft_id"] not in matched_golden]
    extra = [s for s in system if s["draft_id"] not in used]
    return {"pairs": pairs, "unmatched_golden": unmatched, "system_extra": extra,
            "type_rejected": type_rejected, "id_split": id_split}


# ------------------------------------------------------------------ 台账取数

def read_case_outputs(db_path: str | Path, result_path: str | Path,
                      event_id: str) -> dict:
    """只读取数:claims 终态 / S6 意见数 / 验票失败 / 翻转 / result.json。

    先把 db 连同 -wal/-shm 复制进临时目录再打开——原库零触碰,同时保证
    WAL 里未检查点的数据被读全(与 e2e 测试读库行为等价,但不写原库)。
    """
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"台账不存在: {src}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="s2a_ro_"))
    try:
        local = tmp_dir / src.name
        shutil.copy2(src, local)
        for suffix in ("-wal", "-shm"):
            side = src.with_name(src.name + suffix)
            if side.exists():
                shutil.copy2(side, tmp_dir / (src.name + suffix))
        conn = sqlite3.connect(local)
        conn.row_factory = sqlite3.Row
        try:
            claims = [dict(r) for r in conn.execute(
                "SELECT claim_id, draft_id, text, category, status, unreachable,"
                " corpus_bound FROM claims WHERE event_id=?"
                " ORDER BY created_round, draft_id",
                (event_id,))]
            hyp_rows = [dict(r) for r in conn.execute(
                "SELECT h.hypothesis_id AS hypothesis_id, h.label AS label,"
                " hc.claim_id AS claim_id FROM hypotheses h"
                " LEFT JOIN hypothesis_claims hc ON hc.hypothesis_id=h.hypothesis_id"
                " WHERE h.event_id=? ORDER BY h.registered_round",
                (event_id,))]
            s6_opinions = conn.execute(
                "SELECT COUNT(*) AS c FROM decision_log"
                " WHERE event_id=? AND kind='意见'", (event_id,)).fetchone()["c"]
            ballot_fail = overridden = 0
            for row in conn.execute(
                    "SELECT payload_json FROM decision_log"
                    " WHERE event_id=? AND kind='判决'", (event_id,)):
                payload = json.loads(row["payload_json"])
                ballot_fail += 1 if payload.get("skipped") else 0
                overridden += 1 if payload.get("overridden") else 0
            transitions: dict[str, int] = {}
            for row in conn.execute(
                    "SELECT t.cause AS cause, COUNT(*) AS c"
                    " FROM claim_transitions t JOIN claims c ON c.claim_id=t.claim_id"
                    " WHERE c.event_id=? GROUP BY t.cause", (event_id,)):
                transitions[row["cause"] or "澄清"] = row["c"]
            transitions_by_round = [dict(r) for r in conn.execute(
                "SELECT t.round AS round, t.new_status AS new_status,"
                " COUNT(*) AS c FROM claim_transitions t"
                " JOIN claims c ON c.claim_id=t.claim_id"
                " WHERE c.event_id=? GROUP BY t.round, t.new_status"
                " ORDER BY t.round", (event_id,))]
            ev_rows = [dict(r) for r in conn.execute(
                "SELECT evidence_id, title, content FROM evidence"
                " WHERE event_id=? ORDER BY evidence_id", (event_id,))]
            cal_rows = []
            for row in conn.execute(
                    "SELECT payload_json FROM decision_log"
                    " WHERE event_id=? AND kind='其他' ORDER BY id", (event_id,)):
                cal = json.loads(row["payload_json"]).get("calibration")
                if cal:
                    cal_rows.append(cal)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    result = None
    if Path(result_path).exists():
        result = json.loads(Path(result_path).read_text(encoding="utf-8-sig"))
    return {"claims": claims, "s6_opinions": s6_opinions,
            "ballot_fail": ballot_fail, "overridden": overridden,
            "transitions": transitions,
            "transitions_by_round": transitions_by_round,
            "evidence": ev_rows, "calibrations": cal_rows,
            "hypotheses": hyp_rows,
            "result": result}


def calibration_rate(calibrations: list[dict]) -> dict | None:
    """改造④规划校准率:验收标准被判"达标"的比例(HESP 预测-观察闭环)。

    过程指标,作解释变量不作准绳(启发②);无判型记录(开关未开)返回 None。
    """
    if not calibrations:
        return None
    judged = [c for c in calibrations if c.get("verdict") in ("达标", "部分相关", "不相关")]
    hit = sum(1 for c in judged if c.get("verdict") == "达标")
    return {"hit": hit, "judged": len(judged), "total": len(calibrations),
            "rate": (round(hit / len(judged), 4) if judged else None)}


def citation_audit(report_md: str, evidence_rows: list[dict],
                   claims: list[dict] | None = None) -> dict | None:
    """E3 引用抽检(P1-5):报告交付物的**黑盒**引用审计。

    与核对证书的承诺同构——报告可独立于内部结构验证(评审只需 report.md
    与公开台账)。三项确定性检查,零 LLM:
    A 悬空引用:每节正文 [n] 标号不超过该节"引用证据"块条数;
    B 证据表完整:附录 A 的 [Ek] 全部能在台账 evidence 中找到;
    C 摘录溯源:节末引用摘录能在台账可公开语料中命中(归一化前缀子串,
      宽松判——摘录允许截断/拼接)。语料=证据(title+content)+claim 文本
      (报告可合法引用解读记录/basisGaps 等 claim 载体,2026-10-06 补)。
    """
    if not report_md:
        return None

    def _norm(s: str) -> str:
        return normalize_text(s)

    corpus = [_norm((e.get("title") or "") + (e.get("content") or ""))
              for e in evidence_rows]
    corpus += [_norm(c.get("text") or "") for c in (claims or [])]
    ev_ids = {e["evidence_id"].split(":")[-1] for e in evidence_rows}

    sections = re.split(r"\n## ", report_md)
    a_bad = c_hit = c_total = 0
    for sec in sections:
        if "**引用证据**" not in sec:
            continue
        body, _, block = sec.partition("**引用证据**")
        nums = [int(n) for n in re.findall(r"\[(\d+)\]", body)]
        quoted = re.findall(r"^\[(\d+)\]\s*(.+)$", block, re.M)
        if nums and max(nums, default=0) > len(quoted):
            a_bad += 1                                   # 悬空引用
        for _n, text in quoted:
            c_total += 1
            head = _norm(text)[:20]
            if head and any(head in doc for doc in corpus):
                c_hit += 1                               # 摘录可溯源

    app_ids = set(re.findall(r"\*\*\[(E\d+)\]\*\*", report_md))
    b_ok = bool(app_ids) and app_ids <= ev_ids
    rate = (round(c_hit / c_total, 4) if c_total else None)
    if c_total == 0:
        # 回放模式的报告由确定性模板生成(字节级复现,无引用证据块)——
        # E3 的有效对象是 live 报告;空转不标 PASS(虚报),标 NA。
        verdict = None
    else:
        verdict = a_bad == 0 and b_ok and rate >= 0.9
    return {"dangling_sections": a_bad, "appendix_complete": b_ok,
            "traceable": c_hit, "quoted_total": c_total,
            "traceable_rate": rate, "pass": verdict}


def round_yield(claims: list[dict], transitions_by_round: list[dict]) -> list[dict]:
    """E2 轮数-收益曲线(P1-5):每轮新增 claim 数与终态化数。

    读数:分解收益是否前置(R1 大额产出=分解层一次到位)+边际递减形态
    (后轮收益衰减=继续轮换的边际成本依据)。零 LLM,台账确定性取数。
    """
    rounds = sorted({c.get("created_round", 0) for c in claims}
                    | {t.get("round", 0) for t in transitions_by_round})
    out = []
    for r in rounds:
        out.append({
            "round": r,
            "new_claims": sum(1 for c in claims
                              if c.get("created_round", 0) == r),
            "finalized": sum(t["c"] for t in transitions_by_round
                             if t.get("round") == r
                             and t.get("new_status") in ("已证实", "矛盾")),
        })
    return out


def count_s6_attempted(responses_path: str | Path) -> int | None:
    """S6 意见调用上界:冻结 responses 里 stage=='s6_opinion' 条数(含验票重试)。"""
    path = Path(responses_path)
    if not path.exists():
        return None
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return sum(1 for r in rows if r.get("stage") == "s6_opinion")


# -------------------------------------------------------------------- 打分

def score_case(*, case: dict, golden_doc: dict, outputs: dict, weights: Weights,
               budget: Budget, align_cfg: AlignConfig, attempted_s6: int | None,
               run_meta: dict, report_md: str = "") -> dict:
    """单案例打分:对齐 -> 逐条判定 -> 加权一致率 + 成本/诊断指标。"""
    golden = golden_doc["claims"]
    system = outputs["claims"]
    al = align(golden, system, align_cfg)

    hyp = None
    if outputs.get("hypotheses"):
        golden_by_draft = {p["golden"]["draft_id"]: p["golden"]["golden_status"]
                           for p in al["pairs"]}
        hyp = hypothesis_concordance(system, outputs["hypotheses"],
                                     golden_by_draft)

    per_claim: list[dict] = []
    counts = {CONSISTENT: 0, MISS: 0, OVER: 0, FLIP: 0}
    weighted_error = 0

    def _row(g: dict, s: dict | None, method: str | None, sim: float | None,
             verdict: str) -> dict:
        return {
            "draft_id": g["draft_id"], "golden_status": g["golden_status"],
            "system_status": s["status"] if s else None,
            "verdict": verdict, "align_method": method, "sim": sim,
            "suspicious": bool(method == "text_sim"
                               and sim is not None
                               and sim < align_cfg.suspicious_threshold),
            "golden_text": g["text"],
            "system_text": s["text"] if s else None,
            "golden_note": g.get("note", ""),
            "unreachable": s.get("unreachable") if s else None,
        }

    for p in al["pairs"]:
        g, s = p["golden"], p["system"]
        verdict = classify(g["golden_status"], s["status"])
        counts[verdict] += 1
        weighted_error += weights.of(verdict)
        per_claim.append(_row(g, s, p["method"], p["sim"], verdict))
    for g in al["unmatched_golden"]:                   # 金标有、系统无 -> 漏报
        counts[MISS] += 1
        weighted_error += weights.of(MISS)
        per_claim.append(_row(g, None, None, None, MISS))

    n = len(golden)
    agreement = (None if n == 0
                 else round(1.0 - weighted_error / (n * weights.w_max), 4))
    result = outputs.get("result") or {}
    costs = result.get("costs", {})
    llm_calls = costs.get("llm_calls")
    collect_calls = costs.get("collect_calls")
    s6_opinions = outputs["s6_opinions"]
    budget_ok = (llm_calls is not None
                 and s6_opinions <= budget.s6_opinion_max
                 and llm_calls <= budget.llm_calls_max)
    flips_total = sum(outputs["transitions"].get(c, 0) for c in _FLIP_CAUSES)

    return {
        "case_id": case["case_id"],
        "title": case.get("title", ""),
        "source": case.get("source", ""),
        "variant": run_meta.get("variant", "中"),
        "run_status": run_meta.get("run_status", ""),
        "note": run_meta.get("note", ""),
        "n_golden": n, "n_matched": len(al["pairs"]),
        "n_unmatched_golden": len(al["unmatched_golden"]),
        "n_system_extra": len(al["system_extra"]),
        "n_type_rejected": len(al.get("type_rejected", [])),
        "type_rejected": al.get("type_rejected", []),
        "n_id_split": len(al.get("id_split", [])),
        "id_split": al.get("id_split", []),
        "correct": counts[CONSISTENT], "miss": counts[MISS],
        "over": counts[OVER], "flip": counts[FLIP],
        "weighted_error": weighted_error, "weighted_agreement": agreement,
        # 错误构成分列(P0-1):A_w 单一数字会掩盖三类漏报(仪器误差/判决分歧/
        # 真漏拆)的性质差异——对齐率=仪器健康度,条件一致率=配对内的判决质量,
        # 未对齐金标清单≈真漏拆+灰色带残余,三者与 A_w 同看(2026-10-05)。
        "align_rate": (None if n == 0
                       else round(len(al["pairs"]) / n, 4)),
        "conditional_agreement": (None if not al["pairs"] else
                                  round(counts[CONSISTENT] / len(al["pairs"]), 4)),
        # 实验注册表(P0-3):录制时管道状态随案例透传,跨套件可比性机器可判
        "recorded_at": case.get("recorded_at", ""),
        "pipeline_note": case.get("pipeline_note", ""),
        # 假设级终态指标(P1-4):研判传导一致性(评估层级上移)
        "hypothesis": hyp,
        # E2 轮数-收益曲线:分解收益前置与边际递减形态
        "round_yield": round_yield(outputs["claims"],
                                   outputs.get("transitions_by_round", [])),
        # E3 引用抽检:报告交付物黑盒审计(核对证书的独立可验证性)
        "citation_audit": citation_audit(report_md, outputs.get("evidence", []),
                                         outputs.get("claims", [])),
        # 改造④规划校准率:验收标准预注册回填(HESP 预测-观察闭环)
        "calibration": calibration_rate(outputs.get("calibrations", [])),
        "s6_opinions": s6_opinions, "s6_attempted": attempted_s6,
        "llm_calls": llm_calls, "collect_calls": collect_calls,
        "budget": budget.as_dict(), "budget_ok": budget_ok,
        "ballot_fail": outputs["ballot_fail"], "overridden": outputs["overridden"],
        "flips": outputs["transitions"], "flips_total": flips_total,
        "stop_reason": result.get("stop_reason", ""),
        "golden_verified": bool(golden_doc.get("verified", False)),
        "claims": per_claim,
        "system_extra_ids": [s["draft_id"] for s in al["system_extra"]],
    }


def hypothesis_concordance(system_claims: list[dict], hyp_rows: list[dict],
                           golden_by_draft: dict[str, str]) -> dict | None:
    """假设级研判传导一致性(P1-4,"评估层级上移"的终态指标,2026-10-05)。

    claim 级 A_w 只是中间指标,终态是"研判层(主导假设)是否被判决误差带偏":
    - 系统排序 = 台账现状 standings(记分公式与 ledger.hypothesis_standings
      严格一致:已证实 +1 / 矛盾 -1 / 待证实 0;dominant = score==max 且 >0);
    - 金标排序 = matched claim 终态按金标覆写、其余保持系统终态后重算——
      回答"若每条 claim 都判对,主导假设是谁";
    - dominant_agree = 两个排序的主导集合一致(传导无失真)。
    金标零新增标注(复用原子层金标)。局限(如实):unmatched 金标条无对应
    系统 claim,不参与覆写;金标排序测的是"与证据账本的传导保真度",
    对外部世界真值的正确性由真实事件另行人工核对,不进评测器。
    """
    status_by_claim = {c["claim_id"]: c["status"] for c in system_claims}
    override = {}
    for c in system_claims:
        g = golden_by_draft.get(c["draft_id"])
        if g is not None:
            override[c["claim_id"]] = g

    def _rank(apply_override: bool) -> list[dict]:
        agg: dict[str, dict] = {}
        for h in hyp_rows:
            agg.setdefault(h["hypothesis_id"],
                           {"label": h["label"] or h["hypothesis_id"],
                            "score": 0, "verified": 0, "contradicted": 0,
                            "pending": 0})
        for h in hyp_rows:
            cid = h.get("claim_id")
            if cid is None:
                continue
            st = agg[h["hypothesis_id"]]
            status = (override.get(cid, status_by_claim.get(cid))
                      if apply_override else status_by_claim.get(cid))
            if status is None:
                continue
            if status == "已证实":
                st["verified"] += 1
                st["score"] += 1
            elif status == "矛盾":
                st["contradicted"] += 1
                st["score"] -= 1
            else:
                st["pending"] += 1
        out = list(agg.values())
        if out:
            best = max(s["score"] for s in out)
            for s in out:
                s["dominant"] = bool(s["score"] == best and s["score"] > 0)
        return out

    sys_rank = _rank(False)
    gold_rank = _rank(True)
    sys_dom = {s["label"] for s in sys_rank if s["dominant"]}
    gold_dom = {s["label"] for s in gold_rank if s["dominant"]}
    if not sys_dom and not gold_dom:
        agree = None                        # 无主导假设(全 0 分)不判一致与否
    else:
        agree = sys_dom == gold_dom
    return {"system": sys_rank, "golden": gold_rank,
            "system_dominant": sorted(sys_dom),
            "golden_dominant": sorted(gold_dom),
            "dominant_agree": agree}


def bootstrap_ci_agreement(errs: list[int], w_max: int, n_boot: int = 1000,
                           seed: int = 20261005) -> tuple[float, float] | None:
    """claim 级自助重采样的加权一致率 95% CI(固定 seed,回放确定性)。

    每条 claim 的加权误差 err∈{0,1,2};有放回重抽 n 条算 A_w*,重复 n_boot
    次取 2.5%/97.5% 分位。回应 sweep3d"n=3 噪声带宽于组间差"——报告必须
    自带不确定性,统计效力从口头诚实变成印在表里。
    """
    if not errs:
        return None
    rng = random.Random(seed)
    n = len(errs)
    vals = sorted(1.0 - sum(errs[rng.randrange(n)] for _ in range(n))
                  / (n * w_max) for _ in range(n_boot))
    return (round(vals[int(0.025 * n_boot)], 4),
            round(vals[min(n_boot - 1, int(0.975 * n_boot))], 4))


def pooled_score(rows: list[dict], weights: Weights,
                 verified_only: bool = False) -> dict:
    """套件级池化:只池化已打分案例;verified_only 再排除金标未核对的案例。"""
    scored = [r for r in rows
              if r.get("run_status") in _SCOREABLE
              and r.get("weighted_agreement") is not None
              and (not verified_only or r.get("golden_verified"))]
    n = sum(r["n_golden"] for r in scored)
    err = sum(r["weighted_error"] for r in scored)
    matched = sum(r["n_matched"] for r in scored)
    correct = sum(r["correct"] for r in scored)
    errs = [weights.of(c["verdict"])
            for r in scored for c in r.get("claims", [])]
    return {
        "n_cases": len(scored), "n_claims": n, "weighted_error": err,
        "n_matched": matched,
        "miss": sum(r["miss"] for r in scored),
        "over": sum(r["over"] for r in scored),
        "flip": sum(r["flip"] for r in scored),
        "weighted_agreement": (None if n == 0
                               else round(1.0 - err / (n * weights.w_max), 4)),
        "agreement_ci95": bootstrap_ci_agreement(errs, weights.w_max),
        "align_rate": (None if n == 0 else round(matched / n, 4)),
        "conditional_agreement": (None if matched == 0
                                  else round(correct / matched, 4)),
    }


# -------------------------------------------------------------- 套件装载

def load_suite(cases_dir: str | Path) -> tuple[dict, list[dict]]:
    """读 suite.json + 各案例 case.json(相对路径一律按仓库根解析,见 run_case)。"""
    root = Path(cases_dir)
    suite_path = root / "suite.json"
    if not suite_path.exists():
        raise FileNotFoundError(f"suite.json 不存在: {suite_path}")
    suite = json.loads(suite_path.read_text(encoding="utf-8-sig"))
    cases = []
    for cid in suite.get("cases", []):
        cpath = root / cid / "case.json"
        if not cpath.exists():
            raise FileNotFoundError(f"案例清单缺失: {cpath}")
        case = json.loads(cpath.read_text(encoding="utf-8-sig"))
        case.setdefault("case_id", cid)
        if case["case_id"] != cid:
            raise ValueError(f"case_id 不一致: {cpath} 声明为 {case['case_id']}")
        if case.get("enabled", True):
            for key in ("responses", "event_id"):
                if not case.get(key):
                    raise ValueError(f"案例缺必填字段 {key}: {cpath}")
            # 单信封与批量信封二选一;batch 模式(--envelope-batch --signal-index)
            # 无 seed(信封证据唯一),seed 由必填降为可选(2026-10-06)
            if not case.get("envelope") and not case.get("envelope_batch"):
                raise ValueError(f"案例缺 envelope 或 envelope_batch: {cpath}")
        cases.append(case)
    return suite, cases


def load_golden(case_dir: str | Path, case: dict) -> dict:
    """读 golden.json 并做结构校验(case_id/event_id 交叉核对、三态合法)。

    case.json 可带 golden_ref(相对 case_dir 路径,如 "../cases/x/golden.json")
    跨套件引用金标原件——防复制漂移:金标人工修订只改源,引用处自动跟随。
    """
    ref = case.get("golden_ref")
    path = ((Path(case_dir) / ref).resolve() if ref
            else Path(case_dir) / "golden.json")
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("case_id", "event_id", "verified", "claims"):
        if key not in doc:
            raise ValueError(f"golden.json 缺字段 {key}: {path}")
    if not ref and (doc["case_id"] != case["case_id"]
                    or doc["event_id"] != case["event_id"]):
        # 跨套件引用(golden_ref)时金标 id 属于源 run(同信号重录场景),
        # 与当前 case 的 id 天然不同,不作交叉校验;同目录金标保持严格校验。
        raise ValueError(f"golden.json 与 case.json 的 id 不一致: {path}")
    for c in doc["claims"]:
        for key in ("draft_id", "text", "golden_status"):
            if not c.get(key):
                raise ValueError(f"golden claim 缺字段 {key}: {path}")
        if c["golden_status"] not in POLARITY:
            raise ValueError(
                f"golden_status 必须是三态之一,得到 {c['golden_status']!r}: {path}")
    return doc


def case_budget(case: dict, default_budget: dict) -> Budget:
    merged = {**default_budget, **(case.get("budget") or {})}
    return Budget(**merged)


def case_path(case: dict, key: str) -> str:
    """案例内相对路径按仓库根解析(与调用时的 cwd 无关)。"""
    raw = case[key]
    p = Path(raw)
    return str(p if p.is_absolute() else (REPO_ROOT / p).resolve())


# -------------------------------------------------------------- 子进程执行

def build_investigate_args(case: dict, db: Path, runs_dir: Path,
                           variant: str = "中") -> list[str]:
    if case.get("envelope_batch"):
        # batch 模式:上游 Top-K 信号就地装配(--envelope-batch --signal-index),
        # 无 seed(信封证据唯一)。2026-10-06 案例集扩充支持。
        args = ["--envelope-batch", case_path(case, "envelope_batch"),
                "--signal-index", str(case.get("signal_index", 0)),
                "--replay", case_path(case, "responses")]
    else:
        args = ["--envelope", case_path(case, "envelope"),
                "--replay", case_path(case, "responses"),
                "--seed", case_path(case, "seed")]
    args += ["--db", str(db), "--runs-dir", str(runs_dir),
             "--config-dir", case_path(case, "config_dir")
             if case.get("config_dir") else "data/config",
             "--variant", variant]
    if case.get("event_id"):
        args += ["--event-id", case["event_id"]]
    if case.get("refresh_plan"):
        args += ["--refresh-plan", case_path(case, "refresh_plan")]
    flag_map = {"max_rounds": "--max-rounds",
                "max_llm_calls": "--max-llm-calls",
                "max_collect_calls": "--max-collect-calls",
                "assoc": "--assoc",
                "calibration": "--calibration",
                "ask_mode": "--ask-mode",
                "ask_gate": "--ask-gate",
                "sim_persona": "--sim-persona",
                "sim_seed": "--sim-seed",
                "ask_final": "--ask-final"}
    for key, flag in flag_map.items():
        if key in (case.get("run_args") or {}):
            args += [flag, str(case["run_args"][key])]
    return args


def classify_failure(err: str) -> str:
    """子进程失败的归因(文案来自 ReplayLLM/ReplayClarifier 的显式报错)。"""
    if "人答回放" in err:        # 人答错位/耗尽(P1-8):meta.ask_answers 与流程不一致
        return "ask_replay_mismatch"
    if "回放错位" in err:
        return "replay_mismatch"
    if "回放响应耗尽" in err:
        return "replay_exhausted"
    return "error"


def run_case(case: dict, work: Path, timeout: int, variant: str = "中") -> dict:
    """跑单案例(独立 subprocess,临时 db + 临时 runs-dir),归类运行状态。"""
    db = work / f"{case['case_id']}.db"
    runs_dir = work / "runs"
    # 参数配对契约(P0 防再犯,2026-10-05):录制件带 .meta.json 时,行为参数
    # (assoc/max_rounds 等)以 meta 为真值自动对齐——评测的目的是忠实复现
    # 录制时刻,case.json 的参数漂移在此被纠正并留痕。
    meta_note = ""
    meta = read_run_meta(case_path(case, "responses"))
    if meta:
        meta_note = align_from_meta(case, meta)
        mv = meta.get("variant")
        if mv and mv != variant:
            # variant 在回放中被冻结响应吸收(按 stage 消费,无行为差异),
            # 但 cli 的配对契约按 meta 硬校验——评测以录制真值对齐。
            meta_note = (meta_note + "; " if meta_note else "") + \
                f"variant:{variant}->{mv}"
            variant = mv
    cmd = [sys.executable, "-m", "sense2act", "investigate",
           *build_investigate_args(case, db, runs_dir, variant)]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                              text=True, encoding="utf-8", timeout=timeout)
        rc, err = proc.returncode, proc.stderr or ""
    except subprocess.TimeoutExpired:
        return {"case_id": case["case_id"], "run_status": "error",
                "db": str(db), "result_path": "",
                "note": f"子进程超时(>{timeout}s)"}
    result_path = runs_dir / case["event_id"] / "result.json"
    if rc == 0:
        if not result_path.exists():
            return {"case_id": case["case_id"], "run_status": "error",
                    "db": str(db), "result_path": str(result_path),
                    "note": "退出码 0 但 result.json 缺失"}
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        # 预算护栏的两种形态:stop_reason 明示;或异常被规划器降级吸收后
        # 以"轮数上限"停止且 llm_calls 顶满显式上限(回放实测形态)
        max_calls = (case.get("run_args") or {}).get("max_llm_calls")
        hit_guard = ("预算护栏" in result.get("stop_reason", "")
                     or (max_calls is not None
                         and (result.get("costs") or {}).get("llm_calls", 0)
                         >= max_calls))
        status = "budget_stop" if hit_guard else "ok"
        return {"case_id": case["case_id"], "run_status": status,
                "db": str(db), "result_path": str(result_path),
                "note": meta_note}
    return {"case_id": case["case_id"], "run_status": classify_failure(err),
            "db": str(db), "result_path": str(result_path), "note": err[-500:]}


def run_and_score(case: dict, case_dir: Path, work: Path, timeout: int,
                  weights: Weights, align_cfg: AlignConfig,
                  default_budget: dict, variant: str) -> dict:
    """disabled 案例直接列行;其余跑 investigate -> 取数 -> 打分。"""
    if not case.get("enabled", True):
        return {"case_id": case["case_id"], "title": case.get("title", ""),
                "run_status": "disabled",
                "note": case.get("blocked_note", "enabled=false")}
    run = run_case(case, work, timeout, variant)
    if run["run_status"] not in _SCOREABLE:
        return {"case_id": case["case_id"], "title": case.get("title", ""),
                "run_status": run["run_status"], "note": run["note"]}
    golden_doc = load_golden(case_dir, case)
    outputs = read_case_outputs(run["db"], run["result_path"], case["event_id"])
    attempted = count_s6_attempted(case_path(case, "responses"))
    report_md_path = Path(run["result_path"]).parent / "report.md"
    report_md = (report_md_path.read_text(encoding="utf-8")
                 if report_md_path.exists() else "")
    return score_case(
        case=case, golden_doc=golden_doc, outputs=outputs, weights=weights,
        budget=case_budget(case, default_budget), align_cfg=align_cfg,
        attempted_s6=attempted, report_md=report_md,
        run_meta={"run_status": run["run_status"], "variant": variant,
                  "note": run.get("note", "")})


# ------------------------------------------------------------------ 报告

_COLS = ("case", "源", "状态", "N", "对齐", "对齐率", "条件一致", "一致", "漏报",
         "过度", "反转", "加权一致率", "S6意见(实/上界)", "LLM调用", "采集",
         "预算", "验票失败", "翻转(矛/时/刷)", "停止原因", "金标")


def _fmt(v, dash="—") -> str:
    return dash if v is None else str(v)


def _case_row_cells(r: dict) -> list[str]:
    if r.get("run_status") not in _SCOREABLE:
        return [r["case_id"], r.get("source", "") or "—", r["run_status"]] \
            + [_fmt(None)] * 16 + [(r.get("note") or "")[:40].replace("|", "/")]
    s6 = f"{r['s6_opinions']}/{_fmt(r.get('s6_attempted'))}"
    fl = r.get("flips") or {}
    flips = "/".join(str(fl.get(c, 0)) for c in _FLIP_CAUSES)
    agree = _fmt(r.get("weighted_agreement"))
    if not r.get("golden_verified"):
        agree += "*"
    return [
        r["case_id"], r.get("source", "") or "—", r["run_status"],
        str(r["n_golden"]), f"{r['n_matched']}/{r['n_golden']}",
        _fmt(r.get("align_rate")), _fmt(r.get("conditional_agreement")),
        str(r["correct"]), str(r["miss"]), str(r["over"]), str(r["flip"]),
        agree, s6, _fmt(r.get("llm_calls")), _fmt(r.get("collect_calls")),
        "OK" if r.get("budget_ok") else "超限",
        str(r.get("ballot_fail", 0)), flips,
        (r.get("stop_reason") or "")[:14], "已核对" if r["golden_verified"] else "待人工核对",
    ]


def render_report_md(*, suite_id: str, variant: str, weights: Weights,
                     align_cfg: AlignConfig, rows: list[dict],
                     pooled_all: dict, pooled_verified: dict,
                     invariant: dict | None = None) -> str:
    lines = [
        f"# Sense2act 评测报告(suite={suite_id})",
        "",
        f"- variant:{variant}"
        f" | 权重:漏报={weights.miss} 过度断言={weights.over} 反转={weights.flip}",
    ]
    if invariant is not None:
        lines.append(
            "- 不变量门(require=" + json.dumps(invariant["require"],
                                               ensure_ascii=False)
            + "): " + ("**PASS**(0 违反)" if invariant["passed"]
                       else "**VIOLATION** " + "; ".join(invariant["violations"])))
    lines += [
        f"- 对齐:draft_id 主键(类型闸+文本校验≥{align_cfg.draft_id_text_floor})"
        f" + 文本相似度≥{align_cfg.text_sim_threshold}"
        f"(可疑阈值 {align_cfg.suspicious_threshold})",
    ]
    if variant != "中":
        lines += [
            "- 注意:回放案例的 responses 为冻结录制件(按 stage 顺序消费,"
            "与档位无关),离线三档得分预期与「中」一致;真实分档差异需按档位"
            " live 运行后评测(sweep 留档)。",
        ]
    lines += [
        "",
        "## 汇总表",
        "",
        "| " + " | ".join(_COLS) + " |",
        "|" + "---|" * len(_COLS),
    ]
    for r in rows:
        lines.append("| " + " | ".join(_case_row_cells(r)) + " |")

    def _pooled_row(label: str, p: dict) -> str:
        aw = f"**{_fmt(p['weighted_agreement'])}**"
        ci = p.get("agreement_ci95")
        if ci:
            aw += f"[{ci[0]},{ci[1]}]"
        return (f"| **{label}** | — | — | {p['n_claims']} | — | "
                f"{_fmt(p.get('align_rate'))} | {_fmt(p.get('conditional_agreement'))} | "
                f"{p['n_claims'] - p['miss'] - p['over'] - p['flip']} "
                f"| {p['miss']} | {p['over']} | {p['flip']} | "
                f"{aw} | — | — | — | — | — | — | — | — |")

    lines.append(_pooled_row("池化(全部已跑)", pooled_all))
    lines.append(_pooled_row("池化(仅已核对金标)", pooled_verified))
    lines += [
        "",
        "\\* 金标待人工核对,分数仅供参考。",
        "",
        "## 口径说明(论文可直接引用)",
        "",
        "- 加权一致率 A_w = 1 − (W_漏·n_漏 + W_过·n_过 + W_反·n_反) / (N × max(W));"
        "分母只依赖金标规模与权重,与系统 claim 数无关,跨档位/案例可比;",
        "- 漏报=金标终态/系统待证实(含金标未对齐到系统 claim);"
        "过度断言=金标待证实/系统终态;反转=已证实与矛盾互错;",
        "- S6 意见数=decision_log kind='意见' 行数(成功入账口径);"
        "上界=冻结 responses 中 s6_opinion 条数(含验票重试);",
        "- S5 派生 claim 与金标外的系统 claim 列入 system_extra 只报告不计分;"
        "BLOCKED/disabled 案例不进池化;",
        "- 对齐率=配对数/金标数(仪器健康度);条件一致率=一致数/配对数"
        "(配对内的判决质量,排除未拆出/未对齐的干扰);未对齐金标清单≈真漏拆+"
        "灰色带残余——三者与 A_w 同看:A_w 单一数字会掩盖漏报里仪器误差/"
        "判决分歧/真漏拆的性质差异(2026-10-04 分诊);",
        "- 假设级研判传导一致性(P1-4,终态指标):金标排序=配对 claim 终态按"
        "金标覆写后重算 standings(记分与 ledger.hypothesis_standings 同式:"
        "已证实+1/矛盾-1),系统排序=台账现状;主导集合一致=判决误差未带偏研判"
        "(评估层级上移:claim 级 A_w 是中间指标,假设级研判才是终态);"
        "金标排序测传导保真度,对外部真值的正确性由真实事件另行人工核对;",
        "- 池化 A_w 附 claim 级 bootstrap 95% CI(重采样 1000 次,固定 seed"
        "确定性)——统计效力印在表里,不靠口头声明;",
        "",
        "## 每案例明细",
    ]
    for r in rows:
        if r.get("run_status") not in _SCOREABLE:
            lines += [f"", f"### {r['case_id']}", "",
                      f"- 状态:{r['run_status']}"
                      + (f" | 备注:{r.get('note', '')[:200]}" if r.get("note") else "")]
            continue
        reg = " | ".join(filter(None, [
            f"录制:{r['recorded_at']}" if r.get("recorded_at") else "",
            f"管道:{r['pipeline_note']}" if r.get("pipeline_note") else "",
        ]))
        lines += ["", f"### {r['case_id']}", ""]
        if reg:
            lines.append(f"- {reg}")
        lines += ["",
                  "| draft_id | 金标 | 系统 | 判定 | 对齐 | sim | 金标文本 | 系统文本 |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in r["claims"]:
            mark = " ⚠可疑" if c.get("suspicious") else ""
            gt = c["golden_text"][:28].replace("|", "/")
            st = (c["system_text"] or "")[:28].replace("|", "/")
            lines.append(
                f"| {c['draft_id']} | {c['golden_status']} "
                f"| {_fmt(c['system_status'])} | {c['verdict']} "
                f"| {_fmt(c['align_method'])}{mark} | {_fmt(c['sim'])} "
                f"| {gt} | {st} |")
        if r["system_extra_ids"]:
            lines.append("")
            lines.append(f"未对齐系统 claim(不计分):{', '.join(r['system_extra_ids'])}")
        if r.get("hypothesis"):
            h = r["hypothesis"]
            state = {True: "一致", False: "失真",
                     None: "无主导假设"}[h["dominant_agree"]]
            lines += ["", f"假设级研判传导({state}):"
                      f"系统主导={h['system_dominant'] or '无'} / "
                      f"金标主导={h['golden_dominant'] or '无'}", "",
                      "| 假设 | 系统分 | 金标分 | 系统(证/矛/待) | 金标(证/矛/待) |",
                      "|---|---|---|---|---|"]
            for s, g in zip(h["system"], h["golden"]):
                lines.append(
                    f"| {s['label'][:26]} | {s['score']} | {g['score']} "
                    f"| {s['verified']}/{s['contradicted']}/{s['pending']}"
                    f"{' ◀主导' if s['dominant'] else ''} "
                    f"| {g['verified']}/{g['contradicted']}/{g['pending']}"
                    f"{' ◀主导' if g['dominant'] else ''} |")
        if r.get("round_yield"):
            ry = " | ".join(f"R{y['round']}:新增{y['new_claims']}/终态{y['finalized']}"
                            for y in r["round_yield"])
            lines += ["", f"轮次收益(E2):{ry}"]
        ca = r.get("citation_audit")
        if ca:
            lines += ["", f"引用审计(E3):悬空引用={'无' if ca['dangling_sections']==0 else ca['dangling_sections']}"
                      f" · 证据表{'完整' if ca['appendix_complete'] else '缺失'}"
                      f" · 摘录溯源 {ca['traceable']}/{ca['quoted_total']}"
                      f"({ca['traceable_rate']}) · {'PASS' if ca['pass'] else 'NA' if ca['pass'] is None else 'FAIL'}"]
        cal = r.get("calibration")
        if cal:
            lines += ["", f"规划校准率(改造④):验收达标 {cal['hit']}/{cal['judged']}"
                      f" 判型(共 {cal['total']} 次) rate={cal['rate']}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 套件执行

def evaluate_suite(cases_dir: str | Path, only: list[str] | None = None,
                   variant: str = "", out: Path | None = None,
                   out_json: Path | None = None,
                   out_dir: Path | None = None, work_root: str | None = None,
                   timeout: int = 180, keep_work: bool = False) -> tuple[int, dict]:
    """跑全套件:装载 -> 逐案例 subprocess+打分 -> 聚合 -> 写报告。

    返回 (exit_code, report);exit_code=0 仅当全部 enabled 案例可打分。
    """
    suite, cases = load_suite(cases_dir)
    if only:
        known = {c["case_id"] for c in cases}
        unknown = [x for x in only if x not in known]
        if unknown:
            raise ValueError(f"--only 未知案例: {', '.join(unknown)}")
        cases = [c for c in cases if c["case_id"] in only]
    eff_variant = variant or suite.get("variant", "中")
    if eff_variant not in ("粗", "中", "细"):
        raise ValueError(f"[非法] variant={eff_variant!r} 只支持 粗/中/细")
    weights = Weights(**(suite.get("weights") or {}))
    align_cfg = AlignConfig(**(suite.get("align") or {}))
    default_budget = suite.get("default_budget") or {}

    work = Path(work_root) if work_root else Path(
        tempfile.mkdtemp(prefix="sense2act_eval_"))
    work.mkdir(parents=True, exist_ok=True)
    root = Path(cases_dir)
    try:
        rows = [run_and_score(case, root / case["case_id"], work, timeout,
                              weights, align_cfg, default_budget, eff_variant)
                for case in cases]
    finally:
        if not keep_work and work_root is None:
            shutil.rmtree(work, ignore_errors=True)

    # 套件级不变量断言(P0-2):suite.json "require"={"over":0,"flip":0} 等,
    # 认识论纪律的回归门——"修好又复发"(如复述型张力回潮)在此当场红;
    # 违反不改变打分数字,但使退出码=1(可作 CI/提交前门禁)。
    require = suite.get("require") or {}
    violations = []
    if require:
        for r in rows:
            if r.get("run_status") not in _SCOREABLE:
                continue
            for key, cap in require.items():
                if r.get(key, 0) > cap:
                    violations.append(f"{r['case_id']}.{key}={r.get(key)}>{cap}")
    report = {
        "suite_id": suite.get("suite_id", ""),
        "variant": eff_variant,
        "weights": weights.as_dict(),
        "align": {"text_sim_threshold": align_cfg.text_sim_threshold,
                  "suspicious_threshold": align_cfg.suspicious_threshold,
                  "draft_id_text_floor": align_cfg.draft_id_text_floor},
        "require": require,
        "invariant": ({"require": require, "violations": violations,
                       "passed": not violations} if require else None),
        "hypothesis_concordance": None,   # 填充见下
        "rows": rows,
        "pooled": {"all": pooled_score(rows, weights),
                   "verified_only": pooled_score(rows, weights,
                                                 verified_only=True)},
    }
    hyp_all = [r["hypothesis"] for r in rows
               if r.get("run_status") in _SCOREABLE and r.get("hypothesis")]
    report["hypothesis_concordance"] = (
        {"agree": sum(1 for h in hyp_all if h["dominant_agree"]),
         "total": len(hyp_all)} if hyp_all else None)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_report_md(
            suite_id=report["suite_id"], variant=eff_variant, weights=weights,
            align_cfg=align_cfg, rows=rows, pooled_all=report["pooled"]["all"],
            pooled_verified=report["pooled"]["verified_only"],
            invariant=report["invariant"]),
            encoding="utf-8")
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in rows:
            if r.get("run_status") in _SCOREABLE:
                (out_dir / f"{r['case_id']}.json").write_text(
                    json.dumps(r, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    ok = (all(r.get("run_status") in (*_SCOREABLE, *_UNRUN) for r in rows)
          and (report["invariant"] is None or report["invariant"]["passed"]))
    return (0 if ok else 1), report


def main(args) -> int:
    """CLI 入口(cli.py 解析后的 namespace):含 variant/only 门禁与摘要打印。"""
    only = [s.strip() for s in (getattr(args, "only", "") or "").split(",")
            if s.strip()]
    try:
        code, report = evaluate_suite(
            args.suite, only=only or None, variant=getattr(args, "variant", ""),
            out=Path(args.out), out_json=Path(args.out_json),
            out_dir=Path(args.out_dir),
            work_root=(getattr(args, "work_root", "") or None),
            timeout=args.timeout, keep_work=getattr(args, "keep_work", False))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[评测] {exc}", file=sys.stderr)
        return 2
    print(f"[评测] 套件={report['suite_id']} variant={report['variant']}"
          f" 案例数={len(report['rows'])}")
    for r in report["rows"]:
        state = r.get("run_status")
        extra = (f" A_w={r['weighted_agreement']}"
                 if r.get("weighted_agreement") is not None else "")
        print(f"  - {r['case_id']}: {state}{extra}")
    inv = report.get("invariant")
    if inv:
        state = "PASS" if inv["passed"] else f"VIOLATION {inv['violations']}"
        print(f"[评测] 不变量门 require={inv['require']}: {state}")
    print(f"[评测] 退出码={code}")
    return code
