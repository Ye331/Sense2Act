"""S7 证据图谱挂载 e2e(档1):supplier_b 回放 → 附录 E 落 report.md/html。

确定性由既有 test_deterministic_rerun 兜底(其字节级比对现覆盖图节)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

EID = "evt_s7graph"


class TestS7EvidenceGraph(unittest.TestCase):

    def _run(self, tmp: str) -> Path:
        out = Path(tmp) / "runs_out"
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", str(ROOT / "examples/supplier_b/envelope.json"),
             "--replay", str(ROOT / "examples/supplier_b/responses.json"),
             "--seed", str(ROOT / "examples/supplier_b/evidence_seed.json"),
             "--refresh-plan",
             str(ROOT / "examples/supplier_b/refresh_plan.json"),
             "--event-id", EID, "--db", str(Path(tmp) / "t.db"),
             "--runs-dir", str(out)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        return out / EID

    def test_report_contains_graph_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
            md = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("## 附录 E:证据图谱", md)
            self.assertIn("### E.1 实体关系图", md)
            self.assertIn("### E.2 复合事实 ⇒ 竞争假设", md)
            self.assertIn("### E.3 证据 → 复合事实", md)
            self.assertIn("图 B2 省略", md)              # supplier_b 全未裁:B2 占位
            # 边带三态(supplier_b 事实:C5"协同"待证实→虚线;C4"供应"关系矛盾→红)
            self.assertIn('-.->|"? 协同"|', md)
            self.assertIn('-->|"供应 ✗"|', md)
            self.assertIn("linkStyle", md)                  # 矛盾红边样式在场
            self.assertIn("图例", md)                       # 三态图例必须在场

    def test_html_embeds_mermaid(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
            html_text = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("mermaid.min.js", html_text)
            # supplier_b:图 B2 省略(占位为小字)→ 实际 mermaid 块 2 个
            self.assertEqual(html_text.count('class="mermaid"'), 2)
            self.assertIn("mermaid.initialize", html_text)
            # 离线降级指引在场
            self.assertIn("report.md 附录 E", html_text)


if __name__ == "__main__":
    unittest.main()
