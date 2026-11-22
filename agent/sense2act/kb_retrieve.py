# -*- coding: utf-8 -*-
"""K2 S3 第四通道检索器(2026-10-27)——kb.db 确定性检索,零网络零 LLM。

蓝图=《实施方案/企业知识库-实施方案-20261025.md》§四(挂载点)/§五(检索
三通道)/§七(lineage 继承+消费通道纪律)。分权纪律:检索命中全确定性、
可解释("为什么检索到它"三通道各有机械判据);嵌入检索=演进项不做。

纪律:
- **消费通道纪律(§七)**:只服务正卡区(已证实/矛盾);未决区=下轮种子属
  S1b 背景通道,缓行——防 KB 上下文锚定判决;
- **敏感红线(§6.4)**:sensitivity=internal 永不进 prompt——检索器只出
  public(internal 件只存在于 kb.db 底稿,K3 上传件的 prompt 过滤同此闸);
- **lineage 继承(§七)**:item 预设原根(EvidenceItemLite.lineage_root,
  S4 守卫消费)——复曝老证据不涨独立根,"KB 证据不能单独支撑已证实"
  自然涌现,零 S6 改动;
- **每指纹单 run 只服务一次**(served 记忆,egress URL 记忆同款);
- **泄漏墙(§四)**:本模块是被调方;bench 主读数永远 KB-off;
- 参数查 kb_rules.retrieval(hits_max/simhash_dist),零代码兜底。
"""
from __future__ import annotations

import json
from pathlib import Path

from .kb_chain import open_kb_ro
from .kb_derive import load_rules
from .models.observation import EvidenceItemLite, SourceReport
from .store.ledger import alias_key, simhash, simhash_distance

_KB_PLATFORM = "kb"


class KbRetrieveError(Exception):
    """KB 检索器错误(库缺失/混版本/规则不合法)。"""


def kb_version_of(kb_path: str | Path) -> str:
    """读 kb.db 版本(kb_anchor DISTINCT;混版本>1=派生 bug 显式报错)。

    供 cli 配对闸在装配检索器前独立核对(meta.kb_version 配对)。"""
    p = Path(kb_path)
    if not p.exists():
        raise KbRetrieveError(f"kb.db 不存在: {p}")
    conn = open_kb_ro(p)
    try:
        versions = sorted({r[0] for r in
                           conn.execute("SELECT DISTINCT kb_version FROM kb_anchor")})
    finally:
        conn.close()
    if len(versions) > 1:
        raise KbRetrieveError(f"kb.db 混版本({versions})=派生不完整,拒绝服务")
    return versions[0] if versions else ""


class KbRetriever:
    """kb.db 检索器(每 run 一例;served 记忆=run 内不重复服务)。"""

    def __init__(self, kb_path: str | Path,
                 config_dir: str | Path = "data/config"):
        self._path = Path(kb_path)
        if not self._path.exists():
            raise KbRetrieveError(f"kb.db 不存在: {self._path}")
        self.version = kb_version_of(self._path)
        rules = load_rules(Path(config_dir) / "kb_rules.json", config_dir)
        self._hits_max = rules["retrieval"]["hits_max"]
        self._sim_dist = rules["retrieval"]["simhash_dist"]
        self._kb = open_kb_ro(self._path)
        # 实体/项目名字索引(归一通道;卡上 names=归一正名+别名,alias_key
        # 同主库锚定口径)
        self._name_index: dict[str, tuple[str, str]] = {}  # alias_key→(entity_id, kind)
        for r in self._kb.execute("SELECT entity_id, kind, names_json FROM entity"):
            names = json.loads(r["names_json"])
            for n in [names.get("canonical", "")] + list(
                    names.get("aliases") or []):
                k = alias_key(n)
                if len(k) >= 2:
                    self._name_index.setdefault(k, (r["entity_id"], r["kind"]))
        self._served: set[str] = set()

    def close(self) -> None:
        if self._kb is not None:
            self._kb.close()
            self._kb = None

    # ---------------------------------------------------------------- 三通道

    def retrieve(self, claim_text: str, hints: list[str] | None = None,
                 search_context: dict | None = None
                 ) -> tuple[list[EvidenceItemLite], SourceReport]:
        """三通道检索(确定性,优先序:字段契约→归一→simhash)。

        返回 (items, SourceReport);空命中=empty 回执(失败不伪装)。
        """
        probe = alias_key(f"{claim_text or ''} {' '.join(hints or [])}")
        channels: list[str] = []
        cands: dict[str, int] = {}   # fingerprint → 通道优先序(越小越先)

        # ② 字段契约:search_context 锚点(pno 精确/buyer 归一)→项目卡→
        #    其 event 的正卡 claim(项目卡 v1 只挂 event 链,claim 走事件
        #    前缀扫描)→关联证据
        sc = search_context or {}
        for key, kind in (("project_no", "项目"), ("buyer", "项目")):
            anchor = (sc.get(key) or "").strip()
            if not anchor:
                continue
            hit = self._name_index.get(alias_key(anchor))
            if hit and hit[1] == kind:
                n = self._collect_via_events(hit[0], cands, 0)
                if n:
                    channels.append(f"{key}契约×{n}")

        # ① 实体归一:claim 文本+提示词命中实体卡(实体种)→正卡 claim→证据
        seen_eids: set[str] = set()
        for k, (eid, kind) in self._name_index.items():
            if k in probe and eid not in seen_eids:
                seen_eids.add(eid)
                n = self._collect_via_entity(eid, cands, 1)
                if n:
                    channels.append(f"归一×{n}")

        # ③ simhash 近重复:claim 文本 simhash 对证据指纹汉明距离≤阈值
        #    (复验同文回灌;阈值与 ledger.SIMHASH_NEAR_DUP_DIST 同源同值)
        sh = simhash(claim_text or "")
        if sh:
            for r in self._kb.execute(
                    "SELECT fingerprint, simhash FROM evidence_index"
                    " WHERE simhash != ''"):
                if simhash_distance(sh, r["simhash"]) <= self._sim_dist:
                    if r["fingerprint"] not in cands:
                        cands[r["fingerprint"]] = 2
                        channels.append("同文×1")

        # 逐一装配(确定性:通道优先序→指纹序;public 过滤+单次服务)
        items: list[EvidenceItemLite] = []
        for fps in sorted(cands, key=lambda f: (cands[f], f)):
            if len(items) >= self._hits_max:
                break
            if fps in self._served:
                continue
            row = self._kb.execute(
                "SELECT * FROM evidence_index WHERE fingerprint=?",
                (fps,)).fetchone()
            if row is None or row["sensitivity"] != "public":
                continue      # 敏感红线:internal 永不进 prompt(§6.4)
            roots = json.loads(row["lineage_roots_json"])
            items.append(EvidenceItemLite(
                platform=_KB_PLATFORM,
                source=row["source"] or _KB_PLATFORM,
                url=row["url"], title=row["title"], content=row["content"],
                published_at=row["observed_at"],
                author_type=row["author_type"],
                lineage_root=roots[0] if roots else ""))
            self._served.add(fps)
        report = SourceReport(
            platform=_KB_PLATFORM,
            status="success" if items else "empty",
            count=len(items), retryable=False,
            message=f"KB检索 命中={len(items)} 通道={channels or '无'}"
                    f" kb_version={self.version}")
        return items, report

    def _collect_via_entity(self, entity_id: str, cands: dict[str, int],
                            prio: int) -> int:
        """实体卡→正卡 claim→关联指纹(claim_evidence_snap 快照)。"""
        n = 0
        for l in self._kb.execute(
                "SELECT ref_id FROM entity_link WHERE entity_id=?"
                " AND ref_kind='claim' AND zone='正卡'", (entity_id,)):
            n += self._collect_via_claim(l["ref_id"], cands, prio)
        return n

    def _collect_via_events(self, project_entity_id: str,
                            cands: dict[str, int], prio: int) -> int:
        """项目卡→event 链→该事件全部正卡 claim(项目卡 v1 无 claim 直挂)。"""
        n = 0
        for l in self._kb.execute(
                "SELECT ref_id FROM entity_link WHERE entity_id=?"
                " AND ref_kind='event'", (project_entity_id,)):
            for cl in self._kb.execute(
                    "SELECT ref_id FROM entity_link WHERE ref_kind='claim'"
                    " AND zone='正卡' AND ref_id LIKE ?",
                    (l["ref_id"] + ":%",)):
                n += self._collect_via_claim(cl["ref_id"], cands, prio)
        return n

    def _collect_via_claim(self, claim_id: str, cands: dict[str, int],
                           prio: int) -> int:
        row = self._kb.execute(
            "SELECT fps_json FROM claim_evidence_snap WHERE claim_id=?",
            (claim_id,)).fetchone()
        n = 0
        for fp in json.loads(row["fps_json"]) if row else []:
            if fp not in cands or prio < cands[fp]:
                cands[fp] = prio
                n += 1
        return n
