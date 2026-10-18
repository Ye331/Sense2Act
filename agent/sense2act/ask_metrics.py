"""主动澄清指标四件套(P1-8 档C 澄清实验台,2026-10-09 方案 §3.3)。零 LLM,纯台账读取。

指标族来源 = Clarify When Necessary(NAACL 2025 Findings:固定交互预算性能/AUROC)
+ AskToAct(EMNLP 2025:IR/ICR/CPS 人格化评测)【文献依据】;
在台账上的具体操作化 = 本项目设计【本项目设计】,公式载明于各函数 docstring。

刻意独立成模块、不并入 evaluate.py:该文件是 2026-10-04 多会话并发改写事故点。
"""
from __future__ import annotations

import json
import random
import sqlite3

# clarifier 的"问答行"payload.mode(排除审计行 askgate_passed/askgate_blocked/
# human_adjudication/adjudication_blocked——它们同属 kind='澄清' 但不是问人本身)
ASK_PAYLOAD_MODES = {
    "headless_auto", "interactive", "mixed_fallback",
    "interactive_eof_fallback", "replay",
}


def _is_ask_row(payload: dict) -> bool:
    mode = payload.get("mode", "")
    return mode in ASK_PAYLOAD_MODES or mode.startswith("simulated_")


def extract_ask_metrics(log_rows: list, total_rounds: int) -> dict:
    """从 decision_log 行提取四件套(一次问人事件=一格)。

    IR  = 问人次(每任务问人轮数,要低;AskToAct Interaction Rounds);
    ICR = 裁决应用数 / 问人次(覆盖率,要高;AskToAct Intent Coverage Rate 的
          台账操作化:问了且答案成功落到台账);
    CE  = 裁决应用数 / 总轮数(轮次效率,要高);
    CPS = 2·ICR·CE/(ICR+CE)(调和均值,要高;两者偏废任一则趋零)。
    附各审计行计数(blocked/审计线),供错误构成分析。
    """
    asks = applied = adj_blocked = gate_blocked = gate_passed = 0
    for row in log_rows:
        if row["kind"] != "澄清":
            continue
        payload = json.loads(row["payload_json"])
        mode = payload.get("mode", "")
        if _is_ask_row(payload):
            asks += 1
        elif mode == "human_adjudication":
            applied += 1
        elif mode == "adjudication_blocked":
            adj_blocked += 1
        elif mode == "askgate_blocked":
            gate_blocked += 1
        elif mode == "askgate_passed":
            gate_passed += 1
    icr = (applied / asks) if asks else 0.0
    ce = (applied / total_rounds) if total_rounds else 0.0
    cps = (2 * icr * ce / (icr + ce)) if (icr + ce) else 0.0
    return {
        "asks": asks, "applied": applied,
        "adjudication_blocked": adj_blocked,
        "askgate_blocked": gate_blocked, "askgate_passed": gate_passed,
        "ir": asks, "icr": round(icr, 4), "ce": round(ce, 4),
        "cps": round(cps, 4),
    }


def gate_realism_samples(log_rows: list) -> list[dict]:
    """抽取门1 评分样本(AUROC 原料):每次"评估过分的问询机会"一条。

    返回 [{round, mode: passed|blocked, realism, gap, question}];
    realism = max(0, θ-gap)(askgate.py 落账),拦下行恒 0。
    label(问了会否翻正)由调用方按金标判定后配对,本模块不算金标。
    """
    out: list[dict] = []
    for row in log_rows:
        if row["kind"] != "澄清":
            continue
        payload = json.loads(row["payload_json"])
        mode = payload.get("mode", "")
        if mode in ("askgate_passed", "askgate_blocked"):
            out.append({
                "round": row["round"], "mode": mode,
                "realism": payload.get("realism", 0),
                "gap": payload.get("gap"),
                "question": payload.get("question", ""),
            })
    return out


def auroc(samples: list[tuple[float, int]]) -> float | None:
    """rank-based AUC(带并列取平均秩);某类为空返回 None。

    samples = (score, label),label=1 表示"问了会翻正"(金标判定由调用方给)。
    """
    pos = [s for s, label in samples if label == 1]
    neg = [s for s, label in samples if label == 0]
    if not pos or not neg:
        return None
    ranked = sorted(pos + neg)
    def avg_rank(value: float) -> float:
        idx = [i + 1 for i, v in enumerate(ranked) if v == value]
        return sum(idx) / len(idx)
    sum_pos = sum(avg_rank(s) for s in pos)
    n_pos, n_neg = len(pos), len(neg)
    return (sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def paired_gain(treated: list[bool], control: list[bool]) -> dict:
    """预算内增益(方案 §3.3 指标①):固定问人预算 b 下两臂的成功率差。

    输入为每格"主导假设是否命中金标"(或任一二值成功判据);
    预算 b 由管线既有 ask_max_per_task 承担,本函数只做两臂对比。
    """
    def rate(xs: list[bool]) -> float:
        return (sum(1 for x in xs if x) / len(xs)) if xs else 0.0
    t, c = rate(treated), rate(control)
    return {"treated_rate": round(t, 4), "control_rate": round(c, 4),
            "gain": round(t - c, 4)}


def permutation_test(a: list[float], b: list[float],
                     n_perm: int = 20000, seed: int = 20261010) -> dict:
    """两臂均值差的随机置换检验(双侧)。仓库首个置换工具(P3,2026-10-10;
    sweep3d 的置换检验当时是会话内手算,未落代码)。

    a/b = 两臂逐格读数(成功判据 0/1 或任意标量);原假设=两臂同分布。
    固定种子可复现;n_perm 足够大时 p 分辨率 ≈ 1/(n_perm+1)。
    空臂返回 p=None(不可检验,调用方如实标注)。
    """
    if not a or not b:
        return {"diff": 0.0, "p_two_sided": None}
    pooled = list(a) + list(b)
    n_a = len(a)
    obs = sum(a) / n_a - sum(b) / len(b)
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(pooled)
        diff = sum(pooled[:n_a]) / n_a - sum(pooled[n_a:]) / len(b)
        if abs(diff) >= abs(obs) - 1e-12:
            count += 1
    return {"diff": round(obs, 4),
            "p_two_sided": round((count + 1) / (n_perm + 1), 5)}


def extract_from_db(db_path: str, event_id: str) -> dict:
    """db 直读打包(裸 sqlite3,不经 LedgerRepository——只读诊断零依赖)。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT round, kind, payload_json FROM decision_log"
            " WHERE event_id=? ORDER BY id", (event_id,)).fetchall()
        rounds = conn.execute(
            "SELECT COALESCE(MAX(round), 0) FROM decision_log WHERE event_id=?",
            (event_id,)).fetchone()[0]
        metrics = extract_ask_metrics(rows, rounds)
        metrics["total_rounds"] = rounds
        metrics["gate_samples"] = gate_realism_samples(rows)
        return metrics
    finally:
        conn.close()
