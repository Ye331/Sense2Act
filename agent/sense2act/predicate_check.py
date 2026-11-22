"""谓词充分性核对(2026-10-25 挂载;定标探针 tests/_pc_probe.py 七轮定标)。

治的病:判决梯"数的是证据根数+权威性,从不核对证据内容是否足以支撑
claim 断言的谓词"——把证据相关性当成了断言充分性。真值口径过度断言
19 条/8 案(3534968)全部走权威放行路径(18 单源例外便车+1 权威双根),
病根族="证据对题但断言超出"(目标公告证明不了"近两年有同类记录"类谓词)。

形态(方案§2.2 A/B 结合 + 探针 F1-F10 判准):LLM 温度 0 对
(claim × 权威支持根)一次调用双产出 items 谓词项对照表(role=core|detail)
+ overall 整体三态;代码验票(是-项引文逐字位于证据+非 claim 复述[判据 A
同款],败→该项回退不确定,永不 fail-open);合并=items 主判+overall 互证,
**双通道一致"不载明"才拦**;**双重标注一致性门**(--runs 默认 2:温度 0
实证非确定,边界条跨 run 翻转而 over 族全稳,各根两轮同拦才拦)。

分权:对照表=模型意见;放行/维持待证实=代码消费意见后判定。
错杀率=0 硬门:①触发正清单(病根族词样,credibility_rules.json 版本化,
ss_blocklist 同款纪律宁窄勿宽)——元数据承载型 claim(发布日期/平台/缺省
核查)不进闸;②cb=1 自证例外豁免(探针定标:不豁免 36 错杀);③不确定/
验票败/标注失败=放行(现行为,宁漏治不可错杀)。
探针读数(v7 冻结):over 19/19 折回(含判读哨兵 nantong c18 带实质折回)、
存活哨兵 yichun c13 存活、引文验票 213/215、清单外零错杀暴露;清单内残余
2 条(linlu c13/zhongshan c11)bench 白名单注记(用户拍板 2026-10-25)。

开关 --predicate-check 默认 off 入 META_KEYS(off=不装配=零调用零痕迹,
照 red_team 先例);on 引入新 LLM 交互,旧录制件回放须同开(meta 配对闸)。
"""
from __future__ import annotations

import json
import re
import unicodedata

# ---------------------------------------------------------------- prompt
# v7 冻结(探针七轮判读迭代;改前先复跑探针定标,勿凭感觉动判准)
PC_ANNO_PROMPT = """你是企业情报调查系统的谓词充分性核对员。系统拟凭若干条权威证据把一条 claim 判为"已证实"(权威来源放行路径)。请核对:这些证据文本是否**载明** claim 断言的具体谓词——即证据是否"白纸黑字说了这件事",而不只是"涉及这个主题"。证据对题不等于断言充分:一份采购公告只证明"本次有这笔采购",证明不了"近两年有同类记录""此前已有""量级相当"。

三步产出:
1. 从 claim 摘出全部需核对的具体谓词项(时间范围/金额量级/数量/主体匹配/存在性/比较等),每项一条;claim_requires 摘 claim 原文短语;predicate 用短语命名该项;并给每项标 role:
   - "core":claim 的核心断言本身——它不被载明则整条 claim 未能成立(如"近两年有同类记录"的时间与存在性、"成立条件包括…"的各项条件、"可实际获取"的可核性);
   - "detail":参数性/例示性成分——主体名称、金额数值、项目名称等仅为断言提供参数的字段,以及举例括号("如 X、Y"/"例如")里的示例项:示例项缺失不使 claim 为假。
2. 逐项判定 satisfied 并给引文:
   - "是":evidence_quote 从该 evidence_id 的证据原文**逐字复制**(禁止改写;禁止用 claim 原文自身充当引文),引文须实质载明该项;
   - "否":证据文本中没有载明该项的内容,evidence_quote 留空串;
   - "不确定":信息不足以下判断,evidence_quote 留空串;
   - "不适用":该项谓词**不在文本内容核对范畴**,evidence_quote 留空串。仅限三类:
     (a)出处/平台/发布时间戳型——"在 X 网站/平台发布""于 D 日发布""真实发布"类:平台归属与平台的发布时间戳是采集元数据,页面文本不自称——页面里找不到 D 日字样(只有投标截止/开标等其他日期)时判"不适用",不得判"否"(页面内出现的其他日期属内容,照常核对);
     (b)缺省核查型——"未被更正/撤销/延期""仍然有效"类:由"页面内无相应记载"这一检查事实成立,本无正面引文可引;
     (c)评估型——"充足/合理/可行"类主观判断,非文本事实。
   同一性判准(治"断言超出"病根的关键):
   - claim 断言的记录/文件就是手头证据所记载的本次采购或文件本身(项目名/金额/日期等参数与之吻合)时,证据文本直接载明即可判"是";时体歧义("公开采购过"的"过")按调查语境取自然义——采购公告在场时指本次公开采购,不以"已完成采购"为载明门槛;在手的多份公告各自都是"公开记录"的载体;
   - 归属/类别断言("属于 X 规划/计划/类别",含嵌套的"已公开的 X 规划"限定):证据文本明确出现该类别或项目名称字样即判"是",不要求规划文件本体在场、也不把"已公开的"拆成对规划文件的存在性断言;例:公告项目名"杭州市'三生共荣'中央财政国土绿化示范项目(桐庐县)-…"含"中央财政国土绿化示范项目"字样,claim 断"属于桐庐县中央财政国土绿化示范项目建设规划"→ 判"是"并引该名称字样;
   - claim 断言的是**另一条**记录(历史同类/此前已有/近两年内其他/另一主体)时,本次公告自身的日期、预算金额、项目名称、采购人等字段**不得**充当"是"的引文——必须引另一条独立记录的实质内容;证据里没有这样的记录就判"否"。判"近两年内/近期"等相对时间时,以用户消息给出的核查时点为基准计算,不得因无基准而判否;"近两年有…记录"=存在至少一条落在窗口内的记录(窗口=核查时点往前两年),不要求覆盖整个窗口,手头多份公告若本身即为窗口内的同类记录(日期在场)即判"是";
   - 载明**要求**不等于载明**满足**:claim 断"本企业/某主体具备 X/满足 X/X 上可行"时,公告列出要求条款只证"要求存在",不证"主体满足"——主体满足状况在证据中无记载即判"否"(有主体满足的实质记载才判"是");同理"可实际获取"类断言,仅有渠道指引文字而无获取成功的实质记载判"否"。
   detail 项判"否"不会拦下整条,但必须如实标注,不得为凑"载明"而改判。
3. overall:对 claim 整体给三态:载明|不载明|不确定——只就不适用之外的核心谓词整体判断。
纪律:只依据文本,不外推;items 覆盖 claim 全部具体断言;拿不准判"不确定"。
输出 JSON:{"items":[{"evidence_id":"...","predicate":"...","claim_requires":"...","role":"core|detail","evidence_quote":"...","satisfied":"是|否|不确定|不适用"}],"overall":"载明|不载明|不确定"}"""

_SAT_OK = ("是", "否", "不确定", "不适用")


def _norm_ws(text: str) -> str:
    """引文比对口径:NFKC+去全部空白(与 s6/predicate_form 同哲学)。"""
    return re.sub(r"\s+", "",
                  unicodedata.normalize("NFKC", text or ""))


def triggered(claim_text: str, patterns: list[str]) -> bool:
    """触发正清单(病根族词样,规则表版本化;宁窄勿宽——清单外 claim
    不进闸=元数据承载型零错杀暴露;漏触发=该条不治,可观测)。"""
    text = claim_text or ""
    return any(p and p in text for p in (patterns or []))


def validate(raw: object, claim_text: str,
             evidence_rows: list[dict]) -> dict:
    """结构化验票+归一化(代码,永不 fail-open):
    - evidence_id 必须 ∈ 送审证据(防幻觉 id);satisfied/overall 封闭枚举;
    - 是-项 evidence_quote 须逐字位于该证据文本且非 claim 原文复述(判据 A
      同款 D4);任何失败 → 该项回退"不确定";
    - role 非法按 detail 处理(不拦;错杀率=0 硬门优先)。
    推导(items 主判):core 否项 ≥1 → 不载明;全部项"是"且验票过 → 载明;
    其余(不适用除外)→ 不确定。LLM 输出畸形 → 不确定(保守放行=现行为)。
    """
    out = {"derived": "不确定", "overall": "不确定", "items": [],
           "n_yes": 0, "n_yes_valid": 0, "n_no_valid": 0, "n_core_no": 0,
           "invalid": False}
    if not isinstance(raw, dict):
        out["invalid"] = True
        return out
    overall = str(raw.get("overall", ""))
    out["overall"] = overall if overall in ("载明", "不载明", "不确定") \
        else "不确定"
    by_id = {e["evidence_id"]: e for e in evidence_rows}
    ctext = _norm_ws(claim_text)
    items = raw.get("items")
    if not isinstance(items, list):
        out["invalid"] = True
        return out
    norm_items: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            out["invalid"] = True
            continue
        eid = str(it.get("evidence_id", ""))
        sat = str(it.get("satisfied", ""))
        pred = str(it.get("predicate", "") or "").strip()
        quote = str(it.get("evidence_quote", "") or "")
        role = str(it.get("role", "") or "")
        if role not in ("core", "detail"):
            role = ""
        if eid not in by_id or sat not in _SAT_OK:
            norm_items.append({"satisfied": "不确定", "role": "",
                               "why": "幻觉id或非法枚举"})
            continue
        if sat == "不适用":
            norm_items.append({"satisfied": "不适用", "evidence_id": eid,
                               "role": role})
            continue
        if sat == "是":
            out["n_yes"] += 1
            ev = by_id[eid]
            etext = _norm_ws((ev.get("title") or "") + (ev.get("content")
                                                        or ""))
            qn = _norm_ws(quote)
            if qn and qn in etext and qn not in ctext:
                out["n_yes_valid"] += 1
                norm_items.append({"satisfied": "是", "evidence_id": eid,
                                   "role": role, "quote": quote[:80]})
            else:
                norm_items.append({"satisfied": "不确定", "evidence_id": eid,
                                   "role": role, "why": "引文验票败"})
        elif sat == "否":
            if pred:
                out["n_no_valid"] += 1
                if role == "core":
                    out["n_core_no"] += 1
                norm_items.append({"satisfied": "否", "evidence_id": eid,
                                   "role": role, "predicate": pred[:40]})
            else:
                norm_items.append({"satisfied": "不确定", "evidence_id": eid,
                                   "role": role, "why": "否项缺谓词名"})
        else:
            norm_items.append({"satisfied": "不确定", "evidence_id": eid,
                               "role": role})
    out["items"] = norm_items
    if out["n_core_no"] >= 1:
        out["derived"] = "不载明"
    else:
        relevant = [i for i in norm_items if i["satisfied"] != "不适用"]
        if relevant and all(i["satisfied"] == "是" for i in relevant):
            out["derived"] = "载明"
    return out


class PredicateChecker:
    """核对器(s6 权威放行路径前调用;off=不装配=全链零字节差异)。

    触发正清单不命中 → 零调用直接放行;命中 → 每权威支持根 × runs 次标注,
    合并=各根各轮 (items 推导 ∧ overall) 均不载明才拦(双重标注一致性门)。
    标注异常=该轮不确定(保守放行=现行为;retry=0 照 s6 意见 provider 纪律,
    不放大消耗)。
    """

    def __init__(self, llm, patterns: list[str], runs: int = 2,
                 as_of: str = "", stage: str = "predicate_check"):
        self._llm = llm
        self._patterns = [p for p in (patterns or []) if p]
        self._runs = max(1, int(runs))
        self._as_of = as_of or ""
        self._stage = stage

    def blocks(self, claim_text: str,
               evidence_rows: list[dict]) -> tuple[bool, dict]:
        """返回(是否拦下放行, 留痕明细)。调用方保证:仅权威放行路径
        (权威双根∨单源例外)且 cb=0 的 claim 才进来。"""
        if not triggered(claim_text, self._patterns):
            return False, {"triggered": False, "blocked": False}
        by_root: dict[str, list[dict]] = {}
        for e in evidence_rows:
            by_root.setdefault(e.get("lineage_root") or "?", []).append(e)
        pair_runs: list[list[dict]] = []
        n_yes = n_yes_valid = 0
        for root in sorted(by_root):
            rows = by_root[root]
            runs_v: list[dict] = []
            for _ in range(self._runs):
                user = ("claim:\n" + (claim_text or "")
                        + "\n\n权威证据(拟凭这些证据放行该 claim):\n"
                        + json.dumps([{"evidence_id": e["evidence_id"],
                                       "title": (e.get("title") or "")[:120],
                                       "content": (e.get("content")
                                                   or "")[:2000]}
                                      for e in rows], ensure_ascii=False)
                        + (f"\n\n核查时点(相对时间谓词的基准):{self._as_of}"
                           if self._as_of else ""))
                try:
                    raw = self._llm.generate_json(PC_ANNO_PROMPT, user,
                                                  stage=self._stage)
                except Exception:
                    runs_v.append({"derived": "不确定", "overall": "不确定",
                                   "items": [], "n_yes": 0, "n_yes_valid": 0,
                                   "n_no_valid": 0, "n_core_no": 0})
                    continue
                v = validate(raw, claim_text, rows)
                n_yes += v["n_yes"]
                n_yes_valid += v["n_yes_valid"]
                runs_v.append(v)
            pair_runs.append(runs_v)
        blocked = all(
            all(r["derived"] == "不载明" and r["overall"] == "不载明"
                for r in runs_v)
            for runs_v in pair_runs) if pair_runs else False
        detail = {
            "triggered": True, "blocked": blocked, "runs": self._runs,
            "as_of": self._as_of or None,
            "n_yes": n_yes, "n_yes_valid": n_yes_valid,
            "pairs": [{"root": root,
                       "runs": [{"derived": r["derived"],
                                 "overall": r["overall"],
                                 "n_core_no": r["n_core_no"],
                                 "items": [{"satisfied": i["satisfied"],
                                            "role": i.get("role", ""),
                                            "predicate": i.get("predicate",
                                                               "")[:30]}
                                           for i in r["items"]]}
                                for r in runs_v]}
                      for root, runs_v in zip(sorted(by_root), pair_runs)],
        }
        return blocked, detail
