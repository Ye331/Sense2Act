"""CLI 入口。

用法(离线回放,零网络零 key):
  python -m sense2act investigate --envelope examples/tender_medical_ai/envelope.json \
      --replay examples/tender_medical_ai/responses.json \
      --seed examples/tender_medical_ai/evidence_seed.json --db data/sense2act.db

第 1 档实测(真模型 + 回放证据):
  python -m sense2act investigate --envelope ... --live --seed ... \
      --record runs/recorded_responses.json --db data/live.db
  (--live 与 --replay 互斥;--record 把真模型响应录成回归 fixture;
   DEEPSEEK_API_KEY 从环境变量或项目根 .env 读取)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .collect import (
    CrawlerGate,
    CrawlerHttpClient,
    FakeCollector,
    OfflineGate,
    ToolRouter,
)
from .config import InvestigationConfig, load_domain_config
from .investigation import InvestigationPipeline
from .llm import DeepSeekLLM, ReplayLLM, RecordingLLM, model_call_budget
from .models.envelope import InvestigationEnvelope
from .prompts import CALIBRATE_SYSTEM_PROMPT
from .run_meta import mismatches, read_run_meta, write_run_meta
from .stages.calibrate import Calibrator
from .stages.adjudicate import Adjudicator
from .stages.askgate import AskGate
from .stages import (
    DraftHooks,
    HeadlessClarifier,
    InteractiveClarifier,
    LLMDeriver,
    LLMOpinionProvider,
    LLMPlanner,
    LLMRecommendProvider,
    LLMSectionWriter,
    LLMSignalAnalyzer,
    Observer,
    PERSONAS,
    ReplayClarifier,
    ReportBuilder,
    SimulatedClarifier,
    StubExplorer,
    TemplateSectionWriter,
)
from .stages.s3_executor import Executor, WordFamilyExplorer
from .stages.s5_ledger import LedgerWriter
from .stages.s6_verdict import VerdictEngine
from .store import LedgerRepository, connect


def _build_llm(args):
    """门禁:--live 与 --replay 互斥(离线/真实二选一,显式 opt-in)。"""
    if bool(args.live) == bool(args.replay):
        print("必须且只能指定 --live 与 --replay 之一(门禁:离线/真实互斥)",
              file=sys.stderr)
        raise SystemExit(2)
    if args.replay:
        entries = json.loads(Path(args.replay).read_text(encoding="utf-8-sig"))
        return ReplayLLM(entries), None
    inner = DeepSeekLLM()
    recorder = RecordingLLM(inner) if args.record else None
    return (recorder if recorder else inner), recorder


def build_collect_gate(args, config: InvestigationConfig):
    """四档采集模式(互斥):
    默认              -> OfflineGate + FakeCollector(--seed fixture;离线);
    --crawler local   -> 本地爬虫框架(自研适配器;录制在真出网点 EgressHttpClient);
    --crawler URL     -> 远程 crawler 服务(CrawlerGate;录制在服务边界);
    --collect-replay  -> 采集回放(与录制文件配套,零网络)。
    """
    if args.collect_replay:
        if args.crawler == "local":
            from .crawler import EgressHttpClient, LocalCollectService, \
                build_default_registry
            egress = EgressHttpClient(replay_path=args.collect_replay)
            return LocalCollectService(
                build_default_registry(egress), egress=egress,
                max_calls=config.max_collect_calls)
        return CrawlerGate(replay_path=args.collect_replay,
                           max_calls=config.max_collect_calls)
    if args.crawler == "local":
        from .crawler import EgressHttpClient, LocalCollectService, \
            build_default_registry
        egress = EgressHttpClient(record_path=args.record_collection)
        return LocalCollectService(
            build_default_registry(egress), egress=egress,
            max_calls=config.max_collect_calls)
    if args.crawler:
        return CrawlerGate(CrawlerHttpClient(args.crawler),
                           record_path=args.record_collection,
                           max_calls=config.max_collect_calls)
    collector = FakeCollector(args.seed) if args.seed else FakeCollector({})
    return OfflineGate(collector, max_calls=config.max_collect_calls)


def build_pipeline(args, llm, db_path: str,
                   ask_answers: list[dict] | None = None) -> InvestigationPipeline:
    """装配工厂(依赖注入:编排器只认协议,这里决定回放/真实实现)。

    ask_answers:录制人答序列(--replay 时构造 ReplayClarifier 用;
    来自录制件 meta.ask_answers 旁车——人答不进 LLM 响应序列,走旁车)。
    """
    config = InvestigationConfig(
        max_rounds=args.max_rounds,
        interleave=not args.no_interleave,
        deployment_mode=args.deployment_mode,
        max_llm_calls=args.max_llm_calls,
        max_collect_calls=args.max_collect_calls,
        assoc_mode=args.assoc,
        disc_enabled=(getattr(args, "disc", "on") == "on"),
        dry_stop_rounds=getattr(args, "dry_stop_rounds", 3),
        calibration_enabled=(getattr(args, "calibration", "off") == "on"),
        rumor_meta_seed=(getattr(args, "rumor_meta_seed", "off") == "on"),
        askgate_enabled=(getattr(args, "ask_gate", "on") == "on"),
        ask_final_enabled=(getattr(args, "ask_final", "off") == "on"),
    )
    domain = load_domain_config(args.config_dir)
    ledger = LedgerRepository(connect(db_path), args.config_dir)

    gate = build_collect_gate(args, config)
    explorer = WordFamilyExplorer(
        gate, ToolRouter(domain.platform_rules),
        depth_max=config.explore_depth_max, calls_max=config.explore_calls_max)
    executor = Executor(gate, ToolRouter(domain.platform_rules), explorer)
    observer = Observer(ledger, domain)
    writer5 = LedgerWriter(
        ledger, domain,
        LLMDeriver(llm, domain.relation_vocab, retry=config.llm_retry),
        assoc_mode=config.assoc_mode)
    verdicts = VerdictEngine(
        ledger, domain.authoritative_roots,
        # 意见槽位内部重试=0:schema 错误与验票错误统一由引擎外层
        # 验票循环带反馈重试(否则两层重试相乘放大消耗)
        LLMOpinionProvider(llm, retry=0), retry=config.llm_retry,
        evidence_topk=config.evidence_topk * 2,
        ss_blocklist=domain.ss_exception_blocklist)
    calibrator = (Calibrator(llm, ledger, CALIBRATE_SYSTEM_PROMPT)
                  if config.calibration_enabled else None)
    # P1-8 主动澄清:裁决应用器恒装(纯确定性零 LLM;无 adjudicationTarget 的
    # 问题包空转=旧行为);门1 按 --ask-gate 开关装配(消融位)
    adjudicator = Adjudicator(ledger)
    askgate = (AskGate(ledger, threshold=config.ask_ambiguity_threshold)
               if config.askgate_enabled else None)

    template = TemplateSectionWriter()
    # 回放模式起草用确定性模板(字节级可复现);live 模式走辅模型,失败降级模板
    writer = (LLMSectionWriter(llm, template, retry=config.llm_retry)
              if args.live else template)
    hooks = DraftHooks(ledger, writer, evidence_topk=config.evidence_topk)
    provider = LLMRecommendProvider(llm, retry=config.llm_retry) if args.live else None
    reports = ReportBuilder(ledger, domain, config, writer, provider,
                            runs_dir=args.runs_dir)

    # 问人终端选择(P1-8 档A/档C):回放优先吃录制人答(配对纪律:人答用录制值,
    # 不重问);headless 录制件无 ask_answers → HeadlessClarifier(首选项与录制一致);
    # simulated=模拟用户(档C bench;replay 时 llm=ReplayLLM 不作 sim_user 判断,
    # 合作型退化首选项——离线确定性)
    ask_mode = getattr(args, "ask_mode", "headless")
    if getattr(args, "replay", None) and ask_answers:
        clarifier = ReplayClarifier(ledger, ask_answers)
    elif ask_mode == "simulated":
        clarifier = SimulatedClarifier(
            ledger,
            persona=getattr(args, "sim_persona", "cooperative"),
            llm=(llm if args.live else None),
            seed=getattr(args, "sim_seed", 20261009))
    elif ask_mode in ("interactive", "mixed"):
        clarifier = InteractiveClarifier(ledger, mode=ask_mode)
    else:
        clarifier = HeadlessClarifier(ledger)

    return InvestigationPipeline(
        ledger, domain, config,
        analyzer=LLMSignalAnalyzer(llm, retry=config.llm_retry,
                                   variant=(getattr(args, "variant", "") or "中")),
        planner=LLMPlanner(llm, retry=config.llm_retry),
        executor=executor, observer=observer, writer5=writer5,
        verdicts=verdicts, hooks=hooks, reports=reports,
        clarifier=clarifier, calibrator=calibrator,
        adjudicator=adjudicator, askgate=askgate)


def build_parser() -> argparse.ArgumentParser:
    """investigate/evaluate/assemble 的参数定义(worker 复用 investigate 的同一套参数)。"""
    parser = argparse.ArgumentParser(
        prog="sense2act", description="Sense2act 企业情报调查 Agent")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("investigate", help="对一份信号信封展开调查")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--envelope", help="信封 JSON 路径")
    src.add_argument("--envelope-batch",
                     help="上游 Top-K 批量信号 JSON(就地装配第 --signal-index 条信封)")
    p.add_argument("--signal-index", type=int, default=0,
                   help="配合 --envelope-batch:信号下标(0 起)")
    p.add_argument("--variant", default="", choices=["", "粗", "中", "细"],
                   help="S1b 拆解粒度档位(空=中;P1-6 三档 sweep 用)")
    p.add_argument("--dry-stop-rounds", type=int, default=3,
                   help="连续 N 轮零新增证据/新 claim → 代码兜底停(0=关;"
                        "S2 停与换纪律的第二道,数据背书见 prompts.py S2)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", help="回放响应 JSON(离线)")
    mode.add_argument("--live", action="store_true", help="真实模型(需 API key)")
    p.add_argument("--record", help="配合 --live:录制响应为回归 fixture")
    p.add_argument("--seed", help="采集证据种子 JSON(FakeCollector;离线档)")
    p.add_argument("--crawler", nargs="?", const="http://127.0.0.1:8301",
                   default=None,
                   help="'local'=本地爬虫框架(自研适配器,录制在真出网点);"
                        "或填服务地址(默认 http://127.0.0.1:8301,用 F 盘爬虫)")
    p.add_argument("--record-collection",
                   help="配合 --crawler:录制采集交互为回放文件(P0 闸门)")
    p.add_argument("--collect-replay",
                   help="采集回放文件(按指纹抄答案,零网络)")
    p.add_argument("--db", default="data/sense2act.db")
    p.add_argument("--config-dir", default="data/config")
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument("--max-llm-calls", type=int, default=160)
    p.add_argument("--max-collect-calls", type=int, default=80)
    p.add_argument("--assoc", default="entity", choices=["entity", "entity_text"],
                   help="关联策略:entity=实体名重合(回放基线)/"
                        "entity_text=+词面重合(真跑建议,宽进)")
    p.add_argument("--disc", default="on", choices=["on", "off"],
                   help="S2 区分度输入(改造②):off=盲规划对照(sweep3d A/B 验收用)")
    p.add_argument("--calibration", default="off", choices=["on", "off"],
                   help="改造④规划校准率:采集后对 S2 验收标准判型回填"
                        "(默认关;判型是新 stage,开启后录制件才含 calibrate 条目)")
    p.add_argument("--rumor-meta-seed", default="off", choices=["on", "off"],
                   help="rumor 信封元数据种子(2026-10-10):信封自述字段入账为"
                        " signal_layer 证据,供 corpus_bound claim 自证例外"
                        "(默认关:证据环境变更会使冻结录制件卷宗截片错位,"
                        "下批重录后转正)")
    p.add_argument("--ask-mode", default="headless",
                   choices=["headless", "interactive", "mixed", "simulated"],
                   help="问人档位(P1-8):headless=自动首选项(默认/回放基线)/"
                        "interactive=终端人答/mixed=TTY 则交互否则自动回退/"
                        "simulated=模拟用户(档C bench,配 --sim-persona);"
                        "--replay 时由录制人答接管(仅入配对校验)")
    p.add_argument("--sim-persona", default="cooperative", choices=list(PERSONAS),
                   help="模拟用户人格(--ask-mode simulated 时生效):cooperative=合作"
                        "(可 LLM 判断)/careless=敷衍乱选/irrelevant=答非所问/"
                        "refuse=拒答/regret=反悔/silence=沉默超时")
    p.add_argument("--sim-seed", type=int, default=20261009,
                   help="模拟用户随机种子(careless 人格;入 meta 配对)")
    p.add_argument("--ask-gate", default="on", choices=["on", "off"],
                   help="门1 分叉实在性(P1-8,默认开;off=消融对照):候选假设已有"
                        "主导就不问人,决策单改写查工具并落 askgate_blocked 审计")
    p.add_argument("--ask-final", default="off", choices=["on", "off"],
                   help="停前问人兜底(P3,默认关):死轮兜底停触发且存在未裁决分叉"
                        "时,停前发起一次确定性裁决问询(候选假设做选项,答案照常"
                        "落账传导);入 meta 配对")
    p.add_argument("--no-interleave", action="store_true",
                   help="退化为查完再写(对照实验)")
    p.add_argument("--deployment-mode", default="report_only",
                   choices=["report_only", "action_facing"])
    p.add_argument("--event-id", default="", help="留空则按信封派生")
    p.add_argument("--refresh-plan", default="",
                   help="状态刷新计划 JSON(测试用;{round:[{draft_id,new_status,evidence}]})")

    pe = sub.add_parser("evaluate",
                        help="评测套件:回放案例 + 确定性打分(零 LLM 评委)")
    pe.add_argument("--suite", default="eval/cases", help="套件目录(含 suite.json)")
    pe.add_argument("--only", default="", help="逗号分隔 case_id 子集")
    pe.add_argument("--variant", default="", choices=["", "粗", "中", "细"],
                    help="拆解粒度档位(粗/细待 P1-6 粒度指令位后启用)")
    pe.add_argument("--out", default="eval/report.md", help="汇总报告 markdown 路径")
    pe.add_argument("--out-json", default="eval/report.json", help="聚合指标 JSON 路径")
    pe.add_argument("--out-dir", default="eval/out", help="每案例明细 JSON 目录")
    pe.add_argument("--work-root", default="", help="临时运行目录(默认自动建并清理)")
    pe.add_argument("--keep-work", action="store_true", help="保留临时运行目录(调试)")
    pe.add_argument("--timeout", type=int, default=180, help="单案例子进程超时秒数")

    pa = sub.add_parser("assemble",
                        help="上游 Top-K 批量信号 → 单信封 JSON 落盘(+manifest 清单)")
    pa.add_argument("--batch", required=True, help="上游批量信号 JSON 路径")
    pa.add_argument("--out-dir", default="",
                    help="输出目录(默认 runs/assembled/<批文件名>/)")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "capture":
        from .capture_cli import main as capture_main
        return capture_main(argv[1:])
    if argv and argv[0] == "worker":
        from .bridge.worker import main as worker_main
        return worker_main(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command not in ("investigate", "evaluate", "assemble"):
        parser.error(f"未知子命令: {args.command}")

    # Windows 控制台默认 GBK,中文摘要会乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    if args.command == "evaluate":
        from .evaluate import main as evaluate_main
        return evaluate_main(args)

    if args.command == "assemble":
        from .assemble import main as assemble_main
        return assemble_main(args)

    signal_index = getattr(args, "signal_index", 0)
    if args.envelope and signal_index:
        print("[门禁] --signal-index 只能配合 --envelope-batch 使用", file=sys.stderr)
        return 2
    if args.envelope_batch:
        from .assemble import load_batch, signal_to_envelope
        try:
            batch = load_batch(args.envelope_batch)
        except (OSError, ValueError) as exc:   # 含文件不存在/schema 不支持
            print(f"[装配] {exc}", file=sys.stderr)
            return 2
        signals = batch["signals"]
        if not 0 <= signal_index < len(signals):
            print(f"[装配] --signal-index {signal_index} 越界(批内共 {len(signals)} 条)",
                  file=sys.stderr)
            return 2
        try:
            envelope = signal_to_envelope(signals[signal_index])
        except ValueError as exc:
            print(f"[装配] {exc}", file=sys.stderr)
            return 2
    else:
        envelope = InvestigationEnvelope.model_validate(json.loads(
            Path(args.envelope).read_text(encoding="utf-8-sig")))
    event_id = args.event_id or envelope.derive_event_id()
    refresh_plan: dict[int, list[dict]] = {}
    if args.refresh_plan:
        raw = json.loads(Path(args.refresh_plan).read_text(encoding="utf-8-sig"))
        refresh_plan = {int(k): v for k, v in raw.items()}

    # 采集档位门禁:回放 LLM 要求证据编号可预测,只能配 --seed
    if args.replay and (args.crawler or args.collect_replay):
        print("[门禁] --replay(LLM 回放)只能配 --seed 证据种子;"
              "真实/回放采集请配 --live(证据编号不可预测,无法回放 LLM)",
              file=sys.stderr)
        return 2
    if args.record_collection and not args.crawler:
        print("[门禁] --record-collection 必须配合 --crawler 使用", file=sys.stderr)
        return 2
    if args.crawler and not args.live:
        print("[门禁] --crawler 必须配合 --live(真实模型)使用:"
              "证据编号不可预测,LLM 回放无法对齐", file=sys.stderr)
        return 2
    ask_answers: list[dict] = []
    if args.replay:
        # 参数配对契约(P0 防再犯,2026-10-05):录制件带 meta 且参数不一致
        # → 硬错误。参数失真是静默毒药(assoc 档位差 → 挂账集变 → S6 验票
        # 失败/错位翻正),不许带病回放。无 meta 的旧录制件放行(兼容存量)。
        meta = read_run_meta(args.replay)
        if meta:
            bad = mismatches(meta, args)
            if bad:
                print("[参数配对] 回放参数与录制 meta 不一致,判决级回放会静默失真:",
                      file=sys.stderr)
                for k, (mv, cv) in bad.items():
                    print(f"    {k}: 录制={mv} 当前={cv}", file=sys.stderr)
                print("  请按录制参数重跑;或删除 .meta.json 旁车文件(明确放弃配对)。",
                      file=sys.stderr)
                return 2
            # 人答配对(P1-8):交互/模拟录制的问答回放必须吃录制值(不重问)。
            # 守卫只拦"键缺失"(半配对);空列表=合法零问询,放行。
            if (meta.get("ask_mode") in ("interactive", "mixed", "simulated")
                    and "ask_answers" not in meta):
                print("[参数配对] meta 声明交互问人(ask_mode)但缺 ask_answers 键:"
                      "半配对状态,回放会静默失真。", file=sys.stderr)
                return 2
            ask_answers = meta.get("ask_answers") or []

    try:
        llm, recorder = _build_llm(args)
        pipeline = build_pipeline(args, llm, args.db, ask_answers=ask_answers)
    except ValueError as exc:   # 缺 API key 等配置类错误:给可操作的提示
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    gate = getattr(pipeline._executor, "_gate", None)  # noqa: SLF001
    try:
        with model_call_budget(args.max_llm_calls):
            summary = pipeline.run(envelope, event_id, refresh_plan)
    except RuntimeError as exc:
        if "人答回放" in str(exc):   # 人答错位/耗尽:配对失败,与参数不一致同级
            print(f"[回放失败] {exc}", file=sys.stderr)
            return 2
        raise
    finally:
        if recorder is not None and args.record:
            recorder.save(args.record)
            # 参数旁车文件(配对契约);ask_answers=人答数据(非参数)同文件附载,
            # 不入 META_KEYS(配对校验不消费,只供回放构造 ReplayClarifier)。
            # ask_mode≠headless 时总写键(空也写):守卫只拦"键缺失"。
            extra = None
            if getattr(args, "ask_mode", "headless") != "headless":
                answers = getattr(pipeline._clarifier, "answers", None)  # noqa: SLF001
                extra = {"ask_answers": answers if answers is not None else []}
            write_run_meta(args.record, args, extra=extra)
        if gate is not None and hasattr(gate, "save"):
            gate.save()   # P0 录制完整性:成败都落盘(I3)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
