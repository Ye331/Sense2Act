"""LedgerRepository:台账仓库(全系统唯一写入口,S5/S6 的物理落点)。

设计纪律:
- 三态只属于 claim;事实(证据)只存一处(evidence 本体,claim 挂指针);
- 快照只由起草代码写入,不当当前状态查询源,可从 claim_transitions 重算;
- relations 是视图,零维护(关系类 claim 已证实自动长边/消边);
- decision_log 全链路留痕(回放可复算的物理基础)。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from ..config import FIXED_SECTIONS
from .db import transaction
from ..models.envelope import InvestigationEnvelope
from ..models.intel import AnalysisBlueprint

# 蓝图去重阈值(sweep2 定标,2026-10-04):应并对最低 0.916(middle c8~c9),
# 不应并对最高 0.823(middle c9~c14,不同事实);fine 档 0.80 以上全是逐卡重复。
# 取可行区间 (0.823, 0.916) 中点附近,两侧余量均衡。模型拆解惯性(逐卡拆条)
# 靠 prompt 压不住(②实测未生效),重复剪枝属结构判定,归代码(分权纪律)。
CLAIM_DEDUP_SIM = 0.87

# 矛盾重述拦截阈值(改造①,2026-10-04):新草案与"矛盾"态 claim 的换措辞重述
# 不予登记(已被推翻的断言不重烧 S6 预算)。θ2 初值沿用去重 θ;与去重语义不同
# (去重=同批合并,拦截=对历史否决),待续查真实数据后独立定标。
CLAIM_CONTRA_REJECT_SIM = 0.87

# simhash 近重复标记阈值(2026-10-05 定标,character-bigram 64-bit 汉明距离):
# 转载微调距离 0-3;"中标vs废标"语义相反标题距离仅 2——阈值必须 ≤1,否则会
# 把矛盾证据误标为"疑似转载"。只标"几乎完全一样"的转载(软提示,不拒绝入账)。
SIMHASH_NEAR_DUP_DIST = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
    """NFKC + 全半角统一(查重与锚定的归一口径)。"""
    norm = unicodedata.normalize("NFKC", text or "")
    out = []
    for ch in norm:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).strip()


def text_key(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def simhash(text: str) -> str:
    """character-bigram simhash(64-bit hex,确定性)。

    中文按字符 2-gram 切(零依赖,回放可复现);sha256 稳定哈希(内置 hash()
    受 PYTHONHASHSEED 随机化影响,禁用)。空文本返回空串。
    """
    compact = re.sub(r"\s+", "", normalize_text(text))
    if not compact:
        return ""
    tokens = [compact[i:i + 2] for i in range(len(compact) - 1)] or [compact]
    vec = [0] * 64
    for t in tokens:
        h = int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:8], "big")
        for i in range(64):
            vec[i] += 1 if (h >> i) & 1 else -1
    fp = 0
    for i, v in enumerate(vec):
        if v > 0:
            fp |= (1 << i)
    return f"{fp:016x}"


def simhash_distance(a: str, b: str) -> int:
    """两 64-bit hex 指纹的汉明距离;空指纹视为不相似(返回大数)。"""
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _dedup_text(text: str) -> str:
    """去重比对口径:仓库归一化(NFKC+全半角)后再去空白(difflib 对空白敏感)。"""
    return re.sub(r"\s+", "", normalize_text(text))


def alias_key(name: str) -> str:
    return normalize_text(name).casefold()


def _guess_type(name: str) -> str:
    """实体类型粗判(锚定不影响类型;仅作展示与查询便利)。"""
    if any(k in name for k in ("公司", "集团", "厂", "院", "医院", "大学", "中心", "事务所")):
        return "company"
    if any(k in name for k in ("号", "君", "博主", "自媒体", "@")):
        return "account"
    return "organization"


class LedgerRepository:
    def __init__(self, conn: sqlite3.Connection, config_dir: str | Path):
        self.conn = conn
        self._config_dir = Path(config_dir)

    # ------------------------------------------------------------------ 事件

    def register_event(
        self, envelope: InvestigationEnvelope, signal_type: str, event_id: str
    ) -> tuple[str, bool]:
        """登记事件;项目生命周期续查:同 project_id 的 open 事件直接续接(不重开)。"""
        project_id = (
            envelope.tender.project_id
            if envelope.kind == "tender" and envelope.tender
            else ""
        )
        if project_id:
            row = self.conn.execute(
                "SELECT event_id FROM events WHERE project_id=? AND status='open'",
                (project_id,),
            ).fetchone()
            if row:
                return row["event_id"], True
        self.conn.execute(
            "INSERT INTO events(event_id, project_id, kind, signal_type, envelope_json,"
            " status, created_at, updated_at) VALUES(?,?,?,?,?,'open',?,?)",
            (
                event_id, project_id, envelope.kind, signal_type,
                envelope.model_dump_json(by_alias=True), _now(), _now(),
            ),
        )
        self.conn.commit()
        return event_id, False

    def close_event(self, event_id: str) -> None:
        self.conn.execute(
            "UPDATE events SET status='closed', updated_at=? WHERE event_id=?",
            (_now(), event_id),
        )
        self.conn.commit()

    def round_progress(self, event_id: str, round_no: int) -> tuple[int, int]:
        """(新证据数, 新 claim 数)——本轮信息增量,死轮计数器的输入。

        死轮兜底停(investigation 主循环)以此判断"连续 N 轮零信息增量":
        新证据=本轮入账 evidence 行;新 claim=本轮创建(含 S5 派生)的 claim 行。
        """
        e = self.conn.execute(
            "SELECT count(*) FROM evidence WHERE event_id=? AND round=?",
            (event_id, round_no)).fetchone()[0]
        c = self.conn.execute(
            "SELECT count(*) FROM claims WHERE event_id=? AND created_round=?",
            (event_id, round_no)).fetchone()[0]
        return e, c

    def event_exists(self, event_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)
        ).fetchone() is not None

    # ------------------------------------------------------------- 蓝图落库

    def install_blueprint(
        self, event_id: str, blueprint: AnalysisBlueprint, round_no: int = 0,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """S1b 蓝图落库:六节(固定)+ claim(草案ID->全ID)+ 实体锚定链接 + 假设登记。"""
        aliases = aliases or {}
        sec_ids: dict[int, str] = {}
        with transaction(self.conn):
            for idx, (title, kind) in enumerate(FIXED_SECTIONS):
                sec_id = f"{event_id}:S{idx}"
                sec_ids[idx] = sec_id
                # 续查重入兼容:固定节内容不变,重装时忽略(否则 section_id 撞
                # UNIQUE,续查场景根本走不到 claim 层——改造①依赖此重入路径)
                self.conn.execute(
                    "INSERT OR IGNORE INTO sections(section_id,event_id,title,ord,kind)"
                    " VALUES(?,?,?,?,?)",
                    (sec_id, event_id, title, idx, kind),
                )
            remap: dict[str, str] = {}      # 被并 claim 草案 id -> 保留草案 id(空串=拦截)
            merges: list[dict] = []
            blocked: list[dict] = []
            kept_texts: list[tuple[str, str]] = []   # (草案 id, 去重比对文本)
            contra = [(r["claim_id"], _dedup_text(r["text"])) for r in
                      self.conn.execute(
                          "SELECT claim_id, text FROM claims"
                          " WHERE event_id=? AND status='矛盾'", (event_id,))]
            for claim in blueprint.claims:
                norm = _dedup_text(claim.text)
                dup_of = ""
                for kept_id, kept_norm in kept_texts:
                    s = SequenceMatcher(None, norm, kept_norm).ratio()
                    if s >= CLAIM_DEDUP_SIM:
                        dup_of = kept_id
                        merges.append({"dropped": claim.id, "kept": dup_of,
                                       "similarity": round(s, 3),
                                       "dropped_text": claim.text[:60]})
                        break
                if dup_of:
                    remap[claim.id] = dup_of    # 确定性去重:同稿重复并入首条
                    continue
                # 改造①:矛盾史相容过滤——已被推翻断言的换措辞重述不登记,
                # 假设归属一并丢弃(防对已否决结论重烧 S6 预算)
                restatement_of = ""
                for c_id, c_norm in contra:
                    s2 = SequenceMatcher(None, norm, c_norm).ratio()
                    if s2 >= CLAIM_CONTRA_REJECT_SIM:
                        restatement_of = c_id
                        blocked.append({"draft": claim.id,
                                        "text": claim.text[:60],
                                        "matched_claim": c_id,
                                        "similarity": round(s2, 3)})
                        break
                if restatement_of:
                    remap[claim.id] = ""
                    continue
                kept_texts.append((claim.id, norm))
                claim_id = f"{event_id}:{claim.id}"
                self.conn.execute(
                    "INSERT INTO claims(claim_id,event_id,draft_id,text,category,status,"
                    "section_id,priority,priority_reason,created_round,updated_round,"
                    "valid_window_days,refresh_cycle_days,relation_type,parent_claim_id,"
                    "corpus_bound)"
                    " VALUES(?,?,?,?,?,'待证实',?,?,?,?,?,?,?,?,?,?)",
                    (
                        claim_id, event_id, claim.id, claim.text,
                        claim.category.value, sec_ids[claim.section_index],
                        claim.priority, claim.priority_reason, round_no, round_no,
                        claim.valid_window_days, claim.refresh_cycle_days,
                        claim.relation_type, None, int(claim.corpus_bound),
                    ),
                )
                for i, name in enumerate(claim.entity_names[:3]):
                    if not name:
                        continue
                    role = "普通"
                    if claim.category.value == "关系" and i < 2:
                        role = "头" if i == 0 else "尾"
                    eid, _, _ = self.ensure_entity(name, _guess_type(name), aliases, event_id)
                    self.link_claim_entity(claim_id, eid, role)
            for hyp in blueprint.hypotheses:
                # 续查重入兼容:同假设重装时忽略(登记语义不变;与 sections 同理)
                self.conn.execute(
                    "INSERT OR IGNORE INTO hypotheses(hypothesis_id,event_id,label,"
                    "description,registered_round,updated_round) VALUES(?,?,?,?,?,?)",
                    (f"{event_id}:{hyp.id}", event_id, hyp.label, hyp.description,
                     round_no, round_no),
                )
                for cid in hyp.claim_ids:
                    cid2 = remap.get(cid, cid)   # 被并 claim 的假设归属转给保留条
                    if not cid2:                 # 空串=矛盾重述被拦截,归属一并丢弃
                        continue
                    self.conn.execute(
                        "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
                        " VALUES(?,?,?)",
                        (f"{event_id}:{hyp.id}", f"{event_id}:{cid2}", hyp.op),
                    )
        self.log(event_id, round_no, "入账", {
            "claims": [c.id for c in blueprint.claims],
            "hypotheses": [h.id for h in blueprint.hypotheses],
            "sections": len(FIXED_SECTIONS),
            # 确定性去重审计线(并入入账:decision_log.kind 有 CHECK 枚举,
            # 加新 kind 会让旧库在 CHECK 处崩——零 schema 改动的兼容写法)
            "dedup_merged": merges,
            # 改造①审计线:矛盾重述拦截记录
            "contradiction_blocked": blocked,
        })

    def add_claim(
        self, event_id: str, draft: dict, round_no: int,
        aliases: dict[str, str] | None = None,
    ) -> str:
        """派生 claim 入账(S5 派生环);draft 可带 entity_names(关系类前两个=头/尾)。"""
        aliases = aliases or {}
        claim_id = f"{event_id}:{draft['id']}"
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO claims(claim_id,event_id,draft_id,text,category,status,"
                "section_id,priority,priority_reason,created_round,updated_round,"
                "valid_window_days,refresh_cycle_days,relation_type,parent_claim_id)"
                " VALUES(?,?,?,?,?,'待证实',?,?,?,?,?,?,?,?,?)",
                (
                    claim_id, event_id, draft["id"], draft["text"], draft["category"],
                    f"{event_id}:S{draft.get('section_index', 2)}", draft.get("priority", 3),
                    draft.get("priority_reason", ""), round_no, round_no,
                    draft.get("valid_window_days"), draft.get("refresh_cycle_days"),
                    draft.get("relation_type"),
                    f"{event_id}:{draft['parent']}" if draft.get("parent") else None,
                ),
            )
            for i, name in enumerate(draft.get("entity_names", [])[:3]):
                if not name:
                    continue
                role = "普通"
                if draft.get("category") == "关系" and i < 2:
                    role = "头" if i == 0 else "尾"
                eid, _, _ = self.ensure_entity(name, _guess_type(name), aliases, event_id)
                self.link_claim_entity(claim_id, eid, role)
        return claim_id

    def add_hypothesis_claim(self, event_id: str, hyp_id: str, draft_claim_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
            " VALUES(?,?,'AND')",
            (f"{event_id}:{hyp_id}", f"{event_id}:{draft_claim_id}"),
        )
        self.conn.execute(
            "UPDATE hypotheses SET updated_round=updated_round WHERE hypothesis_id=?",
            (f"{event_id}:{hyp_id}",),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 证据

    def next_evidence_id(self, event_id: str) -> str:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM evidence WHERE event_id=?", (event_id,)
        ).fetchone()
        return f"{event_id}:E{row['n'] + 1}"

    def insert_evidence(self, event_id: str, item: dict, round_no: int) -> str | None:
        """证据入账(环④);三层去重:
        ①精确(dedupKey 双键=文本哈希+实体id);②结构化近重复(项目编号|阶段);
        ③simhash 标记(不拒绝,只填 near_dup_of)。①② 重复返回 None,③仅标记。
        """
        dedup = item.get("dedup_key") or (
            text_key(item.get("title", "") + "|" + item.get("content", ""))
            + "|" + "|".join(sorted(item.get("entity_ids", [])))
        )
        # 结构化近重复键 = 项目编号|阶段(空=不参与;新闻/无编号条目退回精确去重)
        project_no = normalize_text(item.get("project_no") or "")
        stage = normalize_text(item.get("stage") or "")
        proj_stage_key = (f"{project_no}|{stage}"
                          if project_no and stage and stage != "其他" else "")
        sh = simhash(item.get("content") or "")
        try:
            evidence_id = self.next_evidence_id(event_id)
            with transaction(self.conn):
                near_dup = ""
                if sh:
                    for row in self.conn.execute(
                            "SELECT evidence_id, simhash FROM evidence"
                            " WHERE event_id=? AND simhash != ''", (event_id,)):
                        if simhash_distance(sh, row["simhash"]) <= SIMHASH_NEAR_DUP_DIST:
                            near_dup = row["evidence_id"]
                            break
                self.conn.execute(
                    "INSERT INTO evidence(evidence_id,event_id,platform,source,url,title,"
                    "content,published_at,fetched_at,credibility,lineage_root,author_type,"
                    "dedup_key,entity_ids,sensitive,round,proj_stage_key,simhash,near_dup_of)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_id, event_id, item.get("platform", ""),
                        item.get("source", ""), item.get("url", ""),
                        item.get("title", ""), item.get("content", ""),
                        item.get("published_at", ""), _now(),
                        item.get("credibility", "低"), item.get("lineage_root", ""),
                        item.get("author_type", ""), dedup,
                        json.dumps(item.get("entity_ids", []), ensure_ascii=False),
                        int(item.get("sensitive", False)), round_no,
                        proj_stage_key, sh, near_dup,
                    ),
                )
            return evidence_id
        except sqlite3.IntegrityError:
            return None  # 精确重复或结构化近重复(proj_stage_key 撞唯一索引)

    def associate(self, claim_id: str, evidence_id: str, round_no: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO claim_evidence(claim_id,evidence_id,ruling,round)"
            " VALUES(?,?,NULL,?)",
            (claim_id, evidence_id, round_no),
        )
        self.conn.commit()

    def set_ruling(self, claim_id: str, evidence_id: str, ruling: str, round_no: int) -> None:
        self.conn.execute(
            "INSERT INTO claim_evidence(claim_id,evidence_id,ruling,round) VALUES(?,?,?,?)"
            " ON CONFLICT(claim_id,evidence_id) DO UPDATE SET ruling=excluded.ruling,"
            " round=excluded.round",
            (claim_id, evidence_id, ruling, round_no),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 实体

    def ensure_entity(
        self,
        name: str,
        entity_type: str,
        aliases: dict[str, str],
        event_id: str,
        credit_code: str = "",
    ) -> tuple[str, str, str]:
        """消歧锚定(环③):别名表精确命中 -> 规范名+已锚;命中信用代码 -> 已锚;否则未锚。

        返回 (entity_id, canonical_name, anchor_status)。
        """
        key = alias_key(name)
        canonical = aliases.get(key, normalize_text(name))
        anchored = key in aliases or bool(credit_code)
        status = "已锚" if anchored else "未锚"
        entity_id = "ent_" + hashlib.sha256(alias_key(canonical).encode()).hexdigest()[:10]
        row = self.conn.execute(
            "SELECT entity_id, anchor_status FROM entities WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO entities(entity_id,name,canonical_name,type,disambiguation,"
                "credit_code,anchor_status,first_event_id) VALUES(?,?,?,?,?,?,?,?)",
                (entity_id, name, canonical, entity_type, status, credit_code, status, event_id),
            )
            self.conn.commit()
            return entity_id, canonical, status
        if credit_code and not row["anchor_status"] == "已锚":
            self.conn.execute(
                "UPDATE entities SET credit_code=?, anchor_status='已锚',"
                " disambiguation='已锚' WHERE entity_id=?",
                (credit_code, entity_id),
            )
            self.conn.commit()
        return entity_id, canonical, row["anchor_status"]

    def mark_entity_forked(self, entity_id: str) -> None:
        self.conn.execute(
            "UPDATE entities SET disambiguation='分叉' WHERE entity_id=?", (entity_id,)
        )
        self.conn.commit()

    def link_claim_entity(self, claim_id: str, entity_id: str, role: str = "普通") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO claim_entities(claim_id,entity_id,role) VALUES(?,?,?)",
            (claim_id, entity_id, role),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 状态

    def update_claim_status(
        self,
        claim_id: str,
        new_status: str,
        cause: str | None,
        round_no: int,
        evidence_id: str = "",
    ) -> bool:
        """状态更新唯一入口:UPDATE + INSERT transition + 已起草节漂移置 STALE。"""
        row = self.conn.execute(
            "SELECT status, section_id FROM claims WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if row is None or row["status"] == new_status:
            return False
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE claims SET status=?, updated_round=? WHERE claim_id=?",
                (new_status, round_no, claim_id),
            )
            self.conn.execute(
                "INSERT INTO claim_transitions(claim_id,old_status,new_status,cause,"
                "evidence_id,round,ts) VALUES(?,?,?,?,?,?,?)",
                (claim_id, row["status"], new_status, cause, evidence_id, round_no, _now()),
            )
            # 漂移检测:已起草节的快照与台账现值不一致 -> STALE(等钩子修复)
            section = self.conn.execute(
                "SELECT section_id, snapshot_json, state FROM sections WHERE section_id=?",
                (row["section_id"],),
            ).fetchone()
            if section and section["state"] in ("STABLE_DRAFTED", "REDRAFTED"):
                snapshot = json.loads(section["snapshot_json"] or "{}")
                draft_id = claim_id.rsplit(":", 1)[-1]
                if snapshot.get(draft_id) != new_status:
                    self.conn.execute(
                        "UPDATE sections SET state='STALE' WHERE section_id=?",
                        (row["section_id"],),
                    )
        return True

    def mark_unreachable(self, claim_id: str) -> None:
        self.conn.execute(
            "UPDATE claims SET unreachable=1 WHERE claim_id=?", (claim_id,)
        )
        self.conn.commit()

    def refresh_states(self, event_id: str, changes: list[dict], round_no: int) -> list[str]:
        """状态刷新(环⑦,同步接口):changes=[{draft_id,new_status,evidence}]。

        世界正常运转触发的翻转(追踪模式常态负载),成因=状态刷新。
        """
        refreshed: list[str] = []
        for ch in changes:
            draft = ch.get("draft_id") or ch.get("draftId") or ""
            new_status = ch.get("new_status") or ch.get("newStatus")
            claim_id = f"{event_id}:{draft}"
            if self.conn.execute(
                "SELECT 1 FROM claims WHERE claim_id=?", (claim_id,)
            ).fetchone() is None:
                continue
            if ch.get("evidence"):
                item = dict(ch["evidence"])
                item.setdefault("lineage_root", item.get("source", ""))
                eid = self.insert_evidence(event_id, item, round_no)
                if eid:
                    self.associate(claim_id, eid, round_no)
            if new_status:
                self.update_claim_status(
                    claim_id, new_status, "状态刷新", round_no
                )
                refreshed.append(draft)
        if refreshed:
            self.log(event_id, round_no, "刷新", {"refreshed": refreshed})
        return refreshed

    def set_draft(
        self, section_id: str, draft_text: str, snapshot: dict, round_no: int,
        redraft: bool = False,
    ) -> None:
        """快照写入的唯一入口(起草代码专用;不当当前状态查询源)。"""
        row = self.conn.execute(
            "SELECT snapshot_version FROM sections WHERE section_id=?",
            (section_id,),
        ).fetchone()
        version = (row["snapshot_version"] if row else 0) + 1
        self.conn.execute(
            "UPDATE sections SET state=?, snapshot_version=?, last_draft_round=?,"
            " snapshot_json=?, draft_text=? WHERE section_id=?",
            ("REDRAFTED" if redraft else "STABLE_DRAFTED", version, round_no,
             json.dumps(snapshot, ensure_ascii=False, sort_keys=True), draft_text,
             section_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 查询

    def open_claims(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM claims WHERE event_id=? AND status='待证实'"
            " ORDER BY priority DESC, created_round ASC, draft_id ASC",
            (event_id,),
        ).fetchall()

    def all_claims(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM claims WHERE event_id=? ORDER BY created_round, draft_id",
            (event_id,),
        ).fetchall()

    def corpus_bound_claim_ids(self, event_id: str) -> list[str]:
        """corpus_bound=1 的 claim id(信封自证型,挂账后应轮 0 立即裁定)。"""
        return [r["claim_id"] for r in self.conn.execute(
            "SELECT claim_id FROM claims WHERE event_id=? AND corpus_bound=1"
            " ORDER BY created_round, draft_id", (event_id,)).fetchall()]

    def top_open_claim(self, event_id: str) -> sqlite3.Row | None:
        rows = self.open_claims(event_id)
        return rows[0] if rows else None

    def claims_for_section(self, section_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM claims WHERE section_id=? ORDER BY priority DESC, draft_id",
            (section_id,),
        ).fetchall()

    def stable_undrafted_sections(self, event_id: str) -> list[sqlite3.Row]:
        """钩子1 查询:PENDING ∧ standard ∧ 至少一条 claim ∧ 全部终态(矛盾也是终态)。"""
        return self.conn.execute(
            "SELECT s.* FROM sections s WHERE s.event_id=? AND s.state='PENDING'"
            " AND s.kind='standard'"
            " AND EXISTS(SELECT 1 FROM claims c WHERE c.section_id=s.section_id)"
            " AND NOT EXISTS(SELECT 1 FROM claims c WHERE c.section_id=s.section_id"
            "   AND c.status='待证实')",
            (event_id,),
        ).fetchall()

    def stale_sections(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sections WHERE event_id=? AND state='STALE'",
            (event_id,),
        ).fetchall()

    def drafted_sections(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sections WHERE event_id=? AND state IN"
            " ('STABLE_DRAFTED','REDRAFTED') ORDER BY ord",
            (event_id,),
        ).fetchall()

    def pending_sections(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sections WHERE event_id=? AND state='PENDING'"
            " AND kind='standard' ORDER BY ord",
            (event_id,),
        ).fetchall()

    def get_sections(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sections WHERE event_id=? ORDER BY ord", (event_id,)
        ).fetchall()

    def oq_claims(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM claims WHERE event_id=? AND status='待证实'"
            " ORDER BY priority DESC, draft_id ASC",
            (event_id,),
        ).fetchall()

    def evidence_for_claim(self, claim_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT e.*, ce.ruling FROM evidence e JOIN claim_evidence ce"
            " ON ce.evidence_id=e.evidence_id WHERE ce.claim_id=?"
            " ORDER BY e.round, e.evidence_id",
            (claim_id,),
        ).fetchall()

    def touched_claim_ids(self, event_id: str, round_no: int) -> list[str]:
        """verdict_scope=touched:本轮有新证据挂账的 claim。"""
        rows = self.conn.execute(
            "SELECT DISTINCT ce.claim_id FROM claim_evidence ce"
            " JOIN claims c ON c.claim_id=ce.claim_id"
            " WHERE c.event_id=? AND ce.round=?",
            (event_id, round_no),
        ).fetchall()
        return [r["claim_id"] for r in rows]

    def evidence_row(self, evidence_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)
        ).fetchone()

    def claim_row(self, claim_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM claims WHERE claim_id=?", (claim_id,)
        ).fetchone()

    def relations_view(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM relations WHERE event_id=?", (event_id,)
        ).fetchall()

    def transitions(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT t.* FROM claim_transitions t JOIN claims c ON c.claim_id=t.claim_id"
            " WHERE c.event_id=? ORDER BY t.id",
            (event_id,),
        ).fetchall()

    def independent_roots(self, claim_id: str) -> dict[str, list[str]]:
        """(d) 判决原料:按裁定方向分组数独立谱系根。"""
        rows = self.conn.execute(
            "SELECT e.lineage_root AS root, ce.ruling AS ruling FROM evidence e"
            " JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
            " WHERE ce.claim_id=? AND ce.ruling IS NOT NULL",
            (claim_id,),
        ).fetchall()
        buckets: dict[str, set[str]] = {"支持": set(), "矛盾": set(), "时效刷新": set()}
        for r in rows:
            if r["ruling"] in buckets and r["root"]:
                buckets[r["ruling"]].add(r["root"])
        return {k: sorted(v) for k, v in buckets.items()}

    # -------------------------------------------------------------- 人答裁决(P1-8)

    def record_adjudication(self, event_id: str, round_no: int, target_kind: str,
                            target_id: str, choice_text: str,
                            selected: bool = True) -> None:
        """人答裁决落表(裁决器调用;hypothesis 分支对每个候选各落一行,selected 标中/未中)。"""
        self.conn.execute(
            "INSERT INTO adjudications(event_id,round,target_kind,target_id,"
            "choice_text,selected,ts) VALUES(?,?,?,?,?,?,?)",
            (event_id, round_no, target_kind, target_id, choice_text,
             int(selected), _now()),
        )
        self.conn.commit()

    def latest_adjudications(self, event_id: str,
                             winners_only: bool = True) -> list[sqlite3.Row]:
        """每个 (target_kind, target_id) 取最新一条裁决(latest-wins:人的最新说法为准)。"""
        sql = ("SELECT a.* FROM adjudications a JOIN"
               " (SELECT target_kind, target_id, MAX(id) AS mid FROM adjudications"
               "  WHERE event_id=? GROUP BY target_kind, target_id) t ON a.id = t.mid")
        if winners_only:
            sql += " WHERE a.selected = 1"
        sql += " ORDER BY a.id"
        return self.conn.execute(sql, (event_id,)).fetchall()

    def adjudication_overrides(self, event_id: str) -> dict[str, bool]:
        """假设层人裁覆写表:{完整假设id: 选中?}(target_kind='hypothesis')。"""
        return {a["target_id"]: bool(a["selected"])
                for a in self.latest_adjudications(event_id, winners_only=False)
                if a["target_kind"] == "hypothesis"}

    # -------------------------------------------------------------- 假设记分

    def hypothesis_standings(self, event_id: str) -> list[dict]:
        """竞争假设记分:score = 已证实 +1 / 矛盾 -1(组成 claim);主导=最高分。

        人裁覆写(P1-8):被裁决过的假设,成员 claim 记分按裁决生效态计
        (选中→全按"已证实"+1,未中→全按"矛盾"-1),latest-wins;
        **只钉记分不钉 claim**——claims/transitions 不受人裁影响(分权:
        claim 层=证据账本只归 S6,假设层=研判可被人裁钉死)。每条附
        hypothesis_id(AskGate 候选映射用)与 adjudicated 旗标(展示"人裁钉死")。
        """
        rows = self.conn.execute(
            "SELECT h.hypothesis_id, h.label, h.description, h.registered_round,"
            " c.status FROM hypotheses h"
            " LEFT JOIN hypothesis_claims hc ON hc.hypothesis_id=h.hypothesis_id"
            " LEFT JOIN claims c ON c.claim_id=hc.claim_id"
            " WHERE h.event_id=? ORDER BY h.registered_round",
            (event_id,),
        ).fetchall()
        human = self.adjudication_overrides(event_id)
        standings: dict[str, dict] = {}
        for r in rows:
            st = standings.setdefault(
                r["hypothesis_id"],
                {"hypothesis_id": r["hypothesis_id"],
                 "label": r["label"], "description": r["description"] or "",
                 "verified": 0, "contradicted": 0, "pending": 0, "score": 0,
                 "emerged": False, "adjudicated": False},
            )
            if r["status"] is None:
                continue
            st["emerged"] = True
            override = human.get(r["hypothesis_id"])
            effective = r["status"] if override is None else (
                "已证实" if override else "矛盾")
            if effective == "已证实":
                st["verified"] += 1
                st["score"] += 1
            elif effective == "矛盾":
                st["contradicted"] += 1
                st["score"] -= 1
            elif effective == "待证实":
                st["pending"] += 1
            if override is not None:
                st["adjudicated"] = True
        out = list(standings.values())
        if out:
            best = max(s["score"] for s in out)
            for s in out:
                s["dominant"] = s["score"] == best and s["score"] > 0
        return out

    def claim_discriminativeness(self, event_id: str) -> dict[str, dict]:
        """区分度 D(c)/cost(改造②,2026-10-04):喂 S2 的输入数字,代码算、模型只消费。

        D(c) = Δ⁺ + Δ⁻:把待证实 claim c 反事实置"已证实"/置"矛盾"后,各统计
        dominant 翻转的假设个数。score 语义与 hypothesis_standings 严格一致
        (加性:已证实 +1 / 矛盾 -1 / 待证实 0;op 不参与求值);dominant 是
        相对量(score==max 且 >0),故成员与非成员假设都可能翻转——恰捕捉
        "查这条能否改变领先假设"。已知局限(如实):巩固型查证(领先假设
        再 +1,格局不变)计 0——区分度量的是改变格局,不是推进确认。
        cost(c) = max(1, 名下裁定证据条数),预期 S6 调用数的保守代理。
        返回 {draft_id: {"plus", "minus", "cost", "ratio"}}(ratio = round(D/cost, 3))。
        """
        hyp_ids = [r["hypothesis_id"] for r in self.conn.execute(
            "SELECT hypothesis_id FROM hypotheses WHERE event_id=?"
            " ORDER BY registered_round", (event_id,))]
        rows = self.conn.execute(
            "SELECT hc.claim_id AS cid, hc.hypothesis_id AS hid FROM hypothesis_claims hc"
            " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
            " WHERE h.event_id=?", (event_id,)).fetchall()
        status = {r["claim_id"]: r["status"] for r in self.conn.execute(
            "SELECT claim_id, draft_id, status FROM claims WHERE event_id=?",
            (event_id,))}
        draft_of = {r["claim_id"]: r["draft_id"] for r in self.conn.execute(
            "SELECT claim_id, draft_id FROM claims WHERE event_id=?", (event_id,))}
        cost = {r["cid"]: max(1, r["n"]) for r in self.conn.execute(
            "SELECT ce.claim_id AS cid, COUNT(*) AS n FROM claim_evidence ce"
            " JOIN claims c ON c.claim_id=ce.claim_id WHERE c.event_id=?"
            " GROUP BY ce.claim_id", (event_id,))}

        def _dominant(scores: dict[str, int]) -> dict[str, bool]:
            best = max(scores.values(), default=0)
            return {h: (scores.get(h, 0) == best and best > 0) for h in hyp_ids}

        base: dict[str, int] = {h: 0 for h in hyp_ids}
        members_of: dict[str, list[str]] = {}
        for m in rows:
            st = status.get(m["cid"])
            if st == "已证实":
                base[m["hid"]] = base.get(m["hid"], 0) + 1
            elif st == "矛盾":
                base[m["hid"]] = base.get(m["hid"], 0) - 1
            members_of.setdefault(m["cid"], []).append(m["hid"])
        dom0 = _dominant(base)

        out: dict[str, dict] = {}
        for cid, st in status.items():
            if st != "待证实":
                continue
            flips: dict[str, int] = {}
            for direction, step in (("已证实", 1), ("矛盾", -1)):
                pert = dict(base)
                for h in members_of.get(cid, []):
                    pert[h] += step
                dom1 = _dominant(pert)
                flips[direction] = sum(1 for h in hyp_ids if dom1[h] != dom0[h])
            k = cost.get(cid, 1)
            out[draft_of[cid]] = {
                "plus": flips["已证实"], "minus": flips["矛盾"], "cost": k,
                "ratio": round((flips["已证实"] + flips["矛盾"]) / k, 3),
            }
        return out

    # ------------------------------------------------------------------ 调查圈

    def register_cohort(
        self, event_id: str, name: str, criteria: dict, round_no: int
    ) -> str:
        cohort_id = "coh_" + hashlib.sha256(
            f"{event_id}:{name}".encode()).hexdigest()[:10]
        self.conn.execute(
            "INSERT OR IGNORE INTO cohorts(cohort_id,event_id,name,criteria_json,"
            "created_round) VALUES(?,?,?,?,?)",
            (cohort_id, event_id, name, json.dumps(criteria, ensure_ascii=False), round_no),
        )
        self.conn.commit()
        return cohort_id

    def add_cohort_entities(self, cohort_id: str, entity_ids: list[str]) -> None:
        for eid in entity_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO cohort_entities(cohort_id,entity_id) VALUES(?,?)",
                (cohort_id, eid),
            )
        self.conn.commit()

    def cohorts_of(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cohorts WHERE event_id=?", (event_id,)
        ).fetchall()

    # ------------------------------------------------------------------ 留痕

    def log(
        self, event_id: str, round_no: int, kind: str, payload: dict, degraded: bool = False
    ) -> None:
        self.conn.execute(
            "INSERT INTO decision_log(event_id,round,kind,payload_json,degraded,ts)"
            " VALUES(?,?,?,?,?,?)",
            (event_id, round_no, kind,
             json.dumps(payload, ensure_ascii=False, sort_keys=True), int(degraded), _now()),
        )
        self.conn.commit()

    def decision_log_rows(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decision_log WHERE event_id=? ORDER BY id", (event_id,)
        ).fetchall()

    # ------------------------------------------------------------------ 摘要

    def event_summary(self, event_id: str, include_disc: bool = True) -> dict:
        """喂 S2 的台账摘要(喂摘要不喂全量)。待证实 claim 附区分度数字(改造②)。

        include_disc=False 为盲规划对照(改造②验收的 off 臂):摘要不含 disc。
        """
        claims = self.all_claims(event_id)
        disc = self.claim_discriminativeness(event_id) if include_disc else {}
        by_status: dict[str, list[dict]] = {}
        for c in claims:
            entry = {"id": c["draft_id"], "text": c["text"], "category": c["category"],
                     "priority": c["priority"], "section": c["section_id"].rsplit(":", 1)[-1]}
            if c["status"] == "待证实" and c["draft_id"] in disc:
                entry["disc"] = disc[c["draft_id"]]
            by_status.setdefault(c["status"], []).append(entry)
        summary = {
            "claims_by_status": by_status,
            "hypotheses": self.hypothesis_standings(event_id),
            "relations": [
                {k: r[k] for k in ("head_name", "relation_type", "tail_name")}
                for r in self.relations_view(event_id)
            ],
            "cohorts": [dict(r) for r in self.cohorts_of(event_id)],
        }
        # 裁决结果喂 S2(结构化,不只靠 plan_history 散文);空则省键——旧 prompt 字节不变
        adjs = self.latest_adjudications(event_id, winners_only=True)
        if adjs:
            summary["adjudications"] = [
                {"round": a["round"], "kind": a["target_kind"],
                 "target": a["target_id"].rsplit(":", 1)[-1],
                 "choice": a["choice_text"]}
                for a in adjs][:5]
        return summary

    def status_counts(self, event_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM claims WHERE event_id=? GROUP BY status",
            (event_id,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
