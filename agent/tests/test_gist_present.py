# -*- coding: utf-8 -*-
"""gist 短名方案测试(2026-10-30)。

覆盖施工单四机器锁死:
- 写入:ClaimDraft.gist 可选字段(旧 fixture 兼容=pydantic 默认空)、
  install_blueprint 落库、claim_gists 读口只收非空、add_claim 派生条空串;
- 蓝图解析:S1b 输出 JSON 无 gist 键(旧录制件形态)→ 默认空不炸;
- 无 gist fallback:空短名表时各消费端与不传参数逐字节一致(零行为变化);
- 消费面:正文 OQ 节/brief 红旗/已核实/OQ 三行 gist 优先(空=fallback
  截断,gist 超长同截=版面防线);**附录 B 核对面不消费 gist**(2026-10-30
  用户拍板:附录 B=全文核对位,摘要不得顶替核对正文);
- schema:claims.gist 列存在(db.py 加法式迁移照 created_at 先例)。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import (  # noqa: E402
    ALIASES, install_simple, make_domain, make_ledger, rumor_envelope)
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint, ClaimDraft, HypothesisSeed)
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402

_LONG_TEXT = "超长世界断言样本" * 12          # 96 字(附录 B 截 60 断言用)
_G1 = "大连政采公告基础字段"
_G2 = "近两年同等金额采购记录"


def _install_gist(led, event_id: str = "evt_t") -> None:
    """带 gist 的最小蓝图(长短文本混合;C3 无 gist=fallback 形态)。"""
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="信封载明:项目编号 DLZFCG2026448,采购方为"
                                     "某市公安局本级,金额 45232 元,发布日期"
                                     " 2026-10-22",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=4, gist=_G1),
            ClaimDraft(id="C2", text=_LONG_TEXT,
                       category=ClaimCategory.FACT, section_index=2,
                       priority=5, gist=_G2),
            ClaimDraft(id="C3", text="普通条目无短名",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
        ],
        hypotheses=[HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"])],
        gaps=[])
    led.register_event(rumor_envelope(event_id), "", event_id)
    led.install_blueprint(event_id, bp, aliases=ALIASES)


class TestGistStore(unittest.TestCase):
    """数据层:字段默认/落库/读口/迁移。"""

    def test_ClaimDraft默认空_蓝图JSON无gist兼容(self):
        d = ClaimDraft(id="C1", text="x", category=ClaimCategory.FACT,
                       section_index=0)
        self.assertEqual(d.gist, "")           # 旧 fixture 少字段=pydantic 默认
        bp = AnalysisBlueprint.model_validate({
            "claims": [{"id": "C1", "text": "x", "category": "事实",
                        "section_index": 0}],
            "hypotheses": [], "gaps": []})
        self.assertEqual(bp.claims[0].gist, "")   # 旧录制件回放零错位形态

    def test_install落库_读口只收非空(self):
        led = make_ledger()
        _install_gist(led)
        gists = led.claim_gists("evt_t")
        self.assertEqual(gists, {"C1": _G1, "C2": _G2})   # C3 空=不进表
        row = led.conn.execute(
            "SELECT gist FROM claims WHERE draft_id='C3'").fetchone()
        self.assertEqual(row["gist"], "")

    def test_add_claim派生条无gist空串(self):
        led = make_ledger()
        install_simple(led)
        led.add_claim("evt_t", {"id": "C9", "text": "派生条",
                                "category": "事实", "section_index": 2}, 1)
        self.assertEqual(led.claim_gists("evt_t"), {})    # 派生条短名属父断言

    def test_schema_gist列存在(self):
        led = make_ledger()
        cols = {r[1]: r for r in led.conn.execute(
            "PRAGMA table_info(claims)").fetchall()}
        self.assertIn("gist", cols)
        self.assertEqual(cols["gist"][4], "''")   # NOT NULL DEFAULT ''(PRAGMA 带引号)


class _StubWriter:
    """finalize 环① 起草占位(端到端测试不测起草内容,只测 gist 消费面)。"""

    def draft(self, context):
        return f"### {context.get('title', '')}\n(占位)"


class TestGistRender(unittest.TestCase):
    """呈现层:fallback 零变化 + 消费面 + 附录 B 排除。"""

    def _builder(self, led, tmp, ess=False):
        return ReportBuilder(led, make_domain(),
                             InvestigationConfig(ess_enabled=ess),
                             writer=_StubWriter(), runs_dir=tmp)

    def test_空表各消费端与不传逐字节一致(self):
        led = make_ledger()
        install_simple(led)
        rows = led.oq_claims("evt_t")
        claims = led.all_claims("evt_t")
        b = self._builder(led, Path(tempfile.mkdtemp()))
        self.assertEqual(b._render_oq(rows), b._render_oq(rows, gists={}))
        self.assertEqual(b._render_oq(rows), b._render_oq(rows, gists=None))
        self.assertEqual(b._red_flags(None, claims, rows),
                         b._red_flags(None, claims, rows, gists={}))
        self.assertEqual(b._world_verified_lines(claims, {}),
                         b._world_verified_lines(claims, {}, gists={}))

    def test_OQ行gist优先_无gist回退全文(self):
        led = make_ledger()
        _install_gist(led)
        b = self._builder(led, Path(tempfile.mkdtemp()))
        out = b._render_oq(led.oq_claims("evt_t"),
                           gists=led.claim_gists("evt_t"))
        self.assertIn(_G1, out)                    # C1 短名替代全文
        self.assertNotIn("信封载明:项目编号", out)
        self.assertIn("普通条目无短名", out)        # C3 无 gist=全文现状

    def test_红旗与world行gist优先_超长gist截断防线(self):
        led = make_ledger()
        _install_gist(led)
        b = self._builder(led, Path(tempfile.mkdtemp()))
        rows = led.oq_claims("evt_t")
        claims = led.all_claims("evt_t")
        flags = b._red_flags(None, claims, rows, gists=led.claim_gists("evt_t"))
        oq_flag = [f for f in flags if "未决高优断言" in f and "C2" in f][0]
        self.assertIn(_G2, oq_flag)
        self.assertNotIn("超长世界断言样本超长世界断言样本", oq_flag)
        # 超长 gist 同样过截断(版面防线不依赖 prompt 纪律)
        flags_long = b._red_flags(None, claims, rows,
                                  gists={"C2": "超" * 60})
        self.assertIn("…", flags_long[-1])
        # world 行(已证实∧非复述∧世界根)
        wclaims = [{"claim_id": "evt:C1", "draft_id": "C1",
                    "text": "全文断言内容", "status": "已证实",
                    "corpus_bound": 0}]
        wline = b._world_verified_lines(
            wclaims, {"C1": {"support_roots": ["ccgp"]}}, gists={"C1": _G1})
        self.assertIn(_G1, wline[0])
        self.assertNotIn("全文断言内容", wline[0])

    def test_端到端_OQ出gist_附录B排除(self):
        """用户拍板锁死(2026-10-30):阅读面(OQ 节/brief)用短名,
        附录 B 核对面保持全文——摘要不得顶替核对正文。
        报告文风线第一批(P3,2026-11-07)语义演进同步注明:附录 B 条目列
        text[:60] 截断退役(照批1-B 全文呈现拍板,实锚不腰斩),旧断言
        assertIn("…") 随之翻转为全文断言。"""
        led = make_ledger()
        _install_gist(led)
        tmp = Path(tempfile.mkdtemp())
        b = self._builder(led, tmp, ess=False)
        summary = b.finalize("evt_t", 1, "max_rounds", envelope_label="t",
                             signal_type="")
        out_dir = tmp / "evt_t"
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        brief = (out_dir / "brief.md").read_text(encoding="utf-8")
        # 阅读面出短名
        self.assertIn(_G1, report)                 # 正文 OQ 节
        self.assertIn(_G1, brief)                  # brief OQ 行 + 红旗行(P4)
        self.assertIn(_G2, brief)
        # 附录 B 核对面:全文(截断退役),不含 gist
        appendix_b = report.split("## 附录 B")[1].split("## 附录 C")[0]
        self.assertIn(_LONG_TEXT, appendix_b)      # 96 字全文在位=不截断
        self.assertNotIn("…", appendix_b)
        self.assertNotIn(_G2, appendix_b)

    def test_ESS附录内容列gist消费_端到端(self):
        led = make_ledger()
        _install_gist(led)
        tmp = Path(tempfile.mkdtemp())
        b = self._builder(led, tmp, ess=True)
        b.finalize("evt_t", 1, "max_rounds", envelope_label="t",
                   signal_type="")
        report = (tmp / "evt_t" / "report.md").read_text(encoding="utf-8")
        appendix_ess = report.split("附录·证据强度")[1]
        self.assertIn(f"| C1 | {_G1} |", appendix_ess)   # 内容列短名
        self.assertIn(f"| C2 | {_G2} |", appendix_ess)
        self.assertIn("| C3 | 普通条目无短名 |", appendix_ess)  # fallback


class TestGistPrompt(unittest.TestCase):
    """S1B_PROMPT 基座 gist 要求(2026-10-30 拍板措辞)——防误删锚。

    gist 是纯呈现字段零行为参数:直接进基座不加开关(照 ESS 先例),
    三档 variant 都带;改措辞前必复跑 tests/_pc_probe.py(病根族触发
    正清单不受漂移影响才准改),新措辞录制件并入下次 live 批。"""

    def test_S1B_PROMPT含gist要求与拍板措辞锚(self):
        from sense2act import prompts
        text = prompts.S1B_PROMPT
        self.assertIn("gist 短名", text)
        self.assertIn("gist 字段", text)
        # 用户拍板的三条写作纪律锚
        self.assertIn("对象名与关键数字", text)          # ①完整性优先
        self.assertIn("直接省略,不压缩改写", text)       # ②省略非压缩
        self.assertIn("重要部分完整时可放宽", text)      # ③软参考区间(锚不跨行)
        self.assertIn("不带核实状态预判", text)          # 反面清单
        # few-shot 五条对照(拍板示例防漂移)
        for anchor in ("大连市公安局45232元采购公告",
                       "Mate90首发Wi-Fi融合天线记载",
                       "大连公安局与市采购中心委托代理",
                       "大连公安近两年4.5万服装采购",
                       "B公司工商存续无破产记录"):
            self.assertIn(anchor, text)
        # 三档 variant 拼接后仍带(基座追加 rubric 不剥离)
        for v in ("粗", "中", "细"):
            self.assertIn("gist 短名", prompts.s1b_prompt(v))


if __name__ == "__main__":
    unittest.main()
