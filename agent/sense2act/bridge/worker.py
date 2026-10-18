"""python -m sense2act worker:领取后端调查 → 跑 S1-S7 → 留痕/证据/报告回写 → 收尾。

单进程串行(契约 §9.4:不支持多 worker 同时接管同一调查)。两种模式:
  live    真实模型 + 本地爬虫(需 DEEPSEEK_API_KEY)
  replay  演示回放:用 examples/<case> 的录制响应与证据种子,零网络零 key,
          信封取自录制样例本身(录制件只对它的信封自洽),写回的仍是后端真实调查。
          招投标信号回放 --replay-case,新闻信号(rumor)回放 --replay-rumor-case。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from ..cli import build_parser, build_pipeline
from ..llm import DeepSeekLLM, ModelBudgetExceeded, ReplayLLM, model_call_budget
from ..models.envelope import InvestigationEnvelope
from .client import CONFLICT, BackendClient, BackendError
from .envelope import build_envelope, signal_kind
from .report import build_report
from .reporter import InvestigationAborted, StepReporter, TokenMeter

log = logging.getLogger("sense2act.worker")
ROOT = Path(__file__).resolve().parents[2]


class Worker:
    def __init__(self, client: BackendClient, opts: argparse.Namespace):
        self.client, self.opts = client, opts

    # ------------------------------------------------------------ 恢复与轮询

    def recover(self) -> None:
        """重启恢复(契约 §9.4):reporting 已交报告只收尾;investigating 的进程内状态已丢失,如实标失败。"""
        for inv in self.client.investigations("reporting"):
            self._quietly(self.client.complete, inv["id"])
            log.info("recovered %s: completed", inv["id"])
        for inv in self.client.investigations("investigating"):
            self._quietly(self.client.fail, inv["id"], "Agent 服务重启,进行中的调查已中断;可对该信号重新发起调查")
            log.info("recovered %s: marked failed", inv["id"])

    def poll_once(self) -> int:
        done = 0
        for inv in self.client.investigations("created"):
            try:
                self.client.start(inv["id"])
            except BackendError as exc:
                if exc.code == CONFLICT:      # 已被占用或并发额度已满,下轮再看
                    continue
                raise
            self.investigate(inv["id"])
            done += 1
        return done

    # ------------------------------------------------------------ 单次调查

    def investigate(self, inv_id: str) -> None:
        log.info("investigating %s", inv_id)
        pipeline = None
        try:
            context = self.client.context(inv_id)
            investigation, document = context["investigation"], context["signal"]["document"]
            workdir = Path(self.opts.data_dir) / inv_id
            workdir.mkdir(parents=True, exist_ok=True)
            envelope, event_id, llm, mode_args = self._prepare(context, inv_id)
            args = build_parser().parse_args([
                "investigate", "--envelope", "-", *mode_args,
                "--db", str(workdir / "ledger.db"), "--runs-dir", str(workdir / "runs"),
                "--config-dir", self.opts.config_dir,
                "--max-rounds", str(investigation["max_rounds"]),
                "--max-llm-calls", str(self.opts.max_llm_calls),
                "--max-collect-calls", str(self.opts.max_collect_calls)])
            meter = TokenMeter(llm)
            pipeline = build_pipeline(args, meter, args.db)
            reporter = StepReporter(self.client, inv_id, pipeline._ledger, event_id, document["id"], meter)  # noqa: SLF001
            reporter.attach()

            summary, stop_reason = None, ""
            with model_call_budget(args.max_llm_calls):
                try:
                    summary = pipeline.run(envelope, event_id)
                    stop_reason = summary.get("stop_reason", "")
                except ModelBudgetExceeded as exc:
                    stop_reason = f"预算护栏: {exc}"
            reporter.close_open_questions()
            draft = build_report(reporter, envelope, summary, stop_reason)
            if not draft["claims"]:
                raise RuntimeError("没有可登记的证据支撑任何结论(信号文档缺发布时间或原文快照)")
            self.client.submit_report(inv_id, draft)
            self.client.complete(inv_id)
            log.info("completed %s: %s", inv_id, stop_reason)
        except InvestigationAborted as exc:
            log.info("aborted %s: %s", inv_id, exc)       # 用户停止:后端已是终态,不再写
        except Exception as exc:  # noqa: BLE001 —— 任何失败都要让后端调查落到终态
            log.exception("failed %s", inv_id)
            self._quietly(self.client.fail, inv_id, f"{type(exc).__name__}: {exc}")
        finally:
            if pipeline is not None:   # 每个调查一份台账库,用完即关(常驻进程不泄漏连接/文件锁)
                pipeline._ledger.conn.close()  # noqa: SLF001

    def _prepare(self, context: dict, inv_id: str):
        if self.opts.mode == "replay":
            case = Path(self.opts.replay_rumor_case if signal_kind(context) == "rumor" else self.opts.replay_case)
            envelope = InvestigationEnvelope.model_validate(json.loads((case / "envelope.json").read_text(encoding="utf-8-sig")))
            entries = json.loads((case / "responses.json").read_text(encoding="utf-8-sig"))
            seed = case / "evidence_seed.json"
            return (envelope, envelope.event_id or envelope.derive_event_id(), ReplayLLM(entries),
                    ["--replay", str(case / "responses.json"), *(["--seed", str(seed)] if seed.is_file() else [])])
        event_id = "evt_" + inv_id
        llm = DeepSeekLLM(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url=os.getenv("DEEPSEEK_BASE_URL"),
                          model=os.getenv("DEEPSEEK_MODEL"))
        return build_envelope(context, event_id), event_id, llm, ["--live", "--crawler", "local", "--assoc", "entity_text"]

    @staticmethod
    def _quietly(fn, *args):
        try:
            fn(*args)
        except BackendError as exc:
            log.warning("%s%s -> %s", fn.__name__, args[:1], exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sense2act worker", description="领取并执行 Sense2Act 后端的调查任务")
    parser.add_argument("--backend", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--mode", choices=["live", "replay"], default=os.getenv("AGENT_MODE", "live"))
    parser.add_argument("--replay-case", default=os.getenv("AGENT_REPLAY_CASE", str(ROOT / "examples/tender_medical_ai")))
    parser.add_argument("--replay-rumor-case",
                        default=os.getenv("AGENT_REPLAY_RUMOR_CASE", str(ROOT / "examples/ai_model_release")))
    parser.add_argument("--data-dir", default=os.getenv("AGENT_DATA_DIR", str(ROOT / "data/worker")))
    parser.add_argument("--config-dir", default=str(ROOT / "data/config"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("AGENT_POLL_SECONDS", "5")))
    parser.add_argument("--max-llm-calls", type=int, default=int(os.getenv("AGENT_MAX_LLM_CALLS", "160")))
    parser.add_argument("--max-collect-calls", type=int, default=int(os.getenv("AGENT_MAX_COLLECT_CALLS", "80")))
    parser.add_argument("--once", action="store_true", help="处理一轮队列后退出")
    opts = parser.parse_args(argv)
    key = os.getenv("INTERNAL_API_KEY")
    if not key:
        parser.error("需要环境变量 INTERNAL_API_KEY(与后端一致)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    worker = Worker(BackendClient(opts.backend, key), opts)
    log.info("worker up: backend=%s mode=%s", opts.backend, opts.mode)
    while True:
        try:
            worker.recover()
            break
        except (BackendError, OSError) as exc:     # 后端还没起来:等它
            log.warning("backend not ready (%s), retrying", exc)
            time.sleep(opts.interval)
    while True:
        try:
            worker.poll_once()
        except (BackendError, OSError) as exc:
            log.warning("poll failed: %s", exc)
        if opts.once:
            return 0
        time.sleep(opts.interval)
