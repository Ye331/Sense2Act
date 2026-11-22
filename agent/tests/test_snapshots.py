"""B 线招标公告截图(2026-10-23):本地执行器 dry-run / 确定性选题 / 硬限闸。

零网络:playwright 不被导入(dry-run 路径纯打印);真拍路径用 FakeRunner
替身;硬限用真 CaptureGate(临时状态文件,min_interval=0 避免真等待)。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import install_simple, make_ledger  # noqa: E402
from sense2act.browser_auto.limits import (  # noqa: E402
    CaptureGate,
    CaptureLimits,
)
from sense2act.browser_auto.local_runner import (  # noqa: E402
    CaptureResult,
    LocalPlaywrightRunner,
)
from sense2act.stages.s7_assets import AssetCollector, AssetConfig  # noqa: E402

SHOT_PNG = b"\x89PNG\r\n\x1a\n" + b"s" * 4096
CCGP_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/t20261022_10001.htm"
TJ_URL = "http://ccgp-tianjin.gov.cn/notice/2001.htm"
FOREIGN_URL = "https://not-gov.example.com/notice/1.htm"


class FakeRunner:
    """截图替身:dry-run 只记录;真拍落盘伪 PNG(full+关键行小图)。"""

    def __init__(self, dry_run: bool = False, key_labels: tuple = ()):
        self.dry_run = dry_run
        self.key_labels = key_labels
        self.calls: list[str] = []

    def capture(self, url, out_path, key_patterns=()):
        self.calls.append(str(url))
        result = CaptureResult()
        if self.dry_run:
            return result
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(SHOT_PNG)
        result.full = p
        for label in self.key_labels:
            kp = p.with_name(f"{p.stem}_key_{label}.png")
            kp.write_bytes(SHOT_PNG)
            result.keys.append((label, kp))
        return result


def _make_gate(tmp, **limits) -> CaptureGate:
    path = Path(tmp) / "capture_state.json"
    return CaptureGate(path, limits=CaptureLimits(**limits))


class TestLocalRunnerDryRun(unittest.TestCase):
    def test_dry_run_prints_and_returns_none(self):
        runner = LocalPlaywrightRunner(dry_run=True)
        with mock.patch("sys.stdout.write") as w:
            out = runner.screenshot(CCGP_URL, "x.png")
        self.assertIsNone(out)
        self.assertTrue(any("dry-run" in str(c) and CCGP_URL in str(c)
                            for c in w.call_args_list))


class TestSnapshotSelection(unittest.TestCase):
    """确定性选题:招标域 ∧ 裁定引用 ∧ claim 已证实 ∧ 域白名单。"""

    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)
        self.runs = tempfile.mkdtemp()
        # C1 已证实 + ccgp 证据裁定引用;C2 待证实 + ccgp 证据(不应入选);
        # C3 已证实 + ccgp 证据但无裁定(不应入选);foreign 域(不应入选)
        e1 = self._add_ccgp("evt_t", CCGP_URL, "1")
        e2 = self._add_ccgp("evt_t", CCGP_URL.replace("10001", "10002"), "2")
        e3 = self._add_ccgp("evt_t", CCGP_URL.replace("10001", "10003"), "3")
        self._add_ccgp("evt_t", FOREIGN_URL, "4")
        self.led.associate("evt_t:C1", e1, 1)
        self.led.set_ruling("evt_t:C1", e1, "支持", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.associate("evt_t:C2", e2, 1)
        self.led.set_ruling("evt_t:C2", e2, "支持", 1)   # claim 仍待证实
        self.led.associate("evt_t:C3", e3, 1)             # 无裁定
        self.led.update_claim_status("evt_t:C3", "已证实", None, 1)
        self.e1 = e1

    def _add_ccgp(self, event_id, url, tag):
        eid = self.led.insert_evidence(event_id, {
            "platform": "ccgp", "source": "www.ccgp.gov.cn", "url": url,
            "title": f"公告{tag}", "content": "正文", "dedup_key": f"ccgp:{tag}"},
            1)
        assert eid
        return eid

    def _collector(self, runner, gate=None, **cfg):
        return AssetCollector(self.led, None, runs_dir=self.runs,
                              config=AssetConfig(**cfg) if cfg else None,
                              snapshot_runner=runner, snapshot_gate=gate)

    def test_selection_rules(self):
        runner = FakeRunner()
        gate = _make_gate(self.runs, min_interval_s=0)
        n = self._collector(runner, gate).snapshots("evt_t")
        self.assertEqual(n, 1)
        self.assertEqual(runner.calls, [CCGP_URL])   # 只命中 e1(白名单/已证实/有裁定)
        asset = self.led.evidence_assets("evt_t")[self.e1][0]
        self.assertEqual(asset["kind"], "screenshot")
        import hashlib
        self.assertEqual(asset["sha256"],
                         hashlib.sha256(SHOT_PNG).hexdigest())
        self.assertTrue((Path(self.runs) / "evt_t" / "assets"
                         / "E1_shot.png").exists())

    def test_snapshot_cap(self):
        e5 = self._add_ccgp("evt_t", TJ_URL, "5")
        self.led.associate("evt_t:C1", e5, 1)
        self.led.set_ruling("evt_t:C1", e5, "支持", 1)
        runner = FakeRunner()
        gate = _make_gate(self.runs, min_interval_s=0)
        n = self._collector(runner, gate, snapshot_cap=1).snapshots("evt_t")
        self.assertEqual(n, 1)

    def test_dry_run_zero_cost(self):
        runner = FakeRunner(dry_run=True)
        gate = _make_gate(self.runs, min_interval_s=0)
        n = self._collector(runner, gate).snapshots("evt_t")
        self.assertEqual(n, 0)
        self.assertEqual(runner.calls, [CCGP_URL])   # 打印了计划
        self.assertEqual(gate.state.daily_count, 0)  # 未计硬限
        self.assertEqual(self.led.evidence_assets("evt_t"), {})

    def test_daily_max_stops_whole_step(self):
        e5 = self._add_ccgp("evt_t", TJ_URL, "5")
        self.led.associate("evt_t:C1", e5, 1)
        self.led.set_ruling("evt_t:C1", e5, "支持", 1)
        runner = FakeRunner()
        gate = _make_gate(self.runs, min_interval_s=0, daily_max=1)
        n = self._collector(runner, gate).snapshots("evt_t")
        self.assertEqual(n, 1)                 # 第一张成功,日额耗尽后整步停
        self.assertEqual(len(runner.calls), 1)  # 第二张在闸前即停,未达 runner

    def test_runner_none_is_noop(self):
        self.assertEqual(self._collector(None).snapshots("evt_t"), 0)

    def test_key_clips_attached_with_caption(self):
        """关键行小图:正文区 1 张 + 每词 1 张,带 caption;一次访问一并产出。"""
        runner = FakeRunner(key_labels=("预算金额", "投标截止时间"))
        gate = _make_gate(self.runs, min_interval_s=0)
        n = self._collector(runner, gate).snapshots("evt_t")
        self.assertEqual(n, 1)                      # URL 级计数=1
        self.assertEqual(len(runner.calls), 1)      # 单次浏览器访问
        assets = self.led.evidence_assets("evt_t")[self.e1]
        self.assertEqual(len(assets), 3)            # 正文区 + 2 关键行
        captions = [a["caption"] for a in assets]
        self.assertIn("公告正文区截图", captions)
        self.assertIn("关键信息:预算金额", captions)
        self.assertIn("关键信息:投标截止时间", captions)
        for a in assets:
            self.assertTrue((Path(self.runs) / "evt_t" / a["path"]).exists())

    def test_selection_follows_ledger_order_not_lexical(self):
        """回归(2026-10-23 live 实证):入账序按 rowid,不按 evidence_id 字符串
        序——否则 E13/E14 排在 E2 前,目标公告(source_url 直抓最早入账)反而
        落在截图名额之外。"""
        # 追加 3 条更晚入账的 ccgp 证据(E5..E7),同样挂在已证实 C1 上
        urls = [CCGP_URL.replace("10001", f"2000{n}") for n in (5, 6, 7)]
        for i, u in enumerate(urls):
            e = self._add_ccgp("evt_t", u, f"s{i}")
            self.led.associate("evt_t:C1", e, 2)
            self.led.set_ruling("evt_t:C1", e, "支持", 2)
        runner = FakeRunner()
        gate = _make_gate(self.runs, min_interval_s=0, daily_max=5)
        n = self._collector(runner, gate, snapshot_cap=3).snapshots("evt_t")
        self.assertEqual(n, 3)
        # 入账序:最早入账的 E1(目标公告语义)必须排进前三,而非 E5..E7
        self.assertEqual(runner.calls[:1], [CCGP_URL])

    def test_no_gate_real_run_skipped(self):
        runner = FakeRunner()
        n = self._collector(runner, gate=None).snapshots("evt_t")
        self.assertEqual(n, 0)                 # 真拍缺闸=整步跳过(纪律)


class TestAdmitIntervalWait(unittest.TestCase):
    """最小间隔:等满一次重试;等待异常(时钟漂移)即停,不无限睡。"""

    def setUp(self):
        self.led = make_ledger()
        self.tmp = tempfile.mkdtemp()

    def _gate(self, clock):
        return CaptureGate(Path(self.tmp) / "s.json",
                           limits=CaptureLimits(min_interval_s=60),
                           clock=clock)

    def test_interval_blocked_waits_once_then_gives_up(self):
        fixed = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
        gate = self._gate(lambda: fixed)
        gate.check("shot:a")
        gate.record("shot:a")
        # last_ts=fixed;真实 now 远大于 fixed → elapsed 巨大 → wait<=0 → 即停
        collector = AssetCollector(self.led, None, runs_dir=self.tmp,
                                   snapshot_gate=gate)
        with mock.patch("sense2act.stages.s7_assets.time.sleep") as sl:
            self.assertFalse(collector._admit("b"))
        sl.assert_not_called()

    def test_interval_wait_sleeps_then_rechecks(self):
        gate = self._gate(
            lambda: datetime.now(timezone.utc) - timedelta(seconds=1))
        gate.check("shot:a")
        gate.record("shot:a")      # last_ts = now-1s → elapsed≈1s,wait≈60
        collector = AssetCollector(self.led, None, runs_dir=self.tmp,
                                   snapshot_gate=gate)
        with mock.patch("sense2act.stages.s7_assets.time.sleep") as sl:
            self.assertFalse(collector._admit("b"))   # 睡完仍 <60s → 放弃
        sl.assert_called_once()
        waited = sl.call_args.args[0]
        self.assertGreater(waited, 0)
        self.assertLessEqual(waited, 65)


if __name__ == "__main__":
    unittest.main()
