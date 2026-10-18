"""S4 观察处理:实体抽取(词表+强格式)/可信度标注/谱系根打标/脱敏标记/质量检查。

分权:谱系根是 S4 入账时写进 evidence 的元数据(站点拓扑是世界状态),
S6 只读;规范名一律由别名表/外部权威源裁定,任何模型不生成规范名。
"""
from __future__ import annotations

import re

from ..config import DomainConfig
from ..models.observation import ObservationPackage
from ..store.ledger import LedgerRepository, alias_key, normalize_text

# 统一社会信用代码(GB 32100 定长结构):强格式一票定夺
_CREDIT_CODE_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}")
# 结构化近重复原料:从 content 的采集侧后缀提取(阶段/项目编号,供 S5 结构化去重)
_STAGE_SUFFIX_RE = re.compile(r"阶段[:：]\s*([^\s|]+)")
_PROJECT_NO_RE = re.compile(r"项目编号[:：]\s*([^\s|]+)")
# 合规脱敏 V1:仅标记,不重写原文
_SENSITIVE_RES = [
    re.compile(r"\d{17}[\dXx]"),        # 身份证
    re.compile(r"1[3-9]\d{9}"),         # 手机号
]


class Observer:
    def __init__(self, ledger: LedgerRepository, domain: DomainConfig):
        self._ledger = ledger
        self._domain = domain

    def _known_names(self) -> dict[str, str]:
        """词表 = 别名表 + watchlist 规范名 + 已入账实体规范名(词表生长的读侧)。"""
        names: dict[str, str] = {}
        for alias, canonical in self._domain.aliases.items():
            names[normalize_text(alias).casefold()] = canonical
        for w in self._domain.watchlist:
            cn = w.get("canonical_name", "")
            if cn:
                names[normalize_text(cn).casefold()] = cn
        for row in self._ledger.conn.execute(
                "SELECT name, canonical_name FROM entities").fetchall():
            names[normalize_text(row["name"]).casefold()] = row["canonical_name"]
            names[normalize_text(row["canonical_name"]).casefold()] = \
                row["canonical_name"]
        return names

    def process(
        self, event_id: str, round_no: int, obs: ObservationPackage
    ) -> list[dict]:
        """观察包 -> 加工后的证据条目(供 S5 入账)。

        每条: 附 credibility/lineage_root/sensitive/entity_ids + entities 明细。
        """
        known = self._known_names()
        processed: list[dict] = []
        for item in obs.items:
            text = normalize_text(f"{item.title}\n{item.content}").casefold()
            # 实体抽取 V1:词表扫描(精确子串)+ 信用代码强格式
            found: dict[str, str] = {}
            for name, canonical in known.items():
                if name and name in text:
                    found[name] = canonical
            for code in _CREDIT_CODE_RE.findall(text or ""):
                found[code] = code

            root = self._domain.upstream_root(item.source or item.platform)
            credibility = self._domain.credibility_of(root)
            if item.author_type in ("自媒体", "匿名") and credibility == "高":
                credibility = "中"
            sensitive = any(p.search(item.content or "") for p in _SENSITIVE_RES)

            entity_details = []
            entity_ids = []
            for name, canonical in found.items():
                eid, canon, status = self._ledger.ensure_entity(
                    name, self._guess(name), self._domain.aliases, event_id)
                entity_ids.append(eid)
                entity_details.append(
                    {"entity_id": eid, "name": name, "canonical": canon,
                     "anchor": status})
            # 结构化近重复原料:从 content 后缀确定性提取(无则空,退回精确去重)
            stage_m = _STAGE_SUFFIX_RE.search(item.content or "")
            no_m = _PROJECT_NO_RE.search(item.content or "")
            processed.append({
                "platform": item.platform, "source": item.source or item.platform,
                "url": item.url, "title": item.title, "content": item.content,
                "published_at": item.published_at, "author_type": item.author_type,
                "lineage_root": root, "credibility": credibility,
                "sensitive": sensitive,
                "entity_ids": entity_ids, "entities": entity_details,
                "stage": stage_m.group(1) if stage_m else "",
                "project_no": no_m.group(1) if no_m else "",
                "round": round_no,
            })
        return processed

    @staticmethod
    def _guess(name: str) -> str:
        if re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", name or ""):
            return "company"  # 信用代码必为企业
        from ..store.ledger import _guess_type
        return _guess_type(name)


def quality_signal(obs: ObservationPackage, processed: list[dict]) -> str:
    """质量检查:零条目或全部失败回执 -> poor(触发 CRAG 回边重查一次)。"""
    if processed:
        return "ok"
    if any(r.status in ("success", "empty") and r.count > 0
           for r in obs.source_reports):
        return "ok"
    return "poor"
