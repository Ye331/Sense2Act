"""交互终端(档A)独立演示:不依赖 S2 决策,直接体验"终端真停→人答→落账"。

用法(在 Sense2act_agent/ 下):
    python examples/ask_demo/try_terminal.py

终端会真的停下来等你按编号作答;回答后打印落账的 kind='澄清' 记录
(mode=interactive)。临时内存库,跑完即弃,不碰任何真实数据。
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):   # Windows GBK 控制台
    sys.stdout.reconfigure(encoding="utf-8")

from sense2act.models.decision import QuestionPackage  # noqa: E402
from sense2act.stages.clarify import InteractiveClarifier  # noqa: E402
from sense2act.store import LedgerRepository, connect  # noqa: E402

led = LedgerRepository(connect(":memory:"), ROOT / "data" / "config")
clarifier = InteractiveClarifier(led, mode="interactive")
pkg = QuestionPackage(
    question="华辰精密:两条线索方向相反,按哪个口径继续调查?",
    options=["按'扩产传闻待核'口径,优先查订单一手证据",
             "按'经营收缩'口径,优先查用工与产线变动",
             "按'时点错位'口径,先对齐两条线索的时间线"],
    evidence_summary="k1(财经论坛,自媒体): 传拿下央企年度大订单、车间扩招;"
                     "k2(招聘平台,官方): 近 30 天无新增岗位、两条装配线招聘已关闭。"
                     "两路独立来源,叙事方向相反。")
answer = clarifier.ask("evt_terminal_demo", 1, pkg,
                       context={"trigger": "manual_demo"})
print("\n=== 你刚才的回答已落账(kind='澄清',mode=interactive)===")
row = [r for r in led.decision_log_rows("evt_terminal_demo")
       if r["kind"] == "澄清"][-1]
print(json.dumps(json.loads(row["payload_json"]),
                 ensure_ascii=False, indent=2))
print(f"\n落账答案 = {answer}(与台账一致);临时内存库已用完即弃。")
