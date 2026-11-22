# -*- coding: utf-8 -*-
"""ESS 呈现层测试(阶段二,2026-10-21;2026-10-26 优先级门控升级)。

覆盖定稿的机器锁死:
- 概率禁词:渲染输出无禁词(§九①;运行时不校验,测试锁);
- 数字不裸奔:分数必带 /100;冲突条不出现数字;非重点行(P<4)不露数字;
- 优先级门控(2026-10-26,kind 分流退役——旧断言随语义反转已注明,
  照 4c9751c 先例:病根=无差别逐条噪音,优先级门控从根上解决):
  附录按 priority 降序,重点条(P≥4)给徽章,其余行「—」;
- 分数构成审计:错位超容差黄牌/未超无牌/零 claim 安全(表驱动);
- 校准带:N<30 零输出(当前恒真,断言不含频率读数字样);
- 挂载:_ess_finalize 成功路径向 report.md/brief.md 追加节;off=零痕迹;
- priority 透传:台账→ess.json(呈现消费,ess-0.2 计分语义零改动)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import install_simple, make_ledger  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.ess_present import (  # noqa: E402
    anchor_line, composition_audit, has_badword, render_appendix)
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]


def _claim(draft_id="c1", status="已证实", anchor="中(自证)", score=60,
           cb=True, text="信封载明金额为一百万元", priority=4):
    return {"claim_id": f"evt:{draft_id}", "draft_id": draft_id,
            "text": text, "category": "事实", "status": status,
            "priority": priority,
            "anchor_name": anchor, "anchor_value": score, "s": 1, "a": False,
            "o": 0, "corpus_bound": cb, "tried": False,
            "valid_window_days": None, "decay": 0, "oppose": 0,
            "score": score, "tier": "中", "steps": []}


def _ess(claims) -> dict:
    return {"event_id": "evt_t", "rule_version": "ess-0.2",
            "as_of": "2026-10-21T00:00:00+00:00", "as_of_round": None,
            "n_claims": len(claims), "tier_counts": {}, "claims": claims}


class TestAnchorLine(unittest.TestCase):
    def test_冲突不评分无数字(self):
        line = anchor_line(_claim(anchor="冲突", score=None))
        self.assertEqual(line, "冲突(不评分)")
        self.assertNotIn("/100", line)

    def test_分数带锚点定语与100分母(self):
        line = anchor_line(_claim())
        self.assertIn("中(自证)", line)
        self.assertIn("60/100", line)


class TestBadwords(unittest.TestCase):
    """禁词表(§九①):呈现文本永不出现概率化措辞。"""

    def test_渲染输出无禁词(self):
        claims = [_claim(), _claim("c2", status="待证实", anchor="弱(孤证)",
                                   score=30, cb=False),
                  _claim("c3", status="矛盾", anchor="冲突", score=None,
                         priority=3)]
        out = render_appendix(_ess(claims))
        self.assertIsNone(has_badword(out),
                          f"出现禁词: {has_badword(out)}")

    def test_has_badword(self):
        self.assertEqual(has_badword("成功概率很高"), "概率")
        self.assertEqual(has_badword("confidence is high"), "confidence")
        self.assertIsNone(has_badword("证据强度 60/100"))


class Test优先级门控(unittest.TestCase):
    """2026-10-26 升级:附录按 priority 降序,重点条(P≥4)给徽章。

    (旧 kind 分流断言随语义反转已注明,照 4c9751c 先例:tender 低调/
    rumor 显性的病根=无差别逐条噪音,优先级门控从根上解决。)
    """

    def test_重点条给徽章_其余行横线(self):
        claims = [_claim("c1", priority=5),
                  _claim("c2", priority=2, status="待证实",
                         anchor="弱(孤证)", score=30, cb=False)]
        out = render_appendix(_ess(claims))
        rows = {ln.split("|")[1].strip(): ln for ln in out.splitlines()
                if ln.startswith("| c")}
        self.assertIn("60/100", rows["c1"])
        self.assertIn("| — |", rows["c2"])
        self.assertNotIn("30/100", rows["c2"])   # 非重点行不露数字

    def test_排序_优先级降_同级分数降_冲突最后_条目升(self):
        claims = [_claim("c1", priority=2, score=60),
                  _claim("c2", priority=5, score=30),
                  _claim("c3", priority=5, score=75),
                  _claim("c4", priority=5, score=None, anchor="冲突")]
        out = render_appendix(_ess(claims))
        # 表头「| 条目 |」非数据行(行提取只收条目号数据行"| c")
        order = [ln.split("|")[1].strip() for ln in out.splitlines()
                 if ln.startswith("| c") and not ln.startswith("| 条目")]
        self.assertEqual(order, ["c3", "c2", "c4", "c1"])

    def test_kind参数不再分流_统一形态(self):
        a = render_appendix(_ess([_claim("c1")]), kind="tender")
        b = render_appendix(_ess([_claim("c1")]), kind="rumor")
        c = render_appendix(_ess([_claim("c1")]), kind="")
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_门控阈值可调(self):
        claims = [_claim("c1", priority=3)]
        out = render_appendix(_ess(claims), priority_min=3)
        self.assertIn("60/100",
                      [ln for ln in out.splitlines()
                       if ln.startswith("| c1 ")][0])

    def test_内容列与中文化列头(self):
        """用户可读化(2026-10-27):列头中文不裸奔;「内容」列=截断,
        gist 短名优先(gists 参数消费位,生产侧 S1b gist 待拍板)。"""
        claims = [_claim("c1", priority=5),
                  _claim("c2", priority=2, status="待证实",
                         anchor="弱(孤证)", score=30, cb=False,
                         text="超长" * 40)]
        out = render_appendix(_ess(claims))
        self.assertIn("| 条目 | 内容 | 优先级 | 核查状态 | 证据强度 |", out)
        row = [ln for ln in out.splitlines() if ln.startswith("| c1 ")][0]
        self.assertIn("信封载明金额为一百万元", row)          # 内容列
        row2 = [ln for ln in out.splitlines() if ln.startswith("| c2 ")][0]
        self.assertIn("…", row2)                              # 超长截断
        self.assertNotIn("| claim |", out)
        # gist 短名优先
        out_g = render_appendix(_ess([_claim("c1")]),
                                gists={"c1": "B公司欠薪传闻"})
        self.assertIn("| c1 | B公司欠薪传闻 |", out_g)

    def test_缺priority字段安全_按非重点处理(self):
        raw = _claim("c1")
        del raw["priority"]                     # 旧 ess.json 兼容
        out = render_appendix(_ess([raw]))
        self.assertIn("| — |",
                      [ln for ln in out.splitlines()
                       if ln.startswith("| c1 ")][0])


class Test数字不裸奔(unittest.TestCase):
    def test_重点行分数带100分母_非重点行不露数字(self):
        claims = [_claim(), _claim("c2", status="待证实", anchor="弱(孤证)",
                                   score=30, cb=False),
                  _claim("c3", status="待证实", anchor="弱(孤证)", score=30,
                         cb=False, priority=2)]
        out = render_appendix(_ess(claims))
        rows = {ln.split("|")[1].strip(): ln for ln in out.splitlines()
                if ln.startswith("| c")}
        self.assertIn("60/100", rows["c1"])
        self.assertIn("30/100", rows["c2"])
        self.assertNotIn("30/100", rows["c3"])   # 非重点行:强度列=「—」
        # 冲突条即便重点也不出现数字
        row_c = render_appendix(_ess([_claim("c9", anchor="冲突",
                                            score=None)]))
        conflict_row = [ln for ln in row_c.splitlines()
                        if ln.startswith("| c9 ")][0]
        self.assertNotIn("/100", conflict_row)
        self.assertIn("冲突(不评分)", conflict_row)


class TestAudit(unittest.TestCase):
    def test_表驱动(self):
        cases = [
            # (claims, tolerance, 期望 flag, 场景)
            ([_claim()], 0.1, False, "全自证锚+全cb=零错位"),
            ([_claim(), _claim("c2", cb=False)], 0.1, True,
             "自证锚1.0/cb0.5=错位0.5 黄牌"),
            ([_claim(), _claim("c2", anchor="弱(孤证)", score=30, cb=False,
                               status="待证实")], 0.1, False,
             "自证锚0.5/cb0.5=零错位"),
        ]
        for claims, tol, want, name in cases:
            a = composition_audit(_ess(claims), tolerance=tol)
            self.assertEqual(a["flag"], want, name)

    def test_零claim安全(self):
        a = composition_audit(_ess([]))
        self.assertFalse(a["flag"])
        self.assertEqual(a["n"], 0)

    def test_黄牌标记进渲染输出(self):
        out = render_appendix(_ess([_claim(), _claim("c2", cb=False)]))
        self.assertIn("黄牌", out)
        ok_out = render_appendix(_ess([_claim()]))
        self.assertIn("无错位", ok_out)
        self.assertNotIn("黄牌", ok_out)


class Test校准带(unittest.TestCase):
    def test_N不足30零输出(self):
        out = render_appendix(_ess([_claim()]))
        self.assertNotIn("推翻", out)      # 频率读数字样不得出现
        self.assertNotIn("校准", out)


class TestMountAppendix(unittest.TestCase):
    """挂载:_ess_finalize 成功路径追加节;off=零痕迹。"""

    def _builder(self, led, tmp, ess=True):
        from helpers import make_domain
        return ReportBuilder(led, make_domain(),
                             InvestigationConfig(ess_enabled=ess),
                             writer=None, runs_dir=tmp)

    def test_成功路径追加两文件(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = Path(tempfile.mkdtemp())
        out_dir = tmp / "evt_t"
        out_dir.mkdir(parents=True)
        for name in ("report.md", "brief.md"):   # 模拟 _deliver 先写完
            (out_dir / name).write_text("# 占位\r\n", encoding="utf-8")
        info = self._builder(led, tmp, ess=True)._ess_finalize(
            "evt_t", 3, out_dir)
        self.assertTrue(info["written"])
        for name in ("report.md", "brief.md"):
            text = (out_dir / name).read_text(encoding="utf-8")
            self.assertIn("附录·证据强度", text)
            self.assertIsNone(has_badword(text))
            self.assertIn("决策卡", text)

    def test_开关关零痕迹(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = Path(tempfile.mkdtemp())
        out_dir = tmp / "evt_t"
        out_dir.mkdir(parents=True)
        (out_dir / "report.md").write_text("# 占位\r\n", encoding="utf-8")
        before = (out_dir / "report.md").read_text(encoding="utf-8")
        info = self._builder(led, tmp, ess=False)._ess_finalize(
            "evt_t", 3, out_dir)
        self.assertIsNone(info)
        self.assertEqual(
            before, (out_dir / "report.md").read_text(encoding="utf-8"))


class TestHtmlSection(unittest.TestCase):
    """html 证据强度节(渲染批次 2026-10-27):_deliver 消费预计算 result,
    与 md 附录同源文本过白名单转换;None=零痕迹;off=_ess_compute 返回 None。"""

    def _builder(self, led, tmp, ess=True):
        from helpers import make_domain
        return ReportBuilder(led, make_domain(),
                             InvestigationConfig(ess_enabled=ess),
                             writer=None, runs_dir=tmp)

    def _deliver(self, builder, tmp):
        summary = {"event_id": "evt_t", "signal_type": "传闻核查",
                   "stop_reason": "max_rounds",
                   "status_counts": {"已证实": 1, "矛盾": 0, "待证实": 2},
                   "standings": [], "relations": [], "transitions": [],
                   "recommendations": [], "costs": {}}
        out_dir = tmp / "evt_t"
        builder._deliver(out_dir, "evt_t", summary, "B公司欠薪传闻",
                         ess_result=builder._ess_compute("evt_t"))
        return out_dir

    def test_html含证据强度节_同源数据(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = Path(tempfile.mkdtemp())
        out_dir = self._deliver(self._builder(led, tmp), tmp)
        html = (out_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn("证据强度(ESS,内部工作底稿,不作对外评级)", html)
        self.assertIn("决策卡", html)                     # 回退风险警示块
        self.assertIn("以下按优先级排列", html)            # 优先级门控说明
        # 禁词范围=ESS 呈现文本(html 全文含证据表"可信度"列名,非 ESS 措辞)
        self.assertIsNone(
            has_badword(render_appendix(self._builder(
                led, tmp)._ess_compute("evt_t"))))

    def test_html_none零痕迹(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = Path(tempfile.mkdtemp())
        summary = {"event_id": "evt_t", "signal_type": "", "stop_reason": "x",
                   "status_counts": {}, "standings": [], "relations": [],
                   "transitions": [], "recommendations": [], "costs": {}}
        out_dir = tmp / "evt_t"
        self._builder(led, tmp)._deliver(out_dir, "evt_t", summary, "t",
                                         ess_result=None)
        html = (out_dir / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("证据强度(ESS", html)

    def test_ess_compute_off返回none(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        self.assertIsNone(
            self._builder(led, Path(tempfile.mkdtemp()), ess=False)
            ._ess_compute("evt_t"))


class TestPriorityPassthrough(unittest.TestCase):
    """priority 从台账透传 ess.json(呈现消费;ess-0.2 计分语义零改动)。"""

    def test_collect与score透传priority(self):
        import datetime as _dt
        from sense2act.credibility import compute_all
        led = make_ledger()
        install_simple(led, "evt_t")
        rules = json.loads(
            (_REPO / "data" / "config" / "ess_rules.json")
            .read_text(encoding="utf-8-sig"))
        out = compute_all(led, "evt_t", rules,
                          as_of=_dt.datetime.now(_dt.timezone.utc),
                          authoritative_roots=set())
        ps = {c["draft_id"]: c["priority"] for c in out["claims"]}
        self.assertEqual(ps["C1"], 5)        # install_simple 蓝图优先级
        self.assertEqual(ps["C2"], 4)        # (蓝图 id 大写,小写曾笔误)
        self.assertEqual(ps["C3"], 4)


if __name__ == "__main__":
    unittest.main()
