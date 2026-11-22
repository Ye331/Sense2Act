# -*- coding: utf-8 -*-
"""证据链卷面渲染器(卷面改造二阶段,2026-11-05 方案 §六)——自研确定性
分层布局 + SVG 装配。

废 mermaid 作人看主载体(自动布局=横向爆炸/裁字/拖拽的根因):固定 1000px
宽纵向卷,泳道分栏 + 走廊布线,文本宽度自算(中文 1em / 拉丁 0.55em,
不再裁字),SVG 输出(HTML 内嵌;每图可另出独立 SVG,可打印/可嵌报告)。
零第三方依赖(mermaid CDN/fonts.ready 退役,离线双击即看)。

铁律(方案 §三/§四/§六/§八):
- 渲染期零 LLM 零网络:本模块只消费 build_chain_graph v2 结构(argument/
  fact_groups/overview/claims[].history+in_main/evidence[].summary),
  **只消费不判定**——论证区资格/排序/分组兜底/材料准入全在数据层,两侧
  同判据已在数据层锁死;
- 三线系统(§4-3):灰实线=支撑(默认款,不带字,分层位置已表达语义)/
  红实线=反证·拆台(全图唯一颜色)/灰虚线+「前提」=派生。共据不画线
  (材料卡注记「互不为独立佐证」——同源转发聚注记);长解释进卡片;
- 无边不画(§4-2):主图只收 claims[].in_main;无边命题的去向=缺口图
  (本职)+总览计数;
- 缺口撤出主图(用户拍板):主图=论证区框+机械事实组框+材料层;缺口图=
  独立附录(待证实行+矛盾行翻回条件);
- 注入防御:所有文本过 _x()(XML 五字符转义,escape 优先;mermaid _mq
  引号防御经验平移到 SVG 载体);
- 确定性:同输入两次渲染逐字节一致——全部排序显式、禁 now()、坐标一律
  定点一位小数(规避浮点尾差跨机漂移)。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from .chain_graph import DRAW_RULINGS

CANVAS_W = 1000        # 固定卷宽(§六)
PAD = 20               # 卷内边距
GAP_TOP = 46           # 前提弧走廊高度(弧顶控制点距卡顶)

W_CARD, HGAP_CARD, PER_ROW = 176, 12, 5    # 命题卡:5 卡/行(5*176+4*12=928≤960)
W_EV, HGAP_EV, PER_ROW_EV = 200, 12, 4     # 材料卡:4 卡/行

_C_FILL = {"已证实": "#dff0d8", "矛盾": "#f2dede", "待证实": "#f5f5f5"}
_C_STROKE = {"已证实": "#3c763d", "矛盾": "#c9302c", "待证实": "#999999"}
_LINE_SUP = "#b0b0b0"     # 支撑:细灰实线(默认款)
_LINE_CON = "#c9302c"     # 反证:红(全图唯一颜色)
_LINE_DER = "#999999"     # 前提:灰虚线
_INK = "#1f2328"
_INK_SUB = "#59636e"

# 待证实状态行短语(机械模板;镜像 gap 四型,数据层 gap.type 权威,此处只翻译)
_PENDING_PHRASE = {
    "未触及": "尚未查证",
    "查证排除": "查过未见载明",
    "孤证": "单一来源",
    "谓词不足": "证据已够,载明性核对未过(候审)",
}
# 锚点人话(权威 anchor_name 的卷面翻译;空=兜底"已证实")
_ANCHOR_PHRASE = {
    "强(双根权威)": "双路独立记录+权威源载明",
    "强(权威单源)": "权威记录载明(法定登记源)",
    "中(双根普通)": "两路独立印证",
    "中(自证)": "语料内自证型断言",
}


def _x(s: str) -> str:
    """XML 五字符转义(注入防御:escape 优先;数据来自台账/LLM 旁车,
    一律不可信)。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _f(v: float) -> str:
    """坐标定点一位小数(确定性:规避浮点尾差跨机漂移)。"""
    return f"{v:.1f}"


def _tw(text: str, size: float) -> float:
    """文本像素宽(中文/全角 1em,拉丁 0.55em;自算,不再依赖渲染器测宽)。"""
    w = 0.0
    for ch in text:
        w += size * (1.0 if unicodedata.east_asian_width(ch) in ("W", "F")
                     else 0.55)
    return w


# 断行卫生(卷面改造四阶段 §三,批1-A):不可断单元=拉丁/数字串及内部
# 标点(编号/金额/时刻/域名形态,如 1409992026AGK00184、999910.0、09:30、
# ccgp.gov.cn、E12),整词断行只在词边界;中文照旧逐字断(任意断点可读)。
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,:%/+-]*[A-Za-z0-9]|[A-Za-z0-9]")
_NO_HEAD = set(",.、;:!?。,》)]}〉」』)]:;,.!?")   # 行首禁则(闭合/句读标点)
_NO_TAIL = set("《([{〈「『([{'\"")                # 行尾禁则(开放标点)


def _cw(ch: str, size: float) -> float:
    """单字符像素宽(中文/全角 1em,拉丁 0.55em;_tw 的单字符口径)。"""
    return size * (1.0 if unicodedata.east_asian_width(ch) in ("W", "F")
                   else 0.55)


def _uw(unit: str, size: float) -> float:
    return sum(_cw(ch, size) for ch in unit)


def _units(para: str) -> list[str]:
    """字符流→断行单元序列:token(不可断单元)整体一个单元,其余逐字符。"""
    units: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(para):
        if m.start() > pos:
            units.extend(para[pos:m.start()])
        units.append(m.group())
        pos = m.end()
    if pos < len(para):
        units.extend(para[pos:])
    return units


def _wrap(text: str, width_px: float, size: float) -> list[str]:
    """贪心断行(卷面改造四阶段 §三 断行卫生;空串=单空行占位由调用方
    决定)。单元级贪心:token 整词只在词边界断;单 token 超行宽=内部
    硬断(宁内部断不溢出=失败不伪装的排版版);标点避头尾(行首闭合/
    行尾开放标点,取副作用最小者=挤入前行/移入下行,不拆 token——开放/
    闭合标点恒为独立单字符单元)。dot HTML-like/兜底/纵向主图/缺口图
    四载体共用,修一处全格式生效。"""
    out: list[str] = []
    for para in (text or "").split("\n"):
        out.extend(_break_units(_units(para), width_px, size))
    return out or [""]


def _break_units(units: list[str], width_px: float, size: float) -> list[str]:
    lines: list[str] = []
    cur: list[str] = []
    w = 0.0

    def flush(carry: list[str] | None = None) -> None:
        nonlocal cur, w
        lines.append("".join(cur))
        cur = list(carry or [])
        w = sum(_uw(u, size) for u in cur)

    for u in units:
        uw = _uw(u, size)
        if uw > width_px:            # 病态超宽单元:token 内部硬断
            if cur:
                flush()
            seg = ""
            for ch in u:
                if _tw(seg + ch, size) > width_px and seg:
                    lines.append(seg)
                    seg = ""
                seg += ch
            cur, w = [seg], _uw(seg, size)
            continue
        if w + uw > width_px and cur:
            if u[0] in _NO_HEAD:     # 避头:闭合标点挤入前行(副作用最小者)
                cur.append(u)
                w += uw
                continue
            carry: list[str] = []    # 避尾:行尾开放标点移入下行(整单元移)
            while cur and len(cur[-1]) == 1 and cur[-1] in _NO_TAIL:
                carry.insert(0, cur.pop())
            flush(carry)
        cur.append(u)
        w += uw
    if cur or not lines:
        lines.append("".join(cur))
    return lines


# ---------------------------------------------------------------- 文案(机械模板)

def _status_line(c: dict) -> str:
    """命题卡状态行(§3.1 三层之二;锚点名即判决梯人话,此处只翻译)。
    行式链与缺口退场(2026-11-09):四型徽章「〔待查证·孤证〕」与查证计数
    (已查 N 次/挂证 N 件未裁)撤出卡面——缺口去向=总览注记+报告 OQ 节+
    md-chain 待查证清单;三态徽章+缺口型短语(单一来源/查过未见载明等)
    保留=论证语义,型徽章与计数撤。"""
    st = c["status"]
    if st == "已证实":
        a = c.get("anchor_name")
        phrase = _ANCHOR_PHRASE.get(a) if a else None
        return "〔已证实〕" + phrase if phrase else "〔已证实〕"
    if st == "矛盾":
        return "〔已反对〕出现相反记录"
    g = c.get("gap") or {}
    gt = g.get("type", "")
    segs = [_PENDING_PHRASE.get(gt, "待查证")]
    return "〔待查证〕" + "·".join(segs)


def _morph_line(m: dict | None) -> str:
    """证据形态行(B-1):「证据:1 独立来源(政采公告)+1 条同源相反记载」。
    输入=数据层 evidence_morph 纯函数产物;空/None=空串(调用方省行)。"""
    if not m:
        return ""
    segs: list[str] = []
    n = m.get("n_support_roots", 0)
    srcs = [s for s in (m.get("support_sources") or []) if s]
    if n:
        names = "、".join(srcs[:3])
        segs.append(f"{n} 独立来源({names})" if names else f"{n} 独立来源")
    nc = m.get("n_contra_items", 0)
    if nc:
        segs.append(f"{nc} 条同源相反记载" if m.get("same_source_contra")
                    else f"{nc} 条相反记载")
    return ("证据:" + "+".join(segs)) if segs else ""


def _morph_of(c: dict) -> str:
    """证据形态行取口:chain_cards 预计算键优先,claims 节点原键兜底
    (纵向主图直喂 nodes.claims 同样生效)。"""
    return c.get("morph_line") or _morph_line(c.get("evidence_morph"))


def _next_line(g: dict | None) -> str:
    """补证通道行(B-1):「尚差:第二个独立来源相互印证 · 通道:政采详情页」;
    无通道建议=「通道待建议」(照缺口图措辞);无 next=空串省行。"""
    nx = (g or {}).get("next")
    if not nx:
        return ""
    ch = (g or {}).get("next_channel") or ""
    return (f"尚差:{nx} · 通道:{ch}" if ch
            else f"尚差:{nx} · 通道待建议")


def _next_of(c: dict) -> str:
    return c.get("next_line") or _next_line(c.get("gap"))


def _flip_of(c: dict) -> str:
    """矛盾卡翻回条件行(B-1):chain_cards 预计算键优先,claims 节点原键
    兜底。"""
    return c.get("flipback_line") or c.get("flipback_note") or ""


def _zone_note(g: dict) -> str:
    """小区卡注记(B-2):「查过未见载明·已查 3 次→通道:招标文件正文」
    (形态短语+计数→通道;无通道建议=段省略)。"""
    segs = [_PENDING_PHRASE.get(g.get("type", ""), "待查证")]
    if g.get("tries"):
        segs.append(f"已查 {g['tries']} 次")
    if g.get("unadjudicated"):
        segs.append(f"挂证 {g['unadjudicated']} 件未裁")
    note = "·".join(segs)
    ch = g.get("next_channel") or g.get("channel") or ""
    return note + (f"→通道:{ch}" if ch else "")


def _ev_notes(e: dict) -> str:
    """材料卡注记行(同源转发聚注记+当事方属性;§4-4)。"""
    notes = []
    if e.get("same_root_count", 1) > 1:
        notes.append(f"同根 {e['same_root_count']} 件,互不为独立佐证")
    if e.get("party"):
        notes.append("当事方自述,不单独作数")
    return ";".join(notes)


def _root_no_map(graph: dict) -> dict[str, str]:
    return {c["root"]: f"R{i + 1}" for i, c in enumerate(graph["clusters"])}


# ---------------------------------------------------------------- 总览(§三 卷首)

def overview_lines(graph: dict) -> list[str]:
    """卷首总览文本行:假设资格逐行交代(挣到/亦已立居次/未立起/无材料
    触及)+命题与材料计数+无边命题计数。全部机械模板,零 LLM。"""
    m = graph["meta"]
    ov = graph.get("overview") or {"hypotheses": [], "n_no_edge_claims": 0,
                                   "n_no_edge_pending": 0,
                                   "n_no_edge_verified": 0,
                                   "n_seed_hidden": 0}
    lines = [f"数据截至 {m['as_of']}"
             + (f"〔{m['kb_version']}〕" if m.get("kb_version") else "")]
    state_line = {
        "chosen": lambda h: (f"「{h['label']}」挣到论证区"
                             f"({h['n_verified']}/{h['n_members']} 条已证实)"),
        "runner_up": lambda h: (f"「{h['label']}」亦已立"
                                f"({h['n_verified']} 条已证实),居次,不立区"),
        "unearned": lambda h: (f"「{h['label']}」未立起"
                               f"({h['n_members']} 条在查,尚无已证实)"),
        "untouched": lambda h: f"「{h['label']}」无材料触及",
    }
    for h in ov["hypotheses"]:
        fn = state_line.get(h["state"])
        if fn:
            lines.append(fn(h))
    counts = (f"命题 {m['n_claims']} 条(待证实 {m['n_pending']}),"
              f"材料 {m['n_evidence_drawn']} 件入图")
    if ov.get("n_no_edge_pending"):
        counts += (f";另有 {ov['n_no_edge_pending']} 条无证明材料"
                   "(未上图,见待查证清单)")
    if ov.get("n_no_edge_verified"):
        counts += (f";{ov['n_no_edge_verified']} 条仅信封字段自证"
                   "(元陈述,不进卷面)")
    # v3 分流注记(§三.2:解读型三支去向;正本仍在台账,不冒充删证)
    routed = ov.get("n_routed") or {}
    if any(routed.values()):
        seg = []
        if routed.get("clue"):
            seg.append(f"缺口线索 {routed['clue']} 条(线索区+转生清单)")
        if routed.get("bridge"):
            seg.append(f"解读定性 {routed['bridge']} 条(→解读桥)")
        if routed.get("meta"):
            seg.append(f"元信息 {routed['meta']} 条(挡除)")
        lines.append("解读型分流:" + "、".join(seg) + ";不进命题层,正本仍在台账")
    if ov.get("n_bridges_drawn"):
        bmsg = f"解读桥 {ov['n_bridges_drawn']} 座上图"
        if ov.get("n_bridges_blocked"):
            bmsg += (f";{ov['n_bridges_blocked']} 座未上图"
                     "(指纹漂移/门槛未过/无论证区)")
        lines.append(bmsg)
    # v4 谱系归并注记(§五-5:不冒充删证,照种子挡除注记先例)
    if ov.get("n_lineage_merged"):
        lines.append(f"{ov['n_lineage_merged']} 条跨轮同文谱系命题已归并为 "
                     f"{ov['n_lineage_families']} 卡(正本仍在台账)")
    hidden = []
    if ov.get("n_seed_hidden"):
        hidden.append(f"信封背景字段 {ov['n_seed_hidden']} 件不入材料层")
    if m.get("n_tried_hidden"):
        hidden.append(f"已查无载明 {m['n_tried_hidden']} 次")
    if m.get("n_unadjudicated"):
        hidden.append(f"挂证 {m['n_unadjudicated']} 件未裁")
    if hidden:
        counts += ";" + "、".join(hidden)
    lines.append(counts)
    # 未裁材料总览注记(证据级去重数;未裁=未检验的相关性候选,不冒充
    # 无关,在案方向逐条见下方支撑明细)
    n_unruled = ov.get("n_unruled_evidence") or 0
    if n_unruled:
        lines.append(f"另有 {n_unruled} 份在案材料未入裁定(见支撑明细)")
    return lines


# ---------------------------------------------------------------- 主图(§3.1)

def _claim_card_svg(c: dict, x: float, y: float,
                    w: float = W_CARD) -> tuple[str, float, float]:
    """命题卡(三层人话):标题(gist)/状态行/状态史行(+/前提行:批1.5
    premises 键在才有——纵向主图消费 nodes.claims 无此键=零影响;+/证据
    形态行(行式链与缺口退场:补证通道/翻回条件行撤出卡面,证据形态=
    论证语义保留),键在才画=零痕迹)。返回 (svg, w, h)。"""
    st = c["status"]
    fill, stroke = _C_FILL.get(st, "#f5f5f5"), _C_STROKE.get(st, "#999999")
    dash = ' stroke-dasharray="5 3"' if st == "待证实" else ""
    inner_w = w - 16
    t_lines = _wrap(c["label"], inner_w, 13)
    s_lines = _wrap(c.get("status_line") or _status_line(c), inner_w, 11)
    h_lines = _wrap(c.get("history") or "", inner_w, 10) if c.get("history") else []
    p_lines = (_wrap("前提: " + "、".join(c["premises"]), inner_w, 10)
               if c.get("premises") else [])
    x_txt = [t for t in (_morph_of(c),) if t]
    x_lines: list[str] = []
    for t in x_txt:
        x_lines.extend(_wrap(t, inner_w, 10))
    h = (10 + len(t_lines) * 19 + 4 + len(s_lines) * 15
         + (4 + len(h_lines) * 13 if h_lines else 0)
         + (4 + len(p_lines) * 13 if p_lines else 0)
         + (4 + len(x_lines) * 13 if x_lines else 0) + 8)
    parts = [f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"'
             f' rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>']
    ty = y + 10 + 13
    for ln in t_lines:
        parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="13" '
                     f'font-weight="bold" fill="{_INK}">{_x(ln)}</text>')
        ty += 19
    ty += 4
    scol = _C_STROKE.get(st, "#999999")
    for ln in s_lines:
        parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="11" '
                     f'fill="{scol}">{_x(ln)}</text>')
        ty += 15
    if h_lines:
        ty += 4
        for ln in h_lines:
            parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10" '
                         f'fill="{_INK_SUB}">{_x(ln)}</text>')
            ty += 13
    if p_lines:
        ty += 4
        for ln in p_lines:
            parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10" '
                         f'fill="#777777">{_x(ln)}</text>')
            ty += 13
    if x_lines:
        ty += 4
        for ln in x_lines:
            parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10" '
                         f'fill="{_INK_SUB}">{_x(ln)}</text>')
            ty += 13
    return "\n".join(parts), float(w), h


def _ev_card_svg(e: dict, rno: str, x: float, y: float,
                 w: float = W_EV) -> tuple[str, float, float]:
    """材料卡(§4-4):概括句+出处+独立根编号+注记。返回 (svg, w, h)。"""
    inner_w = w - 16
    if e["sensitive"]:
        head = f"〔internal〕材料 [{e['num']}]"
    else:
        head = f"[{e['num']}] {e['summary']}"
    h_lines = _wrap(head, inner_w, 12)
    src = e["platform"] or e["root"]
    src_line = f"出处 {src} · {rno}" + (" · 权威" if e["authoritative"] else "")
    s_lines = _wrap(src_line, inner_w, 10.5)
    note = _ev_notes(e)
    n_lines = _wrap(note, inner_w, 10.5) if note else []
    h = (10 + len(h_lines) * 17 + 3 + len(s_lines) * 14
         + (3 + len(n_lines) * 13 if n_lines else 0) + 8)
    stroke = "#8a6d3b"   # 材料卡统一框色;红线只留给「会拆台」(§4-3)
    parts = [f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"'
             f' rx="6" fill="#fcf8e3" stroke="{stroke}" stroke-width="1.2"/>']
    ty = y + 10 + 12
    for ln in h_lines:
        parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="12" '
                     f'font-weight="bold" fill="{_INK}">{_x(ln)}</text>')
        ty += 17
    ty += 3
    for ln in s_lines:
        parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10.5" '
                     f'fill="#8a6d3b">{_x(ln)}</text>')
        ty += 14
    if n_lines:
        ty += 3
        for ln in n_lines:
            parts.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10.5" '
                         f'fill="{_INK_SUB}">{_x(ln)}</text>')
            ty += 13
    return "\n".join(parts), float(W_EV), h


def _frame_box(title: str, x: float, y: float, w: float, h: float,
               accent: str) -> list[str]:
    """组框(论证区组/机械事实组):框名条+圆角框。"""
    return [
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"'
        f' rx="8" fill="none" stroke="{accent}" stroke-width="1.2"/>',
        f'<text x="{_f(x + 10)}" y="{_f(y + 18)}" font-size="12.5" '
        f'font-weight="bold" fill="{accent}">{_x(title)}</text>',
    ]


def render_main_svg(graph: dict) -> str:
    """主图(推理树形态,2026-11-06 二改):自下而上四级收束——材料贴身
    (每列纵向堆叠在命题正下方,走廊折线互不交叉)→ 命题纵向递进(前提在
    下依赖在上)→ 列顶名条(论点/事实组归纳)→ 论证区假设节点(顶部收束,
    挣来的研判)。事实组列同构但无收束线(不参与论证)。三线系统不变
    (灰实=支撑与归属/红=反证/灰虚+「前提」=派生走廊线)。确定性:同输入
    逐字节一致。"""
    claims = {c["claim_id"]: c for c in graph["nodes"]["claims"]}
    evs = {e["evidence_id"]: e for e in graph["nodes"]["evidence"]}
    sup_edges = graph["edges"]["supports"]
    der_edges = graph["edges"]["derives"]
    arg = graph.get("argument")
    content_w = CANVAS_W - PAD * 2

    # ---- 1. 列序列(无边不画:只收 in_main;论证区列→事实组列)
    cols: list[dict] = []
    seq: list[str] = []
    if arg:
        for g in arg["groups"]:
            mem = [cid for cid in g["members"]
                   if cid in claims and claims[cid]["in_main"]]
            if mem:
                cols.append({"title": g["name"], "claims": mem,
                             "kind": "arg", "accent": "#2b6cb0"})
                seq += mem
    for fg in graph.get("fact_groups") or []:
        mem = [cid for cid in fg["claim_ids"] if cid in claims]
        if mem:
            cols.append({"title": fg["name"], "claims": mem,
                         "kind": "fact", "accent": "#8a6d3b"})
            seq += mem
    if not seq:
        return ""
    if not cols:   # 防御保底(seq 非空而列缺失的脏形态;正常装配不可达)
        cols = [{"title": "事实", "claims": seq, "kind": "fact",
                 "accent": "#8a6d3b"}]
    n_cols = len(cols)
    col_gap = 14.0 if n_cols <= 5 else 8.0
    # 列多时收窄卡宽适配卷宽(文本断行变碎换取不出画布;列=推理单元不砍)
    cw = W_CARD if n_cols <= 5 else max(
        120.0, (content_w - (n_cols - 1) * col_gap) / n_cols)
    total_w = n_cols * cw + (n_cols - 1) * col_gap
    x0 = PAD + (content_w - total_w) / 2
    col_x = {i: x0 + i * (cw + col_gap) for i in range(n_cols)}

    # ---- 2. 列内命题拓扑定向(前提在下依赖在上:前提链深者靠下)
    der_pairs = [(d["src"], d["dst"]) for d in der_edges]

    def _depth(cid: str, _seen: frozenset = frozenset()) -> int:
        best = 0
        for s, t in der_pairs:
            if t == cid and s not in _seen:
                best = max(best, 1 + _depth(s, _seen | {cid}))
        return best

    for col in cols:
        col["claims"] = sorted(col["claims"], key=lambda c: (-_depth(c), c))
    col_of: dict[str, int] = {}
    order_in_col: dict[str, int] = {}
    for i, col in enumerate(cols):
        for k, cid in enumerate(col["claims"]):
            col_of[cid] = i
            order_in_col[cid] = k

    # ---- 3. 材料归属(主目标=列内序最小目标,贴身该列;其余=次目标右走廊)
    ev_targets: dict[str, list[str]] = {}
    for eid in sorted({e["src"] for e in sup_edges
                       if e["src"] in evs and e["dst"] in col_of}):
        tgts = [e["dst"] for e in sup_edges
                if e["src"] == eid and e["dst"] in col_of]
        tgts.sort(key=lambda c: (col_of[c], order_in_col[c]))
        ev_targets[eid] = tgts
    ev_by_col: dict[int, list[str]] = {i: [] for i in range(n_cols)}
    for eid, tgts in sorted(ev_targets.items()):
        ev_by_col[col_of[tgts[0]]].append(eid)
    rno = _root_no_map(graph)

    # ---- 4. 纵向布局(章首研判→假设节点→名条行→命题堆→材料堆)
    y = float(PAD)
    head: list[str] = []
    if arg and arg.get("narrative") and arg["narrative"].get("chapter"):
        nl = _wrap(arg["narrative"]["chapter"], content_w - 24, 13)
        bh = 12 + len(nl) * 19 + 10
        head.append(f'<rect x="{_f(PAD)}" y="{_f(y)}" width="{_f(content_w)}"'
                    f' height="{_f(bh)}" rx="6" fill="#eef5fb"'
                    ' stroke="#2b6cb0" stroke-width="1"/>')
        ty = y + 12 + 13
        for ln in nl:
            head.append(f'<text x="{_f(PAD + 12)}" y="{_f(ty)}" font-size="13"'
                        f' fill="{_INK}">{_x(ln)}</text>')
            ty += 19
        y += bh + 16
    hyp = None
    if arg:
        hw = min(620.0, content_w)
        hyp = {"x": PAD + (content_w - hw) / 2, "y": y, "w": hw, "h": 46}
        y += 46 + 34   # 收束走廊(漏斗区)
    name_y = y
    y += 34
    prop_top = y
    pos: dict[str, tuple[float, float]] = {}
    card_h: dict[str, float] = {}
    col_prop_bot: dict[int, float] = {}
    for i, col in enumerate(cols):
        cy = prop_top
        for cid in col["claims"]:
            _, _, ch_ = _claim_card_svg(claims[cid], 0, 0, w=cw)
            pos[cid] = (col_x[i], cy)
            card_h[cid] = ch_
            cy += ch_ + 14
        col_prop_bot[i] = cy - 14
    prop_bot = max(col_prop_bot.values())
    ev_top = prop_bot + 22
    ev_pos: dict[str, tuple[float, float]] = {}
    ev_h: dict[str, float] = {}
    col_ev_bot: dict[int, float] = {}
    for i, col in enumerate(cols):
        ey = ev_top
        for eid in ev_by_col[i]:
            _, _, eh_ = _ev_card_svg(evs[eid], rno.get(evs[eid]["root"], "R?"),
                                     0, 0, w=cw)
            ev_pos[eid] = (col_x[i], ey)
            ev_h[eid] = eh_
            ey += eh_ + 8
        col_ev_bot[i] = ey - 8 if ev_by_col[i] else ev_top
    ev_bot = max(col_ev_bot.values())
    total_h = ev_bot + PAD

    # ---- 5. 装配:章首块+假设节点 → 名条 → 线(先) → 卡(后,压线端头)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
             f'{CANVAS_W} {_f(total_h)}" width="{CANVAS_W}" '
             f'font-family="Microsoft YaHei,sans-serif">']
    parts.append('  <defs><marker id="arr" markerWidth="8" markerHeight="8"'
                 ' refX="6" refY="4" orient="auto"><path d="M0,0 L7,4 L0,8 z"'
                 ' fill="#999999"/></marker>'
                 '<marker id="arrR" markerWidth="8" markerHeight="8" refX="6"'
                 ' refY="4" orient="auto"><path d="M0,0 L7,4 L0,8 z"'
                 ' fill="#c9302c"/></marker></defs>')
    parts += head
    if hyp:
        parts.append(f'<rect x="{_f(hyp["x"])}" y="{_f(hyp["y"])}"'
                     f' width="{_f(hyp["w"])}" height="{_f(hyp["h"])}"'
                     ' rx="10" fill="#d9edf7" stroke="#31708f"'
                     ' stroke-width="1.8"/>')
        parts.append(f'<text x="{_f(hyp["x"] + hyp["w"] / 2)}"'
                     f' y="{_f(hyp["y"] + 29)}" font-size="14"'
                     ' font-weight="bold" fill="#31708f" text-anchor="middle">'
                     f'论证区 ·「{_x(arg["label"])}」·{_x(arg["verdict_line"])}'
                     '</text>')
    # 名条行(列顶归纳;归属语义由位置表达,不带词——§4-3 纪律)
    for i, col in enumerate(cols):
        parts.append(f'<text x="{_f(col_x[i] + 2)}" y="{_f(name_y + 15)}"'
                     f' font-size="12.5" font-weight="bold"'
                     f' fill="{col["accent"]}">{_x(col["title"])}</text>')
        parts.append(f'<line x1="{_f(col_x[i])}" y1="{_f(name_y + 21)}"'
                     f' x2="{_f(col_x[i] + cw)}" y2="{_f(name_y + 21)}"'
                     f' stroke="{col["accent"]}" stroke-width="1"/>')
    # 命题→名条(每列栈顶命题;灰实短竖线=归属)
    for i, col in enumerate(cols):
        top = col["claims"][0]
        cx, cy = pos[top]
        parts.append(f'<line x1="{_f(cx + cw / 2)}"'
                     f' y1="{_f(name_y + 21)}" x2="{_f(cx + cw / 2)}"'
                     f' y2="{_f(cy)}" stroke="{_LINE_SUP}"'
                     ' stroke-width="1.5"/>')
    # 收束线(论证区列名条顶→假设节点底,按列序均匀入底边=漏斗)
    if hyp:
        arg_cols = [i for i, c in enumerate(cols) if c["kind"] == "arg"]
        for k, i in enumerate(arg_cols):
            sx = col_x[i] + cw / 2
            tx = hyp["x"] + hyp["w"] * (k + 1) / (len(arg_cols) + 1)
            parts.append(f'<line x1="{_f(sx)}" y1="{_f(name_y)}"'
                         f' x2="{_f(tx)}" y2="{_f(hyp["y"] + hyp["h"])}"'
                         f' stroke="{_LINE_SUP}" stroke-width="1.5"/>')

    # 走廊折线画线器:三段式 M 出点 → H 走廊 → V 目标高 → H 入点
    def _polyline(x1: float, y1: float, lx: float, x2: float, y2: float,
                  color: str, wq: str, dashed: bool, marker: str) -> str:
        dash = ' stroke-dasharray="5 3"' if dashed else ""
        return (f'<path d="M{_f(x1)},{_f(y1)} L{_f(lx)},{_f(y1)} '
                f'L{_f(lx)},{_f(y2)} L{_f(x2)},{_f(y2)}" fill="none"'
                f' stroke="{color}" stroke-width="{wq}"{dash}'
                f' marker-end="url(#{marker})"/>')

    lane: dict[tuple[int, str], int] = {}   # (列序, 侧) → 走廊条数
    enter: dict[tuple[str, str], int] = {}  # (命题, 侧) → 底边入点序
    # 材料线(主目标=左走廊;次目标=右走廊;红=矛盾)
    for eid, tgts in sorted(ev_targets.items(),
                            key=lambda kv: (col_of[kv[1][0]],
                                            order_in_col[kv[1][0]], kv[0])):
        ex, ey_ = ev_pos[eid]
        my = ey_ + ev_h[eid] / 2
        col_i = col_of[tgts[0]]
        for j, dst in enumerate(tgts):
            side = "L" if j == 0 else "R"
            key = (col_i, side)
            ln = lane.get(key, 0)
            lane[key] = ln + 1
            if side == "L":
                lx = col_x[col_i] - 9 - ln * 7
                sx, sy = ex, my
            else:
                lx = col_x[col_i] + cw + 9 + ln * 7
                sx, sy = ex + cw, my
            eseq = enter.get((dst, side), 0)
            enter[(dst, side)] = eseq + 1
            dx_, dy_ = pos[dst]
            ty_ = dy_ + card_h[dst]
            px = (dx_ + cw / 2 - 7 - eseq * 11 if side == "L"
                  else dx_ + cw / 2 + 7 + eseq * 11)
            red = any(e["ruling"] == "矛盾" for e in sup_edges
                      if e["src"] == eid and e["dst"] == dst)
            parts.append(_polyline(sx, sy, lx, px, ty_,
                                   _LINE_CON if red else _LINE_SUP,
                                   "2.5" if red else "1.5", False,
                                   "arrR" if red else "arr"))
    # 前提线(派生;右走廊 D 侧;灰虚+「前提」两字词=需要词的例外)
    for d in sorted(der_edges, key=lambda x: (x["src"], x["dst"])):
        if d["src"] not in col_of or d["dst"] not in col_of:
            continue
        sx0, sy0 = pos[d["src"]]
        sh = card_h[d["src"]]
        col_i = col_of[d["src"]]
        ln = lane.get((col_i, "D"), 0)
        lane[(col_i, "D")] = ln + 1
        lx = col_x[col_i] + cw + 9 + ln * 7
        eseq = enter.get((d["dst"], "D"), 0)
        enter[(d["dst"], "D")] = eseq + 1
        dx_, dy_ = pos[d["dst"]]
        ty_ = dy_ + card_h[d["dst"]]
        px = dx_ + cw / 2 + 7 + eseq * 11
        parts.append(_polyline(sx0 + cw, sy0 + sh / 2, lx, px, ty_,
                               _LINE_DER, "1.5", True, "arr"))
        parts.append(f'<text x="{_f(lx + 4)}"'
                     f' y="{_f((sy0 + sh / 2 + ty_) / 2)}" font-size="11"'
                     f' fill="#777777">前提</text>')
    # 卡(命题列序;材料列序;卡压线的端头)
    for cid in seq:
        cx, cy = pos[cid]
        svg_c, _, _ = _claim_card_svg(claims[cid], cx, cy, w=cw)
        parts.append(svg_c)
    for i, col in enumerate(cols):
        for eid in ev_by_col[i]:
            ex, ey_ = ev_pos[eid]
            svg_e, _, _ = _ev_card_svg(evs[eid],
                                       rno.get(evs[eid]["root"], "R?"),
                                       ex, ey_, w=cw)
            parts.append(svg_e)
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 缺口图(§3.2)

def render_gap_svg(graph: dict) -> str:
    """缺口图(呈现完整化 B-3 起**退役**:调用点退役=html 卷面不再出该
    section;函数保留一个版本期=可回溯,照 mermaid 退役先例)。原形态:
    待证实行「命题 ──◇〔四型徽章〕缺什么 → 通道建议」+ 矛盾行
    「命题 ──◇〔已反对〕… → 翻回条件」(无四型徽章)——信息已收编主图
    虚框卡注记行(B-1)、待查证小区(B-2)与裁定明细(B-4)。"""
    claims = sorted(graph["nodes"]["claims"], key=lambda c: c["claim_id"])
    rows: list[dict] = []
    for c in claims:
        if c["status"] == "矛盾":
            rows.append({"cid": c["claim_id"], "label": c["label"],
                         "badge": "已反对", "badge_red": True,
                         "what": "出现相反记录",
                         "next": "翻回条件:两独立正向方可翻回"})
        elif c["gap"]:
            g = c["gap"]
            what = g["label"]
            tries = []
            if g.get("tries"):
                tries.append(f"已查 {g['tries']} 次")
            if g.get("unadjudicated"):
                tries.append(f"挂证 {g['unadjudicated']} 件未裁")
            if tries:
                what += "(" + "、".join(tries) + ")"
            rows.append({"cid": c["claim_id"], "label": c["label"],
                         "badge": g["type"], "badge_red": False,
                         "what": what,
                         "next": (f"通道建议:{g['channel']}"
                                  if g.get("channel") else "通道待建议")})
    if not rows:
        return ""
    COL_A, COL_B, COL_C = 320.0, 250.0, 350.0
    content_w = CANVAS_W - PAD * 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
             f'{CANVAS_W} 0" width="{CANVAS_W}" '
             f'font-family="Microsoft YaHei,sans-serif">']
    body: list[str] = []
    y = float(PAD)
    for r in rows:
        la = _wrap(r["label"], COL_A - 30, 12.5)
        lb = _wrap(f"◇〔{r['badge']}〕{r['what']}", COL_B - 20, 11)
        lc = _wrap(r["next"], COL_C - 20, 11)
        rh = max(len(la) * 17, len(lb) * 15, len(lc) * 15) + 12
        ty = y + 14
        for ln in la:
            body.append(f'<text x="{_f(PAD)}" y="{_f(ty)}" font-size="12.5"'
                        f' fill="{_INK}">{_x(ln)}</text>')
            ty += 17
        ty = y + 14
        bx = PAD + COL_A
        red = "#c9302c" if r["badge_red"] else "#777777"
        for ln in lb:
            body.append(f'<text x="{_f(bx)}" y="{_f(ty)}" font-size="11" '
                        f'fill="{red}">{_x(ln)}</text>')
            ty += 15
        ty = y + 14
        cx_ = PAD + COL_A + COL_B
        for ln in lc:
            body.append(f'<text x="{_f(cx_)}" y="{_f(ty)}" font-size="11" '
                        f'fill="{_INK_SUB}">{_x(ln)}</text>')
            ty += 15
        # 命题→缺口的短横线(行首引导,非关系线)
        body.append(f'<line x1="{_f(PAD + COL_A - 24)}" y1="{_f(y + 12)}" '
                    f'x2="{_f(PAD + COL_A - 4)}" y2="{_f(y + 12)}" '
                    f'stroke="#bbbbbb" stroke-width="1.5" stroke-dasharray="3 2"/>')
        body.append(f'<line x1="{_f(PAD)}" y1="{_f(y + rh)}" '
                    f'x2="{_f(PAD + content_w)}" y2="{_f(y + rh)}" '
                    'stroke="#ececec" stroke-width="1"/>')
        y += rh
    total_h = y + PAD
    parts[0] = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
                f'{CANVAS_W} {_f(total_h)}" width="{CANVAS_W}" '
                f'font-family="Microsoft YaHei,sans-serif">')
    parts += body
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 横向证据链(三阶段 §四)

class ChainSvgError(Exception):
    """横向链渲染失败(如 png 无 dot——失败不伪装,由调用方如实报)。"""


def _find_dot() -> str | None:
    """dot 定位器(§七.0,优先级拍板):环境变量 GRAPHVIZ_DOT > PATH
    (shutil.which)> 项目内 tools/graphviz/**/bin/dot.exe > 常见安装路径
    (winget/choco 默认位——第三批复查:winget 装的 Graphviz 只写新终端
    PATH;直查默认位根治)>None→render_chain_png 显式报错(行式链与缺口
    退场后 svg/html 主图已不依赖 dot)。零 Python 绑定。"""
    exe = os.environ.get("GRAPHVIZ_DOT", "")
    if exe and Path(exe).exists():
        return exe
    w = shutil.which("dot")
    if w:
        return w
    for p in sorted(Path("tools/graphviz").glob("**/bin/dot.exe")):
        return str(p)
    for p in ("C:/Program Files/Graphviz/bin/dot.exe",
              "C:/Program Files (x86)/Graphviz/bin/dot.exe",
              "C:/ProgramData/chocolatey/bin/dot.exe"):
        if Path(p).exists():
            return p
    return None


def _h(s: str) -> str:
    """dot HTML-like label 转义(注入防御:escape 优先,数据一律不可信)。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _ev_title(summary: str, title: str, src: str, sensitive: bool) -> str:
    """证据卡标题(第三批 M6 来源去重):sensitive=定位符;否则 summary(旁车
    指纹/词典短语)优先,再原始 title。词典短语兜底(_dict_phrase)会把来源
    拼进 summary 尾部「标题(平台)」,而 md-chain 渲染又拼一次 (平台) →
    「标题(平台)(平台)」——此处剥离尾部 (平台) 重复,来源只在末尾出一次
    (dot 引擎出处行/fallback src 列同源,全格式一致)。全文保留(批1-B 纪律),
    裁断只在 md-chain 渲染层(第三批 M6)。"""
    if sensitive:
        return "〔internal〕材料"
    t = (summary or title or "").strip() or "(无题)"
    if src and t.endswith(f"({src})"):
        t = t[:-len(src) - 2].rstrip()
    return t


def _clip_ev(text: str, n: int = 30) -> str:
    """md-chain 证据标题裁断(第三批 M6):超长尾「…」,全文在附录 A 证据表。
    数据层 title 全文保留(批1-B 纪律),裁断只在文字版证据链渲染层(纯文本
    行,超长标题撑破阅读,dot 引擎已有 _wrap 断行不受影响)。"""
    t = (text or "").strip()
    return t if len(t) <= n else t[:n] + "…"


def chain_cards(graph: dict) -> dict:
    """五类卡统一数据模型(§五):证据卡/命题卡/解读桥卡/论点名条/结论卡。
    全格式投影母版(html 主图/svg/png/md-chain 同源消费);零 LLM 确定性;
    行序=graph.rows(论证区组→事实组,数据层已定),此处不再排序。
    谱系归并投影(四阶段 §五):family_rep 非空=被归并行,不单独成卡
    (家族信息在代表卡:最新代状态+家族全轨迹史行);代表卡证据列=家族
    成员证据并集,(证据id, 关系词) 去重——JSON/rows 全量不动,折叠仅
    投影行为(正本仍在台账)。"""
    claims = {c["claim_id"]: c for c in graph["nodes"]["claims"]}
    ev_by_id = {e["evidence_id"]: e for e in graph["nodes"]["evidence"]}
    rows = graph.get("rows") or []
    arg = graph.get("argument")
    rno = _root_no_map(graph)
    rep_members: dict[str, list[str]] = {}
    for c in graph["nodes"]["claims"]:
        fr = c.get("family_rep") or ""
        if fr:
            rep_members.setdefault(fr, []).append(c["claim_id"])
    # 前提依赖行(批1.5 拍板①:前提边撤图,信息收编卡内;端点按家族
    # 重定向到代表;gist 优先/gist 空=claim 短 id(定位符,零新裁断);
    # 族内自环不收;JSON derives 全量不动)
    gist_by = {c["claim_id"]: (c.get("gist") or "").strip()
               for c in graph["nodes"]["claims"]}
    frep_cc = {c["claim_id"]: (c.get("family_rep") or c["claim_id"])
               for c in graph["nodes"]["claims"]}
    prem_by: dict[str, list[str]] = {}
    for d in sorted(graph["edges"].get("derives") or [],
                    key=lambda x: (x["src"], x["dst"])):
        s = frep_cc.get(d["src"], d["src"])
        t = frep_cc.get(d["dst"], d["dst"])
        if s == t:
            continue   # 族内自环(归并后不存在)
        plabel = gist_by.get(d["src"]) or d["src"].rsplit(":", 1)[-1]
        bucket = prem_by.setdefault(t, [])
        if plabel not in bucket:
            bucket.append(plabel)
    # 命题级多源印证投影:融合卡替代目标命题名下被融合材料卡与逐条边;
    # 原材料仍在 graph JSON supports 与 HTML 证据附录全量保留。若同一材料
    # 同时支撑别的命题,别处仍照画(只折叠目标命题这一份投影)。
    fusion_members = {f["claim_id"]: set(f.get("all_members") or f["members"])
                      for f in (graph.get("fusions") or [])}
    # 呈现完整化 B-4:裁定明细联接口((claim, evidence) → 清单项),证据行
    # 方向标/引文/同根折并标注同源消费;未裁注记行同源。
    rmap = {rc["claim_id"]: rc for rc in (graph.get("rulings") or [])}
    itmap: dict[tuple[str, str], dict] = {}
    for rc in (graph.get("rulings") or []):
        for it in rc.get("items") or []:
            itmap[(rc["claim_id"], it["evidence_id"])] = it
    claim_cards = []
    used_ev: set[str] = set()
    for r in rows:
        c = claims.get(r["claim_id"])
        if c is None or c.get("family_rep"):
            continue   # 归并投影:非代表行不单独成卡(正本仍在 JSON/台账)
        fam_ev: list[dict] = list(r.get("evidence") or [])
        for m in sorted(rep_members.get(r["claim_id"], [])):
            mr = next((x for x in rows if x["claim_id"] == m), None)
            if mr:
                fam_ev += mr.get("evidence") or []
        ded: dict[tuple[str, str], dict] = {}
        for e in fam_ev:
            ded.setdefault((e["evidence_id"], e["relation"]), e)
        ev_list = [ded[k] for k in sorted(ded)
                   if k[0] not in fusion_members.get(r["claim_id"], set())]
        rr = rmap.get(r["claim_id"]) or {}
        claim_cards.append({
            "id": r["claim_id"], "label": c["label"], "status": c["status"],
            "status_line": _status_line(c), "history": c.get("history") or "",
            "anchor_name": c.get("anchor_name"),
            "reborn": bool(c.get("reborn_from")),
            "group": r.get("group"), "terminal": r.get("terminal", True),
            "premises": prem_by.get(r["claim_id"], []),
            # 证据形态行(行式链与缺口退场后唯一保留的卡注记行);补证通道/
            # 翻回条件/未裁注记行随缺口退场撤出渲染,字段仍投影留底
            # (全格式投影母版;渲染层不消费)
            "morph_line": _morph_of(c), "next_line": _next_of(c),
            "flipback_line": _flip_of(c),
            "n_unruled": len(rr.get("unruled_nums") or []),
            "unruled_nums": list(rr.get("unruled_nums") or []),
            "evidence": [{"evidence_id": e["evidence_id"], "num": e["num"],
                          "relation": e["relation"],
                          "ruling": itmap.get((r["claim_id"],
                                               e["evidence_id"]), {})
                          .get("ruling") or e.get("ruling") or "",
                          "quote": itmap.get((r["claim_id"],
                                              e["evidence_id"]), {})
                          .get("quote", ""),
                          "dup_root": bool(itmap.get((r["claim_id"],
                                                      e["evidence_id"]), {})
                                           .get("dup_root"))}
                         for e in ev_list]})
        used_ev |= {e["evidence_id"] for e in ev_list}
    ev_cards = []
    for eid in sorted(used_ev):
        e = ev_by_id.get(eid)
        if e is None:
            continue
        src = e["platform"] or e["root"]
        ev_cards.append({"id": eid, "num": e["num"],
                         "title": _ev_title(e["summary"], e["title"], src,
                                            e["sensitive"]),
                         "src": src,
                         "root_no": rno.get(e["root"], "R?"),
                         "authoritative": bool(e["authoritative"]),
                         "same_root": int(e.get("same_root_count", 1)),
                         "party": bool(e.get("party")),
                         "sensitive": bool(e["sensitive"])})
    bridge_cards = [{"id": b["insight_id"], "text": b["text"],
                     "basis": list(b["basis"]), "n_basis": b["n_basis"],
                     "n_verified": b["n_verified"],
                     "line": (f"依据 {b['n_basis']} 项事实·已查实 "
                              f"{b['n_verified']}"),
                     "target_group": b["target_group"],
                     "rationale": b.get("rationale") or ""}
                    for b in (graph.get("bridges") or [])]
    # 命题级多源印证卡(v5 修正版):一张卡只指向一个命题;实线框+锚列表,
    # 替代该命题被融合材料的逐条卡/边。渲染纯消费,不在此重判。
    fusion_cards = [{"id": f"FU{i}", "group": f["group"],
                     "claim_id": f["claim_id"], "text": f["text"],
                     "n": f["n"], "n_items": f.get("n_items", f["n"]),
                     "anchors": list(f["anchors"]),
                     "party": bool(f["party"]),
                     "relation": f.get("relation") or "印证"}
                    for i, f in enumerate(graph.get("fusions") or [])]
    main_ids = {cc["id"] for cc in claim_cards}
    group_cards = []
    if arg:
        for g in arg["groups"]:
            mem = [cid for cid in g["members"] if cid in main_ids]
            if mem:
                group_cards.append({"name": g["name"], "members": mem,
                                    "kind": "arg"})
    for fg in graph.get("fact_groups") or []:
        mem = [cid for cid in fg["claim_ids"] if cid in main_ids]
        if mem:
            group_cards.append({"name": fg["name"], "members": mem,
                                "kind": "fact"})
    # 结论卡披露(R3/R1,批2 §二):竞争假设(runners_up=总览 state 现成)
    # +票型(装配侧 argument["ticket"] 现成)+组极性聚合(收束线消费);
    # 无落选假设=runners_up 空=竞争行零痕迹(不冒充);无论证区=整卡无。
    conclusion = None
    if arg:
        runners = [{"label": h["label"], "n_verified": h["n_verified"]}
                   for h in ((graph.get("overview") or {})
                             .get("hypotheses") or [])
                   if h.get("state") == "runner_up"]
        conclusion = {"label": arg["label"], "verdict_line": arg["verdict_line"],
                      "group_polarity": arg.get("group_polarity") or [],
                      "ticket": arg.get("ticket"), "runners_up": runners}
    # 待查证小区卡(呈现完整化 B-2):无边命题上图不上线——极简卡=label+
    # 四型徽章+一句话注记,零边隔离;资格判定在数据层(in_zone),此处纯投影。
    zone_cards = []
    for c in graph["nodes"]["claims"]:
        if not c.get("in_zone"):
            continue
        g = c.get("gap") or {}
        zone_cards.append({"id": c["claim_id"], "label": c["label"],
                           "badge": g.get("type", ""),
                           "note": _zone_note(g)})
    return {"claims": claim_cards, "evidence": ev_cards,
            "bridges": bridge_cards, "fusions": fusion_cards,
            "groups": group_cards, "conclusion": conclusion,
            "zone": zone_cards}


_CLAIM_FILL = {"已证实": "#dff0d8", "矛盾": "#f2dede", "待证实": "#f5f5f5"}
_CLAIM_STROKE = {"已证实": "#3c763d", "矛盾": "#c9302c", "待证实": "#999999"}

# R2 关系强度分级线(批2 §二):支撑边粗细三档=直接证明 3px/印证 2px/
# 佐证 1.2px;反证红线 2.5px 保持(红线语义不靠粗细,与 fallback 旧值
# 一致=两引擎统一);其余(时效更新等非三档)=1.2 最细不冒充硬。
# 样式常量照颜色先例(纯呈现,非行为参数,不入模式表)。
_REL_W = {"直接证明": 3.0, "印证": 2.0, "佐证": 1.2}


def _rel_width(rel: str, red: bool) -> str:
    if red:
        return "2.5"
    return str(_REL_W.get(rel, 1.2))


def _wrap_html(text: str, width_px: float, size: float) -> str:
    """断行文本→HTML-like 片段(行间 <BR/>;断行由 _wrap 自算=不裁字)。"""
    return "<BR/>".join(_h(ln) for ln in _wrap(text, width_px, size))


def _dot_source(graph: dict) -> str:
    """graph→DOT 源(横向证据链;rankdir=LR,四层自左向右:证据→命题→
    论点→结论,桥在命题与论点之间)。确定性:节点/边全显式排序、无时间戳。
    论点/事实组=cluster(成员命题**声明在簇内**);论证区簇锚点(不可见点)
    承载 簇→结论(支撑)边并给结论 rank;独立事实无出边=止于命题层。
    布线定稿(2026-11-07 试点拍板):splines=ortho+全部边标签 xlabel
    (外置标签=标签放线旁固定位置不吸附线体)——批1 试 ortho 裸标签
    飘离退回样条;本试点补 xlabel 后交叉弧线清零+标签可读,demo 两版
    png 对比目检定稿;fallback 渲染器行式布局不受影响。"""
    cards = chain_cards(graph)
    lines = [
        "digraph chain {",
        '  rankdir=LR; bgcolor="white"; splines=ortho; nodesep=0.5;'
        ' ranksep=1.1;',
        '  node [fontname="Microsoft YaHei" shape=box',
        '        style="rounded,filled" fontsize=12 penwidth=1.2]',
        '  edge [fontname="Microsoft YaHei" fontsize=10 color="#b0b0b0"',
        '        arrowsize=0.7 fontcolor="#59636e"]',
    ]
    con = cards["conclusion"]
    if con:
        # 结论卡披露(R3,批2 §二):竞争假设行(每落选一行,无=零痕迹)+
        # 票型行;台账现成量纯机械模板。
        body = (f'<B>研判结论 ·{_h(con["label"])}</B><BR/>'
                f'<FONT POINT-SIZE="10">{_h(con["verdict_line"])}(事实)</FONT>')
        for r in con.get("runners_up") or []:
            body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                     f'竞争假设:「{_h(r["label"])}」·已证实 '
                     f'{r["n_verified"]}·居次</FONT>')
        tk = con.get("ticket")
        if tk:
            body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                     f'票型:支撑组 {tk["support"]} ·拆台组 '
                     f'{tk["counter"]} ·在查组 {tk["checking"]}</FONT>')
        lines.append(f'  CON [label=<{body}>'
                     ' fillcolor="#d9edf7" color="#31708f" penwidth=1.8]')

    def _claim_body(c: dict) -> str:
        st = c["status"]
        body = (f'<B>{_wrap_html(c["label"], 150, 12)}</B><BR/>'
                f'<FONT POINT-SIZE="10" COLOR="'
                f'{_CLAIM_STROKE.get(st, "#999999")}">'
                f'{_h(c["status_line"])}</FONT>')
        if c["history"]:
            body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                     f'{_wrap_html(c["history"], 160, 9)}</FONT>')
        if c.get("premises"):
            body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                     f'前提: {_h("、".join(c["premises"]))}</FONT>')
        # 证据形态行(虚框卡注记,空=零痕迹;补证通道/翻回条件行随缺口
        # 退场撤出主图)
        for txt in (_morph_of(c),):
            if txt:
                body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                         f'{_wrap_html(txt, 160, 9)}</FONT>')
        if c["reborn"]:
            body += '<BR/><FONT POINT-SIZE="9" COLOR="#8e6fb8">由解读转生</FONT>'
        return body

    def _claim_attrs(c: dict) -> str:
        st = c["status"]
        style = ('"rounded,filled,dashed"' if st == "待证实"
                 else '"rounded,filled"')
        return (f' label=<{_claim_body(c)}>'
                f' fillcolor="{_CLAIM_FILL.get(st, "#f5f5f5")}"'
                f' color="{_CLAIM_STROKE.get(st, "#999999")}"'
                f' style={style}')

    claim_idx = {c["id"]: f"CL{i}" for i, c in enumerate(cards["claims"])}
    claim_by_id = {c["id"]: c for c in cards["claims"]}
    # 论证区/事实组簇(名条=cluster label;成员命题声明在簇内;每簇锚点=
    # 不可见点:论证区簇锚点承载 簇→结论 支撑边+给结论 rank,全簇锚点
    # (v4)承载扇出汇流边=证据→组框)
    grouped: set[str] = set()
    for gi, g in enumerate(cards["groups"]):
        color = "#2b6cb0" if g["kind"] == "arg" else "#8a6d3b"
        lines.append(f"  subgraph cluster_g{gi} {{")
        lines.append('    fontname="Microsoft YaHei"; style="rounded";'
                     f' color="{color}"; penwidth=1.2; margin=10')
        lines.append(f'    label=<<B><FONT POINT-SIZE="13">'
                     f'{_h(g["name"])}</FONT></B>>')
        for cid in g["members"]:
            c = claim_by_id.get(cid)
            if c is not None:
                grouped.add(cid)
                lines.append(f"    {claim_idx[cid]} [{_claim_attrs(c)}]")
        lines.append(f"    GA{gi} [shape=point style=invis]")
        lines.append("  }")
    # 簇外命题(防御:正常装配不存在——in_main 命题必属某组)
    for i, c in enumerate(cards["claims"]):
        if c["id"] not in grouped:
            lines.append(f"  CL{i} [{_claim_attrs(c)}]")
    # 证据节点
    ev_idx = {e["id"]: f"EV{i}" for i, e in enumerate(cards["evidence"])}
    for i, e in enumerate(cards["evidence"]):
        auth = " ·权威" if e["authoritative"] else ""
        body = (f'<B>[{_h(e["num"])}] {_wrap_html(e["title"], 170, 11)}</B>'
                f'<BR/><FONT POINT-SIZE="9" COLOR="#8a6d3b">'
                f'出处 {_h(e["src"])} ·{_h(e["root_no"])}{auth}</FONT>')
        if e["same_root"] > 1 or e["party"]:
            note = []
            if e["same_root"] > 1:
                note.append(f"同根 {e['same_root']} 件,互不为独立佐证")
            if e["party"]:
                note.append("当事方自述,不单独作数")
            body += (f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">'
                     f'{_h(";".join(note))}</FONT>')
        lines.append(f'  EV{i} [label=<{body}> fillcolor="#fcf8e3"'
                     ' color="#8a6d3b"]')
    # 解读桥节点(虚线框卡;入边=依据,出边=据实推断)
    for i, b in enumerate(cards["bridges"]):
        body = (f'<B>◇ 解读</B><BR/>{_wrap_html(b["text"], 150, 10.5)}'
                f'<BR/><FONT POINT-SIZE="9" COLOR="#8e6fb8">'
                f'{_h(b["line"])}(已证实 {b["n_verified"]})</FONT>')
        lines.append(f'  BR{i} [label=<{body}>'
                     ' style="rounded,filled,dashed" fillcolor="#f7f3fb"'
                     ' color="#8e6fb8"]')
    # 融合卡节点(批2 F2,拍板④=实线框):「融合 N 件」徽章+概括全文+
    # [En] 锚列表(交叉引用不画线)+当事方注记
    for i, fu in enumerate(cards["fusions"]):
        tail = " ".join(f"[{_h(a)}]" for a in fu["anchors"])
        if fu["party"]:
            tail += " ·含当事方自述"
        badge = (f'{fu["n"]} 路·{fu["n_items"]} 件'
                 if fu["n_items"] != fu["n"] else f'{fu["n"]} 路')
        body = (f'<B>▸ 多源印证 {badge}</B><BR/>'
                f'{_wrap_html(fu["text"], 170, 10.5)}'
                f'<BR/><FONT POINT-SIZE="9" COLOR="#59636e">{tail}</FONT>')
        lines.append(f'  FU{i} [label=<{body}> fillcolor="#fcf8e3"'
                     ' color="#8a6d3b" penwidth=1.6]')
    # 边:证据─关系词→命题(批1.5 拍板③定稿:边标签=关系词;**翻转条件
    # 文案(「唯一根失效→回待证实」=过程中间态的机器推导表述)撤出图**,
    # flip 字段在 JSON 全量保留=机器对账与 D 线呈现底座不变。反证=红=
    # 全图唯一颜色。扇出汇流(v4 §六):「关系词 ×N」;lhead=cluster
    # 吸附框边(锚点仅承载 rank,线尾不再扎进框内))
    group_gi = {g["name"]: gi for gi, g in enumerate(cards["groups"])}
    merged_pairs: set[tuple[str, str]] = set()
    for m in graph.get("fanout_merges") or []:
        gi = group_gi.get(m["group"])
        if gi is None or m["evidence_id"] not in ev_idx:
            continue
        for cid in m["members"]:
            if cid in claim_idx:
                merged_pairs.add((m["evidence_id"], cid))
        red = m["relation"] == "反证"
        col = (', color="#c9302c", fontcolor="#c9302c"' if red else "")
        lines.append(f'  {ev_idx[m["evidence_id"]]} -> GA{gi}'
                     f' [xlabel="{_h(m["relation"])} ×{m["n"]}"{col}'
                     f' penwidth={_rel_width(m["relation"], red)}'
                     f' lhead="cluster_g{gi}"]')
    sup = {(e["src"], e["dst"]): e for e in graph["edges"]["supports"]}
    for c in cards["claims"]:
        for e in c["evidence"]:
            if (e["evidence_id"], c["id"]) in merged_pairs:
                continue   # 已汇流:成员边不逐条画(元数据在 JSON 全量)
            key = (e["evidence_id"], c["id"])
            red = sup.get(key, {}).get("ruling") == "矛盾"
            col = ', color="#c9302c", fontcolor="#c9302c"' if red else ""
            lines.append(f'  {ev_idx[e["evidence_id"]]} -> {claim_idx[c["id"]]}'
                         f' [xlabel="{_h(e["relation"])}"{col}'
                         f' penwidth={_rel_width(e["relation"], red)}]')
    # 前提边已退役下图(批1.5 拍板①:前提依赖=命题卡「前提:」小字行;
    # JSON derives 全量保留=机器对账通道不变;撤线理由=三重冗余+dot
    # 局部路由不可控,详见批1.5 方案 §二)
    # 边:命题─依据→桥 / 桥─据实推断→论点簇(lhead 吸附框边,批1.5)/
    # 论点簇─支撑→结论
    for i, b in enumerate(cards["bridges"]):
        for bid in b["basis"]:
            if bid in claim_idx:
                lines.append(f'  {claim_idx[bid]} -> BR{i} [xlabel="依据"]')
        gi = next((gi for gi, g in enumerate(cards["groups"])
                   if g["name"] == b["target_group"] and g["kind"] == "arg"),
                  None)
        if gi is not None:
            lines.append(f'  BR{i} -> GA{gi}'
                         ' [xlabel="据实推断" style=dashed color="#8e6fb8"'
                         ' fontcolor="#8e6fb8"'
                         f' lhead="cluster_g{gi}"]')
    # 命题级多源印证边:一卡只指向一个命题,一条「印证 ×N」边；
    # 被融合的原逐条边已在 chain_cards 投影折叠,JSON supports 全量不动。
    for i, fu in enumerate(cards["fusions"]):
        cid = fu["claim_id"]
        if cid not in claim_idx:
            continue
        rel = fu["relation"]
        lines.append(f'  FU{i} -> {claim_idx[cid]}'
                     f' [xlabel="{_h(rel)} ×{fu["n"]}"'
                     f' penwidth={_rel_width(rel, False)}]')
    if con:
        # 收束线极性(R1,批2 §二):组内含反向→线中段标「含反向 ×N」
        # (●红点字符随标签;dot 单边单色约束,线尾红点仅 fallback marker
        # 可为真红——如实备考,照抄偏差记录施工实录);全正向/全无极性
        # =灰实线现状字节不变。arrowhead=dot=圆点箭头(色随边线灰)。
        gp_by = {x["group"]: x for x in con.get("group_polarity") or []}
        for gi, g in enumerate(cards["groups"]):
            if g["kind"] != "arg":
                continue
            gp = gp_by.get(g["name"])
            if gp and gp["n_rev"] > 0:
                lines.append(
                    f'  GA{gi} -> CON [xlabel=<<FONT COLOR="#31708f">支撑'
                    f'</FONT><BR/><FONT COLOR="#c9302c">● 含反向 '
                    f'×{gp["n_rev"]}</FONT>> color="#b0b0b0" arrowhead=dot'
                    f' ltail="cluster_g{gi}"]')
            else:
                lines.append(f'  GA{gi} -> CON [xlabel="支撑" color="#b0b0b0"'
                             f' ltail="cluster_g{gi}"]')
    lines.append("}")
    return "\n".join(lines)


def render_chain_svg(graph: dict, *, engine: str = "") -> str:
    """横向证据链主图(行式链与缺口退场 2026-11-09:自研行式=主引擎,
    人看主载体 html/svg/报告内嵌;dot 仅 render_chain_png 位图直出,
    「降级布局」注记退役)。engine 参数保留签名(历史调试口,恒走行式)。
    确定性:同输入逐字节一致。"""
    return render_chain_svg_fallback(graph)


def render_chain_png(graph: dict, out_path: str | Path) -> Path:
    """png 直出(dot -Tpng;报告嵌入/Word/PPT)。无 dot=显式报错(失败
    不伪装,不用 svg 冒充 png)。"""
    dot = _find_dot()
    if not dot:
        raise ChainSvgError(
            "未检测到 Graphviz dot(设 GRAPHVIZ_DOT 或安装后重试;"
            "svg/html 走自研兜底可用)")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([dot, "-Tpng"], input=_dot_source(graph).encode("utf-8"),
                          capture_output=True, timeout=60, check=True)
    out.write_bytes(proc.stdout)
    return out


# 行式贴身链主引擎(同一 chain_cards 语义层);「group」独立列已撤销=
# 框即组(组框化 §三),故无 group 列宽
_FB_W = {"ev": 210.0, "claim": 220.0, "bridge": 190.0, "con": 190.0}


def _fusion_fb_svg(fu: dict, x: float, y: float, w: float) -> tuple[str, float]:
    """fallback 融合卡(批2 F2,拍板④=实线框;徽章+概括全文+[En] 锚列表
    +当事方注记)。返回 (svg 片段, 高度)。"""
    tail = " ".join(f"[{a}]" for a in fu["anchors"])
    if fu["party"]:
        tail += " ·含当事方自述"
    lines = _wrap(fu["text"], w - 16, 10.5)
    tl = _wrap(tail, w - 16, 9)
    h = 12 + 15 + 11 + len(lines) * 15 + len(tl) * 12 + 8
    out = [f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"'
           ' rx="6" fill="#fcf8e3" stroke="#8a6d3b" stroke-width="1.6"/>']
    badge = (f'{fu["n"]} 路·{fu["n_items"]} 件'
             if fu["n_items"] != fu["n"] else f'{fu["n"]} 路')
    out.append(f'<text x="{_f(x + 8)}" y="{_f(y + 12 + 11)}" font-size="11"'
               ' font-weight="bold" fill="#8a6d3b">'
               f'▸ 多源印证 {badge}</text>')
    ty = y + 12 + 15 + 11
    for ln in lines:
        out.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="10.5"'
                   f' fill="{_INK}">{_x(ln)}</text>')
        ty += 15
    for ln in tl:
        out.append(f'<text x="{_f(x + 8)}" y="{_f(ty)}" font-size="9"'
                   f' fill="#59636e">{_x(ln)}</text>')
        ty += 12
    return "\n".join(out), h


def render_chain_svg_fallback(graph: dict) -> str:
    """行式贴身链主引擎(行式链与缺口退场 2026-11-09 升主):固定列式
    布局(证据→命题→桥→结论),每行一条推理线=证据堆贴身本行左侧,论点/
    事实组以圆角框包命题行(框名条在框顶,框右缘收束结论);文本 _wrap
    自算不裁字。确定性:全排序显式、禁 now、坐标定点一位小数。"""
    cards = chain_cards(graph)
    cols = [("ev", _FB_W["ev"]), ("claim", _FB_W["claim"])]
    if cards["bridges"]:
        cols.append(("bridge", _FB_W["bridge"]))
    if cards["conclusion"]:
        cols.append(("con", _FB_W["con"]))
    gap = (CANVAS_W - PAD * 2 - sum(w for _, w in cols)) / max(len(cols) - 1, 1)
    col_x = {}
    x = float(PAD)
    for name, w in cols:
        col_x[name] = x
        x += w + gap
    content_w = CANVAS_W - PAD * 2

    head: list[str] = []
    arg = graph.get("argument")
    if arg and arg.get("narrative") and arg["narrative"].get("chapter"):
        nl = _wrap(arg["narrative"]["chapter"], content_w - 24, 13)
        bh = 12 + len(nl) * 19 + 10
        head.append(f'<rect x="{_f(PAD)}" y="0" width="{_f(content_w)}"'
                    f' height="{_f(bh)}" rx="6" fill="#eef5fb"'
                    ' stroke="#2b6cb0" stroke-width="1"/>')
        ty = 12 + 13
        for ln in nl:
            head.append(f'<text x="{_f(PAD + 12)}" y="{_f(ty)}" font-size="13"'
                        f' fill="{_INK}">{_x(ln)}</text>')
            ty += 19
        head_h = bh + 16
    else:
        head_h = 0.0

    # 扇出汇流(fallback 版,四阶段 §六:与 dot 同一 fanout_merges 元数据
    # =两引擎一致行为):被汇流证据卡只在组内首行(行序)画+边标「关系词
    # ×N」,其余成员行不再堆该卡(明细见机器对账 JSON+证据附录 [En] 锚表)
    merges = graph.get("fanout_merges") or []
    group_first: dict[str, str] = {}
    for c in cards["claims"]:
        g = c.get("group")
        if g and g not in group_first:
            group_first[g] = c["id"]
    ev_merge: dict[str, dict] = {}
    skip_cards: set[tuple[str, str]] = set()
    for m in merges:
        first = group_first.get(m["group"])
        if first is None:
            continue
        ev_merge[m["evidence_id"]] = dict(m, first_row=first)
        for cid in m["members"]:
            if cid != first:
                skip_cards.add((cid, m["evidence_id"]))
    # 命题级多源印证卡(fallback):绑定目标命题行,画在证据堆首位；目标
    # 命题的被融合材料已在 chain_cards 中折叠,不会和融合卡重复出现。
    fu_in_row: dict[str, list[dict]] = {}
    claim_ids_fb = {c["id"] for c in cards["claims"]}
    for fu in cards["fusions"]:
        if fu["claim_id"] in claim_ids_fb:
            fu_in_row.setdefault(fu["claim_id"], []).append(fu)

    # 命题行(行序;行高=max(命题卡, 该行证据堆))
    y = float(head_h)
    claim_h: dict[str, float] = {}
    row_y: dict[str, float] = {}
    row_h: dict[str, float] = {}
    ev_stack: dict[str, list[tuple[str, float]]] = {}
    for c in cards["claims"]:
        svg_c, _, ch_ = _claim_card_svg(
            {"label": c["label"], "status": c["status"],
             "anchor_name": c["anchor_name"], "gap": None,
             "history": c["history"], "premises": c["premises"],
             "status_line": c["status_line"],
             "morph_line": c.get("morph_line", ""),
             "next_line": c.get("next_line", ""),
             "flipback_line": c.get("flipback_line", "")},
            0, 0, w=_FB_W["claim"])
        claim_h[c["id"]] = ch_
    for c in cards["claims"]:
        ev_h_sum = 0.0
        ev_stack[c["id"]] = []
        for fu in fu_in_row.get(c["id"], []):
            _, fh_ = _fusion_fb_svg(fu, 0, 0, _FB_W["ev"] - 10)
            ev_stack[c["id"]].append(("FU:" + fu["id"], fh_))
            ev_h_sum += fh_ + 6
        for e in c["evidence"]:
            if (c["id"], e["evidence_id"]) in skip_cards:
                continue   # 已汇流:成员行不再堆该证据卡(首行画,标 ×N)
            ecard = next((x for x in cards["evidence"]
                          if x["id"] == e["evidence_id"]), None)
            if ecard is None:
                continue
            _, _, eh_ = _ev_card_svg(
                {"num": ecard["num"], "summary": ecard["title"],
                 "sensitive": ecard["sensitive"], "platform": ecard["src"],
                 "root": ecard["root_no"], "authoritative":
                     ecard["authoritative"], "same_root_count":
                     ecard["same_root"], "party": ecard["party"]},
                ecard["root_no"], 0, 0, w=_FB_W["ev"] - 10)
            ev_stack[c["id"]].append((e["evidence_id"], eh_))
            ev_h_sum += eh_ + 6
        row_h[c["id"]] = max(claim_h[c["id"]], ev_h_sum, 40.0)
    # 组框化(§三):组间预留框名条高(name_h);组内行距 14 不变;独立事实
    # (group=None)不入组=无名字条,照旧 14 行距。
    name_h = 24.0
    prev_group: str | None = None
    for c in cards["claims"]:
        g = c.get("group")
        if g is not None and g != prev_group:
            y += name_h
        row_y[c["id"]] = y
        y += row_h[c["id"]] + 14
        prev_group = g
    body_h = y - 14 - head_h
    total_h = head_h + body_h + PAD

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
             f'{CANVAS_W} {_f(total_h)}" width="{CANVAS_W}" '
             'font-family="Microsoft YaHei,sans-serif">']
    parts.append('  <defs><marker id="arr" markerWidth="8" markerHeight="8"'
                 ' refX="6" refY="4" orient="auto"><path d="M0,0 L7,4 L0,8 z"'
                 ' fill="#999999"/></marker>'
                 '<marker id="arrR" markerWidth="8" markerHeight="8" refX="6"'
                 ' refY="4" orient="auto"><path d="M0,0 L7,4 L0,8 z"'
                 ' fill="#c9302c"/></marker>'
                 '<marker id="arrDot" markerWidth="10" markerHeight="10"'
                 ' refX="5" refY="5" orient="auto"><circle cx="5" cy="5"'
                 ' r="3" fill="#c9302c"/></marker></defs>')
    parts += head
    # 组框化后列头退役:证据/命题/结论列由卡样式与关系词自传达,论点/
    # 事实组名由组框名条承载(「命题」列头原与组名条同位碰撞)。
    sup = {(e["src"], e["dst"]): e for e in graph["edges"]["supports"]}
    # 卡(命题行)+行内证据堆+行内边(证据─关系词→命题)
    claim_cx = col_x["claim"]
    ev_cx = col_x["ev"]
    # 组框(行式链 §三):论点组(蓝)/事实组(棕)圆角框包住组内命题行,
    # 框名条在框顶;证据堆仍贴身本行左侧(框外);「group」独立列撤销=
    # 框即组。框先画(置底,卡/边在上);框右缘收束结论在结论块画。
    group_boxes: list[dict] = []
    for g in cards["groups"]:
        ys = [row_y[cid] for cid in g["members"] if cid in row_y]
        ye = [row_y[cid] + claim_h.get(cid, 0)
              for cid in g["members"] if cid in row_y]
        if not ys:
            continue
        y1, y2 = min(ys), max(ye)
        accent = "#2b6cb0" if g["kind"] == "arg" else "#8a6d3b"
        bx = claim_cx - 10
        bw = _FB_W["claim"] + 20
        by = y1 - name_h
        bh = (y2 - y1) + name_h + 8
        parts += _frame_box(g["name"], bx, by, bw, bh, accent)
        group_boxes.append({"name": g["name"], "kind": g["kind"],
                            "bx": bx, "bw": bw,
                            "y_top": y1, "y_bot": y2,
                            "y_mid": (y1 + y2) / 2})
    fu_pos: dict[str, tuple[float, float]] = {}
    for c in cards["claims"]:
        ry = row_y[c["id"]]
        svg_c, _, _ = _claim_card_svg(
            {"label": c["label"], "status": c["status"],
             "anchor_name": c["anchor_name"], "gap": None,
             "history": c["history"], "premises": c["premises"],
             "status_line": c["status_line"],
             "morph_line": c.get("morph_line", ""),
             "next_line": c.get("next_line", ""),
             "flipback_line": c.get("flipback_line", "")},
            claim_cx, ry, w=_FB_W["claim"])
        parts.append(svg_c)
        ey = ry
        for eid, eh_ in ev_stack[c["id"]]:
            if eid.startswith("FU:"):
                fu = next(x for x in cards["fusions"] if x["id"] == eid[3:])
                svg_f, _ = _fusion_fb_svg(fu, ev_cx, ey, _FB_W["ev"] - 10)
                parts.append(svg_f)
                fu_pos[fu["id"]] = (ev_cx + _FB_W["ev"] - 10, ey + eh_ / 2)
                ey += eh_ + 6
                continue
            ecard = next(x for x in cards["evidence"] if x["id"] == eid)
            svg_e, _, _ = _ev_card_svg(
                {"num": ecard["num"], "summary": ecard["title"],
                 "sensitive": ecard["sensitive"], "platform": ecard["src"],
                 "root": "", "authoritative": ecard["authoritative"],
                 "same_root_count": ecard["same_root"],
                 "party": ecard["party"]},
                ecard["root_no"], ev_cx, ey, w=_FB_W["ev"] - 10)
            parts.append(svg_e)
            key = (eid, c["id"])
            red = sup.get(key, {}).get("ruling") == "矛盾"
            rel = next((e["relation"] for e in c["evidence"]
                        if e["evidence_id"] == eid), "")
            m = ev_merge.get(eid)
            rel_plain = rel
            if m is not None and m.get("first_row") == c["id"]:
                rel = f"{m['relation']} ×{m['n']}"   # 汇流边标注(首行)
                rel_plain = m["relation"]
            w_line = _rel_width(rel_plain, red)   # R2 分级(汇流=关系词同档)
            lx1, ly1 = ev_cx + (_FB_W["ev"] - 10), ey + eh_ / 2
            lx2, ly2 = claim_cx, ry + claim_h[c["id"]] / 2
            if abs(ly1 - ly2) < 4:   # 同高=直线(关系词置线上)
                parts.append(f'<line x1="{_f(lx1)}" y1="{_f(ly1)}"'
                             f' x2="{_f(lx2)}" y2="{_f(ly2)}"'
                             f' stroke="{_LINE_CON if red else _LINE_SUP}"'
                             f' stroke-width="{w_line}"'
                             f' marker-end="url(#{"arrR" if red else "arr"})"/>')
            else:                    # 折线(证据堆多行时)
                parts.append(f'<path d="M{_f(lx1)},{_f(ly1)} '
                             f'L{_f(lx1 + 12)},{_f(ly1)} '
                             f'L{_f(lx1 + 12)},{_f(ly2)} '
                             f'L{_f(lx2)},{_f(ly2)}" fill="none"'
                             f' stroke="{_LINE_CON if red else _LINE_SUP}"'
                             f' stroke-width="{w_line}"'
                             f' marker-end="url(#{"arrR" if red else "arr"})"/>')
            if rel:
                parts.append(f'<text x="{_f(lx1 + 16)}" y="{_f(ly1 - 4)}"'
                             f' font-size="9.5" fill="{"#c9302c" if red else "#59636e"}"'
                             f'>{_x(rel)}</text>')
            ey += eh_ + 6
    # 前提边已退役下图(批1.5 拍板①):依赖信息在命题卡「前提:」小字行
    # (与 dot 引擎同源 chain_cards);JSON derives 全量=机器对账不变
    claim_pos = {c["id"]: (claim_cx, row_y[c["id"]], claim_h[c["id"]])
                 for c in cards["claims"]}
    # 桥卡(虚线框;y=依据行中心均值,顺序防叠)
    if cards["bridges"]:
        bx = col_x["bridge"]
        prev_bot = head_h
        for i, b in enumerate(cards["bridges"]):
            ys = [row_y[bid] + claim_h.get(bid, 0) / 2
                  for bid in b["basis"] if bid in row_y]
            by = max(sum(ys) / len(ys) - 30, prev_bot + 8) if ys else prev_bot + 8
            bl = _wrap(b["text"], _FB_W["bridge"] - 16, 10.5)
            bh_ = 12 + len(bl) * 15 + 22
            parts.append(f'<rect x="{_f(bx)}" y="{_f(by)}"'
                         f' width="{_f(_FB_W["bridge"])}" height="{_f(bh_)}"'
                         ' rx="6" fill="#f7f3fb" stroke="#8e6fb8"'
                         ' stroke-width="1.2" stroke-dasharray="5 3"/>')
            ty2 = by + 12 + 11
            for ln in bl:
                parts.append(f'<text x="{_f(bx + 8)}" y="{_f(ty2)}"'
                             ' font-size="10.5" fill="'
                             f'{_INK}">{_x(ln)}</text>')
                ty2 += 15
            parts.append(f'<text x="{_f(bx + 8)}" y="{_f(by + bh_ - 7)}"'
                         ' font-size="9.5" fill="#8e6fb8">'
                         f'◇ {_x(b["line"])}</text>')
            for bid in b["basis"]:
                if bid in claim_pos:
                    sx2 = claim_cx + _FB_W["claim"]
                    sy2 = row_y[bid] + claim_h[bid] / 2
                    parts.append(f'<line x1="{_f(sx2)}" y1="{_f(sy2)}"'
                                 f' x2="{_f(bx)}" y2="{_f(by + bh_ / 2)}"'
                                 f' stroke="{_LINE_SUP}" stroke-width="1.5"'
                                 ' marker-end="url(#arr)"/>')
            prev_bot = by + bh_
    # 论证区组框右缘→结论收束线(R1 极性)+结论卡披露
    if cards["conclusion"]:
        con_x = col_x["con"]
        arg_boxes = [b for b in group_boxes if b["kind"] == "arg"]
        if arg_boxes:
            cnl = _wrap(cards["conclusion"]["label"],
                        _FB_W["con"] - 16, 12.5)
            vnl = _wrap(cards["conclusion"]["verdict_line"] + "(事实)",
                        _FB_W["con"] - 16, 10.5)
            # 结论卡披露(R3,批2 §二):竞争假设行(每落选一行)+票型行;
            # 无=零痕迹(不冒充);高度动态计入卡高。
            rls: list[str] = []
            for r in cards["conclusion"].get("runners_up") or []:
                rls.append(f'竞争假设:「{r["label"]}」·已证实 '
                           f'{r["n_verified"]}·居次')
            tk = cards["conclusion"].get("ticket")
            if tk:
                rls.append(f'票型:支撑组 {tk["support"]} ·拆台组 '
                           f'{tk["counter"]} ·在查组 {tk["checking"]}')
            rnl: list[str] = []
            for rl0 in rls:
                rnl += _wrap(rl0, _FB_W["con"] - 16, 9.5)
            con_h = (12 + len(cnl) * 17 + 6 + len(vnl) * 14 + 10
                     + len(rnl) * 12)
            con_y = (arg_boxes[0]["y_top"] + arg_boxes[-1]["y_bot"]) / 2 - con_h / 2
            parts.append(f'<rect x="{_f(con_x)}" y="{_f(con_y)}"'
                         f' width="{_f(_FB_W["con"])}" height="{_f(con_h)}"'
                         ' rx="8" fill="#d9edf7" stroke="#31708f"'
                         ' stroke-width="1.8"/>')
            ty3 = con_y + 12 + 13
            for ln in cnl:
                parts.append(f'<text x="{_f(con_x + _FB_W["con"] / 2)}"'
                             f' y="{_f(ty3)}" font-size="12.5"'
                             ' font-weight="bold" fill="#31708f"'
                             ' text-anchor="middle">'
                             f'研判结论 ·{_x(ln)}'
                             '</text>')
                ty3 += 17
            ty3 += 6
            for ln in vnl:
                parts.append(f'<text x="{_f(con_x + _FB_W["con"] / 2)}"'
                             f' y="{_f(ty3)}" font-size="10.5"'
                             f' fill="#31708f" text-anchor="middle">'
                             f'{_x(ln)}</text>')
                ty3 += 14
            for ln in rnl:
                parts.append(f'<text x="{_f(con_x + _FB_W["con"] / 2)}"'
                             f' y="{_f(ty3)}" font-size="9.5"'
                             f' fill="#59636e" text-anchor="middle">'
                             f'{_x(ln)}</text>')
                ty3 += 12
            # 组框右缘→结论支撑线(R1,批2 §二):组内含反向=线中段标
            # 「● 含反向 ×N」红小字+线尾红圆点 marker(arrDot;dot 引擎
            # 单边单色约束,红点字符在标签内——两引擎语义一致,形态差异
            # 如实备考);全正向/全无极性=现状灰线字节不变。
            gp_by = {x["group"]: x for x in
                     cards["conclusion"].get("group_polarity") or []}
            for b in arg_boxes:
                gp = gp_by.get(b["name"])
                rev = gp["n_rev"] if gp else 0
                sx = b["bx"] + b["bw"]      # 框右缘
                if rev > 0:
                    y_mid = b["y_mid"]
                    y_end = con_y + con_h / 2
                    parts.append(f'<line x1="{_f(sx)}"'
                                 f' y1="{_f(y_mid)}"'
                                 f' x2="{_f(con_x)}" y2="{_f(y_end)}"'
                                 f' stroke="{_LINE_SUP}" stroke-width="1.5"'
                                 ' marker-end="url(#arrDot)"/>')
                    parts.append(f'<text x="{_f((sx + con_x) / 2)}"'
                                 f' y="{_f((y_mid + y_end) / 2 - 4)}"'
                                 ' font-size="9.5" fill="#c9302c"'
                                 f' text-anchor="middle">● 含反向 ×{rev}</text>')
                else:
                    parts.append(f'<line x1="{_f(sx)}"'
                                 f' y1="{_f(b["y_mid"])}"'
                                 f' x2="{_f(con_x)}" y2="{_f(con_y + con_h / 2)}"'
                                 f' stroke="{_LINE_SUP}" stroke-width="1.5"'
                                 ' marker-end="url(#arr)"/>')
    # 命题级多源印证边(fallback):一卡→目标命题一条「印证 ×N」折线。
    if fu_pos:
        claim_pos2 = {c["id"]: (claim_cx, row_y[c["id"]], claim_h[c["id"]])
                      for c in cards["claims"]}
        for fu in cards["fusions"]:
            pos = fu_pos.get(fu["id"])
            tgt = claim_pos2.get(fu["claim_id"])
            if pos is None or tgt is None:
                continue
            x1, y1m = pos
            _cx2, ry2, ch2 = tgt
            y2m = ry2 + ch2 / 2
            rel = fu["relation"]
            parts.append(f'<path d="M{_f(x1)},{_f(y1m)} '
                         f'L{_f(x1 + 12)},{_f(y1m)} '
                         f'L{_f(x1 + 12)},{_f(y2m)} '
                         f'L{_f(claim_cx)},{_f(y2m)}" fill="none"'
                         f' stroke="{_LINE_SUP}"'
                         f' stroke-width="{_rel_width(rel, False)}"'
                         ' marker-end="url(#arr)"/>')
            rel_label = f"{rel} ×{fu['n']}"
            parts.append(f'<text x="{_f(x1 + 16)}" y="{_f(y1m - 4)}"'
                         ' font-size="9.5" fill="#59636e">'
                         f'{_x(rel_label)}</text>')
    # 桥→论点组框(据实推断;虚线;入框右缘)
    if cards["bridges"]:
        arg_box_by_name = {b["name"]: b for b in group_boxes
                           if b["kind"] == "arg"}
        for b in cards["bridges"]:
            tgt = arg_box_by_name.get(b["target_group"])
            if not tgt:
                continue
            b_ys = [row_y[bid] + claim_h.get(bid, 0) / 2
                    for bid in b["basis"] if bid in row_y]
            if not b_ys:
                continue
            by_ref = sum(b_ys) / len(b_ys)
            parts.append(f'<line x1="{_f(col_x["bridge"])}"'
                         f' y1="{_f(by_ref)}"'
                         f' x2="{_f(tgt["bx"] + tgt["bw"])}"'
                         f' y2="{_f(tgt["y_mid"])}" stroke="#8e6fb8"'
                         ' stroke-width="1.3" stroke-dasharray="5 3"'
                         ' marker-end="url(#arr)"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def ruling_list_lines(graph: dict) -> list[str]:
    """支撑明细(行式链与缺口退场 2026-11-09 由「裁定明细」改名;**md-chain
    与 html 链卷面同源函数**,两处一致=同源函数产文本)。只列裁定在案项
    (支持/矛盾/时效刷新=DRAW_RULINGS 同口径);无关/未裁不逐条列,每命题
    无在案材料=整命题行省略(JSON rulings 全量不动,总览另保留全局计数)。
    方向标后附引文(件 5 起意见落账 quote;旧台账无 quote=自动回落只列方向,
    两态各自如实);同根折并标注=同一命题名下同根的第二件起(独立根计数
    不虚高)。"""
    out: list[str] = []
    for rc in graph.get("rulings") or []:
        shown = [it for it in rc.get("items") or []
                 if it["ruling"] in DRAW_RULINGS]
        if not shown:
            continue   # 每命题无在案材料=整命题行省略
        st = {"已证实": "〔已证实〕", "矛盾": "〔已反对〕",
              "待证实": "〔待查证〕"}.get(rc["status"], "")
        out.append(f"- {rc['label']}{st}"
                   f"(材料 {len(shown)} 份·独立根 {rc['n_roots']})")
        for it in shown:
            seg = f"  - [{it['num']}] {_clip_ev(it['title'])}"
            if it.get("src"):
                seg += f"({it['src']})"
            seg += f" → {it['ruling']}"
            if it.get("quote"):
                seg += f"(「{_clip_ev(it['quote'], 40)}」)"
            if it.get("dup_root"):
                seg += "(同根折并,不另计数)"
            out.append(seg)
    return out


def render_md_chain(graph: dict) -> str:
    """文字版证据链(§五 格式矩阵新增:报告正文嵌入)。行序=graph.rows;
    结论→论点→命题(证据[En],关系词→方向标)缩进列表;+待查证小区清单
    +支撑明细(行式链与缺口退场:文字版无版面问题,待查证清单是其天然
    组成);零 LLM 确定性。"""
    cards = chain_cards(graph)
    ev_by_id = {e["id"]: e for e in cards["evidence"]}
    lines: list[str] = ["### 证据链(文字版)", ""]
    con = cards["conclusion"]
    if con:
        lines.append(f'研判结论「{con["label"]}」:{con["verdict_line"]}(事实)'
                     "——自下而上:证据→命题→论点→结论")
        for r in con.get("runners_up") or []:
            lines.append(f'竞争假设:「{r["label"]}」·已证实 '
                         f'{r["n_verified"]}·居次')
        tk = con.get("ticket")
        if tk:
            lines.append(f'票型:支撑组 {tk["support"]} ·拆台组 '
                         f'{tk["counter"]} ·在查组 {tk["checking"]}')
        lines.append("")

    def _claim_block(c: dict) -> None:
        st_mark = {"已证实": "〔已证实〕", "矛盾": "〔已反对〕",
                   "待证实": "〔待查证〕"}.get(c["status"], "")
        lines.append(f'   - {c["label"]}{st_mark}')
        for f in fu_by_claim.get(c["id"], []):
            anchors = " ".join(f"[{a}]" for a in f["anchors"])
            party = " ·含当事方自述" if f["party"] else ""
            lines.append(f'     ←〔{f["relation"]} ×{f["n"]}〕'
                         f'▸ 多源印证:{f["text"]}({anchors}{party})')
        if c.get("premises"):
            lines.append(f'     (前提: {"、".join(c["premises"])})')
        # 证据形态注记行(空=零痕迹;补证通道/翻回条件随缺口退场撤出)
        if c.get("morph_line"):
            lines.append(f"     {c['morph_line']}")
        for e in c["evidence"]:
            ecard = ev_by_id.get(e["evidence_id"])
            if ecard is None:
                continue
            seg = (f'     ←〔{e["relation"]}〕[{ecard["num"]}] '
                   f'{_clip_ev(ecard["title"])}({ecard["src"]})')
            # B-4 方向标+引文(件 5)+同根折并标注
            if e.get("ruling"):
                seg += f" → {e['ruling']}"
            if e.get("quote"):
                seg += f"(「{_clip_ev(e['quote'], 40)}」)"
            if e.get("dup_root"):
                seg += "(同根折并,不另计数)"
            lines.append(seg)

    group_names = []
    for g in cards["groups"]:
        if g["name"] not in group_names:
            group_names.append(g["name"])
    by_group: dict[str | None, list[dict]] = {}
    for c in cards["claims"]:
        by_group.setdefault(c["group"], []).append(c)
    fu_by_claim: dict[str, list[dict]] = {}
    for f in cards["fusions"]:
        fu_by_claim.setdefault(f["claim_id"], []).append(f)
    for gi, gname in enumerate(group_names):
        g = next(x for x in cards["groups"] if x["name"] == gname)
        prefix = "论点" if g["kind"] == "arg" else "事实组"
        lines.append(f'{gi + 1}. {prefix}「{gname}」')
        for c in by_group.get(gname, []):
            _claim_block(c)
    rest = by_group.get(None) or []
    if rest:
        lines.append(f'{len(group_names) + 1}. 独立事实(止于命题层)')
        for c in rest:
            _claim_block(c)
    for b in cards["bridges"]:
        lines.append(f'◇ 解读:「{b["text"]}」({b["line"]})'
                     f' → 支撑「{b["target_group"]}」')
    # 待查证小区清单(B-3:缺口图退役后文字版天然承载;资格在数据层)
    if cards["zone"]:
        lines.append("")
        lines.append("#### 待查证(未上图)")
        for z in cards["zone"]:
            lines.append(f"- 〔{z['badge']}〕{z['label']}——{z['note']}")
    # 支撑明细(与 html 链卷面同源函数产文本,两处一致)
    rl = ruling_list_lines(graph)
    if rl:
        lines += ["", "#### 支撑明细"] + rl
    if len(lines) == 2:
        return ""
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- HTML 整页

_CSS = (
    "body{font-family:'Microsoft YaHei',sans-serif;max-width:1040px;"
    "margin:24px auto;padding:0 16px 60px;line-height:1.7;color:#1f2328;"
    "background:#fafafa}"
    "h1{font-size:20px;border-bottom:2px solid #2b6cb0;padding-bottom:6px}"
    "h2{font-size:16px;margin-top:40px;border-left:4px solid #2b6cb0;"
    "padding-left:10px}"
    ".note{color:#59636e;font-size:13px}"
    ".overview{background:#fff;border:1px solid #e0e0e0;border-radius:10px;"
    "padding:12px 18px;font-size:14px;margin:10px 0}"
    "svg{background:#fff;border:1px solid #e8e8e8;border-radius:8px}"
    "table{border-collapse:collapse;font-size:13px}td,th{border:1px solid "
    "#d0d7de;padding:4px 10px}"
)


def main_view_html(graph: dict) -> str:
    """人看主载体整页(行式链与缺口退场:横向证据链=行式主引擎主图)。
    卷面构成:卷首总览+主图(行式贴身链)+**支撑明细**(+证据附录);缺口图
    section 与待查证小区已退役(render_gap_svg 函数保留一个版本期=可回溯,
    照 mermaid 退役先例);零外部依赖。"""
    m = graph["meta"]
    ov_html = "".join(f"<div>{_x(ln)}</div>" for ln in overview_lines(graph))
    main_svg = render_chain_svg(graph)
    parts = [
        "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
        f"<title>证据链卷面 · {_x(m['event_id'])}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>证据链卷面 · {_x(m['event_id'])}</h1>",
        '<h2>卷首总览</h2><div class="overview">' + ov_html + "</div>",
    ]
    if main_svg:
        parts.append("<h2>主图(横向证据链:证据→命题→论点→结论)</h2>"
                     + main_svg)
        parts.append('<p class="note">自左向右=推理方向。边标关系词:直接证明'
                     "(权威源)/印证(双根)/佐证(单根)/反证(红)/时效更新;"
                     "线越粗=支撑越硬(直接证明>印证>佐证)。收束线标「含反向"
                     " ×N」=该论点组内含反向解读(拆台证据),线尾红点;结论卡"
                     "披露竞争假设与票型(支撑组/拆台组/在查组)。▸ 多源印证 N 件卡"
                     "=同一命题的独立来源共同印证(实线框;[En] 锚=原始材料编号;"
                     "被融合材料在主图折叠,JSON 与证据附录全量保留)。命题间前提依赖"
                     "不画线,见命题卡「前提:」小字;桥=◇解读卡"
                     "(依据 N 项事实·已查实 M→据实推断→论点)。同根材料互不为"
                     "独立佐证(见卡片注记);标 ×N 的边=N 条同关系边汇流(同证据"
                     "同组同关系词),明细见机器对账 JSON 与证据附录 [En] 锚表;"
                     "同文谱系命题已归并为一卡(全轨迹见卡内状态史行,正本仍在"
                     "台账);待证实命题=主图虚框卡(注记证据形态);每条材料的"
                     "裁定方向逐条见下方支撑明细;解读型命题分流(总览注记),"
                     "正本仍在台账。</p>")
    # 支撑明细(与 md-chain 同源函数产文本,两处一致——html 侧只过转义,
    # 不再各写一套模板)
    rl = ruling_list_lines(graph)
    if rl:
        parts.append('<h2>支撑明细</h2><div class="overview">'
                     + "".join(f"<div>{_x(ln)}</div>" for ln in rl)
                     + "</div>")
    evs = graph["nodes"]["evidence"]
    if evs:
        rno = _root_no_map(graph)
        rows = "".join(
            f'<tr id="ev-{e["num"]}"><td>[{e["num"]}]</td>'
            f"<td>{_x('〔internal〕' if e['sensitive'] else (e['title'] or '(无题)'))}</td>"
            f"<td>{_x(rno.get(e['root'], e['root']))}"
            f"{'(权威)' if e['authoritative'] else ''}</td>"
            f"<td>{_x(e['platform'])}</td>"
            f"<td>{_x(e['summary'])}</td></tr>" for e in evs)
        parts.append("<h2>证据附录(图中 [En] 锚落点)</h2><table><thead>"
                     "<tr><th>编号</th><th>标题</th><th>谱系根</th>"
                     "<th>来源</th><th>概括</th></tr></thead>" + rows
                     + "</table>")
    parts.append("</body></html>")
    return "\n".join(parts)
