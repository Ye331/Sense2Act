"""ESS 呈现层(阶段二,2026-10-21)。纯函数零 LLM;append-only 挂载。

挂载点=stages/s7_report.py::_ess_finalize 成功路径尾部,向 report.md /
brief.md 追加「证据强度」节——**不碰 _deliver/_render_* 渲染层**(图片线
并行施工区,合并面≈零;照降级注记 append 先例)。--ess off 时本模块根本
不被调用(呈现零痕迹)。

四项定稿(2026-10-21,拍板理由;①已于 2026-10-26 升级):
① 呈现门控(原 §12.4 kind 分流退役):**按 priority 降序排列,重点条
   (P≥4)逐条给证据强度徽章,其余行只列条目+三态**——kind 分流的病根
   =无差别逐条(tender 自证条全是「中」会制造噪音),优先级门控从根上
   解决,rumor/tender 统一形态(用户拍板 2026-10-25 讨论);
② 概率禁词(方案 §九①):呈现文本永不含禁词表(_BADWORDS;测试锁死渲染
   输出,不做运行时校验——模板是确定的,校验属于测试不属于运行时);措辞
   钉死「证据强度」;数字不裸奔(§九②):分数必带「/100」+锚点定语,
   层级=锚点名>数字(截图裁剪带定语走);
③ 校准带(方案 §八):N<30 零输出(「N≥30 才显示」;当前翻转正例=0,
   呈现必空;分桶 75/45/8 与频率读数等 E7 生存分析有数后再接入);
④ 降级形态:沿用阶段一终态(规则表缺失→仅「本报告未评」注记,不装 0 分;
   off→零痕迹),本模块只在计分成功路径被调用。

决策卡=低强度警示(人读):已证实∧score<中档下界=回退风险条(高分被误读
代价远大于低分,§九);待证实计数+Open Questions 指引。分诊路由人消费强度
属合法「给人读」一翼——Goodhart 防的是行为回路自动消费,不防人看。

分数构成审计(方案 §12.2):自证锚点占比 vs corpus_bound 占比,错位超容差
→黄牌(刷分在交付物上现形)。tolerance=显式参数默认 0.1:呈现诊断参数,
**不入计分规则表**(ess_rules.json 保持 ess-0.2 计分语义纯净,505 门禁的
版本断言零改动);日后若需运营期可调,再入表并升版本。
"""
from __future__ import annotations

from typing import Any

# 概率禁词表(§九①:永不单用"可信度"对外;阶段演化 v0=全面禁概率化措辞)
_BADWORDS = ("概率", "可能性", "把握", "置信度", "可信度",
             "probability", "confidence", "likelihood")

_HEADER = "### 附录·证据强度(ESS,内部工作底稿,不作对外评级)"


def anchor_line(c: dict[str, Any]) -> str:
    """单条强度徽章:锚点名>数字,数字必带 /100(禁词表外措辞)。"""
    if c["anchor_name"] == "冲突":
        return "冲突(不评分)"
    if c["score"] is None:
        return c["anchor_name"]
    return f"{c['anchor_name']}·{c['score']}/100"


def composition_audit(ess_result: dict, tolerance: float = 0.1) -> dict:
    """分数构成审计(§12.2):自证锚占比 vs corpus_bound 占比的错位检测。

    错位语义:自证锚(中(自证))占比显著高于 corpus_bound 标注占比 →
    有 claim 拿了自证形态的锚点却未被标注 corpus_bound=刷分信号,黄牌。
    """
    claims = [c for c in ess_result["claims"] if c["anchor_name"] != "冲突"]
    n = len(claims)
    if n == 0:
        return {"n": 0, "self_anchor_ratio": 0.0, "corpus_bound_ratio": 0.0,
                "misalign": 0.0, "flag": False}
    self_n = sum(1 for c in claims if c["anchor_name"] == "中(自证)")
    cb_n = sum(1 for c in claims if c["corpus_bound"])
    self_r, cb_r = self_n / n, cb_n / n
    mis = self_r - cb_r
    return {"n": n, "self_anchor_ratio": round(self_r, 4),
            "corpus_bound_ratio": round(cb_r, 4), "misalign": round(mis, 4),
            "flag": mis > tolerance}


def _content(c: dict, gists: dict | None, n: int = 40) -> str:
    """条目内容短摘(用户可读,2026-10-27):gist 短名优先(空=fallback
    截断),md 表格单元格内竖线折竖括号防破表。"""
    text = (gists or {}).get(c["draft_id"]) or (c.get("text") or "")
    if len(text) > n:
        text = text[:n] + "…"
    return text.replace("|", "∣")


def render_appendix(ess_result: dict, kind: str = "",
                    audit_tolerance: float = 0.1,
                    priority_min: int = 4,
                    gists: dict | None = None) -> str:
    """「证据强度」追加节(2026-10-26 升级:优先级门控逐条强度)。

    用户拍板(2026-10-25 讨论):附录内 claim 按 priority 降序排列,
    重点条(P>=priority_min)逐条给证据强度徽章,其余行只列条目+三态
    (逐条分数在 ess.json 底稿)——治徽章噪音病根(信封载明类自证条
    全是「中」档,无差别逐条=淹没信号)。**旧 kind 分流退役**:tender
    低调/rumor 显性的病根就是无差别逐条,优先级门控从根上解决,两类信封
    统一形态;kind 参数保留兼容旧调用但不再分流。分选规则(确定性):
    priority 降 → score 降(None 最后)→ draft_id 升(同序稳定)。
    回退风险(已证实∧<中档)不限优先级全量点名(正确性信号,§九:
    高分被误读代价远大于低分)。禁词/锚点名>数字//100 纪律不变;
    校准带:N<30 零输出(§八)不变。
    """
    claims = ess_result.get("claims", [])
    rules_tier_mid = 45  # 中档下界读数(与 ess_rules tiers.中 同值;呈现阈值)
    low_verified = [c for c in claims
                    if c["status"] == "已证实" and c["score"] is not None
                    and c["score"] < rules_tier_mid]
    pending = [c for c in claims if c["status"] == "待证实"]
    conflict = [c for c in claims if c["anchor_name"] == "冲突"]
    audit = composition_audit(ess_result, audit_tolerance)
    ordered = sorted(
        claims,
        key=lambda c: (-(c.get("priority") or 0),
                       0 if c["score"] is None else -c["score"],
                       c["draft_id"]))

    lines = ["", _HEADER, ""]
    # ---- 决策卡(回退风险不限优先级;人读) ----
    lines.append("**决策卡**(低强度警示):")
    if low_verified:
        lines.append(f"- ⚠ 已证实但强度低于中档 {len(low_verified)} 条"
                     "(反方证据压制,回退风险):"
                     + ";".join(f"条目 {c['draft_id']}「{_content(c, gists, 16)}」"
                                f"({c['score']}/100)"
                                for c in low_verified))
    else:
        lines.append("- 已证实条无一低于中档,无回退风险条。")
    lines.append(f"- 待证实 {len(pending)} 条(见 Open Questions,如实保留"
                 f"不做推测);冲突 {len(conflict)} 条(不评分)。")
    lines.append("")
    # ---- 优先级门控逐条强度(2026-10-26;kind 分流退役) ----
    # 列头/措辞用户可读化(2026-10-27:英文字段不裸奔——claim→条目,
    # P→优先级,三态→核查状态;「内容」列=gist 短名或截断,全文见附录 B)
    lines.append(f"以下按优先级排列;「内容」列为条目短摘(全文见附录 B"
                 f"核对证书与正文);优先级≥{priority_min}的重点条给出"
                 "证据强度,其余条的逐条分数见 ess.json(内部底稿):")
    lines.append("")
    lines.append("| 条目 | 内容 | 优先级 | 核查状态 | 证据强度 |")
    lines.append("|---|---|---|---|---|")
    for c in ordered:
        p = int(c.get("priority") or 0)
        badge = anchor_line(c) if p >= priority_min else "—"
        lines.append(
            f"| {c['draft_id']} | {_content(c, gists)} | {p} "
            f"| {c['status']} | {badge} |")
    lines.append("")
    lines.append("> 条目全文见附录 B 核对证书(编号对账)与正文对应节。")
    dist = _anchor_dist(claims)
    if dist:
        lines.append("")
        lines.append("全量强度构成=" + "、".join(f"{a}×{n}" for a, n in dist)
                     + "。")
    # ---- 分数构成审计(§12.2) ----
    if audit["n"]:
        mark = "⚠ 黄牌:构成错位超容差(刷分信号,需人工复核)" if audit["flag"] \
            else "无错位"
        lines.append(f"- 审计:自证锚占比 {audit['self_anchor_ratio']:g} / "
                     f"corpus_bound 占比 {audit['corpus_bound_ratio']:g}"
                     f" → {mark}。")
    # 校准带:N<30 零输出(③;接入点见模块 docstring)
    return "\r\n".join(lines) + "\r\n"


def _anchor_dist(claims: list[dict]) -> list[tuple[str, int]]:
    dist: dict[str, int] = {}
    for c in claims:
        if c["score"] is None:
            continue
        dist[c["anchor_name"]] = dist.get(c["anchor_name"], 0) + 1
    return sorted(dist.items(), key=lambda kv: -kv[1])


def has_badword(text: str) -> str | None:
    """禁词检查(测试用):返回首个命中的禁词,无命中返回 None。"""
    low = text.lower()
    for w in _BADWORDS:
        if w in low:
            return w
    return None
