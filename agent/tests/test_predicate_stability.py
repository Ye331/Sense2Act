# -*- coding: utf-8 -*-
"""谓词触发面 property 测试(E3 沉淀,2026-10-22)。

tests/fixtures/predicate_stability.json 是双重身份:
1. E3 改写集(受控构造,tests/_e3_rewrite.py 消费出读数报告);
2. 手术包件 4(谓词形态标注)的**预注册测试集**——标注器实现后,
   在 TODO 处接入其接口,按 expected_beyond 验票(三判据之判据 3)。

本测试锁三件事:
- fixture 结构完整性(11 谓词全覆盖/每正样本恰 3 避词+2 保词改写);
- fixture 与 blocklist 的消费一致性(改 blocklist 必须同步 fixture——
  测试红=提醒重跑 _e3_rewrite.py 更新触发面读数,防基线静默漂移);
- blocklist 现状行为基线(避词改写漏拦/保词改写命中/自证复述误拦——
  这是 E3 报告读数的机器可检形态)。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "predicate_stability.json"
RULES = ROOT / "data" / "config" / "credibility_rules.json"


def _load():
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    rules = json.load(open(RULES, encoding="utf-8-sig"))
    return fx, rules["single_source_exception_blocklist"]


def _firing(text: str, blocklist: list[str]) -> list[str]:
    return [p for p in blocklist if p in (text or "")]


class TestFixtureStructure(unittest.TestCase):
    def test_谓词全覆盖blocklist(self):
        fx, blocklist = _load()
        self.assertEqual({p["predicate"] for p in fx["positives"]},
                         set(blocklist),
                         "blocklist 与 fixture 谓词集不一致——同步 fixture 并重跑 _e3_rewrite.py")

    def test_每正样本恰3避词2保词(self):
        fx, _ = _load()
        for p in fx["positives"]:
            rs = p["rewrites"]
            self.assertEqual(len(rs), 5, p["id"])
            self.assertEqual(sum(1 for r in rs if not r["keep_word"]), 3, p["id"])
            self.assertEqual(sum(1 for r in rs if r["keep_word"]), 2, p["id"])

    def test_期望语义自洽(self):
        fx, _ = _load()
        self.assertTrue(all(p["expected_beyond"] for p in fx["positives"]))
        self.assertFalse(any(n["expected_beyond"] for n in fx["negatives"]))

    def test_避词改写确实避开全清单(self):
        """构造承诺(keep_word=false ⇒ 不含任何触发词)——fixture 自检。"""
        fx, blocklist = _load()
        for p in fx["positives"]:
            for r in p["rewrites"]:
                if not r["keep_word"]:
                    self.assertFalse(_firing(r["text"], blocklist),
                                     f"{p['id']} 避词改写仍命中: {r['text']}")


class TestBlocklistBaseline(unittest.TestCase):
    """blocklist 现状行为基线(E3 读数的机器可检形态;2026-10-22)。"""

    def test_避词改写全漏拦_33of33(self):
        fx, blocklist = _load()
        miss = sum(1 for p in fx["positives"] for r in p["rewrites"]
                   if not r["keep_word"] and not _firing(r["text"], blocklist))
        self.assertEqual(miss, 33)

    def test_保词改写全命中_22of22(self):
        fx, blocklist = _load()
        hit = sum(1 for p in fx["positives"] for r in p["rewrites"]
                  if r["keep_word"] and _firing(r["text"], blocklist))
        self.assertEqual(hit, 22)

    def test_自证复述误拦_2of6(self):
        fx, blocklist = _load()
        ff = sum(1 for n in fx["negatives"] if _firing(n["claim"], blocklist))
        self.assertEqual(ff, 2)


class TestFutureAnnotator(unittest.TestCase):
    """件 4 预注册验票位(2026-10-22 兑现):谓词形态已施工
    (sense2act/predicate_form.validate),E2 live 标注
    (eval/predicate_e2/annotations.json)经施工验票通道回归——
    判据 3(同义改写稳定)=全部改写标注过验票且与原句判定一致。
    """

    def test_预注册验票位(self):
        from sense2act.predicate_form import validate
        ann_path = ROOT / "eval" / "predicate_e2" / "annotations.json"
        if not ann_path.exists():
            self.skipTest("E2 标注件未生成(先跑 tests/_e2_annotate.py)")
            return
        doc = json.load(open(ann_path, encoding="utf-8"))
        case_ann = {a["id"]: a["beyond_single_doc"] for a in doc["case_set"]}
        fx, _ = _load()
        by_id = {p["id"]: p for p in fx["positives"]}
        checked = 0
        for a in doc["rewrites"]:
            pid = a["parent_id"]
            if pid not in by_id:         # 负样本改写(同文已入 case_set)
                continue
            # id 形态 rw_<pid>_<k>(tests/_e2_annotate.py 编码),k=改写序号
            k = int(a["id"].rsplit("_", 1)[1])
            text = by_id[pid]["rewrites"][k - 1]["text"]
            out = validate({key: a[key] for key in
                            ("beyond_single_doc", "predicate_type",
                             "grounding_quotes")}, text)
            self.assertIsNotNone(out, f"{a['id']} 改写标注未过验票")
            self.assertEqual(out["beyond_single_doc"],
                             case_ann.get(pid, by_id[pid]["expected_beyond"]),
                             f"{pid} 改写标注与原句不一致(判据 3 破坏)")
            checked += 1
        self.assertEqual(checked, 55)    # 11 正样本 × 5 改写全覆盖


if __name__ == "__main__":
    unittest.main()
