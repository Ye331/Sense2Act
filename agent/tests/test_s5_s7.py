"""S5 记账(派生校验/回溯挂账/调查圈)+ D 钩子 + S7 finalize。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ALIASES, add_evidence, install_simple, make_domain, make_ledger  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.llm import ReplayLLM  # noqa: E402
from sense2act.models.base import ClaimStatus  # noqa: E402
from sense2act.stages.d_hooks import DraftHooks, TemplateSectionWriter  # noqa: E402
from sense2act.stages.s5_ledger import LLMDeriver, LedgerWriter  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402


class TestS5Derive(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)

    def _writer(self, responses):
        return LedgerWriter(self.led, self.domain,
                            LLMDeriver(ReplayLLM(responses),
                                       self.domain.relation_vocab))

    def test_derive_hard_validation(self):
        """派生硬校验:id 冲突跳过/关系类须命中词表且双实体/坏类目跳过;合法项入账。"""
        e = add_evidence(self.led, "evt_t", "weibo.com", "矩阵账号行为", "C1")
        writer = self._writer([{"stage": "s5_derive", "response": {
            "derivedClaims": [
                {"id": "C1", "text": "id 冲突项", "category": "事实",
                 "sectionIndex": 2},
                {"id": "C9", "text": "关系词表未命中", "category": "关系",
                 "sectionIndex": 1, "relationType": "不存在的类型",
                 "entityNames": ["甲", "乙"]},
                {"id": "C10", "text": "合法派生断言", "category": "事实",
                 "sectionIndex": 2, "priority": 4, "hypothesisLinks": ["H0"]},
            ],
            "cohorts": [{"name": "矩阵账号", "criteria": {"d": 1},
                         "members": ["产业内幕君2026"]}],
        }}])
        report = writer.commit("evt_t", 1, [])
        # 无新证据时派生不触发——先验证这一点
        self.assertEqual(report["derived_claims"], [])
        # 重新入账一条新证据触发派生
        add_evidence(self.led, "evt_t", "weibo.com", "第二条不同证据", "C1")
        report = writer.commit("evt_t", 2, [{
            "platform": "weibo", "source": "weibo.com", "title": "新",
            "content": "矩阵账号行为补充", "lineage_root": "weibo.com",
            "entity_ids": []}])
        self.assertEqual(report["derived_claims"], ["C10"])
        self.assertEqual(report["cohorts"], ["矩阵账号"])
        self.assertEqual(
            self.led.claim_row("evt_t:C10")["status"], "待证实")
        self.assertEqual(len(self.led.cohorts_of("evt_t")), 1)

    def test_associate_backlog(self):
        """轮 0 证据在 claim 落库后按实体交集补挂。"""
        item = {"platform": "weibo", "source": "weibo.com", "title": "t",
                "content": "B公司 拖欠工资爆料", "lineage_root": "weibo.com",
                "entity_ids": []}
        # 先入账(无 claim),再装蓝图,再回溯
        writer = LedgerWriter(self.led, self.domain, deriver=None)
        eid = self.led.insert_evidence("evt_t", item, 0)
        linked = writer.associate_backlog("evt_t", 0)
        self.assertEqual(linked, [])  # 此时 claim_entities 已在蓝图装库时建好
        self.led.conn.execute("DELETE FROM claim_evidence")
        self.led.conn.commit()
        linked2 = writer.associate_backlog("evt_t", 0)
        # E1 无实体 id(未过 S4),回溯以 entity_ids 列为准
        self.assertEqual(linked2, [])


class TestDHooks(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)
        self.hooks = DraftHooks(self.led, TemplateSectionWriter())

    def test_hook1_stable_means_terminal(self):
        """稳定即起草:矛盾也是终态——查清了哪怕结论是假的,立刻变现。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        report = self.hooks.run_round_end("evt_t", 1)
        self.assertIn("核心事实", report["drafted"])
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "STABLE_DRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "矛盾"})
        self.assertIn("[矛盾]", sec["draft_text"])

    def test_hook2_drift_redraft(self):
        """翻转即重写:快照漂移 -> STALE -> check/edit -> REDRAFTED,快照刷新。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        self.hooks.run_round_end("evt_t", 1)
        # 世界变化:刷新翻转
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        self.assertEqual(
            [s["title"] for s in self.led.stale_sections("evt_t")], ["核心事实"])
        report = self.hooks.run_round_end("evt_t", 2)
        self.assertIn("核心事实", report["redrafted"])
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "REDRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "已证实"})
        self.assertIn("[已证实]", sec["draft_text"])

    def test_oq_and_recommendation_excluded(self):
        """Open Questions / 行动建议节排除在钩子外(永不稳定,finalize 生成)。"""
        for cid in ("C1", "C2", "C3"):
            self.led.update_claim_status(
                f"evt_t:{cid}", "已证实", None, 1)
        titles = [s["title"] for s in self.led.stable_undrafted_sections("evt_t")]
        self.assertIn("核心事实", titles)
        self.assertIn("动态时间线与关联方", titles)
        self.assertNotIn("Open Questions", titles)
        self.assertNotIn("行动建议", titles)


class TestMdBlockRender(unittest.TestCase):
    """正文 md→HTML 转换(2026-10-27:此前逐行 esc+<br> 连接,md 源码原样
    显示影响阅读)。白名单语法确定性转换;escape 优先,正文不可注入 HTML。"""

    def test_headings_lists_table_quote(self):
        from sense2act.stages.s7_report import _md_block
        md = ("# 节内标题\n正文段落 [1][2]。\n"
              "- 甲 **加粗**\n- 乙\n\n"
              "1. 有序甲\n2. 有序乙\n\n"
              "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
              "> 引用行\n")
        h = _md_block(md)
        self.assertIn("<h3>节内标题</h3>", h)      # #/## 降为 h3,不抢节标题层级
        self.assertIn("<strong>加粗</strong>", h)
        self.assertIn('<sup><a href="#ev-E1">[1]</a></sup>', h)   # 锚到证据表
        self.assertIn("<ul><li>甲", h)
        self.assertIn("<li>乙</li></ul>", h)
        self.assertIn("<ol><li>有序甲</li><li>有序乙</li></ol>", h)
        table = h.split("<table>")[1].split("</table>")[0]
        self.assertIn("<th>A</th>", table)
        self.assertIn("<td>1</td>", table)
        self.assertNotIn("---", table)             # |---|---| 分隔行被吞
        self.assertIn("<blockquote><p>引用行</p></blockquote>", h)

    def test_escape_no_html_injection(self):
        from sense2act.stages.s7_report import _md_block
        h = _md_block('<script>alert(1)</script> **x** [9]')
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("<strong>x</strong>", h)
        self.assertIn('[9]', h)

    def test_strip_dup_heading(self):
        """节内首标题与节标题同文→剥离(html 侧 h2+h3 相邻重复,用户 10-27
        晚报告);不同文/非标题/仅首个非空行之外的同文标题不动。"""
        from sense2act.stages.s7_report import _strip_dup_heading
        self.assertEqual(
            _strip_dup_heading("# 主体与事件背景\n正文甲", "主体与事件背景"),
            "正文甲")
        # 前导空行跳过后仍命中(空行保留——渲染时空行不产出元素)
        self.assertEqual(
            _strip_dup_heading("\n\n## 结论\n内容", "结论"), "\n\n内容")
        # 不同文不剥
        self.assertEqual(
            _strip_dup_heading("# 其他标题\n正文", "主体与事件背景"),
            "# 其他标题\n正文")
        # 正文中间的同文标题不剥(只看首个非空行)
        self.assertEqual(
            _strip_dup_heading("正文甲\n\n# 主体与事件背景\n正文乙",
                               "主体与事件背景"),
            "正文甲\n\n# 主体与事件背景\n正文乙")


class TestS7Finalize(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, deployment="report_only"):
        return ReportBuilder(
            self.led, self.domain,
            InvestigationConfig(deployment_mode=deployment),
            TemplateSectionWriter(), provider=None, runs_dir=self.tmp)

    def test_finalize_full_artifacts(self):
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        # C3 保持待证实 -> Open Questions
        summary = self._builder().finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")
        out = Path(self.tmp) / "evt_t"
        for name in ("report.md", "report.html", "result.json", "audit.json"):
            self.assertTrue((out / name).exists(), name)
        md = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("### Open Questions", md)
        self.assertIn("C3", md)                     # 未决项如实呈现
        self.assertIn("判决核对证书", md)
        self.assertIn("结论变更日志", md)
        self.assertIn("假设记分板", md)
        self.assertEqual(summary["status_counts"]["待证实"], 1)
        self.assertTrue(summary["recommendations"])  # 模板兜底建议(挂依据链)

    def test_action_facing_blocks_stale(self):
        """action_facing:STALE 节在核对通过前禁止对外交付。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        DraftHooks(self.led, TemplateSectionWriter()).run_round_end("evt_t", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        with self.assertRaises(RuntimeError):
            self._builder(deployment="action_facing").finalize(
                "evt_t", 2, "停", envelope_label="x", signal_type="风险")

    def test_report_only_repairs_stale_then_asserts(self):
        """report_only:残留 STALE 节被定向重写修复,终稿核对通过。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        DraftHooks(self.led, TemplateSectionWriter()).run_round_end("evt_t", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        self._builder().finalize("evt_t", 2, "停", envelope_label="x",
                                 signal_type="风险")
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "REDRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "已证实"})


class TestReportStyleBatch1(unittest.TestCase):
    """报告文风线第一批(施工单 20261107 P1-P5):读者不知情原则——过程性
    机器表述(判决梯判据/轮次/台账动词/机制名)撤出人看层,机器口径留附录 B
    与 result.json 两层各锁。OQ 三层卡/附录 A 折叠/附录 B 全文/头部清扫。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, runs_dir=None):
        return ReportBuilder(
            self.led, self.domain, InvestigationConfig(),
            TemplateSectionWriter(), provider=None,
            runs_dir=runs_dir or self.tmp)

    def _finalize(self, stop="轮数上限(硬停兜底;未决 claim 已进 Open Questions)"):
        return self._builder().finalize(
            "evt_t", 2, stop, costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")

    def _report(self):
        return (Path(self.tmp) / "evt_t" / "report.md").read_text(encoding="utf-8")

    def _html(self):
        return (Path(self.tmp) / "evt_t" / "report.html").read_text(encoding="utf-8")

    def _verdict(self, draft, note, sup=None, opp=None):
        self.led.log("evt_t", 1, "判决", {
            "claim_id": f"evt_t:{draft}", "note": note,
            "support_roots": sup or [], "oppose_roots": opp or []})

    def _ev(self, n, root=None):
        item = {"platform": "weibo", "source": root or f"s{n}.com",
                "title": f"标题{n}", "content": f"内容{n}",
                "lineage_root": root or f"s{n}.com", "entity_ids": []}
        return self.led.insert_evidence("evt_t", item, 1)

    def _oq_segment(self):
        md = self._report()
        return md.split("### Open Questions")[1].split("### 行动建议")[0]

    # ------------------------------------------------------------ OQ 三层卡

    def test_oq_card_badge_gist_unreachable(self):
        """①结论行:呈现词徽章(待查证)/gist 优先+无 gist 全文 fallback/
        unreachable 用户话注记(旧机器词不上卡)。"""
        self.led.conn.execute(
            "UPDATE claims SET gist='B公司欠薪' WHERE draft_id='C1'")
        self.led.conn.execute(
            "UPDATE claims SET unreachable=1 WHERE draft_id='C3'")
        self._verdict("C1", "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实")
        self._verdict("C3", "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实")
        self._finalize()
        oq = self._oq_segment()
        self.assertIn("- **〔待查证〕B公司欠薪** · 优先级 5", oq)
        self.assertIn("**〔待查证〕A公司未发布公告** · 优先级 4", oq)  # 无 gist=全文
        self.assertIn("· 优先级 4 · 原链接已不可访问,已登记待补查", oq)
        self.assertNotIn("来源不可达", oq)          # 旧机器词撤出

    def test_oq_material_phrase_four_anchors(self):
        """②材料行映射:四个真实台账锚点各命中(双根印证/权威单源/零根暂无/
        信源条件谓词);锚=裁定证据 [E{n}]。"""
        from sense2act.stages.s7_report import (
            _load_report_phrases, _prefix_phrase, _MATERIAL_FALLBACK)
        tbl = _load_report_phrases(self.domain.config_dir)[
            "report_verdict_phrases"]
        cases = [
            ("独立谱系根 2 个(>=2) -> 已证实", "相互独立"),
            ("权威登记源单源例外 -> 已证实", "权威公示渠道已直接载明"),
            ("支持根 0 个(不足 2 且非权威登记源) -> 维持待证实",
             "暂未检索到能证实或否定"),
            ("含信源条件谓词[blocklist](单源例外不适用),权威支持根 1 个"
             "不足 2 -> 维持待证实", "尚不足以直接证实"),
        ]
        for note, want in cases:
            self.assertIn(want, _prefix_phrase(note, tbl, _MATERIAL_FALLBACK))
        # 集成:note+裁定证据锚上图(证据须挂账该 claim 且裁定方向非空)
        eid = self._ev(1)
        self.led.associate("evt_t:C3", eid, 1)
        self.led.conn.execute(
            "UPDATE claim_evidence SET ruling='支持' WHERE evidence_id=?", (eid,))
        self._verdict("C3", "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实")
        self._finalize()
        oq = self._oq_segment()
        self.assertIn("  - 材料:公开渠道暂未检索到能证实或否定这一条的材料 [E1]", oq)

    def test_phrase_table_editable_and_fallback(self):
        """③映射表改表即生效(改短语→卡面变)+缺键→通用兜底短语。"""
        from sense2act.stages.s7_report import (
            _MATERIAL_FALLBACK, _load_report_phrases, _prefix_phrase,
            _stop_phrase, _STOP_FALLBACK)
        note = "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实"
        self.assertEqual(_prefix_phrase(note, {"支持根": "改表短语X"},
                                        _MATERIAL_FALLBACK), "改表短语X")
        self.assertEqual(_prefix_phrase("全新判据 xyz", {}, _MATERIAL_FALLBACK),
                         _MATERIAL_FALLBACK)      # 缺键=兜底,零错杀零冒充
        self.assertEqual(_stop_phrase("随便什么", {}), _STOP_FALLBACK)
        self.assertEqual(_stop_phrase("", {}), _STOP_FALLBACK)
        # 真实配置两键在位(锁模式表编辑)
        tbl = _load_report_phrases(self.domain.config_dir)
        self.assertIn("report_verdict_phrases", tbl)
        self.assertIn("report_stop_phrases", tbl)

    def test_note_not_on_card_machine_layer_kept(self):
        """④读者不知情负测试:判决 note 原文不上卡;附录 B 原样保留(两层各锁)。"""
        note = "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实"
        self._verdict("C3", note)
        self._finalize()
        self.assertNotIn("支持根", self._oq_segment())   # note 原样不上卡
        md = self._report()
        self.assertIn(note, md)                          # 附录 B 机器层全量

    def test_oq_history_line_variants(self):
        """⑤历史行:有变更=MM-DD 变更链(呈现词);无轮次号无"立项";超 4 截断
        注记;零变更=整行省略;无 ts=只写状态链。"""
        from sense2act.stages.s7_report import _oq_card_lines
        # 零变更(C1)→整行省略
        self._finalize()
        oq = self._oq_segment()
        c1_block = oq.split("- **〔待查证〕B公司拖欠工资**")[1].split("- **〔")[0]
        self.assertNotIn("结论变更", c1_block)
        # 有变更:C3 两跳(待证实→已证实→待证实)
        self.led.update_claim_status("evt_t:C3", "已证实", None, 2)
        self.led.update_claim_status("evt_t:C3", "待证实", None, 3)
        self._finalize()
        oq = self._oq_segment()
        self.assertIn("结论变更:", oq)
        self.assertIn("由待查证转为已查证", oq)
        self.assertIn("由已查证转为待查证", oq)
        self.assertNotIn("R2", oq.split("结论变更")[1].split("\n")[0])
        self.assertNotIn("立项", oq)
        # 无 ts=只写状态链不写日期(旧台账 ts 列晚加,历史行可空串;列 NOT NULL
        # 故以空串模拟旧数据形态)
        self.led.conn.execute(
            "UPDATE claim_transitions SET ts='' WHERE claim_id='evt_t:C3'")
        self._finalize()
        self.assertIn("结论变更:由待查证转为已查证;", self._report())
        # 超 4 节点截断+注记(纯函数,链卡 MAX_HISTORY_NODES 先例)
        trans = [{"old": "待证实", "new": s, "ts": f"2026-11-0{i}T00:00:00"}
                 for i, s in enumerate(
                     ["已证实", "待证实", "矛盾", "待证实", "已证实", "待证实"], 1)]
        row = self.led.claim_row("evt_t:C1")
        lines = _oq_card_lines(row, "", {}, trans, {}, [])
        self.assertEqual(sum(l.count("由") for l in lines if "结论变更" in l), 4)
        self.assertIn("(共 6 次变更,其余略)", "".join(lines))

    # ------------------------------------------------------------ 附录 A 折叠

    def _draft_core(self, text):
        sec = self.led.conn.execute(
            "SELECT section_id FROM sections WHERE event_id='evt_t'"
            " AND title='核心事实'").fetchone()
        snap = {c["draft_id"]: c["status"]
                for c in self.led.claims_for_section(sec["section_id"])}
        self.led.set_draft(sec["section_id"], text, snap, 1)

    def test_appendix_a_folds_unreferenced(self):
        """⑥未被引用折叠+被引用保留;⑩折叠行 id 列表完整。"""
        self._ev(1); self._ev(2)
        self._draft_core("核心事实引用 [1]")           # E1 被正文引用
        self._finalize()
        md = self._report()
        self.assertIn("- **[E1]**", md)                # 被引用=逐条保留
        self.assertNotIn("- **[E2]**", md)             # 未引用=不逐条
        self.assertIn("> 另有 1 条关联检索件未被任何结论引用"
                      "(全文见台账 result.json):E2", md)

    def test_appendix_a_numbering_stable(self):
        """⑦编号稳定不重排:E 号原样,折叠不合并语义(〔同题 N 稿〕保留)。"""
        self._ev(1); self._ev(2); self._ev(3)
        self.led.conn.execute(
            "UPDATE evidence SET title='同题稿' WHERE event_id='evt_t'")
        self._finalize()
        md = self._report()
        # 全部未引用→全部折叠,顺序=入账顺序
        self.assertIn(":E1、E2、E3", md)
        self.assertNotIn("- **[", md.split("附录 A")[1].split("附录 B")[0])

    def test_appendix_a_assets_not_folded(self):
        """⑧带报告附件的证据不折叠(图片条目保持可点回)。"""
        eid = self._ev(2)
        self.led.conn.execute(
            "UPDATE evidence SET assets=? WHERE evidence_id=?",
            ('[{"path":"a.png","caption":"附件图"}]', eid))
        self._finalize()
        md = self._report()
        self.assertIn("- **[E1]**", md)
        self.assertIn("![附件图](a.png)〔仅呈现,非证据〕", md)
        self.assertNotIn("E1、", md.split("附录 A")[1].split("附录 B")[0])

    def test_appendix_a_no_fold_when_all_cited(self):
        """⑨全部被引用=零折叠行(不冒充)。"""
        self._ev(1); self._ev(2)
        self._draft_core("引用 [1] 与 [2]")
        self._finalize()
        seg = self._report().split("附录 A")[1].split("附录 B")[0]
        self.assertNotIn("另有", seg)

    # ------------------------------------------------------------ 附录 B 全文

    def test_appendix_b_full_text(self):
        """⑪全文退役截断:60 位之后的实锚(项目编号)不被腰斩。"""
        tail_anchor = "项目编号1409992026AGK00184全文在位"
        long_text = "背景陈述" * 20 + tail_anchor     # >60 字,锚在尾部
        self.led.conn.execute(
            "UPDATE claims SET text=? WHERE draft_id='C1'", (long_text,))
        self._finalize()
        self.assertIn(tail_anchor, self._report())     # 全文上图不截断

    def test_cert_md_html_same_source(self):
        """⑫md/html 同源函数(_cert_claims):两侧同文,缺判决='-' 兜底。"""
        from sense2act.stages.s7_report import _cert_claims
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        self._finalize()
        rows = _cert_claims(self.led.all_claims("evt_t"),
                            self._builder()._verdict_notes("evt_t"))
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["C3"]["note"], "-")     # 无判决=机器层 '-' 兜底
        html = self._html()
        md = self._report()
        for r in rows:                                 # 同一投影两侧同文
            self.assertIn(r["text"] or "C1", md)
        self.assertIn("内容(全文)", html)             # html 证书卡补全文列
        self.assertIn("B公司拖欠工资", html)

    # ------------------------------------------------------------ P5 头部

    def test_stop_reason_all_enums(self):
        """⑬stop_reason 全枚举用户话(逐值,从代码枚举取全集);机制词不落头部。"""
        from sense2act.stages.s7_report import (
            _load_report_phrases, _stop_phrase)
        tbl = _load_report_phrases(self.domain.config_dir)[
            "report_stop_phrases"]
        # 代码全集(investigation.py):静态值+动态前缀
        real_values = [
            "全部澄清", "问人上限(打断纪律)",
            "证据通道已穷尽(代码兜底:连续 3 轮零新增证据/新 claim)",
            "预算护栏: 上下文超限", "规划器选择停止",
            "轮数上限(硬停兜底;未决 claim 已进 Open Questions)"]
        for v in real_values:
            phrase = _stop_phrase(v, tbl)
            self.assertTrue(phrase and phrase != v or phrase == tbl.get(v))
            self.assertNotIn("兜底", phrase)
            self.assertNotIn("护栏", phrase)
        # 集成:头部出用户话,机器原词撤出
        self._finalize()
        head = self._report().split("## 主体")[0]
        self.assertIn("完成设定调查轮次后收尾(未核实事项见文末「待查证」清单)", head)
        self.assertNotIn("硬停兜底", head)
        self.assertIn("停止原因:完成设定调查轮次后收尾", self._html())

    def test_cost_line_no_parenthetical(self):
        """⑭成本行括号删除(内部架构名不入报告;数字保留)。"""
        self._finalize()
        head = self._report().split("## 主体")[0]
        self.assertIn("成本:模型调用 5 次 / 采集调用 0 次", head)
        self.assertNotIn("分级调度", head)

    def test_header_dominant_user_line(self):
        """⑮领先假设行:无"快照"无裸得分;无主导=(暂无领先假设)。"""
        self._finalize()
        head = self._report().split("## 主体")[0]
        self.assertIn("当前领先假设:(暂无领先假设)", head)
        self.assertNotIn("交付时快照", head)
        # 有主导:标签上图,无得分内部量
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)  # H0(暴雷)成员
        self._finalize()
        head = self._report().split("## 主体")[0]
        self.assertIn("当前领先假设:暴雷", head)
        self.assertNotIn("得分", head)

    # ------------------------------------------------------------ 横切

    def test_deterministic_two_renders(self):
        """⑯确定性:同台账两次 finalize 逐字节一致(渲染零 now 零随机)。"""
        tmp2 = tempfile.mkdtemp()
        self._finalize()
        first = self._report()
        self._builder(runs_dir=tmp2).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")
        second = (Path(tmp2) / "evt_t" / "report.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_injection_defense(self):
        """⑰注入防御:恶意 claim 文本/gist/判决 note 进卡前过 escape;[E1]
        链接化不引入注入面。"""
        self.led.conn.execute(
            "UPDATE claims SET text=?, gist=? WHERE draft_id='C1'",
            ("<script>alert(1)</script>", "<img src=x onerror=alert(2)>"))
        self._verdict("C1", "支持根 0 个<script>(不足 2) -> 维持待证实")
        eid = self._ev(1)
        self.led.associate("evt_t:C1", eid, 1)
        self.led.conn.execute(
            "UPDATE claim_evidence SET ruling='支持' WHERE evidence_id=?", (eid,))
        self._finalize()
        html = self._html()
        # (报告文风线第二批 M5 起 mermaid/CDN 退役,html 零外链脚本;断言
        # 随语义演进注明——恶意载荷仍必须被转义,内嵌链 svg 同样不可注入)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('<a href="#ev-E1">[E1]</a>', html)   # [En] 可点回

    def test_ledger_zero_write_and_brief_lock(self):
        """⑱台账零写入负测试(claims/判决 全量不动)+brief 零变化锁(旧 OQ
        格式与 _dominant_line 得分口径=第一批 brief 零改动拍板;报告文风线
        第二批 M2 起 brief 建议依据行随报告同源人话化,零变化锁在依据行处
        被打破——本锁收窄为 OQ 格式+主导假设口径,照旧断言演进注明先例)。"""
        before = {
            "claims": self.led.conn.execute(
                "SELECT * FROM claims ORDER BY claim_id").fetchall(),
            "verdicts": self.led.conn.execute(
                "SELECT * FROM decision_log WHERE kind='判决' ORDER BY id"
            ).fetchall()}
        self.led.update_claim_status("evt_t:C3", "已证实", None, 1)
        self.led.update_claim_status("evt_t:C3", "待证实", None, 2)
        self._verdict("C3", "支持根 0 个(不足 2 且非权威登记源) -> 维持待证实")
        before2 = {
            "claims": [dict(r) for r in self.led.conn.execute(
                "SELECT * FROM claims ORDER BY claim_id").fetchall()],
            "verdicts": [dict(r) for r in self.led.conn.execute(
                "SELECT * FROM decision_log WHERE kind='判决' ORDER BY id"
            ).fetchall()]}
        self._finalize()
        after = {
            "claims": [dict(r) for r in self.led.conn.execute(
                "SELECT * FROM claims ORDER BY claim_id").fetchall()],
            "verdicts": [dict(r) for r in self.led.conn.execute(
                "SELECT * FROM decision_log WHERE kind='判决' ORDER BY id"
            ).fetchall()]}
        self.assertEqual(before2, after)              # 渲染层零写 claims/判决
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("**条目 C1**(优先级 5)", brief)  # brief OQ 旧格式锁
        self.assertIn("主导假设:", brief)               # _dominant_line 口径锁


class _StubRecProvider:
    """测试注入口(照 test_s7_brief._StubProvider 先例):绕过 LLM 直给建议。"""

    def __init__(self, items):
        self._items = items

    def recommend(self, context):
        return self._items


class TestReportStyleBatch2(unittest.TestCase):
    """报告文风线第二批(施工单 20261108 M 波):总根因=claim 台账文本(机器
    对账口径)被当人看口径直接消费——M1 输入三档分流(挡除/转写/融入,M 波
    先挡)+M2 依据行人话化(gist 化非退役,brief 同改)+M3 转写局限句+
    M4 结论三段卡(四问=凭什么/比过谁/差什么/何时变,剥壳纪律)+M5 附录 E
    mermaid 退役换证据链+M6 ev_fusion 消费。挡除≠抹平:台账三态/附录 B/
    result.json 全量照旧;闸1/闸3 确定性+模式表,改表即生效。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, rec_items=None, **kw):
        cfg = InvestigationConfig(**kw)
        builder = ReportBuilder(self.led, self.domain, cfg,
                                TemplateSectionWriter(), provider=None,
                                runs_dir=self.tmp)
        if rec_items is not None:
            builder._provider = _StubRecProvider(rec_items)
        return builder

    def _finalize(self, rec_items=None, **kw):
        return self._builder(rec_items=rec_items, **kw).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")

    def _report(self):
        return (Path(self.tmp) / "evt_t" / "report.md").read_text(
            encoding="utf-8")

    def _html(self):
        return (Path(self.tmp) / "evt_t" / "report.html").read_text(
            encoding="utf-8")

    def _brief(self):
        return (Path(self.tmp) / "evt_t" / "brief.md").read_text(
            encoding="utf-8")

    def _verdict(self, draft, note, sup=None, opp=None):
        self.led.log("evt_t", 1, "判决", {
            "claim_id": f"evt_t:{draft}", "note": note,
            "support_roots": sup or [], "oppose_roots": opp or []})

    def _add_meta_claims(self):
        """M1 分流锚点条目(照 claim_present_rules.json 前缀,全半角冒号双版
        在真实表):C6/C7=挡除(信封未提供/解读登记),C8/C9=转写(relevance/
        domain),C10=挡除(「信封的…字段均为 null」组合变体,忻州 r2 实账
        锚点,目检实抓后补入表)。"""
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import AnalysisBlueprint, ClaimDraft
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C6", text="信封未提供:三基线历史数据",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
            ClaimDraft(id="C7", text="解读登记了“真实经营动态”等三个种子标签",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=2),
            ClaimDraft(id="C8", text="解读的 basisGaps 载明：该信号与本公司"
                                     "业务的相关性未评估（relevance 为 null）",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
            ClaimDraft(id="C9", text="解读的 basisGaps 载明：domain 字段为空,"
                                     "项目所属领域尚未补齐。",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
            ClaimDraft(id="C10", text="信封的 amountHistory、relevance 字段"
                                      "均为 null,companyProfile 与 domain "
                                      "为空。",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
        ], hypotheses=[], gaps=[])
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)

    def _section(self, md, title):
        return md.split(f"### {title}")[1].split("\n## ")[0]

    # ------------------------------------------------------------ M1 分流

    def test_m1_tier_table_driven(self):
        """①判据纯函数:挡除/转写/不命中三态;改表即生效(改表→档位变);
        缺键=分流全关(照 load_present_rules 缺文件纪律)。"""
        from sense2act.stages.s7_report import (_claim_tier,
                                                _load_report_phrases)
        tbl = {"interpretation": ["解读将本信号定性"],
               "meta_info": ["信封未提供"],
               "report_block_extra": ["解读登记", "解读的 basisGaps 载明"],
               "report_rewrite_prefixes":
                   ["解读的 basisGaps 载明：该信号与本公司业务的相关性未评估"]}
        self.assertEqual(_claim_tier("信封未提供:基线数据", tbl), "block")
        self.assertEqual(_claim_tier("解读登记了三个种子标签", tbl), "block")
        self.assertEqual(_claim_tier("解读的 basisGaps 载明：该采购单位历史"
                                     "记录缺失", tbl), "block")   # 未知变体默认挡
        # 真实表锚点(r2 组合 null 变体,M5 目检实抓后补入)
        real = _load_report_phrases(self.domain.config_dir)
        self.assertEqual(_claim_tier("信封的 amountHistory、relevance 字段"
                                     "均为 null,companyProfile 与 domain 为空。",
                                     real), "block")
        self.assertEqual(_claim_tier("解读的 basisGaps 载明：该信号与本公司"
                                     "业务的相关性未评估（relevance）", tbl),
                         "rewrite")                # 转写长前缀胜挡除短前缀
        self.assertEqual(_claim_tier("B公司拖欠工资", tbl), "")  # 不命中=照旧
        # 改表即生效:删挡除键→原挡除条照旧进正文输入
        self.assertEqual(_claim_tier("信封未提供:基线数据",
                                     {"interpretation": [], "meta_info": []}),
                         "")
        # 半角冒号变体(照 gap_hint_strip 双版先例)
        tbl2 = dict(tbl, report_rewrite_prefixes=[
            "解读的 basisGaps 载明:domain 字段为空"])
        self.assertEqual(_claim_tier("解读的 basisGaps 载明:domain 字段为空,"
                                     "领域未补齐", tbl2), "rewrite")

    def test_m1_block_not_in_input_and_oq_card(self):
        """②挡除类不进正文 LLM 输入(TemplateSectionWriter 节文=输入投影)
        不上 OQ 卡;挡除计数注记;台账三态/附录 B 全量照旧(挡除≠抹平)。"""
        self._add_meta_claims()
        self._finalize()
        md = self._report()
        s2 = self._section(md, "核心事实")
        self.assertNotIn("信封未提供", s2)          # 不进正文输入(闸1)
        self.assertNotIn("解读登记", s2)
        oq = self._section(md, "Open Questions")
        self.assertNotIn("信封未提供", oq)           # 不上 OQ 卡
        self.assertNotIn("解读登记", oq)
        self.assertIn("> 另有 3 条信号层登记信息不进正文", oq)   # 计数注记
        cert = md[md.index("## 附录 B"):md.index("## 附录 C")]
        self.assertIn("信封未提供", cert)            # 台账照旧:附录 B 全量
        self.assertIn("解读登记", cert)
        # 转写类仍在 OQ 卡(卡面显示转写句,机器原文不露)
        self.assertIn("**〔待查证〕本次调查未评估该采购与贵司业务的匹配相关性**"
                      " · 优先级 3", oq)
        self.assertIn("**〔待查证〕项目所属行业领域亦无公开标注** · 优先级 3",
                      oq)
        self.assertNotIn("relevance", oq)            # 字段名不出境(F5)
        self.assertNotIn("domain 字段为空", oq)

    def test_m1_rewrite_limitation_sentence_m3(self):
        """③M3 转写注入:结论卡局限说明合并模板句(零 LLM),无此类 claim
        零痕迹;样句=施工单原文。"""
        self._add_meta_claims()
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.log("evt_t", 1, "判决", {
            "claim_id": "evt_t:C1", "note": "独立谱系根 2 个(>=2) -> 已证实",
            "support_roots": ["a.com", "b.com"], "oppose_roots": []})
        from sense2act.models.intel import AnalysisBlueprint, HypothesisSeed
        self.led.install_blueprint("evt_t", AnalysisBlueprint(
            claims=[], hypotheses=[HypothesisSeed(
                id="H1", label="欠薪属实", claim_ids=["C1", "C8"])], gaps=[]),
            aliases=ALIASES)
        self._finalize()
        con = self._section(self._report(), "结论")
        self.assertIn("- 局限说明:本次调查未评估该采购与贵司业务的匹配相关性,"
                      "项目所属行业领域亦无公开标注。", con)

    # ------------------------------------------------------------ M2 依据行

    def test_m2_basis_line_humanized(self):
        """④依据行人话:gist 优先+剥壳 fallback+机器腔不上行;禁裸原文禁 id;
        rationale 照旧;计数与已查实数如实。"""
        from sense2act.models.intel import Recommendation
        self.led.conn.execute(
            "UPDATE claims SET gist='B公司欠薪' WHERE draft_id='C1'")
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        recs = [Recommendation(
            action="跟踪欠薪事件进展", rationale="已查证事实构成跟踪前提。",
            basis_claim_ids=["C1", "C2", "C3"], hypothesis_label="")]
        self._finalize(rec_items=recs)
        rec = self._section(self._report(), "行动建议")
        self.assertNotIn("依据: C1", rec)            # 机械行退役(F6)
        self.assertIn("依据(3 项,其中 2 项已查实):", rec)   # 计数如实(三态)
        self.assertIn("B公司欠薪 〔已查证〕", rec)     # gist 优先+逐条徽章(M5)
        self.assertIn("A公司投资了乙公司 〔已查证〕", rec)   # 剥壳 fallback
        self.assertIn("A公司未发布公告 〔待查证〕", rec)     # 无 gist 无壳=剥壳全文
        # 机器腔不上行:给 C3 挂解读前缀后不再列举
        self.led.conn.execute(
            "UPDATE claims SET text='解读指出:某事' WHERE draft_id='C3'")
        recs2 = [Recommendation(action="跟踪", rationale="x",
                                basis_claim_ids=["C3"], hypothesis_label="")]
        self._finalize(rec_items=recs2)
        rec2 = self._section(self._report(), "行动建议")
        self.assertNotIn("解读指出", rec2)
        self.assertIn("依据(1 项,其中 0 项已查实):", rec2)

    def test_m2_brief_and_internal_same_source(self):
        """⑤brief 建议下一步与报告内部待办同源人话(第一批 brief 零变化拍板
        自本批依据行处被打破,照旧断言演进注明先例)。"""
        from sense2act.models.intel import Recommendation
        recs = [Recommendation(
            action="补齐历史基线", rationale="x。",
            basis_claim_ids=["C1"], hypothesis_label="")]
        self._finalize(rec_items=recs)
        self.assertIn("依据(1 项,其中 0 项已查实):", self._brief())
        recs2 = [Recommendation(
            action="替换 8 条模拟样本", rationale="x。",
            basis_claim_ids=["C1"], hypothesis_label="")]
        self._finalize(rec_items=recs2)
        md = self._report()
        internal = md[md.index("## 内部待办"):md.index("## 附录 A")]
        self.assertNotIn("依据: C1", internal)
        self.assertIn("依据(1 项,其中 0 项已查实):", internal)

    def test_m2_rerender_old_s5_idempotent(self):
        """⑥旧台账 S5 渲染期人话化(机器行解析→同一渲染函数);已人话化产物
        幂等原样(新台账二次渲染逐字节一致)。"""
        old = ("### 行动建议\n\n每条建议附依据链(可沿 [Ex] 追溯到证据原文);"
               "建议供决策参考,不替代企业决策。\n\n"
               "1. **跟踪欠薪事件** \n   - 依据: C1, C2;已查证事实构成前提。\n")
        from sense2act.stages.s7_report import _parse_rec_draft
        items = _parse_rec_draft(old)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].basis_claim_ids, ["C1", "C2"])
        self.assertEqual(items[0].rationale, "已查证事实构成前提。")
        # 已人话化行不再解析出机器行(幂等)
        self.assertEqual(_parse_rec_draft(
            "   - 依据: 共 2 项(其中 1 项已查实)(x;等,可沿正文引用标追溯);y。\n"),
            [])

    # ------------------------------------------------------------ M4 结论卡

    def _single_lead_fixture(self):
        """单一领先:H1(欠薪属实)=[C1 已证实双根(非权威), C2 已证实权威
        单根 ccgp, C3 待证实],H2(澄清声明)=[C5 已证实独立件]→runner
        (成员非共享);install_simple 的 H0(暴雷)=[C1] 共享成员 runner。"""
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import AnalysisBlueprint, HypothesisSeed
        self.led.add_claim("evt_t", {
            "id": "C5", "text": "乙公司已发布澄清公告",
            "category": ClaimCategory.FACT, "section_index": 2,
            "priority": 3, "entity_names": []}, 1)
        for cid in ("C1", "C2", "C5"):
            self.led.update_claim_status(f"evt_t:{cid}", "已证实", None, 1)
        self._verdict("C1", "独立谱系根 2 个(>=2) -> 已证实",
                      sup=["b.com", "c.com"])
        self._verdict("C2", "权威登记源单源例外 -> 已证实", sup=["ccgp"])
        self._verdict("C5", "独立谱系根 2 个(>=2) -> 已证实",
                      sup=["d.com", "e.com"])
        self.led.install_blueprint("evt_t", AnalysisBlueprint(
            claims=[], hypotheses=[
                HypothesisSeed(id="H1", label="欠薪属实",
                               claim_ids=["C1", "C2", "C3"]),
                HypothesisSeed(id="H2", label="澄清声明", claim_ids=["C5"])],
            gaps=[]), aliases=ALIASES)
        return self.domain

    def test_m4_card_four_questions(self):
        """⑦四问齐:凭什么(当前研判+支撑结构+根数人话 auth_mixed)/比过谁
        (为何不是其他假设)/差什么(还差 N 步)/何时变(flip 人话两触发:
        权威更正句+材料失实回落句);禁 id 禁 (hN) 禁裸原文。"""
        self._single_lead_fixture()
        self._finalize()
        con = self._section(self._report(), "结论")
        self.assertIn("**当前研判:欠薪属实**(领先;2 项事实支撑,无反向证据)",
                      con)
        self.assertIn("- 支撑结构(2 项已查证):", con)
        self.assertIn("  - B公司拖欠工资 〔已查证〕", con)   # M3 逐条徽章
        self.assertIn("── 权威公示渠道已直接载明,其余各条亦获独立来源相互"
                      "印证 ──", con)
        self.assertIn("**为何不是其他假设**:", con)
        self.assertIn("「澄清声明」—— 已查证 1 项,少于当前研判的 2 项", con)
        self.assertIn("**本研判还差 1 步才成立**", con)
        self.assertIn("1. A公司未发布公告 —— 未查实", con)
        self.assertIn("- 何时变:", con)
        self.assertIn("权威渠道的记载若被更正或撤销", con)   # auth 单源 flip
        self.assertIn("支撑材料若被证实失实", con)           # 非 auth 双根 flip
        # 剥壳纪律+禁 id:卡面无 claim id/假设编号;无 gist 时已证实剥壳直陈
        # (卡本体切片=S3 在 S4 前,_section 捕获含 OQ+建议;OQ 卡带 draft_id
        # 为批1 既有设计,不在本禁令内)
        card = con.split("### Open Questions")[0]
        self.assertNotIn("C1", card)
        self.assertNotIn("C3", card)
        self.assertNotIn("C5", card)
        self.assertNotIn("(h", card)
        self.assertNotIn("H1", card)

    def test_m4_tie_and_pending_union(self):
        """⑧并列主导:逐条徽章(第三批复查:已查证/未查实分开,原「两列对照
        挤一行」退役,旧断言随语义演进注明);何时变两触发。"""
        from sense2act.models.intel import AnalysisBlueprint, HypothesisSeed
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        self._verdict("C1", "独立谱系根 2 个(>=2) -> 已证实",
                      sup=["b.com", "c.com"])
        self._verdict("C2", "独立谱系根 2 个(>=2) -> 已证实",
                      sup=["b.com", "c.com"])
        self.led.install_blueprint("evt_t", AnalysisBlueprint(
            claims=[], hypotheses=[
                HypothesisSeed(id="H1", label="欠薪属实", claim_ids=["C1"]),
                HypothesisSeed(id="H2", label="澄清声明", claim_ids=["C2"])],
            gaps=[]), aliases=ALIASES)
        self._finalize()
        con = self._section(self._report(), "结论")
        self.assertIn("并列领先,证据尚不能区分", con)
        self.assertIn("「欠薪属实」", con)
        self.assertIn("「澄清声明」", con)
        self.assertIn("「暴雷」", con)             # install_simple 的 H0 同分并列
        self.assertEqual(con.count("- 支撑结构(1 项已查证):"), 3)
        self.assertIn("  - B公司拖欠工资 〔已查证〕", con)
        self.assertIn("  - A公司投资了乙公司 〔已查证〕", con)
        self.assertIn("── 由两处相互独立的公开来源分别印证 ──", con)
        self.assertNotIn("为何不是其他假设", con)   # 并列态无落选者
        self.assertIn("- 何时变:", con)

    def test_m4_degrade_no_masquerade(self):
        """⑨无已证实支撑→降级一行(失败不伪装);无转写类→局限说明零痕迹;
        无判决→根数行/何时变零痕迹。"""
        self._add_meta_claims()
        self._finalize()
        con = self._section(self._report(), "结论")
        self.assertIn("(尚无足够已查证事实支撑研判", con)
        self.assertNotIn("局限说明", con)

    def test_m4_render_replace_ledger_text_intact(self):
        """⑩渲染期替换:台账 sections 照存原文(双回放对照口径);同输入两次
        渲染逐字节一致(确定性,照离线重渲染工具同路径)。"""
        self._single_lead_fixture()
        b = self._builder()
        b.finalize("evt_t", 2, "轮数上限", envelope_label="x", signal_type="风")
        s3 = [s for s in self.led.get_sections("evt_t")
              if s["section_id"] == "evt_t:S3"][0]
        self.assertIn("主导假设", s3["draft_text"])   # 台账存模板原文
        md = self._report()
        self.assertNotIn("主导假设:", md.split("### Open Questions")[0])
        # 同输入双渲染逐字节一致(离线重渲染工具同路径;rec_items=[] 走
        # draft_text 解析=工具口径)
        sections = self.led.get_sections("evt_t")
        evidence = self.led.conn.execute(
            "SELECT * FROM evidence WHERE event_id=? ORDER BY rowid",
            ("evt_t",)).fetchall()
        verdicts = b._verdict_notes("evt_t")
        summary = json.loads((Path(self.tmp) / "evt_t" / "result.json")
                             .read_text(encoding="utf-8"))
        gists = self.led.claim_gists("evt_t")
        kw = dict(rec_items=[], gists=gists)
        md_a = b._render_markdown(sections, evidence, verdicts, summary, "x",
                                  **kw)
        md_b = b._render_markdown(sections, evidence, verdicts, summary, "x",
                                  **kw)
        self.assertEqual(md_a, md_b)

    # ------------------------------------------------------------ M5 换链

    def test_m5_appendix_e_chain_no_mermaid(self):
        """⑪附录 E:mermaid 三图退役(md 无 flowchart/html 无 CDN+内嵌 svg);
        空链=降级注记(失败不伪装)。"""
        self._finalize()
        md = self._report()
        self.assertIn("## 附录 E:证据链(文字版)", md)
        self.assertNotIn("flowchart", md)
        self.assertNotIn("mermaid", md)
        # 呈现完整化 B-2/B-3(2026-11-09):缺口图退役后待查证清单收编文字版
        # ——无证据事件=零证明材料命题进「待查证(未上图)」小区清单,附录 E
        # 不再空链降级(旧断言「尚无成链的证据关系」随语义演进同步注明,照
        # 三阶段先例;真空链=无 claim 无裁定无小区仍降级,B 线新测试锁)
        self.assertIn("#### 待查证(未上图)", md)
        self.assertIn("〔未触及〕B公司拖欠工资", md)
        html = self._html()
        self.assertIn("<svg", html)                   # 链卷面内嵌
        self.assertIn("附录:证据链", html)
        self.assertNotIn("cdn.jsdelivr", html)        # CDN 退役=离线零依赖
        self.assertNotIn("mermaid", html)

    # ------------------------------------------------------------ M6 融合

    def test_m6_fusion_line_and_kb_gate(self):
        """⑫M6:有效融合旁车→OQ 材料行升「获 N 路独立材料共同印证」;未开
        kb=原判决短语行(K5 门控先例,只读+缺省零痕迹)。"""
        import hashlib
        from sense2act import chain_graph as cgr
        from sense2act.chain_gen import _put, open_insights_rw
        eid1 = add_evidence(self.led, "evt_t", "b.com", "欠薪报道甲", "C1")
        eid2 = add_evidence(self.led, "evt_t", "c.com", "欠薪报道乙", "C1")
        for eid in (eid1, eid2):
            self.led.conn.execute(
                "UPDATE claim_evidence SET ruling='支持' WHERE evidence_id=?",
                (eid,))
        self.led.conn.execute(
            "UPDATE evidence SET author_type='官方' WHERE evidence_id IN (?,?)",
            (eid1, eid2))
        # 独立根达阈但谓词不足→待证实上 OQ 卡(融合卡现实挂载面)
        self._verdict("C1", "含信源条件谓词,支持根 2 个不足 -> 维持待证实",
                      sup=["b.com", "c.com"])
        fps = {}
        for r in self.led.conn.execute(
                "SELECT evidence_id, title, content FROM evidence"
                " WHERE evidence_id IN (?,?)", (eid1, eid2)).fetchall():
            fps[r["evidence_id"]] = cgr.ev_summary_fp(r["title"] or "",
                                                      r["content"] or "")
        ms = sorted([eid1, eid2])
        kbp = Path(self.tmp) / "kb.db"
        conn = open_insights_rw(kbp)
        try:
            _put(conn, sid="fusion:" + hashlib.sha256(
                json.dumps(["evt_t:C1", *ms]).encode()).hexdigest()[:16],
                event_id="evt_t", kind="fusion", claim_id="evt_t:C1",
                hypothesis_id="", value=json.dumps(
                    {"claim": "evt_t:C1",
                     "text": "B公司拖欠工资获2路独立材料共同印证",
                     "members": ms}, ensure_ascii=False, sort_keys=True),
                fingerprint=cgr.fusion_fp(ms, fps), model="fake")
        finally:
            conn.close()
        self._finalize(kb_enabled=True, kb_path=str(kbp))
        oq = self._section(self._report(), "Open Questions")
        self.assertIn("材料:获 2 路独立材料共同印证:B公司拖欠工资获"
                      "2路独立材料共同印证", oq)
        # 未开 kb=原判决短语行(缺省零痕迹)
        self._finalize()
        oq2 = self._section(self._report(), "Open Questions")
        self.assertIn("材料:权威渠道虽有相关记载,尚不足以直接证实", oq2)
        self.assertNotIn("获 2 路独立材料共同印证", oq2)

    # ------------------------------------------------------------ 横切

    def test_forbidden_words_on_card_surfaces(self):
        """⑬读者不知情禁词扫(M 波机械面=OQ 卡/结论卡/依据行):信封/解读
        主语/登记/种子/claim id/(hN) 清零;正文 LLM 节(S0-S2)=P 波口径,
        不在本批机械面。"""
        self._add_meta_claims()
        self._single_lead_fixture()
        self._finalize()
        md = self._report()
        surfaces = (self._section(md, "Open Questions")
                    + self._section(md, "结论")
                    + self._section(md, "行动建议"))
        # 注:挡除计数注记「信号层登记信息」为施工单 M1 拍板措辞(自带
        # "登记"二字),不在禁词列;OQ 卡带 draft_id 为批1 既有设计,id 禁令
        # 只扫结论卡本体+依据行;正文 LLM 节(S0-S2)=P 波口径。
        for w in ("信封", "解读", "种子", "立项", "判决", "独立根",
                  "轮次", "快照", "(h1)", "(h2)"):
            self.assertNotIn(w, surfaces)
        import re
        card = self._section(md, "结论").split("### Open Questions")[0]
        rec = self._section(md, "行动建议")
        self.assertIsNone(re.search(r"\b[Cc]\d+\b", card + rec),
                          (card + rec)[:200])


class TestReportStyleBatch3(unittest.TestCase):
    """报告文风线第三批(施工单 20261108):M1 正文节文字段名/壳词/元陈述口径
    转写(S0-S2)+M2 S1 时间轴+关联方卡+M3 结论卡支撑结构徽章逐条+M4 OQ 去
    id+M5 建议依据逐条徽章+M6 md 证据链对齐(来源去重/裁断/缩进)。全机械层
    零 LLM,读者不知情:英文字段名/信封/解读(作主语)/种子标签/claim id/给定
    证据列表撤出人看正文,机器口径两层各锁(附录 B/result.json)。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, rec_items=None, runs_dir=None, **kw):
        cfg = InvestigationConfig(**kw)
        builder = ReportBuilder(self.led, self.domain, cfg,
                                TemplateSectionWriter(), provider=None,
                                runs_dir=runs_dir or self.tmp)
        if rec_items is not None:
            builder._provider = _StubRecProvider(rec_items)
        return builder

    def _finalize(self, rec_items=None, **kw):
        return self._builder(rec_items=rec_items, **kw).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")

    def _report(self):
        return (Path(self.tmp) / "evt_t" / "report.md").read_text(
            encoding="utf-8")

    def _html(self):
        return (Path(self.tmp) / "evt_t" / "report.html").read_text(
            encoding="utf-8")

    def _section(self, md, title):
        return md.split(f"### {title}")[1].split("\n## ")[0]

    # ------------------------------------------------------------ M1 转写

    def test_m1_field_phrase_table_driven(self):
        """字段名→人话表驱动:驼峰/蛇形双形式;改表即生效;缺键保留原文
        (纪律7,不硬翻)。"""
        from sense2act.stages.s7_report import (_load_report_phrases,
                                                _polish_section_text)
        real = _load_report_phrases(self.domain.config_dir)
        out = _polish_section_text(
            "amountHistory 与 relevance 均为 null，project_id 缺失", real)
        self.assertIn("历史采购金额记录", out)      # 驼峰
        self.assertIn("与本企业业务的相关性", out)   # 驼峰
        self.assertIn("项目编号", out)               # 蛇形
        self.assertNotIn("amountHistory", out)
        self.assertNotIn("relevance", out)
        self.assertNotIn("project_id", out)
        # 改表即生效:改短语→输出变
        tbl = dict(real, report_field_phrases={"amountHistory": "自定义X"})
        self.assertIn("自定义X", _polish_section_text("amountHistory 数据", tbl))
        # 缺键=保留原文(封闭集合防漏,不硬翻)
        self.assertIn("unknownField", _polish_section_text("unknownField", real))

    def test_m1_shell_strip_and_meta(self):
        """壳词剥离(信封载明→事实直陈)+元陈述省略(给定证据列表撤出/登记
        转写事实态)。"""
        from sense2act.stages.s7_report import (_load_report_phrases,
                                                _polish_section_text)
        real = _load_report_phrases(self.domain.config_dir)
        out = _polish_section_text(
            "信封载明：项目编号 1409992026AGK00184，采购方为某公司", real)
        self.assertIn("项目编号 1409992026AGK00184", out)
        self.assertNotIn("信封载明", out)
        out2 = _polish_section_text(
            "该登记情况目前尚未核实(给定证据列表未提供)", real)
        self.assertNotIn("给定证据列表", out2)
        self.assertIn("该情况尚待核实", out2)

    def test_m1_transcribe_ledger_intact(self):
        """节文转写只在渲染层,台账 sections 照存原文(双回放安全);正文节
        字段名/壳词清零(禁词扫)。"""
        self._finalize()
        s0 = [s for s in self.led.get_sections("evt_t")
              if s["section_id"] == "evt_t:S0"][0]
        # 台账 S0 draft_text 原样(转写不动台账)
        md = self._report()
        body = md.split("## 主体与事件背景")[1].split("## 结论")[0]
        for w in ("relevance", "amountHistory", "信封", "解读", "种子"):
            self.assertNotIn(w, body)
        self.assertTrue(s0["draft_text"])  # 台账原文仍在

    # ------------------------------------------------------------ M2 时间轴

    def test_m2_timeline_and_parties(self):
        """S1 时间轴(信封字段用户话+claim 日期事件)+关联方关系卡;无
        "信封载明"机器词。"""
        from sense2act.models.base import ClaimCategory
        self.led.add_claim("evt_t", {
            "id": "C9", "text": "代理机构为某代理公司，投标截止时间为 "
                               "2026年11月13日 09:30",
            "category": ClaimCategory.FACT, "section_index": 1,
            "priority": 3, "entity_names": []}, 1)
        self.led.update_claim_status("evt_t:C9", "已证实", None, 1)
        self._finalize()
        s1 = self._section(self._report(), "动态时间线与关联方")
        self.assertIn("2026-09-22:信号识别", s1)     # 信封 signal_time 用户话
        self.assertIn("2026-11-13 09:30", s1)         # claim 日期事件
        self.assertIn("监测对象", s1)                  # 关联方卡(rumor)
        self.assertIn("代理机构", s1)
        self.assertNotIn("信封载明", s1)

    def test_m2_timeline_no_date_degrade(self):
        """无日期事件=降级一行(纪律7,失败不伪装)。"""
        # 清空信封 signal_time(rumor_envelope 缺省有,造空日期场景)
        self.led.conn.execute(
            "UPDATE events SET envelope_json='{\"kind\":\"rumor\","
            "\"rumor\":{\"subject_name\":\"B公司\",\"summary\":\"x\","
            "\"source\":\"weibo.com\",\"signal_time\":\"\",\"clues\":[]}}'"
            " WHERE event_id='evt_t'")
        self._finalize()
        s1 = self._section(self._report(), "动态时间线与关联方")
        self.assertIn("(无明确日期事件)", s1)

    # ------------------------------------------------------------ M3 徽章逐条

    def test_m3_support_structure_badges(self):
        """结论卡支撑结构逐条徽章;根数人话行保留。"""
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import AnalysisBlueprint, HypothesisSeed
        self.led.add_claim("evt_t", {
            "id": "C5", "text": "乙公司已发布澄清公告",
            "category": ClaimCategory.FACT, "section_index": 2,
            "priority": 3, "entity_names": []}, 1)
        for cid in ("C1", "C2", "C5"):
            self.led.update_claim_status(f"evt_t:{cid}", "已证实", None, 1)
        for cid, sup in (("C1", ["b.com", "c.com"]),
                         ("C2", ["ccgp"]), ("C5", ["d.com", "e.com"])):
            self.led.log("evt_t", 1, "判决", {
                "claim_id": f"evt_t:{cid}", "note": "独立谱系根 2 个(>=2) -> 已证实",
                "support_roots": sup, "oppose_roots": []})
        self.led.install_blueprint("evt_t", AnalysisBlueprint(
            claims=[], hypotheses=[
                HypothesisSeed(id="H1", label="欠薪属实",
                               claim_ids=["C1", "C2", "C3"]),
                HypothesisSeed(id="H2", label="澄清声明", claim_ids=["C5"])],
            gaps=[]), aliases=ALIASES)
        self._finalize()
        con = self._section(self._report(), "结论")
        self.assertIn("- 支撑结构(2 项已查证):", con)
        self.assertIn("  - B公司拖欠工资 〔已查证〕", con)
        self.assertIn("  - A公司投资了乙公司 〔已查证〕", con)
        self.assertIn("──", con)                       # 根数人话行保留

    # ------------------------------------------------------------ M4 去 id

    def test_m4_oq_no_id_appendix_b_keeps(self):
        """OQ 卡去 claim id 只留优先级;附录 B id 仍留(对账不受影响)。"""
        self._finalize()
        oq = self._section(self._report(), "Open Questions")
        self.assertIn("· 优先级 5", oq)
        self.assertNotIn("C1 · 优先级", oq)             # 无 draft_id
        cert = self._report()[self._report().index("## 附录 B"):]
        self.assertIn("C1", cert)                       # 机器层 id 仍留

    # ------------------------------------------------------------ M5 逐条

    def test_m5_basis_badges_rationale_kept(self):
        """建议依据逐条徽章,rationale 原文照旧另起行。"""
        from sense2act.models.intel import Recommendation
        self.led.conn.execute(
            "UPDATE claims SET gist='B公司欠薪' WHERE draft_id='C1'")
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        recs = [Recommendation(action="跟踪欠薪事件", rationale="已查证事实构成前提。",
                               basis_claim_ids=["C1", "C3"], hypothesis_label="")]
        self._finalize(rec_items=recs)
        rec = self._section(self._report(), "行动建议")
        self.assertIn("- 依据(2 项,其中 1 项已查实):", rec)
        self.assertIn("B公司欠薪 〔已查证〕", rec)
        self.assertIn("A公司未发布公告 〔待查证〕", rec)
        self.assertIn("已查证事实构成前提。", rec)      # rationale 照旧

    # ------------------------------------------------------------ M6 对齐

    def test_m6_ev_title_dedup_and_clip(self):
        """证据标题来源去重(词典短语把来源拼进 summary 尾部→剥离,来源只在
        末尾一次)+裁断 30 字。"""
        from sense2act.chain_svg import _clip_ev, _ev_title
        self.assertEqual(_ev_title("公告标题(ccgp.gov.cn)", "", "ccgp.gov.cn",
                                   False), "公告标题")       # 去重
        self.assertEqual(_ev_title("公告标题", "", "ccgp.gov.cn", False),
                         "公告标题")                         # 无重复不动
        self.assertEqual(_ev_title("", "无题", "", True), "〔internal〕材料")
        long_title = "很长的标题" * 10                        # 50 字
        self.assertEqual(_clip_ev(long_title), "很长的标题" * 6 + "…")
        self.assertLessEqual(len(_clip_ev(long_title)), 31)

    # ------------------------------------------------------------ 横切

    def test_batch3_deterministic(self):
        """确定性:同台账两次 finalize 逐字节一致(转写/时间轴零 now 零随机)。"""
        tmp2 = tempfile.mkdtemp()
        self._finalize()
        first = self._report()
        self._builder(runs_dir=tmp2).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")
        second = (Path(tmp2) / "evt_t" / "report.md").read_text(
            encoding="utf-8")
        self.assertEqual(first, second)

    def test_batch3_forbidden_words(self):
        """读者不知情禁词扫(正文 S0-S2):英文字段名/信封/解读/种子清零。"""
        self.led.conn.execute(
            "UPDATE claims SET text='信封未提供 relevance 字段，均 null'"
            " WHERE draft_id='C1'")
        self._finalize()
        body = self._report().split("## 主体与事件背景")[1].split("## 结论")[0]
        for w in ("relevance", "amountHistory", "信封", "解读", "种子",
                  "givenEvidence", "null"):
            self.assertNotIn(w, body)

    # ---------------------------------------------------- 复查机械层(用户目检)

    def test_review_timeline_event_name(self):
        """时间轴事件名提取(复查拍板 A):事件关键词→事件名(最长匹配);
        缺键=空串(fallback 全文不截断);空格日期归一。"""
        from sense2act.stages.s7_report import (ReportBuilder,
                                                _load_report_phrases,
                                                _timeline_event_name)
        real = _load_report_phrases(self.domain.config_dir)
        self.assertEqual(
            _timeline_event_name(
                "代理机构为X,投标截止时间为 2026年11月13日 09:30", real),
            "投标截止")
        self.assertEqual(
            _timeline_event_name(
                "潜在投标人应线上获取招标文件,并于 2026 年 11 月 13 日 "
                "09:30 前递交投标文件", real),
            "投标文件递交截止")                    # 最长关键词优先
        self.assertEqual(_timeline_event_name("无关文本无事件词", real), "")
        # 空格/紧凑日期形态归一(r1/r3 实账双形态)
        self.assertEqual(ReportBuilder._norm_date("2026 年 11 月 13 日 09:30"),
                         "2026-11-13 09:30")
        self.assertEqual(ReportBuilder._norm_date("2026年11月13日 09:30"),
                         "2026-11-13 09:30")

    def test_review_timeline_claim_date_event(self):
        """时间轴 claim 日期事件集成:事件名优先;无事件词=全文不截断(复查:
        机械截断退役)。"""
        from sense2act.models.base import ClaimCategory
        self.led.add_claim("evt_t", {
            "id": "C9", "text": "代理机构为某代理公司,投标截止时间为 "
                               "2026年11月13日 09:30",
            "category": ClaimCategory.FACT, "section_index": 1,
            "priority": 3, "entity_names": []}, 1)
        self.led.update_claim_status("evt_t:C9", "已证实", None, 1)
        self._finalize()
        s1 = self._section(self._report(), "动态时间线与关联方")
        self.assertIn("2026-11-13 09:30:投标截止", s1)
        # 无事件词的 claim 日期:全文不截断(20 字硬裁退役)
        self.led.add_claim("evt_t", {
            "id": "C10", "text": "本公司于 2026年11月15日 完成现场踏勘并"
                                 "取得招标文件补遗通知",
            "category": ClaimCategory.FACT, "section_index": 1,
            "priority": 3, "entity_names": []}, 1)
        self.led.update_claim_status("evt_t:C10", "已证实", None, 1)
        self._finalize()
        s1b = self._section(self._report(), "动态时间线与关联方")
        self.assertIn("2026-11-15:本公司于 2026年11月15日 完成现场踏勘并"
                      "取得招标文件补遗通知", s1b)
        self.assertNotIn("…", s1b.split("2026-11-15:")[1].split("\n")[0])

    def test_review_shell_halfwidth_variants(self):
        """壳词变体(复查 r3 实账):半角冒号「信封载明:」与「信封正文载明」
        (无摘要)剥离——此前「:项目编号」冒号残留/壳词漏剥。"""
        from sense2act.chain_graph import strip_shell
        from sense2act.stages.s7_report import _load_report_phrases
        real = _load_report_phrases(self.domain.config_dir)
        self.assertEqual(
            strip_shell("信封载明:项目编号 1409992026AGK00184", real),
            "项目编号 1409992026AGK00184")
        self.assertEqual(
            strip_shell("信封正文载明潜在投标人应线上获取招标文件", real),
            "潜在投标人应线上获取招标文件")

    def test_review_render_ul_nested(self):
        """嵌套列表渲染(复查:依据行/OQ 材料行「  - 」子项此前平级渲染无
        视觉缩进):同缩进平级,更深缩进嵌套进父 li。"""
        from sense2act.stages.s7_report import _render_ul
        self.assertEqual(
            _render_ul([(0, "A"), (2, "a1"), (2, "a2"), (0, "B")]),
            "<ul><li>A<ul><li>a1</li><li>a2</li></ul></li><li>B</li></ul>")

    def test_review_html_nested_list_and_png(self):
        """html 嵌套列表在位(依据子项嵌父 li)+链图附 png(复查拍板 C:dot
        检测修复后 html=dot svg 无降级注记+同目录 chain.png;无 dot=如实
        降级+零 png,失败不伪装)。"""
        from sense2act.chain_svg import _find_dot
        from sense2act.models.intel import Recommendation
        recs = [Recommendation(action="跟踪", rationale="x",
                               basis_claim_ids=["C1"], hypothesis_label="")]
        self._finalize(rec_items=recs)
        html = self._html()
        self.assertIn("<li>依据(1 项,其中 0 项已查实):<ul><li>", html)
        if _find_dot():
            self.assertNotIn("降级布局", html)
            self.assertIn("另存图片:chain.png", html)
            self.assertTrue((Path(self.tmp) / "evt_t" / "chain.png").exists())
        else:
            self.assertIn("降级布局", html)   # 无 dot=如实降级


class TestReportStyleBatchP(unittest.TestCase):
    """报告文风线 P 波(prompt 口径根治,施工单 20261108 ①e):闸2——P1 节
    起草串讲化+背景节定标/P2 剥壳直陈(输入侧附 gist/plain,五处同源)/
    P3 待证实合并概括禁分号连排/P4 结论首段 80-150 字串讲渲染期提取挂卡
    上方/P5 建议 rationale 三步归纳禁 id。P0 机器闸纯函数=探针(tests/
    _s7_probe.py)与测试同源锁。prompt 改动不影响回放(重放录制输出)。
    P6 新录制件 live 挂下次 live 批(与 gist/K2 同批)。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()
        from sense2act.stages.s7_report import _load_report_phrases
        self.rules = _load_report_phrases(self.domain.config_dir)

    def _builder(self, rec_items=None, **kw):
        from sense2act.config import InvestigationConfig
        cfg = InvestigationConfig(**kw)
        builder = ReportBuilder(self.led, self.domain, cfg,
                                TemplateSectionWriter(), provider=None,
                                runs_dir=self.tmp)
        if rec_items is not None:
            builder._provider = _StubRecProvider(rec_items)
        return builder

    def _finalize(self, rec_items=None, **kw):
        return self._builder(rec_items=rec_items, **kw).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")

    def _report(self):
        return (Path(self.tmp) / "evt_t" / "report.md").read_text(
            encoding="utf-8")

    def _section(self, md, title):
        return md.split(f"### {title}")[1].split("\n## ")[0]

    # ------------------------------------------------------------ P0 机器闸

    def test_p0_ban_scan(self):
        """禁词扫纯函数:好例零命中/坏例分类命中(机器词/驼峰字段/claim id/
        假设编号/壳前缀/分号连排引导句);「立项」业务形态豁免(立项批复=
        采购项目审批文件,探针忻州 --llm 首跑实抓校准);「台账」不入表
        (正文=采购台账业务词)。"""
        from sense2act.stages.s7_report import _BAN_EXTRA, _ban_scan
        rules = self.rules
        self.assertEqual(_ban_scan(
            "本项目预算金额45232元,投标截止2026-11-13。", rules), [])
        self.assertIn("信封", _ban_scan("信封载明:预算若干。", rules))
        self.assertIn("amountHistory",
                      _ban_scan("amountHistory 异常", rules))
        self.assertEqual(_ban_scan("依据 C3 的记载", rules), ["C3"])
        self.assertEqual(_ban_scan("(h1)主导", rules), ["(hN)"])
        self.assertIn("以下事项目前尚未核实",
                      _ban_scan("以下事项目前尚未核实:A;B;C", rules))
        # 立项豁免:业务形态全命中→豁免;调查动词形态→命中
        self.assertEqual(_ban_scan("本次采购的立项批复文件已公开。", rules),
                         [])
        self.assertEqual(_ban_scan("项目立项与核准手续齐备", rules), [])
        self.assertIn("立项", _ban_scan("该 claim 于第 2 轮立项", rules))
        self.assertNotIn("台账", _BAN_EXTRA)

    # ------------------------------------------------------------ P0 提取

    def test_p0_narr_extract(self):
        """结论串讲段提取纯函数:旧台账模板产物(粗体主导行)不提取=零变化;
        普通文本段过三道守门(字数窗 60-220/禁词零命中)才提取;超窗/禁词
        =空串讲(渲染纯卡,失败不伪装)。"""
        from sense2act.stages.s7_report import _narr_extract
        rules = self.rules
        old = "### 结论\n\n**主导假设:暴雷**(得分 3;已证实 1 条)\n\n" \
              "- 备择:正常经营\n"
        self.assertEqual(_narr_extract(old, rules), "")
        good = ("### 结论\n\n" + "当前证据结构更支持正常经营判断:三处独立公开"
                "来源分别印证了公告真实性与采购方同类记录,而另一组假设所需的"
                "资金流向证据尚未查实,两组假设共享同一批公告事实。\n\n"
                "- 支撑结构:x")
        self.assertIn("当前证据结构更支持正常经营判断",
                      _narr_extract(good, rules))
        too_long = "### 结论\n\n" + "字" * 230 + "\n\n- x"
        self.assertEqual(_narr_extract(too_long, rules), "")
        banned = ("### 结论\n\n依据信封载明的内容,本节认为事实清楚,证据充分,"
                  "各项判断与记录相互一致,可以据此形成结论并供后续使用。\n\n- x")
        self.assertEqual(_narr_extract(banned, rules), "")   # 禁词守门
        # 旧 prompt 状态说明段=无研判词特征→不提取(忻州 r1 重渲染实抓:
        # 形态过窗+零禁词仍语义错位,第四道守门治之)
        stale = ("### 结论\n\n关于本公司能否在 2026年11月13日 09:30 前完成"
                 "投标文件编制与递交,目前尚未核实。现有证据尚不足以确认本"
                 "公司具备相应资质与资源条件,该断言仍处于待证实状态,本节"
                 "不作事实认定。\n\n- x")
        self.assertEqual(_narr_extract(stale, rules), "")

    # ------------------------------------------------------------ P1/P3/P5

    def test_p_prompt_discipline(self):
        """prompt 纪律锁(P1 串讲化+背景节定标/P3 合并概括禁分号连排/P4 结论
        80-150/P5 rationale 三步禁 id):关键词在位=改 prompt 不丢纪律。"""
        from sense2act.prompts import S7_DRAFT_PROMPT, S7_RECOMMEND_PROMPT
        for kw in ("研判", "串讲", "信封", "英文字段名", "尚未查实",
                   "禁止用分号", "以下事项目前尚未核实", "80-150",
                   "背景节", "kind=conclusion", "只述证据结构"):
            self.assertIn(kw, S7_DRAFT_PROMPT)
        for kw in ("三步", "前提", "禁止出现 claim 编号", "正例", "反例"):
            self.assertIn(kw, S7_RECOMMEND_PROMPT)

    # ------------------------------------------------------------ P2 输入侧

    def test_p2_claim_input_row(self):
        """公共输入行函数:已证实附 plain=剥壳直陈文本;待证实/矛盾不带 plain
        (三态纪律:待证实保留壳);gist 短名附带;五处起草路径同源。"""
        from sense2act.stages.s7_report import claim_input_row
        rules = self.rules
        row = claim_input_row({"draft_id": "C1", "text": "信封载明:B公司拖欠工资",
                               "status": "已证实", "category": "事实"},
                              ["1"], rules, {"C1": "B公司欠薪"})
        self.assertEqual(row["plain"], "B公司拖欠工资")    # 剥壳直陈
        self.assertEqual(row["gist"], "B公司欠薪")
        self.assertEqual(row["refs"], ["1"])
        row2 = claim_input_row({"draft_id": "C2", "text": "信封载明:某事未决",
                                "status": "待证实", "category": "事实"},
                               [], rules, {})
        self.assertEqual(row2["plain"], "")               # 待证实保留壳
        row3 = claim_input_row({"draft_id": "C3", "text": "某记载",
                                "status": "矛盾", "category": "事实"},
                               [], rules, {})
        self.assertEqual(row3["plain"], "")               # 矛盾不剥

    def test_p2_hooks_context_filter(self):
        """轮间钩子路径(M 波漏滤收口):_context=M1 挡除滤+plain/gist 附带;
        快照语义不变(全量 claims)。"""
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import AnalysisBlueprint, ClaimDraft
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C6", text="信封未提供:三基线历史数据",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
            ClaimDraft(id="C7", text="信封载明:B公司新证据",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3),
        ], hypotheses=[], gaps=[])
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)
        self.led.update_claim_status("evt_t:C7", "已证实", None, 1)
        self.led.conn.execute(
            "UPDATE claims SET gist='B公司新证据' WHERE draft_id='C7'")
        self.led.conn.commit()
        hooks = DraftHooks(self.led, TemplateSectionWriter())
        sec = {"section_id": "evt_t:S2", "title": "核心事实", "kind": "standard"}
        claims = self.led.claims_for_section("evt_t:S2")
        ctx = hooks._context("evt_t", sec, claims)
        ids = [c["id"] for c in ctx["claims"]]
        self.assertNotIn("C6", ids)          # M1 挡除滤(钩子路径收口)
        self.assertIn("C7", ids)
        c7 = next(c for c in ctx["claims"] if c["id"] == "C7")
        self.assertEqual(c7["plain"], "B公司新证据")       # 剥壳附带
        self.assertEqual(c7["gist"], "B公司新证据")
        # 快照仍全量(挡除类也在漂移检测内=挡除≠抹平)
        snap_ids = {c["draft_id"] for c in claims}
        self.assertIn("C6", snap_ids)

    def test_p2_finalize_payload_same_source(self):
        """finalize/edit 路径同判据:_claim_payload=M1 滤+plain/gist(与钩子
        同源公共函数)。"""
        from sense2act.config import InvestigationConfig
        from sense2act.chain_graph import strip_shell
        cfg = InvestigationConfig()
        b = ReportBuilder(self.led, self.domain, cfg,
                          TemplateSectionWriter(), provider=None,
                          runs_dir=self.tmp)
        claims = self.led.claims_for_section("evt_t:S2")
        rows = b._claim_payload(claims, "evt_t")
        self.assertTrue(rows)
        self.assertTrue(all(c["id"] and "plain" in c and "gist" in c
                            for c in rows))
        # plain 判据同源:已证实=剥壳文本,否则空
        by_id = {c["draft_id"]: c for c in claims}
        for c in rows:
            src = by_id[c["id"]]
            want = strip_shell(src["text"], self.rules) \
                if src["status"] == "已证实" else ""
            self.assertEqual(c["plain"], want)

    # ------------------------------------------------------------ P4 渲染

    def test_p4_narr_on_card(self):
        """结论串讲挂卡上方:台账 draft_text 带串讲段→渲染期提取挂机械卡上;
        无串讲(旧台账)=纯卡零占位(逐字节锁)。"""
        from sense2act.stages.s7_report import _narr_extract
        self.led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE draft_id IN ('C1','C2')")
        self.led.conn.commit()
        self._finalize()
        b = self._builder()
        narr = ("当前证据结构更支持暴雷判断:两处独立公开来源分别印证了欠薪"
                "事实与投资关系记载,尚无相反材料,其余假设暂无已查证事实"
                "支撑,证据结构当前趋于一致。")
        stored = f"### 结论\n\n{narr}\n\n- 支撑结构:x\n"
        self.led.set_draft("evt_t:S3", stored, {}, 2)
        out = b._render_conclusion(
            "evt_t", gists=self.led.claim_gists("evt_t"),
            narr=_narr_extract(stored, self.rules))
        self.assertIn(narr, out)
        self.assertLess(out.index(narr), out.index("**当前研判"))
        # 旧台账(模板产物,粗体行开头)=无串讲=零占位
        old = "### 结论\n\n**主导假设:暴雷**(得分 3)\n\n- x"
        self.assertEqual(_narr_extract(old, self.rules), "")

    # ------------------------------------------------------------ P5 输入

    def test_p5_recommend_input_carries_gist(self):
        """建议输入附 gist/plain(同源 claim_input_row);**不做 M1 滤**
        (M2/M5 拍板:机器腔依据不上行但整条建议保留+计数如实=挡除≠抹平,
        滤会丢整条建议给模板兜底——M 波回归锁延续)。"""
        from sense2act.models.intel import Recommendation
        self.led.conn.execute(
            "UPDATE claims SET text='解读指出:某事' WHERE draft_id='C3'")
        self.led.conn.execute(
            "UPDATE claims SET gist='B公司欠薪' WHERE draft_id='C1'")
        self.led.conn.commit()
        b = self._builder(rec_items=[Recommendation(
            action="跟踪", rationale="x", basis_claim_ids=["C3"],
            hypothesis_label="")])
        b._recommend("evt_t", "测试事件")   # 触发输入构造(不炸=同源函数 OK)
        # 渲染语义锁:机器腔依据不上行,但整条建议保留+计数如实
        self._finalize(rec_items=[Recommendation(
            action="跟踪", rationale="x", basis_claim_ids=["C3"],
            hypothesis_label="")])
        rec = self._section(self._report(), "行动建议")
        self.assertNotIn("解读指出", rec)
        self.assertIn("依据(1 项,其中 0 项已查实):", rec)

    def test_p_probe_gate_samples_sync(self):
        """探针与测试同源锁:_s7_probe._GATE_SAMPLES 全过闸(闸改口径探针
        同步,防两处漂移)。"""
        import _s7_probe as probe
        from sense2act.stages.s7_report import (_ban_scan, _bg_subject_ok,
                                                _narr_extract, _narr_ok,
                                                _rationale_ok)
        rules = self.rules
        for gate, text, want in probe._GATE_SAMPLES:
            got = {"ban": lambda: _ban_scan(text, rules),
                   "narr": lambda: _narr_ok(text),
                   "bg": lambda: _bg_subject_ok(text),
                   "rat": lambda: _rationale_ok(text),
                   "extract": lambda: _narr_extract(text, rules)}[gate]()
            self.assertEqual(got, want, f"{gate}: {text[:24]}")


def _ban_extra_words():
    from sense2act.stages.s7_report import _BAN_EXTRA
    return _BAN_EXTRA


class TestReportInsightI1(unittest.TestCase):
    """结论启发式 I1(方案《报告结论启发式呈现-设计方案-20261108》§八机械半,
    2026-11-08 施工):「我们的判断」(T6 观点演化:解读定性经代码翻译通道+
    方向三态 站住/打折/逆转)+「这意味着什么」(T1 基线+固定四拍=窗口→
    有利→不利→待决)。零 LLM 零录制件模板句直出;读者不知情延续(禁「解读」
    二字,观点主语=调查初期/本报告,拍板7);neutral 保守永不出逆转句(拍板9);
    全空=整段零痕迹(拍板5 失败不伪装);确定性逐字节(时间基准=台账结算锚
    非 now,§十相容性自检)。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()
        from sense2act.stages.s7_report import _load_report_phrases
        self.rules = _load_report_phrases(self.domain.config_dir)
        cfg = InvestigationConfig()
        self.builder = ReportBuilder(self.led, self.domain, cfg,
                                     TemplateSectionWriter(), provider=None,
                                     runs_dir=self.tmp)

    def _insight(self, label="投标机会", event_id="evt_t", gists=None,
                 verdicts=None, arg=None):
        claims = self.led.all_claims(event_id)
        by_cid = {c["claim_id"]: dict(c) for c in claims}
        member = list(by_cid)
        if arg is None:
            arg = {"hypothesis_id": f"{event_id}:H1", "label": label,
                   "member_ids": member}
        hc = {arg["hypothesis_id"]:
              list(arg.get("member_ids")) if "member_ids" in arg else member}
        return self.builder._insight_lines(
            event_id, arg, by_cid, gists or {}, self.rules,
            verdicts or {}, hc)

    def _add_interpret(self, text: str, draft: str = "C4") -> None:
        from sense2act.models.intel import ClaimDraft
        from sense2act.models.base import ClaimCategory
        self.led.add_claim("evt_t", {
            "id": draft, "text": text, "category": ClaimCategory.FACT.value,
            "section_index": 0, "priority": 3, "entity_names": []}, 1)

    # ------------------------------------------------------------ T6 纯函数

    def test_stance_and_family_pure(self):
        """定性提取(忻州 r2 实锚:解读将本信号定性为商机,理由为…)+假设
        关键词归族(多者胜/零命中=neutral);_hyp_family 整表传参兼容。"""
        from sense2act.stages.s7_report import _interpret_stance, _hyp_family
        st = _interpret_stance([{"text": "解读将本信号定性为商机，理由为公开"
                                         "招标公告构成潜在投标/供货合作机会"}],
                               self.rules)
        self.assertEqual(st["word"], "商机")
        self.assertEqual(st["family"], "positive")
        self.assertEqual(st["phrase"], "投标机会线索")
        self.assertIn("公开招标公告", st["reason"])
        self.assertIsNone(_interpret_stance(
            [{"text": "采购人预算金额高于历史"}], self.rules))   # 非解读=无观点
        self.assertEqual(_hyp_family("投标机会", self.rules), "positive")
        self.assertEqual(_hyp_family("市场误读", self.rules), "skeptic")
        self.assertEqual(_hyp_family("风险预警", self.rules), "negative")
        self.assertEqual(_hyp_family("真实经营动态", self.rules), "neutral")
        self.assertEqual(_hyp_family("投标机会", {"report_stance_words":
                                                  self.rules["report_stance_words"]}),
                         "positive")                            # 子表传参同效

    # ------------------------------------------------------------ T6 三态

    def test_t6_hold(self):
        """方向一致∧无反向=站住:主语=调查初期(禁「解读」二字),依据=
        解读理由(现成量翻译),措辞走 signal_phrases。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        out = self._insight(label="投标机会")
        self.assertIn("**我们的判断**", out)
        self.assertIn("调查初期将其列为投标机会线索,依据是公开招标公告"
                      "构成潜在参与机会;查证后该判断方向站住。", out)
        self.assertNotIn("解读", out)          # 读者不知情:主语替换
        self.assertNotIn("商机,", out)          # 定性词已被措辞表翻译

    def test_t6_reversal(self):
        """方向不一致=逆转:解读=风险(negative)vs 假设=投标机会(positive)。"""
        self._add_interpret("解读将本信号定性为风险，理由为预算异常")
        out = self._insight(label="投标机会")
        self.assertIn("调查初期将其列为风险信号,依据是预算异常;", out)
        self.assertIn("查证后证据更支持「投标机会」,前期判断需要修正。", out)

    def test_t6_neutral_conservative(self):
        """拍板9:假设族 neutral(零命中)永不出逆转句——按一致处理站住;
        定性词未入表(family 空)同保守。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开渠道可见")
        out = self._insight(label="真实经营动态")
        self.assertIn("方向站住", out)
        self.assertNotIn("前期判断需要修正", out)
        self._add_interpret("解读将本信号定性为蓝色幻影，理由为色号存疑",
                            draft="C5")
        out2 = self._insight(label="真实经营动态")
        self.assertIn("方向站住", out2)
        self.assertNotIn("前期判断需要修正", out2)

    def test_t6_discount_with_contra(self):
        """打折=方向一致∧有反向材料:矛盾成员卡面短语上句(拍板8 反证指向),
        组装固定连接词「;但」+行动含义尾缀。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 2)
        out = self._insight(label="投标机会")
        self.assertIn("查证后方向站住,但B公司拖欠工资是当前最大变数,"
                      "推进前需先核实。", out)

    def test_t6_discount_rival_hypothesis_member(self):
        """忻州 r2 实锚形态:矛盾态 claim(排斥条款)挂竞争假设而非领先假设
        ——对领先假设仍是反向信号,打折句指向它(拍板8;反向材料判据=
        事件级矛盾态);同一条不再进四拍不利拍(不重复点名)。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        self._add_interpret("本项目的招标文件对投标人资格设置了排斥潜在"
                            "竞争者的特定条件", draft="C5")
        self.led.update_claim_status("evt_t:C5", "矛盾", "矛盾翻转", 2)
        out = self._insight(label="投标机会")
        self.assertIn("查证后方向站住,但本项目的招标文件对投标人资格设置了"
                      "排斥潜在竞争者的特定条件是当前最大变数,推进前需先"
                      "核实。", out)
        self.assertEqual(out.count("是当前最大变数"), 1)   # 不重复点名

    # ------------------------------------------------------------ 四拍

    def test_window_beat_with_anchor(self):
        """窗口拍(T2 时效子型):已证实成员带截止日期+结算锚 → 「距投标截止
        (MM-DD HH:MM)仅剩 N 天」;时间基准=last_finalize.json(重渲染稳定),
        事件名走 report_timeline_events 表。"""
        from sense2act.delta_report import ANCHOR_FILE
        self.led.conn.execute(
            "UPDATE claims SET status='已证实', text='投标截止时间为"
            "2026-11-13 09:30' WHERE claim_id='evt_t:C3'")
        self.led.conn.commit()
        (Path(self.tmp) / "evt_t").mkdir(parents=True, exist_ok=True)
        (Path(self.tmp) / "evt_t" / ANCHOR_FILE).write_text(
            json.dumps({"last_finalize_ts": "2026-11-08T10:00:00+00:00"}),
            encoding="utf-8")
        out = self._insight(label="真实经营动态")
        self.assertIn("距投标截止(11-13 09:30)仅剩 5 天", out)

    def test_favor_beat_derived_chain(self):
        """有利拍派生链子型(方案 §四 T2):父待证实∧子已证实 → 「父整体
        尚未查实,但其下子已获查证」。"""
        self.led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE claim_id='evt_t:C1'")
        self.led.conn.execute(
            "UPDATE claims SET parent_claim_id='evt_t:C3'"
            " WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        out = self._insight(label="真实经营动态")
        self.assertIn("「A公司未发布公告」整体尚未查实,但其下「B公司拖欠工资」"
                      "已获查证——方向已有实质支撑", out)

    def test_pending_beat_action(self):
        """待决拍(T3 行动含义):pending 首项+窗口内闭合表述。"""
        out = self._insight(label="真实经营动态")
        self.assertIn("若决定推进,B公司拖欠工资是窗口内要闭合的第一件事", out)

    def test_t1_baseline_from_envelope(self):
        """T1 基线对照(检测器标量→人话):信封 amountHistory 有值 → 倍数句;
        null(忻州实账)→ 零痕迹(没素材不出句,方案附录自检)。"""
        from sense2act.models.envelope import InvestigationEnvelope
        from sense2act.models.intel import AnalysisBlueprint, ClaimDraft, \
            HypothesisSeed
        env = InvestigationEnvelope.model_validate({
            "kind": "tender", "event_id": "evt_i1",
            "tender": {"projectId": "PRJ-1", "projectName": "项目X",
                       "purchaser": "采购人A", "publishedDate": "2026-10-01",
                       "amountHistory": {"amountRatio": 4, "historyCount": 6}}})
        bp = AnalysisBlueprint(claims=[ClaimDraft(
            id="C1", text="采购人本期预算高于历史", category="事实",
            section_index=2, priority=5)],
            hypotheses=[HypothesisSeed(id="H1", label="投标机会",
                                       claim_ids=["C1"])], gaps=[])
        self.led.register_event(env, "", "evt_i1")
        self.led.install_blueprint("evt_i1", bp)
        out = self._insight(label="投标机会", event_id="evt_i1")
        self.assertIn("本次项目金额约为该采购人近期同类项目中位数的 4 倍"
                      "(对照 6 条历史记录)", out)
        env2 = InvestigationEnvelope.model_validate({
            "kind": "tender", "event_id": "evt_i2",
            "tender": {"projectId": "PRJ-2", "projectName": "项目Y",
                       "purchaser": "采购人A", "publishedDate": "2026-10-01",
                       "amountHistory": None}})
        bp2 = AnalysisBlueprint(claims=[ClaimDraft(
            id="C1", text="采购人本期预算高于历史", category="事实",
            section_index=2, priority=5)],
            hypotheses=[HypothesisSeed(id="H1", label="投标机会",
                                       claim_ids=["C1"])], gaps=[])
        self.led.register_event(env2, "", "evt_i2")
        self.led.install_blueprint("evt_i2", bp2)
        out2 = self._insight(label="投标机会", event_id="evt_i2")
        self.assertNotIn("中位数", out2)       # amountHistory=null 不出句

    # ------------------------------------------------------------ 横切

    def test_zero_trace_when_no_material(self):
        """拍板5 全空=整段零痕迹:无解读∧无任何拍素材(arg 成员空)→ 空串。"""
        self.assertEqual(self._insight(label="投标机会", arg={
            "hypothesis_id": "evt_t:H1", "label": "投标机会",
            "member_ids": []}), "")

    def test_deterministic_bytewise(self):
        """确定性:同输入两次渲染逐字节一致(机械句=模板)。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        a = self._insight(label="投标机会")
        b = self._insight(label="投标机会")
        self.assertEqual(a, b)

    def test_table_driven(self):
        """改表即生效:signal_phrases 措辞改表 → T6 句面随之变(数值参数
        疫苗纪律);缺键=定性词原词兜底。"""
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        tbl = dict(self.rules)
        tbl["report_stance_words"] = dict(
            self.rules["report_stance_words"])
        tbl["report_stance_words"]["signal_phrases"] = {"商机": "自定义机会措辞"}
        claims = self.led.all_claims("evt_t")
        by_cid = {c["claim_id"]: dict(c) for c in claims}
        member = list(by_cid)
        arg = {"hypothesis_id": "evt_t:H1", "label": "投标机会",
               "member_ids": member}
        out = self.builder._insight_lines(
            "evt_t", arg, by_cid, {}, tbl, {}, {"evt_t:H1": member})
        self.assertIn("调查初期将其列为自定义机会措辞", out)

    def test_e2e_render_conclusion_carries_insight(self):
        """端到端:_render_conclusion 单一领先分支(已证实成员挣到资格)在
        机械卡后带出洞察段(拍板1 卡是分界);洞察禁 claim id。"""
        add_evidence(self.led, "evt_t", "forum_a", "员工爆料工资已发", "C1",
                     round_no=1)
        add_evidence(self.led, "evt_t", "forum_b", "工会通报已解决", "C1",
                     round_no=1)
        self._add_interpret("解读将本信号定性为商机，理由为公开招标公告"
                            "构成潜在参与机会")
        out = self.builder._render_conclusion("evt_t")
        if "**当前研判" in out:                # graph argument 挣到资格时
            self.assertIn("**我们的判断**", out)
            self.assertIn("**这意味着什么**", out)
            self.assertNotIn("evt_t:C", out)   # 洞察段禁 claim id


class TestReportInsightI2(unittest.TestCase):
    """报告结论研判说明 I2(施工单 20261109,11-09 三轮拍板):「判据×本案
    具体化」=LLM 措辞层(拍板 3 分权终点形态)——素材代码算(I2-1 verdict_
    brief)/prompt 结论节扩展(I2-2 正反例)/五闸+段落形态机器验票(I2-3,
    拒收粒度=单句撤/整段回落零占位)/提取接线(I2-4 _verdict_narr,旧台账
    零占位零变化;brief 零改动)。定标=探针 --llm 忻州 r2+dalian 异主题
    双轮 4/4 PASS 零翻转(实录);live 落库挂 P6 批。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()
        from sense2act.stages.s7_report import _load_report_phrases
        self.rules = _load_report_phrases(self.domain.config_dir)
        # r2 形态夹具:H0「正常大额采购(真实商机)」={C1,C2} 双已证实(主导),
        # H1「异常输送」={C3} 已证实(居次);C4 矛盾(事件级反向,I1 判据)。
        self.led.conn.execute(
            "UPDATE hypotheses SET label=?, description=? "
            "WHERE hypothesis_id='evt_t:H0'",
            ("正常大额采购(真实商机)",
             "本次采购为忻州职院的真实需求,金额与流程正常,本企业可正常"
             "参与投标。可区分 claim:c10(近两年同等金额记录)——若为真"
             "则支持,若为假则削弱。"))
        self.led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES('evt_t:H1','evt_t',"
            "'异常输送','资金流向异常',0,0)")
        self.led.conn.execute(
            "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
            " VALUES('evt_t:H0','evt_t:C2','AND')")   # H0={C1,C2} 双已证实
        self.led.conn.execute(
            "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
            " VALUES('evt_t:H1','evt_t:C3','AND')")
        self.led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE draft_id IN ('C1','C2','C3')")
        self.led.add_claim("evt_t", {"id": "C4",
                                     "text": "信封未提供本事件相关记载",
                                     "category": "事实", "section_index": 3,
                                     "priority": 3}, 2)
        self.led.conn.execute(
            "UPDATE claims SET text='招标文件设置了排斥潜在竞争者的条件',"
            " status='矛盾' WHERE draft_id='C4'")
        self.led.conn.commit()

    def _builder(self, **kw):
        from sense2act.config import InvestigationConfig
        cfg = InvestigationConfig(**kw)
        return ReportBuilder(self.led, self.domain, cfg,
                             TemplateSectionWriter(), provider=None,
                             runs_dir=self.tmp)

    # ------------------------------------------------------------ I2-1 素材

    def test_brief_fields_r2_shape(self):
        """素材包(忻州 r2 形态):label 剥括号/criteria 取判据陈述句(机器腔
        c10 不上素材)/verified_gists≤2/contra_state 反向短语/runners_up_
        reason 一句话(M4 逻辑复用);t1 键缺席(rumor 信封无 amount_history)。"""
        from sense2act.stages.s7_report import verdict_brief
        brief = verdict_brief(self.led, "evt_t", self.rules,
                              self.led.claim_gists("evt_t"))
        self.assertEqual(brief["label"], "正常大额采购")   # 剥括号(r2 实锚)
        self.assertIn("金额与流程正常", brief["criteria"])
        self.assertNotIn("c10", brief["criteria"])         # 判定规则不上素材
        self.assertNotIn("t1_baseline", brief)             # null=键缺席
        self.assertEqual(len(brief["verified_gists"]), 2)
        self.assertEqual(brief["verified_gists"][0], "B公司拖欠工资")
        self.assertIn("排斥潜在竞争者", brief["contra_state"])
        self.assertIn("已查证 1 项", brief["runners_up_reason"])

    def test_brief_absent_keys(self):
        """缺项=键缺席零痕迹:无居次已证实假设=runners_up_reason 键缺席;
        无反向材料=contra_state=「无反向」;无主导假设=空表(失败不伪装)。"""
        from sense2act.stages.s7_report import verdict_brief
        self.led.conn.execute(
            "UPDATE claims SET status='待证实' WHERE draft_id IN ('C3','C4')")
        self.led.conn.commit()
        brief = verdict_brief(self.led, "evt_t", self.rules)
        self.assertNotIn("runners_up_reason", brief)
        self.assertEqual(brief["contra_state"], "无反向")   # C4 撤矛盾后
        # 无主导:全部矛盾=记分全负(dominant 判据 score>0)→ 空表
        self.led.conn.execute(
            "UPDATE claims SET status='矛盾' WHERE draft_id IN ('C1','C2','C3','C4')")
        self.led.conn.commit()
        self.assertEqual(verdict_brief(self.led, "evt_t", self.rules), {})

    # ------------------------------------------------------------ I2-3 五闸

    def test_gates_anchor_numeric_upgrade(self):
        """闸1 锚有效/闸2 数值保真(⊆允许省略禁变形)/闸3 禁升格:好例零
        命中,坏例分类命中。"""
        from sense2act.stages.s7_report import (_anchor_bad, _numeric_bad,
                                                _upgrade_missing)
        self.assertEqual(_anchor_bad("公告已发布[E1]。", {"E1", "1"}), [])
        self.assertEqual(_anchor_bad("名单见[99]。", {"E1"}), ["[99]"])
        self.assertEqual(_numeric_bad("预算 99.9 万。", {"99.9"}), [])
        self.assertEqual(_numeric_bad("预算 99.90 万。", {"99.9"}), [])  # 等形
        self.assertEqual(_numeric_bad("金额 12345 元。", {"99.9"}), ["12345"])
        self.assertEqual(_upgrade_missing("资质匹配尚未查实。", True), "")
        self.assertEqual(_upgrade_missing("资质匹配确认无误。", True),
                         "待证实未带口径(禁升格)")
        self.assertEqual(_upgrade_missing("资质匹配确认无误。", False), "")

    def test_gates_form_and_quoted(self):
        """闸5 形态闸+段落形态检查(分号半/全角码点明写/序号堆叠/列表形态)
        +闸4 实体保真(引号面⊆已知名)。"""
        from sense2act.stages.s7_report import (_quoted_unknown,
                                                _verdict_form_bad)
        self.assertEqual(_verdict_form_bad("预算与量级一致,属常规大额采购。"),
                         "")
        self.assertEqual(_verdict_form_bad("甲尚未查实;乙尚未查实;丙尚未查实。"),
                         "分号连排罗列")
        self.assertEqual(_verdict_form_bad("甲尚未查实；乙尚未查实；丙尚未查实。"),
                         "分号连排罗列")   # 全角(照 fusion 码点明写教训)
        self.assertEqual(_verdict_form_bad("如下。\n1. 甲\n2. 乙"), "序号堆叠")
        self.assertEqual(_verdict_form_bad("如下。\n- 甲\n- 乙"),
                         "列表形态(段落混入结构行)")
        self.assertEqual(_quoted_unknown("更支持「正常大额采购」。",
                                         ["正常大额采购(真实商机)"]), [])
        self.assertEqual(_quoted_unknown("指向「幽灵公司」。",
                                         ["正常大额采购"]), ["幽灵公司"])

    def test_gates_apply_sentence_surgery(self):
        """verdict_gates_apply:句级三闸败句撤其余照上;整段句撤/段级闸败=
        整段回落零占位;好段原样过。"""
        from sense2act.stages.s7_report import verdict_gates_apply
        good = ("本项目预算与该院近两年同类采购量级一致,属常规大额采购,"
                "资质匹配尚未查实。")
        body, reasons = verdict_gates_apply(
            good, valid_nums=set(), valid_refs=set(),
            known_names=["常规大额采购"], has_pending=True)
        self.assertEqual(body, good)
        self.assertEqual(reasons, [])
        # 坏句撤其余照上:幻觉数字句撤,好句保留
        mixed = ("本项目金额 12345 元。预算与该院同类采购量级一致,属常规"
                 "大额采购,资质匹配尚未查实。")
        body, reasons = verdict_gates_apply(
            mixed, valid_nums=set(), valid_refs=set(),
            known_names=["常规大额采购"], has_pending=True)
        self.assertNotIn("12345", body)
        self.assertIn("量级一致", body)
        self.assertTrue(reasons)
        # 整段句撤=回落零占位
        body, reasons = verdict_gates_apply(
            "涉及金额 12345 元。另涉 6789 元。",
            valid_nums=set(), valid_refs=set(), known_names=[],
            has_pending=False)
        self.assertEqual(body, "")
        # 形态闸败=整段回落(即便句句干净)
        body, reasons = verdict_gates_apply(
            "甲尚未查实;乙尚未查实;丙尚未查实。",
            valid_nums=set(), valid_refs=set(), known_names=[],
            has_pending=True)
        self.assertEqual(body, "")

    # ------------------------------------------------------------ I2-2 prompt

    def test_prompt_verdict_discipline(self):
        """prompt 纪律关键词锁:结论节判据×本案要求/正例(量级一致范例)/
        反例(术语原文整句引用=禁止)在 prompt。"""
        from sense2act.prompts import S7_DRAFT_PROMPT
        self.assertIn("研判说明", S7_DRAFT_PROMPT)
        self.assertIn("verdict_brief", S7_DRAFT_PROMPT)
        self.assertIn("具体化", S7_DRAFT_PROMPT)
        self.assertIn("99.99 万", S7_DRAFT_PROMPT)      # 正例(拍板 2 范例)
        self.assertIn("禁止", S7_DRAFT_PROMPT)
        self.assertIn("术语原文", S7_DRAFT_PROMPT)       # 反例禁则

    # ------------------------------------------------------------ I2-4 接线

    def test_hooks_context_carries_brief(self):
        """轮间钩子结论节 context 附 verdict_brief(与 finalize 同源=输入侧
        口径不分叉);非结论节不带。"""
        from sense2act.stages.d_hooks import DraftHooks, TemplateSectionWriter
        hooks = DraftHooks(self.led, TemplateSectionWriter())
        secs = {s["section_id"]: s for s in self.led.get_sections("evt_t")}
        ctx_con = hooks._context("evt_t", secs["evt_t:S3"],
                                 self.led.claims_for_section("evt_t:S3"))
        self.assertIn("verdict_brief", ctx_con)
        self.assertEqual(ctx_con["verdict_brief"]["label"], "正常大额采购")
        ctx_bg = hooks._context("evt_t", secs["evt_t:S0"],
                                self.led.claims_for_section("evt_t:S0"))
        self.assertNotIn("verdict_brief", ctx_bg)

    def test_finalize_context_brief(self):
        """finalize 环① 结论节 context 附 verdict_brief(捕获 writer 观察)。"""
        from sense2act.stages.d_hooks import TemplateSectionWriter

        class _Capture:
            def __init__(self):
                self.contexts = []

            def draft(self, ctx):
                self.contexts.append(dict(ctx))
                return TemplateSectionWriter().draft(ctx)

            def check(self, text, ctx):
                return []

            def edit(self, text, issues, ctx):
                return text

        cap = _Capture()
        from sense2act.config import InvestigationConfig
        b = ReportBuilder(self.led, self.domain, InvestigationConfig(),
                          cap, provider=None, runs_dir=self.tmp)
        b.finalize("evt_t", 2, "轮数上限", envelope_label="测试事件",
                   signal_type="风险")
        con = [c for c in cap.contexts if c.get("kind") == "conclusion"]
        self.assertTrue(con)
        self.assertIn("verdict_brief", con[0])
        self.assertEqual(con[0]["verdict_brief"]["label"], "正常大额采购")

    def test_verdict_narr_wiring(self):
        """提取接线:好串讲(判据具体化+口径)过闸上卡上方;坏串讲(幻觉
        数字/分号连排)整段回落零占位;旧台账模板产物=零占位(零变化锁);
        素材缺(无主导)=不验照提(P 波形态保守)。"""
        from sense2act.stages.s7_report import _narr_extract
        b = self._builder()

        def _sec():
            return next(s for s in self.led.get_sections("evt_t")
                        if s["section_id"] == "evt_t:S3")   # set 后取新快照
        good = ("当前证据结构更支持「正常大额采购」:公告与历史采购记录已获"
                "查证且量级一致属常规大额采购,反向条款是主要变数,资质匹配"
                "尚未查实。")
        stored = f"### 结论\n\n{good}\n\n- 支撑结构:x\n"
        self.led.set_draft("evt_t:S3", stored, {}, 2)
        out = b._verdict_narr("evt_t", _sec(), {})
        self.assertEqual(out, good)
        # 坏例:幻觉数字 → 整段回落
        bad = ("当前证据结构更支持「正常大额采购」:预算 12345 万元量级一致,"
               "资质匹配尚未查实。")
        self.led.set_draft("evt_t:S3", f"### 结论\n\n{bad}\n", {}, 2)
        self.assertEqual(b._verdict_narr("evt_t", _sec(), {}), "")
        # 坏例:分号连排 → 整段回落
        bad2 = ("甲证据已获查证;乙证据已获查证;丙证据已获查证,资质匹配"
                "尚未查实。")
        self.led.set_draft("evt_t:S3", f"### 结论\n\n{bad2}\n", {}, 2)
        self.assertEqual(b._verdict_narr("evt_t", _sec(), {}), "")
        # 旧台账模板产物 = 零占位
        old = "### 结论\n\n**主导假设:正常大额采购(真实商机)**(得分 2)\n\n- x"
        self.led.set_draft("evt_t:S3", old, {}, 2)
        self.assertEqual(_narr_extract(old, self.rules), "")
        self.assertEqual(b._verdict_narr("evt_t", _sec(), {}), "")

    def test_display_sections_conclusion_gated(self):
        """端到端:_display_sections 结论节=串讲(过闸)挂机械卡上方;坏串讲
        =纯卡零占位。"""
        b = self._builder()
        good = ("当前证据结构更支持「正常大额采购」:公告与历史采购记录已获"
                "查证且量级一致属常规大额采购,反向条款是主要变数,资质匹配"
                "尚未查实。")
        self.led.set_draft("evt_t:S3", f"### 结论\n\n{good}\n\n- x\n", {}, 2)
        sections = self.led.get_sections("evt_t")
        out = b._display_sections("evt_t", sections)
        con = next(x for x in out if x["title"] == "结论")
        self.assertIn(good, con["text"])
        self.assertLess(con["text"].index(good), con["text"].index("**当前研判"))
        # 坏串讲=纯卡
        bad = ("当前证据结构更支持「正常大额采购」:预算 12345 万元量级一致,"
               "资质匹配尚未查实。")
        self.led.set_draft("evt_t:S3", f"### 结论\n\n{bad}\n", {}, 2)
        out = b._display_sections("evt_t", self.led.get_sections("evt_t"))
        con = next(x for x in out if x["title"] == "结论")
        self.assertNotIn("12345", con["text"])
        self.assertIn("**当前研判", con["text"])

    def test_brief_zero_change(self):
        """brief 零改动锁:结论区只动 report 侧——S3 draft 带串讲时 brief.md
        不含串讲(brief 结论走机械行路径,与 _verdict_narr 无交集)。"""
        good = ("当前证据结构更支持「正常大额采购」:公告与历史采购记录已获"
                "查证且量级一致属常规大额采购,反向条款是主要变数,资质匹配"
                "尚未查实。")
        self.led.set_draft("evt_t:S3", f"### 结论\n\n{good}\n\n- x\n", {}, 2)
        self._builder().finalize("evt_t", 2, "轮数上限",
                                 envelope_label="测试事件", signal_type="风险")
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(
            encoding="utf-8")
        self.assertNotIn(good, brief)

    def test_probe_gate_samples_source_lock(self):
        """探针与测试同源锁:_insight_probe._GATE_SAMPLES 全过闸(闸改口径
        探针同步=双锁,照 BatchP 先例)。"""
        import _insight_probe as probe
        from sense2act.stages.s7_report import (_anchor_bad, _numeric_bad,
                                                _quoted_unknown,
                                                _upgrade_missing,
                                                _verdict_form_bad,
                                                _norm_num)
        for sample in probe._GATE_SAMPLES:
            gate, want = sample[0], sample[1]
            if gate == "anchor":
                got = _anchor_bad(sample[2], sample[3])
            elif gate == "numeric":
                got = _numeric_bad(sample[2],
                                   {_norm_num(x) for x in sample[3]})
            elif gate == "upgrade":
                got = _upgrade_missing(sample[2], sample[3])
            elif gate == "form":
                got = _verdict_form_bad(sample[2])
            else:
                got = _quoted_unknown(sample[2], sample[3])
            self.assertEqual(got, want, f"{gate}: {sample[2][:20]}")


if __name__ == "__main__":
    unittest.main()
