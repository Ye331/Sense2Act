"""S7 报告生成 = 审查界面(两阶段之阶段二:出循环后本体)。

五环: ①补齐未起草节 -> ②Open Questions 生成(纯 SQL 零 LLM,禁止抹平)
     -> ③行动建议(溯源式,每条挂依据链) -> ④终稿核对(机械断言快照==台账;
        action_facing 下 STALE 禁交付) -> ⑤交付(report.md + report.html 演示包
        + brief.md 企业简报(报告双形态,交付层视图) + result.json + audit.json
        研判审计包)。

报告双形态纪律(2026-10-10 设计决策):六节固定结构与其架构决策不动——
report.md 是台账的六节三态投影(canonical 叙事),brief.md 是**同一台账的
交付层决策视图**(商机卡片/红旗/建议,全部模板确定性生成,零 LLM、零台账
写入);多视图不改变 claim 的节归属与任何调查行为。运营者事项(如替换
模拟样本)经确定性标记词分流进「内部待办」,不进企业简报。

S6 与 check 的分工边界:S6 问"claim 是真的吗"(claim vs 证据,代码终判);
check 问"文本忠实表达台账了吗"(文本 vs 台账)——报告是台账的投影。
"""
from __future__ import annotations

import html as html_mod
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import Field

from ..chain_graph import (build_chain_graph, claim_tier, classify_claim,
                           strip_shell)
from ..conclusion_present import build_conclusion_views
from ..chain_svg import (ChainSvgError, render_chain_png,
                         render_chain_svg, render_md_chain)
from ..config import DomainConfig, InvestigationConfig
from ..credibility import EssRulesError, compute_all, load_rules
from ..delta_present import _cn_ts as _delta_cn_ts
from ..delta_present import render_brief_line as _delta_brief_line
from ..delta_present import render_md as _delta_md
from ..delta_report import compute_delta, read_anchor, write_anchor
from ..ess_present import render_appendix as _ess_appendix
from ..kb_chain import open_kb_ro
from ..kb_check import KbCheckError, assemble_subject, compare_rules
from ..kb_derive import KbDeriveError
from ..kb_derive import load_rules as load_kb_rules
from ..kb_present import render_appendix as _kb_appendix
from ..kb_store import KbStore, KbStoreError
from ..store.ledger import SIMHASH_NEAR_DUP_DIST
from ..llm import StructuredLLM, call_structured
from ..models.base import SchemaModel
from ..models.envelope import InvestigationEnvelope
from ..models.intel import Recommendation
from ..prompts import S7_RECOMMEND_PROMPT
from ..store.ledger import LedgerRepository


class RecommendationsResult(SchemaModel):
    items: list[Recommendation] = Field(default_factory=list)


class RecommendProvider(Protocol):
    def recommend(self, context: dict) -> list[Recommendation]: ...


class LLMRecommendProvider:
    def __init__(self, llm: StructuredLLM, retry: int = 2):
        self._llm = llm
        self._retry = retry

    def recommend(self, context: dict) -> list[Recommendation]:
        out = call_structured(
            self._llm, stage="s7_recommend", system=S7_RECOMMEND_PROMPT,
            user=json.dumps(context, ensure_ascii=False),
            model_cls=RecommendationsResult, retry=self._retry)
        return out.items


_STATUS_BADGE = {"已证实": ("已证实", "ok"), "矛盾": ("矛盾", "bad"),
                 "待证实": ("待证实", "warn")}

# ------------------------------------------------- 报告文风线第一批(P1-P5)
# 施工单 20261107:最高纪律=**读者不知情**——报告读者不了解调查过程与 agent
# loop,过程性机器表述(挂账/判决梯判据/独立根阈值/轮次/"立项"等台账动词/
# 兜底机制名)撤出人看层;机器口径全文留机器层(附录 B 判决证书=每条结论可
# 机器复核章程;result.json 台账),两层各锁。机器判据→用户话只走确定性映射
# 表(claim_present_rules.json 表驱动,改表即生效,禁 LLM),缺键=通用兜底
# 短语仍如实 convey;零信息的过程行整行省略。纯渲染层:台账零写入零新判定。

# 卡上状态呈现词(台账原词 已证实/待证实 留机器层——"呈现层中文、数据层
# 口径"分层纪律,照链卡先例)
_STATUS_PRESENT = {"已证实": "已查证", "待证实": "待查证", "矛盾": "矛盾"}

# 历史行节点上限(照链卡 MAX_HISTORY_NODES=4 截断+注记先例;复查链多轮防线)
OQ_HISTORY_MAX = 4

# 材料/停止原因缺键兜底短语(仍如实 convey 未证实/有未决,失败不伪装)
_MATERIAL_FALLBACK = "现有材料还不足以证实这一条"
_STOP_FALLBACK = "调查收尾(未核实事项见文末「待查证」清单)"

_CITE_RE = re.compile(r"\[E?(\d{1,3})\]")   # 正文引用标两形态:[2]/[E2]


def _load_report_phrases(config_dir: str | Path) -> dict:
    """报告文风短语表(claim_present_rules.json 的 report_verdict_phrases/
    report_stop_phrases 两键,表驱动改表即生效,照 fanin_merge_min 先例);
    缺文件/坏 JSON/非对象 → {}(消费端走兜底短语,失败不伪装)。"""
    try:
        with open(Path(config_dir) / "claim_present_rules.json",
                  encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _prefix_phrase(text: str, table: dict, fallback: str) -> str:
    """最长前缀匹配→用户短语:判决 note/停止原因含动态计数与自由文本,精确键
    不可枚举,前缀锚定首段判据;缺键命中=通用兜底短语(零错杀零冒充)。"""
    hit_key, hit_val = "", fallback
    for key, phrase in (table or {}).items():
        if key and text.startswith(key) and len(key) > len(hit_key):
            hit_key, hit_val = key, phrase
    return hit_val


def _stop_phrase(stop_reason: str, table: dict) -> str:
    """停止原因用户话(P5):全枚举值/前缀表驱动;机器原词不落人看层
    (result.json summary.stop_reason 全量保留=机器层对照)。"""
    return _prefix_phrase(stop_reason or "", table, _STOP_FALLBACK)


def _oq_card_lines(row, gist: str, verdict: dict, trans: list[dict],
                   phrases: dict, anchors: list[str],
                   fusion: dict | None = None) -> list[str]:
    """OQ 三层卡(P1):结论行(徽章+gist/全文+unreachable 用户话注记)/
    材料行(判决 note→用户短语映射,note 原文不上卡退附录 B;锚=实际进入
    裁定的证据 [E{n}];note 空=整行省略零痕迹)/历史行(MM-DD 变更链,呈现
    词,不显轮次号/"立项"等过程词;零变更=整行省略;超 OQ_HISTORY_MAX 截断
    +注记)。trans=[{old,new,ts}](该 claim 的 transitions 投影);全部现成
    量零新判定,矛盾条目同款卡形(徽章〔矛盾〕)。fusion=有效融合旁车
    (第二批 M6,kb.db insights kind=fusion 指纹校验过,经 build_chain_graph
    消费)→材料行升为融合概括「获 N 路独立材料共同印证」;无旁车/未开 kb
    =None=原判决短语行(照 K5 门控先例,只读+缺省零痕迹)。"""
    present = _STATUS_PRESENT.get(row["status"], row["status"])
    head = (f"- **〔{present}〕{gist or row['text']}** "
            f"· 优先级 {row['priority']}")
    if row["unreachable"]:
        head += " · 原链接已不可访问,已登记待补查"
    lines = [head]
    tail = (" " + " ".join(anchors)) if anchors else ""
    if fusion:
        lines.append(f"  - 材料:获 {fusion['n']} 路独立材料共同印证:"
                     f"{fusion['text']}{tail}")
    else:
        note = (verdict or {}).get("note") or ""
        if note:                   # note 空=材料行整行省略(零痕迹,不冒充)
            phrase = _prefix_phrase(note, phrases, _MATERIAL_FALLBACK)
            lines.append(f"  - 材料:{phrase}{tail}")
    if trans:
        items = []
        for t in trans[:OQ_HISTORY_MAX]:
            old = _STATUS_PRESENT.get(t["old"], t["old"])
            new = _STATUS_PRESENT.get(t["new"], t["new"])
            day = (t.get("ts") or "")[:10]
            items.append((f"{day[5:]} " if day else "")
                         + f"由{old}转为{new}")
        more = (f"(共 {len(trans)} 次变更,其余略)"
                if len(trans) > OQ_HISTORY_MAX else "")
        lines.append(f"  - 结论变更:{';'.join(items)}{more}")
    return lines


def _cited_nums(sec_texts) -> set[str]:
    """引用证据号并集(P2 折叠判据):各节文本 [n]/[En] 引用标——[n] 经
    _md_inline 锚到 ev-E{n}(位置引用契约),[En] 为卡面显式形态,两收。"""
    nums: set[str] = set()
    for text in sec_texts:
        nums.update(_CITE_RE.findall(text or ""))
    return nums


def _split_evidence(evidence, cited: set[str],
                    assets: dict) -> tuple[list, list[str]]:
    """附录 A/证据表同源折叠(P2):被引用 ∨ 带报告附件(图片条目保持可点回)
    =逐条保留;其余折叠(只留 id,全文可查 result.json)。编号不重排([n] 锚
    稳定——未引用件本就无锚);全部被引用=零折叠行不冒充。cited=纯数字串
    集合(_cited_nums 口径),tag 尾缀形如 E12 需去 E 归一后再比对。"""
    shown, folded = [], []
    for ev in evidence:
        tag = ev["evidence_id"].rsplit(":", 1)[-1]
        num = tag[1:] if tag.startswith("E") else tag
        if num in cited or (assets or {}).get(ev["evidence_id"]):
            shown.append(ev)
        else:
            folded.append(tag)
    return shown, folded


def _cert_claims(claims, verdicts) -> list[dict]:
    """判决核对证书共用投影(P3):md 附录 B 表与 html 证书卡同源消费(照
    delta/K5"md/html 同源零重复逻辑"先例);条目列**全文**——text[:60] 截断
    退役(批1-B 全文呈现同款精神:项目编号/金额等实锚不腰斩;核对面=全文
    对账位,gist 不替代)。"""
    return [
        {"id": c["draft_id"], "text": c["text"] or "", "status": c["status"],
         "sup": ", ".join(
             (verdicts.get(c["draft_id"]) or {}).get("support_roots") or [])
         or "-",
         "opp": ", ".join(
             (verdicts.get(c["draft_id"]) or {}).get("oppose_roots") or [])
         or "-",
         "note": (verdicts.get(c["draft_id"]) or {}).get("note") or "-"}
        for c in claims]


# ------------------------------------------------ 报告文风线第二批(M1-M5)
# 施工单 20261108:总根因=claim 台账文本(机器对账口径)被当人看口径直接消费
# ——第二批治 LLM 复读通道(闸1 输入过滤)+机械件残余 id(闸3 渲染件)+
# 推理呈现缺位(M4 结论三段卡四问)。三道闸分工:闸1/闸3 确定性代码+模式表;
# 闸2(prompt 口径)属 P 波不在本批。挡除≠抹平:台账三态/附录 B/result.json
# 全量照旧,人看正文不提。

def _claim_tier(text: str, rules: dict) -> str:
    """三档分流判据(M1)——2026-11-08 P 波起公共化到 chain_graph.claim_tier
    (起草输入侧[d_hooks 钩子/finalize/建议]与渲染层同判据同源),此处保留
    兼容别名(既有测试 import 照旧)。"""
    return claim_tier(text, rules)


def _rewrite_clause(text: str, rules: dict) -> str:
    """转写档→用户话短句(M1/M3):模式表 report_rewrite_phrases 前缀映射
    (最长前缀),字段名不出境;缺键=空串(调用方零痕迹,不冒充)。"""
    t = (text or "").lstrip()
    hit, clause = "", ""
    for key, phrase in (rules.get("report_rewrite_phrases") or {}).items():
        if key and t.startswith(key) and len(key) > len(hit):
            hit, clause = key, phrase
    return clause


# 壳词剥离前缀来源键(第三批 M1):shells=信封系转述壳;meta_info=元信息
# (解读自述/指出/种子标签/信封未提供);interpretation=研判定性;gap_hint/
# gap_hint_strip=缺口登记(长形态带"了"+冒号);report_block_extra=信封的/
# 解读登记/解读的 basisGaps 载明。全部确定性前缀,剥前缀留事实直陈。
_SHELL_KEYS = ("shells", "meta_info", "interpretation", "gap_hint",
               "gap_hint_strip", "report_block_extra")


def _polish_section_text(text: str, rules: dict) -> str:
    """正文节文口径转写(M1,F1/F2,第三批):对 S0-S2 draft_text 确定性转写,
    台账 sections 照存原文=双回放安全。三层:①壳词剥离(信封/解读/种子系
    前缀,含报告侧长形态「解读将本信号定性为」——interpretation 表无「为」
    尾缀,剥长形态后剩「商机,理由为…」通顺)+②字段名→人话(report_field_phrases
    表驱动,长度降序防子串覆盖;字段名无映射=保留原文不硬翻,纪律7)+③元
    陈述转写(report_meta_phrases,短语→转写/空串=省略)。改表即生效+测试锁;
    读者不知情:英文字段名/信封/解读(作主语)/种子标签/给定证据列表撤出
    人看正文,机器口径照旧两层各锁(附录 B/result.json)。"""
    t = text or ""
    # ①壳词剥离(前缀+可选冒号,内嵌,长度降序最长先剥)
    shells = ["解读将本信号定性为"]   # 报告侧专用长形态(表无"为"尾缀)
    for key in _SHELL_KEYS:
        shells += list(rules.get(key) or ())
    for shell in sorted(set(shells), key=len, reverse=True):
        base = shell.rstrip("：:")
        if not base:
            continue
        t = t.replace(base + "：", "").replace(base + ":", "").replace(base, "")
    # ②字段名→人话
    field = rules.get("report_field_phrases") or {}
    for k in sorted(field, key=len, reverse=True):
        if k in t:
            t = t.replace(k, field[k])
    # ③元陈述转写
    meta = rules.get("report_meta_phrases") or {}
    for k in sorted(meta, key=len, reverse=True):
        if k in t:
            t = t.replace(k, meta[k])
    return t


def _timeline_event_name(text: str, rules: dict) -> str:
    """时间轴事件名提取(第三批复查,用户拍板 A):从剥壳文本命中事件关键词
    →事件名(模式表 report_timeline_events,最长关键词优先);命中不到=空串
    (调用方 fallback gist/剥壳全文不截断,失败不伪装)。事件名不写日期
    (日期在时间轴行首),治「代理机构为…,投标…」机械截断。"""
    t = text or ""
    hit, name = "", ""
    for kw, ev in (rules.get("report_timeline_events") or {}).items():
        if kw and kw in t and len(kw) > len(hit):
            hit, name = kw, ev
    return name


# ------------------------------------------------- 结论启发式 I1(纯函数)

def _interpret_stance(claims: list[dict], rules: dict) -> dict | None:
    """T6 观点源(结论启发式 I1,方案 §三增补/拍板7):解读定性 claim(M1
    挡除档∧interpretation 前缀)经代码翻译通道提取观点——壳词剥离复用
    _polish_section_text 同源(禁「解读」二字,主语替换由模板句承担),再
    从转写后文本机械提取定性词+理由。多条取台账序首个;无=None(T6 整段
    零痕迹,失败不伪装)。挡除≠观点不可见,挡除=机器话不可见(读者不知情)。
    claims 兼容 sqlite3.Row 与 dict。"""
    inter = list(rules.get("interpretation") or ())
    for c in claims:
        row = c if isinstance(c, dict) else dict(c)
        t = (row.get("text") or "").lstrip()
        if not any(t.startswith(p) for p in inter):
            continue
        polished = _polish_section_text(t, rules)
        m = re.match(r"[:：]?\s*([^,，;。;]+)", polished)
        word = (m.group(1).strip() if m else "")
        if not word:
            continue
        mr = re.search(r"理由为([^。;]+)", polished)
        phrase, family = "", ""
        tbl = rules.get("report_stance_words") or {}
        for k in sorted((tbl.get("signal_stances") or {}), key=len,
                        reverse=True):
            if k and k in word:
                phrase = (tbl.get("signal_phrases") or {}).get(k, "")
                family = tbl["signal_stances"][k]
                break
        return {"word": word, "phrase": phrase or word,
                "family": family, "reason": (mr.group(1).strip() if mr else "")}
    return None


def _hyp_family(label: str, tbl: dict) -> str:
    """假设标签→方向族(report_stance_words.hypothesis_keywords,多者胜;
    平票/零命中=neutral——拍板9 保守:与任何解读定性按一致处理,永不出
    逆转句,宁漏翻不误翻,D4 同款哲学)。"""
    tbl = tbl.get("report_stance_words", tbl)   # 兼容整表/子表两种传参
    hits: dict[str, int] = {}
    for fam, kws in (tbl.get("hypothesis_keywords") or {}).items():
        for kw in kws or ():
            if kw and kw in (label or ""):
                hits[fam] = hits.get(fam, 0) + 1
    if not hits:
        return "neutral"
    top = max(hits.values())
    winners = [f for f, n in hits.items() if n == top]
    return winners[0] if len(winners) == 1 else "neutral"
    for fam, kws in (tbl.get("hypothesis_keywords") or {}).items():
        for kw in kws or ():
            if kw and kw in (label or ""):
                hits[fam] = hits.get(fam, 0) + 1
    if not hits:
        return "neutral"
    top = max(hits.values())
    winners = [f for f, n in hits.items() if n == top]
    return winners[0] if len(winners) == 1 else "neutral"


# ------------------------------------------------- P 波机器闸(P0 探针同轴)
# 报告文风线 P 波(prompt 口径根治,2026-11-08):闸2=prompt 教新口径;机器
# 闸在此做**探针定标仪器+测试禁词锁**,不上渲染路径——live 节文靠 prompt
# 口径,渲染层 M1 机械转写兜底(增强不阻塞=失败不伪装)。禁词=读者不知情
# 纪律口径(与 TestReportStyleBatch 禁词扫同源;样式/纪律常量照颜色先例
# 不入模式表)。探针=tests/_s7_probe.py(照 _pc_probe/_fusion_probe 先例)。

# 过程性机器词(读者不了解调查过程与 agent loop;正文中出现即命中)。
# 「判决」在既有机械面禁词列(判决梯判据语境),法院文书类业务文本由探针
# 逐条判读表人工复核兜底(闸是仪器不是渲染路径,误伤不伪装结论)。
# 「台账」不入表=P0 --llm 实抓校准:报告正文「采购台账」是采购人业务词
# (采购人的账目记录),非调查过程话;过程话载体「挂账」已单列。
# 「立项」带业务形态豁免(_BAN_OK_FORMS):「立项批复/立项文件」等是采购
# 项目审批文件名,非调查 claim 立项动词——探针忻州 r1 真模型首跑实抓。
_BAN_EXTRA = ("信封", "解读", "种子", "立项", "判决", "独立根", "轮次",
              "快照", "挂账", "给定证据列表", "来源不可达", "已登记缺口",
              "登记待补查", "兜底", "以下事项目前尚未核实")
_BAN_OK_FORMS = ("立项批复", "立项文件", "立项材料", "立项手续")  # 后缀形态
_BAN_OK_PREFIX = ("项目立项", "核准立项", "工程立项")   # 前缀形态(立项收尾)
_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z][a-zA-Z0-9]*\b")   # 驼峰字段名形态
_CLAIM_ID_RE = re.compile(r"\b[Cc]\d{1,3}\b")            # claim 草案 id token
_HYP_ID_RE = re.compile(r"[(（][hH]\d{1,2}[)）]|假设\s*[hH]\d{1,2}")  # 假设编号
# 机器腔主语(壳系前缀,模式表同源+_BAN_EXTRA 起头词)
_BG_SUBJECT_WORDS = ("信封", "解读", "信号", "上游", "系统", "种子")
# rationale 三步句式的软指标词(前提→为何现在做→前提条件;形似度读数,
# 供判读表人工复核,不作硬拒——自然语言句式机械硬判会误伤)
_RATIONALE_HINTS = ("前提", "窗口", "若", "条件", "当前", "已查")


def _ban_scan(text: str, rules: dict) -> list[str]:
    """读者不知情禁词扫(P0 探针仪器+测试锁同源):过程性机器词/驼峰字段名/
    claim id token/假设编号/壳系前缀,返回命中清单(空=过)。壳系前缀从
    模式表读(_SHELL_KEYS 同源),禁词常量在此(纪律口径,测试锁死)。"""
    t = text or ""
    hits: list[str] = []
    for w in _BAN_EXTRA:
        if w not in t:
            continue
        if w == "立项":
            # 业务形态豁免:立项批复/立项文件(后缀)与项目立项/核准立项
            # (前缀)是采购项目审批文件名,非调查过程话;全部出现处均为
            # 业务形态才豁免(探针忻州 --llm 实抓校准)
            pos = t.find(w)
            while pos >= 0:
                ok = (t[max(0, pos - 2):pos + 2] in _BAN_OK_PREFIX
                      or any(t[pos:pos + len(f)] == f
                             for f in _BAN_OK_FORMS))
                if not ok:
                    break
                pos = t.find(w, pos + 1)
            if pos < 0:
                continue
        hits.append(w)
    hits += _CAMEL_RE.findall(t)
    hits += _CLAIM_ID_RE.findall(t)
    if _HYP_ID_RE.search(t):
        hits.append("(hN)")
    for key in _SHELL_KEYS:
        for p in rules.get(key) or ():
            if p and p in t:
                hits.append(p)
    return sorted(set(hits))


def _narr_ok(text: str) -> str:
    """结论串讲字数闸(P4):prompt 教 80-150 字,闸窗 60-220 宽容(过窗=
    拒=渲染期纯卡,失败不伪装)。返回空串=过,否则=拒因。"""
    n = len((text or "").strip())
    if not n:
        return "空串讲"
    if n < 60:
        return f"串讲过短({n} 字)"
    if n > 220:
        return f"串讲过长({n} 字)"
    return ""


# 串讲研判词特征(P4 第四道守门):prompt 教的串讲三要素(区分关键/共享
# 事实/未决项)必携带的语义词——旧 prompt 产物的状态说明段(「关于…目前
# 尚未核实…不作事实认定」,忻州 r1 重渲染实抓的误提取实例)不含→不提取。
# 误伤=真串讲换了完全不同措辞→不提取→纯卡=失败不伪装,可接受。
_NARR_MARKS = ("更支持", "倾向", "区分", "共享", "互斥", "更符合", "比较")


def _narr_extract(draft_text: str, rules: dict) -> str:
    """结论节串讲段提取(P4 渲染期):跳过段前标题/列表/粗体行(旧台账模板
    产物「**主导假设:…**」粗体行天然不提取=零变化),取首个普通文本段(连续
    非结构行),过四道守门才算串讲——字数窗(_narr_ok)+禁词零命中(_ban_scan,
    旧 LLM 产物带机器话不冒充串讲)+研判词特征(_NARR_MARKS,治旧 prompt
    状态说明段误提取=忻州 r1 重渲染实抓)。不满足=空串(纯卡,失败不伪装)。"""
    para: list[str] = []
    for raw in (draft_text or "").splitlines():
        line = raw.strip()
        if not line:
            if para:
                break                      # 段落结束
            continue                       # 段前空行
        if line.startswith("#") or line.startswith(("**", "-", "*", ">", "|")) \
                or re.match(r"\d+[.、)]", line):
            if para:
                break
            continue                       # 段前结构行跳过
        para.append(line)
    text = "".join(para)
    if not text or _narr_ok(text) or _ban_scan(text, rules):
        return ""
    if not any(w in text for w in _NARR_MARKS):
        return ""
    return text


def _bg_subject_ok(text: str) -> str:
    """背景节主语闸(P1):首段起头词禁以信封/解读/信号/上游/系统/种子作
    主语(空串=过)。只看起头(句中宾语形态「按信封信息补全」不拦——闸是
    仪器,精确度交给探针判读表)。"""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for w in _BG_SUBJECT_WORDS:
            if line.startswith(w):
                return f"背景节以「{w}」作主语起头"
        return ""
    return ""


def _rationale_ok(text: str) -> str:
    """建议 rationale 闸(P5):禁 claim id token/假设编号(硬闸,空串=过);
    三步句式(前提→为何现在做→前提条件)给形似度读数 hints 键(软指标,
    判读表人工复核,不作拒收)。"""
    t = text or ""
    id_hits = _CLAIM_ID_RE.findall(t)
    if id_hits:
        return f"rationale 含 claim id({id_hits[0]})"
    if _HYP_ID_RE.search(t):
        return "rationale 含假设编号"
    return ""


def _rationale_hints(text: str) -> int:
    """rationale 三步句式形似度(P5 探针读数):命中软指标词个数(0-6)。"""
    t = text or ""
    return sum(1 for w in _RATIONALE_HINTS if w in t)


def claim_input_row(c, refs: list[str], rules: dict,
                    gists: dict | None = None) -> dict:
    """S7 起草输入行(P2 剥壳直陈,2026-11-08):M1 三档过滤后的 claim→
    prompt 输入行——附 gist 短名与 plain(去壳直陈文本)。三态纪律:仅已
    证实剥壳(链侧 strip_shell 同判据,待证实保留壳),待证实/矛盾不带
    plain(prompt 教如实标注原文口径)。finalize 补节/轮间钩子/终稿核对/
    建议/探针五处同源消费=输入侧口径不分叉。"""
    text = c["text"] or ""
    plain = strip_shell(text, rules) if c["status"] == "已证实" else ""
    return {"id": c["draft_id"], "text": text, "status": c["status"],
            "category": c["category"], "refs": refs,
            "gist": (gists or {}).get(c["draft_id"]) or "",
            "plain": plain}


def _brief_phrase(c: dict, gists: dict | None, rules: dict) -> str:
    """素材短语(I2 素材包内部口):gist 优先+剥壳 fallback(_card_phrase
    同判据的模块级形态——机器腔命中=空串,禁裸原文)。"""
    p = (gists or {}).get(c.get("draft_id")) or ""
    if not p:
        stripped = strip_shell(c.get("text") or "", rules)
        if stripped and classify_claim(stripped, rules) is None:
            p = stripped
    return p.strip()


def verdict_brief(led, event_id: str, rules: dict,
                  gists: dict | None = None) -> dict:
    """研判说明素材包(I2-1,2026-11-09 施工单;_insight_lines 旁的输入层,
    加法式):「判据×本案具体化」的素材=代码算,措辞=LLM 写,验票=机器闸
    (I2 拍板 3=分权纪律的终点形态)。全部现成量零 LLM;缺项=键缺席(照
    I1 零痕迹纪律);无主导假设=空表(素材缺=研判说明无从谈起,失败不
    伪装)。字段=label(领先假设名剥括号,r2 实锚「正常大额采购(真实
    商机)」→「正常大额采购」)/criteria(判据=种子表 description 现成;
    禁术语原文整句引用由 prompt 纪律+探针合格判据管,素材照供)/
    t1_baseline(信封 amount_history 检测标量人话,null=键缺席照忻州实账)/
    verified_gists(≤2,已证实事实卡面短语)/contra_state(事件级矛盾态:
    反向短语|「无反向」,I1 判据同源)/runners_up_reason(竞争假设落选
    一句话,M4 比过谁逻辑复用;无落选=键缺席)。机器原文不上 LLM 输入
    (闸 1 纪律延续):素材=翻译后人话。led=LedgerRepository(探针/钩子/
    finalize 三处同源)。"""
    standings = [s for s in led.hypothesis_standings(event_id)
                 if s.get("emerged")]
    dom = next((s for s in standings if s.get("dominant")), None)
    if dom is None:
        return {}
    out: dict = {"label": ""}
    label_raw = dom.get("label") or ""
    out["label"] = (re.sub(r"[(（][^)）]*[)）]", "", label_raw).strip()
                    or label_raw)
    hrow = led.conn.execute(
        "SELECT description FROM hypotheses WHERE event_id=?"
        " AND hypothesis_id=?",
        (event_id, dom.get("hypothesis_id"))).fetchone()
    desc = (hrow["description"] or "").strip() if hrow else ""
    if desc:
        # 判据=description 首句(判据陈述;description 尾部「可区分证据/
        # 判定规则」段是机器结构含 claim id,不上素材=素材是翻译后人话的
        # 纪律[I2-1;探针干跑实抓 description 直出带 c18/c10 机器腔])。
        # 照抄偏差备考:施工单字段表写「判据 description」,落地取其判据
        # 陈述句=语义等价+机器腔剥离。
        first = re.split(r"[。;;\n]", desc)
        criteria = next((x.strip() for x in first if x.strip()), "")
        criteria = re.sub(r"[Cc]\d{1,3}", "", criteria)
        criteria = re.sub(r"[((]c\d{1,3}[))]", "", criteria).strip()
        if criteria:
            out["criteria"] = criteria
    try:
        raw = led.event_envelope_json(event_id)
        env = json.loads(raw) if raw else {}
        ah = (env.get("tender") or {}).get("amount_history") or {}
        ratio = ah.get("amount_ratio")
    except (ValueError, TypeError, AttributeError):
        ratio = None
    if isinstance(ratio, (int, float)) and ratio > 0:
        t1 = f"本次项目金额约为该采购人近期同类项目中位数的 {ratio:g} 倍"
        hc = ah.get("history_count")
        if isinstance(hc, int) and hc:
            t1 += f"(对照 {hc} 条历史记录)"
        out["t1_baseline"] = t1
    by_cid = {c["claim_id"]: dict(c) for c in led.all_claims(event_id)}
    hc_map: list[str] = [r["claim_id"] for r in led.conn.execute(
        "SELECT hc.claim_id FROM hypothesis_claims hc"
        " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
        " WHERE h.event_id=? AND hc.hypothesis_id=? ORDER BY hc.claim_id",
        (event_id, dom.get("hypothesis_id")))]
    ver = [cid for cid in hc_map
           if by_cid.get(cid, {}).get("status") == "已证实"
           and not claim_tier(by_cid[cid]["text"], rules)]
    phrases = []
    for cid in ver:
        p = _brief_phrase(by_cid[cid], gists, rules)
        if p:
            phrases.append(p)
        if len(phrases) >= 2:
            break
    if phrases:
        out["verified_gists"] = phrases
    # contra_state(事件级矛盾态=I1 打折句判据同源;含竞争假设名下矛盾)
    contra = ""
    for cid in sorted(by_cid):
        c = by_cid[cid]
        if c["status"] == "矛盾" and not claim_tier(c["text"], rules):
            p = _brief_phrase(c, gists, rules)
            if p:
                contra = p
                break
    out["contra_state"] = contra or "无反向"
    # runners_up_reason(首个落选假设一句话;M4 比过谁逻辑复用)
    runner = next((s for s in standings if not s.get("dominant")
                   and s.get("verified")), None)
    if runner is not None:
        rid = runner.get("hypothesis_id")
        mem = [r["claim_id"] for r in led.conn.execute(
            "SELECT hc.claim_id FROM hypothesis_claims hc"
            " WHERE hc.hypothesis_id=? ORDER BY hc.claim_id", (rid,))]
        dom_set = set(hc_map)
        if mem and set(mem) <= dom_set:
            reason = "与当前研判共享同一批事实,现有证据尚不能区分二者"
        elif runner.get("pending"):
            reason = (f"已查证 {runner['verified']} 项,但关键事实未查实,"
                      "暂不能排除")
        else:
            reason = (f"已查证 {runner['verified']} 项,少于当前研判"
                      f"({len(ver)} 项已查证)")
        out["runners_up_reason"] = reason
    return out


# ------------------------------------------------ I2 研判说明机器验票(五闸)
# 2026-11-09 施工单 I2-3(照 _ban_scan 家族:纯函数,探针与生产同源;
# 验票=机器,I2 拍板 3)。拒收粒度=单句撤/整段回落零占位(失败不伪装):
# 锚有效/数值保真/实体保真=句级;禁升格/形态闸+段落形态检查=段级。
# 阈值(字数窗/计数)照 fusion 形态闸先例:常量在此测试锁,探针定标复核。

_PENDING_MARKS = ("尚未查实", "未查实", "待查证", "待核实", "待确认",
                  "存疑", "有待核实")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_QUOTED_RE = re.compile(r"[「『“\"']([^「」『』“”\"']{1,60})[」』”\"']")
_SEMIS = (";", chr(0xFF1B))   # 全角分号码点明写(照 _fusion_probe 实抓教训)


def _norm_num(s: str) -> str:
    """数字保真归一(100.0→100;99.90→99.9——数值等形,禁变形语义保留)。"""
    return s.rstrip("0").rstrip(".") if "." in s else s


def _anchor_bad(text: str, valid_refs: set) -> list[str]:
    """闸1 锚有效:输出 [n]/[En] 引用标必须在台账证据号集合内(幻觉锚=句撤)。"""
    bad = []
    for m in re.finditer(r"\[(E?)(\d{1,3})\]", text or ""):
        ref = "E" + m.group(2) if m.group(1) else m.group(2)
        if ref not in valid_refs and m.group(2) not in valid_refs:
            bad.append(m.group(0))
    return sorted(set(bad))


def _numeric_bad(text: str, valid_nums: set) -> list[str]:
    """闸2 数值保真:输出数字串 ⊆ 素材数字集合(允许省略禁变形;fusion
    数字保真同纪律)。归一口径=_norm_num。"""
    bad = []
    for m in _NUM_RE.finditer(text or ""):
        if _norm_num(m.group(0)) not in valid_nums:
            bad.append(m.group(0))
    return sorted(set(bad), key=lambda x: (len(x), x))


def _quoted_unknown(text: str, known_names: list[str]) -> list[str]:
    """闸4 实体保真:引号实体(「X」/『X』/“X”形态)必须是素材已知名
    (假设名/卡面短语/claim 直陈文本)的子串——凭空引用的新实体=句撤
    (可实现形态:语义级实体识别不可为,引号面是确定性抓手,探针定标看
    误伤)。"""
    bad = []
    for m in _QUOTED_RE.finditer(text or ""):
        span = m.group(1).strip()
        if not span:
            continue
        if not any(span in k for k in known_names if k):
            bad.append(span)
    return sorted(set(bad))


def _upgrade_missing(text: str, has_pending: bool) -> str:
    """闸3 禁升格:素材含待证实项时,段落必须带口径标记(「尚未查实」族)
    ——待证实断言不许写成事实(空串=过)。"""
    if not has_pending:
        return ""
    if any(w in (text or "") for w in _PENDING_MARKS):
        return ""
    return "待证实未带口径(禁升格)"


def _verdict_form_bad(text: str) -> str:
    """闸5 形态闸+段落形态检查(照 fusion 第七闸先例):分号连排(半角/
    全角码点明写)≥2/序号堆叠≥2/列表形态混入(破折号/引用/表格结构行)=
    拒。空=过。序号行归「序号堆叠」专判(先于列表形态检查,样例锁)。"""
    t = (text or "").strip()
    if not t:
        return "空串讲"
    if sum(t.count(s) for s in _SEMIS) >= 2:
        return "分号连排罗列"
    if len(re.findall(r"(?m)^\s*\d+[.、)]", t)) >= 2:
        return "序号堆叠"
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) > 1 and any(
            ln.lstrip().startswith(("-", "*", ">", "|")) for ln in lines[1:]):
        return "列表形态(段落混入结构行)"
    return ""


def verdict_gate_material(led, event_id: str, section_id: str, rules: dict,
                          gists: dict | None, brief: dict) -> dict:
    """验票素材面(I2-3;生产 _verdict_narr 与探针同源)=LLM 输入侧同池:
    brief 字段+本节 claim 行(M1 同滤)的 gist/plain 文本与证据引用标。
    valid_nums=素材数字集合(数值保真闸)/valid_refs=证据号集合(锚闸)/
    known_names=已知名面(实体保真闸)/has_pending(禁升格闸)。"""
    valid_nums: set[str] = set()
    valid_refs: set[str] = set()
    known_names: list[str] = [str(v) for v in brief.values()
                              if isinstance(v, str)]
    for v in brief.get("verified_gists") or []:
        known_names.append(v)
    known_names += [str(x.get("label") or "")
                    for x in led.hypothesis_standings(event_id)]
    has_pending = False
    for c in led.claims_for_section(section_id):
        if claim_tier(c["text"], rules):
            continue
        texts = [c["text"] or "", (gists or {}).get(c["draft_id"]) or ""]
        if c["status"] == "已证实":
            texts.append(strip_shell(c["text"] or "", rules))
        known_names += [t for t in texts if t]
        for m in _NUM_RE.finditer(" ".join(texts)):
            valid_nums.add(_norm_num(m.group(0)))
        if c["status"] == "待证实":
            has_pending = True
        for e in led.evidence_for_claim(c["claim_id"]):
            num = e["evidence_id"].rsplit(":", 1)[-1]
            valid_refs.add(num)
            if num.startswith("E"):
                valid_refs.add(num[1:])
    for m in _NUM_RE.finditer(json.dumps(brief, ensure_ascii=False)):
        valid_nums.add(_norm_num(m.group(0)))
    return {"valid_nums": valid_nums, "valid_refs": valid_refs,
            "known_names": known_names, "has_pending": has_pending}


def verdict_gates_apply(narr: str, *, valid_nums: set, valid_refs: set,
                        known_names: list[str], has_pending: bool
                        ) -> tuple[str, list[str]]:
    """研判说明串讲验票(I2-3 主入口;探针与生产同源):句级三闸(锚/数值/
    实体)逐句验,败句撤其余照上;段级两闸(升格/形态)败=整段回落。
    返回 (滤后串讲, 拒因清单);滤后空串=整段回落零占位(失败不伪装,
    机械卡+洞察两段照旧)。"""
    t = (narr or "").strip()
    if not t:
        return "", ["空串讲"]
    parts = re.split(r"([。!?;;])", t)   # 句级切分(保留终止符)
    sents: list[str] = []
    cur = ""
    for p in parts:
        cur += p
        if p in ("。", "!", "?", ";", ";"):
            sents.append(cur)
            cur = ""
    if cur.strip():
        sents.append(cur)
    kept: list[str] = []
    reasons: list[str] = []
    for s in sents:
        bad = (_anchor_bad(s, valid_refs) + _numeric_bad(s, valid_nums)
               + _quoted_unknown(s, known_names))
        if bad:
            reasons.append("句撤:" + "、".join(bad[:3]))
            continue
        kept.append(s)
    body = "".join(kept).strip()
    if not body:
        return "", reasons + ["整段句撤"]
    up = _upgrade_missing(body, has_pending)
    if up:
        return "", reasons + [up]
    form = _verdict_form_bad(body)
    if form:
        return "", reasons + [form]
    # 字数窗不在此重施(提取通道 _narr_ok 已管 60-220;句撤后变短=如实保留
    # 其余照上,照施工单拒收粒度不引申)
    return body, reasons


# draft_id 形态 token(c1/C1/c10aaa——字母开头字母数字串)。S5 渲染期人话化
# 用它区分机器行(依据: c1, c2;…)与已人话化行(幂等),不引入新脆弱面。
_DRAFT_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_REC_ITEM_RE = re.compile(r"^(\d+)\. \*\*(.+?)\*\*\s*(?:\(依赖假设: (.*?)\))?\s*$")
_REC_BASIS_RE = re.compile(r"^   - 依据: (.*?);(.*)$")


def _parse_rec_draft(stored: str) -> list:
    """从台账 S5 draft_text 解析结构化建议(旧台账离线重渲染路径):finalize
    每跑必重写 S5(新产物=新格式),解析只服务旧台账——机器行(draft_id 形态
    token 全数命中)才收;已人话化/解析不出=空表(调用方原文返回,失败不
    伪装)。Recommendation 重建后走同一渲染函数=报告与离线重渲染同源。"""
    from ..models.intel import Recommendation
    items: list[Recommendation] = []
    action, hyp, rationale, ids = "", "", "", []
    for line in (stored or "").splitlines():
        m = _REC_ITEM_RE.match(line)
        if m:
            if action and ids:
                items.append(Recommendation(
                    action=action, rationale=rationale,
                    basis_claim_ids=ids, hypothesis_label=hyp))
            action, hyp, rationale, ids = m.group(2), m.group(3) or "", "", []
            continue
        m = _REC_BASIS_RE.match(line)
        if m and action:
            toks = [t.strip() for t in m.group(1).split(",")]
            if toks and all(_DRAFT_TOKEN_RE.match(t) for t in toks):
                ids, rationale = toks, m.group(2)
    if action and ids:
        items.append(Recommendation(
            action=action, rationale=rationale,
            basis_claim_ids=ids, hypothesis_label=hyp))
    return items


# ------------------------------------------------------------------ 正文 md 渲染
# 正文(draft_text)是 markdown 子集(标题/列表/表格/引用块/粗体/引用标[n])。
# report.html 用零依赖确定性转换呈现(2026-10-27:此前逐行 esc+<br> 连接,
# md 源码原样显示影响阅读)。转换只认白名单语法,一切字符先 escape 再替换
# ——正文永远不可注入 HTML。

_MD_H_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_UL_RE = re.compile(r"^([ ]*)[-*]\s+(.+)$")
_MD_OL_RE = re.compile(r"^\s*\d+[.、]\s+(.+)$")
# 引用标两形态(报告文风线第一批起 [En] 显式形态也锚定:OQ 卡材料行 [E2]
# 原为死文本,现与 [2] 同款可点回证据表;[n] 行为逐字节不变)
_MD_REF_RE = re.compile(r"\[(E?)(\d{1,3})\]")
_MD_HR_RE = re.compile(r"^\s*-{3,}\s*$")
_MD_SEP_RE = re.compile(r"^:?-{2,}:?$")


def _md_inline(text: str) -> str:
    """行内:escape 优先;**粗体** 与 [n]/[En] 引用标(上标,锚到证据表 ev-E{n})。"""
    s = html_mod.escape(text)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = _MD_REF_RE.sub(r'<sup><a href="#ev-E\2">[\1\2]</a></sup>', s)
    return s


def _strip_dup_heading(md: str, title: str | None) -> str:
    """节内正文首个非空行若是标题且与节标题同文则剥离——LLM 起草的正文常
    以 "# 节标题" 开头,report.html 渲染后与节标题 h2 相邻重复(用户 10-27
    晚报告);md 侧天然无此病(draft_text 自带标题行被直接采用)。只看首个
    非空行,不同文/非标题即停,保守。"""
    lines = (md or "").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = _MD_H_RE.match(line.strip())
        if m and m.group(2).strip() == (title or "").strip():
            del lines[i]
        break
    return "\n".join(lines)


def _render_ul(items: list[tuple[int, str]]) -> str:
    """嵌套无序列表(items=(缩进空格数, 行内 html)):同缩进平级,更深缩进
    嵌套进父 li——第三批复查:依据行/OQ 材料行等「  - 」子项此前被平级
    渲染(无视觉缩进),嵌套后与 md 语义一致。层级=空格数//2。"""
    out: list[str] = ["<ul>"]
    stack: list[int] = []
    for indent, h in items:
        level = indent // 2
        if not stack:
            stack.append(level)
            out.append(f"<li>{h}")
            continue
        cur = len(stack) - 1
        if level > cur:
            out.append("<ul>" * (level - cur))
            stack += [level] * (level - cur)
            out.append(f"<li>{h}")
        elif level < cur:
            out.append("</li></ul>" * (cur - level))
            del stack[level + 1:]
            out.append(f"</li><li>{h}")
        else:
            out.append(f"</li><li>{h}")
    out.append("</li>")
    out.append("</ul>" * len(stack))
    return "".join(out)


def _md_block(md: str) -> str:
    """块级状态机:标题(#/##→h3,###↓→h4,不与节标题 h2 抢层级)/无序与
    有序列表(无序支持嵌套缩进)/md 表格(吞 |---| 分隔行)/引用块/分隔线;
    普通连续行聚为段落,空行分段。"""
    out: list[str] = []
    kind = ""                       # 当前聚合块:ul/ol/table/quote/p
    buf: list[str] = []

    def flush() -> None:
        nonlocal kind, buf
        if kind == "ul":
            out.append(_render_ul(buf))
        elif kind == "ol":
            out.append("<ol>" + "".join(f"<li>{b}</li>" for b in buf) + "</ol>")
        elif kind == "table":
            rows = [[c.strip() for c in r.strip().strip("|").split("|")]
                    for r in buf]
            head, body = rows[0], [r for r in rows[1:] if not all(
                _MD_SEP_RE.fullmatch(c) for c in r if c)]
            out.append("<table><thead><tr>" + "".join(
                f"<th>{_md_inline(c)}</th>" for c in head)
                + "</tr></thead><tbody>" + "".join(
                    "<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r)
                    + "</tr>" for r in body) + "</tbody></table>")
        elif kind == "quote":
            out.append("<blockquote>" + "".join(
                f"<p>{b}</p>" for b in buf) + "</blockquote>")
        elif kind == "p":
            out.append("<p>" + "<br>".join(buf) + "</p>")
        buf, kind = [], ""

    for line in (md or "").splitlines():
        m_h, m_ul, m_ol = (_MD_H_RE.match(line), _MD_UL_RE.match(line),
                           _MD_OL_RE.match(line))
        if line.strip().startswith("|"):
            if kind != "table":
                flush()
                kind = "table"
            buf.append(line)
        elif m_h:
            flush()
            level = 3 if len(m_h.group(1)) <= 2 else 4
            out.append(f"<h{level}>{_md_inline(m_h.group(2))}</h{level}>")
        elif m_ul:
            if kind != "ul":
                flush()
                kind = "ul"
            buf.append((len(m_ul.group(1)), _md_inline(m_ul.group(2))))
        elif m_ol:
            if kind != "ol":
                flush()
                kind = "ol"
            buf.append(_md_inline(m_ol.group(1)))
        elif line.startswith(">"):
            if kind != "quote":
                flush()
                kind = "quote"
            buf.append(_md_inline(line.lstrip("> ").rstrip()))
        elif _MD_HR_RE.match(line):
            flush()
            out.append("<hr>")
        elif line.strip():
            if kind not in ("", "p"):
                flush()
            kind = "p"
            buf.append(_md_inline(line.strip()))
        else:
            flush()
    flush()
    return "".join(out)

def _appendix_body(md: str) -> str:
    """appendix md → card 正文:剥首个标题行(card h2 已表意,防相邻重复),
    走 _md_block 白名单转换(与六节正文同一安全口径,正文不可注入)。渲染
    批次(2026-10-27)起 ESS/K5 html 节共用——appendix 文本即 md 双 append
    的同源产物。"""
    lines = md.replace("\r\n", "\n").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return _md_block("\n".join(lines))


def _card(title: str, body_html: str) -> str:
    return (f'<div class="card"><h2>{html_mod.escape(title)}</h2>'
            f'<div class="body">{body_html}</div></div>')


# 运营者事项分流标记(报告双形态 P1,2026-10-10):建议文案涉及系统内部数据
# 质量(如替换模拟历史样本)时归「内部待办」,不进企业简报。确定性规则先行,
# prompt 受众字段留待下次录制批(分权:能代码兜底的不先动 prompt)。
_INTERNAL_MARKERS = ("模拟样本", "模拟历史", "模拟数据")


class ReportBuilder:
    def __init__(
        self,
        ledger: LedgerRepository,
        domain: DomainConfig,
        config: InvestigationConfig,
        writer,                     # SectionWriter(draft/check/edit)
        provider: RecommendProvider | None = None,
        runs_dir: str | Path = "runs",
    ):
        self._ledger = ledger
        self._domain = domain
        self._config = config
        self._writer = writer
        self._provider = provider
        self._runs = Path(runs_dir)

    # ------------------------------------------------------------- 主入口

    def finalize(self, event_id: str, round_no: int, stop_reason: str,
                 costs: dict | None = None, envelope_label: str = "",
                 signal_type: str = "", envelope=None) -> dict:
        # 环① 补齐未起草节(结论节从假设记分板起草;空节用信封摘要)
        # M1 三档分流(第二批 闸1):挡除/转写类 claim 不进正文 LLM 输入
        # ——确定性前缀判据(模式表),LLM 想复读机器口径也没料;快照与
        # 节归属全量不动(挡除≠抹平)。回放=重放录制输出,不受影响。
        rules = self._report_phrases()
        gists0 = self._ledger.claim_gists(event_id)
        for sec in self._ledger.pending_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            kind = "conclusion" if sec["ord"] == 3 else sec["kind"]
            in_claims = [c for c in claims
                         if _claim_tier(c["text"], rules) == ""]
            context = {
                "event_id": event_id, "title": sec["title"], "kind": kind,
                "claims": self._claim_payload(in_claims, event_id),
                "standings": self._ledger.hypothesis_standings(event_id),
                "envelope_label": envelope_label,
            }
            if kind == "conclusion":
                # I2 研判说明素材包(判据×本案;代码算措辞 LLM 写,拍板 3;
                # 空表=无主导假设=键缺席,prompt 照常走串讲不冒充)
                brief = verdict_brief(self._ledger, event_id, rules, gists0)
                if brief:
                    context["verdict_brief"] = brief
            text = self._writer.draft(context)
            snapshot = {c["draft_id"]: c["status"] for c in claims}
            self._ledger.set_draft(sec["section_id"], text, snapshot, round_no)

        # 环② Open Questions(纯代码零 LLM,按优先级置顶,禁止抹平)
        # M1:挡除类不上卡(同判据;_render_oq 内部再滤=离线重渲染同口径)
        oq_rows = [r for r in self._ledger.oq_claims(event_id)
                   if _claim_tier(r["text"], rules) != "block"]
        # gist 短名表(gist 短名方案,2026-10-30):S1b 落库后即定型,交付前查一次
        # 全产物共用;空表(旧台账/未开 prompt)=消费端 fallback=逐字节现状
        gists = self._ledger.claim_gists(event_id)
        oq_text = self._render_oq(oq_rows, gists=gists, event_id=event_id)
        oq_sec = f"{event_id}:S4"
        self._ledger.set_draft(oq_sec, oq_text, {}, round_no)

        # 环③ 行动建议(溯源式:依据链校验不过则丢弃该条)
        recommendations = self._recommend(event_id, envelope_label)
        rec_text = self._render_recommendations(
            recommendations, gists=gists,
            claims=self._ledger.all_claims(event_id))
        self._ledger.set_draft(f"{event_id}:S5", rec_text, {}, round_no)

        # 环④ 终稿核对:断言全部 standard 节快照 == 最终台账(不一致即流程 bug)
        self._final_check(event_id, round_no)

        # 环⑤ 交付
        summary = self._summary(event_id, stop_reason, recommendations, costs,
                                signal_type)
        out_dir = self._runs / event_id
        # 渲染批次(2026-10-27):ESS 计算上移到交付前——html 证据强度节与
        # md 落盘同源消费同一 result;off/降级形态见 _ess_compute。
        ess_result = self._ess_compute(event_id)
        # delta 头部(渲染批次):上次结算锚→纯读 diff;首跑无锚=None=零痕迹
        delta = self._delta_rows(event_id, out_dir)
        # K5 资质核对(渲染批次):门控链不满足→None=零痕迹;门控链见 _kb_rows
        k5 = self._kb_rows(event_id)
        self._deliver(out_dir, event_id, summary, envelope_label,
                      envelope=envelope, oq_rows=oq_rows,
                      rec_items=recommendations, ess_result=ess_result,
                      delta=delta, k5=k5, gists=gists)
        # ESS 证据强度分旁车(阶段一,2026-10-20;方案 §五):finalize 末尾
        # 判决后只读派生——纯计算零 LLM,ess.json 旁车+decision_log 概要 1 条
        # (kind='其他');不回流台账/prompt(一账两读;P6 负测试锁死 S6 隔离)。
        summary["ess"] = self._ess_finalize(event_id, round_no, out_dir,
                                            envelope=envelope,
                                            ess_result=ess_result, gists=gists)
        # K5 呈现落盘(渲染批次):kb_check.json 旁车+md/brief 双 append
        summary["k5"] = self._kb_finalize(out_dir, k5)
        # 结算锚落盘(渲染批次,末位=全部产物结算后):次跑 delta 的时点锚
        self._delta_anchor(out_dir)
        return summary

    def _kb_rows(self, event_id: str) -> dict | None:
        """K5 比对(渲染批次):门控链=--kb on ∧ kb.db 存在 ∧ 有规则卡 ∧
        有自我卡,任一不满足→None(零痕迹,用户拍板语义);配置坏/比对执行
        错→{"error": …}(降级注记,照 ESS"本报告未评"先例);成功→
        {"rows": […], "internal": bool}。数据层=kb_check 纯函数;mains 传
        当前 ledger 现成连接(零新连接零锁;跨 event 链接→KbCheckError 降级)。
        """
        kb_path = getattr(self._config, "kb_path", "")
        if (not getattr(self._config, "kb_enabled", False)) or not kb_path:
            return None
        p = Path(kb_path)
        if not p.exists():
            return None
        try:
            kbr = load_kb_rules(self._domain.config_dir / "kb_rules.json")
        except KbDeriveError as exc:
            return {"error": f"kb_rules.json 不可用({exc})"}
        zones = kbr["zones"]                   # zones 节本体(CLI kb-check 同款)
        self_id = (kbr.get("rule_check") or {}).get(
            "self_entity_id", "ent_self")
        store = KbStore.open(p)
        try:
            rules = store.rules()
            if not rules or self_id not in {e["entity_id"]
                                            for e in store.entities()}:
                return None                    # 门控:无规则卡/无自我卡
            try:
                subject = assemble_subject(store, {event_id: self._ledger},
                                           self_id, zones)
            except KbCheckError as exc:
                return {"error": str(exc)}
            internal = str((subject.get("attributes") or {})
                           .get("sensitivity") or "") == "internal"
            try:
                return {"rows": compare_rules(rules, [subject], zones),
                        "internal": internal}
            except KbCheckError as exc:
                return {"error": str(exc)}
        except (KbStoreError, sqlite3.DatabaseError) as exc:
            return {"error": str(exc)}     # kb.db 缺表/损坏→降级注记不炸交付
        finally:
            store.conn.close()

    def _kb_finalize(self, out_dir: Path, k5: dict | None) -> dict | None:
        """K5 呈现落盘(渲染批次):成功→kb_check.json 旁车+report.md/brief.md
        双 append(照 ESS append-only 先例,用户拍板双挂);执行错→注记;
        门控不满足→零痕迹。呈现零写台账(待证实/红线只呈现与指引)。"""
        if k5 is None:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        if "error" in k5:
            try:
                with (out_dir / "report.md").open("a", encoding="utf-8") as f:
                    f.write(f"\n> ⚠️ 资质核对(K5)未执行:{k5['error']}\n")
            except OSError:
                pass
            return {"written": False, "reason": k5["error"]}
        rows = k5["rows"]
        (out_dir / "kb_check.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        appendix = _kb_appendix(rows, subject_internal=bool(k5["internal"]))
        for name in ("report.md", "brief.md"):
            with (out_dir / name).open("a", encoding="utf-8") as f:
                f.write(appendix)
        return {"written": True, "n_cells": len(rows),
                "internal": bool(k5["internal"])}

    def _delta_rows(self, event_id: str, out_dir: Path) -> dict | None:
        """delta 纯读计算(渲染批次):上次结算锚后的修正/新增/不变。
        锚缺失(首次运行)→None=三产物零痕迹。数据层=delta_report 纯函数。"""
        anchor = read_anchor(out_dir)
        if anchor is None:
            return None
        return compute_delta(self._ledger, event_id, anchor)

    def _delta_anchor(self, out_dir: Path) -> None:
        """结算锚落盘(finalize 末位):次跑 delta 的时点锚。粒度=台账 _now
        同款秒级(timespec=seconds)——锚带微秒会让秒级 transitions 字符串
        比较恒判「锚前」('+'<'.'),锚后同秒修正被漏(demo 实证);秒级锚下
        「ts<=锚」=不晚于本结算秒,结算后到达必下一秒起,无歧义。now=运行时
        旁车时间戳(与 ESS as_of 同款;禁 now 纪律针对派生层幂等,此处不涉)。"""
        write_anchor(out_dir, datetime.now(timezone.utc).isoformat(
            timespec="seconds"))

    def _ess_compute(self, event_id: str) -> dict | None:
        """ESS 计算上移(渲染批次 2026-10-27):finalize 环⑤前算一次,html
        证据强度节与 md 落盘同源消费。三态返回:off→None(零痕迹);
        规则表坏→{"error": …}(降级标记,_ess_finalize 消费它写"本报告未评"
        注记);成功→compute_all 结果。
        """
        if not getattr(self._config, "ess_enabled", False):
            return None
        try:
            rules = load_rules(self._domain.config_dir / "ess_rules.json")
        except EssRulesError as exc:
            return {"error": str(exc)}
        return compute_all(
            self._ledger, event_id, rules,
            as_of=datetime.now(timezone.utc),
            authoritative_roots=self._domain.authoritative_roots,
            near_dup_dist=(SIMHASH_NEAR_DUP_DIST
                           if getattr(self._config, "t1_independence_enabled",
                                      False) else None))

    def _ess_finalize(self, event_id: str, round_no: int,
                      out_dir: Path, envelope=None,
                      ess_result: dict | None = None,
                      gists: dict | None = None) -> dict | None:
        """ESS 旁车落盘(零行为参数:关=只不落旁车;不入 META_KEYS)。

        降级语义:规则表缺失/损坏 → 整体跳过+报告注"本报告未评",
        不装 0 分(失败不伪装平移到分数层)。ess_result=渲染批次
        (2026-10-27)起由 finalize 预算传入(html 节同源消费);缺省=
        内部现算(兼容直调)。gists=短名表(gist 方案,呈现消费)。
        """
        if not getattr(self._config, "ess_enabled", False):
            return None
        if ess_result is None:
            ess_result = self._ess_compute(event_id)
        if ess_result is None:
            return None
        if "error" in ess_result:
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                with (out_dir / "report.md").open("a", encoding="utf-8") as f:
                    f.write(f"\n> ⚠️ 本报告未评:证据强度分(ESS)规则表不可用,"
                            f"已整体跳过({ess_result['error']})。\n")
            except OSError:
                pass
            return {"written": False, "reason": str(ess_result["error"])}
        result = ess_result
        # decision_log 概要 1 条(CHECK 纪律:复用 kind='其他',同去重审计先例;
        # 不加新 kind——旧库会崩)
        self._ledger.log(event_id, round_no, "其他", {
            "ess": {"rule_version": result["rule_version"],
                    "n_claims": result["n_claims"],
                    "tier_counts": result["tier_counts"]}})
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ess.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        # ESS 呈现追加节(阶段二,2026-10-21):append-only 尾部落盘,不碰
        # _deliver/渲染层(图片线并行安全);off/降级路径不会走到这里
        appendix = _ess_appendix(
            result, kind=str(getattr(envelope, "kind", "") or ""),
            gists=gists)
        if appendix:
            for name in ("report.md", "brief.md"):
                with (out_dir / name).open("a", encoding="utf-8") as f:
                    f.write(appendix)
        return {"written": True, "rule_version": result["rule_version"],
                "n_claims": result["n_claims"],
                "tier_counts": result["tier_counts"]}

    # ------------------------------------------------------------- 环②③

    def _render_oq(self, rows, gists: dict | None = None,
                   event_id: str = "") -> str:
        """OQ 三层卡(报告文风线第一批 P1):结论/材料/历史三行全用户话,与
        链图命题卡同构;排序与节资格(pending/unreachable 行=现状)不动。
        材料行短语表/历史行/锚全部现算自台账(零新判定);note 原文不上卡
        (读者不知情,退附录 B 机器层)。
        第二批(M1/M3/M6):挡除类 claim 不上卡(台账照旧=挡除≠抹平),转写
        类无 gist 时卡面显示转写短句(机器原文不露);有效融合旁车→材料行升
        为融合概括;尾部挡除计数注记(措辞不暴露机制名,N=0 整行省略)。
        event_id=调用方显式传(finalize/渲染期),空则自行取 rows[0](挡除
        清零后 rows 可能全空,注记计数仍需 event)。"""
        eid = event_id or (rows[0]["event_id"] if rows else "")
        tbl = self._report_phrases()
        phrases = tbl.get("report_verdict_phrases") or {}
        rows = [r for r in rows or []
                if _claim_tier(r["text"], tbl) != "block"]
        lines = ["### Open Questions", "",
                 "以下断言截至交付仍未获足够独立证据,**如实保留,不做推测性结论**:",
                 ""]
        if not rows:
            lines.append("- (无未决问题)")
        else:
            verdicts = self._verdict_notes(eid)
            trans_by_claim: dict[str, list[dict]] = {}
            for r in self._ledger.transitions(eid):
                trans_by_claim.setdefault(
                    r["claim_id"].rsplit(":", 1)[-1], []).append(
                    {"old": r["old_status"], "new": r["new_status"],
                     "ts": r["ts"]})
            # 融合旁车(M6):build_chain_graph 已做指纹校验+独立根折一代表
            # +sensitive/反证排除;此处纯消费。kb 未开/graph 不可用=空表。
            fu_by: dict[str, dict] = {}
            for f in (self._chain_graph(eid) or {}).get("fusions") or []:
                fu_by.setdefault(f["claim_id"].rsplit(":", 1)[-1], f)
            for r in rows:
                # 锚=证据 id 尾缀原样(evidence_id 形如 "{event}:E2",尾缀已含
                # E,不得再叠前缀——[EE2] 会被 _CITE_RE 漏收连累 P2 折叠判据)
                anchors = [f"[{e['evidence_id'].rsplit(':', 1)[-1]}]"
                           for e in self._ledger.evidence_for_claim(r["claim_id"])
                           if e["ruling"] in ("支持", "矛盾", "时效刷新")]
                disp = (gists or {}).get(r["draft_id"]) or ""
                if not disp and _claim_tier(r["text"], tbl) == "rewrite":
                    disp = _rewrite_clause(r["text"], tbl)
                lines += _oq_card_lines(
                    r, disp,
                    verdicts.get(r["draft_id"]) or {},
                    trans_by_claim.get(r["draft_id"]) or [],
                    phrases, anchors, fusion=fu_by.get(r["draft_id"]))
        n_block = sum(1 for c in self._ledger.all_claims(eid)
                      if _claim_tier(c["text"], tbl) == "block")
        if n_block:
            lines += ["", f"> 另有 {n_block} 条信号层登记信息不进正文"
                          f"(全文见附录 B)"]
        return "\n".join(lines)

    def _recommend(self, event_id: str, envelope_label: str) -> list[Recommendation]:
        standings = self._ledger.hypothesis_standings(event_id)
        # P 波输入侧(2026-11-08):claims 附 gist/plain(claim_input_row 同源,
        # refs 不带=建议输入不需要证据列表);**不做 M1 滤**——建议依据链校验
        # (valid_ids)语义=M2/M5 拍板「机器腔依据不上行但整条保留+计数如实
        # (挡除≠抹平)」,滤会把整条建议丢给模板兜底(实测抓的回归);机器腔
        # 条进 prompt 输入由 P5 口径(禁机关话)+渲染层 classify 拦截兜底
        rules = self._report_phrases()
        gists = self._ledger.claim_gists(event_id)
        claims = [claim_input_row(r, [], rules, gists)
                  for r in self._ledger.all_claims(event_id)]
        valid_ids = {c["id"] for c in claims}
        context = {
            "event": envelope_label, "standings": standings, "claims": claims,
            "watch_focus": [w.get("focus", "") for w in self._domain.watchlist],
        }
        items: list[Recommendation] = []
        if self._provider is not None:
            try:
                items = self._provider.recommend(context)
            except Exception:
                items = []
        # 校验依据链(建议不能凭空出现);不足则用模板建议兜底
        items = [r for r in items if set(r.basis_claim_ids) & valid_ids]
        if not items and standings:
            # 模板兜底:主导假设优先;无主导(得分<=0)时取最高分假设,
            # 但依据链必须挂在已证实 claim 上——没有已证实项则不出建议
            top = next((s for s in standings if s.get("dominant")), None)
            if top is None:
                top = max(standings, key=lambda s: s["score"])
            verified = [r["draft_id"] for r in self._ledger.all_claims(event_id)
                        if r["status"] == "已证实"]
            if verified:
                lead = "当前证据结构更支持该假设" if top.get("dominant") \
                    else "当前证据倾向该假设(尚不构成主导)"
                items = [Recommendation(
                    action=f"围绕假设「{top['label']}」组织后续跟踪,"
                           f"并对未决项安排补充核查",
                    rationale=f"{lead}(已证实 {top['verified']} 条,"
                              f"矛盾 {top['contradicted']} 条);未决项依据"
                              f" Open Questions 节安排人工补查。",
                    basis_claim_ids=verified[:2],
                    hypothesis_label=top["label"],
                )]
        return items

    def _basis_lines(self, basis_ids: list[str], by_draft: dict,
                     gists: dict | None, rules: dict) -> list[str]:
        """建议依据逐条徽章(M5,F6,第三批):每条依据 claim 一行带〔已查证/
        待查证/已反对〕,与 OQ 卡/结论卡同徽章式;gist 优先/剥壳 fallback/
        机器腔不上行(判据同第二批 M2);计数行如实(三态),超 3 折叠「等」。
        rationale 由调用方原文照旧另起行。禁裸 claim 原文禁 id(负测试锁)。"""
        ids = list(basis_ids or [])
        n = len(ids)
        ver = sum(1 for did in ids
                  if (by_draft.get(did) or {}).get("status") == "已证实")
        rows = [f"- 依据({n} 项,其中 {ver} 项已查实):"]
        phrases: list[tuple[str, str]] = []
        for did in ids:
            c = by_draft.get(did) or {}
            p = (gists or {}).get(did) or ""
            if not p:
                stripped = strip_shell(c.get("text") or "", rules)
                if stripped and classify_claim(stripped, rules) is None:
                    p = stripped
            if p:
                st = {"已证实": "已查证", "矛盾": "已反对"}.get(
                    c.get("status"), "待查证")
                phrases.append((p.rstrip("。;;、,，"), st))
        for p, st in phrases[:3]:
            rows.append(f"  - {p} 〔{st}〕")
        if len(phrases) > 3:
            rows.append(f"  - (其余 {len(phrases) - 3} 项等,见附录 B)")
        return rows

    def _render_recommendations(self, items: list[Recommendation],
                                gists: dict | None = None,
                                claims=None) -> str:
        """行动建议节(M2 人话化+M5 逐条徽章):机械行「依据: c1, c2…」退役→
        依据逐条徽章(_basis_lines,第三批 M5);LLM rationale 原文照旧另起行
        (P 波定标后禁 id)。gists/claims 缺省=旧形态(直调兼容);finalize/
        brief/内部待办同源消费(报告与 brief 同一函数=同改)。"""
        lines = ["### 行动建议", "",
                 "每条建议附依据(已查证事实列举,可沿正文引用标追溯);"
                 "建议供决策参考,不替代企业决策。", ""]
        if not items:
            lines.append("- (无足以支撑行动建议的已查实结论)")
        rules = self._report_phrases()
        by_draft = {c["draft_id"]: dict(c) for c in (claims or [])}
        for i, r in enumerate(items, 1):
            hyp = f"(依赖假设: {r.hypothesis_label})" if r.hypothesis_label else ""
            lines.append(f"{i}. **{r.action}** {hyp}")
            lines += self._basis_lines(r.basis_claim_ids, by_draft,
                                       gists or {}, rules)
            if r.rationale:
                lines.append(f"   {r.rationale}")
        return "\n".join(lines)

    # ------------------------------------------------------------- 环④

    def _final_check(self, event_id: str, round_no: int) -> None:
        if self._config.deployment_mode == "action_facing":
            stale = self._ledger.stale_sections(event_id)
            if stale:
                raise RuntimeError(
                    f"action_facing 模式禁止交付 STALE 节: "
                    f"{[s['title'] for s in stale]}")
        # report_only:先修复残留 STALE 节(定向重写+快照刷新),再做断言
        for sec in self._ledger.stale_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            snapshot = json.loads(sec["snapshot_json"] or "{}")
            current = {c["draft_id"]: c["status"] for c in claims}
            drift = [
                {"claim_id": cid, "issue": f"快照 {old} != 台账 {current.get(cid)}"}
                for cid, old in snapshot.items() if current.get(cid) != old]
            text = self._writer.edit(
                sec["draft_text"] or "", drift,
                {"title": sec["title"], "kind": sec["kind"],
                 "claims": self._claim_payload(claims, event_id),
                 "drift": drift})
            self._ledger.set_draft(
                sec["section_id"], text, current, round_no, redraft=True)
        for sec in self._ledger.drafted_sections(event_id):
            if sec["kind"] != "standard":
                continue  # OQ/建议节不做快照断言(自动聚合视图)
            claims = self._ledger.claims_for_section(sec["section_id"])
            snapshot = json.loads(sec["snapshot_json"] or "{}")
            current = {c["draft_id"]: c["status"] for c in claims}
            if snapshot != current:
                # 修复一次(edit 定向重写)再断言;再不一致即流程 bug
                drift = [
                    {"claim_id": cid, "issue": f"快照 {old} != 台账 {current.get(cid)}"}
                    for cid, old in snapshot.items() if current.get(cid) != old]
                text = self._writer.edit(
                    sec["draft_text"] or "", drift,
                    {"title": sec["title"], "kind": sec["kind"],
                     "claims": self._claim_payload(claims, event_id),
                     "drift": drift})
                self._ledger.set_draft(
                    sec["section_id"], text, current, round_no, redraft=True)
                claims2 = self._ledger.claims_for_section(sec["section_id"])
                snapshot2 = {c["draft_id"]: c["status"] for c in claims2}
                sec2 = [s for s in self._ledger.drafted_sections(event_id)
                        if s["section_id"] == sec["section_id"]][0]
                if json.loads(sec2["snapshot_json"] or "{}") != snapshot2:
                    raise AssertionError(
                        f"终稿核对失败(快照!=台账): {sec['title']} —— 流程 bug")

    # ------------------------------------------------------------- 环⑤

    def _summary(self, event_id: str, stop_reason: str,
                 recommendations: list[Recommendation], costs: dict | None,
                 signal_type: str) -> dict:
        return {
            "event_id": event_id, "signal_type": signal_type,
            "stop_reason": stop_reason,
            "status_counts": self._ledger.status_counts(event_id),
            "standings": self._ledger.hypothesis_standings(event_id),
            "relations": [
                {k: r[k] for k in ("head_name", "relation_type", "tail_name")}
                for r in self._ledger.relations_view(event_id)],
            "transitions": [
                {"claim": t["claim_id"].rsplit(":", 1)[-1],
                 "old": t["old_status"], "new": t["new_status"],
                 "cause": t["cause"], "round": t["round"],
                 "ts": t["ts"]}
                for t in self._ledger.transitions(event_id)],
            "recommendations": [r.model_dump(by_alias=True) for r in recommendations],
            "costs": costs or {},
        }

    def _deliver(self, out_dir: Path, event_id: str, summary: dict,
                 envelope_label: str, envelope=None, oq_rows=None,
                 rec_items=None, ess_result=None, delta=None,
                 k5=None, gists=None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._archive_previous(out_dir)     # 动态报告⑤:版本化存档(旧报告进 history)
        sections = self._ledger.get_sections(event_id)
        evidence = self._ledger.conn.execute(
            "SELECT * FROM evidence WHERE event_id=? ORDER BY rowid",
            (event_id,)).fetchall()
        verdicts = self._verdict_notes(event_id)
        # 报告附件(A 线):evidence.assets 只在此消费;无附件时各产物零变化
        assets = self._ledger.evidence_assets(event_id)
        md = self._render_markdown(
            sections, evidence, verdicts, summary, envelope_label,
            rec_items=rec_items, assets=assets, delta=delta, gists=gists)
        (out_dir / "report.md").write_text(md, encoding="utf-8")
        (out_dir / "report.html").write_text(
            self._render_html(sections, evidence, verdicts, summary,
                              envelope_label, assets=assets,
                              ess_result=ess_result, delta=delta, k5=k5,
                              gists=gists, rec_items=rec_items,
                              out_dir=out_dir),
            encoding="utf-8")
        # 企业简报(形态 A):同一台账的决策视图,模板确定性生成,零 LLM 零写库
        (out_dir / "brief.md").write_text(
            self._render_brief(summary, envelope, envelope_label,
                               oq_rows or [], rec_items or [], evidence,
                               delta=delta, gists=gists),
            encoding="utf-8")
        (out_dir / "result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        # 研判审计包:全链路留痕导出(合规/复盘用)
        audit = {
            "event_id": event_id,
            "decision_log": [
                {"round": r["round"], "kind": r["kind"],
                 "payload": json.loads(r["payload_json"]),
                 "degraded": bool(r["degraded"])}
                for r in self._ledger.decision_log_rows(event_id)],
            "transitions": summary["transitions"],
            "sections": [
                {"title": s["title"], "state": s["state"],
                 "snapshot": json.loads(s["snapshot_json"] or "{}")}
                for s in sections],
        }
        if assets:   # 附件清单(路径+sha256,防"图被换");无附件不加键=旧产物逐字节不变
            audit["assets"] = [
                {"evidence_id": eid, **a}
                for eid, items in sorted(assets.items()) for a in items]
        (out_dir / "audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")

    # ------------------------------------------------------------- 渲染

    @staticmethod
    def _archive_previous(out_dir: Path) -> None:
        """动态报告⑤:交付前把上一版产物存档到 report_history/。

        report.md/report.html/brief.md 三件同档(同一结算时点的完整呈现);
        文件名取上一版 delta 锚时间(结算语义:那版是"截至该时点"的结论),
        无锚(首跑/旧产物)用文件修改时间。首跑无 report.md = 零动作。
        """
        prev = out_dir / "report.md"
        if not prev.exists():
            return
        from ..delta_report import read_anchor
        anchor = read_anchor(out_dir)
        import time as _time
        stamp = (anchor or "").replace(":", "").replace("-", "")[:15] \
            or _time.strftime("%Y%m%d_%H%M%S",
                              _time.localtime(prev.stat().st_mtime))
        hist = out_dir / "report_history"
        hist.mkdir(exist_ok=True)
        for name in ("report.md", "report.html", "brief.md"):
            src = out_dir / name
            if src.exists():
                stem = "report" if name.startswith("report") else name.split(".")[0]
                suffix = name.split(".", 1)[1] if "." in name else "md"
                dst = hist / f"{stem}_{stamp}.{suffix}"
                # 同一秒内多次结算(复查链快续跑/测试连发 finalize)会取到同一
                # 锚秒→归档名撞车;Windows rename 不覆盖,直 rename 会崩——
                # 撞名时加序号后缀保住两版(语义不变,不丢档)
                n = 1
                while dst.exists():
                    dst = hist / f"{stem}_{stamp}_{n}.{suffix}"
                    n += 1
                src.rename(dst)

    def _claim_payload(self, claims, event_id: str) -> list[dict]:
        """S7 起草/重写输入行(P 波改造,2026-11-08):M1 挡除/转写滤+P2
        附 gist/plain(claim_input_row 公共函数=与轮间钩子同源,输入侧口径
        不分叉)。快照/节归属语义不变(滤只动 LLM 输入,不动台账)。"""
        rules = self._report_phrases()
        gists = self._ledger.claim_gists(event_id)
        out = []
        for c in claims:
            if _claim_tier(c["text"], rules):
                continue   # M1 同滤(finalize 环① 外层滤后的幂等二道;补节
            rows = self._ledger.evidence_for_claim(c["claim_id"])  # 外无滤的
            strong = [e for e in rows                      # edit 路径在此收口)
                      if e["ruling"] in ("支持", "矛盾", "时效刷新")]
            weak = [e for e in rows
                    if e["ruling"] is None or e["ruling"] == "无关"]
            refs = [e["evidence_id"].rsplit(":", 1)[-1]
                    for e in (strong + weak)[: self._config.evidence_topk]]
            out.append(claim_input_row(c, refs, rules, gists))
        return out

    def _verdict_notes(self, event_id: str) -> dict[str, dict]:
        """从 decision_log 提取每条 claim 的最终判决(核对证书的规则命中栏)。"""
        notes: dict[str, dict] = {}
        for r in self._ledger.decision_log_rows(event_id):
            if r["kind"] != "判决":
                continue
            p = json.loads(r["payload_json"])
            notes[p["claim_id"].rsplit(":", 1)[-1]] = p
        return notes

    def _report_phrases(self) -> dict:
        """报告文风短语表(报告文风线第一批):domain 缺席(旧直调测试经
        ReportBuilder.__new__ 只挂 _ledger)→ {}=消费端兜底短语,失败不
        伪装、不炸渲染。"""
        dom = getattr(self, "_domain", None)
        if dom is None:
            return {}
        return _load_report_phrases(getattr(dom, "config_dir", ""))

    def _chain_graph(self, event_id: str) -> dict | None:
        """证据链 graph(第二批 M4/M5/M6 数据源):chain_graph 纯台账确定性
        投影(零 LLM 零网络),按 event 现算一次缓存(渲染期结论卡/附录 E/
        OQ 融合行同源消费,md/html 逐字节一致)。kb 旁车按 K5 同款门控
        (--kb on ∧ kb.db 存在)只读打开,缺省=无旁车(融合/极性零痕迹,
        照 K5 门控先例);构建异常→None(结论卡/附录 E 降级,失败不伪装)。
        config/domain 缺席(旧直调 ReportBuilder.__new__)=getattr 防御。"""
        cached = getattr(self, "_chain_graph_cache", None)
        if cached is not None and cached[0] == event_id:
            return cached[1]
        kb = None
        cfg = getattr(self, "_config", None)
        kb_path = getattr(cfg, "kb_path", "") or ""
        if getattr(cfg, "kb_enabled", False) and kb_path and \
                Path(kb_path).exists():
            try:
                kb = open_kb_ro(kb_path)
            except (OSError, ValueError):
                kb = None
        dom = getattr(self, "_domain", None)
        cfg_dir = str(getattr(dom, "config_dir", "") or "data/config")
        ess_rules = None
        try:
            ess_rules = load_rules(Path(cfg_dir) / "ess_rules.json")
        except EssRulesError:
            ess_rules = None        # 规则表坏=链卡无锚点名(降级照旧)
        try:
            graph = build_chain_graph(
                {event_id: self._ledger}, kb, event_id,
                ess_rules=ess_rules,
                auth_roots=getattr(dom, "authoritative_roots", None) or None,
                near_dup_dist=(SIMHASH_NEAR_DUP_DIST
                               if getattr(cfg, "t1_independence_enabled",
                                          False) else None),
                config_dir=cfg_dir,
                seeds_path=(str(Path(cfg_dir) / "hypothesis_seeds.json")
                            if (Path(cfg_dir) / "hypothesis_seeds.json")
                            .exists() else None))
        except Exception:
            graph = None
        finally:
            if kb is not None:
                kb.close()
        self._chain_graph_cache = (event_id, graph)
        return graph

    # ------------------------------------------------- 结论三段卡(M3+M4)

    def _card_phrase(self, did: str, by_cid: dict, gists: dict | None,
                     rules: dict) -> str:
        """卡面短语(M4 剥壳纪律,拍板 2):gist 短名优先;gist 缺失=壳前缀
        表剥壳后文本(strip_shell 链侧同表同源+报告侧同判据);剥壳后仍带
        机器腔(解读/元信息类前缀命中)→空串=整行省略——禁 claim id/假设
        编号/裸 claim 原文(负测试锁)。链侧仅已证实剥壳(三态纪律),报告
        卡面各行自带「未查实」标注、剥壳不冒充事实(第二批拍板,实录备考)。"""
        c = by_cid.get(did) or {}
        p = (gists or {}).get(c.get("draft_id")) or ""
        if not p:
            stripped = strip_shell(c.get("text") or "", rules)
            if stripped and classify_claim(stripped, rules) is None:
                p = stripped
        return p.strip()

    def _verdict_narr(self, event_id: str, s, gists: dict | None) -> str:
        """结论串讲提取+研判说明验票(I2-4 提取接线,2026-11-09):串讲段
        (含研判说明,同一通道=_narr_extract 四道守门)→ 机器验票(verdict_
        gates_apply 五闸+段落形态;研判说明句随串讲段同验)——过=上卡上方,
        败=整段回落零占位(失败不伪装,机械卡+洞察两段照旧)。旧台账(无
        串讲段)=零占位零变化;素材缺(无主导假设,verdict_brief 空)=不验
        照提(P 波串讲形态保守不破坏);验票素材面=LLM 输入侧同源(brief+
        本节 claim 行的 gist/plain/refs;滤=与起草输入同判据 M1)。"""
        rules = self._report_phrases()
        narr = _narr_extract(s["draft_text"] or "", rules)
        if not narr:
            return ""
        brief = verdict_brief(self._ledger, event_id, rules, gists)
        if not brief:
            return narr
        mat = verdict_gate_material(self._ledger, event_id, s["section_id"],
                                    rules, gists, brief)
        body, _reasons = verdict_gates_apply(narr, **mat)
        return body

    def _render_conclusion(self, event_id: str,
                           gists: dict | None = None,
                           narr: str = "") -> str:
        """结论三段卡(M4 主菜,F7;四问=凭什么/比过谁/差什么/何时变):
        渲染期整节替换(照第一批 _display_sections OQ 节先例,台账 sections
        照存原文=双回放对照不受影响)。数据全部现成量:argument/runners_up
        (pick_argument 判据,消费链图同源结果)/hc_map 记分成员/verdicts
        根数/gist/壳表;卡面剥壳纪律+禁 id 禁 (hN) 禁裸原文;根数人话分档
        与 flip 模板走模式表(report_root_phrases/report_flip_phrases,机器
        推导表述禁上卡照批1.5 撤图先例);矛盾双口径扩行(三态纪律);转写
        类合并注入局限说明句(M3,零 LLM);无支撑/无图=降级一行(失败不
        伪装);串讲段=P 波起 live 产物(M4 卡骨架上方,提取判据=_narr_extract
        三道守门;旧台账无串讲=空=纯卡零占位,失败不伪装)。"""
        tbl = self._report_phrases()
        rules = tbl
        graph = self._chain_graph(event_id)
        lines = ["### 结论", ""]
        if narr:
            lines += [narr, ""]          # P4:串讲段挂机械卡上方
        if graph is None:
            lines.append("- (证据链暂不可用,研判以附录 D 假设记分板为准)")
            return "\n".join(lines)
        arg = graph.get("argument")
        ov = (graph.get("overview") or {}).get("hypotheses") or []
        standings = [s for s in self._ledger.hypothesis_standings(event_id)
                     if s.get("emerged")]
        doms = [s for s in standings if s.get("dominant")]
        by_cid = {c["claim_id"]: dict(c)
                  for c in self._ledger.all_claims(event_id)}
        verdicts = self._verdict_notes(event_id)
        auth = set(getattr(getattr(self, "_domain", None),
                           "authoritative_roots", None) or ())
        hc_map: dict[str, list[str]] = {}
        for r in self._ledger.conn.execute(
                "SELECT hc.hypothesis_id, hc.claim_id FROM hypothesis_claims hc"
                " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
                " WHERE h.event_id=? ORDER BY hc.hypothesis_id, hc.claim_id",
                (event_id,)):
            hc_map.setdefault(r["hypothesis_id"], []).append(r["claim_id"])

        # 报告与 chain_web 同源：都先生成结构化结论视图，再各自
        # 渲染 Markdown/抽屉。这里不读 report.md，也不重算假设胜负。
        standing_by_id = {s["hypothesis_id"]: s for s in standings}
        hyp_inputs = []
        for h in ov:
            hid = h["hypothesis_id"]
            standing = standing_by_id.get(hid) or {}
            hyp_inputs.append({
                "hypothesis_id": hid, "label": h.get("label", hid),
                "description": standing.get("description", ""),
                "state": h.get("state", ""), "score": h.get("score", 0),
                "chosen": bool(arg and hid == arg.get("hypothesis_id")),
                "member_ids": hc_map.get(hid, []),
            })
        excluded = {cid for cid, c in by_cid.items()
                    if _claim_tier(c.get("text") or "", rules)}
        gmeta = graph.get("meta") or {}
        snapshot = (f"{gmeta.get('as_of') or 'unknown'}|"
                    f"round:{gmeta.get('as_of_round') if gmeta.get('as_of_round') is not None else 'latest'}")
        conclusion_views = build_conclusion_views(
            graph, list(by_cid.values()), hyp_inputs,
            excluded_claim_ids=excluded,
            source_section_id=f"{event_id}:S3",
            snapshot_version=snapshot)
        present_by_hid = {v["hypothesis_id"]: v
                          for v in conclusion_views}

        def facts_of(hid: str) -> list[str]:
            """假设成员中可上卡的事实成员(M1 同判据:挡除/转写类不上卡)。"""
            present = present_by_hid.get(hid)
            if present:
                return list(dict.fromkeys([
                    *present["supporting_claim_ids"],
                    *present["pending_claim_ids"],
                    *present["contradicted_claim_ids"],
                ]))
            out = []
            for cid in hc_map.get(hid, []):
                c = by_cid.get(cid)
                if c is not None and not _claim_tier(c["text"], rules):
                    out.append(cid)
            return out

        def phrase_of(cid: str) -> str:
            return self._card_phrase(cid, by_cid, gists, rules)

        def roots_stats(ver_cids: list[str]) -> list[tuple[bool, int]]:
            stats = []
            for cid in ver_cids:
                sup = (verdicts.get(by_cid[cid]["draft_id"]) or {}).get(
                    "support_roots") or []
                stats.append((bool(set(sup) & auth), len(set(sup))))
            return stats

        root_phr = tbl.get("report_root_phrases") or {}

        def root_line(ver_cids: list[str]) -> str:
            """根数人话(状态分档,禁「独立根≥2」判据名——多根/两根/单源/
            权威载明/混合五档,模式表承载改表即生效)。"""
            stats = roots_stats(ver_cids)
            if not stats:
                return ""
            if all(a for a, _ in stats):
                return root_phr.get("auth", "权威公示渠道已直接载明")
            if any(a for a, _ in stats):
                return root_phr.get(
                    "auth_mixed", "权威公示渠道已直接载明,其余各条亦获"
                                  "独立来源相互印证")
            mn = min(n for _, n in stats)
            if mn >= 3:
                return root_phr.get("multi", "均由多处相互独立的公开来源"
                                             "分别印证")
            if mn == 2:
                return root_phr.get("two", "由两处相互独立的公开来源分别"
                                           "印证")
            return root_phr.get("single", "目前仅由单一公开来源支撑,"
                                          "补证后更稳固")

        flip_phr = tbl.get("report_flip_phrases") or {}

        def flip_line(pend_cids: list[str], ver_cids: list[str]) -> str:
            """何时变(flip 人话模板,机器推导表述「唯一根失效→回待证实」
            禁上卡,flip JSON 全量不动=对账与 D 线底座)。三态:缺口为否/
            权威载明被更正撤销/支撑材料失实——按 verdict 根数+权威命中
            分档触发,全无则整行省略。"""
            bits = []
            stats = roots_stats(ver_cids)
            if pend_cids:
                bits.append(flip_phr.get("gap", "上述任一项查证为否,"
                                               "本研判不成立"))
            if any(a and n < 3 for a, n in stats):
                bits.append(flip_phr.get(
                    "evidence_auth", "权威渠道的记载若被更正或撤销,"
                                     "相关结论将随之回落"))
            if any((not a) and n < 3 for a, n in stats):
                bits.append(flip_phr.get("evidence", "支撑材料若被证实失实,"
                                                     "相关结论将回落为待查证"))
            return f"- 何时变:{';'.join(bits)}。" if bits else ""

        if arg and len(doms) <= 1:
            # 单一领先(或证据倾向尚未主导):四问全卡
            label = arg["label"]
            facts = facts_of(arg["hypothesis_id"])
            ver = [cid for cid in facts if by_cid[cid]["status"] == "已证实"]
            pend = [cid for cid in facts if by_cid[cid]["status"] == "待证实"]
            contra = [cid for cid in facts if by_cid[cid]["status"] == "矛盾"]
            qual = ("领先" if doms else "证据倾向,尚未构成主导")
            tail = (f",无反向证据" if not contra
                    else f",含反向材料 {len(contra)} 项")
            lines.append(f"**当前研判:{label}**({qual};{len(ver)} 项事实"
                         f"支撑{tail})")
            lines.append("")
            present = present_by_hid.get(arg["hypothesis_id"])
            if present and present.get("current_judgment"):
                lines.append(f"- 当前判断:{present['current_judgment']}")
                lines.append("")
            items = [p for p in (phrase_of(cid) for cid in ver) if p]
            if items:
                lines.append(f"- 支撑结构({len(items)} 项已查证):")
                for p in items[:3]:
                    lines.append(f"  - {p} 〔已查证〕")
                if len(items) > 3:
                    lines.append(f"  - (其余 {len(items) - 3} 项等,见附录 B)")
            rl = root_line(ver)
            if rl:
                lines.append(f"  ── {rl} ──")
            lines.append("")
            runners = [h for h in ov if h.get("state") == "runner_up"]
            if runners:
                lines.append("**为何不是其他假设**:")
                lines.append("")
                top_set = set(facts)
                for h in runners:
                    mem = facts_of(h["hypothesis_id"])
                    hver = [cid for cid in mem
                            if by_cid[cid]["status"] == "已证实"]
                    hpend = [cid for cid in mem
                             if by_cid[cid]["status"] == "待证实"]
                    if mem and set(mem) <= top_set:
                        reason = ("与当前研判共享同一批事实,现有证据尚不能"
                                  "区分二者")
                    elif hpend:
                        names = [x for x in (phrase_of(p)
                                             for p in hpend[:2]) if x]
                        quant = (f"等 {len(hpend)} 项"
                                 if len(hpend) > len(names) or not names
                                 else f"{len(hpend)} 项")
                        key = ("、".join(f"「{x}」" for x in names)
                               if names else "关键事实")
                        reason = (f"已查证 {len(hver)} 项,但{key}{quant}"
                                  "关键事实未查实,暂不能排除")
                    elif hver:
                        reason = (f"已查证 {len(hver)} 项,少于当前研判的 "
                                  f"{len(ver)} 项")
                    else:
                        reason = "暂无已查证事实支撑"
                    lines.append(f"- 「{h['label']}」—— {reason}")
                lines.append("")
            if pend:
                lines.append(f"**本研判还差 {len(pend)} 步才成立**"
                             "(缺什么卡什么):")
                lines.append("")
                for i, cid in enumerate(pend[:3], 1):
                    lines.append(f"{i}. {phrase_of(cid) or '关键事实待补证'}"
                                 " —— 未查实")
                if len(pend) > 3:
                    lines.append(f"(其余 {len(pend) - 3} 项见「待查证」清单)")
                lines.append("")
            fl = flip_line(pend, ver)
            if fl:
                lines += [fl, ""]
        elif doms:
            # 并列领先:证据尚不能区分——逐条徽章(第三批复查,用户反馈
            # 「已查证+缺口挤一行缺乏逻辑」):每假设支撑结构逐条〔已查证〕
            # +尚差逐条〔待查证〕,已查证/未查实分开,与单一领先同风格;
            # 诚实呈现不偏袒任一
            labels = "、".join(s["label"] for s in doms)
            lines.append(f"**当前研判:{labels}(并列领先,证据尚不能区分)**")
            lines.append("")
            all_ver: list[str] = []
            all_pend: list[str] = []
            for s in doms:
                facts = facts_of(s["hypothesis_id"])
                ver = [cid for cid in facts
                       if by_cid[cid]["status"] == "已证实"]
                pend = [cid for cid in facts
                        if by_cid[cid]["status"] == "待证实"]
                all_ver += ver
                all_pend += pend
                lines.append(f"「{s['label']}」")
                vph = [p for p in (phrase_of(cid) for cid in ver) if p]
                lines.append(f"- 支撑结构({len(ver)} 项已查证):")
                for p in vph[:3]:
                    lines.append(f"  - {p} 〔已查证〕")
                if len(vph) > 3:
                    lines.append(f"  - (其余 {len(vph) - 3} 项等,见附录 B)")
                pph = [p for p in (phrase_of(cid) for cid in pend) if p]
                if pph:
                    lines.append(f"- 尚差({len(pend)} 项未查实):")
                    for p in pph[:3]:
                        lines.append(f"  - {p} 〔待查证〕")
                    if len(pph) > 3:
                        lines.append(f"  - (其余 {len(pph) - 3} 项等,见附录 B)")
                lines.append("")
            rl = root_line(list(dict.fromkeys(all_ver)))
            if rl:
                lines += [f"  ── {rl} ──", ""]
            fl = flip_line(list(dict.fromkeys(all_pend)),
                           list(dict.fromkeys(all_ver)))
            if fl:
                lines += [fl, ""]
        else:
            # 无论证/无主导:降级一行(失败不伪装)
            lines.append("- (尚无足够已查证事实支撑研判;"
                         "未决事项见「待查证」清单)")
            return "\n".join(lines)
        # 矛盾双口径扩行(三态纪律:两类记载并存如实,不压成已证实)
        contra_lines = []
        for cid, c in sorted(by_cid.items()):
            if c["status"] == "矛盾" and \
                    not _claim_tier(c["text"], rules) and \
                    phrase_of(cid):
                contra_lines.append(phrase_of(cid))
        if contra_lines:
            lines.append(f"- **〔注意〕存在相反记载**:"
                         f"{';'.join(contra_lines[:3])}"
                         f"{'等' if len(contra_lines) > 3 else ''}"
                         "——两类材料并存,以附录 B 核对证书为准")
            lines.append("")
        # 局限说明(M3):转写类合并模板句,零 LLM;无此类 claim 零痕迹
        clauses: list[str] = []
        for c in self._ledger.all_claims(event_id):
            if _claim_tier(c["text"], rules) == "rewrite":
                cl = _rewrite_clause(c["text"], rules)
                if cl and cl not in clauses:
                    clauses.append(cl)
        if clauses:
            lines.append(f"- 局限说明:{','.join(clauses)}。")
        # 两段式洞察(结论启发式 I1,2026-11-08,方案《报告结论启发式呈现-
        # 设计方案-20261108》拍板1/3/4/5/9):「我们的判断」(T6 观点演化,
        # 解读定性经代码翻译通道——挡除≠观点不可见,挡除=机器话不可见)+
        # 「这意味着什么」(T1 基线+四拍=窗口→有利→不利→待决,模板句+固定
        # 连接词,零 LLM 零录制件,确定性逐字节;时间基准=台账结算锚非 now
        # =重渲染稳定)。挂机械卡后(拍板1:串讲讲研判、四问卡讲可信度、
        # 洞察讲含义,卡是分界);某拍无素材=该拍跳过,全空=整段零痕迹
        # (拍板5 失败不伪装);neutral 保守永不出逆转句(拍板9)。
        insight = self._insight_lines(event_id, arg, by_cid, gists, rules,
                                      verdicts, hc_map)
        if insight:
            lines += [insight, ""]
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------- I1 洞察两段

    def _insight_lines(self, event_id: str, arg: dict | None, by_cid: dict,
                       gists: dict | None, rules: dict, verdicts: dict,
                       hc_map: dict) -> str:
        """「我们的判断」+「这意味着什么」(I1 机械半,零 LLM 模板句直出)。

        T6(我们的判断):解读定性(_interpret_stance 纯函数)× 领先假设
        方向(_hyp_family)→ 三态;反向材料布尔=领先假设名下矛盾态成员∨
        已证实成员 oppose_roots 非空(现成量);打折句反证指向=矛盾成员
        卡面短语优先,缺=登记号定位符兜底(拍板8;矛盾成员也无=通用表述
        不点名,失败不伪装)。
        四拍(这意味着什么):T1 基线(信封 amount_history 检测器标量,
        null=零痕迹照忻州实账)→ 窗口(已证实成员带截止日期,基准=结算锚
        [last_finalize.json>transitions 末条 ts],事件名=report_timeline_
        events 表,gist 兜底,无=拍跳过)→ 有利(T2 派生链子型[父待证实∧
        子已证实]优先,否则已证实成员≤2 组合)→ 不利(矛盾成员卡面短语)
        → 待决(pending 首项+行动含义)。固定连接词组装;无素材拍跳过;
        全空=整段零痕迹。"""
        if arg is None:
            return ""
        tbl = rules.get("report_stance_words") or {}
        member_ids = hc_map.get(arg["hypothesis_id"], [])
        facts = [cid for cid in member_ids if cid in by_cid
                 and not _claim_tier(by_cid[cid]["text"], rules)]
        ver = [cid for cid in facts if by_cid[cid]["status"] == "已证实"]
        pend = [cid for cid in facts if by_cid[cid]["status"] == "待证实"]
        contra = [cid for cid in facts if by_cid[cid]["status"] == "矛盾"]

        def phrase_of(cid: str) -> str:
            return self._card_phrase(cid, by_cid, gists, rules)

        def clean(s: str) -> str:
            """拍内短语去尾句号(卡面短语自带句读,拼接前剥尾防「。是窗口…」
            破句——忻州 r1 目检实抓)。"""
            return (s or "").rstrip("。;;!?!?　 ")

        # 矛盾态 claim(全事件,M1 同滤)——T6 打折句的反证指向(拍板8):
        # 反向材料的判据=事件级矛盾态存在(含竞争假设名下的矛盾成员——
        # 对领先假设而言正是反向信号;忻州 r2 实账 c12[排斥条款]挂 h2,
        # 方案 §附录示意打折句指向它);领先假设名下子集另供不利拍。
        contra_all = [cid for cid, c in sorted(by_cid.items())
                      if c["status"] == "矛盾"
                      and not _claim_tier(c["text"], rules)
                      and phrase_of(cid)]

        # ---- T6 我们的判断
        seg1 = ""
        stance = _interpret_stance(list(self._ledger.all_claims(event_id)),
                                   rules)
        if stance is not None:
            hfam = _hyp_family(arg.get("label", ""), tbl)
            fam = stance["family"]
            # 一致判定(保守):假设族 neutral/定性族 neutral(未命中)→一致;
            # skeptic=市场误读类,方向取决于查证结果而非定性词面,保守视同
            # neutral(拍板9 宁漏翻不误翻;实录备考)。其余异族=逆转。
            agreed = (hfam == "neutral" or fam in ("", "neutral", "skeptic")
                      or fam == hfam)
            has_back = bool(contra_all) or any(
                (verdicts.get(by_cid[c]["draft_id"]) or {}).get("oppose_roots")
                for c in ver)
            reason = f",依据是{stance['reason']}" if stance["reason"] else ""
            head = f"调查初期将其列为{stance['phrase']}{reason}"
            if not agreed:
                seg1 = (f"{head};查证后证据更支持「{arg.get('label', '')}」,"
                        "前期判断需要修正。")
            elif has_back:
                back = ""
                if contra_all:
                    back = clean(phrase_of(contra_all[0]))
                    if not back:
                        back = (f"登记号 "
                                f"{by_cid[contra_all[0]]['draft_id']} 的反向材料")
                seg1 = (f"{head};查证后方向站住,"
                        + (f"但{back}是当前最大变数,推进前需先核实。"
                           if back else
                           "但存在反向材料,推进前需先核实。"))
            else:
                seg1 = f"{head};查证后该判断方向站住。"
            t6_pointed = contra_all[0] if (has_back and contra_all) else ""
        else:
            t6_pointed = ""

        # ---- 这意味着什么(四拍;元组=(句,是否不利拍)——不利拍组装时
        # 前加「但」,固定连接词不靠词面判断)
        beats: list[tuple[str, bool]] = []

        def _short(did: str, text: str) -> str:
            g = (gists or {}).get(did) or ""
            return g or _polish_section_text(text, rules)

        # T1 基线对照(检测器标量→人话;信封字段 null=零痕迹,忻州实账)
        env = self._envelope(event_id)
        ah = getattr(getattr(env, "tender", None), "amount_history", None) \
            if env is not None else None
        ratio = getattr(ah, "amount_ratio", None) if ah is not None else None
        if ratio and ratio > 0:
            t1 = (f"本次项目金额约为该采购人近期同类项目中位数的 "
                  f"{ratio:g} 倍")
            hc = getattr(ah, "history_count", None)
            if hc:
                t1 += f"(对照 {hc} 条历史记录)"
            beats.append((t1, False))

        # 窗口拍(T2 时效子型):已证实成员带截止/届满/到期+日期
        anchor = self._settle_anchor(event_id)
        if anchor:
            for cid in ver:
                p = phrase_of(cid)
                if not p or not any(k in p for k in ("截止", "届满", "到期")):
                    continue
                dm = (self._DATE_CN_RE.search(p)
                      or self._DATE_ISO_TM_RE.search(p))
                if not dm:
                    continue
                # 日期归一(分形态:中文形态去空白归一为 ISO;ISO 形态保
                # 空格——直接去空白会把「2026-11-13 09:30」粘成「11-1309:30」,
                # 两形态忻州实账均在)
                raw = re.sub(r"\s+", " ", dm.group(0)).strip()
                if "年" in raw:
                    due = (re.sub(r"\s+", "", raw)
                           .replace("年", "-").replace("月", "-")
                           .replace("日", " ").strip())
                else:
                    due = raw
                try:
                    from datetime import date as _d
                    due_d = _d.fromisoformat(due[:10])
                    anc_d = _d.fromisoformat(anchor[:10])
                except ValueError:
                    continue
                c = by_cid[cid]
                ev = (_timeline_event_name(p, rules)
                      or (gists or {}).get(c["draft_id"]) or "")
                if not ev:
                    continue
                days = (due_d - anc_d).days
                due_show = due[5:16].strip()      # MM-DD [HH:MM]
                if days > 0:
                    beats.append((f"距{ev}({due_show})仅剩 {days} 天", False))
                elif days == 0:
                    beats.append((f"{ev}就在今天({due_show})", False))
                break                              # 取首个窗口事实
        # 有利拍(T2 派生链子型优先,否则已证实成员组合≤2)
        adv = ""
        for cid in ver:
            pid = by_cid[cid].get("parent_claim_id") or ""
            p = by_cid.get(pid) if pid else None
            if p is not None and p["status"] == "待证实":
                pdid = pid.rsplit(":", 1)[-1]
                ptxt = _short(pdid, p["text"])
                if ptxt:
                    adv = (f"「{clean(ptxt)}」整体尚未查实,但其下"
                           f"「{clean(phrase_of(cid))}」已获查证"
                           "——方向已有实质支撑")
                    break
        if not adv and ver:
            names = [clean(x) for x in (phrase_of(c) for c in ver[:2]) if x]
            names = [x for x in names if x]
            if names:
                adv = "、".join(f"「{x}」" for x in names) + "均已查证"
        if adv:
            beats.append((adv, False))
        # 不利拍(矛盾成员卡面短语;无矛盾成员=拍跳过,失败不伪装;与 T6
        # 打折句同指一条时跳过=不重复点名)
        if contra and contra[0] != t6_pointed:
            cph = clean(phrase_of(contra[0]))
            if cph:
                beats.append((f"{cph}是当前最大变数", True))
        # 待决拍(T3 行动含义:pending 首项)
        if pend:
            p0 = clean(phrase_of(pend[0]))
            beats.append((f"若决定推进,{p0 or '关键事实'}"
                          "是窗口内要闭合的第一件事", False))

        segs = []
        if seg1:
            segs += ["**我们的判断**", "", seg1]
        if beats:
            body = beats[0][0]
            for text, adverse in beats[1:]:
                body += (";但" if adverse else ";") + text
            segs += ["", "**这意味着什么**", "", body + "。"]
        return "\n".join(segs)

    def _settle_anchor(self, event_id: str) -> str:
        """结算锚(I1 时效句时间基准,§十相容性:非 now()=重渲染逐字节稳定):
        last_finalize.json 优先(finalize 落盘);缺失=台账 transitions 末条
        ts(event 级兜底,重渲染同样稳定);再无=空串(窗口拍跳过)。"""
        try:
            a = read_anchor(Path(self._runs) / event_id)
        except (OSError, ValueError):
            a = None
        if a:
            return str(a)
        try:
            row = self._ledger.conn.execute(
                "SELECT ts FROM claim_transitions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return ""
        return (row["ts"] if row else "") or ""

    # ------------------------------------------------------------- S1 时间轴

    _DATE_CN_RE = re.compile(
        r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*\d{1,2}:\d{2})?")
    _DATE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
    _DATE_ISO_TM_RE = re.compile(          # I1 窗口拍:带时间形态优先
        r"\d{4}-\d{2}-\d{2}(?:\s*\d{1,2}:\d{2})?")
    _AGENCY_RE = re.compile(r"代理机构为([^\s，。;、]{2,40})")

    def _envelope(self, event_id: str) -> InvestigationEnvelope | None:
        """从台账读回调查信封(M2 时间轴/关联方卡数据源,零侵入只读;register_event
        已存 envelope_json)。缺失/坏 JSON=None(时间轴降级一行,失败不伪装)。"""
        raw = self._ledger.event_envelope_json(event_id)
        if not raw:
            return None
        try:
            return InvestigationEnvelope.model_validate(json.loads(raw))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _norm_date(d: str) -> str:
        """日期归一(第三批复查:r3 实账为空格形态「2026 年 11 月 13 日
        09:30」,r1 为紧凑形态;统一为「YYYY-MM-DD HH:MM」)。"""
        t = re.sub(r"\s+", "", d or "")
        return t.replace("年", "-").replace("月", "-").replace("日", " ").strip()

    def _render_timeline(self, event_id: str) -> str:
        """S1 节渲染期替换(M2,F3):纵向时间轴 + 关联方关系卡,零 LLM 纯投影。
        时间轴事件=envelope 结构化日期(published_date/signal_time)+已证实 claim
        带日期关键事件(正则提取,事件=gist 短名优先/剥壳截断);表述用户话
        ("招标公告发布"而非"信封载明发布日期")。关联方=采购人/监测对象(信封
        purchaser/subject_name)+代理机构(claim 文本正则"代理机构为X")。无日期
        事件=降级一行(纪律7);台账 sections 照存原文=双回放安全。"""
        lines = ["### 动态时间线与关联方", ""]
        env = self._envelope(event_id)
        rules = self._report_phrases()
        gists = self._ledger.claim_gists(event_id)
        events: list[tuple[str, str]] = []
        parties: list[tuple[str, str]] = []
        if env is not None:
            if env.kind == "tender" and env.tender:
                t = env.tender
                if t.published_date:
                    events.append((self._norm_date(t.published_date),
                                   "招标公告发布"))
                if t.purchaser:
                    parties.append(("采购人", t.purchaser))
            elif env.rumor:
                r = env.rumor
                if r.signal_time:
                    events.append((self._norm_date(r.signal_time), "信号识别"))
                if r.subject_name:
                    parties.append(("监测对象", r.subject_name))
        for c in self._ledger.all_claims(event_id):
            if c["status"] != "已证实" or _claim_tier(c["text"], rules):
                continue
            gist = (gists.get(c["draft_id"]) or "").strip()
            label = gist or strip_shell(c["text"] or "", rules)
            if not label or classify_claim(label, rules) is not None:
                continue
            if any(w in label for w in ("发布", "公告")):   # 信封公告发布已覆盖
                continue
            ev_name = _timeline_event_name(label, rules)
            label = ev_name or label      # 事件名优先;无则全文不截断(复查拍板 A)
            for d in self._DATE_CN_RE.findall(c["text"] or ""):
                events.append((self._norm_date(d), label))
            for d in self._DATE_ISO_RE.findall(c["text"] or ""):
                events.append((d, label))
        for c in self._ledger.all_claims(event_id):
            m = self._AGENCY_RE.search(c["text"] or "")
            if m and not any(p[0] == "代理机构" for p in parties):
                parties.append(("代理机构", m.group(1)))
                break
        lines.append("**时间轴**:")
        if events:
            seen: set[tuple[str, str]] = set()
            for d, label in sorted(events, key=lambda x: x[0]):
                if (d, label) in seen:
                    continue
                seen.add((d, label))
                lines.append(f"- {d}:{label}")
        else:
            lines.append("- (无明确日期事件)")
        lines.append("")
        lines.append("**关联方**:")
        if parties:
            lines.append("| 角色 | 名称 |")
            lines.append("|---|---|")
            for role, name in parties:
                lines.append(f"| {role} | {name} |")
        else:
            lines.append("- (无关联方信息)")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------- 企业简报(形态 A)
    # 全部模板确定性生成(零 LLM、零台账写入):卡片/红旗/研判倾向都是从台账
    # 与信封"读出来"的交付层视图;六节 canonical 叙事在 report.md 原样保留。

    def _dominant_line(self, standings: list[dict]) -> str:
        doms = [s for s in standings if s.get("dominant")]
        if not doms:
            return "(无主导假设)"
        labels = "、".join(s["label"] for s in doms)
        score = doms[0]["score"]
        return (f"{labels}(并列,得分 {score})" if len(doms) > 1
                else f"{labels}(得分 {score})")

    def _dominant_line_user(self, standings: list[dict]) -> str:
        """report.md 头部领先假设行(报告文风线 P5):用户话——"交付时快照"
        (调查过程词)与"得分 N"(记分机制内部量)撤出人看层,记分明细在
        附录 D;brief 侧继续用 _dominant_line(本批 brief 零改动)。"""
        doms = [s for s in standings if s.get("dominant")]
        if not doms:
            return "(暂无领先假设)"
        labels = "、".join(s["label"] for s in doms)
        return f"{labels}(并列)" if len(doms) > 1 else labels

    def _split_recommendations(self, rec_items):
        """确定性受众分流:文案命中内部标记词的归「内部待办」,不进企业简报。"""
        internal, external = [], []
        for r in rec_items or []:
            blob = f"{r.action} {r.rationale}"
            (internal if any(m in blob for m in _INTERNAL_MARKERS)
             else external).append(r)
        return external, internal

    def _refs_of(self, claim_id: str, k: int = 1) -> str:
        rows = self._ledger.evidence_for_claim(claim_id)
        return "、".join(
            f"[{r['evidence_id'].rsplit(':', 1)[-1]}]" for r in rows[:k])

    def _field_status(self, match: str, claims):
        """卡片字段状态(确定性):字段值出现在已证实 claim 文本 -> 已证实(带
        该 claim 首个引用);仅出现在待证实/矛盾 claim -> 同状态;完全未提及 ->
        信封载明(锚 [E1] 基础字段种子)。"""
        if not match:
            return "信封未提供", ""
        for want in ("已证实", "待证实", "矛盾"):
            for c in claims:
                if c["status"] == want and match in (c["text"] or ""):
                    refs = self._refs_of(c["claim_id"])
                    return (want if want != "矛盾" else "矛盾"), refs
        return "信封载明", "[E1]"

    @staticmethod
    def _clip(text: str, n: int = 50) -> str:
        text = text or ""
        return text if len(text) <= n else text[:n] + "…"

    def _tender_card_rows(self, t) -> list[tuple[str, str, str]]:
        """(字段, 显示值, 状态匹配值)三元组;匹配值=信封原值,供 _field_status
        在 claim 文本中定位。缺失字段(截止/获取文件/代理)如实标"信封未提供"。"""
        if t.amount_yuan is None:
            amount_disp, amount_match = "—", ""
        elif t.amount_yuan >= 10000:
            amount_disp, amount_match = f"{t.amount_yuan / 10000:g} 万元", str(t.amount_yuan)
        else:
            amount_disp, amount_match = f"{t.amount_yuan:g} 元", str(t.amount_yuan)
        return [
            ("采购人", t.purchaser or "—", t.purchaser),
            ("预算金额", amount_disp, amount_match),
            ("项目编号", t.project_id or "—", t.project_id),
            ("公告发布日", t.published_date or "—", t.published_date),
            ("采购内容", self._clip(t.project_summary) or "—", t.project_summary or ""),
            ("公告原文链接", t.source_url or "—", t.source_url or ""),
            ("投标截止时间", "—", ""),
            ("获取招标文件方式", "—", ""),
            ("代理机构 / 联系人", "—", ""),
        ]

    def _rumor_card_rows(self, r, evidence) -> list[tuple[str, str, str]]:
        """rumor 无商机卡片,给主体卡片;线索行附轮 0 种子的谱系根折算读数。"""
        seeds = [ev for ev in evidence if ev["round"] == 0 and ev["lineage_root"]]
        roots = sorted({ev["lineage_root"] for ev in seeds})
        clue = (f"{len(r.clues)} 条线索;谱系根 {len(roots)} 个"
                f"({'、'.join(roots)};同源折算后)" if roots else "无线索卡")
        return [
            ("监测主体", r.subject_name or "—", r.subject_name),
            ("统一信用代码", r.subject_credit_code or "—", r.subject_credit_code),
            ("事件摘要", self._clip(r.summary, 60) or "—", r.summary),
            ("信号识别时间", r.signal_time or "—", r.signal_time),
            ("监测重点", r.watch_focus or "—", r.watch_focus),
            ("线索", clue, ""),
        ]

    def _render_card(self, rows, claims) -> list[str]:
        lines = ["| 字段 | 值 | 状态 |", "|---|---|---|"]
        for label, disp, match in rows:
            status, refs = self._field_status(match, claims)
            cell = " ".join(x for x in (status, refs) if x)
            lines.append(f"| {label} | {disp or '—'} | {cell} |")
        return lines

    def _gist(self, summary: dict, envelope) -> str:
        """一句话研判(确定性规则,非 LLM):定性 + 画像相关分 + 研判倾向。
        倾向规则:有矛盾 -> 复核;多假设并列主导 -> 观察跟踪;单一主导 -> 可推进;
        无主导 -> 人工复核。待证实计数如实附注。"""
        counts = summary["status_counts"]
        doms = [s for s in summary["standings"] if s.get("dominant")]
        parts = [f"信号定性「{summary['signal_type'] or '未定性'}」"]
        rel = envelope.tender.relevance if (envelope and envelope.tender) else None
        if rel is not None and rel.score is not None:
            parts.append(f"与企业画像相关得分 {rel.score:g}")
        if counts.get("矛盾"):
            lean = "存在被推翻结论,建议复核后再采取行动"
        elif len(doms) > 1:
            lean = (f"{len(doms)} 个假设并列主导"
                    f"({'、'.join(s['label'] for s in doms)}),缺乏区分证据,"
                    "建议观察性跟踪,补齐区分证据后再实质投入")
        elif doms:
            lean = (f"证据结构支持「{doms[0]['label']}」(得分 {doms[0]['score']}),"
                    "可按建议推进")
        else:
            lean = "暂无主导假设,建议人工复核"
        if counts.get("待证实"):
            lean += f";{counts['待证实']} 项未决如实保留(见 Open Questions)"
        return "。".join(parts) + f"。**研判倾向:{lean}。**"

    def _red_flags(self, envelope, claims, oq_rows,
                   gists: dict | None = None) -> list[str]:
        flags: list[str] = []
        for c in claims:                       # 被推翻的结论最优先
            if c["status"] == "矛盾":
                flags.append(f"**结论被推翻**(条目 {c['draft_id']}):"
                             f"{self._clip(c['text'], 40)}")
        if envelope and envelope.tender:
            t = envelope.tender
            for name, det in (("金额异常", t.amount_anomaly),
                              ("频率异常", t.frequency_anomaly),
                              ("新颖性", t.novelty_anomaly)):
                if det is None:
                    continue
                basis = ("历史依据充分" if det.basis_sufficient
                         else "历史依据不足,不强行评分")
                flags.append(f"**{name}信号**:得分 {det.score:g}({basis})"
                             "——是否成立以裁定为准,不预作风险结论")
            if t.content_source_note:
                flags.append(f"**正文质量**:上游说明「{t.content_source_note}」")
        for r in oq_rows or []:
            if r["priority"] >= 4:
                note = " [来源不可达]" if r["unreachable"] else ""
                # gist 短名优先(空=fallback 截断 44;超长 gist 同样截=版面防线)
                text = (gists or {}).get(r["draft_id"]) or r["text"]
                flags.append(f"**未决高优断言**(条目 {r['draft_id']}"
                             f",优先级 {r['priority']}){note}:"
                             f"{self._clip(text, 44)}")
        return flags[:6]

    def _world_verified_lines(self, claims, verdicts,
                              gists: dict | None = None) -> list[str]:
        """已核实事实(外部证据):已证实 ∧ 非复述 ∧ 存在非信封根的世界断言。
        纯 signal_layer 根的复述型已证实不进正文(已压缩进卡片)——篇幅重分配。"""
        lines = []
        for c in claims:
            if c["status"] != "已证实" or c["corpus_bound"]:
                continue
            roots = [r for r in verdicts.get(c["draft_id"], {}).get(
                "support_roots", []) if r != "signal_layer"]
            if not roots:
                continue
            # gist 短名优先(空=fallback 截断 60;超长 gist 同样截=版面防线)
            text = (gists or {}).get(c["draft_id"]) or c["text"]
            lines.append(f"- **条目 {c['draft_id']}** "
                         f"{self._clip(text, 60)}"
                         f"(独立根:{'、'.join(roots)})")
        return lines

    def _brief_evidence(self, claims, rec_items, oq_rows, evidence, cap: int = 15):
        """简报引用证据:信封种子(卡片 [E1] 锚)+ 世界断言/OQ/建议依据链所引
        证据,去重保序,上限 cap。非种子证据只保留**实际进入裁定的**(裁定方向
        非 空/无关)——实体交集宽进挂账会带进未参与判决的噪声,不进企业简报。"""
        by_draft = {c["draft_id"]: c["claim_id"] for c in claims}
        want: set[str] = set()
        for c in claims:
            if c["status"] == "已证实" and not c["corpus_bound"]:
                want.add(c["claim_id"])
        for r in oq_rows or []:
            want.add(r["claim_id"])
        for r in rec_items or []:
            for did in r.basis_claim_ids:
                if did in by_draft:
                    want.add(by_draft[did])
        ids: list[str] = []
        for cid in want:
            for e in self._ledger.evidence_for_claim(cid):
                if e["ruling"] not in ("支持", "矛盾", "时效刷新"):
                    continue
                eid = e["evidence_id"].rsplit(":", 1)[-1]
                if eid not in ids:
                    ids.append(eid)
        seeds = [ev["evidence_id"].rsplit(":", 1)[-1] for ev in evidence
                 if ev["round"] == 0]
        ordered = seeds + [i for i in ids if i not in seeds]
        by_id = {ev["evidence_id"].rsplit(":", 1)[-1]: ev for ev in evidence}
        return [by_id[i] for i in ordered if i in by_id][:cap]

    def _render_brief(self, summary: dict, envelope, envelope_label: str,
                      oq_rows, rec_items, evidence, delta=None,
                      gists: dict | None = None) -> str:
        event_id = summary["event_id"]
        claims = self._ledger.all_claims(event_id)
        verdicts = self._verdict_notes(event_id)
        counts = summary["status_counts"]
        external, internal = self._split_recommendations(rec_items)

        lines = [f"# 企业情报简报:{envelope_label}", ""]
        lines.append(
            f"- 信号类型:{summary['signal_type'] or '未定性'}"
            f" | 已证实 {counts.get('已证实', 0)} / 矛盾 {counts.get('矛盾', 0)}"
            f" / 待证实 {counts.get('待证实', 0)}"
            f" | 主导假设:{self._dominant_line(summary['standings'])}")
        lines.append("")
        # delta 头部一行(渲染批次;None=零痕迹;细节见 report.md)
        if delta:
            lines.append(f"- {_delta_brief_line(delta)}")
            lines.append("")

        lines += ["## 一句话研判", "", self._gist(summary, envelope), ""]

        lines += ["## 商机卡片" if (envelope and envelope.kind == "tender")
                  else "## 主体卡片", ""]
        if envelope is None:
            lines.append("(信封上下文不可用,仅台账视图)")
        elif envelope.kind == "tender" and envelope.tender:
            lines += self._render_card(
                self._tender_card_rows(envelope.tender), claims)
        elif envelope.rumor:
            lines += self._render_card(
                self._rumor_card_rows(envelope.rumor, evidence), claims)
        lines.append("")

        flags = self._red_flags(envelope, claims, oq_rows, gists=gists)
        lines += ["## 红旗与信号", ""]
        lines += [f"{i}. {f}" for i, f in enumerate(flags, 1)] or \
                 ["- (无红旗信号)"]
        lines.append("")

        world = self._world_verified_lines(claims, verdicts, gists=gists)
        lines += ["## 已核实事实与研判状态", ""]
        lines += world or ["- (暂无外部证据核实的事实)"]
        for r in summary["relations"]:
            lines.append(f"- {r['head_name']} —{r['relation_type']}→ "
                         f"{r['tail_name']}")
        lines.append("- 竞争格局:需按采购人检索历史中标人"
                     "(定向证据源接入后本节自动填充)")
        lines.append("")

        lines += ["## 建议下一步", "",
                  "每条附依据(已查证事实列举,可沿正文引用标追溯);"
                  "建议供决策参考,不替代企业决策。", ""]
        if external:
            # 依据行人话(M2,brief 与 report 同源函数同改;第一批 brief 零
            # 变化拍板自本批起在依据行处被打破,照旧断言演进注明先例)
            rules = self._report_phrases()
            by_draft = {c["draft_id"]: dict(c) for c in claims}
            for i, r in enumerate(external, 1):
                hyp = f"(依赖假设: {r.hypothesis_label})" if r.hypothesis_label else ""
                lines.append(f"{i}. **{r.action}** {hyp}")
                lines += self._basis_lines(r.basis_claim_ids, by_draft,
                                           gists, rules)
                if r.rationale:
                    lines.append(f"   {r.rationale}")
        else:
            lines.append("- (无足以支撑行动建议的已查实结论)")
        if internal:
            lines.append("")
            lines.append(f"(另有 {len(internal)} 条系统内部事项,"
                         "见运营者报告「内部待办」)")
        lines.append("")

        lines += ["## Open Questions", ""]
        if oq_rows:
            for r in oq_rows:
                note = " [来源不可达,已登记缺口]" if r["unreachable"] else ""
                # gist 短名优先(空=fallback 全文=旧形态零变化)
                text = (gists or {}).get(r["draft_id"]) or r["text"]
                lines.append(f"- **条目 {r['draft_id']}"
                             f"**(优先级 {r['priority']})"
                             f"{note}:{text}")
        else:
            lines.append("- (无未决问题)")
        lines.append("")

        lines += ["### 引用证据(精简)", ""]
        for ev in self._brief_evidence(claims, rec_items, oq_rows, evidence):
            tag = ev["evidence_id"].rsplit(":", 1)[-1]
            lines.append(f"- **[{tag}]** {ev['title'] or '(无标题)'} | "
                         f"来源:{ev['source'] or ev['platform']} | "
                         f"谱系根:{ev['lineage_root']} | {ev['url'] or '(无链接)'}")
        lines += ["", "---", "",
                  "*研判倾向、卡片状态与红旗清单均由台账确定性规则生成(零 LLM);"
                  "逐条判决规则命中与完整 55 项证据表见运营者报告(report.md)附录。"
                  "本简报不替代企业决策。*"]
        return "\n".join(lines)

    def _display_sections(self, event_id: str, sections,
                          gists: dict | None = None,
                          rec_items: list | None = None) -> list[dict]:
        """渲染用节列表(报告文风线第一批:OQ 节现渲染替换;**第二批扩展**:
        结论节 {event}:S3 三段卡 + 行动建议节 {event}:S5 依据行人话化,均
        **渲染期替换**——卡是台账投影,重渲染幂等(新跑=与 finalize 落账
        同文);旧台账离线重渲染(P4 工具)据此出新格式。台账 sections 照存
        原文=双回放对照不受影响。其余节 draft_text 原样。
        rec_items=finalize 传入的结构化建议;缺省从台账 draft_text 解析
        (_parse_rec_draft,机器行才收——已人话化产物幂等原样)。
        第三批扩展:S1 节时间轴/关联方卡现渲染(M2),S0/S2 正文节字段名/壳词/
        元陈述口径转写(M1)——均渲染期替换,台账 sections 照存原文。"""
        oq_sec = f"{event_id}:S4"
        con_sec = f"{event_id}:S3"
        rec_sec = f"{event_id}:S5"
        tl_sec = f"{event_id}:S1"
        out = []
        for s in sections:
            text = s["draft_text"] or f"### {s['title']}"
            if s["section_id"] == oq_sec:
                text = self._render_oq(self._ledger.oq_claims(event_id),
                                       gists=gists, event_id=event_id)
            elif s["section_id"] == con_sec:
                text = self._render_conclusion(
                    event_id, gists=gists,
                    narr=self._verdict_narr(event_id, s, gists))
            elif s["section_id"] == rec_sec:
                items = rec_items or _parse_rec_draft(s["draft_text"] or "")
                if items:
                    text = self._render_recommendations(
                        items, gists=gists,
                        claims=self._ledger.all_claims(event_id))
            elif s["section_id"] == tl_sec:
                text = self._render_timeline(event_id)
            elif s["kind"] == "standard" and (s["ord"] or 0) < 3:
                text = _polish_section_text(s["draft_text"] or
                                            f"### {s['title']}",
                                            self._report_phrases())
            out.append({"title": s["title"], "text": text})
        return out

    def _render_markdown(self, sections, evidence, verdicts, summary,
                         envelope_label, rec_items=None, assets=None,
                         delta=None, gists: dict | None = None) -> str:
        counts = summary["status_counts"]
        costs = summary["costs"] or {}
        # 停止原因用户话(P5,表驱动缺键兜底;"轮数上限(硬停兜底)"等机制名
        # 不落人看层,result.json 原词全量保留=机器层对照)
        stop_tbl = self._report_phrases().get("report_stop_phrases") or {}
        disp = self._display_sections(summary["event_id"], sections, gists,
                                      rec_items=rec_items)
        lines = [
            f"# 企业情报调查报告:{envelope_label}",
            "",
            f"- 信号类型:{summary['signal_type']} | 停止原因:"
            f"{_stop_phrase(summary['stop_reason'], stop_tbl)}",
            # 用户可读化(2026-10-27):统计与成本不再 json 直出
            f"- 结论分布:已证实 {counts.get('已证实', 0)} / 矛盾 "
            f"{counts.get('矛盾', 0)} / 待证实 {counts.get('待证实', 0)}",
            # 当前领先假设(报告文风线 P5:"交付时快照"过程词与得分内部量撤出
            # 人看层;记分明细在附录 D;brief 侧 _dominant_line 不动)
            f"- 当前领先假设:{self._dominant_line_user(summary['standings'])}",
            f"- 成本:模型调用 {costs.get('llm_calls', 0)} 次 / 采集调用 "
            f"{costs.get('collect_calls', 0)} 次",
            "",
        ]
        # delta 头部(渲染批次,2026-10-27):上次结算以来的修正条件句;
        # None(首跑无锚)=零痕迹
        if delta:
            lines += _delta_md(delta, gists).splitlines() + [""]
        for sec in disp:
            lines.append(sec["text"])
            lines.append("")
        _, internal = self._split_recommendations(rec_items)
        if internal:
            # 内部待办依据行同人话化(M2:运营者也是报告读者,读者不知情同纪律)
            claims = self._ledger.all_claims(summary["event_id"])
            rules = self._report_phrases()
            by_draft = {c["draft_id"]: dict(c) for c in claims}
            lines.append("## 内部待办(运营者事项,企业简报不含)")
            lines.append("")
            for i, r in enumerate(internal, 1):
                lines.append(f"{i}. **{r.action}**")
                lines += self._basis_lines(r.basis_claim_ids, by_draft,
                                           gists, rules)
                if r.rationale:
                    lines.append(f"   {r.rationale}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附录 A:证据表(点回原文)")
        lines.append("")
        # P2 未引用证据折叠(噪音治理):引用集合=各节文本 [n]/[En] 引用标并集
        # (含现渲染 OQ 卡材料行锚);未被任何结论引用∧无附件的关联检索件
        # 移入折叠行——折叠不是合并(〔同题 N 稿〕标注逻辑保留),编号不重排
        cited = _cited_nums(s["text"] for s in disp)
        shown, folded = _split_evidence(evidence, cited, assets or {})
        # 同题聚类标注:同题多稿不合并(可能为独立稿件),但显式标注防误读
        # (计数取全部证据——被折叠的同题件也计入,标注信息不失真)
        title_counts: dict[str, int] = {}
        for ev in evidence:
            title_counts[ev["title"] or ""] = title_counts.get(ev["title"] or "", 0) + 1
        for ev in shown:
            tag = ev["evidence_id"].rsplit(":", 1)[-1]
            same = title_counts.get(ev["title"] or "", 0)
            mark = f"〔同题 {same} 稿〕" if same > 1 else ""
            lines.append(
                f"- **[{tag}]**{mark} {ev['title'] or '(无标题)'} | 来源:"
                f"{ev['source'] or ev['platform']} | 谱系根:{ev['lineage_root']}"
                f" | 可信度:{ev['credibility']} | {ev['url'] or '(无链接)'}")
            # 报告附件(A 线):图片只补充呈现,不构成判决证据(相对路径,与
            # report.md 同目录渲染;无附件时本块零输出=旧产物不变)
            for a in (assets or {}).get(ev["evidence_id"], []):
                cap = f"![{a.get('caption') or '附件图'}]({a['path']})"
                lines.append(f"  {cap}〔仅呈现,非证据〕")
        if folded:                 # 全部被引用=零折叠行(不冒充)
            lines.append("")
            lines.append(
                f"> 另有 {len(folded)} 条关联检索件未被任何结论引用"
                f"(全文见台账 result.json):{'、'.join(folded)}")
        lines.append("")
        lines.append("## 附录 B:判决核对证书(每条结论可机器复核)")
        lines.append("")
        lines.append("| 条目 | 终态 | 独立根(支持) | 独立根(反方) | 判决规则 |")
        lines.append("|---|---|---|---|---|")
        # P3 条目全文(共用投影 _cert_claims;text[:60] 截断退役——实锚不
        # 腰斩,html 证书卡同源消费)
        for r in _cert_claims(self._ledger.all_claims(summary["event_id"]),
                              verdicts):
            lines.append(
                f"| {r['id']} {r['text']} | {r['status']} "
                f"| {r['sup']} | {r['opp']} | {r['note']} |")
        lines.append("")
        lines.append("## 附录 C:结论变更日志(时间机器:翻转全程留痕)")
        lines.append("")
        if summary["transitions"]:
            # 会话分隔:复查后轮次重号(每会话各从 R1 起),按 ts 日期切会话
            # ——时间线读者能分清"哪个 R1 是哪一天"。ts 键旧调用方不传时兼容。
            sess_marks: list[str] = []
            last_day = ""
            for t in summary["transitions"]:
                day = (t.get("ts") or "")[:10]
                if day and day != last_day:
                    sess_marks.append(day)
                    last_day = day
            if len(sess_marks) > 1:
                lines.append(f"> 复查会话:{' → '.join(sess_marks)}"
                             f"(轮次在各会话内独立编号,以日期为准)")
                lines.append("")
            for t in summary["transitions"]:
                day = (t.get("ts") or "")[:10]
                stamp = f"{day} " if day else ""
                lines.append(
                    f"- {stamp}轮次{t['round']} 条目{t['claim']}: {t['old']} → "
                    f"{t['new']}({t['cause'] or '澄清'})")
            lines.append("")
            lines.append("### 结论演变时间轴(逐条目:结论随判据变化的完整轨迹)")
            lines.append("")
            by_claim: dict[str, list] = {}
            for t in summary["transitions"]:
                by_claim.setdefault(t["claim"], []).append(t)
            current = {c["draft_id"]: c["status"]
                       for c in self._ledger.all_claims(summary["event_id"])}
            for cid, tl in by_claim.items():
                path = " → ".join(
                    [tl[0]["old"]] + [x["new"] for x in tl])
                marks = " ".join(
                    f"[{(x.get('ts') or '')[:10]}]R{x['round']}·{x['new']}"
                    f"({x['cause'] or '澄清'})" for x in tl)
                lines.append(
                    f"- **条目 {cid}**(现值 {current.get(cid, '?')}):{path}")
                lines.append(f"  - 轨迹:{marks}")
        else:
            lines.append("- (无状态变更)")
        lines.append("")
        lines.append("## 附录 D:假设记分板")
        lines.append("")
        for s in summary["standings"]:
            star = " **[主导]**" if s.get("dominant") else ""
            lines.append(
                f"- {s['label']}{star}: 得分 {s['score']}"
                f"(已证实 {s['verified']} / 矛盾 {s['contradicted']}"
                f" / 待证实 {s['pending']})")

        # 附录 E:证据链(第二批 M5,拍板 3:mermaid 证据图谱退役→证据链;
        # 机器对账=chain-graph json/md 投影仍可用,evidence_graph 模块保留
        # 仅别处消费)。md=文字版证据链(render_md_chain 台账直出,零 LLM
        # 零旁车硬依赖);空链/构建失败=降级注记(失败不伪装)。
        lines.append("## 附录 E:证据链(文字版)")
        lines.append("")
        graph = self._chain_graph(summary["event_id"])
        if graph is None:
            lines.append("(证据链暂不可用)")
        else:
            chain_md = render_md_chain(graph)
            if chain_md:
                body = chain_md.splitlines()
                # render_md_chain 自带「### 证据链(文字版)」首行,换用附录
                # 命名与 A-D 一致(函数本体不动=chain-graph CLI 输出不受影响)
                lines.extend(body[1:] if body[:1] == ["### 证据链(文字版)"]
                             else body)
            else:
                lines.append("(尚无成链的证据关系)")
        return "\n".join(lines)

    def _render_html(self, sections, evidence, verdicts, summary,
                     envelope_label, assets=None, ess_result=None,
                     delta=None, k5=None, gists=None,
                     rec_items: list | None = None,
                     out_dir: Path | str | None = None) -> str:
        """演示包:红绿灯徽章/点击查账/双口径/决策卡/记分板/证书/成本行。
        out_dir 提供时(交付/离线重渲染),证据链卡在 svg 内嵌外同时直出
        chain.png 供单独取用(第三批复查拍板 C:html 内嵌 svg+附 png;无
        dot=不生成 png,html 照旧=失败不伪装)。"""
        esc = html_mod.escape
        counts = summary["status_counts"]
        costs = summary["costs"] or {}
        top = next((s for s in summary["standings"] if s.get("dominant")), None)
        has_assets = bool(assets)
        # 报告文风线第一批:停止原因用户话(P5,与 md 头部同表同函数)+OQ 节
        # 现渲染(P1)+证据表折叠(P2,与附录 A 同源函数)+证书全文列(P3)
        stop_tbl = self._report_phrases().get("report_stop_phrases") or {}
        disp = self._display_sections(summary["event_id"], sections, gists,
                                      rec_items=rec_items)
        cited = _cited_nums(s["text"] for s in disp)
        shown, folded = _split_evidence(evidence, cited, assets or {})
        # 渲染批次(2026-10-27):ESS 证据强度节——md 附录同源文本过白名单
        # 转换(零重复逻辑,md/html 同数据);off/降级/None=零痕迹
        ess_card = ""
        if ess_result and "error" not in ess_result:
            ess_card = _card(
                "证据强度(ESS,内部工作底稿,不作对外评级)",
                _appendix_body(_ess_appendix(ess_result, gists=gists)))
        # delta 修正卡(渲染批次):首跑无锚=None=零痕迹;同源文本过白名单
        delta_card = ""
        if delta:
            delta_card = _card(
                "较上次报告的修正(上次结算 "
                f"{html_mod.escape(_delta_cn_ts(delta['anchor_ts']))})",
                _appendix_body(_delta_md(delta, gists)))
        # K5 资质核对卡(渲染批次):门控不满足/降级=None=零痕迹;同源文本
        k5_card = ""
        if k5 and "rows" in k5:
            k5_card = _card(
                "资质核对(K5 规则卡比对)",
                _appendix_body(_kb_appendix(k5["rows"],
                                            bool(k5.get("internal")))))

        def badge(status: str) -> str:
            label, cls = _STATUS_BADGE.get(status, (status, "warn"))
            return f'<span class="badge {cls}">{esc(label)}</span>'

        cards = []
        for sec in disp:
            body = _md_block(_strip_dup_heading(sec["text"], sec["title"]))
            cards.append(
                f'<section><h2>{esc(sec["title"])}</h2>'
                f'<div class="body">{body}</div></section>')
        def _thumbs(ev) -> str:
            items = (assets or {}).get(ev["evidence_id"], [])
            if not items:
                return ""
            return "".join(
                f'<a href="{esc(a["path"])}" target="_blank" '
                f'title="{esc(a.get("caption") or "附件图")} · 仅呈现,非证据">'
                f'<img src="{esc(a["path"])}" alt="{esc(a.get("caption") or "附件图")}" '
                f'style="max-width:120px;max-height:90px;object-fit:contain;'
                f'margin:2px;border:1px solid #eee"></a>'
                for a in items)

        ev_rows = "".join(
            f'<tr id="ev-{esc(ev["evidence_id"].rsplit(":", 1)[-1])}">'
            f'<td>[{esc(ev["evidence_id"].rsplit(":", 1)[-1])}]</td>'
            f'<td>{esc(ev["title"] or "(无标题)")}</td>'
            f'<td>{esc(ev["source"] or ev["platform"])}</td>'
            f'<td>{esc(ev["lineage_root"])}</td>'
            f'<td>{esc(ev["credibility"])}</td>'
            f'<td>{esc(ev["url"] or "-")}</td>'
            + (f'<td>{_thumbs(ev)}</td>' if has_assets else "")
            + "</tr>"
            for ev in shown)
        # P2 折叠行(与附录 A 同源判据;全部被引用=零输出)
        fold_note = (
            f'<p class="small">另有 {len(folded)} 条关联检索件未被任何结论引用'
            f'(全文见台账 result.json):'
            f'{esc("、".join(folded))}</p>'
            if folded else "")
        cert = _cert_claims(self._ledger.all_claims(summary["event_id"]),
                            verdicts)
        cert_rows = "".join(
            f'<tr><td>{esc(r["id"])}</td><td>{esc(r["text"])}</td>'
            f'<td>{badge(r["status"])}</td><td>{esc(r["sup"])}</td>'
            f'<td>{esc(r["opp"])}</td><td>{esc(r["note"])}</td></tr>'
            for r in cert)
        change_rows = "".join(
            f'<li>{esc((t.get("ts") or "")[:10])} 轮次{t["round"]} '
            f'条目{esc(t["claim"])}: {esc(t["old"])} '
            f'&rarr; {esc(t["new"])}({esc(t["cause"] or "澄清")})</li>'
            for t in summary["transitions"]) or "<li>(无变更)</li>"
        # 逐条目演变时间轴(与 md 附录 C 同源:轨迹=翻转序列,现值=台账终态)
        current_map = {c["draft_id"]: c["status"]
                       for c in self._ledger.all_claims(summary["event_id"])}
        by_claim_html: dict[str, list] = {}
        for t in summary["transitions"]:
            by_claim_html.setdefault(t["claim"], []).append(t)
        timeline_rows = "".join(
            f'<li><b>条目 {esc(cid)}</b>(现值 {esc(current_map.get(cid, "?"))}):'
            + esc(" → ".join([tl[0]["old"]] + [x["new"] for x in tl]))
            + '<br><span class="small">'
            + esc(" ".join(
                f"[{(x.get('ts') or '')[:10]}]R{x['round']}·{x['new']}"
                f"({x['cause'] or '澄清'})" for x in tl))
            + "</span></li>"
            for cid, tl in by_claim_html.items()) or "<li>(无变更)</li>"
        score_rows = "".join(
            f'<li>{esc(s["label"])}{" ★主导" if s.get("dominant") else ""}'
            f' —— 得分 {s["score"]}(已证实 {s["verified"]} / 矛盾 {s["contradicted"]}'
            f' / 待证实 {s["pending"]})</li>'
            for s in summary["standings"]) or "<li>(无假设)</li>"

        # 附录:证据链(第二批 M5,拍板 3:mermaid 证据图谱退役→链卷面 svg
        # 主图内嵌;dot 为主引擎,无 dot=自研兜底(render_chain_svg 内部降级,
        # 图上带注记),构建失败=降级一行说明不伪装。CDN mermaid 全套退役
        # =report.html 离线零依赖,照 chain_svg CDN 退役先例)。
        graph = self._chain_graph(summary["event_id"])
        chain_card = '<div class="card"><h2>附录:证据链</h2>'
        if graph is None:
            chain_card += ('<p class="small">证据链暂不可用(逐条明细见'
                           "附录 A 证据表与附录 B 核对证书)</p></div>")
        else:
            try:
                svg = render_chain_svg(graph)
                # 附 png(第三批复查拍板 C):svg 内嵌外直出位图供单独取用;
                # 无 dot=不生成(html 照旧,失败不伪装)
                png_note = ""
                if out_dir:
                    try:
                        render_chain_png(graph, Path(out_dir) / "chain.png")
                        png_note = " 另存图片:chain.png(同目录)。"
                    except ChainSvgError:
                        png_note = ""
                chain_card += (
                    svg
                    + '<p class="small">图例:线越粗支撑越硬;红=相反记载;'
                    "虚线=待查证。逐条明细见附录 A 证据表与附录 B 核对证书,"
                    "本图为离线确定性渲染。" + png_note + "</p></div>")
            except ChainSvgError as exc:
                chain_card += (f'<p class="small">链图暂不可用({esc(str(exc))});'
                               "逐条明细见附录 A 证据表与附录 B 核对证书"
                               "</p></div>")

        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Sense2act 调查报告 · {esc(envelope_label)}</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:980px;margin:24px auto;
 padding:0 16px;color:#1a1a1a;background:#fafafa}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;
 margin:14px 0;overflow-x:auto}}
.badge{{display:inline-block;padding:1px 10px;border-radius:10px;font-size:13px}}
.badge.ok{{background:#e6f6e6;color:#1c6b1c}} .badge.bad{{background:#fde8e8;color:#a11}}
.badge.warn{{background:#fff4dd;color:#8a6100}}
table{{border-collapse:collapse;width:100%;font-size:14px;table-layout:fixed}}
td,th{{border:1px solid #ddd;padding:6px 8px;text-align:left;overflow-wrap:anywhere}}
h2{{border-bottom:2px solid #2b6cb0;padding-bottom:4px;font-size:18px}}
.kpi{{display:inline-block;margin:0 18px 0 0;font-size:15px}}
.small{{color:#666;font-size:13px}}
.body h3{{font-size:16px;margin:14px 0 6px}}
.body h4{{font-size:15px;margin:10px 0 4px}}
.body p{{margin:8px 0;line-height:1.75}}
.body ul,.body ol{{margin:6px 0;padding-left:26px}}
.body li{{margin:3px 0;line-height:1.7}}
.body table{{margin:8px 0}}
.body blockquote{{margin:8px 0;padding:6px 12px;border-left:3px solid #cbd5e0;
 color:#555;background:#f8fafc}}
.body sup a{{color:#2b6cb0;text-decoration:none}}
.body hr{{border:none;border-top:1px solid #e0e0e0;margin:12px 0}}
</style></head><body>
<div class="card">
 <h2 style="border:none;margin:0">一页决策卡 · {esc(envelope_label)}</h2>
 <p>
  <span class="kpi">{badge("已证实")} × {counts.get("已证实", 0)}</span>
  <span class="kpi">{badge("矛盾")} × {counts.get("矛盾", 0)}</span>
  <span class="kpi">{badge("待证实")} × {counts.get("待证实", 0)}</span>
 </p>
 <p><b>主导假设:</b>{esc(top["label"]) if top else "(无)"} ——
 {esc(json.dumps(summary["relations"], ensure_ascii=False)) if summary["relations"] else "无已证实关系边"}</p>
 <p class="small">停止原因:{esc(_stop_phrase(summary["stop_reason"], stop_tbl))} |
 成本:模型调用 {costs.get("llm_calls", 0)} 次 / 采集调用 {costs.get("collect_calls", 0)} 次 |
 每条结论可在附录核对证书中机器复核</p>
</div>
{ess_card}{delta_card}{k5_card}{''.join(cards)}
<div class="card"><h2>附录:判决核对证书</h2>
<table><tr><th style="width:8%">条目</th><th style="width:26%">内容(全文)</th>
<th style="width:9%">终态</th><th style="width:12%">独立根(支持)</th>
<th style="width:12%">独立根(反方)</th><th style="width:33%">判决规则</th></tr>{cert_rows}</table></div>
<div class="card"><h2>附录:证据表(点击查账)</h2>
<table><tr><th style="width:6%">编号</th><th style="width:27%">标题</th>
<th style="width:10%">来源</th><th style="width:10%">谱系根</th>
<th style="width:7%">可信度</th><th style="width:24%">链接</th>
{'<th style="width:16%">附件(仅呈现,非证据)</th>' if has_assets else ''}</tr>{ev_rows}</table>{fold_note}</div>
<div class="card"><h2>结论变更日志(时间机器)</h2><ul>{change_rows}</ul></div>
<div class="card"><h2>结论演变时间轴(逐条目)</h2><ul>{timeline_rows}</ul></div>
<div class="card"><h2>假设记分板</h2><ul>{score_rows}</ul></div>
{chain_card}
</body></html>"""
