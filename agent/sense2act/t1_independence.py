"""T1 根独立性查检(手术包件 5,2026-10-22)。

判决前内容级独立性检查:同 claim 的支持证据,两根文本近重复(逐字级
转载/同文多挂)→ 折一根——独立性判定从"数根"升级"查形态"
(independence-aware corroboration;治发现四残余"数根不查形态")。

机制:根文本 simhash(store.ledger 同款 character-bigram 64-bit,采集去重
同源);根组两两距离 ≤ 阈值 → 同组折一,每组保留排序首个(确定性,判决
侧与 ESS 侧同输入同函数=口径一致,credibility 口径测试自动延续)。

阈值语义(保守上限):沿用 SIMHASH_NEAR_DUP_DIST=1(2026-10-05 定标:
转载微调距离 0-3,"中标 vs 废标"语义相反标题距离仅 2)——**只折几乎
逐字相同的根**;语义改写级折并为演进项,宁漏折(慢)不可误折(把独立
根折掉=漏报侧)。与域名折算正交:域名折算=谱系层先验(十家同域=一根),
本检查=跨域内容级事后再验一道(不同域名的同文转载)。

消费点两处(口径一致):s6_verdict 判决梯数根前折;credibility.collect_factors
ESS 因子采集同折(--t1-independence on 时传阈值,off=None 现行为)。

开关:--t1-independence on|off 默认 off;行为参数入 META_KEYS;重录波
全开录制后转正(照 predicate_form 先例)。第一版只折支持根(反方折并
为演进项)。
"""
from __future__ import annotations

from .store.ledger import SIMHASH_NEAR_DUP_DIST, simhash, simhash_distance

__all__ = ["distinct_support_roots", "SIMHASH_NEAR_DUP_DIST"]


def distinct_support_roots(roots: list[str], content_of,
                           threshold: int = SIMHASH_NEAR_DUP_DIST) -> list[str]:
    """内容级独立支持根:近重复根折并;返回保留根列表(排序确定性)。

    content_of: root -> 文本(查不到文本的根=不可验,保守单列一组,
    永不因缺文本被折掉——失败不伪装)。
    """
    kept: list[str] = []
    reps: list[str] = []      # 与 kept 对齐:各保留组的指纹(""=无文本组)
    for r in sorted(roots):
        text = content_of(r)
        fp = simhash(text) if text else ""
        merged = False
        if fp:
            for rep in reps:
                if rep and simhash_distance(fp, rep) <= threshold:
                    merged = True
                    break
        if not merged:
            kept.append(r)
            reps.append(fp)
    return kept
