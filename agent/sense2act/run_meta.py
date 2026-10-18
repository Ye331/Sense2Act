"""运行参数旁车文件(.meta.json):录制件 <-> 运行参数的配对契约(P0 防再犯)。

背景(2026-10-05 失真排查,会话记录第十五节):live 用 --assoc entity_text 录制、
评测回放走默认 entity 档 → 证据挂账集不同(23 vs 15) → S6 验票失败丢失翻正、
错位挂账触发危险翻正——判决级回放**静默**失真。配对纪律第三条:运行参数与
管道版本同等必须配对。

机制:--record 落盘时把运行参数写在录制件旁(<record>.meta.json)。**不插响应
序列内**(ReplayLLM 按 stage 顺序消费,插入即错位)。回放(--replay)发现 meta
且参数不一致 → 硬错误(参数失真是静默毒药,不允许带病回放);评测(run_case)
发现 meta → 以 meta 为真值自动对齐运行参数并在结果 note 留痕(评测的目的是
忠实复现录制时刻)。无 meta 的旧录制件放行(兼容存量)。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# 影响管道行为的运行参数(录制时写入,回放时比对/对齐)
# ask_mode/ask_gate(P1-8,2026-10-09):问人档位与门1 开关也是行为参数
# (交互录制的人答回放必须吃录制值;门 on/off 改变问询是否发生)。
# sim_persona/sim_seed(P2):模拟用户人格与随机种子——人格改变答案本身
# (careless 伪随机),入配对。
# 注意:ask_answers(人答数据)不入此表——它是数据非参数,由 cli 以 extra
# 附载进同一 meta json,只供回放构造 ReplayClarifier,mismatches/对齐层不消费。
META_KEYS = ("variant", "assoc", "max_rounds", "max_llm_calls",
             "max_collect_calls", "disc", "interleave", "calibration",
             "ask_mode", "ask_gate", "sim_persona", "sim_seed", "ask_final",
             "rumor_meta_seed")


def meta_path(record_path: str | Path) -> Path:
    """旁车文件路径:<record>.meta.json(与录制件同目录同名加后缀)。"""
    return Path(str(record_path) + ".meta.json")


def _norm_variant(v: object) -> str:
    """variant 空串=默认中档(与 pipeline 装配口径一致),归一后再比。"""
    return (v or "中") if isinstance(v, str) else "中"


def _snapshot(args) -> dict:
    """从 argparse namespace 提取参数快照(缺键容忍,值做归一)。"""
    snap: dict = {}
    for key in META_KEYS:
        if key == "interleave":
            # CLI 形态是 --no-interleave(store_true),反推布尔
            val = (not args.no_interleave) if hasattr(args, "no_interleave") \
                else bool(getattr(args, "interleave", None))
        else:
            val = getattr(args, key, None)
            if key == "variant":
                val = _norm_variant(val)
            elif key in ("disc", "calibration", "ask_gate", "ask_final",
                         "rumor_meta_seed"):
                val = (val == "on") if isinstance(val, str) else bool(val)
        snap[key] = val
    return snap


def write_run_meta(record_path: str | Path, args,
                   extra: dict | None = None) -> Path:
    """录制落盘时写参数旁车文件(--record 场景调用)。

    extra:非参数数据的附载键(如 ask_answers 人答序列)——与参数同文件,
    但不入 META_KEYS,配对校验/评测对齐永不消费。
    """
    meta = _snapshot(args)
    if extra:
        meta.update(extra)
    meta["recorded_at"] = datetime.now().isoformat(timespec="seconds")
    path = meta_path(record_path)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def read_run_meta(record_path: str | Path) -> dict | None:
    """读参数旁车文件;不存在(旧录制件)返回 None。"""
    path = meta_path(record_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mismatches(meta: dict, args) -> dict[str, tuple]:
    """当前运行参数与录制 meta 的不一致项:{key: (meta值, 当前值)}。

    meta 中为 None 的键(录制时未记录)跳过;variant 归一后比较。
    """
    snap = _snapshot(args)
    out: dict[str, tuple] = {}
    for key in META_KEYS:
        mv = meta.get(key)
        if mv is None:
            continue
        if key == "variant":
            if _norm_variant(mv) != _norm_variant(snap[key]):
                out[key] = (mv, snap[key])
        elif mv != snap[key]:
            out[key] = (mv, snap[key])
    return out


def align_from_meta(case: dict, meta: dict) -> str:
    """评测回放前把 case.run_args 对齐到录制 meta(原地修改,返回留痕文案)。

    只对齐**行为参数**(assoc/max_rounds/max_llm_calls/max_collect_calls)——
    它们改变挂账集与循环轮数;variant 在回放中被冻结响应吸收(按 stage 消费,
    与档位无关),只比对报告不强改。case 显式写值与 meta 冲突时同样以 meta
    为准(评测的目的是忠实复现录制时刻)。
    """
    ra = case.setdefault("run_args", {})
    fixed: list[str] = []
    for key in ("assoc", "max_rounds", "max_llm_calls", "max_collect_calls",
                "ask_mode", "ask_gate", "sim_persona", "sim_seed", "ask_final"):
        mv = meta.get(key)
        if mv is not None and ra.get(key) != mv:
            fixed.append(f"{key}:{ra.get(key)}->{mv}")
            ra[key] = mv
    return ("参数按录制件meta对齐: " + "; ".join(fixed)) if fixed else ""
