"""corpus_bound 代码判定化(手术包件 6,2026-10-22;v2 2026-10-23)。

最锋利的文本触发开关:corpus_bound 一比特控判决路径(自证例外=单根放行)。

**v2 语义(2026-10-23,重录波 live 数据强制;原 v1=纯子串覆写)**:
统一重录波 smoke(54 run)实证 v1 纯子串覆写在 live 分布上把主流复述型
claim(跨字段拼接+框架词前缀"信封载明")整体否决(audit cb_code_audit
changed=8/n=15)→ 自证例外全波失灵。v2 改为**校验语义,与件 4 同款哲学**:

- **加法提升(0→1)**:代码分段确认(框架词剥离+分段逐段子串+数字全中)
  全命中 → 确证为信封内忠实复述,模型漏标的补上——治自证漏报族;
- **硬证据否决(1→0)**:仅当存在**数字级伪造**段(段内数字与信封语料
  冲突且文面不命中)才否决——"预算 158 万"≠语料"627 万"属客观可判;
- **转述不否决**:综合/转述类 claim("解读将本信号定性为…"跨字段比较)
  子串原理上不可判,无硬冲突时尊重模型标注——宁可放过不可错杀,与
  v1 文档"概括复述落 world"的教训一致(v1 实测=大面积错杀);
- 数字保留原样参与判定;"198"不得因是"1980000"的子串而命中
  (数字 token 须精确命中)。

分段算法:框架词头剥离(信封载明/公告载明…)→分隔符切段(逗号/顿号/
括号/引号等)→段首标签词剥离(项目编号/金额/采购方…)→单位后缀剥离
(元/圆)→规范化(去空白标点/英文小写,数字汉字保留)→逐段要求
"文面子串 ∧ 数字 token 精确命中"。任一实质段落空=确认失败(保守)。

信封语料 = envelope 全部字符串值(基础字段/检测器解释/线索卡,任何字段
的忠实复述都算语料内)。

边界:本判定只作用于 S1b 产 claims;investigation 挂载点在
tender_field_claims 机械追加**之前**——PRJ- 门语义(商机字段 claim 须
详情正文级证实)不被本判定绕过。

审计:decision_log kind='其他' payload.cb_code_audit
{n, upgraded, vetoed, agreed}——模型与代码的不一致率是模型标注退役的
数据依据(v2 起不一致≠错,含转述类保守保留)。

开关:--corpus-bound-code on|off 默认 off(off=模型标注现行为,零迁移);
on 时代码校验。行为参数入 META_KEYS(重录波全开录制后转正)。
"""
from __future__ import annotations

import re

# 删除类规范化(保守清单:仅无语义标点与空白;数字/字母/汉字全保留)
_STRIP = str.maketrans("", "", " \t\r\n　,:;、。!?!?,,;;::()()[]{}【】"
                       "《》<>\"\"''\"'`'·…—–-_~=*#『』「」''""")

# 框架词头(循环剥离,长词优先由排序保证——元组按长度降序书写)
_FRAMEWORK_HEADS = ("信封中载明", "信封载明", "信封注明", "公告载明", "正文载明",
                    "标题载明", "线索卡载明", "解读载明", "据信封",
                    "信封摘要", "信封", "公告", "正文", "标题", "线索卡", "解读")
_LEAD_JUNK = " \t\n:：,，、;；"
# 分段符(段界;不含连字符与 ASCII 点号——日期/编号/小数内部不切)
_SEG_DELIMS = ",，、;；:：。!?!?…\n『』「」“”\"''‘’()（）【】《》/｜|"
# 段首标签词(值前缀形态;长度降序)
_SEG_LABELS = ("统一社会信用代码", "获取文件截止", "预算金额", "最高限价",
               "投标截止", "截止时间", "项目编号", "项目名称", "采购单位",
               "采购方", "采购人", "发布日期", "发布日", "信号类型", "事件编号",
               "信号时间", "监测重点", "来源链接", "代理机构", "联系人",
               "金额", "主体", "平台", "作者", "来源", "预算", "时间",
               "日期", "编号", "名称", "状态", "数量", "链接", "标题")
_LABEL_JUNK = ":：,， 、()（）为是系"
_SEG_LABELS_SET = frozenset(_SEG_LABELS)
# 单位后缀(不改量级;万元不剥——数字 token 精确判定下剥万会伪造命中)
_UNIT_SUFFIXES = ("人民币", "元", "圆")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def norm(text: str) -> str:
    """规范化:去空白/常见标点/英文小写;数字与汉字原样保留。"""
    return (text or "").translate(_STRIP).lower()


def _num_tokens(text: str) -> set[str]:
    """数字 token(精确判定用):'6270000.0'→'6270000';'0.5'保持。"""
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        t = m.group()
        out.add(t.rstrip("0").rstrip(".") if "." in t else t)
    return out


def _strip_heads(text: str) -> str:
    """框架词头+引导标点循环剥离。"""
    prev, t = None, (text or "").strip()
    while prev != t:
        prev = t
        t = t.lstrip(_LEAD_JUNK)
        for h in _FRAMEWORK_HEADS:
            if t.startswith(h):
                t = t[len(h):]
                break
    return t


def envelope_corpus(envelope) -> str:
    """信封语料:model_dump 全部字符串值拼接(嵌套 dict/list 递归收集)。

    值间以换行分隔——防跨字段数字粘连("3227000.0"+"2026"被 tokenizer
    读成一个 token);norm 剥空白,子串判定不受影响。
    """

    def walk(v):
        if v is None:
            return
        if isinstance(v, str):
            yield v
        elif isinstance(v, (int, float)):
            yield str(v)
        elif isinstance(v, dict):
            for x in v.values():
                yield from walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)

    return "\n".join(walk(envelope.model_dump(by_alias=True)))


def _segments(claim_text: str) -> list[str]:
    """框架词剥离→切段→段首标签剥离→单位后缀剥离,返回实质段列表。"""
    body = _strip_heads(claim_text)
    for d in _SEG_DELIMS:
        body = body.replace(d, "\n")
    segs = []
    for raw in body.split("\n"):
        seg = raw.strip()
        again = True
        while again:
            again = False
            for lab in _SEG_LABELS:
                if seg.startswith(lab) and len(seg) > len(lab):
                    seg = seg[len(lab):].lstrip(_LABEL_JUNK)
                    again = True
                    break
        for suf in _UNIT_SUFFIXES:
            if seg.endswith(suf):
                seg = seg[:-len(suf)]
                break
        if not seg or seg in _SEG_LABELS_SET:
            continue                     # 裸标签/纯胶水段(括号切段常见),无断言力
        segs.append(seg)
    return segs


def confirm(claim_text: str, corpus: str) -> bool:
    """代码确认:全部分段逐段命中(文面子串∧数字精确)→忠实复述。"""
    ctext = norm(corpus)
    if not ctext:
        return False
    cnums = _num_tokens(corpus)
    checked = 0
    for seg in _segments(claim_text):
        n = norm(seg)
        if len(n) < 2:
            continue                       # 纯胶水/单位,无断言力
        checked += 1
        if n in ctext and _num_tokens(seg) <= cnums:
            continue
        return False                       # 任一实质段落空=确认失败(保守)
    return checked > 0


def hard_contradiction(claim_text: str, corpus: str) -> bool:
    """硬否决判据:存在数字级伪造段(段内数字与语料冲突∧文面不命中)。"""
    ctext = norm(corpus)
    cnums = _num_tokens(corpus)
    for seg in _segments(claim_text):
        n = norm(seg)
        if len(n) < 2:
            continue
        seg_nums = _num_tokens(seg)
        if seg_nums and seg_nums - cnums and n not in ctext:
            return True
    return False


def judge(claim_text: str, corpus: str) -> bool:
    """兼容入口=confirm(v1 的 judge 语义"忠实复述可证",测试与探针沿用)。"""
    return confirm(claim_text, corpus)


def reconcile(claims: list, envelope) -> tuple[list, dict]:
    """v2 校验语义:加法提升+硬证据否决,转述类尊重模型。返回 (claims, 审计)。

    final = (model OR confirm) AND NOT hard_contradiction
    审计:upgraded=模型漏标代码补上;vetoed=数字伪造否决;agreed=一致
    (含"模型True+转述不可判+无硬冲突→保留True")。
    """
    corpus = envelope_corpus(envelope)
    audit = {"n": len(claims), "upgraded": 0, "vetoed": 0, "agreed": 0}
    for c in claims:
        conf = confirm(c.text, corpus)
        veto = hard_contradiction(c.text, corpus)
        final = (not veto) and (bool(c.corpus_bound) or conf)
        if veto and c.corpus_bound:
            audit["vetoed"] += 1
        elif final and not c.corpus_bound:
            audit["upgraded"] += 1
        else:
            audit["agreed"] += 1
        c.corpus_bound = final
    return claims, audit
