# -*- coding: utf-8 -*-
"""证据链形态图·生成期(2026-11-05 方案 §五)——LLM 两处:方向极性+通道建议。

统一链路(照 gist 成熟先例:LLM 出生→落库→渲染读库→人工覆写优先):
LLM 判 → 落库旁车(kb.db chain_sidecar 表,带 model/prompt_version/指纹/
时间戳)→ 渲染零 LLM 零网络(chain_graph 只读消费+指纹校验,漂移弃用=
重跑本命令即刷新)→ hypothesis_seeds.json 人工标 polarity 的**人工优先**
(本命令跳过人工已标条目;seeds polarity map 为加法式字段,旧文件兼容)。

纪律(方案 §五/§八,施工中不可破):
- 独立生成期命令:不进 investigate 管道、不进 META_KEYS、零既有 prompt 改动
  (本模块 prompt 内聚,照 red_team 先例)、不动录制件;
- 呈现层**不标 AI**:rationale 只是边注释,LLM 来源留痕只在旁车数据
  (model/prompt_version 字段)供对账——照"数据层英文供机器对账,呈现层
  负责翻译"分层纪律,与 gist 同款;
- LLM 失败=旁车缺项=图上不画该边属性(失败不伪装,不伪装人工标);
- **方向/通道/概括永不回流判决与评分**(负测试锁:stages/s6_verdict.py 与
  credibility.py 不 import 本模块与 chain_graph,照 ESS 不回流先例);
- 通道建议=机器闸兜幻觉:白名单(platform_rules.json 已登记通道)外的建议
  落库前直接丢弃计数(chain_graph 消费侧同校验,两侧同判据);
- kb_derive 幂等纯函数零 LLM docstring 不触碰:sidecar 表由本模块自管
  (CREATE TABLE IF NOT EXISTS,不进 kb_store 写入路径——LLM 旁车不进 write_kb);
- 指纹漂移:旁车带命题(文本+状态+gist)/假设标签(极性)或缺口型(通道)
  的 sha256;渲染期重算比对,不一致=弃用——本命令重跑即整体刷新。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from .chain_graph import (DRAW_RULINGS, SEED_PLATFORM, channel_fp,
                          channel_whitelist, classify_claim, ev_summary_fp,
                          fusion_fp,
                          gap_type_of, grouping_fp, interp_fp,
                          load_manual_grouping, load_manual_polarity,
                          load_present_rules, legacy_fp, narrative_fp,
                          pick_argument, polarity_fp, strip_shell,
                          validate_grouping)
from .credibility import collect_factors
from .llm import call_structured


class ChainGenError(Exception):
    """生成期失败(kb.db 不可写/主库缺失——失败不伪装)。"""


# ---------------------------------------------------------------- 解读推理集合存储
# (P7 升格正名,三阶段 §二/§七.1:chain_sidecar → insights;双名兼容)
# 物理形态二选一,由 open_insights_rw 就地定型,此后读写一律走 insights 名:
#   新库   = insights 物理表 + chain_sidecar 视图(INSTEAD OF 触发器照写)
#   升格前老库 = chain_sidecar 物理表 ALTER 补三列(照 created_at 加法式
#            先例)+ insights 反向视图(读写触发器路由)
# 老五 kind(polarity/channel/ev_summary/grouping/narrative)source=internal、
# status 空(轻状态机不适用);新 kind:signal_interpretation/gap_hint/
# signal_meta/detector_field(source=upstream|legacy,轻状态机 received→
# anchored→derived)、interpretation(LLM 锚点桥,出生即 anchored)。
INSIGHTS_DDL = """
CREATE TABLE IF NOT EXISTS insights (
  insight_id     TEXT PRIMARY KEY,   -- {kind}:{claim_id}[:{hyp}] | legacy:{claim_id}
                                     -- | upstream:{event}:{n} | interp:{source_id}
  event_id       TEXT NOT NULL,
  kind           TEXT NOT NULL,      -- 五老 kind + 解读四类 + interpretation
  claim_id       TEXT NOT NULL,      -- 上游解读类=空串(不锚命题)
  hypothesis_id  TEXT NOT NULL DEFAULT '',
  value          TEXT NOT NULL,      -- 正向|反向|通道|概括|JSON|解读原文
  rationale      TEXT NOT NULL DEFAULT '',   -- LLM 一句理由(边注释;呈现不标 AI)
  fingerprint    TEXT NOT NULL DEFAULT '',   -- 漂移检测(消费侧重算比对)
  model          TEXT NOT NULL DEFAULT '',
  prompt_version TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT '',   -- received|anchored|derived(解读类)
  source         TEXT NOT NULL DEFAULT '',   -- upstream | internal | legacy
  legacy_claim_id TEXT NOT NULL DEFAULT '',  -- P9 存量回指主库 claim
  created_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_insights_event ON insights(event_id, kind);
CREATE VIEW IF NOT EXISTS chain_sidecar AS
  SELECT insight_id AS sidecar_id, event_id, kind, claim_id, hypothesis_id,
         value, rationale, fingerprint, model, prompt_version, created_at
  FROM insights;
CREATE TRIGGER IF NOT EXISTS trg_sidecar_ins INSTEAD OF INSERT ON chain_sidecar
BEGIN
  INSERT OR REPLACE INTO insights(insight_id,event_id,kind,claim_id,hypothesis_id,
    value,rationale,fingerprint,model,prompt_version,created_at,source,status)
  VALUES(NEW.sidecar_id,NEW.event_id,NEW.kind,NEW.claim_id,NEW.hypothesis_id,
    NEW.value,NEW.rationale,NEW.fingerprint,NEW.model,NEW.prompt_version,
    NEW.created_at,'internal','');
END;
"""

# 升格前老库的反向视图与触发器(open 时按需建;前置=三新列已 ALTER 补齐)
_LEGACY_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS insights AS
  SELECT sidecar_id AS insight_id, event_id, kind, claim_id, hypothesis_id,
         value, rationale, fingerprint, model, prompt_version, created_at,
         status, source, legacy_claim_id
  FROM chain_sidecar;
CREATE TRIGGER IF NOT EXISTS trg_insights_ins INSTEAD OF INSERT ON insights
BEGIN
  INSERT OR REPLACE INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,
    hypothesis_id,value,rationale,fingerprint,model,prompt_version,created_at,
    status,source,legacy_claim_id)
  VALUES(NEW.insight_id,NEW.event_id,NEW.kind,NEW.claim_id,NEW.hypothesis_id,
    NEW.value,NEW.rationale,NEW.fingerprint,NEW.model,NEW.prompt_version,
    NEW.created_at,NEW.status,NEW.source,NEW.legacy_claim_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_insights_upd INSTEAD OF UPDATE ON insights
BEGIN
  UPDATE chain_sidecar SET event_id=NEW.event_id, kind=NEW.kind,
    claim_id=NEW.claim_id, hypothesis_id=NEW.hypothesis_id, value=NEW.value,
    rationale=NEW.rationale, fingerprint=NEW.fingerprint, model=NEW.model,
    prompt_version=NEW.prompt_version, created_at=NEW.created_at,
    status=NEW.status, source=NEW.source, legacy_claim_id=NEW.legacy_claim_id
  WHERE sidecar_id=OLD.sidecar_id;
END;
"""

_V3_COLS = (("status", "TEXT NOT NULL DEFAULT ''"),
            ("source", "TEXT NOT NULL DEFAULT ''"),
            ("legacy_claim_id", "TEXT NOT NULL DEFAULT ''"))

PROMPT_VERSION = "chain-gen-4"   # v4=批2(ev_fusion 组内材料融合;六重机器闸)


def open_insights_rw(path: str | Path) -> sqlite3.Connection:
    """kb.db 读写打开(P7 升格):按既有物理形态就地定型(幂等,可重开),
    此后本连接读写一律走 insights 名(视图触发器路由到底表)。失败不伪装。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    ttype = {r[0]: r[1] for r in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')")}
    if ttype.get("insights") == "table":
        conn.executescript(INSIGHTS_DDL)          # 新库:物理表在,幂等补旧名视图
    elif ttype.get("insights") == "view" or "chain_sidecar" in ttype:
        for col, ddl in _V3_COLS:                 # 老库:**先补列**(反向视图
            try:                                  # SELECT 这些列,必须先在)
                conn.execute(f"ALTER TABLE chain_sidecar ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass                              # 列已在/chain_sidecar=视图(新库形态)
        conn.executescript(_LEGACY_VIEW_DDL)      # 再建反向视图(幂等)
    else:
        conn.executescript(INSIGHTS_DDL)          # 空库=新名建表
    conn.commit()
    return conn


open_sidecar_rw = open_insights_rw   # 一阶段旧名兼容(测试与调用点零改动)
SIDECAR_DDL = INSIGHTS_DDL           # 一阶段旧名兼容(内容已升格=insights 正名
                                     # +旧名视图照写;老测试经此路径同测新机制)


def _put(conn: sqlite3.Connection, *, event_id: str, kind: str,
         claim_id: str, hypothesis_id: str = "", value: str = "",
         rationale: str = "", fingerprint: str = "", model: str = "",
         source: str = "internal", status: str = "",
         legacy_claim_id: str = "", sid: str | None = None) -> None:
    """insights 写入口(INSERT OR REPLACE=幂等)。sid 缺省=老五 kind 的
    一阶段约定 {kind}:{claim_id}[:{hyp}](旧调用零改动);新 kind 由调用方
    显式传 sid(legacy:{claim_id} | upstream:{event}:{n} | interp:{source})。"""
    if sid is None:
        sid = _sid(kind, claim_id, hypothesis_id)
    conn.execute(
        "INSERT OR REPLACE INTO insights(insight_id,event_id,kind,claim_id,"
        "hypothesis_id,value,rationale,fingerprint,model,prompt_version,"
        "created_at,status,source,legacy_claim_id)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?)",
        (sid, event_id, kind, claim_id, hypothesis_id, value, rationale,
         fingerprint, model, PROMPT_VERSION, status, source, legacy_claim_id))
    conn.commit()


def _sid(kind: str, claim_id: str, hypothesis_id: str = "") -> str:
    """老五 kind 的 id 约定(一阶段沿用,零迁移)。"""
    return f"{kind}:{claim_id}" + (f":{hypothesis_id}" if hypothesis_id else "")


# ---------------------------------------------------------------- LLM 意见模型

class _PolarityItem(BaseModel):
    claim_id: str
    hypothesis_id: str
    polarity: str          # 正向 | 反向(值域校验在消费侧,LLM 只出意见)
    rationale: str = ""


class PolarityOut(BaseModel):
    items: list[_PolarityItem] = []


class _ChannelItem(BaseModel):
    claim_id: str
    channel: str
    rationale: str = ""


class ChannelOut(BaseModel):
    items: list[_ChannelItem] = []


class _SummaryItem(BaseModel):
    evidence_id: str
    summary: str          # 一句概括(≤40字;值域校验在消费侧,LLM 只出意见)


class SummaryOut(BaseModel):
    items: list[_SummaryItem] = []


class _GroupItem(BaseModel):
    name: str             # 论点名(≤14字软参考;覆盖校验在 validate_grouping)
    members: list[str]


class GroupingOut(BaseModel):
    groups: list[_GroupItem] = []


class NarrativeOut(BaseModel):
    chapter: str = ""       # 章首研判(≤80字;只述证据结构)
    walkthrough: str = ""   # 论证区串讲(≤160字;按论点顺序)


class _InterpItem(BaseModel):
    source_id: str          # 上游解读 insights 条目 id
    conclusion: str         # 锚点命题 claim_id(机器闸:必须在台账)
    basis: list[str] = []   # 依据事实 claim_id(机器闸:必须在链;1~3 条)
    rationale: str = ""


class InterpOut(BaseModel):
    items: list[_InterpItem] = []


class _FusionItem(BaseModel):
    claim: str              # 命题 claim_id(输入原样回;机器闸:必须在触发清单)
    text: str               # 融合概括(一句共同印证;形态闸拒列表形态)
    members: list[str] = []  # 引用材料 evidence_id(机器闸:恰好=该命题材料全集)


class FusionOut(BaseModel):
    items: list[_FusionItem] = []


# ---------------------------------------------------------------- 六重机器闸(批2 §四)

_NUM_RE = re.compile(r"\d+(?:[.,，]\d+)*")
_SEQ_RE = re.compile(r"(?:^|[。;;!!??\s(（])([①-⑳]|\d{1,2}[..、))])")
_FUSION_CUE_RE = re.compile(r"共同印证|共同指向|相互印证")
_SOURCE_HEAD_RE = re.compile(r"^(公告|公示|论坛帖|供应商|材料|记录|报道|来函)"
                             r"(显示|载明|称|自述|提及|指出|记载)")


def fusion_number_set(text: str) -> set[str]:
    """数字串提取(千分位逗号/全角逗号归一后比对——归一相等=视觉等价,
    千分位变形是否算失真由探针定标,首批宽松档)。"""
    return {m.replace(",", "").replace("，", "")
            for m in _NUM_RE.findall(text or "")}


def has_list_form(text: str) -> bool:
    """第六闸(形态闸,反机械拼接):概括呈列表形态=序号前缀(≥2 个不同
    序号 token)或分号并列(≥2 个分号=≥3 段并列)→整组 invalid。阈值
    首批值,_fusion_probe.py 探针定标校准(照 predicate_check 探针先例)。"""
    t = text or ""
    if len(set(_SEQ_RE.findall(t))) >= 2:
        return True
    # 分号计数半角(0x3B)+全角(0xFF1B)都数——码点明写,防字面全角
    # 字符在编辑/传输中退化成半角的同款笔误(探针实抓)
    return t.count(";") + t.count(chr(0xFF1B)) >= 2


def validate_fusion_item(text: str, members: list[str],
                         group_mats: dict, *, min_members: int = 2) -> str:
    """融合六重机器闸(批2 §四 F1):返回 ""=过;非空=拒收原因(计数留痕
    失败不伪装)。①成员幻觉(锚须在输入材料集内)②全覆盖(锚=全集,概括
    必须触及每件材料)③不重复(锚+句子两层面)④最小件数(表驱动
    fusion_min_members)⑤数字保真(输出数字串⊆输入数字串集合=禁止变形
    与虚构,允许省略)⑥形态闸(列表形态=机械拼接→整组 invalid——
    反机械拼接原则的机器形态,prompt 硬纪律之外的代码闸)。"""
    ids = set(group_mats)
    ms = [m for m in (members or []) if isinstance(m, str)]
    if len(ms) != len(set(ms)):
        return "重复成员"
    ms_set = set(ms)
    if not ms_set <= ids:
        return "成员幻觉"
    if len(ms_set) < min_members:
        return "最小件数"
    if ms_set != ids:
        return "覆盖不全"
    t = (text or "").strip()
    if not t:
        return "空文本"
    sents = [s.strip() for s in re.split(r"[。!!??]", t) if s.strip()]
    if len(sents) != len(set(sents)):
        return "文本重复"
    if has_list_form(t):
        return "列表形态"   # 形态闸先于数字保真(序号列表的序号数字不作数)
    if _SOURCE_HEAD_RE.search(t) or not _FUSION_CUE_RE.search(t):
        return "非共同归纳"   # 来源逐条复述/无共同事实收束=仍是列表换标点
    in_nums: set[str] = {str(len(group_mats))}  # 「获N路」的 N=成员件数,结构量
    for v in group_mats.values():
        in_nums |= fusion_number_set((v.get("title") or "")
                                     + (v.get("content") or ""))
    if not fusion_number_set(t) <= in_nums:
        return "数字失真"
    return ""


# ---------------------------------------------------------------- prompts(内聚,照 red_team 先例)

POLARITY_PROMPT = (
    "你是企业情报调查系统的证据链形态图生成期助手。任务:对每一条"
    "(命题, 假设)组合判断传导方向极性——该命题内容若成立,是对该假设的"
    "支撑(正向)还是拆台(反向)。判定依据=命题语义与假设描述的相容方向,"
    "不是命题自身的三态(待证实命题也要判,图上要画)。输出 JSON:"
    '{"items":[{"claim_id":"…","hypothesis_id":"…","polarity":"正向|反向",'
    '"rationale":"一句理由,≤40字"}]}。纪律:不得虚构 claim_id/hypothesis_id;'
    "只判给出的组合;理由只说语义关系,不给调查结论。")

CHANNEL_PROMPT = (
    "你是企业情报调查系统的证据链形态图生成期助手。任务:为调查缺口建议"
    "「走哪个通道获取」——候选通道白名单会在输入中给出,只能从中选一个,"
    "且只建议确有把握的缺口(不合适则省略该条)。输出 JSON:"
    '{"items":[{"claim_id":"…","channel":"白名单中的通道名",'
    '"rationale":"一句理由,≤30字"}]}。纪律:不得输出白名单外的通道;'
    "不得虚构 claim_id;建议是获取渠道线索,不是结论。")

EV_SUMMARY_PROMPT = (
    "你是企业情报调查系统的证据链形态图生成期助手。任务:为每件证据材料写"
    "一句概括(≤40字),供图上材料卡显示。输出 JSON:"
    '{"items":[{"evidence_id":"…","summary":"一句概括"}]}。'
    "纪律:只用材料原文信息;数字、金额、日期、对象名必须原样保留不变形;"
    "禁止引入材料中没有的内容;禁止推测与评价;不写「该材料显示」类套话;"
    "不得虚构 evidence_id;只概括给出的材料。")

GROUPING_PROMPT = (
    "你是企业情报调查系统的证据链形态图生成期助手。任务:把给定命题集合"
    "聚类为 2~4 个论点组并为每组命名(≤14字,概括事实主题)。输出 JSON:"
    '{"groups":[{"name":"论点名","members":["claim_id",…]}]}。'
    "纪律:必须恰好覆盖全部命题——每条恰好出现一次,不得遗漏、不得重复、"
    "不得虚构 claim_id;分组依据=命题内容的事实主题;不判状态、不改状态;"
    "论点名只概括主题,不含任何结论。")

NARRATIVE_PROMPT = (
    "你是企业情报调查系统的证据链形态图生成期助手。任务:为论证区写两段"
    "文字:①章首研判(≤80字);②论证区串讲(≤160字,按论点顺序串)。"
    '输出 JSON:{"chapter":"…","walkthrough":"…"}。纪律:只述证据结构'
    "(哪些命题已查实、由几路材料支撑、哪些还悬着);禁止真相断言,禁止"
    "「假设成立/属实」类定性;语气只陈述证据结构本身。")

INTERP_PROMPT = (
    "你是企业情报调查系统的证据链生成期助手。任务:为每条上游解读选取锚点——"
    "该解读说的是台账里的哪条命题(conclusion=台账命题的 claim_id),以及它依据"
    "哪些已入链的事实(basis=1~3 条 claim_id,不得含 conclusion 自身)。"
    '输出 JSON:{"items":[{"source_id":"…","conclusion":"台账 claim_id",'
    '"basis":["claim_id",…],"rationale":"一句理由,≤40字"}]}。'
    "纪律:conclusion 与 basis 只能取「台账命题」清单里给出的 claim_id,"
    "不得虚构;无合适锚点的解读可整条省略;只做锚定,不判真伪、不下调查结论。")

FUSION_PROMPT = (
    "你是企业情报调查系统的证据链生成期助手。任务:对每个给定命题,把支持"
    "该命题的全部独立来源材料融合成一句共同印证(≤70字),供图上「多源印证"
    "卡」显示。输出 JSON:"
    '{"items":[{"claim":"命题 claim_id(输入原样)",'
    '"text":"共同事实，获N路独立材料共同印证","members":["全部 evidence_id"]}]}。'
    "硬纪律:①句子必须**先说共同事实**,再用「获N路独立材料共同印证」或"
    "「多路材料共同指向」收束;必须含「共同印证/共同指向/相互印证」之一,"
    "否则机器闸拒收;②禁止以「公告显示/论坛帖称/供应商自述/材料A」开头,"
    "禁止逐件复述来源;③members 必须恰好等于该命题给出的全部材料(一件不落);"
    "④只用材料原文信息,数字、金额、日期、对象名必须原样保留不变形;⑤禁止"
    "引入材料中没有的内容、禁止推测与评价;⑥写成一句连贯的话,禁止分号或"
    "逗号串联逐材料摘要,禁止「1./2./①」等序号列表;⑦材料若没有可共同"
    "归纳的交集,整条省略;不下调查结论。反例:「论坛帖称欠薪，供应商自述"
    "付款争议」;正例:「B公司与供应商存在付款争议，获2路独立材料共同印证」。")


# ---------------------------------------------------------------- 主入口

def run_chain_gen(mains, kb_path, event_id: str, *, provider,
                  auth_roots=None, config_dir: str | Path = "data/config",
                  seeds_path=None) -> dict:
    """生成期主入口:LLM 两处→旁车落库;返回 stats dict(留痕供对账)。

    polarity:跳过人工已标(seeds polarity map);值域/存在性校验失败=计数
    不落库。channel:只对待证实缺口的 claim;白名单外=丢弃计数。
    LLM 调用失败(重试耗尽)=该处零落库,stats 带错误串(失败不伪装)。
    """
    led = mains.get(event_id)
    if led is None:
        raise ChainGenError(f"主库未提供: {event_id}")
    claims = {r["claim_id"]: dict(r) for r in led.all_claims(event_id)}
    hyp_rows = [dict(r) for r in led.conn.execute(
        "SELECT hypothesis_id, label, description FROM hypotheses"
        " WHERE event_id=? ORDER BY hypothesis_id", (event_id,))]
    hc_map: dict[str, list[str]] = {}
    for r in led.conn.execute(
            "SELECT hc.hypothesis_id, hc.claim_id FROM hypothesis_claims hc"
            " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
            " WHERE h.event_id=? ORDER BY hc.hypothesis_id, hc.claim_id",
            (event_id,)):
        hc_map.setdefault(r["hypothesis_id"], []).append(r["claim_id"])
    factors = {f["claim_id"]: f for f in collect_factors(led, event_id)}
    auth = set(auth_roots or ())
    manual = load_manual_polarity(seeds_path)
    wl = channel_whitelist(config_dir)

    conn = open_insights_rw(kb_path)
    rules = load_present_rules(config_dir)
    stats = {"event_id": event_id, "model": getattr(provider, "model_name", ""),
             "prompt_version": PROMPT_VERSION,
             "legacy_migrated": 0, "upstream_extracted": 0,
             "interp_total": 0, "interp_written": 0,
             "interp_invalid": 0, "interp_error": "",
             "polarity_total": 0, "polarity_manual_skipped": 0,
             "polarity_written": 0, "polarity_invalid": 0,
             "polarity_error": "",
             "channel_total": 0, "channel_written": 0,
             "channel_dropped": 0, "channel_error": "",
             "ev_summary_total": 0, "ev_summary_written": 0,
             "ev_summary_invalid": 0, "ev_summary_error": "",
             "grouping_total": 0, "grouping_manual_skipped": 0,
             "grouping_written": 0, "grouping_invalid": 0,
             "grouping_error": "",
             "narrative_total": 0, "narrative_written": 0,
             "narrative_invalid": 0, "narrative_error": ""}
    try:
        # ---- ⓪存量迁移(P9,机械零 LLM:主库解读型 claims 复制入 insights,
        #      source=legacy 回指;幂等已存在跳过,主库零改动)
        #      + 上游解读抽取(§六.1,机械零 LLM:信封摘要解读段逐句入
        #      insights,source=upstream 原文保留)
        stats["legacy_migrated"] = migrate_legacy_insights(
            led, conn, event_id, rules)
        stats["upstream_extracted"] = extract_upstream_insights(
            led, conn, event_id, rules)

        # ---- ①方向极性(人工优先:人工已标不入 prompt)
        todo: list[tuple[str, dict]] = []
        for h in hyp_rows:
            for cid in hc_map.get(h["hypothesis_id"], []):
                c = claims.get(cid)
                if c is None:
                    continue
                stats["polarity_total"] += 1
                if manual.get(h["label"], {}).get(c["draft_id"]):
                    stats["polarity_manual_skipped"] += 1
                    continue
                todo.append((cid, h))
        if todo:
            user = json_items(todo, claims, hyp_rows)
            try:
                out = call_structured(provider, stage="chain_polarity",
                                      system=POLARITY_PROMPT, user=user,
                                      model_cls=PolarityOut, retry=1)
                for it in out.items:
                    h = next((x for x in todo if x[0] == it.claim_id
                              and x[1]["hypothesis_id"] == it.hypothesis_id),
                             None)
                    c = claims.get(it.claim_id)
                    if h is None or c is None or it.polarity not in ("正向", "反向"):
                        stats["polarity_invalid"] += 1
                        continue
                    f = factors.get(it.claim_id)
                    status = f["status"] if f else c["status"]
                    _put(conn, sid=_sid("polarity", it.claim_id,
                                        it.hypothesis_id),
                         event_id=event_id, kind="polarity",
                         claim_id=it.claim_id, hypothesis_id=it.hypothesis_id,
                         value=it.polarity, rationale=it.rationale[:80],
                         fingerprint=polarity_fp(c["text"], status,
                                                 c.get("gist") or "", h[1]["label"]),
                         model=getattr(provider, "model_name", ""))
                    stats["polarity_written"] += 1
            except Exception as exc:    # 失败不伪装:零落库,留痕
                stats["polarity_error"] = str(exc)[:200]

        # ---- ②缺口通道建议(白名单机器闸)
        gaps: list[tuple[str, str]] = []
        for cid, c in sorted(claims.items()):
            f = factors.get(cid)
            status = f["status"] if f else c["status"]
            s = len(f["support_roots"]) if f else 0
            a_hit = bool(set(f["support_roots"]) & auth) if f else False
            tried = f["tried"] if f else bool(c["unreachable"])
            g = gap_type_of(status, s, a_hit, tried)
            if g:
                gaps.append((cid, g))
        stats["channel_total"] = len(gaps)
        if gaps:
            user = json.dumps(
                {"缺口": [{"claim_id": cid, "gap_type": g,
                           "claim": _brief(claims[cid])} for cid, g in gaps],
                 "通道白名单": sorted(wl)},
                ensure_ascii=False)
            try:
                out = call_structured(provider, stage="chain_channel",
                                      system=CHANNEL_PROMPT, user=user,
                                      model_cls=ChannelOut, retry=1)
                for it in out.items:
                    if it.claim_id not in {cid for cid, _ in gaps}:
                        stats["channel_invalid"] = stats.get(
                            "channel_invalid", 0) + 1
                        continue
                    if it.channel not in wl:
                        stats["channel_dropped"] += 1   # 白名单外:丢弃不伪装
                        continue
                    c = claims[it.claim_id]
                    f = factors.get(it.claim_id)
                    status = f["status"] if f else c["status"]
                    g = next(x for cid, x in gaps if cid == it.claim_id)
                    _put(conn, sid=_sid("channel", it.claim_id),
                         event_id=event_id, kind="channel",
                         claim_id=it.claim_id, hypothesis_id="",
                         value=it.channel, rationale=it.rationale[:60],
                         fingerprint=channel_fp(c["text"], status, g),
                         model=getattr(provider, "model_name", ""))
                    stats["channel_written"] += 1
            except Exception as exc:
                stats["channel_error"] = str(exc)[:200]

        # ---- ③材料概括 ev_summary(卷面改造 §五;材料集与装配侧同判据=
        #      裁定在案∧非信封种子;失败=旁车缺项=装配侧词典短语兜底,
        #      不伪装 LLM 不留空)
        drawn: dict[str, dict] = {}
        for r in led.conn.execute(_EV_SQL, (event_id,)):
            if (r["ruling"] not in DRAW_RULINGS
                    or (r["platform"] or "") == SEED_PLATFORM):
                continue
            drawn.setdefault(r["evidence_id"], {
                "title": r["title"] or "", "content": r["content"] or ""})
        stats["ev_summary_total"] = len(drawn)
        if drawn:
            user = json.dumps(
                {"材料": [{"evidence_id": eid, "title": v["title"],
                           "content": _clip_text(v["content"], 300)}
                          for eid, v in sorted(drawn.items())]},
                ensure_ascii=False)
            try:
                out = call_structured(provider, stage="chain_ev_summary",
                                      system=EV_SUMMARY_PROMPT, user=user,
                                      model_cls=SummaryOut, retry=1)
                for it in out.items:
                    v = drawn.get(it.evidence_id)
                    s = (it.summary or "").strip()
                    if v is None or not s:
                        stats["ev_summary_invalid"] = \
                            stats.get("ev_summary_invalid", 0) + 1
                        continue
                    _put(conn, sid=_sid("ev_summary", it.evidence_id),
                         event_id=event_id, kind="ev_summary",
                         claim_id=it.evidence_id, hypothesis_id="",
                         value=s[:60], rationale="",
                         fingerprint=ev_summary_fp(v["title"], v["content"]),
                         model=getattr(provider, "model_name", ""))
                    stats["ev_summary_written"] += 1
            except Exception as exc:    # 失败不伪装:零落库,留痕
                stats["ev_summary_error"] = str(exc)[:200]

        # ---- ④论点分组 grouping(仅论证区存在才跑;人工覆写=跳过=人工优先)
        #      + ⑤章首研判 narrative(输入带分组结果;失败=无该段/无串讲)。
        #      论证区判据=pick_argument(与装配侧同判据函数,防漂移)。
        status_by_id = {cid: (factors[cid]["status"] if cid in factors
                              else c["status"])
                        for cid, c in claims.items()}
        standings = {s["hypothesis_id"]: s
                     for s in led.hypothesis_standings(event_id)}
        # 资格收紧(P4)两侧同源:与 chain_graph 装配侧同判据——legacy 镜像
        # 行(指纹仍有效)反查解读型集合,排除出已证实计数
        routed_ids: set[str] = set()
        for row in conn.execute(
                "SELECT legacy_claim_id, fingerprint FROM insights"
                " WHERE event_id=? AND legacy_claim_id!='' AND kind IN"
                " ('signal_interpretation','gap_hint','signal_meta',"
                "'detector_field')", (event_id,)).fetchall():
            crow = led.claim_row(row["legacy_claim_id"])
            if (crow is not None
                    and legacy_fp(crow["text"]) == row["fingerprint"]):
                routed_ids.add(row["legacy_claim_id"])
        top, _runners = pick_argument(
            [{"hypothesis_id": h["hypothesis_id"], "label": h["label"],
              "score": standings.get(h["hypothesis_id"], {}).get("score", 0)}
             for h in hyp_rows],
            hc_map, status_by_id, exclude_ids=routed_ids)
        groups_for_nar: list[dict] = []   # ⑦融合步同消费(top 分支内外皆可用)
        if top is not None:
            hid = top["hypothesis_id"]
            members = top["member_ids"]
            label = top["label"]
            stats["grouping_total"] = 1
            manual_g = load_manual_grouping(seeds_path).get(label)
            if manual_g:
                stats["grouping_manual_skipped"] = 1
            else:
                user = json.dumps(
                    {"假设": label,
                     "命题": [{"claim_id": cid,
                               "gist": claims[cid].get("gist") or "",
                               "text": claims[cid]["text"],
                               "status": status_by_id.get(cid)}
                              for cid in members]},
                    ensure_ascii=False)
                try:
                    out = call_structured(provider, stage="chain_grouping",
                                          system=GROUPING_PROMPT, user=user,
                                          model_cls=GroupingOut, retry=1)
                    cand = [{"name": g.name.strip(),
                             "members": [m for m in g.members
                                         if isinstance(m, str)]}
                            for g in out.groups]
                    if (validate_grouping(cand, members)
                            and all(len(g["name"]) <= 20 for g in cand)):
                        _put(conn, sid=_sid("grouping", "", hid),
                             event_id=event_id, kind="grouping",
                             claim_id="", hypothesis_id=hid,
                             value=json.dumps(cand, ensure_ascii=False,
                                              sort_keys=True),
                             rationale="",
                             fingerprint=grouping_fp(label, members),
                             model=getattr(provider, "model_name", ""))
                        stats["grouping_written"] = 1
                    else:
                        stats["grouping_invalid"] = 1   # 漏/多/虚构→拒收退机械平铺
                except Exception as exc:
                    stats["grouping_error"] = str(exc)[:200]
            # narrative 输入带论点结构(人工>旁车指纹有效;无=平铺输入,合法)
            if manual_g:
                d2c = {c["draft_id"]: cid for cid, c in claims.items()}
                groups_for_nar = [{"name": g["name"],
                                   "members": [d2c[m] for m in g["members"]
                                               if m in d2c]}
                                  for g in manual_g]
            else:
                for row in conn.execute(
                        "SELECT * FROM insights WHERE event_id=? AND kind=?",
                        (event_id, "grouping")).fetchall():
                    if (row["hypothesis_id"] == hid
                            and row["fingerprint"] == grouping_fp(label,
                                                                  members)):
                        try:
                            groups_for_nar = json.loads(row["value"])
                        except ValueError:
                            groups_for_nar = []
                        break
            stats["narrative_total"] = 1
            try:
                user = json.dumps(
                    {"假设": label, "论点": groups_for_nar,
                     "命题": [{"claim_id": cid,
                               "gist": claims[cid].get("gist") or "",
                               "status": status_by_id.get(cid)}
                              for cid in members]},
                    ensure_ascii=False)
                out = call_structured(provider, stage="chain_narrative",
                                      system=NARRATIVE_PROMPT, user=user,
                                      model_cls=NarrativeOut, retry=1)
                ch = (out.chapter or "").strip()
                wk = (out.walkthrough or "").strip()
                if ch and wk:
                    _put(conn, sid=_sid("narrative", "", hid),
                         event_id=event_id, kind="narrative",
                         claim_id="", hypothesis_id=hid,
                         value=json.dumps({"chapter": ch[:100],
                                           "walkthrough": wk[:200]},
                                          ensure_ascii=False, sort_keys=True),
                         rationale="",
                         fingerprint=narrative_fp(label, members),
                         model=getattr(provider, "model_name", ""))
                    stats["narrative_written"] = 1
                else:
                    stats["narrative_invalid"] = 1
            except Exception as exc:
                stats["narrative_error"] = str(exc)[:200]

        # ---- ⑥解读锚点 interpretation(§六.2;LLM:解读原文+信封结构化
        #      字段+链上事实命题 → {conclusion,basis,rationale};机器闸=
        #      conclusion 在台账∧basis 真实在链,幻觉锚→拒收退挡除;落
        #      insights(指纹=解读文本+basis 集);锚上后源行 received→
        #      anchored(机械迁移,禁 now——状态由锚定事件驱动非时钟))
        ups = [dict(r) for r in conn.execute(
            "SELECT * FROM insights WHERE event_id=? AND kind=? AND source=?"
            " AND status=? ORDER BY insight_id",
            (event_id, "signal_interpretation", "upstream",
             "received")).fetchall()]
        stats["interp_total"] = len(ups)
        if ups:
            user = json.dumps(
                {"解读": [{"source_id": r["insight_id"],
                           "text": r["value"]} for r in ups],
                 "信封": _envelope_fields(led, event_id),
                 "台账命题": [{"claim_id": cid,
                               "gist": claims[cid].get("gist") or "",
                               "text": claims[cid]["text"],
                               "status": status_by_id.get(
                                   cid, claims[cid]["status"])}
                              for cid in sorted(claims)]},
                ensure_ascii=False)
            try:
                out = call_structured(provider, stage="chain_interp",
                                      system=INTERP_PROMPT, user=user,
                                      model_cls=InterpOut, retry=1)
                for it in out.items:
                    src = next((r for r in ups
                                if r["insight_id"] == it.source_id), None)
                    c = claims.get(it.conclusion)
                    basis = [b for b in dict.fromkeys(it.basis or [])
                             if isinstance(b, str)]
                    ok_basis = bool(basis) and all(b in claims for b in basis)
                    if (src is None or c is None or not ok_basis
                            or it.conclusion in basis):
                        stats["interp_invalid"] = \
                            stats.get("interp_invalid", 0) + 1
                        continue    # 幻觉锚/自锚:拒收退挡除(机器闸)
                    _put(conn, sid=f"interp:{it.source_id}",
                         event_id=event_id, kind="interpretation",
                         claim_id=it.conclusion,
                         value=json.dumps({"source": it.source_id,
                                           "conclusion": it.conclusion,
                                           "basis": basis,
                                           "rationale": (it.rationale or "")[:80]},
                                          ensure_ascii=False, sort_keys=True),
                         rationale="", fingerprint=interp_fp(src["value"], basis),
                         model=getattr(provider, "model_name", ""),
                         source="internal", status="anchored")
                    conn.execute("UPDATE insights SET status='anchored'"
                                 " WHERE insight_id=?", (it.source_id,))
                    conn.commit()
                    stats["interp_written"] += 1
            except Exception as exc:    # 失败不伪装:零落库,留痕
                stats["interp_error"] = str(exc)[:200]
        # ---- ⑦命题级多源印证 ev_fusion(批2 用户复查修正):触发=同一事实
        #      命题的支持材料≥fusion_min_members 且独立根同样达阈值;
        #      信封种子/sensitive/反证/时效刷新全排除。旧组级融合退役:
        #      主语桶把异质事实串成材料清单,不是融合;反证混入支撑卡亦违义。
        #      六重机器闸→insights kind=fusion;value 明写 claim+members,
        #      指纹=成员集+各成员 ev_summary_fp,sid=claim+成员集 hash 幂等。
        stats["fusion_total"] = 0
        stats["fusion_written"] = 0
        stats["fusion_invalid"] = 0
        stats["fusion_error"] = ""
        fm_raw = rules.get("fusion_min_members", 2)
        try:
            fusion_min = int(fm_raw) if fm_raw is not None else 2
        except (TypeError, ValueError):
            fusion_min = 2
        fusion_todo = fusion_scope(led, event_id, claims, top=top,
                                   groups_for_nar=groups_for_nar,
                                   routed_ids=routed_ids,
                                   fusion_min=fusion_min)
        stats["fusion_total"] = len(fusion_todo)
        if fusion_todo:
            user = json.dumps(
                {"命题": [{"claim_id": c["claim_id"],
                           "gist": c["gist"], "text": c["text"],
                           "材料": [{"evidence_id": eid, "root": m["root"],
                                     "title": m["title"],
                                     "content": _clip_text(m["content"], 300)}
                                    for eid, m in sorted(mats.items())]}
                          for c, mats in fusion_todo]},
                ensure_ascii=False)
            try:
                out = call_structured(provider, stage="chain_fusion",
                                      system=FUSION_PROMPT, user=user,
                                      model_cls=FusionOut, retry=1)
                for it in out.items:
                    tgt = next(((c, mats) for c, mats in fusion_todo
                                if c["claim_id"] == it.claim), None)
                    if tgt is None:
                        stats["fusion_invalid"] += 1
                        continue
                    claim_meta, gmats = tgt
                    reason = validate_fusion_item(it.text, it.members, gmats,
                                                  min_members=fusion_min)
                    if reason:
                        stats["fusion_invalid"] += 1
                        continue
                    ms = sorted(set(it.members))
                    fps = {eid: ev_summary_fp(gmats[eid]["title"],
                                              gmats[eid]["content"])
                           for eid in ms}
                    cid = claim_meta["claim_id"]
                    _put(conn, sid="fusion:" + hashlib.sha256(
                            json.dumps([cid, *ms]).encode("utf-8")).hexdigest()[:16],
                         event_id=event_id, kind="fusion", claim_id=cid,
                         hypothesis_id="",
                         value=json.dumps({"claim": cid,
                                           "text": it.text.strip(),
                                           "members": ms},
                                          ensure_ascii=False, sort_keys=True),
                         rationale="", fingerprint=fusion_fp(ms, fps),
                         model=getattr(provider, "model_name", ""))
                    stats["fusion_written"] += 1
            except Exception as exc:    # 失败不伪装:零落库,留痕
                stats["fusion_error"] = str(exc)[:200]

    finally:
        conn.close()
    return stats


def _brief(c: dict) -> dict:
    """命题 brief(gist 优先,照 gist 设计纪律:对象与关键数字不变形)。"""
    return {"gist": c.get("gist") or "", "text": c["text"], "status": c["status"]}


_EV_SQL = (
    "SELECT ce.claim_id, ce.ruling, e.evidence_id, e.title, e.content,"
    " e.platform FROM claim_evidence ce JOIN evidence e"
    " ON e.evidence_id=ce.evidence_id WHERE e.event_id=?"
    " ORDER BY e.evidence_id")


_FUSION_EV_SQL = (
    "SELECT ce.claim_id, ce.ruling, e.evidence_id, e.title, e.content,"
    " e.platform, e.sensitive, e.author_type, e.lineage_root"
    " FROM claim_evidence ce JOIN evidence e ON e.evidence_id=ce.evidence_id"
    " WHERE e.event_id=? ORDER BY ce.claim_id, e.evidence_id")


def _clip_text(text: str, n: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[:n] + "…"


def fusion_scope(led, event_id: str, claims: dict, *, top=None,
                 groups_for_nar=None, routed_ids=(),
                 fusion_min: int) -> list[tuple[dict, dict]]:
    """命题级多源印证触发清单(⑦融合步与 _fusion_probe 同轴)。

    返回 [(命题, 支持材料)]:命题={claim_id,gist,text,status};材料=
    {eid:{title,content,party,root}}。准入=同一**事实命题**的「支持」材料
    ≥fusion_min 件且独立根≥fusion_min;信封种子/sensitive/反证/时效刷新
    全部排除。反证不融是语义红线:红边必须独立可见,不能混入支撑性融合卡。
    routed_ids 解读型命题同样排除。top/groups_for_nar 参数保留为旧探针调用
    兼容口,不参与判定——融合对象从组级正式退役为命题级。
    """
    routed = set(routed_ids or ())
    by_claim: dict[str, dict[str, dict]] = {}
    for r in led.conn.execute(_FUSION_EV_SQL, (event_id,)):
        cid = r["claim_id"]
        if (cid not in claims or cid in routed or r["ruling"] != "支持"
                or (r["platform"] or "") == SEED_PLATFORM
                or bool(r["sensitive"])):
            continue
        by_claim.setdefault(cid, {})[r["evidence_id"]] = {
            "title": r["title"] or "", "content": r["content"] or "",
            "party": (r["author_type"] or "") == "当事方",
            "root": r["lineage_root"] or "(无谱系)"}
    todo: list[tuple[dict, dict]] = []
    for cid, mats in sorted(by_claim.items()):
        # 同源谱系折算:每个 lineage_root 只取 evidence_id 最小的一件代表;
        # 十家转载仍是一根,「N路独立材料」的 N 不得按件虚高。
        root_rep: dict[str, tuple[str, dict]] = {}
        for eid, m in sorted(mats.items()):
            root_rep.setdefault(m["root"], (eid, m))
        independent = {eid: m for eid, m in root_rep.values()}
        if len(independent) < fusion_min:
            continue
        c = claims[cid]
        todo.append(({"claim_id": cid, "gist": c.get("gist") or "",
                      "text": c["text"], "status": c["status"]}, independent))
    return todo


def json_items(todo, claims, hyp_rows) -> str:
    """极性 prompt user 件:假设种子(语义判据)+成员命题(gist 优先)。"""
    hyp_by_id = {h["hypothesis_id"]: h for h in hyp_rows}
    return json.dumps(
        {"假设": [{"hypothesis_id": h, "label": hyp_by_id[h]["label"],
                   "description": hyp_by_id[h]["description"] or ""}
                  for h in sorted({x[1]["hypothesis_id"] for x in todo})],
         "命题": [{"claim_id": cid, "hypothesis_id": h["hypothesis_id"],
                   "gist": claims[cid].get("gist") or "",
                   "text": claims[cid]["text"],
                   "status": claims[cid]["status"]}
                  for cid, h in sorted(todo, key=lambda x: (x[0], x[1]["hypothesis_id"]))]},
        ensure_ascii=False)


def _legacy_kind(text: str, cls: str) -> str:
    """解读类→insights kind(元信息再机械细分:种子标签→signal_meta,
    其余→detector_field;判据=词面,确定性)。"""
    if cls == "interpretation":
        return "signal_interpretation"
    if cls == "gap_hint":
        return "gap_hint"
    return "signal_meta" if "种子标签" in (text or "") else "detector_field"


def migrate_legacy_insights(led, conn: sqlite3.Connection, event_id: str,
                            rules: dict) -> int:
    """P9 存量迁移(§二.2;chain-gen 首步,机械零 LLM):主库 claims 中
    模式表命中解读/缺口/元信息三类的存量条目→复制入 insights(source=
    legacy,legacy_claim_id 回指,指纹锚定原文本)。幂等:legacy:{claim_id}
    已存在跳过(含状态已推进的行,不覆盖);**主库零改动**(P9 红线)。
    返回本次新迁移条数。"""
    n = 0
    for r in led.all_claims(event_id):
        cls = classify_claim(r["text"], rules)
        if not cls:
            continue
        sid = f"legacy:{r['claim_id']}"
        if conn.execute("SELECT 1 FROM insights WHERE insight_id=?",
                        (sid,)).fetchone() is not None:
            continue    # 幂等
        _put(conn, sid=sid, event_id=event_id, kind=_legacy_kind(r["text"], cls),
             claim_id=r["claim_id"], value=r["text"],
             fingerprint=legacy_fp(r["text"]), model="",
             source="legacy", status="received", legacy_claim_id=r["claim_id"])
        n += 1
    return n


def extract_upstream_insights(led, conn: sqlite3.Connection, event_id: str,
                              rules: dict) -> int:
    """上游解读机械抽取(§六.1,零 LLM):信封 projectSummary(tender)/
    summary(rumor)按句切分,模式表命中三类的句子→insights(source=
    upstream,value=句子原文,指纹锚定原文)。幂等:REPLACE 同 id;返回
    在案条数(含幂等重写)。"""
    raw = led.event_envelope_json(event_id)
    if not raw:
        return 0
    env = json.loads(raw)
    t = env.get("tender") or {}
    r = env.get("rumor") or {}
    text = str(t.get("projectSummary") or t.get("project_summary")
               or r.get("summary") or "")
    if not text.strip():
        return 0
    n = 0
    for i, s in enumerate(x.strip() for x in re.split(r"[。;;\n]", text)):
        if not s:
            continue
        cls = classify_claim(s, rules)
        if not cls:
            continue
        _put(conn, sid=f"upstream:{event_id}:{i}", event_id=event_id,
             kind=_legacy_kind(s, cls), claim_id="", value=s,
             fingerprint=legacy_fp(s), model="",
             source="upstream", status="received")
        n += 1
    return n


def _envelope_fields(led, event_id: str) -> dict:
    """信封结构化字段(§六.2 锚点输入;缺=如实少键,不伪装)。"""
    raw = led.event_envelope_json(event_id)
    if not raw:
        return {}
    env = json.loads(raw)
    out = {"kind": env.get("kind", "")}
    for src, keys in ((env.get("tender") or {},
                       ("projectName", "project_summary", "amountYuan",
                        "amount_yuan", "purchaser", "buyer", "sourceUrl",
                        "source_url")),
                      (env.get("rumor") or {},
                       ("subject_name", "summary", "source_url"))):
        for k in keys:
            v = src.get(k)
            if v not in (None, ""):
                out[k] = str(v)
    return out


def reborn_drafts(conn: sqlite3.Connection, led, event_id: str,
                  rules: dict) -> list[dict]:
    """线索转生清单(§六.3,机械):扫缺口建议类 insights(status!=derived)
    →去壳命题草稿(gap_hint_strip 前缀剥离)。legacy 行指纹对主库原文本
    重算比对(漂移=弃用不进清单);确定性按 insight_id 排序。"""
    out: list[dict] = []
    for row in conn.execute(
            "SELECT * FROM insights WHERE event_id=? AND kind='gap_hint'"
            " ORDER BY insight_id", (event_id,)).fetchall():
        if (row["status"] or "") == "derived":
            continue
        src_claim = row["legacy_claim_id"] or ""
        if src_claim:    # legacy 行:锚定主库原文本,漂移即弃用
            crow = led.claim_row(src_claim)
            if crow is None or legacy_fp(crow["text"]) != row["fingerprint"]:
                continue
        text = strip_shell(row["value"] or "", rules)
        for pfx in sorted(rules.get("gap_hint_strip") or (),
                          key=len, reverse=True):
            if text.startswith(pfx):
                text = text[len(pfx):].strip()
                break
        if not text:
            continue
        out.append({"insight_id": row["insight_id"], "text": text,
                    "source": row["source"] or "internal"})
    return out


def register_reborn(kb_path: str | Path, led, event_id: str, rules: dict,
                    round_no: int) -> list[str]:
    """resume 转生注册(§六.3;investigate --event finalize 前调用):清单
    逐条 add_claim 派生口入账(draft.reborn_from=insight_id=转生谱系),
    回写源行 status received→derived(机械迁移)。幂等:已 derived 跳过。
    返回本次新注册 claim_id 列表。"""
    conn = open_insights_rw(kb_path)
    try:
        drafts = reborn_drafts(conn, led, event_id, rules)
        if not drafts:
            return []
        n_existing = led.conn.execute(   # RB 序号跨会话连续(确定性;查主库)
            "SELECT COUNT(*) FROM claims WHERE claim_id LIKE ? AND reborn_from!=''",
            (event_id + ":%",)).fetchone()[0] if _has_reborn_col(led) else 0
        registered: list[str] = []
        for i, d in enumerate(drafts, start=n_existing + 1):
            cid = led.add_claim(event_id, {
                "id": f"RB{i}", "text": d["text"], "category": "事实",
                "section_index": 2, "priority": 3,
                "priority_reason": "由解读转生的线索命题",
                "reborn_from": d["insight_id"]}, round_no)
            conn.execute("UPDATE insights SET status='derived'"
                         " WHERE insight_id=?", (d["insight_id"],))
            registered.append(cid)
        conn.commit()
        return registered
    finally:
        conn.close()


def _has_reborn_col(led) -> bool:
    """老主库缺 reborn_from 列(未过 connect() 迁移)时序号基线退 0
    (add_claim 仍会因缺列报错——老库先走一次 connect() 迁移,如实暴露)。"""
    try:
        led.conn.execute("SELECT reborn_from FROM claims LIMIT 1").fetchone()
        return True
    except sqlite3.OperationalError:
        return False
