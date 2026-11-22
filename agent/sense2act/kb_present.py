"""K5 资质核对表呈现层(渲染批次,2026-10-27)。纯函数零 LLM;append-only。

挂载=stages/s7_report.py::_kb_finalize 成功路径尾部,向 report.md/brief.md
追加「资质核对」节(照 ESS 附录 append-only 先例;双挂=用户 2026-10-27
拍板);门控=--kb on ∧ kb.db 有规则卡 ∧ 有自我卡,否则零痕迹。html 节
消费同一文本过白名单转换(s7 侧 _appendix_body,与 ESS 节同手法)。

数据层=kb_check.py(查表比对纯函数零 LLM;本模块方向合法地消费其 rows,
kb_check 不 import 渲染——纪律不动)。**呈现只读零写台账**:待证实/红线
只呈现与指引,不改 claim 三态(三态只属于 claim);待证实="查不到→Open
Questions"纪律延伸,不伪装满足。

话术拍板(2026-10-27,用户):
- 结论句三行机械模板:合计行 / 红线硬伤行(kind=红线∧status≠已证实 逐条
  点名——载明不符与未载明都按硬伤对待;红线全坐实或无红线项时如实改写或
  省略)/ 待证实 OQ 指引行(附 rid+field 定位);
- 敏感过滤(红线件最先):subject 卡 attributes.sensitivity=internal
  (kb_import 上传件缺省形态)→ 表格"依据"列中的显式值不出境,只出定位符
  (kb_chain 敏感过滤同口径:internal 只出存在性+定位符)。要求列来自规则
  卡(尺子)非对象敏感面,照旧呈现。
"""
from __future__ import annotations

from .kb_check import _STATUS_BADGE

_HEADER = "### 附录·资质核对(K5 规则卡比对)"


def _basis_cell(basis: str, field: str, internal: bool) -> str:
    """依据列(用户可读,2026-10-27:英文字段不裸奔——数据层 basis 保持
    attributes:/claim: 前缀供机器对账,呈现层转中文):登记信息:{field}=值;
    claim 全 id 短标化为「调查条目 C3」。internal 时显式值不出境,只出定位符
    (「登记信息:注册资本」)。"""
    if not basis:
        return "—"
    if basis.startswith("attributes:"):
        if internal:
            return f"登记信息:{field}"
        return "登记信息:" + basis[len("attributes:"):]
    if basis.startswith("claim:"):
        return f"调查条目 {basis[len('claim:'):].rsplit(':', 1)[-1]}"
    return basis


def render_appendix(rows: list[dict], subject_internal: bool = False) -> str:
    """「资质核对」追加节:按对象分组的比对表格+结论句三行机械模板。

    rows=kb_check.compare_rules 产出(每格三态已证实/矛盾/待证实);
    subject_internal=对象卡敏感级(呈现侧敏感过滤,数据层不管呈现)。"""
    by_sub: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_sub.setdefault(
            (r["subject_id"], r["subject_name"]), []).append(r)
    lines = ["", _HEADER, ""]
    rule_names = sorted({r["rule_name"] for r in rows})
    for (sid, sname), items in sorted(by_sub.items()):
        head = f"**{sname or sid}**(`{sid}`)"
        if len(rule_names) > 1:
            head += f" · 规则卡:{'、'.join(rule_names)}"
        lines.append(head)
        lines.append("")
        lines.append("| 条目号 | 种类 | 要求 | 核查结论 | 依据 |")
        lines.append("|---|---|---|---|---|")
        for r in items:
            badge = _STATUS_BADGE.get(r["status"], "?")
            basis = _basis_cell(r["basis"], r["field"], subject_internal)
            req = r["requirement"] or (
                f"{r['field']} {r['predicate']} {r['value']}")
            lines.append(f"| {r['rid']} | {r['kind']} | {req} "
                         f"| {badge} {r['status']} | {basis} |")
        lines.append("")
        _conclusion(lines, items, subject_internal)
    return "\n".join(lines) + "\n"


def _conclusion(lines: list[str], items: list[dict],
                internal: bool) -> None:
    """结论句三行机械模板(拍板话术;零 LLM):合计/红线硬伤/待证实指引。"""
    n_pos = sum(1 for r in items if r["status"] == "已证实")
    n_neg = sum(1 for r in items if r["status"] == "矛盾")
    n_unk = sum(1 for r in items if r["status"] == "待证实")
    lines.append(f"> 资质核对结论:{len(items)} 项要求 = 已证实 {n_pos}"
                 f" / 矛盾 {n_neg} / 待证实 {n_unk}。")
    # 红线硬伤逐条点名(kind=红线∧status≠已证实);载明不符与未载明都按
    # 硬伤对待(要求未坐实即风险);internal 不影响本行——不出显式值
    red_bad = [r for r in items
               if r["kind"] == "红线" and r["status"] != "已证实"]
    red_all = [r for r in items if r["kind"] == "红线"]
    if red_bad:
        cells = ";".join(
            f"「{_req_text(r)}」{'载明不符' if r['status'] == '矛盾' else '未载明'}"
            f"(条目 {r['rid']})" for r in red_bad)
        lines.append(f"> ⚠ 红线硬伤:{cells}——红线未坐实按硬伤对待。")
    elif red_all:
        lines.append(f"> 红线项 {len(red_all)} 条全部坐实。")
    if n_unk:
        pend = "、".join(f"「{r['field']}」(条目 {r['rid']})"
                         for r in items if r["status"] == "待证实")
        lines.append(f"> 待证实 {n_unk} 项 = 查不到,不伪装满足,建议列入"
                     f" Open Questions:{pend}。")
    lines.append("")


def _req_text(r: dict) -> str:
    return r["requirement"] or f"{r['field']} {r['predicate']} {r['value']}"
