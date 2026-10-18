"""查询词族重建(路径 B 探索器 v1 的确定性换词策略)。

背景(2026-09-27 live 实测发现):S2 的搜索提示与证据库的关键词批次字面不重叠时,
路径 A 一跳扑空、后续轮次全部空转。按架构,补救属于路径 B 探索器:
"未达标换词,深度+1(<=3)"。本模块是确定性 v1:从缺口文本重建候选查询,
不消耗 LLM、回放可复现;验收自评(结果是否满足验收标准)与真实网页抓取
仍属真探索器(P1)。

生成策略(分深度层,层内"跨源增强"优先):
  d0 = S2 的搜索提示(直觉,原样先试);
  d1 = 2 克词(同时出现在提示/主张里的优先——跨源词大概率是真主题);
  d2 = 3 克词(同样跨源优先);
  d3 = 4 克词。
轨迹纪律:每次尝试全部记录——高频命中的词族应沉淀进平台规则表
(B->A 迁移的原料)。
"""
from __future__ import annotations

import re

from ..store.ledger import normalize_text

_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_CJK = re.compile(r"[一-鿿]{2,}")
_ALNUM = re.compile(r"[a-zA-Z0-9_-]{2,}")
_STOP = {
    "该院", "以及", "或者", "是否", "对于", "关于", "进行", "一个",
    "这个", "那个", "等等", "有关", "相关", "情况", "问题",
}


def _grams(text: str, n: int) -> list[str]:
    """从文本切出 n 克词(字母数字词只在 n==2 层作为精确词产出一次)。"""
    out: list[str] = []
    for tok in _SPLIT.split(normalize_text(text)):
        if not tok or re.fullmatch(r"[\d.]+", tok):
            continue
        if _ALNUM.fullmatch(tok):
            if n == 2:
                out.append(tok.casefold())
            continue
        for run in _CJK.findall(tok):
            for i in range(len(run) - n + 1):
                gram = run[i : i + n]
                if gram not in _STOP:
                    out.append(gram)
    return out


def text_overlap(a: str, b: str, min_shared: int = 2) -> bool:
    """词面重合:两段文本共享 >=min_shared 个 2 克词元即算相关。

    用于 S5 关联的"宽进"通道(实体名重合之外的补充)——行业级模糊信号的
    证据与 claim 常无实体名交集,但词面重合。方向过滤交给 S6。
    """
    ga = set(_grams(a, 2))
    gb = set(_grams(b, 2))
    return len(ga & gb) >= min_shared


def candidate_queries(
    hints: list[str] | None, gap: str, claim: str = "",
    max_depth: int = 3, cap: int = 20,
) -> list[tuple[int, str]]:
    """生成带深度标签的候选查询序列(有序、去重、封顶)。

    跨源增强:某词同时出现在 缺口 与 提示/主张 中,排在本层最前
    ——它大概率是调查主题而非切词噪声。
    """
    out: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(depth: int, q: str) -> None:
        q = normalize_text(q).strip()
        key = q.casefold()
        if q and key not in seen:
            seen.add(key)
            out.append((depth, q))

    for h in hints or []:
        add(0, h)

    ctx = normalize_text(" ".join(hints or []) + " " + claim).casefold()
    for depth, n in ((1, 2), (2, 3), (3, 4)):
        if depth > max_depth:
            break
        grams = _grams(gap, n)
        if not grams:
            continue
        boosted = [g for g in grams if g in ctx]
        for g in boosted:
            add(depth, g)
        for g in grams:
            add(depth, g)
    return out[:cap]
