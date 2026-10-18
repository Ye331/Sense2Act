"""浏览器捕获隔离通道:硬限协议 / 信封解析 / 执行器。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.browser_auto import (  # noqa: E402
    CaptureBlocked,
    CaptureEnvelopeError,
    CaptureGate,
    CaptureLimits,
    CaptureRunner,
    RunnerConfig,
    toutiao_detail_to_lite,
    xhs_items_to_lite,
)


def make_gate(tmp, **limit_kw):
    t0 = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
    clock = {"now": t0}

    def now():
        return clock["now"]

    gate = CaptureGate(Path(tmp) / "state.json",
                       limits=CaptureLimits(**limit_kw), clock=now)
    return gate, clock


class TestCaptureLimits(unittest.TestCase):
    def test_per_word_daily(self):
        gate, _ = make_gate(tempfile.mkdtemp(), per_word_daily=2,
                            min_interval_s=0)
        gate.check("词A"); gate.record("词A")
        gate.check("词A"); gate.record("词A")
        with self.assertRaises(CaptureBlocked):
            gate.check("词A")
        gate.check("词B")                     # 其他词不受影响

    def test_min_interval(self):
        gate, clock = make_gate(tempfile.mkdtemp(), min_interval_s=60)
        gate.check("词A"); gate.record("词A")
        clock["now"] += timedelta(seconds=30)
        with self.assertRaises(CaptureBlocked):
            gate.check("词B")
        clock["now"] += timedelta(seconds=31)
        gate.check("词B")                     # 间隔满足后放行

    def test_lockout_on_block(self):
        gate, clock = make_gate(tempfile.mkdtemp(), per_word_daily=1,
                                min_interval_s=0)
        gate.check("词A"); gate.record("词A")
        gate.record("词A", blocked=True)      # 捕获遇阻 -> 当日自锁
        with self.assertRaises(CaptureBlocked):
            gate.check("词C")                 # 即使换词也拒(当日自锁)
        clock["now"] += timedelta(days=1)
        gate.check("词C")                     # 次日自动解锁

    def test_daily_max(self):
        gate, _ = make_gate(tempfile.mkdtemp(), daily_max=1, min_interval_s=0)
        gate.check("词A"); gate.record("词A")
        with self.assertRaises(CaptureBlocked):
            gate.check("词B")


class TestToutiaoEnvelope(unittest.TestCase):
    def test_valid_envelope(self):
        lite = toutiao_detail_to_lite({
            "captureType": "toutiao_article_detail",
            "articleId": "1730000000001", "url": "https://www.toutiao.com/a1730000000001/",
            "title": "某医院AI采购报道", "publishTime": "2026-09-20T10:00:00",
            "content": "正文内容超过四十字:该项目预算480万元,涵盖影像辅助诊断与临床决策支持两大模块,采购方式为公开招标。"})
        self.assertEqual(lite.platform, "toutiao")
        self.assertEqual(lite.dedup_key, "toutiao:1730000000001")
        self.assertIn("480", lite.content)

    def test_short_body_rejected(self):
        with self.assertRaises(CaptureEnvelopeError):
            toutiao_detail_to_lite({
                "captureType": "toutiao_article_detail", "articleId": "x",
                "url": "https://www.toutiao.com/a1/", "title": "t",
                "content": "太短"})

    def test_wrong_type_rejected(self):
        with self.assertRaises(CaptureEnvelopeError):
            toutiao_detail_to_lite({"captureType": "other"})


class TestXhsEnvelope(unittest.TestCase):
    def test_tolerant_parse(self):
        items = xhs_items_to_lite({"items": [
            {"noteId": "n1", "title": "小红书笔记一", "url": "https://www.xiaohongshu.com/n1",
             "desc": "内容描述", "nickName": "博主甲"},
            {"title": "无ID笔记"},
            "不是字典的脏数据",
        ]})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].platform, "xiaohongshu")
        self.assertEqual(items[0].dedup_key, "xhs:n1")


class TestRunner(unittest.TestCase):
    def test_command_shape_and_dry_run(self):
        runner = CaptureRunner(RunnerConfig(dry_run=True))
        cmd = runner.command("toutiao", url="https://www.toutiao.com/a1/")
        self.assertIn("-d", cmd)
        self.assertIn("toutiao_browser_capture.py", " ".join(cmd))
        self.assertIn("https://www.toutiao.com/a1/", " ".join(cmd))
        result = runner.run("toutiao", url="https://x")   # dry-run 不真跑
        self.assertEqual(result, {})

    def test_unknown_platform(self):
        with self.assertRaises(ValueError):
            CaptureRunner().command("weibo")


if __name__ == "__main__":
    unittest.main()
