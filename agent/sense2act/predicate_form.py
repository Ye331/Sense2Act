"""谓词形态标注(手术包件 4,2026-10-22 施工;前置硬闸门=E2 三判据全过)。

S1b 出生时对每条 claim 标注 predicate_form{beyond_single_doc,
predicate_type, grounding_quotes};消费点=s6 单源例外判定,**加法保守
合并**:blocklist ∨ LLM——LLM 只能加拦不能解除(零回归)。

三纪律(方案 §十一 / 手术包清单件 4):
- 代码验票:引文锚点 _norm_ws 归一化子串闸(必须是 claim 原文片段,幻觉
  引文整条作废)+ predicate_type 封闭枚举 + beyond_single_doc=true 须至少
  1 条过闸引文;任何失败 → None(回退 blocklist 判定,**永不 fail-open**);
- 仲裁:corpus_bound 自证路径优先,谓词门不适用于自证例外(s6 分支顺序
  既有结构;判决 note 注明来源);
- blocklist 降级为兜底,标注稳定后按误伤数据逐词退役(数据驱动,后置)。

兼容:ClaimDraft.predicate_form 可选(dict 原始透传,畸形不炸 S1b),
老 fixture/录制件无字段 → None → 现行为(eval 零迁移);开关
`--predicate-form` 默认 off(行为参数入 META_KEYS,重录波以全开录制)。

E2 证据(《实施方案/E2谓词形态标注-20261022.md》):查全 0.824→1.000 /
查准 0.875→1.000 / 改写稳定 11/11;改写集回归=tests/fixtures/
predicate_stability.json × eval/predicate_e2/annotations.json。
"""
from __future__ import annotations

# 封闭枚举(E2 标注与施工共用;扩枚举须同步 E2 测试集并重跑三判据)
PREDICATE_TYPES = frozenset({
    "comparison",      # 比较(高于历史水平/相比去年)
    "dependency",      # 依赖/独立(独立于、不依赖)
    "persistence",     # 持续性(持续成立、延续性)
    "cross_entity",    # 跨主体(多家均/同市另有)
    "conjunction",     # 合取(且…也有…)
    "self_contained",  # 单文件可证/自证
})


def _norm_ws(text: str) -> str:
    """空白归一化(子串闸比较用):去全部空白——中文文本空白无语义,
    引文跨换行/含多余空格时与原文仍应等价。"""
    return "".join((text or "").split())


def validate(raw: dict | None, claim_text: str) -> dict | None:
    """三字段验票 → 归一化 dict;任何失败返回 None(回退 blocklist)。

    归一化:grounding_quotes 折叠空白后须为 claim 文本子串(防幻觉引文);
    beyond_single_doc=true 时须至少 1 条过闸引文(加拦必须带锚点)。
    """
    if not isinstance(raw, dict):
        return None
    beyond = raw.get("beyond_single_doc")
    if not isinstance(beyond, bool):
        return None
    ptype = raw.get("predicate_type") or ""
    if ptype and ptype not in PREDICATE_TYPES:
        return None
    quotes = raw.get("grounding_quotes")
    if quotes is None:
        quotes = []
    if not isinstance(quotes, list):
        return None
    hay = _norm_ws(claim_text)
    normed: list[str] = []
    for q in quotes:
        if not isinstance(q, str) or not q.strip():
            return None
        nq = _norm_ws(q)
        if nq not in hay:
            return None
        normed.append(nq)
    if beyond and not normed:
        return None
    return {"beyond_single_doc": beyond, "predicate_type": ptype,
            "grounding_quotes": normed}


def merge(blocklist_hit: bool, form: dict | None) -> bool:
    """加法保守合并:blocklist ∨ LLM——LLM 只能加拦不能解除(零回归)。"""
    return bool(blocklist_hit) or bool(
        form and form.get("beyond_single_doc"))
