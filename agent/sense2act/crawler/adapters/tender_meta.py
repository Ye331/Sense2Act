"""招标域共享元数据:阶段分类/商机语义/类别词纪律/统一过滤排序。

从 tianjin_zfcg 抽出(ccgp 接入时复用);纯确定性,零网络,零 LLM。
供各招标源适配器与采集成果转储共用。
"""
from __future__ import annotations

import re

from ..base import AdapterResult

# 类别词:招标公告标题几乎条条含之,单独命中零选择性(如只命中"建设"的
# 无关公告会带权威根身份污染台账)。过滤只认信息词;全类别词查询拒绝泛匹配。
GENERIC_TOKENS = frozenset({
    "项目", "公告", "采购", "工程", "建设", "招标", "中标", "成交",
    "询价", "更正", "终止", "公示", "结果", "磋商", "谈判",
})

# 招标生命周期阶段(确定性关键词分类;一条标题可含多词,取"最靠尾部"者——
# 公告类型词位于标题尾,如"合同能源管理项目公开招标公告"应判招标而非合同)
STAGE_RULES: dict[str, tuple[str, ...]] = {
    "投诉处理": ("投诉",),
    "监督处罚": ("行政处罚", "监督检查", "监督考核", "考核"),
    "采购意向": ("采购意向", "意向公开"),
    "需求公示": ("征求意见",),
    "单一来源": ("单一来源",),
    "资格预审": ("资格预审",),
    "更正变更": ("更正", "变更"),
    "终止废标": ("终止", "废标"),
    "中标成交": ("中标", "成交", "结果公告"),
    "合同签订": ("合同",),
    "招标采购": ("招标", "磋商", "谈判", "询价", "竞争性"),
    "执行情况": ("执行情况",),
}

# 商机语义(面向企业投标需求):阶段 -> 商机状态
#   商机进行中=可跟进的投标机会(意向/预审/招标/更正);
#   商机已关闭=结果与合同(竞争情报:谁中了什么);
#   非商机=无投标机会或与投标无关(单一来源无竞争;投诉处罚/执行情况/新闻)。
OPPORTUNITY_MAP: dict[str, str] = {
    "采购意向": "商机进行中", "需求公示": "商机进行中", "资格预审": "商机进行中",
    "招标采购": "商机进行中", "更正变更": "商机进行中",
    "中标成交": "商机已关闭", "合同签订": "商机已关闭",
    "单一来源": "非商机", "终止废标": "非商机", "投诉处理": "非商机",
    "监督处罚": "非商机", "执行情况": "非商机", "其他": "非商机",
}


def parse_tender_meta(title: str) -> dict:
    """标题 -> {阶段, 商机状态, 项目编号, 采购人}(纯确定性,失败字段为空串)。

    阶段:取全部阶段关键词中"最后出现"者的分组(公告类型词在标题尾);
    商机状态:阶段 -> 进行中(可投标)/已关闭(竞争情报)/非商机;
    项目编号:『项目编号:XXX』捕获到空白/右括号为止;
    采购人:仅识别"前缀重复"形态(采购人名+项目名以同一主体开头)——
    无把握时返回空串,不给错值(失败不伪装);列表页自带采购人的源
    (如 ccgp)应优先用列表字段,不依赖此启发式。
    """
    text = str(title or "")
    best_pos, stage = -1, "其他"
    for group, keywords in STAGE_RULES.items():
        for kw in keywords:
            pos = text.rfind(kw)
            if pos > best_pos:
                best_pos, stage = pos, group
    m = re.search(r"项目编号[:：]\s*([^\s)）,，、]+)", text)
    project_no = m.group(1) if m else ""
    buyer = ""
    for size in range(min(30, len(text) // 2), 3, -1):
        if text[:size] == text[size:size * 2]:
            buyer = text[:size]
            break
    return {"stage": stage, "opportunity": OPPORTUNITY_MAP[stage],
            "project_no": project_no, "buyer": buyer}


def filter_by_query(items: list[dict], query: str, limit: int,
                    label: str = "列表", window_note: str = "") -> AdapterResult:
    """统一过滤排序(各招标源共用;确定性,回放安全)。

    纪律:空查询拒绝全量返回;类别词不计入命中;命中词数多者优先,
    同分发布时间新者优先(两趟稳定排序)。
    """
    raw = str(query or "").strip()
    if not raw:
        # 空/纯空白查询不得伪装成成功并全量返回(权威根证据污染台账)
        return AdapterResult(
            status="empty",
            message=f"查询词为空,拒绝全量返回(扫查{label}共 {len(items)} 条)")
    informative = [t for t in raw.split() if t not in GENERIC_TOKENS]
    if not informative:
        return AdapterResult(
            status="empty",
            message=f"查询词『{raw}』均为类别词(项目/公告/采购/建设等),"
                    "单独命中零选择性,拒绝泛匹配;请细化关键词")

    def hits(item: dict) -> int:
        title = item["title"].casefold()
        return sum(1 for t in informative if t.casefold() in title)

    matched = [(hits(i), i) for i in items if hits(i) > 0]
    if not matched:
        note = f"({window_note})" if window_note else ""
        return AdapterResult(
            status="empty",
            message=f"扫查{label}共 {len(items)} 条,没有匹配关键词的条目{note}")
    matched.sort(key=lambda p: p[1]["published_at"] or "", reverse=True)
    matched.sort(key=lambda p: -p[0])
    return AdapterResult(status="success",
                         items=[i for _, i in matched[:limit]])
