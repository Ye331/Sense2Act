"""Delta 呈现层(渲染批次,2026-10-27)。纯函数零 LLM;头部注入。

挂载=stages/s7_report.py 三产物头部经 kwarg 注入(默认 None=零痕迹,照
assets 先例);数据层=delta_report.py(纯读零 LLM,不 import 渲染——纪律
不动,本模块方向合法地消费它)。锚随本批次接线:finalize 首跑写结算锚,
次跑起头部出修正条件句;首跑无锚=三产物零痕迹。

话术拍板(2026-10-27,用户):头部节=「较上次报告的修正(时点锚 ts)」+
总览行(修正 N/新增 M/不变 K)+逐条修正条件句(旧态→新态+成因+依据+来源)
+新增条(含派生谱系)+触及假设行;零变化=总览行并"无状态变化";brief 只
出一行总览(细节见 report.md)。html 侧消费同一 render_md 文本过白名单
转换(s7 侧 _appendix_body;与 ESS/K5 节同手法)。

cause 中文映射:矛盾翻转→新矛盾证据/时效翻转→证据时效刷新/状态刷新→
状态刷新/空→澄清(schema CHECK 枚举;附录 C 的 `cause or '澄清'` 同源)。
"""
from __future__ import annotations

_CAUSE_CN = {"矛盾翻转": "新矛盾证据", "时效翻转": "证据时效刷新",
             "状态刷新": "状态刷新", "": "澄清"}


def _cause_cn(cause: str) -> str:
    return _CAUSE_CN.get(cause or "", "澄清")


def _clip(text: str, n: int = 40) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def _short(eid: str) -> str:
    """证据短标({event}:E7 → E7;与报告通篇 [E7] 同一口径)。"""
    return (eid or "").rsplit(":", 1)[-1] or "—"


def _cn_ts(ts: str) -> str:
    """ISO 时间戳 → 用户可读("2026-10-27T13:38:44+00:00"→
    "2026-10-27 13:38 UTC";英文字段不裸奔,2026-10-27)。"""
    return (ts or "")[:16].replace("T", " ") + " UTC"


def _cell_text(text: str, gists: dict | None, key: str, n: int = 40) -> str:
    """条目内容:gist 短名优先(空=fallback 截断);竖线防御。"""
    t = (gists or {}).get(key) or (text or "")
    if len(t) > n:
        t = t[:n] + "…"
    return t.replace("|", "∣")


def _modified_line(m: dict, gists: dict | None = None) -> str:
    """逐条修正条件句(机械模板,拍板话术):旧态 → 新态(成因,依据,轮次;
    来源)。证据锚缺失(如人答触发)时只写成因。"""
    cause = f"{_cause_cn(m['cause'])},依据 证据{_short(m['evidence_id'])}," \
            f"轮次{m['round']}" if m["evidence_id"] else _cause_cn(m["cause"])
    src = ""
    ev = m.get("evidence")
    if ev and ev.get("platform"):
        src = f";来源 {ev['platform']}"
    return (f"- 「{_cell_text(m['text'], gists, m['draft_id'])}」"
            f"(条目 {m['draft_id']}):"
            f"{m['old_status']} → {m['new_status']}({cause}{src})")


def render_md(delta: dict, gists: dict | None = None) -> str:
    """头部修正节(report.md 元信息块后注入;html 侧经 _appendix_body 同源
    消费,首个标题行由 card 标题承担)。"""
    modified, added = delta["modified"], delta["added"]
    lines = [f"### 较上次报告的修正(上次结算 {_cn_ts(delta['anchor_ts'])})",
             ""]
    lines.append(f"自上次结算:修正 {len(modified)} 条 / 新增 {len(added)} 条"
                 f" / 不变 {delta['unchanged_count']} 条"
                 + ("——无状态变化。" if not modified and not added else "。"))
    for m in modified:                     # 严重度序(数据层已排:矛盾>回退>翻正)
        lines.append(_modified_line(m, gists))
    for a in added:
        der = (f",派生自 条目{_short(a['derived_from'])}"
               if a["derived_from"] else "")
        lines.append(f"- 新增条目 {a['draft_id']}"
                     f"「{_cell_text(a['text'], gists, a['draft_id'])}」:"
                     f"{a['status']}{der}")
    touched = sorted(delta.get("hypotheses_touched", {}).items())
    if touched:
        cells = "、".join(
            f"{_short_ref(h)}(关联条目 "
            f"{'、'.join(_short_ref(c) for c in cs)})"
            for h, cs in touched)
        lines.append(f"触及假设:{cells}(假设名见记分板)。")
    return "\n".join(lines)


def render_brief_line(delta: dict) -> str:
    """企业简报头部一行(细节见 report.md;零变化如实说)。"""
    n_mod, n_add = len(delta["modified"]), len(delta["added"])
    if not n_mod and not n_add:
        return "较上次结算:无状态变化。"
    return (f"较上次:修正 {n_mod} 条 / 新增 {n_add} 条(细节见 report.md)。")


def _short_ref(claim_id: str) -> str:
    """假设触及行的 claim 短标(与 draft_id 展示一致口径)。"""
    return (claim_id or "").rsplit(":", 1)[-1]
