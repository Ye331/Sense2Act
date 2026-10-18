"""改造④(规划校准率)单元:验收标准判型器 + 校准率取数。

分权纪律:模型判型(封闭三选一,须引原句)/代码落档;判型失败降级"未判"
不阻断主流程;先判后用,不改判证据。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.evaluate import calibration_rate  # noqa: E402
from sense2act.stages.calibrate import Calibrator  # noqa: E402
from helpers import make_ledger  # noqa: E402


class FakeLLM:
    def __init__(self, payload=None, exc=None):
        self._payload = payload or {}
        self._exc = exc
        self.calls = []

    def generate_json(self, system, user, stage=""):
        self.calls.append(stage)
        if self._exc:
            raise self._exc
        return dict(self._payload)


class FakeSheet:
    main_target = "c11"

    class _Step:
        gap, acceptance = "找到官方一手信源", "存在讯飞官方公告且载明联合研发"

    gap_chain = [_Step()]


class FakeObs:
    items = [type("I", (), {"title": "讯飞官网公告:联合发布 3.0Pro"})()]
    source_reports = [type("R", (), {"platform": "toutiao", "status": "success"})()]


class TestCalibrator(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile_dir = Path(__file__).parent / "_cal_tmp"
        self.tmp.mkdir(exist_ok=True)
        self.led = make_ledger()
        self.eid = "evt_cal"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verdict_logged(self):
        """判型"达标"→ decision_log kind='其他' 落档,字段齐全。"""
        led = self.led
        led.log(self.eid, 1, "决策单", {"main_target": "c11"})
        cal = Calibrator(FakeLLM({"verdict": "达标", "quote": "官网公告原句"}),
                         led, "sys")
        cal.assess(self.eid, 1, FakeSheet(), FakeObs())
        rows = [json.loads(r["payload_json"])["calibration"]
                for r in led.decision_log_rows(self.eid)
                if r["kind"] == "其他"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "达标")
        self.assertEqual(rows[0]["quote"], "官网公告原句")
        self.assertEqual(rows[0]["claim_id"], f"{self.eid}:c11")
        self.assertTrue(rows[0]["acceptance"])

    def test_invalid_verdict_downgrades(self):
        """模型输出越界值 → 降级"未判"落档(不采信越界判型)。"""
        led = self.led
        cal = Calibrator(FakeLLM({"verdict": "非常达标", "quote": "x"}),
                         led, "sys")
        cal.assess(self.eid, 1, FakeSheet(), FakeObs())
        rows = [json.loads(r["payload_json"])["calibration"]
                for r in led.decision_log_rows(self.eid) if r["kind"] == "其他"]
        self.assertEqual(rows[0]["verdict"], "未判")

    def test_llm_failure_degrades_without_raise(self):
        """LLM 异常 → "未判"落档,不抛出(辅助环不阻断主流程)。"""
        led = self.led
        cal = Calibrator(FakeLLM(exc=RuntimeError("底座超时")), led, "sys")
        cal.assess(self.eid, 1, FakeSheet(), FakeObs())   # 不应抛
        rows = [json.loads(r["payload_json"])["calibration"]
                for r in led.decision_log_rows(self.eid) if r["kind"] == "其他"]
        self.assertEqual(rows[0]["verdict"], "未判")

    def test_stage_name(self):
        """判型走独立 stage(calibrate)——配对契约与录制回放依赖它。"""
        from sense2act.stages.calibrate import CALIBRATE_STAGE
        llm = FakeLLM({"verdict": "不相关"})
        Calibrator(llm, self.led, "sys").assess(self.eid, 1, FakeSheet(), FakeObs())
        self.assertEqual(llm.calls, ["calibrate"])


class TestCalibrationRate(unittest.TestCase):

    def test_rate_computed(self):
        rows = [{"verdict": "达标"}, {"verdict": "部分相关"}, {"verdict": "达标"}]
        r = calibration_rate(rows)
        self.assertEqual((r["hit"], r["judged"]), (2, 3))
        self.assertEqual(r["rate"], 0.6667)

    def test_empty_returns_none(self):
        """开关未开(无判型记录)→ None,报告不显示该行。"""
        self.assertIsNone(calibration_rate([]))


import json  # noqa: E402  (置底供上方用例引用)

if __name__ == "__main__":
    unittest.main()
