"""离线重渲染企业简报(诊断工具,对齐 _inspect.py 先例;只读,不写台账)。

用法:
    python tests/_render_brief.py <run_dir> <envelope.json> <db> <event_id>

从已完成调查的 run 目录(result.json + 台账)重渲染 brief.md——报告双形态
的验收工具:管道改动后重渲染对照,不改任何调查行为。台账先复制到临时目录
再打开(与 evaluate 同款零侵入读法)。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from sense2act.config import load_domain_config  # noqa: E402
from sense2act.models.envelope import InvestigationEnvelope  # noqa: E402
from sense2act.models.intel import Recommendation  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402
from sense2act.stages.d_hooks import TemplateSectionWriter  # noqa: E402
from sense2act.store.db import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _rec_from_dump(d: dict) -> Recommendation:
    """result.json 的 by_alias 转储 -> Recommendation(兼容 snake/camel 键)。"""
    return Recommendation(
        action=d.get("action", ""),
        rationale=d.get("rationale", ""),
        basis_claim_ids=(d.get("basisClaimIds") or d.get("basis_claim_ids") or []),
        hypothesis_label=d.get("hypothesisLabel") or d.get("hypothesis_label") or "")


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    run_dir, envelope_path, db_path = (Path(p) for p in sys.argv[1:4])
    event_id = sys.argv[4]
    # 台账零侵入:复制(含 -wal/-shm)到临时目录再打开
    tmp = Path(tempfile.mkdtemp(prefix="s2a_brief_"))
    local = tmp / db_path.name
    shutil.copy2(db_path, local)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, tmp / side.name)
    conn = connect(str(local))
    ledger = LedgerRepository(conn, ROOT / "data" / "config")

    summary = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    envelope = InvestigationEnvelope.model_validate(
        json.loads(Path(envelope_path).read_text(encoding="utf-8-sig")))
    rec_items = [_rec_from_dump(d) for d in summary.get("recommendations", [])]

    builder = ReportBuilder(ledger, load_domain_config(ROOT / "data" / "config"),
                            None, TemplateSectionWriter(), provider=None,
                            runs_dir=run_dir)
    oq_rows = ledger.oq_claims(event_id)
    evidence = ledger.conn.execute(
        "SELECT * FROM evidence WHERE event_id=? ORDER BY rowid",
        (event_id,)).fetchall()
    text = builder._render_brief(summary, envelope,
                                 envelope.signal_label(), oq_rows,
                                 rec_items, evidence)
    out = run_dir / "brief.md"
    out.write_text(text, encoding="utf-8")
    print(f"[brief] 已重渲染 -> {out}({len(text)} 字符)")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
