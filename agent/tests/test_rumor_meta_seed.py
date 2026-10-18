"""rumor 信封元数据种子开关(2026-10-10)。

纪律:证据环境变更会使冻结录制件卷宗截片错位(录制件配对纪律)——
种子默认关(--rumor-meta-seed off),live 验证/下批重录后转正;
no_extract=True 的种子不入 S4 实体抽取(内部记录不伪造挂账谱系),
只走 associate_backlog ② 的 corpus 直挂道。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import rumor_envelope  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.stages.s1_signal import envelope_evidence_seeds  # noqa: E402


class TestRumorMetaSeed(unittest.TestCase):
    def test_default_off_no_metadata(self):
        """默认关:种子与旧行为逐字节一致(线索卡,无元数据项)。"""
        seeds = envelope_evidence_seeds(rumor_envelope())
        self.assertEqual([s["platform"] for s in seeds], ["weibo"])
        self.assertTrue(all(s["platform"] != "signal_layer" for s in seeds))

    def test_on_appends_metadata_last(self):
        """开启:元数据种子追加在线索卡之后(E 编号零平移),signal_layer 直挂道可用。"""
        seeds = envelope_evidence_seeds(rumor_envelope(), include_metadata=True)
        self.assertEqual(len(seeds), 2)
        meta = seeds[-1]
        self.assertEqual(meta["platform"], "signal_layer")
        self.assertEqual(meta["lineage_root"], "signal_layer")
        self.assertTrue(meta["no_extract"])
        self.assertIn("B公司", meta["content"])           # 主体忠实文本在种子内
        self.assertIn("欠薪传闻", meta["content"])         # 摘要忠实文本在种子内
        # 线索卡仍在首位且未被改动
        self.assertEqual(seeds[0]["platform"], "weibo")

    def test_tender_unaffected(self):
        """tender 路径与开关无关(基础字段+检测器种子保持原样)。"""
        from sense2act.models.envelope import InvestigationEnvelope, TenderSignal
        env = InvestigationEnvelope(kind="tender", tender=TenderSignal(
            project_id="P1", project_name="N", purchaser="U",
            published_date="2026-10-01"))
        for flag in (False, True):
            seeds = envelope_evidence_seeds(env, include_metadata=flag)
            self.assertEqual([s["title"] for s in seeds],
                             ["信号层信封基础字段"])   # 无检测器 → 仅基础字段

    def test_config_default_off(self):
        """配置默认关(录制件配对纪律);CLI 侧由 --rumor-meta-seed on 启用。"""
        self.assertFalse(InvestigationConfig().rumor_meta_seed)


if __name__ == "__main__":
    unittest.main()
