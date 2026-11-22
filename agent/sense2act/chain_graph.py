# -*- coding: utf-8 -*-
"""证据链形态图(2026-11-05 方案施工)——链呈现从「附录拼接」改图形态。

方案=《实施方案/证据链形态图-设计方案-20261105.md》(§七施工清单;前置核实
三件已核:①parent_claim_id 已落库[缺口#1 零成本,两写入口=add_claim 派生口/
install_blueprint 跨 run 撞号口];②evidence_graph mermaid 管线可复用
[_mq/_esc/fence_mermaid];③hypothesis_seeds.json 无 polarity 字段[人工覆写
口=加法式 polarity map,旧文件兼容])。

四类节点(术语不上图面,分层位置自传达;数据层 kind 保留机器术语供对账):
  研判层 hypothesis(假设+现势)/结论层 claim(gist 短名做标签,立住=实心
  没立住=虚线)/证据层 evidence(材料名+出处,同 lineage_root 聚类框=
  十家转载一根的可视化)/缺口层 gap(虚位节点:缺什么+走哪个通道)。
边:载明 evidence→claim(权威根加粗+独立根编号+翻转条件边标注[此边失效→
  哪些 claim 回待证实;D 线呈现侧前身,留接口注释])/派生 claim→claim
  (parent_claim_id,前提→依赖者)/共据 claim↔claim(共享支持根=冗余拓扑,
  上限防线)/传导 claim→hypothesis(方向极性正反线型)/同源折并=聚类框。

铁律(方案 §四/§八,kb_chain 同源纪律):
- 渲染期**零 LLM 零网络**——查询时现算纯函数;polarity/通道建议是生成期
  旁车(chain_gen.py 落库 chain_sidecar 表),本模块只读消费+指纹校验
  (漂移弃用=重跑生成期即刷新),seeds 人工标 polarity 的**人工优先**;
- 三态只属于 claim:图是呈现,不产生新状态;LLM 产物永不回流判决与评分
  (负测试锁:s6_verdict/credibility 不 import 本模块与 chain_gen);
- 失败不伪装:旁车缺项/漂移/白名单外=图上不画该边属性,不伪装人工标;
- **噪音平铺病灶的解法**:无关票/未裁证据不产节点(过程留痕不进图),
  计入缺口节点的尝试注记(「已查 N 次」);
- kb_chain 五投影返回结构零改动(chain_topic 仅加法式 with_graph 键);
- 确定性:全部排序显式、零 now()(meta.as_of 取 events.updated_at 源侧
  时间),同输入重算逐字节一致;mermaid 纪律照 evidence_graph 先例
  (_mq 引号防御/中文宽度 CSS 由 html 侧负责/[n] 编号入标签)。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from .credibility import anchor_name, collect_factors
from .evidence_graph import _esc, _mq, fence_mermaid


class ChainGraphError(Exception):
    """图装配失败(主库/claim 不存在——失败不伪装)。"""


MAX_CO_ROOT_EDGES = 40   # 共据边上限(冗余拓扑噪音防线;超限省略并注记,照 MAX_CLAIMS 先例)
CLIP_EV = 26             # 证据标题裁宽(机器对账投影 label;人看卡走 summary 全文)
CLIP_RATIONALE = 24      # 传导边注释裁宽(边标签版面防线;机器对账投影保留)
MAX_HISTORY_NODES = 4    # 状态史行节点上限(复查链多轮结算防线;超限截断+注记总次数)
DRAW_RULINGS = ("支持", "矛盾", "时效刷新")   # 产证据节点的裁定;无关/未裁=过程留痕不进图

# 缺口四型(方案 §六;判据与 s6 判决梯/锚点表同源——见 gap_type_of)
GAP_LABELS = {
    "未触及": "尚无任何查证尝试",
    "查证排除": "已查证未见载明(无关票/不可达)",
    "孤证": "仅一根独立支持,缺第二独立根",
    "谓词不足": "证据量已达判决门槛,载明性核对未过",
}
GAP_TYPES = tuple(GAP_LABELS)

# 补证通道「尚差」短语(呈现完整化 B-1:缺口图退役后信息收编虚框卡;
# 缺口型→差什么的人话模板,通道建议仍消费 channel 旁车)
_GAP_NEXT = {
    "未触及": "首次查证",
    "查证排除": "换证据通道补查",
    "孤证": "第二个独立来源相互印证",
    "谓词不足": "核心谓词载明复核(候审)",
}
# 待查证小区收编两型(呈现完整化 B-2:无边命题上图不上线;孤证/谓词不足
# 有边=主图虚框,不入小区)
ZONE_GAP_TYPES = ("查证排除", "未触及")
# 矛盾卡翻回条件注记(用户话,照缺口图矛盾行措辞;红只留给反证线,本行灰)
FLIPBACK_NOTE = "翻回条件:两独立正向方可翻回"

SEED_PLATFORM = "signal_layer"   # 信封种子/检测器字段陈述(§4-4 不入材料层的机器判据;生成期同判据)
_ROLE_RANK = {"头": 0, "普通": 1, "尾": 2}   # 主语实体排序(机械事实组聚类键)


def validate_grouping(groups, member_ids) -> bool:
    """覆盖完整性机械校验(卷面改造 §五-护栏①):groups 必须恰好覆盖
    member_ids——漏/多/重/虚构 id 一律拒收(退机械平铺)。生成期落库前、
    消费侧读取后**两侧同校验**(照通道白名单两侧同判据先例)。"""
    if not isinstance(groups, list) or not member_ids:
        return False
    seen: list[str] = []
    for g in groups:
        if not isinstance(g, dict):
            return False
        name = g.get("name")
        members = g.get("members")
        if not isinstance(name, str) or not name.strip():
            return False
        if not isinstance(members, list) or not members:
            return False
        for m in members:
            if not isinstance(m, str) or not m:
                return False
            seen.append(m)
    return sorted(seen) == sorted(set(member_ids))


def _claim_subject(cid: str,
                   ent_by_claim: dict[str, list[tuple[int, str]]]) -> str:
    """claim 主语实体(机械事实组聚类键):候选按(角色, 名)升序取第一。"""
    named = sorted(ent_by_claim.get(cid) or [])
    return named[0][1] if named else "(未归一)"


def pick_argument(hyps: list[dict], hc_map: dict[str, list[str]],
                  status_by_id: dict[str, str],
                  exclude_ids: tuple | list | set = ()) -> tuple[dict | None, list[dict]]:
    """论证区资格与排序(卷面改造 §4-1 机械判据;**生成期与装配两侧同判据
    函数**——chain_gen 跑 LLM grouping 前与本模块装配用同一份,防判据漂移)。

    资格=有已证实成员(候审不算——章是挣来的,必须查实);排序=已证实
    成员数降序→记分降序→hypothesis_id 升序,取第 1;落选者进总览一行。
    hyps 条目需含 hypothesis_id(+score 可选)。返回 (top|None, runners_up):
    top 附 n_verified/n_members/member_ids。
    资格收紧(P4,三阶段 §三.3):exclude_ids=解读型 claim 集合(insights
    legacy 镜像反查),**不计入已证实数与资格**——章只能由事实挣,两侧同
    传同一集合(负测试锁:资格不含解读型)。
    """

    ex = set(exclude_ids or ())

    def _nv(hid: str) -> int:
        return sum(1 for cid in hc_map.get(hid, [])
                   if cid not in ex and status_by_id.get(cid) == "已证实")

    qual = [dict(h) for h in hyps if _nv(h["hypothesis_id"]) > 0]
    qual.sort(key=lambda h: (-_nv(h["hypothesis_id"]),
                             -h.get("score", 0), h["hypothesis_id"]))
    if not qual:
        return None, []
    top = qual[0]
    mem = sorted(hc_map.get(top["hypothesis_id"], []))
    top = dict(top, n_verified=_nv(top["hypothesis_id"]), n_members=len(mem),
               member_ids=mem)
    return top, qual[1:]

# 状态史动词(卷面改造 §3.1 命题卡三层之三;时序链精华吸收位——人话模板,
# 状态只来自 claim_transitions 正本,行是呈现不产生新状态)
_HISTORY_VERB = {
    ("已证实", None): "查实",
    ("矛盾", None): "被推翻",
    ("待证实", "矛盾翻转"): "翻转回落",
    ("待证实", "时效翻转"): "时效回落",
    ("待证实", "状态刷新"): "复查刷新",
}


# ---------------------------------------------------------------- 判据(纯函数)

def gap_type_of(status: str, s: int, a: bool, tried: bool) -> str | None:
    """缺口四型判据:镜像 credibility.anchor_name 待证实分支(逐支对齐)。

    未触及(s=0∧没查过)/查证排除(s=0∧查过)[=锚点「弱(查过没有)"]/
    孤证(s=1 非权威)[=「弱(孤证)"]/谓词不足(s≥2∨权威单根仍在待证实
    [=「候审」:证据量够判决梯、卡在载明性——谓词核对拦回/单源例外不适
    用/卷宗截断均落此型])。非待证实=无缺口(已证实/矛盾)。判据只消费
    判决梯已承认的量,与判决自动同源——判决梯改,本函数同步改+测试锁。
    """
    if status != "待证实":
        return None
    if s >= 2 or (s == 1 and a):
        return "谓词不足"
    if s == 1:
        return "孤证"
    if tried:
        return "查证排除"
    return "未触及"


# 关系词六词(三阶段 §4.2 拍板 P5:机械骨架,判据=判决梯已承认的量;
# 命题→论点→结论归属由位置表达不标词,红只留拆台)
def relation_of(ruling: str, root: str, sup_roots: list, auth: set) -> str:
    """载明边关系词(纯函数,两侧同源):直接证明(支持∧权威登记源)/
    印证(支持∧双根成员)/佐证(支持∧单根)/反证(矛盾)/时效更新
    (时效刷新);前提=派生边(装配处直接标,不经本函数)。"""
    if ruling == "矛盾":
        return "反证"
    if ruling == "时效刷新":
        return "时效更新"
    if root in auth:
        return "直接证明"
    return "印证" if len(sup_roots or []) >= 2 else "佐证"


# 同文谱系归并(卷面改造四阶段 §五,批1-C:呈现层省线第一主药;机械零 LLM)
MERGE_SIM_MIN = 0.80   # 家族近文阈值(规范后 difflib ratio;忻州家族实测 0.86-0.93)


def _decimal_token(raw: str) -> str:
    """数值口径规范化:999,910 / 999910.0 / 999910 归一。

    只用于同一撞号谱系闭包内的保守语义键，不做跨谱系、
    跨谓词的全局文本聚类。
    """
    token = (raw or "").replace(",", "")
    try:
        value = float(token)
    except ValueError:
        return token
    if value.is_integer():
        return str(int(value))
    return ("%.12f" % value).rstrip("0").rstrip(".")


def _family_semantic_key(text: str) -> tuple[str, str] | None:
    """同文家族的确定性谓词键(首批只收金额断言)。

    门禁:
    - 候选仍必须先在同一 lineage 撞号闭包内;
    - 只收「预算金额为 N 元」的客观谓词;
    - 出现历史中位数/异常/偏离/比较等判断词立即退出，
      防止把「金额是多少」与「金额是否异常」误并。
    """
    t = unicodedata.normalize("NFKC", text or "")
    if any(k in t for k in ("中位数", "异常", "偏离", "相比", "高于", "低于",
                            "倍数", "波动", "常态", "是否")):
        return None
    m = re.search(r"预算金额(?:约)?(?:为|是|=)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*元", t)
    if not m:
        return None
    return ("budget_amount_yuan", _decimal_token(m.group(1)))


def _derived_suffix(cid: str, parent_cid: str) -> bool:
    """撞号边形态判定:dst 的 draft 段=parent 的 draft 段+小写尾缀(字母
    +可选数字;跨 run 撞号口 _resolve_claim_id 的 d 后缀形态与旧 a 后缀
    链式都覆盖)。**显式 parent 的真派生**(id 无此形态,如 supplier_b
    C5←C1)不进家族闭包——家族治的是撞号噪音(§五 病灶),真派生是推理
    谱系,不连坐家族合并;误入闭包的残余风险由近文双条件兜底(异文不并)。"""
    cd = cid.rsplit(":", 1)[-1]
    pd = parent_cid.rsplit(":", 1)[-1]
    return (cd != pd and cd.startswith(pd)
            and re.fullmatch(r"[a-z]+\d*", cd[len(pd):]) is not None)


def lineage_families(claim_rows: list[dict]) -> list[list[str]]:
    """同文谱系家族判定(§五-1,纯函数确定性,可复算):撞号谱系闭包
    连通分量(只沿撞号边,_derived_suffix 形态判定)圈定候选,分量内
    **近文关系自身成簇**(规范化后 difflib ratio ≥ MERGE_SIM_MIN 的对
    做连通子聚类,≥2 才成族)=双条件;真异文成员独立不连坐整族(防误并,
    忻州实账:c17/c22 原条与后代为语义演进的真异文,子聚类口径下后代
    近文代合并、原条独立——见方案 §十 施工实录备考)。含转生成员的
    子簇不合=转生是新增命题语义不同(§五-4,「由解读转生」徽章原样)。
    子簇内 claim_id 升序输出;家族列表按首成员 id 升序(确定性)。"""
    ids = sorted(r["claim_id"] for r in claim_rows)
    idset = set(ids)
    text_of = {r["claim_id"]: _norm(r["text"]) for r in claim_rows}
    reborn = {r["claim_id"] for r in claim_rows if r.get("reborn_from")}
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for r in claim_rows:
        p = r.get("parent_claim_id")
        if p and p in idset and _derived_suffix(r["claim_id"], p):
            # 父不在装配范围(as_of 截断)=边不收;非撞号形态=真派生边不收
            adj[r["claim_id"]].add(p)
            adj[p].add(r["claim_id"])

    semantic_of = {r["claim_id"]: _family_semantic_key(r["text"])
                   for r in claim_rows}

    def _near(a: str, b: str) -> bool:
        semantic_match = (semantic_of.get(a) is not None
                          and semantic_of.get(a) == semantic_of.get(b))
        return (semantic_match or text_of[a] == text_of[b]
                or difflib.SequenceMatcher(
                    None, text_of[a], text_of[b]).ratio() >= MERGE_SIM_MIN)

    seen: set[str] = set()
    fams: list[list[str]] = []
    for cid in ids:
        if cid in seen:
            continue
        comp, stack = [], [cid]
        seen.add(cid)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in sorted(adj[x]):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        if len(comp) < 2:
            continue
        comp.sort()
        near_adj: dict[str, set[str]] = {x: set() for x in comp}
        for i, a in enumerate(comp):
            for b in comp[i + 1:]:
                if _near(a, b):
                    near_adj[a].add(b)
                    near_adj[b].add(a)
        seen2: set[str] = set()
        for x in comp:
            if x in seen2:
                continue
            sub, stack2 = [], [x]
            seen2.add(x)
            while stack2:
                y = stack2.pop()
                sub.append(y)
                for z in sorted(near_adj[y]):
                    if z not in seen2:
                        seen2.add(z)
                        stack2.append(z)
            if len(sub) >= 2 and not any(y in reborn for y in sub):
                fams.append(sorted(sub))
    return fams


def family_history_line(member_ids: list[str], created_by: dict[str, str],
                        hist_by: dict[str, list[dict]]) -> str:
    """家族合并状态史行(§五-2 拍板①:史行显全轨迹,已证实历史不丢)。
    全家族成员的立项(created_at)与状态迁移合并按时间排序,逐个过
    _HISTORY_VERB 人话模板;截断纪律同 history_line(MAX_HISTORY_NODES
    +注记总次数)。时间取正本字段禁 now;同刻按 claim_id 排序(确定性)。"""
    events: list[tuple[str, str, str]] = []   # (排序键 ts, claim_id, 文本)
    for cid in member_ids:
        ca = created_by.get(cid, "")
        if ca:
            events.append((ca, cid, f"立项 {ca[5:10]}"))
        for t in hist_by.get(cid, []):
            verb = _HISTORY_VERB.get((t["new_status"], t["cause"]))
            if verb:
                ts = str(t["ts"])
                events.append((ts, cid, f"{verb} {ts[5:10]}"))
    events.sort(key=lambda e: (e[0], e[1]))
    nodes = [e[2] for e in events]
    if len(nodes) > MAX_HISTORY_NODES:
        return (" → ".join(nodes[:MAX_HISTORY_NODES])
                + f"(共 {len(nodes) - 1} 次状态变化)")
    return " → ".join(nodes)


def load_manual_polarity(seeds_path: str | Path | None) -> dict[str, dict[str, str]]:
    """hypothesis_seeds.json 人工 polarity 覆写口(加法式,旧文件零兼容成本)。

    条目可选 "polarity": {draft_id: "正向"|"反向"};值域外的键忽略(失败
    不伪装);无该文件/无该键=空表(现行为)。人工标了的本模块与生成期都
    **人工优先**(chain_gen 跳过,本函数消费时直取)。
    """
    if not seeds_path:
        return {}
    p = Path(seeds_path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    out: dict[str, dict[str, str]] = {}
    for entry in data if isinstance(data, list) else []:
        pol = entry.get("polarity") if isinstance(entry, dict) else None
        label = entry.get("label") if isinstance(entry, dict) else None
        if not isinstance(pol, dict) or not label:
            continue
        marked = {str(k): v for k, v in pol.items() if v in ("正向", "反向")}
        if marked:
            out[str(label)] = marked
    return out


def channel_whitelist(config_dir: str | Path = "data/config") -> set[str]:
    """通道白名单(机器闸兜幻觉的判据):platform_rules.json 已登记通道并集
    (各规则 platforms + fallback_platforms)。生成期建议落库前与本模块消费
    时**两侧同校验**;未登记通道=丢弃计数,不伪装成有效建议。"""
    p = Path(config_dir) / "platform_rules.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    out = set(data.get("fallback_platforms") or [])
    for r in data.get("platform_rules") or []:
        out |= set(r.get("platforms") or [])
    return out


def _norm(text: str) -> str:
    """旁车指纹归一口(NFKC+折叠空白;与引文比对口径同族)。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def polarity_fp(text: str, status: str, gist: str, hyp_label: str) -> str:
    """极性旁车指纹:命题(文本+状态+gist)+假设标签——任一漂移即弃用
    (渲染期重算比对,重跑生成期即刷新)。"""
    payload = {"gist": gist or "", "hyp": hyp_label or "",
               "status": status, "text": _norm(text)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def channel_fp(text: str, status: str, gap: str) -> str:
    """通道旁车指纹:命题+状态+缺口型(缺口型变=查证形态变,建议失效)。"""
    payload = {"gap": gap or "", "status": status, "text": _norm(text)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def ev_summary_fp(title: str, content: str) -> str:
    """材料概括旁车指纹:材料原文(title+content)——材料变=概括失效。"""
    payload = {"t": _norm(title), "c": _norm(content)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def grouping_fp(hyp_label: str, member_ids: list[str]) -> str:
    """论点分组旁车指纹:假设标签+成员命题集合(成员集变=分组失效)。"""
    payload = {"for": "grouping", "hyp": hyp_label or "",
               "members": sorted(member_ids or [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def narrative_fp(hyp_label: str, member_ids: list[str]) -> str:
    """章首研判旁车指纹:同 grouping 输入(独立函数防单边改判据;kind 分表)。"""
    payload = {"for": "narrative", "hyp": hyp_label or "",
               "members": sorted(member_ids or [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def legacy_fp(text: str) -> str:
    """存量/上游解读指纹(三阶段 §二.2):锚定原文本——文本变=镜像失效
    (P9:fingerprint 锚定原文本;呈现层反查分流以本指纹为有效闸)。"""
    payload = {"for": "insight", "text": _norm(text)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def fusion_fp(members: list[str], fps: dict[str, str]) -> str:
    """融合旁车指纹(批2 §四):成员材料集+各成员 ev_summary_fp 组合——
    材料增删/内容变=漂移弃用重跑即刷;**组名不参与指纹**(装配侧按成员
    反查归属组,分组漂移不影响挂载)。"""
    payload = {"for": "fusion", "members": sorted(members),
               "fps": {k: fps[k] for k in sorted(fps)}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def interp_fp(insight_text: str, basis: list[str]) -> str:
    """解读锚点指纹(§六.2):解读文本+basis 集——任一漂移即弃用。"""
    payload = {"for": "interpretation", "text": _norm(insight_text),
               "basis": sorted(basis or [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def load_present_rules(config_dir: str | Path = "data/config") -> dict:
    """卷面呈现模式表(§七.2):claim_present_rules.json 版本化,改表即生效
    +测试锁(照 ess_rules 数值参数纪律)。缺文件=空表=去壳/分流全关
    (确定性零行为变化)。键:shells/interpretation/gap_hint/meta_info/
    gap_hint_strip/bridge_min_verified。"""
    p = Path(config_dir) / "claim_present_rules.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def classify_claim(text: str, rules: dict) -> str | None:
    """解读型三支机械判据(§三.2):前缀命中,顺序=定性(桥)→缺口(线索)
    →元信息(挡除),先命中先归;全不中=None(保守=留作事实命题,拒绝误分流)。"""
    t = (text or "").lstrip()
    for cls in ("interpretation", "gap_hint", "meta_info"):
        for pat in rules.get(cls) or ():
            if t.startswith(pat):
                return cls
    return None


def strip_shell(text: str, rules: dict) -> str:
    """转述壳剥离(§三.1):最长前缀优先,剩余逐字保留(省略非压缩纪律)。
    未命中=原文返回(调用方按三态决定是否剥——待证实保留壳)。"""
    t = (text or "").strip()
    for shell in sorted(rules.get("shells") or (), key=len, reverse=True):
        if t.startswith(shell):
            return t[len(shell):].strip()
    return t


def claim_tier(text: str, rules: dict) -> str:
    """报告正文输入三档分流判据(第二批 M1,2026-11-08 起公共化到此:
    S7 渲染层与起草输入侧[d_hooks 钩子/finalize]同判据同源,探针同轴)。
    确定性前缀匹配,模式表承载——挡除=interpretation/meta_info 两类前缀+
    report_block_extra 新增(种子标签/缺口登记家族+basisGaps 未知变体默认
    挡),转写=report_rewrite_prefixes(relevance/domain 类,长前缀)。最长
    匹配优先、转写长前缀胜过挡除短前缀(同族 basisGaps 分档:基线缺失→挡,
    相关性/领域→转写);全不中=""(照旧进正文输入)。零行为键缺席=分流
    全关(照 load_present_rules 缺文件纪律)。"""
    t = (text or "").lstrip()
    rws = rules.get("report_rewrite_prefixes") or []
    best_r = max((len(p) for p in rws if p and t.startswith(p)), default=0)
    blk = list(rules.get("report_block_extra") or [])
    for cls in ("interpretation", "meta_info"):
        blk += rules.get(cls) or ()
    best_b = max((len(p) for p in blk if p and t.startswith(p)), default=0)
    if best_r and best_r >= best_b:
        return "rewrite"
    if best_b:
        return "block"
    return ""


def sidecar_rows(kb, event_id: str, kind: str) -> list[dict]:
    """insights 读口(P7 双名兼容):先 insights 正名;OperationalError 退
    chain_sidecar(升格前老库 ro 打开无法建视图);再退=空表(如实,照
    _load_sidecar 旁车缺项纪律)。"""
    if kb is None:
        return []
    for t in ("insights", "chain_sidecar"):
        try:
            return [dict(r) for r in kb.execute(
                "SELECT * FROM {t} WHERE event_id=? AND kind=?".format(t=t),
                (event_id, kind)).fetchall()]
        except sqlite3.OperationalError:
            continue
    return []


def load_manual_grouping(seeds_path: str | Path | None,
                         event_id: str | None = None) -> dict[str, list[dict]]:
    """hypothesis_seeds.json 人工 grouping 覆写口(加法式,旧文件零兼容成本;
    卷面改造 §五-④:分组类人工优先,polarity map 同款)。

    条目可选 "grouping": [{"name": "论点名", "members": ["draft_id",…]}];
    name 非空串且 members 非空串列表才收(不合法条目忽略=失败不伪装);
    人工标了的假设生成期跳过、装配侧直取(优先级:人工 > 旁车 > 机械平铺)。
    """
    if not seeds_path:
        return {}
    p = Path(seeds_path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    out: dict[str, list[dict]] = {}
    for entry in data if isinstance(data, list) else []:
        scoped = entry.get("event_id") if isinstance(entry, dict) else None
        if scoped and event_id and scoped != event_id:
            continue
        groups = entry.get("grouping") if isinstance(entry, dict) else None
        label = entry.get("label") if isinstance(entry, dict) else None
        if not isinstance(groups, list) or not label:
            continue
        ok = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            name = g.get("name")
            members = g.get("members")
            if (isinstance(name, str) and name.strip()
                    and isinstance(members, list)
                    and members and all(isinstance(m, str) and m for m in members)):
                ok.append({"name": name.strip(), "members": list(members)})
        if ok:
            out[str(label)] = ok
    return out


def inference_fp(label: str, basis_rows: list[dict], rule: str,
                 inference_text: str, condition_or_gap: str,
                 target_hypothesis_ids: list[str]) -> str:
    """结构化推理指纹:前提正本文本+三态、规则、条件结论和目标集。

    本指纹不参与判决，只用于证明主图推理卡确实绑定了当前
    claim 正本与目标假设；任一前提文本/状态漂移都会产生新指纹。
    """
    payload = {
        "for": "structured_inference",
        "label": _norm(label),
        "basis": [{"claim_id": r["claim_id"], "text": _norm(r["text"]),
                   "status": r["status"]} for r in sorted(
                       basis_rows, key=lambda x: x["claim_id"])],
        "rule": _norm(rule),
        "inference_text": _norm(inference_text),
        "condition_or_gap": _norm(condition_or_gap),
        "targets": sorted(target_hypothesis_ids),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def load_manual_inferences(seeds_path: str | Path | None,
                           event_id: str) -> list[dict]:
    """读取版本化人工推理桥定义；只做形状预过滤，存在性/覆盖/
    指纹门禁在 build_chain_graph 绑定真实 claim/hypothesis 后执行。"""
    if not seeds_path:
        return []
    p = Path(seeds_path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    out: list[dict] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        scoped = entry.get("event_id")
        if scoped and scoped != event_id:
            continue
        defs = entry.get("reasoning_bridges")
        if not isinstance(defs, list):
            continue
        for item in defs:
            if isinstance(item, dict):
                out.append(dict(item))
    return out


def _dict_phrase(title: str, content: str, platform: str) -> str:
    """词典短语(材料概括的确定性兜底;卷面改造 §五失败兜底列):清洗标题+
    出处,不留空、**不伪装 LLM**(调用点只在旁车缺项/漂移分支)。四阶段
    §四:去硬裁(标题/首句本身短,裁断保险变裁断事实——全文呈现)。"""
    t = (title or "").strip()
    if not t:
        seg = re.split(r"[。;;\n]", (content or "").strip())
        t = next((x.strip() for x in seg if x.strip()), "")
    src = (platform or "").strip()
    if not t:
        return f"材料({src})" if src else "材料"
    return f"{t}({src})" if src else t


def history_line(created_at: str, transitions: list[dict]) -> str:
    """命题卡状态史行(机械模板;时间取正本 ts/created_at,禁 now)。

    形态:「立项 10-30 → 查实 11-08 → 翻转回落 11-12」(月-日短日期,卡内
    不折行);transitions 超 MAX_HISTORY_NODES 截断并注记总次数(复查链多轮
    结算防线)。无记录=空串(渲染侧不画该行,不伪装)。"""
    nodes: list[str] = []
    if created_at:
        nodes.append(f"立项 {created_at[5:10]}")
    for t in transitions:
        verb = _HISTORY_VERB.get((t["new_status"], t["cause"]))
        if not verb:
            continue
        nodes.append(f"{verb} {str(t['ts'])[5:10]}")
    if len(nodes) > MAX_HISTORY_NODES:
        total = len(nodes) - 1
        head = nodes[:MAX_HISTORY_NODES]
        return " → ".join(head) + f"(共 {total} 次状态变化)"
    return " → ".join(nodes)


def evidence_morph(edges: list[dict], drawn_ev: dict) -> dict | None:
    """证据形态(呈现完整化 B-1;纯函数零 LLM):该命题名下已裁定在案的
    形态——独立根数(支持裁定×lineage_root 去重,判据=判决梯已承认的量)
    +反证件数+同源反证(矛盾裁定根 ⊆ 支持根=同一来源的相反记载)。
    每独立根取 evidence_id 最小件的来源描述(平台;空=谱系根定位符)。
    无任何支持/反证=返回 None(调用方零痕迹省行,不伪装)。"""
    es = sorted(edges or [], key=lambda x: x["src"])
    sup_roots: list[str] = []
    contra_roots: set[str] = set()
    contra_items = 0
    for e in es:
        if e["ruling"] == "支持":
            if e["root"] not in sup_roots:
                sup_roots.append(e["root"])
        elif e["ruling"] == "矛盾":
            contra_roots.add(e["root"])
            contra_items += 1
    if not sup_roots and not contra_items:
        return None
    srcs = []
    for r in sup_roots:
        eid = next((e["src"] for e in es
                    if e["ruling"] == "支持" and e["root"] == r), "")
        srcs.append((drawn_ev.get(eid, {}).get("platform") or r) if eid else r)
    return {"n_support_roots": len(sup_roots), "support_sources": srcs,
            "n_contra_items": contra_items,
            "same_source_contra": bool(contra_items)
            and contra_roots <= set(sup_roots)}


def _opinion_quotes(led, event_id: str) -> dict[tuple[str, str], dict]:
    """裁定理由读口(呈现完整化件 5,2026-11-09 拍板带上):decision_log
    「意见」payload judgments 的 quote/reason(件 5 起 s6 归档不再丢弃;
    旧台账 payload 无 quote 键=自然缺席,清单回落只列方向,两态各自如实)。
    同一 (claim, evidence) 多轮意见=后写覆盖(与 claim_evidence upsert
    最新裁定同口径)。零写台账。"""
    out: dict[tuple[str, str], dict] = {}
    try:
        rows = led.decision_log_rows(event_id)
    except sqlite3.OperationalError:
        return out
    for r in rows:
        if r["kind"] != "意见":
            continue
        try:
            p = json.loads(r["payload_json"])
        except ValueError:
            continue
        if not isinstance(p, dict):
            continue
        cid = p.get("claim_id") or ""
        for j in p.get("judgments") or []:
            if not isinstance(j, dict):
                continue
            q = (j.get("quote") or "").strip()
            if not q:
                continue
            out[(cid, j.get("evidence_id") or "")] = {
                "quote": q, "reason": (j.get("reason") or "").strip()}
    return out


# ---------------------------------------------------------------- 装配

_CE_SQL = (
    "SELECT ce.claim_id, ce.ruling, ce.round AS ce_round,"
    " e.evidence_id, e.title, e.content, e.lineage_root,"
    " e.platform, e.source, e.sensitive, e.author_type"
    " FROM claim_evidence ce JOIN evidence e ON e.evidence_id=ce.evidence_id"
    " WHERE e.event_id=?"
    " ORDER BY e.evidence_id, ce.claim_id"
)


def _clip(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[:n] + "…"


def _full(text: str) -> str:
    """全文呈现标签(四阶段 §四 裁断退役):换行归一空格(断行交渲染层
    _wrap),其余逐字保留(省略非压缩纪律)。机器对账 text 字段不动。"""
    return (text or "").replace("\n", " ").strip()


def _load_sidecar(kb, event_id: str, kind: str) -> list[dict]:
    return sidecar_rows(kb, event_id, kind)   # P7 双名兼容读口(一阶段名保留)


def _flip_note(status: str, s_eff: int) -> str | None:
    """翻转条件边标注(机械可导,D 线呈现侧前身):此根失效→该 claim 落哪态。"""
    if status != "已证实":
        return None
    if s_eff >= 3:
        return None                    # 余根仍足,单根失效不改态
    if s_eff == 2:
        return "此根失效→回待证实(仅剩 1 根)"
    return "唯一根失效→回待证实"


def build_chain_graph(mains, kb, event_id: str, *, ess_rules=None,
                      auth_roots=None, near_dup_dist=None, seeds_path=None,
                      config_dir: str | Path = "data/config",
                      as_of_round: int | None = None) -> dict:
    """graph JSON 装配(第一公民;查询时现算纯函数,零 LLM 零网络)。

    nodes.hypotheses/claims/evidence + edges.supports/derives/co_roots/
    conveys + clusters(同 lineage_root 聚类框)+ meta。消费旁车:
    polarity(seeds 人工 > 旁车指纹有效)> 无(边不带极性属性);
    channel(旁车指纹有效∧白名单内)> 无(缺口节点不带通道)。
    """
    led = mains.get(event_id)
    if led is None:
        raise ChainGraphError(f"主库未提供: {event_id}")
    auth = set(auth_roots or ())
    wl = channel_whitelist(config_dir)
    rules = load_present_rules(config_dir)   # 三阶段 §七.2(缺文件=空=治理全关)

    claim_rows = [dict(r) for r in led.all_claims(event_id)]
    if as_of_round is not None:
        claim_rows = [r for r in claim_rows if r["created_round"] <= as_of_round]
    claim_ids = {r["claim_id"] for r in claim_rows}
    factors = {f["claim_id"]: f
               for f in collect_factors(led, event_id, as_of_round,
                                        near_dup_dist=near_dup_dist)}

    ce_rows = [dict(r) for r in led.conn.execute(_CE_SQL, (event_id,))]
    if as_of_round is not None:
        ce_rows = [r for r in ce_rows if r["ce_round"] <= as_of_round]
    ce_rows = [r for r in ce_rows if r["claim_id"] in claim_ids]

    # 假设与成员(传导边原料;op 只呈现不计逻辑组合,照 kb_chain §五-E 边界)
    hyp_rows = [dict(r) for r in led.conn.execute(
        "SELECT hypothesis_id, label, description FROM hypotheses"
        " WHERE event_id=? ORDER BY hypothesis_id", (event_id,))]
    standings = {s["hypothesis_id"]: s for s in led.hypothesis_standings(event_id)}
    hc_map: dict[str, list[str]] = {}
    for r in led.conn.execute(
            "SELECT hc.hypothesis_id, hc.claim_id FROM hypothesis_claims hc"
            " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
            " WHERE h.event_id=? ORDER BY hc.hypothesis_id, hc.claim_id",
            (event_id,)):
        if r["claim_id"] in claim_ids:
            hc_map.setdefault(r["hypothesis_id"], []).append(r["claim_id"])

    # 旁车消费(polarity/channel;指纹漂移=弃用计数,白名单外=丢弃计数)
    manual = load_manual_polarity(seeds_path)
    drifted = 0
    dropped_channels = 0
    pol_by_pair: dict[tuple[str, str], dict] = {}
    ch_by_claim: dict[str, dict] = {}
    for row in _load_sidecar(kb, event_id, "polarity"):
        crow = led.claim_row(row["claim_id"])
        h = next((x for x in hyp_rows
                  if x["hypothesis_id"] == row["hypothesis_id"]), None)
        if crow is None or h is None:
            drifted += 1
            continue
        c = dict(crow)   # 老库缺 gist 列=dict.get 兜空串(照 fallback 纪律)
        cur = factors.get(row["claim_id"], {})
        want = polarity_fp(c["text"], cur.get("status", c["status"]),
                           c.get("gist") or "", h["label"])
        if row["fingerprint"] != want:
            drifted += 1
            continue
        pol_by_pair[(row["claim_id"], row["hypothesis_id"])] = row
    cur_gap: dict[str, str] = {}
    for r in claim_rows:
        f = factors.get(r["claim_id"])
        s = len(f["support_roots"]) if f else 0
        a_hit = bool(set(f["support_roots"]) & auth) if f else False
        tried = f["tried"] if f else bool(r["unreachable"])
        g = gap_type_of(f["status"] if f else r["status"], s, a_hit, tried)
        if g:
            cur_gap[r["claim_id"]] = g
    for row in _load_sidecar(kb, event_id, "channel"):
        c = led.claim_row(row["claim_id"])
        if c is None or row["claim_id"] not in cur_gap:
            drifted += 1
            continue
        want = channel_fp(c["text"], factors.get(row["claim_id"], {}).get(
            "status", c["status"]), cur_gap[row["claim_id"]])
        if row["fingerprint"] != want:
            drifted += 1
            continue
        if row["value"] not in wl:
            dropped_channels += 1      # 白名单外:丢弃不伪装(机器闸兜幻觉)
            continue
        ch_by_claim[row["claim_id"]] = row

    # ---- v3 分流原料(§三.2):legacy 镜像行反查→解读型 claim 集(三支
    #      归类);指纹锚定原文本,漂移=镜像失效不分流(计数,失败不伪装)。
    #      upstream 行(claim_id 空)非 claim 镜像,不在此路由。
    routed: dict[str, str] = {}          # claim_id → branch(bridge|clue|meta)
    n_insight_drifted = 0
    for kind, branch in (("signal_interpretation", "bridge"),
                         ("gap_hint", "clue"),
                         ("signal_meta", "meta"), ("detector_field", "meta")):
        for row in sidecar_rows(kb, event_id, kind):
            cid = row.get("legacy_claim_id") or ""
            if not cid:
                continue
            crow = led.claim_row(cid)
            if crow is None or legacy_fp(crow["text"]) != row.get("fingerprint"):
                n_insight_drifted += 1
                continue
            routed[cid] = branch
    # ---- 节点:claims(含缺口)/evidence(仅裁定在案的进图;无关票=查证
    #      尝试计数,挂账未裁=另一种留痕——语义分开注记,不冒充「已查」)
    ev_tries: dict[str, int] = {}     # 无关票(查过没有的「查过」)
    ev_unadj: dict[str, int] = {}     # 挂账未裁(宽进关联但 S6 未裁定)
    n_seed_hidden = 0                 # 信封种子裁定边(§4-4 不入材料层,计数注记)
    drawn_ev: dict[str, dict] = {}
    content_by_eid: dict[str, str] = {}   # 全文缓存(指纹/兜底用;graph JSON 不带,防膨胀)
    sup_edges: list[dict] = []
    for r in ce_rows:
        cid, ruling, eid = r["claim_id"], r["ruling"], r["evidence_id"]
        if ruling not in DRAW_RULINGS:
            bucket = ev_tries if ruling == "无关" else ev_unadj
            bucket[cid] = bucket.get(cid, 0) + 1
            continue
        if (r["platform"] or "") == SEED_PLATFORM:
            # 卷面改造 §4-4:信封背景字段/检测器字段陈述无事实含量——材料卡、
            # 支撑线都不画(病灶#2),信息归并缺口注记;该 claim 若仅此类
            # 「支持」即无证明关系,自然落「无边不画」(§4-2)去缺口图。
            n_seed_hidden += 1
            continue
        f = factors.get(cid)
        sup_roots = f["support_roots"] if f else []
        root = r["lineage_root"] or "(无谱系)"
        root_no = (sup_roots.index(root) + 1
                   if ruling == "支持" and root in sup_roots else None)
        content_by_eid.setdefault(eid, r["content"] or "")
        if eid not in drawn_ev:
            drawn_ev[eid] = {
                "evidence_id": eid, "num": eid.rsplit(":", 1)[-1],
                "title": r["title"] or "",
                "label": (f"[{eid.rsplit(':', 1)[-1]}] "
                          + (_clip(r["title"], CLIP_EV)
                             or _clip(r["content"], CLIP_EV))),
                "root": root, "cluster": root,
                "platform": r["platform"] or r["source"] or "",
                "sensitive": bool(r["sensitive"]),
                "authoritative": root in auth,
                "party": (r["author_type"] or "") == "当事方",
                "summary": "",          # 旁车指纹有效 > 词典短语(下消费段填充)
            }
        if ruling == "矛盾":
            flip = None
        else:
            flip = _flip_note(f["status"] if f else "待证实", len(sup_roots))
        sup_edges.append({"src": eid, "dst": cid, "ruling": ruling,
                          "root": root, "root_no": root_no,
                          "authoritative": root in auth, "flip": flip,
                          "relation": relation_of(ruling, root, sup_roots,
                                                  auth)})
    sup_edges.sort(key=lambda e: (e["dst"], e["src"], e["ruling"]))

    # ---- v2 材料概括消费(§4-4 材料卡;旁车指纹有效 > 词典短语兜底,
    #      不留空、不伪装 LLM;sensitive 件概括不出境=只出定位符,照
    #      kb_check「显式值不出境」纪律)
    n_summary_drifted = 0
    for row in _load_sidecar(kb, event_id, "ev_summary"):
        e = drawn_ev.get(row["claim_id"])   # 旁车 claim_id 列复用存 evidence_id
        if e is None or row["fingerprint"] != ev_summary_fp(
                e["title"], content_by_eid.get(row["claim_id"], "")):
            n_summary_drifted += 1
            continue
        e["summary"] = (row["value"] or "").strip()
    for eid, e in drawn_ev.items():
        if not e["summary"]:
            e["summary"] = _dict_phrase(e["title"],
                                        content_by_eid.get(eid, ""), e["platform"])
        if e["sensitive"]:
            e["summary"] = "〔internal〕证据,内容不出境"

    claims_out = []
    status_by_id: dict[str, str] = {}   # v2 论证区资格判据用(三态现势,factors 覆盖后)
    for r in sorted(claim_rows, key=lambda x: x["claim_id"]):
        cid = r["claim_id"]
        f = factors.get(cid)
        status = f["status"] if f else r["status"]
        sup = f["support_roots"] if f else []
        a_hit = bool(set(sup) & auth)
        tried = f["tried"] if f else bool(r["unreachable"])
        g = cur_gap.get(cid)
        branch = routed.get(cid)
        anchor = None
        if ess_rules is not None:
            anchor = anchor_name(status, len(sup), a_hit,
                                 bool(r["corpus_bound"]), tried)
        # 去壳(§三.1):已证实∧命中转述壳→呈现标签剥壳直陈;待证实/矛盾
        # 保留壳(去壳=冒充事实,违三态纪律)。只动呈现标签,text 原文不动。
        # 四阶段 §四:无 gist→全文(裁断退役;事实陈述不裁,与「不抹平」
        # 纪律同源;卡自适应增高由渲染层 _wrap 承担)。
        label_src = (r.get("gist") or "").strip()
        label = label_src or _full(r["text"])
        if status == "已证实" and rules:
            src_for_strip = label_src or r["text"]
            stripped = strip_shell(src_for_strip, rules)
            if stripped and stripped != src_for_strip:
                label = _full(stripped)
        ch = ch_by_claim.get(cid, {})
        claims_out.append({
            "claim_id": cid, "draft_id": r["draft_id"],
            "label": label,
            "gist": r.get("gist") or "", "text": r["text"],
            "status": status, "priority": r["priority"],
            "anchor_name": anchor,
            "corpus_bound": bool(r["corpus_bound"]),
            "interpretive": branch,          # v3 分流(§三.2);None=事实命题
            "reborn_from": r.get("reborn_from") or "",   # 转生谱系(§六.3)
            "gap": (None if branch else {    # 分流条不进缺口图(§三.2)
                "type": g, "label": GAP_LABELS[g],
                "tries": ev_tries.get(cid, 0)
                + (1 if tried and r["unreachable"] else 0),
                "unadjudicated": ev_unadj.get(cid, 0),
                "channel": ch.get("value", ""),
                "channel_rationale": ch.get("rationale", ""),
                # 呈现完整化 B-1(加法式):补证通道行「尚差…·通道…」素材——
                # next=缺口型人话;next_channel=通道建议(与 channel 同值,
                # 渲染层模板消费键,照施工单命名)
                "next": (_GAP_NEXT.get(g, "") if g else ""),
                "next_channel": ch.get("value", "")}
                if g else None),
        })
        status_by_id[cid] = status

    # ---- 边:derives(parent_claim_id;前提→依赖者)/co_roots(冗余拓扑)
    derives, derives_skipped = [], 0
    for r in sorted(claim_rows, key=lambda x: x["claim_id"]):
        p = r["parent_claim_id"]
        if not p:
            continue
        if p in claim_ids:
            derives.append({"src": p, "dst": r["claim_id"]})
        else:
            derives_skipped += 1      # 父条不在装配范围(as_of 截断等),不硬画
    derive_pairs = {(d["src"], d["dst"]) for d in derives}
    # ---- v2 无边不画(§4-2):零证明关系(无载明/反证/前提边)的命题不进
    #      主图;去向=缺口图(本职)+总览计数。
    edge_claims = ({e["dst"] for e in sup_edges}
                   | {d["src"] for d in derives} | {d["dst"] for d in derives})
    for c in claims_out:
        # v3 分流(§三.2):解读型不进主图命题层(有边也不画——三支去向=
        # 线索卡/解读桥/挡除计数;三态正本仍在主库,P9 一字不动)
        c["in_main"] = c["claim_id"] in edge_claims and not c["interpretive"]
    # ---- v2 状态史行(命题卡三层之三;时序链精华吸收位,模板=history_line)
    hist_by: dict[str, list[dict]] = {}
    for r in led.conn.execute(
            "SELECT claim_id, new_status, cause, ts FROM claim_transitions"
            " WHERE claim_id LIKE ? ORDER BY ts, id", (event_id + ":%",)):
        if r["claim_id"] in claim_ids:
            hist_by.setdefault(r["claim_id"], []).append(
                {"new_status": r["new_status"], "cause": r["cause"], "ts": r["ts"]})
    created_by = {r["claim_id"]: (r["created_at"] or "") for r in claim_rows}
    for c in claims_out:
        c["history"] = history_line(created_by.get(c["claim_id"], ""),
                                    hist_by.get(c["claim_id"], []))
    sup_by_claim: dict[str, set[str]] = {}
    for e in sup_edges:
        if e["ruling"] == "支持":
            sup_by_claim.setdefault(e["dst"], set()).add(e["root"])
    co_roots: list[dict] = []
    cids_sorted = sorted(sup_by_claim)
    for i, a in enumerate(cids_sorted):
        for b in cids_sorted[i + 1:]:
            shared = sorted(sup_by_claim[a] & sup_by_claim[b])
            if shared and (a, b) not in derive_pairs:
                co_roots.append({"a": a, "b": b, "roots": shared})
    co_roots.sort(key=lambda x: (x["a"], x["b"]))
    co_root_omitted = len(co_roots) > MAX_CO_ROOT_EDGES
    if co_root_omitted:
        co_roots = co_roots[:MAX_CO_ROOT_EDGES]

    # ---- 边:conveys(claim→hypothesis;polarity 三档:人工>旁车>无)
    conveys = []
    for h in hyp_rows:
        if h["hypothesis_id"] not in hc_map:
            continue
        st = standings.get(h["hypothesis_id"], {})
        for cid in sorted(hc_map[h["hypothesis_id"]]):
            row = next(x for x in claim_rows if x["claim_id"] == cid)
            m = manual.get(h["label"], {}).get(row["draft_id"])
            pol, rationale, is_manual = None, "", False
            if m:
                pol, is_manual = m, True
            else:
                side = pol_by_pair.get((cid, h["hypothesis_id"]))
                if side and side["value"] in ("正向", "反向"):
                    pol, rationale = side["value"], side["rationale"] or ""
            conveys.append({"src": cid, "dst": h["hypothesis_id"],
                            "polarity": pol, "rationale": rationale,
                            "manual": is_manual})
    conveys.sort(key=lambda e: (e["dst"], e["src"]))

    hyps_out = []
    for h in hyp_rows:
        st = standings.get(h["hypothesis_id"], {})
        hyps_out.append({
            "hypothesis_id": h["hypothesis_id"], "label": h["label"],
            "description": h["description"] or "",
            "score": st.get("score", 0),
            "dominant": bool(st.get("dominant")),
            "adjudicated": bool(st.get("adjudicated")),
        })

    clusters = []
    for root in sorted({e["cluster"] for e in drawn_ev.values()}):
        members = sorted(eid for eid, e in drawn_ev.items()
                         if e["cluster"] == root)
        clusters.append({"root": root, "label": root, "authoritative": root in auth,
                         "evidence_ids": members})

    # ================= v2 主图装配(卷面改造 §三/§四;加法式演进,v1 键零改动)

    # 机械事实组聚类键(§3.1):claim 主语实体(角色 头>普通>尾 再 entity_id
    # 升序取一);无实体归一=「(未归一)」桶。纯确定性零 LLM。
    ent_by_claim: dict[str, list[tuple[int, str]]] = {}
    for r in led.conn.execute(
            "SELECT ce.claim_id, ce.role, ent.canonical_name AS cname"
            " FROM claim_entities ce JOIN entities ent ON ent.entity_id=ce.entity_id"
            " WHERE ce.claim_id LIKE ? ORDER BY ce.claim_id, ent.entity_id",
            (event_id + ":%",)):
        if r["claim_id"] in claim_ids and (r["cname"] or "").strip():
            ent_by_claim.setdefault(r["claim_id"], []).append(
                (_ROLE_RANK.get(r["role"], 1), r["cname"].strip()))

    # ---- 论证区(§4-1 机械判据,代码判;每卷至多 1 个):资格=有已证实成员
    #      (候审不算——章是挣来的);排序=已证实成员数降序→记分降序→id 升序;
    #      落选者进总览一行,无够格=无论证区不冒充(不降级砍图)。
    manual_g_all = load_manual_grouping(seeds_path, event_id)
    argument = None
    arg_member_set: set[str] = set()
    # 资格收紧(P4):解读型不计已证实数(两侧同源——chain_gen 传同一集合)
    top, runners_up = pick_argument(hyps_out, hc_map, status_by_id,
                                    exclude_ids=set(routed))
    grouping_side_drift = 0
    if top:
        hid = top["hypothesis_id"]
        members = top["member_ids"]
        arg_member_set = set(members)
        n_ver = top["n_verified"]
        d2c = {r["draft_id"]: r["claim_id"] for r in claim_rows}
        groups: list[dict] = []
        gsrc = "fallback"
        mg = manual_g_all.get(top["label"])
        if mg:   # 人工覆写优先(§五-④):draft_id 全可解析+恰好覆盖才收
            parsed = [{"name": g["name"],
                       "members": [d2c[m] for m in g["members"] if m in d2c]}
                      for g in mg]
            if (all(len(p["members"]) == len(g["members"])
                    for p, g in zip(parsed, mg))
                    and validate_grouping(parsed, members)):
                groups, gsrc = parsed, "manual"
        if not groups:   # 旁车:指纹有效+消费侧同校验(照白名单两侧同判据)
            for row in _load_sidecar(kb, event_id, "grouping"):
                cand = None
                if row["fingerprint"] == grouping_fp(top["label"], members):
                    try:
                        cand = json.loads(row["value"])
                    except ValueError:
                        cand = None
                    if isinstance(cand, list) and validate_grouping(cand, members):
                        if not groups:   # 首个有效组生效,余行不重复计数
                            groups = [{"name": str(g.get("name", "")).strip()
                                       or "论点",
                                       "members": [str(m) for m in
                                                   g.get("members", [])]}
                                      for g in cand if isinstance(g, dict)]
                            gsrc = "sidecar"
                        continue
                grouping_side_drift += 1   # 漂移/坏值/覆盖不完整=弃用计数
        if not groups:   # 机械平铺兜底(§五失败兜底列,按主语类目聚类)
            buckets: dict[str, list[str]] = {}
            for cid in members:
                buckets.setdefault(_claim_subject(cid, ent_by_claim),
                                   []).append(cid)
            if len(buckets) == 1:
                groups = [{"name": "成员命题", "members": list(members)}]
            else:
                groups = [{"name": (f"关于{k}的事实" if k != "(未归一)"
                                    else "成员命题"),
                           "members": v} for k, v in sorted(buckets.items())]
        # 章首研判+论证区串讲(旁车指纹有效才有;失败不伪装=无该段/无串讲)
        narrative = None
        for row in _load_sidecar(kb, event_id, "narrative"):
            nv = None
            if row["fingerprint"] == narrative_fp(top["label"], members):
                try:
                    nv = json.loads(row["value"])
                except ValueError:
                    nv = None
                if (isinstance(nv, dict) and isinstance(nv.get("chapter"), str)
                        and nv["chapter"].strip()
                        and isinstance(nv.get("walkthrough"), str)):
                    if narrative is None:   # 首个有效段生效
                        narrative = {"chapter": nv["chapter"].strip(),
                                     "walkthrough": nv["walkthrough"].strip()}
                    continue
            grouping_side_drift += 1   # 漂移/坏值=弃用计数(全行扫描)
        argument = {
            "hypothesis_id": hid, "label": top["label"],
            "score": top["score"], "n_verified": n_ver,
            "n_members": len(members),
            "verdict_line": (f"全部立住:{n_ver}/{len(members)} 条已证实"
                             if n_ver == len(members)
                             else f"部分立住:{n_ver}/{len(members)} 条已证实"),
            "member_ids": members,
            "groups": groups, "grouping_source": gsrc,
            "narrative": narrative,
        }
        # ---- R 线(R1/R3,批2 §二):组极性聚合+票型——消费 conveys 现成量
        #      (polarity 三档=人工>旁车>无,指纹漂移=无极性天然不标不冒充),
        #      装配侧判定渲染只消费(批1-D 铁律);conveys JSON 全量不动。
        #      组分类判据:counter=含反向(拆台组);support=无反向∧含正向;
        #      checking=全无极性(在查组)。零新增 LLM,旁车只上呈现不回流
        #      判决(记分在 S6,此处只读现成 polarity)。
        pol_by_claim = {e["src"]: e.get("polarity")
                        for e in conveys if e["dst"] == hid}
        gp = []
        for g in groups:
            pos = sum(1 for cid in g["members"]
                      if pol_by_claim.get(cid) == "正向")
            rev = sum(1 for cid in g["members"]
                      if pol_by_claim.get(cid) == "反向")
            gp.append({"group": g["name"], "n_pos": pos, "n_rev": rev,
                       "n_none": len(g["members"]) - pos - rev})
        argument["group_polarity"] = gp
        argument["ticket"] = {
            "support": sum(1 for x in gp
                           if x["n_rev"] == 0 and x["n_pos"] > 0),
            "counter": sum(1 for x in gp if x["n_rev"] > 0),
            "checking": sum(1 for x in gp
                            if x["n_pos"] == 0 and x["n_rev"] == 0),
        }

    # ---- 机械事实组框(§3.1):非论证区的有边命题按主语对象确定性聚类
    #      (框名机械:"关于X的事实";零研判零 LLM)
    fb: dict[str, list[str]] = {}
    for c in claims_out:
        if c["in_main"] and c["claim_id"] not in arg_member_set:
            fb.setdefault(_claim_subject(c["claim_id"], ent_by_claim),
                          []).append(c["claim_id"])
    fact_groups = [{"name": (f"关于{k}的事实" if k != "(未归一)" else "其他事实"),
                    "claim_ids": v} for k, v in sorted(fb.items())]

    # ---- v3 解读桥(§四,P3:桥=纯结构零 LLM——interpretation 旁车指纹
    #      有效∧机器闸重验(conclusion 在台账∧basis 在链)∧门槛(参数表
    #      bridge_min_verified,首批 1)→画;出边目标=basis 多数归的论点组
    #      (平票取 conclusion 所在组);无论证区/无归属=不上图计数,照
    #      narrative 无论证区先例。桥不进三态/记分/资格(护栏,负测试锁)。
    bridges: list[dict] = []
    n_bridges_blocked = 0
    arg_groups = (argument or {}).get("groups") or []
    src_rows = {r["insight_id"]: r
                for r in sidecar_rows(kb, event_id, "signal_interpretation")}
    for row in sidecar_rows(kb, event_id, "interpretation"):
        try:
            v = json.loads(row["value"] or "{}")
        except ValueError:
            v = None
        if not isinstance(v, dict):
            n_bridges_blocked += 1
            continue
        basis_raw = [b for b in v.get("basis") or [] if isinstance(b, str)]
        basis = [b for b in basis_raw if b in claim_ids]
        src = src_rows.get(v.get("source") or "")
        if (src is None or not basis or row["claim_id"] not in claim_ids
                or row["fingerprint"] != interp_fp(src.get("value") or "",
                                                   basis_raw)):
            n_bridges_blocked += 1    # 指纹漂移/幻觉锚(消费侧同机器闸)
            continue
        n_ver = sum(1 for b in basis if status_by_id.get(b) == "已证实")
        if n_ver < int(rules.get("bridge_min_verified", 1)):
            n_bridges_blocked += 1    # 门槛未过(改表收紧即生效,P3)
            continue

        def _hit(g: dict) -> int:
            return sum(1 for b in basis if b in g["members"])

        target = None
        if arg_groups:
            best = max(_hit(g) for g in arg_groups)
            if best > 0:
                cands = [g for g in arg_groups if _hit(g) == best]
                target = next((g["name"] for g in cands
                               if row["claim_id"] in g["members"]),
                              cands[0]["name"])
            else:
                target = next((g["name"] for g in arg_groups
                               if row["claim_id"] in g["members"]), None)
        if target is None:
            n_bridges_blocked += 1    # 无论证区/无归属组:不上图,计数交代
            continue
        bridges.append({"insight_id": row["insight_id"],
                        "text": _full(src.get("value") or ""),   # 全文(§四)
                        "conclusion": row["claim_id"],
                        "basis": basis, "n_basis": len(basis),
                        "n_verified": n_ver, "target_group": target,
                        "rationale": (v.get("rationale") or "").strip()})
    bridges.sort(key=lambda b: b["insight_id"])

    # ---- 结构化推理桥(语义分层施工单 P3):人工配置只是可审计
    #      投影素材，不写 claim/三态/假设裁定。渲染资格=前提和目标
    #      全存在∧至少两前提(或一前提+明确规则)∧字段完整。
    #      失败整桥拒绝且计数，绝不凭空补结论。
    inferences: list[dict] = []
    n_inferences_blocked = 0
    draft_to_cid = {r["draft_id"]: r["claim_id"] for r in claim_rows}
    claim_row_by_id = {r["claim_id"]: r for r in claim_rows}
    hyp_by_label = {h["label"]: h["hypothesis_id"] for h in hyp_rows}
    for raw in load_manual_inferences(seeds_path, event_id):
        iid = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        rule = str(raw.get("rule") or "").strip()
        inference_text = str(raw.get("inference_text") or "").strip()
        condition = str(raw.get("condition_or_gap") or "").strip()
        basis_drafts = raw.get("basis") if isinstance(raw.get("basis"), list) else []
        context_drafts = raw.get("context") if isinstance(raw.get("context"), list) else []
        routed_drafts = (raw.get("routed_claims")
                         if isinstance(raw.get("routed_claims"), list) else [])
        targets_raw = raw.get("targets") if isinstance(raw.get("targets"), list) else []
        basis = [draft_to_cid.get(str(x), "") for x in basis_drafts]
        context = [draft_to_cid.get(str(x), "") for x in context_drafts]
        routed_claim_ids = [draft_to_cid.get(str(x), "") for x in routed_drafts]
        targets: list[dict] = []
        targets_ok = True
        for target in targets_raw:
            if not isinstance(target, dict):
                targets_ok = False
                break
            hid = hyp_by_label.get(str(target.get("label") or ""), "")
            if not hid:
                targets_ok = False
                break
            targets.append({
                "hypothesis_id": hid,
                "polarity": (str(target.get("polarity") or "").strip()
                             or None),
                "rationale": str(target.get("rationale") or "").strip(),
            })
        all_refs_ok = (all(basis) and all(context) and all(routed_claim_ids))
        enough_premises = len(basis) >= 2 or (len(basis) == 1 and bool(rule))
        if (not iid or not label or not inference_text or not condition
                or not all_refs_ok or not enough_premises or not targets
                or not targets_ok or len(set(basis)) != len(basis)):
            n_inferences_blocked += 1
            continue
        basis_rows = []
        for cid in basis:
            r = dict(claim_row_by_id[cid])
            r["status"] = status_by_id.get(cid, r["status"])
            basis_rows.append(r)
        status_summary = {s: sum(1 for cid in basis
                                 if status_by_id.get(cid) == s)
                          for s in ("已证实", "待证实", "矛盾")}
        target_ids = [t["hypothesis_id"] for t in targets]
        inferences.append({
            "inference_id": iid,
            "label": label,
            "inference_text": inference_text,
            "basis_claim_ids": basis,
            "rule": rule,
            "target_hypothesis_ids": target_ids,
            "targets": targets,
            "premise_status_summary": status_summary,
            "condition_or_gap": condition,
            "context_claim_ids": context,
            "routed_claim_ids": routed_claim_ids,
            "fingerprint": inference_fp(label, basis_rows, rule,
                                         inference_text, condition, target_ids),
            "sidecar_provenance": "manual_config",
            "fingerprint_verified": True,
        })
    inferences.sort(key=lambda x: x["inference_id"])

    # ---- v3 行式结构(§四 P1:每行≈一条推理线——证据─关系词→命题→论点
    #      →结论;独立事实止于命题层 terminal=True。确定性按论证区组→
    #      事实组序排;渲染器与 md-chain 均消费此序)
    claims_by = {c["claim_id"]: c for c in claims_out}
    group_of: dict[str, str] = {}
    ordered: list[str] = []
    if argument:
        for g in argument["groups"]:
            for cid in g["members"]:
                group_of[cid] = g["name"]
                if cid not in ordered:
                    ordered.append(cid)
    sup_by_claim_v3: dict[str, list[dict]] = {}
    for e in sup_edges:
        sup_by_claim_v3.setdefault(e["dst"], []).append(
            {"evidence_id": e["src"],
             "num": drawn_ev.get(e["src"], {}).get("num", ""),
             "relation": e["relation"],
             "ruling": e["ruling"]})   # B-4:裁定方向标素材(加法式)
    for fg in fact_groups:
        for cid in fg["claim_ids"]:
            if cid not in ordered:
                ordered.append(cid)
    fact_of: dict[str, str] = {}
    for fg in fact_groups:
        for cid in fg["claim_ids"]:
            fact_of[cid] = fg["name"]
    rows = []
    for cid in ordered:
        c = claims_by.get(cid)
        if c is None or not c["in_main"]:
            continue
        rows.append({"claim_id": cid, "label": c["label"],
                     "status": c["status"],
                     "group": group_of.get(cid) or fact_of.get(cid),
                     "evidence": sorted(sup_by_claim_v3.get(cid, []),
                                        key=lambda x: x["evidence_id"]),
                     "terminal": cid not in group_of})

    # ---- v4 同文谱系归并(四阶段 §五,批1-C;呈现层投影,机器对账全量
    #      不动):家族=谱系闭包∧两两近文双条件(lineage_families);代表=
    #      纯最新代(拍板①:卡面状态=时间最新代真实状态,不让旧代已证实
    #      掩盖新代待证实,与时钟复查链「最新结算状态为准」语义一致);代表
    #      卡史行=家族全轨迹(已证实历史不丢);论证区资格/记分按台账全量
    #      算(上方已定)=归并不影响资格;JSON claims/rows 全量保留,家族
    #      信息经 claims[].family_rep+总览注记交代(不冒充删证,照种子挡除
    #      注记先例)。家族内部前提边随非代表行不进渲染卡自然消失(§五-3),
    #      跨家族真实 derive 照旧。
    fams = lineage_families(claim_rows)
    family_rep: dict[str, str] = {}       # 非代表→代表 id;代表/无族=""
    fam_members: dict[str, list[str]] = {}
    for mem in fams:
        rep = max(mem, key=lambda cid: (created_by.get(cid, ""), cid))
        fam_members[rep] = mem
        for cid in mem:
            if cid != rep:
                family_rep[cid] = rep
    for c in claims_out:
        c["family_rep"] = family_rep.get(c["claim_id"], "")
    for rep, mem in fam_members.items():
        rc = next(x for x in claims_out if x["claim_id"] == rep)
        rc["history"] = family_history_line(mem, created_by, hist_by)

    # ---- 呈现完整化(2026-11-09 施工单,B 线:缺口图退役+待查证小区+裁定
    #      逐条清单;数据层判定,渲染只消费):①矛盾卡翻回条件注记;②虚框卡
    #      证据形态(独立根数+同源反证,纯函数 evidence_morph);③待查证小区
    #      资格=无边∧非解读分流∧非家族非代表∧『查证排除/未触及』两型∧非
    #      挡除/解读前缀(挡除类继续全隐藏,11-08 会话边界);④裁定逐条清单
    #      全量挂账对(含未裁/无关,件 5 起带意见 quote/reason)。全部加法式。
    sup_edges_by_claim: dict[str, list[dict]] = {}
    for e in sup_edges:
        sup_edges_by_claim.setdefault(e["dst"], []).append(e)
    quotes = _opinion_quotes(led, event_id)
    unruled_eids: set[str] = set()
    for r in ce_rows:
        if not (r["ruling"] or "").strip():
            unruled_eids.add(r["evidence_id"])
    for c in claims_out:
        if c["status"] == "矛盾":
            c["flipback_note"] = FLIPBACK_NOTE
        if c["status"] == "待证实" and c["in_main"]:
            m = evidence_morph(sup_edges_by_claim.get(c["claim_id"], []),
                               drawn_ev)
            if m:
                c["evidence_morph"] = m
        c["in_zone"] = (c["status"] == "待证实" and not c["in_main"]
                        and not c["interpretive"] and not c["family_rep"]
                        and (c["gap"] or {}).get("type") in ZONE_GAP_TYPES
                        and classify_claim(c["text"], rules) is None)
    claims_by_id = {c["claim_id"]: c for c in claims_out}
    with_items = sorted({r["claim_id"] for r in ce_rows})
    row_ids = {r["claim_id"] for r in rows}
    ruling_order = ([cid for cid in (r["claim_id"] for r in rows)
                     if cid in set(with_items)]
                    + [cid for cid in with_items
                       if cid not in row_ids])   # 按主图命题序,余者 id 序
    rulings: list[dict] = []
    for cid in ruling_order:
        cnode = claims_by_id.get(cid)
        if cnode is None:
            continue   # as_of 截断防御(ce_rows 已过滤,正常不可达)
        items: list[dict] = []
        sup_roots: list[str] = []
        unruled: list[str] = []
        for r in sorted((x for x in ce_rows if x["claim_id"] == cid),
                        key=lambda x: x["evidence_id"]):
            eid = r["evidence_id"]
            num = eid.rsplit(":", 1)[-1]
            if not (r["ruling"] or "").strip():
                unruled.append(num)   # 未裁不冒充方向,如实计数点名
                continue
            it = {"evidence_id": eid, "num": num,
                  "title": r["title"] or "",
                  "src": r["platform"] or r["source"] or "",
                  "ruling": r["ruling"], "root": r["lineage_root"] or ""}
            if r["ruling"] == "支持":
                it["dup_root"] = it["root"] in sup_roots   # 同根折并标注
                if it["root"] and it["root"] not in sup_roots:
                    sup_roots.append(it["root"])
            q = quotes.get((cid, eid))
            if q and q.get("quote"):
                it["quote"] = q["quote"]   # 件 5:清单方向标后附引文
            if q and q.get("reason"):
                it["reason"] = q["reason"]
            items.append(it)
        rulings.append({"claim_id": cid, "label": cnode["label"],
                        "status": cnode["status"],
                        "n_items": len(items) + len(unruled),   # 全量挂账数
                        "n_roots": len(sup_roots), "items": items,
                        "unruled_nums": unruled})

    # ---- v4 扇出汇流(四阶段 §六,批1-D;装配算元数据/渲染投影并边):
    #      (证据, 关系词, 目标组)三元组相同的边数 ≥ fanin_merge_min
    #      (模式表新参数,首批 3;**缺键=汇流关**,表驱动无代码常量兜底照
    #      ess_rules 纪律)→渲染层并 1 条汇流边(标签「关系词 ×N」入组锚点),
    #      簇内成员不再逐条入边;关系词不同不并(每条边语义保真)。计数基于
    #      归并投影口径(dst=家族代表)与渲染形态一致;JSON supports 全量
    #      保留(汇流仅 svg/png/html 渲染行为,明细见机器对账+证据附录)。
    fanout_merges: list[dict] = []
    fanin_raw = rules.get("fanin_merge_min")
    try:
        fanin_min = int(fanin_raw) if fanin_raw is not None else 0
    except (TypeError, ValueError):
        fanin_min = 0
    if fanin_min >= 2:
        grp_proj: dict[str, str] = {}
        for cid in ordered:
            if family_rep.get(cid):
                continue   # 非代表不决定组归属(与渲染投影同口径=代表行)
            g = group_of.get(cid) or fact_of.get(cid)
            if g:
                grp_proj[cid] = g
        cnt: dict[tuple[str, str, str], list[str]] = {}
        for e in sup_edges:
            rep = family_rep.get(e["dst"]) or e["dst"]
            g = grp_proj.get(rep)
            if g:
                cnt.setdefault((e["src"], e["relation"], g), []).append(rep)
        for (ev, rel, g), dsts in sorted(cnt.items()):
            if len(dsts) >= fanin_min:
                fanout_merges.append({"evidence_id": ev, "relation": rel,
                                      "group": g, "n": len(dsts),
                                      "members": sorted(set(dsts))})

    # ---- v5 命题级多源印证卡消费(用户复查修正):fusion 旁车必须明确
    #      claim_id,且 members 恰好等于该命题全部可融合支持材料。准入口径与
    #      生成期同源:支持∧非 sensitive∧非信封种子,独立根数与材料数均过
    #      fusion_min_members;反证/时效刷新不融,红边独立保留。融合卡→命题
    #      只画一条「印证 ×N」边;被融合材料卡/逐条边仅在人看主图折叠,
    #      graph JSON supports 与证据附录全量不动(融合才真正省线)。
    fusions: list[dict] = []
    n_fusion_drifted = 0
    n_fusion_blocked = 0
    fm_raw = rules.get("fusion_min_members")
    try:
        fusion_min = int(fm_raw) if fm_raw is not None else 2
    except (TypeError, ValueError):
        fusion_min = 2
    grp_of_claim = dict(group_of)
    for k, v in fact_of.items():
        grp_of_claim.setdefault(k, v)
    fam_proj = {c["claim_id"]: (c.get("family_rep") or c["claim_id"])
                for c in claims_out}
    for row in sidecar_rows(kb, event_id, "fusion"):
        try:
            v = json.loads(row["value"] or "{}")
        except ValueError:
            v = None
        cid = row.get("claim_id") or ""
        value_claim = (v or {}).get("claim") if isinstance(v, dict) else None
        raw_m = (v or {}).get("members") if isinstance(v, dict) else None
        members = [m for m in (raw_m or []) if isinstance(m, str)]
        text = str((v or {}).get("text") or "").strip() if isinstance(v, dict) else ""
        eligible_all = sorted({e["src"] for e in sup_edges
                               if e["dst"] == cid and e["ruling"] == "支持"
                               and not drawn_ev.get(e["src"], {}).get("sensitive")})
        # 与生成期同源折算:每 root 取 evidence_id 最小代表;旁车 members
        # 必须等于代表集,避免同根转载把「N路」虚高。主图折叠全部同根件。
        root_rep: dict[str, str] = {}
        for e in sorted((x for x in sup_edges
                         if x["dst"] == cid and x["src"] in eligible_all
                         and x["ruling"] == "支持"),
                        key=lambda x: (x["root"], x["src"])):
            root_rep.setdefault(e["root"], e["src"])
        eligible = sorted(root_rep.values())
        fps = {eid: ev_summary_fp(drawn_ev[eid]["title"],
                                  content_by_eid.get(eid, ""))
               for eid in eligible if eid in drawn_ev}
        if (not isinstance(v, dict) or not text or not cid
                or value_claim != cid or sorted(members) != eligible
                or len(eligible) < fusion_min
                or len(fps) != len(eligible)
                or row["fingerprint"] != fusion_fp(eligible, fps)):
            n_fusion_drifted += 1
            continue
        target = fam_proj.get(cid, cid)
        group = grp_of_claim.get(target)
        if target not in {r["claim_id"] for r in rows} or not group:
            n_fusion_blocked += 1
            continue
        fusions.append({"claim_id": target, "source_claim_id": cid,
                        "text": text, "members": eligible,
                        "all_members": eligible_all,
                        "n": len(eligible), "n_items": len(eligible_all),
                        "anchors": [drawn_ev[m]["num"] for m in eligible],
                        "party": any(drawn_ev[m]["party"] for m in eligible_all),
                        "group": group, "relation": "印证"})
    fusions.sort(key=lambda f: (f["claim_id"], f["members"]))

    # ---- 同根件数注记(材料卡「互不为独立佐证」;十家转载=一根的可视化)
    root_count = {c["root"]: len(c["evidence_ids"]) for c in clusters}
    for e in drawn_ev.values():
        e["same_root_count"] = root_count.get(e["cluster"], 1)

    # ---- 总览原料(§三:假设资格逐行交代,渲染侧组装文本;计数在此)
    runner_ids = {r["hypothesis_id"] for r in runners_up}
    ov_hyps = []
    for h in hyps_out:
        hid = h["hypothesis_id"]
        mem = hc_map.get(hid, [])
        nv = sum(1 for cid in mem if status_by_id.get(cid) == "已证实")
        state = ("chosen" if argument and hid == argument["hypothesis_id"]
                 else "runner_up" if hid in runner_ids
                 else "unearned" if mem else "untouched")
        ov_hyps.append({"hypothesis_id": hid, "label": h["label"],
                        "state": state, "n_verified": nv,
                        "n_members": len(mem), "score": h["score"]})
    n_routed = {"bridge": 0, "clue": 0, "meta": 0}
    for branch in routed.values():
        n_routed[branch] = n_routed.get(branch, 0) + 1
    n_no_edge = sum(1 for c in claims_out
                    if not c["in_main"] and not c["interpretive"])
    # 无边已证实=仅信封种子自证查实的元陈述(「信封未提供 X 字段」类,
    # §4-4 机器字段陈述:主图不画、缺口图不收〔已证实无缺口〕,总览注记
    # 交代——不是抹平,机器对账通道全量在)
    n_no_edge_pending = sum(1 for c in claims_out
                            if not c["in_main"] and not c["interpretive"]
                            and c["status"] == "待证实")
    n_no_edge_verified = n_no_edge - n_no_edge_pending

    erow = led.conn.execute(
        "SELECT updated_at FROM events WHERE event_id=?", (event_id,)).fetchone()
    kb_version = ""
    if kb is not None:
        try:
            arow = kb.execute("SELECT kb_version FROM kb_anchor WHERE event_id=?",
                              (event_id,)).fetchone()
            kb_version = arow["kb_version"] if arow else ""
        except sqlite3.OperationalError:
            kb_version = ""   # 纯旁车库形态(无 kb 表)=无版本注记,如实空
    return {
        "meta": {
            "event_id": event_id,
            "kb_version": kb_version,
            "as_of": erow["updated_at"] if erow else "",
            "as_of_round": as_of_round,
            "n_claims": len(claims_out),
            "n_pending": sum(1 for c in claims_out if c["status"] == "待证实"),
            "n_evidence_drawn": len(drawn_ev),
            "n_tried_hidden": sum(ev_tries.values()),
            "n_unadjudicated": sum(ev_unadj.values()),
            "n_seed_hidden": n_seed_hidden,
            "n_no_edge_claims": n_no_edge,
            "n_summary_drifted": n_summary_drifted,
            "n_gen_drifted": grouping_side_drift,
            "n_co_root_edges": len(co_roots),
            "co_root_omitted": co_root_omitted,
            "sidecar_drifted": drifted,
            "channels_dropped": dropped_channels,
            "derives_skipped": derives_skipped,
            "n_routed": n_routed,
            "n_bridges_drawn": len(bridges),
            "n_bridges_blocked": n_bridges_blocked,
            "n_inferences_drawn": len(inferences),
            "n_inferences_blocked": n_inferences_blocked,
            "n_insight_drifted": n_insight_drifted,
            "n_lineage_families": len(fams),
            "n_lineage_merged": sum(len(m) - 1 for m in fams),
            "n_fanout_merged_edges": sum(m["n"] - 1 for m in fanout_merges),
            "n_fusion_cards": len(fusions),
            "n_fusion_drifted": n_fusion_drifted,
            "n_fusion_blocked": n_fusion_blocked,
            # 呈现完整化(B 线;加法式):小区卡数+未裁证据数(证据级去重)
            "n_zone": sum(1 for c in claims_out if c.get("in_zone")),
            "n_unruled_evidence": len(unruled_eids),
        },
        "nodes": {"hypotheses": hyps_out, "claims": claims_out,
                  "evidence": sorted(drawn_ev.values(),
                                     key=lambda e: e["evidence_id"])},
        "edges": {"supports": sup_edges, "derives": derives,
                  "co_roots": co_roots, "conveys": conveys},
        "clusters": clusters,
        # v2 顶键(卷面改造 §三;消费缺省兼容——老调用按 v1 键取零影响)
        "argument": argument,
        "fact_groups": fact_groups,
        # v3 顶键(三阶段 §四/§五;加法式——解读桥+行式结构)
        "bridges": bridges,
        # 语义分层 P3:可审计结构化推理桥(配置投影,不改判决)
        "inferences": inferences,
        "rows": rows,
        # v4 顶键(四阶段 §五/§六;加法式——谱系归并+扇出汇流元数据)
        "fanout_merges": fanout_merges,
        # v5 顶键(批2 F2;加法式——融合卡元数据,渲染纯消费)
        "fusions": fusions,
        # v6 顶键(呈现完整化 B-4;加法式——裁定逐条清单,渲染同源消费)
        "rulings": rulings,
        "overview": {"hypotheses": ov_hyps, "n_no_edge_claims": n_no_edge,
                     "n_no_edge_pending": n_no_edge_pending,
                     "n_no_edge_verified": n_no_edge_verified,
                     "n_seed_hidden": n_seed_hidden,
                     "n_routed": n_routed,
                     "n_bridges_drawn": len(bridges),
                     "n_bridges_blocked": n_bridges_blocked,
                     "n_lineage_families": len(fams),
                     "n_lineage_merged": sum(len(m) - 1 for m in fams),
                     "n_zone": sum(1 for c in claims_out if c.get("in_zone")),
                     "n_unruled_evidence": len(unruled_eids)},
    }


# ---------------------------------------------------------------- 渲染

_CLASSDEFS = (
    "  classDef hypDom fill:#d9edf7,stroke:#31708f,stroke-width:2px\n"
    "  classDef hypCand fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 3\n"
    "  classDef clVerified fill:#dff0d8,stroke:#3c763d\n"
    "  classDef clConflict fill:#f2dede,stroke:#c9302c\n"
    "  classDef clPending fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 3\n"
    "  classDef evNode fill:#fcf8e3,stroke:#8a6d3b\n"
    "  classDef gapNode fill:#ffffff,stroke:#999,stroke-dasharray: 4 4")

_CLAIM_CLS = {"已证实": "clVerified", "矛盾": "clConflict", "待证实": "clPending"}
_POL_VERB = {"正向": "支撑", "反向": "拆台"}


def _subgraph_for(graph: dict, claim_id: str) -> dict:
    """单命题前提闭包子图(方案 §3.3):该 claim+前提祖先链(derives 递归)
    +直接依赖(子代一跳)+它们的名下证据/缺口/传导。靶不存在=显式报错。"""
    claims = {c["claim_id"]: c for c in graph["nodes"]["claims"]}
    target = claim_id
    if target not in claims:                # draft_id 便捷形态(事件内唯一解析)
        cands = [k for k in claims if k.rsplit(":", 1)[-1] == claim_id]
        if len(cands) != 1:
            raise ChainGraphError(f"claim 不在图中: {claim_id}")
        target = cands[0]
    claim_id = target                       # 以下统一用解析后的全量 id
    want = {claim_id}
    derives = [(e["src"], e["dst"]) for e in graph["edges"]["derives"]]
    changed = True
    while changed:                          # 祖先闭包
        changed = False
        for p, ch in derives:
            if ch in want and p not in want:
                want.add(p)
                changed = True
    for p, ch in derives:                   # 直接依赖(一跳)
        if p in want:
            want.add(ch)
    keep_ev = {e["src"] for e in graph["edges"]["supports"] if e["dst"] in want}
    keep_hyp = {e["dst"] for e in graph["edges"]["conveys"] if e["src"] in want}
    sub = {
        "meta": dict(graph["meta"]),   # 计数改闭包口径(注释与图一致,不冒充全卷)
        "nodes": {
            "hypotheses": [h for h in graph["nodes"]["hypotheses"]
                           if h["hypothesis_id"] in keep_hyp],
            "claims": [c for c in graph["nodes"]["claims"] if c["claim_id"] in want],
            "evidence": [e for e in graph["nodes"]["evidence"]
                         if e["evidence_id"] in keep_ev],
        },
        "edges": {
            "supports": [e for e in graph["edges"]["supports"]
                         if e["dst"] in want],
            "derives": [e for e in graph["edges"]["derives"]
                        if e["src"] in want and e["dst"] in want],
            "co_roots": [e for e in graph["edges"]["co_roots"]
                         if e["a"] in want and e["b"] in want],
            "conveys": [e for e in graph["edges"]["conveys"]
                        if e["src"] in want],
        },
        "clusters": [dict(c, evidence_ids=sorted(
            set(c["evidence_ids"]) & keep_ev))
            for c in graph["clusters"]
            if set(c["evidence_ids"]) & keep_ev],
    }
    sub["meta"]["n_claims"] = len(sub["nodes"]["claims"])
    sub["meta"]["n_pending"] = sum(1 for c in sub["nodes"]["claims"]
                                   if c["status"] == "待证实")
    sub["meta"]["n_evidence_drawn"] = len(sub["nodes"]["evidence"])
    return sub


def _render_mermaid(graph: dict) -> str:
    """graph dict → mermaid flowchart TD(确定性;_mq 引号防御;linkStyle
    按 file 序计数)。分层自传达:假设先声明(顶)、claim 次之、缺口再次、
    证据聚类框殿后(底)。"""
    hid = {h["hypothesis_id"]: f"HY{i}"
           for i, h in enumerate(graph["nodes"]["hypotheses"])}
    cid = {c["claim_id"]: f"CL{i}"
           for i, c in enumerate(graph["nodes"]["claims"])}
    vid = {e["evidence_id"]: f"EV{i}"
           for i, e in enumerate(graph["nodes"]["evidence"])}
    gid = {c["claim_id"]: f"GP{i}"
           for i, c in enumerate(graph["nodes"]["claims"]) if c["gap"]}
    root_no = {c["root"]: f"R{i + 1}"
               for i, c in enumerate(graph["clusters"])}
    lines = ["flowchart TD"]

    for h in graph["nodes"]["hypotheses"]:
        badge = f"·主导" if h["dominant"] else ""
        if h["adjudicated"]:
            badge += "·人裁"
        lines.append(f'  {hid[h["hypothesis_id"]]}'
                     f'["{_mq(h["label"])}〔得分 {h["score"]:+d}{badge}〕"]'
                     f":::{'hypDom' if h['dominant'] else 'hypCand'}")
    for c in graph["nodes"]["claims"]:
        lines.append(f'  {cid[c["claim_id"]]}["{_mq(c["label"])}"]'
                     f":::{_CLAIM_CLS.get(c['status'], 'clPending')}")
    for c in graph["nodes"]["claims"]:
        if not c["gap"]:
            continue
        g = c["gap"]
        label = f"缺口·{g['type']}:{g['label']}"
        if g["type"] == "未触及" and g["unadjudicated"]:
            label += f"(挂证 {g['unadjudicated']} 件未裁)"
        elif g["tries"]:
            label += f"(已查 {g['tries']} 次)"
        if g["channel"]:
            label += f" 通道:{g['channel']}"
        lines.append(f'  {gid[c["claim_id"]]}["{_mq(label)}"]:::gapNode')

    for i, cl in enumerate(graph["clusters"]):
        auth = "(权威)" if cl["authoritative"] else ""
        lines.append(f'  subgraph SG{i}["根 {root_no[cl["root"]]} · '
                     f'{_esc(cl["root"], 20)}{auth}"]')
        for eid in cl["evidence_ids"]:
            e = next(x for x in graph["nodes"]["evidence"]
                     if x["evidence_id"] == eid)
            label = ("〔internal〕" if e["sensitive"] else e["label"])
            lines.append(f'    {vid[eid]}["{_mq(label)}"]:::evNode')
        lines.append("  end")

    styles: list[tuple[int, str]] = []
    n_edges = 0
    for e in graph["edges"]["conveys"]:
        verb = _POL_VERB.get(e["polarity"], "触及")
        label = verb
        if e["rationale"]:
            label += f"<br/>{_clip(e['rationale'], CLIP_RATIONALE)}"
        lines.append(f'  {cid[e["src"]]} -->|"{_mq(label)}"'
                     f"| {hid[e['dst']]}")
        if e["polarity"] == "反向":
            styles.append((n_edges, "stroke:#c9302c,stroke-dasharray: 5 3"))
        n_edges += 1
    for e in graph["edges"]["supports"]:
        if e["ruling"] == "矛盾":
            label = "反证"
        elif e["ruling"] == "时效刷新":
            label = "时效刷新"
        else:
            label = "载明" + (f"·根{root_no.get(e['root'], '?')}"
                              if e["root_no"] else "")
        if e["flip"]:
            label += f"<br/>{e['flip']}"
        lines.append(f'  {vid[e["src"]]} -->|"{_mq(label)}"| {cid[e["dst"]]}')
        if e["ruling"] == "矛盾":
            styles.append((n_edges, "stroke:#c9302c,stroke-width:2px"))
        elif e["authoritative"]:
            styles.append((n_edges, "stroke-width:3px"))   # 权威根=加粗
        n_edges += 1
    for e in graph["edges"]["derives"]:
        lines.append(f'  {cid[e["src"]]} -.->|"派生"| {cid[e["dst"]]}')
        n_edges += 1
    for e in graph["edges"]["co_roots"]:
        lines.append(f'  {cid[e["a"]]} -.-|"共据"| {cid[e["b"]]}')
        styles.append((n_edges, "stroke:#999"))
        n_edges += 1
    for c in graph["nodes"]["claims"]:
        if c["gap"]:
            lines.append(f'  {cid[c["claim_id"]]} -.-> {gid[c["claim_id"]]}')
            n_edges += 1
    for idx, st in styles:
        lines.append(f"  linkStyle {idx} {st}")
    lines.append(_CLASSDEFS)
    m = graph["meta"]
    tail = (f"  %% claim {m['n_claims']}(待证实 {m['n_pending']}) / "
            f"证据 {m['n_evidence_drawn']}(无关票 {m['n_tried_hidden']}"
            f"+挂证未裁 {m['n_unadjudicated']} 件不进图)"
            + (f" / 共据边超限省略" if m["co_root_omitted"] else "")
            + (f" / 旁车漂移弃用 {m['sidecar_drifted']} 条"
               if m["sidecar_drifted"] else "")
            + (f" / 通道白名单外丢弃 {m['channels_dropped']} 条"
               if m["channels_dropped"] else ""))
    lines.append(tail)
    return "\n".join(lines)


_LEGEND = (
    "> 图例:绿实框=已证实(立住),红框=矛盾,灰虚框=待证实(没立住);"
    "黄框=证据材料,同根聚一框(十家转载=一根);虚线框=缺口(还没查清的部分,"
    "标缺什么+走哪个通道)。粗边=权威源载明;红边=反证/反向传导(拆台);"
    "「派生」虚边=前提依赖;边标「失效→回待证实」=翻转条件。")


def render_graph_full(graph: dict) -> str:
    """全卷网络图 mermaid(四层完整形态)。"""
    return _render_mermaid(graph)


def render_graph_claim(graph: dict, claim_id: str) -> str:
    """单命题前提闭包图 mermaid(证据+缺口+直接依赖)。"""
    return _render_mermaid(_subgraph_for(graph, claim_id))


def graph_section_md(graph: dict, claim_id: str | None = None) -> str:
    """md 投影(图例+全卷图[+单命题闭包图];md 只是投影之一,graph JSON
    第一公民)。离线可看 mermaid 源码(VSCode/Typora 原生渲染)。"""
    parts = ["## 证据链形态图", "", _LEGEND, "",
             fence_mermaid(render_graph_full(graph))]
    if claim_id:
        parts += ["", "### 单命题前提闭包图", "",
                  fence_mermaid(render_graph_claim(graph, claim_id))]
    notes = []
    m = graph["meta"]
    if m["co_root_omitted"]:
        notes.append(f"> 共据边超 {MAX_CO_ROOT_EDGES} 条,仅画前 "
                     f"{MAX_CO_ROOT_EDGES} 条(冗余拓扑噪音防线)。")
    if m["sidecar_drifted"]:
        notes.append(f"> 旁车漂移弃用 {m['sidecar_drifted']} 条"
                     "(命题/假设已变;重跑 chain-gen 刷新)。")
    if m["channels_dropped"]:
        notes.append(f"> 通道建议 {m['channels_dropped']} 条不在路由表白名单,"
                     "已丢弃(机器闸兜幻觉)。")
    if notes:
        parts += [""] + notes
    return "\n".join(parts) + "\n"


def payload_to_json(graph: dict) -> str:
    """graph JSON(第一公民产物;确定性序列化,与 evidence_graph 同约定)。"""
    return json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2)
