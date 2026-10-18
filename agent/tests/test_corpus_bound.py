"""第一方自证例外单元:corpus_bound 标注全链 + S6 第五阶梯分支 + 旧库迁移。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sense2act.models.base import ClaimCategory, ClaimStatus, Direction  # noqa: E402
from sense2act.models.envelope import InvestigationEnvelope, TenderSignal  # noqa: E402
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    GapDraft,
    HypothesisSeed,
)
from sense2act.models.verdict import EvidenceJudgment, Opinion  # noqa: E402
from sense2act.store import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402
from sense2act.stages.s6_verdict import VerdictEngine  # noqa: E402

ENVELOPE_EVID = {
    "platform": "signal_layer", "source": "signal_layer",
    "title": "信号层信封基础字段",
    "content": "招标信号基础字段:项目编号 MEDAI-2026-001;采购方 测试医院。",
    "lineage_root": "signal_layer", "author_type": "内部检测",
    "credibility": "中", "url": "", "published_at": "2026-09-20",
}
NEWS_EVID = {
    "platform": "toutiao", "source": "toutiao.com",
    "title": "某媒体稿", "content": "媒体报道:测试医院采购 AI 系统。",
    "lineage_root": "toutiao.com", "author_type": "媒体",
    "credibility": "低", "url": "", "published_at": "2026-09-21",
}


def blueprint(corpus_bound: bool) -> AnalysisBlueprint:
    return AnalysisBlueprint(
        claims=[ClaimDraft(id="C1", text="信封载明项目编号为 MEDAI-2026-001",
                           category=ClaimCategory.FACT, section_index=2,
                           corpus_bound=corpus_bound),
                ClaimDraft(id="C2", text="测试医院采购 AI 系统",
                           category=ClaimCategory.FACT, section_index=2)],
        hypotheses=[HypothesisSeed(id="H0", label="正常经营")],
        gaps=[GapDraft(text="需求细节")],
    )


def install(led: LedgerRepository, corpus_bound: bool) -> tuple[str, str]:
    env = InvestigationEnvelope(kind="tender", event_id="", tender=TenderSignal(
        project_id="MEDAI-2026-001", project_name="测试采购项目",
        purchaser="测试医院", published_date="2026-09-20"))
    led.register_event(env, signal_type="商机", event_id="evt_cb")
    led.install_blueprint("evt_cb", blueprint(corpus_bound))
    return "evt_cb:C1", "evt_cb:C2"


class TestCorpusBoundModel(unittest.TestCase):

    def test_default_false_backward_compatible(self):
        """默认 False:旧回放/旧录制件的 s1b 响应缺字段照常解析。"""
        d = ClaimDraft(id="C9", text="t", category=ClaimCategory.FACT,
                       section_index=0)
        self.assertFalse(d.corpus_bound)
        self.assertTrue(ClaimDraft(id="C9", text="t", category=ClaimCategory.FACT,
                                   section_index=0, corpus_bound=True).corpus_bound)


class TestPersistence(unittest.TestCase):

    def test_install_blueprint_persists_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = LedgerRepository(
                connect(Path(tmp) / "t.db"), ROOT / "data" / "config")
            try:
                cb_id, plain_id = install(led, corpus_bound=True)
                self.assertEqual(led.claim_row(cb_id)["corpus_bound"], 1)
                self.assertEqual(led.claim_row(plain_id)["corpus_bound"], 0)
            finally:
                led.conn.close()

    def test_legacy_db_migration(self):
        """旧库(claims 无 corpus_bound 列)connect 时守卫式 ALTER 平滑迁移。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(str(path))
            conn.execute("""
                CREATE TABLE claims (
                  claim_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
                  draft_id TEXT NOT NULL, text TEXT NOT NULL,
                  category TEXT NOT NULL, status TEXT NOT NULL DEFAULT '待证实',
                  section_id TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 3,
                  priority_reason TEXT DEFAULT '', created_round INTEGER NOT NULL DEFAULT 0,
                  updated_round INTEGER NOT NULL DEFAULT 0, valid_window_days INTEGER,
                  refresh_cycle_days INTEGER, relation_type TEXT,
                  parent_claim_id TEXT, unreachable INTEGER NOT NULL DEFAULT 0)
            """)
            conn.commit()
            conn.close()
            led = LedgerRepository(connect(path), ROOT / "data" / "config")
            try:
                cb_id, _ = install(led, corpus_bound=True)
                self.assertEqual(led.claim_row(cb_id)["corpus_bound"], 1)
            finally:
                led.conn.close()


def _seed_evidence(led: LedgerRepository, claim_id: str, item: dict) -> str:
    eid = led.insert_evidence("evt_cb", item, round_no=0)
    led.associate(claim_id, eid, round_no=0)
    return eid


class _FixedProvider:
    """意见桩:按指定方向全覆盖卷宗;quote 取证据 content 首段(验票必过)。"""

    def __init__(self, plan: dict[str, list[str]]):
        self._plan = plan   # evidence_id -> direction

    def opinion(self, claim, evidence, feedback=""):
        return Opinion(
            claim_id=claim["claim_id"],
            evidence_judgments=[
                EvidenceJudgment(
                    evidence_id=e["evidence_id"],
                    direction=Direction(self._plan.get(e["evidence_id"], "无关")),
                    quote=e["content"][:10],
                ) for e in evidence],
            verdict=ClaimStatus.VERIFIED,
        )


def _engine(led: LedgerRepository, plan: dict) -> VerdictEngine:
    return VerdictEngine(led, authoritative_roots=set(),
                         provider=_FixedProvider(plan), evidence_topk=8)


class TestFirstPartyException(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = LedgerRepository(
            connect(Path(self.tmp.name) / "t.db"), ROOT / "data" / "config")
        self.cb_id, self.plain_id = install(self.led, corpus_bound=True)

    def tearDown(self):
        self.led.conn.close()
        self.tmp.cleanup()

    def _ev(self, item):
        return _seed_evidence(self.led, self.cb_id, item)

    def test_corpus_bound_single_first_party_root_verified(self):
        """corpus_bound + 单 signal_layer 支持根 -> 已证实(自证例外)。"""
        eid = self._ev(ENVELOPE_EVID)
        result = _engine(self.led, {eid: "支持"}).verify(self.cb_id, 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertIn("自证例外", result.note)

    def test_corpus_bound_single_corpus_root_verified(self):
        """corpus_bound + 单语料根(如线索转载)同样放行——"语料这么说"单文本即证。"""
        eid = self._ev(NEWS_EVID)
        result = _engine(self.led, {eid: "支持"}).verify(self.cb_id, 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertIn("自证例外", result.note)

    def test_plain_claim_same_evidence_stays_pending(self):
        """非 corpus_bound 同证据 -> 孤证维持待证实(例外只看标注,代码判决)。"""
        eid = _seed_evidence(self.led, self.plain_id, ENVELOPE_EVID)
        result = _engine(self.led, {eid: "支持"}).verify(self.plain_id, 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)

    def test_no_exception_with_oppose_root(self):
        """有反方根时不放行(保守优先):落回孤证待证实。"""
        e1 = self._ev(ENVELOPE_EVID)
        e2 = self._ev(dict(NEWS_EVID, content="报道称该采购未获证实。"))
        result = _engine(self.led, {e1: "支持", e2: "矛盾"}).verify(self.cb_id, 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)


if __name__ == "__main__":
    unittest.main()


class TestCorpusBoundSignalLayerLink(unittest.TestCase):
    """corpus_bound claim 自动挂 signal_layer 信封证据(2026-10-06)。

    根因:signal_layer 证据 entity_ids 恒空,实体交集挂账失效 → S6 自证例外
    无支持根 → tender 无种子形态全待证实。修复=corpus_bound claim 直接挂
    signal_layer 证据(同根,数根=1,判决等价)。
    """

    def test_corpus_bound_links_signal_layer(self):
        from sense2act.config import load_domain_config
        from sense2act.stages.s5_ledger import LedgerWriter
        with tempfile.TemporaryDirectory() as tmp:
            led = LedgerRepository(connect(Path(tmp) / "t.db"),
                                   ROOT / "data" / "config")
            try:
                env = InvestigationEnvelope(kind="tender", event_id="",
                                            tender=TenderSignal(
                                                project_id="MEDAI-2026-001",
                                                project_name="测试采购项目",
                                                purchaser="测试医院",
                                                published_date="2026-09-20"))
                led.register_event(env, signal_type="商机", event_id="evt_cb")
                led.insert_evidence("evt_cb", ENVELOPE_EVID, round_no=0)
                led.install_blueprint("evt_cb", blueprint(corpus_bound=True))
                w = LedgerWriter(led, load_domain_config("data/config"))
                w.associate_backlog("evt_cb", 0)
                cb = led.conn.execute(
                    "SELECT evidence_id FROM claim_evidence WHERE claim_id='evt_cb:C1'"
                ).fetchall()
                plain = led.conn.execute(
                    "SELECT evidence_id FROM claim_evidence WHERE claim_id='evt_cb:C2'"
                ).fetchall()
                self.assertEqual(len(cb), 1, "corpus_bound claim 应挂 signal_layer 证据")
                self.assertEqual(len(plain), 0, "世界断言不应挂 signal_layer 证据")
            finally:
                led.conn.close()


if __name__ == "__main__":
    unittest.main()
