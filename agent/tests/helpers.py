"""测试公共件:临时库 + 最小蓝图装配。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sense2act.config import load_domain_config  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.envelope import (  # noqa: E402
    ClueCard,
    InvestigationEnvelope,
    RumorSignal,
)
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    HypothesisSeed,
)
from sense2act.store import LedgerRepository, connect  # noqa: E402

ALIASES = {
    "b公司": "B新能源科技有限公司", "a公司": "A汽车股份有限公司",
    "乙公司": "乙动力科技有限公司", "产业内幕君2026": "产业内幕君2026",
}


def make_ledger() -> LedgerRepository:
    tmp = tempfile.mkdtemp()
    conn = connect(os.path.join(tmp, "t.db"))
    return LedgerRepository(conn, ROOT / "data" / "config")


def make_domain():
    return load_domain_config(ROOT / "data" / "config")


def rumor_envelope(event_id: str = "evt_t") -> InvestigationEnvelope:
    return InvestigationEnvelope(
        kind="rumor", event_id=event_id,
        rumor=RumorSignal(
            subject_name="B公司", summary="B公司欠薪传闻", source="weibo.com",
            signal_time="2026-09-22",
            clues=[ClueCard(clue_id="k1", source="weibo.com", platform="weibo",
                            title="线索", content="B公司 拖欠工资",
                            author_type="自媒体")]))


def install_simple(led: LedgerRepository, event_id: str = "evt_t") -> None:
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C2", text="A公司投资了乙公司", category=ClaimCategory.RELATION,
                       section_index=1, priority=4, relation_type="持股",
                       entity_names=["A公司", "乙公司"]),
            ClaimDraft(id="C3", text="A公司未发布公告", category=ClaimCategory.TEMPORAL,
                       section_index=1, priority=4, valid_window_days=14,
                       entity_names=["A公司"]),
        ],
        hypotheses=[HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"])],
        gaps=[])
    led.register_event(rumor_envelope(event_id), "", event_id)
    led.install_blueprint(event_id, bp, aliases=ALIASES)


def add_evidence(led: LedgerRepository, event_id: str, root: str, content: str,
                 claim_draft: str, round_no: int = 1, **kw) -> str:
    item = {"platform": kw.get("platform", "official_website"),
            "source": kw.get("source", root), "title": kw.get("title", root),
            "content": content, "lineage_root": root,
            "author_type": kw.get("author_type", "官方"),
            "credibility": kw.get("credibility", "高")}
    eid = led.insert_evidence(event_id, item, round_no)
    assert eid
    led.associate(f"{event_id}:{claim_draft}", eid, round_no)
    return eid
