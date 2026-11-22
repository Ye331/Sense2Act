"""S7 附录 e2e(档1):supplier_b 回放 → 附录落 report.md/html。

(报告文风线第二批 M5,2026-11-08:附录 E mermaid 证据图谱退役→证据链
——md=md-chain 文字版/html=链卷面 svg 内嵌/CDN 退役;旧 mermaid 断言随
语义演进同步注明,照三阶段先例。机器对账=chain-graph json/md 投影,
evidence_graph 模块与单测保留=别处消费。)
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

    def test_report_contains_chain_section(self):
        """附录 E=证据链文字版(md-chain):事实组+三态标注+前提行在位
        (supplier_b 全未裁→无边不画语义,旧 mermaid 三图断言随 M5 语义
        演进注明退役)。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
            md = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("## 附录 E:证据链(文字版)", md)
            self.assertIn("事实组「关于", md)
            self.assertIn("〔待查证〕", md)
            self.assertIn("(前提: C1)", md)             # 前提收编卡内(批1.5)
            self.assertNotIn("flowchart", md)           # mermaid 退役
            self.assertNotIn("mermaid", md)

    def test_html_embeds_chain_svg(self):
        """html 附录=链卷面 svg 内嵌;CDN mermaid 全套退役=离线零依赖
        (断言随 M5 语义演进注明)。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
            html_text = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("附录:证据链", html_text)
            self.assertIn("<svg", html_text)            # 链卷面内嵌
            self.assertNotIn("mermaid", html_text)
            self.assertNotIn("cdn.jsdelivr", html_text)  # CDN 退役
            self.assertIn("本图为离线确定性渲染", html_text)


if __name__ == "__main__":
    unittest.main()
