"""上游信号 → 调查信封装配器(常驻装配器待办的落地)。

支持两种上游格式,按条目形状自动分派:

v1 批量导出(schema_version 0.1-draft,examples/upstream_topk/):每条含
project/scores/historical_context/trigger/sources/scoring_context 六块,仅 tender。

v2 统一提交外层(《信号输出字段与Agent对接格式.md》,examples/upstream_submit/):
顶层 {signal_id, company_id, kind, payload},payload 单层;kind tender|rumor。
- tender:procurement_project_id 是调查关联键(→信封 projectId 续查路由);
  历史明细降为上游内部保存,关键依据在 signal_reason 散文中;
- rumor:payload 与 RumorSignal 字段几乎一一对应,近直通映射。

分权纪律的装配版:纯确定性代码,零 LLM。硬规则:
1. 白名单映射,不整块直灌——信封 extra=forbid,上游未建模键整灌即炸;
2. v1 检测器解释由结构化字段确定性合成,不解析上游散文;v2 的历史细节只存在于
   signal_reason 散文(结构块已上游内部化),故 relevance.explanation 收全文
   (它是"调查原因"本体),其余检测器解释由 score+reliable 确定性合成短句
   ——仍然不解析散文;
3. 过程量不进信封(置信不继承),只落 manifest 供人工分诊;
4. score null → 检测器整个缺省(不造 0 分,"分数未知填 null 不能填为零");
   越界 → ValueError 显式报错,不静默截断。

event_id:tender 留空(investigate 时按 procurement_project_id 派生,续查可能
并入旧事件,派生值并非最终值);rumor 预填 "evt_"+signal_id——rumor 无 project
路由键,derive_event_id 对所有 rumor 返回同一个 "evt_signal" 会撞事件主键,
signal_id 是上游唯一 ID,天然无碰撞。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from .models.envelope import (
    AmountHistory,
    ClueCard,
    DetectorScore,
    FrequencyHistory,
    InvestigationEnvelope,
    NoveltyHistory,
    RumorSignal,
    SimilarProjectRef,
    TenderSignal,
)

_SUPPORTED_SCHEMA = "0.1"    # v1 批量文件的版本门槛(仅当文件带 schema_version 时校验)


# ---------- 批加载 ----------

def load_batch(path: str | Path) -> dict:
    """读信号文件并归一为 {"signals": [...]}(条目 v1/v2 形状混合皆可);
    失败 raise ValueError(FileNotFoundError 透传)。"""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):                      # v2 提交列表
        signals = data
    elif isinstance(data, dict) and isinstance(data.get("payload"), dict):
        signals = [data]                            # v2 单条提交
    elif isinstance(data, dict) and isinstance(data.get("signals"), list):
        version = str(data.get("schema_version", ""))
        if version and not version.startswith(_SUPPORTED_SCHEMA):
            raise ValueError(f"{p}: 不支持的 schema_version {version!r}"
                             f"(本装配器支持 {_SUPPORTED_SCHEMA}.x,请核对字段映射)")
        signals = data["signals"]                   # v1 批量(或 v2 批量容器)
    else:
        raise ValueError(f"{p}: 无法识别的信号文件结构"
                         "(支持:提交对象/提交列表/{signals: [...]})")
    if not signals:
        raise ValueError(f"{p}: signals 缺失或为空")
    return {"signals": signals}


# ---------- 数值与结构块归一 ----------

def _num(value: object, label: str) -> float | None:
    """数值归一:缺失/None → None;非数值 → ValueError(显式报错)。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            return float(str(value))            # 宽容字符串数字
        except ValueError as exc:
            raise ValueError(f"{label} 不是数值: {value!r}") from exc
    return float(value)


def _int(value: object, label: str) -> int | None:
    f = _num(value, label)
    return None if f is None else int(f)


def _score(value: object, label: str) -> float | None:
    """检测器分数:None → None(整个检测器缺省);越界 → ValueError,不截断。"""
    if value is None:
        return None
    v = _num(value, label)
    assert v is not None
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"{label} 超出 [0,1]: {v}(不静默截断)")
    return v


def _reliable(block: object, default: bool = True) -> bool:
    """historical_context.X.reliable → basis_sufficient;块缺失按 default。"""
    if not isinstance(block, dict):
        return default
    v = block.get("reliable")
    return v if isinstance(v, bool) else default


def _sim_ref(raw: object) -> SimilarProjectRef | None:
    if not isinstance(raw, dict):
        return None
    return SimilarProjectRef(
        project_id=str(raw.get("project_id") or ""),
        title=str(raw.get("title") or ""),
        publish_date=str(raw.get("publish_date") or ""),
        data_origin=str(raw.get("data_origin") or ""),
        source_url=str(raw.get("source_url") or ""),     # 上游 null → 空串
    )


def _amount_history(raw: object) -> AmountHistory | None:
    if not isinstance(raw, dict):
        return None
    return AmountHistory(
        historical_median_amount_yuan=_num(raw.get("historical_median_amount_yuan"),
                                           "amount.historical_median_amount_yuan"),
        amount_ratio=_num(raw.get("amount_ratio"), "amount.amount_ratio"),
        history_count=_int(raw.get("history_count"), "amount.history_count"),
        simulated_history_count=_int(raw.get("simulated_history_count"),
                                     "amount.simulated_history_count"))


def _novelty_history(raw: object) -> NoveltyHistory | None:
    if not isinstance(raw, dict):
        return None
    return NoveltyHistory(
        history_count=_int(raw.get("history_count"), "novelty.history_count"),
        simulated_history_count=_int(raw.get("simulated_history_count"),
                                     "novelty.simulated_history_count"),
        max_history_similarity=_num(raw.get("max_history_similarity"),
                                    "novelty.max_history_similarity"),
        most_similar_project=_sim_ref(raw.get("most_similar_project")))


def _freq_history(raw: object) -> FrequencyHistory | None:
    if not isinstance(raw, dict):
        return None
    return FrequencyHistory(
        window_days=_int(raw.get("window_days"), "frequency.window_days"),
        recent_count=_int(raw.get("recent_count"), "frequency.recent_count"),
        historical_window_average_count=_num(
            raw.get("historical_window_average_count"),
            "frequency.historical_window_average_count"),
        frequency_ratio=_num(raw.get("frequency_ratio"), "frequency.frequency_ratio"),
        historical_windows_used=_int(raw.get("historical_windows_used"),
                                     "frequency.historical_windows_used"))


# ---------- 检测器解释合成器(结构化字段 → 句子,零散文解析) ----------

def _wan(v: float) -> str:
    return f"{v / 1e4:.1f}"


def _sim_warn(count: int | None) -> str:
    return (f"历史基线含 {count} 个模拟样本,不代表真实历史证据充分。"
            if count else "")


def _amount_exp(amount_yuan: float | None, score: float,
                hist: AmountHistory | None) -> str:
    text = ("本项目未披露采购金额" if amount_yuan is None
            else f"本项目金额 {_wan(amount_yuan)} 万元")
    if hist is not None and hist.historical_median_amount_yuan is not None \
            and hist.amount_ratio is not None:
        scope = (f"过去 {hist.history_count} 条"
                 if hist.history_count is not None else "")
        text += (f",为该采购单位{scope}有效金额中位数"
                 f"({_wan(hist.historical_median_amount_yuan)} 万元)"
                 f"的 {hist.amount_ratio:.2f} 倍")
    elif hist is not None and hist.amount_ratio is not None:
        text += f",相对历史中位数倍数 {hist.amount_ratio:.2f}"
    text += f",金额异常得分 {score:.3f}。"
    if hist is not None:
        text += _sim_warn(hist.simulated_history_count)
    return text


def _novelty_exp(score: float, hist: NoveltyHistory | None) -> str:
    if hist is None:
        return f"新颖性异常得分 {score:.3f}。"
    msp = hist.most_similar_project
    if msp is not None and msp.title:
        text = f"该采购单位历史最相似项目为『{msp.title}』"
        tail = [t for t in (msp.publish_date,
                            "模拟样本" if msp.data_origin == "simulated" else "") if t]
        if tail:
            text += "(" + "/".join(tail) + ")"
        if hist.max_history_similarity is not None:
            text += f",相似度 {hist.max_history_similarity:.3f}"
    elif hist.max_history_similarity is not None:
        text = f"该采购单位历史最相似项目相似度 {hist.max_history_similarity:.3f}"
    else:
        text = "该采购单位无有效历史对照项目"
    if hist.history_count is not None:
        text += f"(比较基数 {hist.history_count} 个历史项目)"
    text += f",新颖性异常得分 {score:.3f}。"
    return text + _sim_warn(hist.simulated_history_count)


def _freq_exp(score: float, hist: FrequencyHistory | None) -> str:
    if hist is None:
        return f"频率异常得分 {score:.3f}。"
    text = ""
    if hist.window_days is not None and hist.recent_count is not None:
        text = f"截至当天近 {hist.window_days} 天该采购单位采购 {hist.recent_count} 次"
    if hist.historical_window_average_count is not None:
        base = "已完整观测历史窗口"
        if hist.historical_windows_used is not None:
            base = f"已完整观测的 {hist.historical_windows_used} 个历史窗口"
        text += f",{base}平均 {hist.historical_window_average_count:g} 次"
    if hist.frequency_ratio is not None:
        text += f",比值 {hist.frequency_ratio:.2f}"
    if not text:
        text = "频率历史依据缺失"
    text += f",频率异常得分 {score:.3f}。"
    return text


def _rel_exp(score: float, trigger: dict) -> str:
    text = f"项目与企业业务画像的语义相似度为 {score:.3f}"
    thr = trigger.get("relevance_threshold")
    if isinstance(thr, (int, float)) and not isinstance(thr, bool):
        word = "达到" if score >= float(thr) else "未达到"
        text += f",{word}准入阈值 {float(thr):.3f}"
    if str(trigger.get("evidence_status", "")) != "sufficient":
        text += ";上游标记触发依据完整度不足"
    return text + "。"


# ---------- v2 统一提交外层(kind: tender | rumor) ----------

def _flag(value: object, default: bool = True) -> bool:
    """reliable 布尔归一:缺失/非法按 default(与上游'不可用才 false'对齐)。"""
    return value if isinstance(value, bool) else default


def _compact_score_line(label: str, score: float, reliable: bool) -> str:
    basis = "历史依据可用" if reliable else "上游标记历史依据不足,不强行评分"
    return f"{label}得分 {score:.3f};{basis}。数值依据见相关性条目所附调查原因全文。"


def _tender_from_submission(payload: dict) -> InvestigationEnvelope:
    """v2 tender:payload 单层 → TenderSignal(白名单直映,历史结构块为 None)。"""
    project_id = str(payload.get("procurement_project_id")
                     or payload.get("project_id") or "").strip()
    project_name = str(payload.get("title") or "").strip()
    purchaser = str(payload.get("buyer") or "").strip()
    published_date = str(payload.get("publish_date") or "").strip()
    if not project_id:
        raise ValueError("缺少 payload.procurement_project_id(调查关联键必填)")
    if not project_name:
        raise ValueError(f"信号 {project_id} 缺少 payload.title")
    if not purchaser:
        raise ValueError(f"信号 {project_id} 缺少 payload.buyer")
    if not published_date:
        raise ValueError(f"信号 {project_id} 缺少 payload.publish_date")

    amount_yuan = _num(payload.get("amount_yuan"), "payload.amount_yuan")
    amount_score = _score(payload.get("amount_score"), "payload.amount_score")
    freq_score = _score(payload.get("frequency_score"), "payload.frequency_score")
    novelty_score = _score(payload.get("novelty_score"), "payload.novelty_score")
    rel_score = _score(payload.get("relevance_score"), "payload.relevance_score")

    amount_reliable = _flag(payload.get("amount_reliable"))
    novelty_reliable = _flag(payload.get("novelty_reliable"))
    frequency_reliable = _flag(payload.get("frequency_reliable"))
    signal_reason = str(payload.get("signal_reason") or "").strip()

    return InvestigationEnvelope(
        kind="tender",
        event_id="",                 # investigate 时按 procurement_project_id 派生
        tender=TenderSignal(
            project_id=project_id,
            project_name=project_name,
            purchaser=purchaser,
            amount_yuan=amount_yuan,
            published_date=published_date,
            domain=str(payload.get("category") or "").strip(),
            project_summary=str(payload.get("project_summary") or "").strip(),
            source_url=str(payload.get("source_url") or ""),
            # 历史明细上游内部化:结构块无来源保持 None,数值依据在 signal_reason
            relevance=None if rel_score is None else DetectorScore(
                score=rel_score,
                # signal_reason=调查原因本体(含各指标依据/历史截止日/缺失说明),
                # 收全文进相关性条目;不解析散文做逐检测器切分
                explanation=signal_reason or "上游未提供调查原因。",
                basis_sufficient=True),   # 上游明确:相关性不使用历史可靠性标记
            amount_anomaly=None if amount_score is None else DetectorScore(
                score=amount_score,
                explanation=_compact_score_line("金额变化", amount_score,
                                                amount_reliable),
                basis_sufficient=amount_reliable),
            novelty_anomaly=None if novelty_score is None else DetectorScore(
                score=novelty_score,
                explanation=_compact_score_line("采购新颖性", novelty_score,
                                                novelty_reliable),
                basis_sufficient=novelty_reliable),
            frequency_anomaly=None if freq_score is None else DetectorScore(
                score=freq_score,
                explanation=_compact_score_line("频率变化", freq_score,
                                                frequency_reliable),
                basis_sufficient=frequency_reliable),
        ))


def _rumor_from_submission(signal_id: str, payload: dict) -> InvestigationEnvelope:
    """v2 rumor:payload 与 RumorSignal 字段几乎一一对应,近直通。"""
    subject_name = str(payload.get("subject_name") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    source = str(payload.get("source") or "").strip()
    signal_time = str(payload.get("signal_time") or "").strip()
    if not subject_name:
        raise ValueError("缺少 payload.subject_name")
    if not summary:
        raise ValueError(f"信号 {subject_name} 缺少 payload.summary")
    if not source:
        raise ValueError(f"信号 {subject_name} 缺少 payload.source")
    if not signal_time:
        raise ValueError(f"信号 {subject_name} 缺少 payload.signal_time")

    clues = []
    for i, raw in enumerate(payload.get("clues") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"clues[{i}] 不是 JSON 对象")
        content = str(raw.get("content") or "").strip()
        if not content:
            raise ValueError(f"clues[{i}] 缺少 content(正文或标明为摘要的文本)")
        clues.append(ClueCard(
            clue_id=str(raw.get("clue_id") or f"clue_{i+1}"),
            source=str(raw.get("source") or source),
            platform=str(raw.get("platform") or "other"),
            title=str(raw.get("title") or ""),
            content=content,
            url=str(raw.get("url") or ""),
            published_at=str(raw.get("published_at") or ""),
            author_type=str(raw.get("author_type") or "自媒体"),
        ))

    return InvestigationEnvelope(
        kind="rumor",
        # rumor 无 project 路由键,derive_event_id 会全员撞 "evt_signal";
        # signal_id 是上游唯一 ID,预填防事件主键碰撞
        event_id=f"evt_{_safe(signal_id)}" if signal_id else "",
        rumor=RumorSignal(
            subject_name=subject_name,
            subject_credit_code=str(payload.get("subject_credit_code") or ""),
            summary=summary,
            source=source,
            signal_time=signal_time,
            watch_focus=str(payload.get("watch_focus") or ""),
            clues=clues,
        ))


def _submission_envelope(signal: dict) -> InvestigationEnvelope:
    """v2 单条提交 {signal_id, company_id, kind, payload} → 信封。"""
    payload = signal.get("payload")
    kind = str(signal.get("kind") or "").strip()
    signal_id = str(signal.get("signal_id") or "").strip()
    if kind == "tender":
        return _tender_from_submission(payload)
    if kind == "rumor":
        return _rumor_from_submission(signal_id, payload)
    raise ValueError(f"未知 kind: {kind!r}(支持 tender|rumor)")


# ---------- 单条装配(按形状分派:有 payload 即 v2,否则 v1 批量条目) ----------

def signal_to_envelope(signal: dict) -> InvestigationEnvelope:
    """一条上游信号 → 一份信封;缺关键键或数值非法 raise ValueError。"""
    if isinstance(signal.get("payload"), dict):
        return _submission_envelope(signal)

    project = signal.get("project") if isinstance(signal.get("project"), dict) else {}
    scores = signal.get("scores") if isinstance(signal.get("scores"), dict) else {}
    hist = (signal.get("historical_context")
            if isinstance(signal.get("historical_context"), dict) else {})
    trigger = signal.get("trigger") if isinstance(signal.get("trigger"), dict) else {}
    sources = signal.get("sources") if isinstance(signal.get("sources"), dict) else {}
    scoring_ctx = (signal.get("scoring_context")
                   if isinstance(signal.get("scoring_context"), dict) else {})

    project_id = str(project.get("project_id") or "").strip()
    project_name = str(project.get("title") or "").strip()
    purchaser = str(project.get("buyer") or "").strip()
    published_date = str(project.get("publish_date") or "").strip()
    if not project_id:
        raise ValueError("缺少 project.project_id(续查路由键必填)")
    if not project_name:
        raise ValueError(f"信号 {project_id} 缺少 project.title")
    if not purchaser:
        raise ValueError(f"信号 {project_id} 缺少 project.buyer")
    if not published_date:
        raise ValueError(f"信号 {project_id} 缺少 project.publish_date")

    amount_yuan = _num(project.get("amount_yuan"), "project.amount_yuan")
    amount_hist = _amount_history(hist.get("amount"))
    novelty_hist = _novelty_history(hist.get("novelty"))
    freq_hist = _freq_history(hist.get("frequency"))

    amount_score = _score(scores.get("amount_score"), "scores.amount_score")
    freq_score = _score(scores.get("frequency_score"), "scores.frequency_score")
    novelty_score = _score(scores.get("novelty_score"), "scores.novelty_score")
    rel_score = _score(scores.get("relevance_score"), "scores.relevance_score")

    return InvestigationEnvelope(
        kind="tender",
        event_id="",                     # 留空:investigate 时按 derive_event_id 派生
        tender=TenderSignal(
            project_id=project_id,
            project_name=project_name,
            purchaser=purchaser,
            amount_yuan=amount_yuan,
            published_date=published_date,
            domain=str(project.get("category") or "").strip(),
            project_summary=str(project.get("project_summary") or "").strip(),
            source_url=str(sources.get("source_url") or ""),
            content_source_note=str(sources.get("content_source_note") or ""),
            company_profile=str(scoring_ctx.get("company_profile_used") or ""),
            amount_history=amount_hist,
            novelty_history=novelty_hist,
            frequency_history=freq_hist,
            amount_anomaly=None if amount_score is None else DetectorScore(
                score=amount_score,
                explanation=_amount_exp(amount_yuan, amount_score, amount_hist),
                basis_sufficient=_reliable(hist.get("amount"))),
            frequency_anomaly=None if freq_score is None else DetectorScore(
                score=freq_score,
                explanation=_freq_exp(freq_score, freq_hist),
                basis_sufficient=_reliable(hist.get("frequency"))),
            novelty_anomaly=None if novelty_score is None else DetectorScore(
                score=novelty_score,
                explanation=_novelty_exp(novelty_score, novelty_hist),
                basis_sufficient=_reliable(hist.get("novelty"))),
            relevance=None if rel_score is None else DetectorScore(
                score=rel_score,
                explanation=_rel_exp(rel_score, trigger),
                basis_sufficient=str(trigger.get("evidence_status", "")) == "sufficient"),
        ))


# ---------- 批装配(纯函数,不落盘) ----------

def assemble_batch(batch: dict) -> list[dict]:
    """逐条装配;单条失败不拖垮整批(status=error 记原因)。

    返回 [{index, project_id, event_id, envelope|None, status, error, warning?}]。
    批内 project_id 重复不硬失败:续查语义下会并入同一事件,manifest 标警告。
    """
    rows: list[dict] = []
    seen: dict[str, int] = {}
    for i, signal in enumerate(batch.get("signals") or []):
        row: dict = {"index": i, "project_id": "", "event_id": "",
                     "envelope": None, "status": "ok", "error": ""}
        try:
            if not isinstance(signal, dict):
                raise ValueError("信号条目不是 JSON 对象")
            env = signal_to_envelope(signal)
            if env.tender is not None:
                identity = env.tender.project_id
            elif env.rumor is not None:
                identity = env.rumor.subject_name
            else:
                identity = ""
            row["project_id"] = identity
            row["event_id"] = env.event_id or env.derive_event_id()
            if signal.get("signal_id"):
                row["signal_id"] = str(signal["signal_id"])
            if signal.get("company_id"):
                row["company_id"] = str(signal["company_id"])
            if identity in seen:
                row["warning"] = (f"标识 {identity} 与第 {seen[identity]} 条重复"
                                  "(续查语义将并入同一事件)")
            seen.setdefault(identity, i)
            row["envelope"] = env
        except ValueError as exc:
            row["status"] = "error"
            row["error"] = f"signals[{i}]: {exc}"
        rows.append(row)
    return rows


def _safe(project_id: str) -> str:
    """文件名安全化(与 derive_event_id 同一变换,截 32 位)。"""
    return "".join(ch if ch.isalnum() else "_" for ch in project_id)[:32]


# ---------- CLI 入口 ----------

def main(args) -> int:
    """CLI 入口(cli.py 解析后的 namespace):批装配 → 落盘信封 + manifest。"""
    try:
        batch = load_batch(args.batch)
    except (OSError, ValueError) as exc:      # 含 FileNotFoundError
        print(f"[装配] {exc}", file=sys.stderr)
        return 2
    batch_path = Path(args.batch)
    out_dir = (Path(args.out_dir) if getattr(args, "out_dir", "")
               else Path("runs/assembled") / batch_path.stem)
    rows = assemble_batch(batch)
    ok_rows = [r for r in rows if r["status"] == "ok"]

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_batch": str(batch_path),
        "schema_version": str(batch.get("schema_version", "")),
        "source_result_file": str(batch.get("source_result_file") or ""),
        "replay_period": batch.get("replay_period"),
        "selection_method": str((batch.get("selection") or {}).get("method") or ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(rows),
        "ok_count": len(ok_rows),
        "rows": [],
    }
    for r in rows:
        entry: dict = {"index": r["index"], "project_id": r["project_id"],
                       "event_id": r["event_id"], "status": r["status"]}
        for k in ("signal_id", "company_id", "warning"):
            if r.get(k):
                entry[k] = r[k]
        if r.get("error"):
            entry["error"] = r["error"]
        sig = batch["signals"][r["index"]] if r["index"] < len(batch["signals"]) else {}
        scores = sig.get("scores") if isinstance(sig.get("scores"), dict) else {}
        trigger = sig.get("trigger") if isinstance(sig.get("trigger"), dict) else {}
        if scores.get("priority_score") is not None:          # 分诊量只进 manifest
            entry["priority_score"] = scores["priority_score"]
        if trigger.get("priority_label"):
            entry["priority_label"] = trigger["priority_label"]
        if r.get("warning"):
            entry["warning"] = r["warning"]
        if r["status"] == "ok":
            fname = f"signal_{r['index']:02d}_{_safe(r['project_id'])}.json"
            (out_dir / fname).write_text(
                r["envelope"].model_dump_json(by_alias=True, indent=2),
                encoding="utf-8")
            entry["file"] = fname
        manifest["rows"].append(entry)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[装配] 批={batch_path.name} 共 {len(rows)} 条,"
          f"成功 {len(ok_rows)},失败 {len(rows) - len(ok_rows)} → {out_dir}")
    for entry in manifest["rows"]:
        mark = "OK" if entry["status"] == "ok" else "ERR"
        detail = entry.get("file") or entry.get("error", "")
        warn = f"({entry['warning']})" if entry.get("warning") else ""
        print(f"  - [{mark}] #{entry['index']} {entry['project_id'] or '?'}"
              f" → {detail}{warn}")
    return 0 if not manifest["rows"] or all(
        e["status"] == "ok" for e in manifest["rows"]) else 1
