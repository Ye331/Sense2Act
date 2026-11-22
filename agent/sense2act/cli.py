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
import datetime
import json
import sys
from pathlib import Path

from . import __version__
from .browser_auto.limits import CaptureGate
from .browser_auto.local_runner import LocalPlaywrightRunner
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
from .store.ledger import SIMHASH_NEAR_DUP_DIST
from .models.envelope import InvestigationEnvelope
from .predicate_check import PredicateChecker
from .prompts import CALIBRATE_SYSTEM_PROMPT
from .run_meta import mismatches, read_run_meta, write_run_meta
from .stages.calibrate import Calibrator
from .stages.adjudicate import Adjudicator
from .stages.askgate import AskGate
from .stages.red_team import RedTeam
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
from .stages.s7_assets import AssetCollector
from .stages.s6_verdict import VerdictEngine, load_flip_rules
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
                   ask_answers: list[dict] | None = None,
                   reborn=None) -> InvestigationPipeline:
    """装配工厂(依赖注入:编排器只认协议,这里决定回放/真实实现)。

    ask_answers:录制人答序列(--replay 时构造 ReplayClarifier 用;
    来自录制件 meta.ask_answers 旁车——人答不进 LLM 响应序列,走旁车)。
    reborn:线索转生注册器(三阶段 §六.3,callable(ledger, event_id, round);
    仅 resume 会话装配,--replay 不装配=回放 fixture 零触碰)。
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
        predicate_form_enabled=(getattr(args, "predicate_form", "off") == "on"),
        predicate_check_enabled=(getattr(args, "predicate_check", "off")
                                 == "on"),
        corpus_bound_code_enabled=(getattr(args, "corpus_bound_code", "off")
                                   == "on"),
        t1_independence_enabled=(getattr(args, "t1_independence", "off")
                                 == "on"),
        red_team_enabled=(getattr(args, "red_team", "off") == "on"),
        red_team_budget=int(getattr(args, "red_team_budget", 2)),
        flip_guard_enabled=(getattr(args, "flip_guard", "off") == "on"),
        kb_enabled=(getattr(args, "kb", "off") == "on"),
        askgate_enabled=(getattr(args, "ask_gate", "on") == "on"),
        ask_final_enabled=(getattr(args, "ask_final", "off") == "on"),
        ess_enabled=(getattr(args, "ess", "on") == "on"),
        kb_path=str(getattr(args, "kb_path", "") or ""),   # K5 呈现(渲染批次)
        assets_enabled=bool(getattr(args, "assets", False)),
        snapshots_enabled=bool(getattr(args, "snapshots", False)),
    )
    domain = load_domain_config(args.config_dir)
    ledger = LedgerRepository(connect(db_path), args.config_dir)

    gate = build_collect_gate(args, config)
    explorer = WordFamilyExplorer(
        gate, ToolRouter(domain.platform_rules),
        depth_max=config.explore_depth_max, calls_max=config.explore_calls_max)
    # S3 第四通道(--kb 默认 off):off=不装配=零痕迹;on=kb.db 确定性
    # 检索器(蓝图 §四;零网络零 LLM 不占 collect 预算;lineage 继承)
    kb_retriever = None
    if config.kb_enabled:
        from .kb_retrieve import KbRetriever
        kb_retriever = KbRetriever(args.kb_path, config_dir=args.config_dir)
    executor = Executor(gate, ToolRouter(domain.platform_rules), explorer,
                        kb_retriever=kb_retriever)
    observer = Observer(ledger, domain)
    writer5 = LedgerWriter(
        ledger, domain,
        LLMDeriver(llm, domain.relation_vocab, retry=config.llm_retry),
        assoc_mode=config.assoc_mode)
    # 谓词充分性核对(--predicate-check 默认 off):off=不装配=零调用零痕迹;
    # on 时核对器挂引擎,判决梯权威放行路径前消费(触发正清单在 checker 内,
    # 规则表 credibility_rules.json 版本化;as_of=运行日,相对时间谓词基准)
    pc_checker = None
    if config.predicate_check_enabled:
        pc_checker = PredicateChecker(
            llm, domain.pc_trigger_patterns, runs=2,
            as_of=datetime.date.today().isoformat())
    verdicts = VerdictEngine(
        ledger, domain.authoritative_roots,
        # 意见槽位内部重试=0:schema 错误与验票错误统一由引擎外层
        # 验票循环带反馈重试(否则两层重试相乘放大消耗)
        LLMOpinionProvider(llm, retry=0), retry=config.llm_retry,
        evidence_topk=config.evidence_topk * 2,
        ss_blocklist=domain.ss_exception_blocklist,
        t1_near_dup_dist=(SIMHASH_NEAR_DUP_DIST
                          if config.t1_independence_enabled else None),
        predicate_checker=pc_checker,
        # D4 维持偏置闸(--flip-guard 默认 off):off=None=闸全关全链零行为;
        # on=load_flip_rules(config_dir) 消费翻转规则表(缺文件=None=同关,
        # 照 load_present_rules 纪律——表缺席不等于开关谎言)
        flip_rules=(load_flip_rules(args.config_dir)
                    if config.flip_guard_enabled else None))
    calibrator = (Calibrator(llm, ledger, CALIBRATE_SYSTEM_PROMPT)
                  if config.calibration_enabled else None)
    # P1-8 主动澄清:裁决应用器恒装(纯确定性零 LLM;无 adjudicationTarget 的
    # 问题包空转=旧行为);门1 按 --ask-gate 开关装配(消融位)
    adjudicator = Adjudicator(ledger)
    askgate = (AskGate(ledger, threshold=config.ask_ambiguity_threshold)
               if config.askgate_enabled else None)
    # 方案三反驳模式(--red-team 默认 off):判决后对抗复核;off=不装配=
    # 全链零痕迹(行为参数照三开关生命周期,重录波读数后定转正)
    red_team = (RedTeam(ledger, llm, verdicts,
                        budget_per_claim=config.red_team_budget)
                if config.red_team_enabled else None)

    template = TemplateSectionWriter()
    # 双回放语义(方案B解禁配套):live 与 LLM 回放都走辅模型——录制件含
    # s7_draft/s7_recommend 段,回放必须按 stage 消费它们,否则自首个钩子起
    # stage 全体平移(调查中途起草的案伤判决,仅 finalize 起草的案伤报告);
    # 冻结响应回放依然是字节级可复现的。模板仅在辅模型异常时兜底降级。
    writer = (LLMSectionWriter(llm, template, retry=config.llm_retry)
              if (args.live or args.replay) else template)
    hooks = DraftHooks(ledger, writer, evidence_topk=config.evidence_topk)
    provider = (LLMRecommendProvider(llm, retry=config.llm_retry)
                if (args.live or args.replay) else None)
    reports = ReportBuilder(ledger, domain, config, writer, provider,
                            runs_dir=args.runs_dir)
    # A/B 线报告附件:仅 live+本地爬虫档装配(出网点在 LocalCollectService 的
    # egress 上);离线/回放/远程服务档不装配=旧行为,报告如实无图。
    # B 线截图走 browser_auto 隔离通道(决策 #14):默认 dry-run,--no-dry-run
    # 真拍,硬限闸 CaptureGate 与 capture 通道共享状态文件(额度共用不放宽)。
    assets = None
    if config.assets_enabled or config.snapshots_enabled:
        egress = getattr(gate, "_egress", None)
        if config.assets_enabled and egress is None:
            raise ValueError("--assets 需要本地出网点(仅支持 --crawler local)")
        runner = gate_cap = None
        if config.snapshots_enabled:
            runner = LocalPlaywrightRunner(dry_run=not args.no_dry_run)
            if not runner.dry_run:
                try:
                    from playwright.sync_api import sync_playwright  # noqa: F401
                except ImportError as exc:
                    raise ValueError(
                        "真拍需安装 playwright: pip install playwright && "
                        "playwright install chromium") from exc
            gate_cap = CaptureGate("data/browser_capture_state.json")
        assets = AssetCollector(ledger, egress, runs_dir=args.runs_dir,
                                snapshot_runner=runner, snapshot_gate=gate_cap)

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
        adjudicator=adjudicator, askgate=askgate, red_team=red_team,
        assets=assets, reborn=reborn)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="sense2act", description="Sense2act 企业情报调查 Agent(离线骨架)")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    if argv and argv[0] == "capture":
        from .capture_cli import main as capture_main
        return capture_main(argv[1:])

    p = _add_investigate_parser(sub)

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

    pk = sub.add_parser("kb-derive",
                        help="知识库 K1:台账→对象卡/案卷卡派生(独立 kb.db,主库只读)")
    pkg = pk.add_mutually_exclusive_group(required=True)
    pkg.add_argument("--suite", default="",
                     help="套件目录(含 suite.json;录制库直读,离线案回放后派生)")
    pkg.add_argument("--source-db", default="",
                     help="单库模式:主台账 db 路径(只读;须配 --event-id)")
    pk.add_argument("--event-id", default="", help="单库模式必填:事件 id")
    pk.add_argument("--kb", default="data/kb.db",
                    help="知识库 db 输出路径(全量重建;默认 data/kb.db)")
    pk.add_argument("--config-dir", default="data/config")
    pk.add_argument("--signals", nargs="*", default=[],
                    help="信号 manifest.json 路径(项目卡信号挂卡,可多个)")
    pk.add_argument("--timeout", type=int, default=180,
                    help="离线案回放子进程超时秒数")
    pk.add_argument("--uploads-dir", default="data/kb_uploads",
                    help="K3 上传件目录(排序 glob 合并;默认 data/kb_uploads)")

    pi = sub.add_parser("kb-import",
                        help="知识库 K3v1/K5:内部知识上传(台账→对象卡/"
                             "md 文档→文档卡/规则表→规则卡)")
    pig = pi.add_mutually_exclusive_group(required=True)
    pig.add_argument("--ledger", default="",
                     help="结构化台账 CSV/JSON(canonical 必填列;aliases 列"
                          "分号分隔;其余列→attributes;确定性零 LLM)")
    pig.add_argument("--doc", default="",
                     help="md/文本文档(档1:原件登记,拆解层押后)")
    pig.add_argument("--rules", default="",
                     help="规则表 JSON/CSV(K5 路径③:独立规则卡,尺子≠证据;"
                          "谓词白名单查 kb_rules.json rule_check)")
    pi.add_argument("--rule-name", default="",
                    help="规则卡名(CSV 缺省取文件名;JSON 单卡可覆盖)")
    pi.add_argument("--title", default="", help="文档卡标题(默认取文件名)")
    pi.add_argument("--sensitivity", default="internal",
                    choices=["public", "internal"],
                    help="敏感级(§6.3 认识论四分;文档默认 internal="
                         "K2 检索器永不服务=敏感件不进 prompt)")
    pi.add_argument("--upload-dir", default="data/kb_uploads",
                    help="上传件目录(源数据,重派生后存活)")
    pi.add_argument("--config-dir", default="data/config",
                    help="域配置目录(--rules 谓词白名单查 kb_rules.json)")

    pkk = sub.add_parser("kb-check",
                         help="知识库 K5:规则卡×对象自动合规比对"
                              "(每格走三态;数据层出口,正式呈现=渲染批次)")
    pkk.add_argument("--kb", default="data/kb.db", help="kb.db 路径(只读)")
    pkk.add_argument("--db", nargs="+", default=[],
                     help="主库路径(可多个;claim 三态现场读正本)")
    pkk.add_argument("--rule", default="",
                     help="规则卡名(缺省=全部规则卡)")
    pkk.add_argument("--subject", default="ent_self",
                     help="比对对象卡 id(默认 ent_self 自我卡=资质核对表)")
    pkk.add_argument("--config-dir", default="data/config")
    pkk.add_argument("--format", default="md", choices=["md", "json"],
                     help="md=人看表格;json=机器(双产出同一数据)")

    pc = sub.add_parser("kb-chain",
                        help="知识库 C1:链查询(查询时现算纯函数,双只读)")
    pc.add_argument("--kind", default="conclusion",
                    choices=["conclusion", "timeline", "topic",
                             "provenance", "hypothesis"],
                    help="投影:结论链/时序链/话题链/证据履历链/假设链")
    pc.add_argument("--claim", default="", help="conclusion/timeline:claim id")
    pc.add_argument("--entity", default="", help="topic:实体/项目/案卷卡 id")
    pc.add_argument("--fingerprint", default="", help="provenance:内容指纹")
    pc.add_argument("--hypothesis", default="", help="hypothesis:假设 id")
    pc.add_argument("--event", default="", help="hypothesis:事件 id(路由主库)")
    pc.add_argument("--db", nargs="+", default=[],
                    help="主库路径(可多个;跨 run 聚合卡逐库路由)")
    pc.add_argument("--kb", default="data/kb.db", help="kb.db 路径(只读)")
    pc.add_argument("--as-of-round", type=int, default=None,
                    help="轮内回放时点(缺省=现势)")
    pc.add_argument("--config-dir", default="data/config")
    pc.add_argument("--t1-independence", action="store_true",
                    help="根折并照录制口径(录制 flags.t1_independence=on 时传)")
    pc.add_argument("--format", default="md", choices=["md", "json"],
                    help="md=人看(敏感过滤);json=机器(K2 被调形态)")

    pcg = sub.add_parser("chain-graph",
                         help="证据链形态图出图(查询时现算,零 LLM 零网络;"
                              "graph JSON 第一公民,md=mermaid 投影;"
                              "旁车 polarity/通道建议只读消费+指纹校验)")
    pcg.add_argument("--event", default="", help="事件 id")
    pcg.add_argument("--db", nargs="+", default=[],
                     help="主库路径(可多个)")
    pcg.add_argument("--kb", default="data/kb.db",
                     help="kb.db 路径(只读;旁车与 kb_version 所在)")
    pcg.add_argument("--claim", default="",
                     help="附单命题前提闭包图(缺省只出全卷网络图)")
    pcg.add_argument("--config-dir", default="data/config")
    pcg.add_argument("--seeds", default="",
                     help="人工极性/grouping seeds 路径(缺省=config/"
                          "hypothesis_seeds.json;人工优先,批2 F3 demo 用)")
    pcg.add_argument("--t1-independence", action="store_true",
                     help="根折并照录制口径(与 kb-chain 同款)")
    pcg.add_argument("--format", default="md",
                     choices=["md", "json", "svg", "html", "png", "md-chain",
                              "web"],
                     help="html=卷面整页(人看主载体:总览+行式贴身链主图);"
                          "svg=独立主图(自研行式布局);"
                          "png=dot 直出位图(报告嵌入/Word/PPT,必配 --out);"
                          "md-chain=文字版证据链(报告正文嵌入);"
                          "md=mermaid 投影(机器对账附录);json=graph JSON 第一公民;"
                          "web=chain_web.v1 交互契约 JSON(前端证据工作台数据源,"
                          "五泳道+provenance 反查+敏感压净,strict 校验不过不出件)")
    pcg.add_argument("--out", default="",
                     help="产物路径(缺省 stdout;独立产物挂账此处写文件)")

    pcgn = sub.add_parser("chain-gen",
                          help="证据链形态图生成期(LLM 方向极性+缺口通道建议,"
                               "旁车落 kb.db chain_sidecar 表;独立命令不进"
                               " investigate 不入 META_KEYS;人工 polarity 优先)")
    pcgn.add_argument("--event", default="", help="事件 id")
    pcgn.add_argument("--db", nargs="+", default=[], help="主库路径")
    pcgn.add_argument("--kb", default="data/kb.db",
                      help="kb.db 路径(读写,仅 chain_sidecar 表)")
    pcgn.add_argument("--config-dir", default="data/config",
                      help="域配置(通道白名单=platform_rules.json)")
    pcgn.add_argument("--seeds", default="",
                      help="假设种子 json(缺省 data/config/hypothesis_seeds.json;"
                           "人工 polarity 条目自动跳过=人工优先)")

    pcb = sub.add_parser("chain-backfill", help="只读台账补生成 chain.web/status 副产物")
    pcb.add_argument("--data-dir", default="data/worker", help="Worker 数据根目录")
    pcb.add_argument("--config-dir", default="data/config")
    group = pcb.add_mutually_exclusive_group(required=True)
    group.add_argument("--investigation", default="", help="指定 investigation id")
    group.add_argument("--all-missing", action="store_true", help="补生成所有非 ready 任务")

    args = parser.parse_args(argv)
    if args.command not in ("investigate", "evaluate", "assemble",
                            "kb-derive", "kb-chain", "kb-import",
                            "kb-check", "chain-graph", "chain-gen", "chain-backfill"):
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

    if args.command == "chain-backfill":
        from .bridge.chain_artifact import backfill
        try:
            results = backfill(args.data_dir, args.config_dir,
                               investigation=args.investigation,
                               all_missing=args.all_missing)
        except Exception as exc:  # 命令行保持非零且不吞失败
            print(f"[chain-backfill] {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.command == "kb-derive":
        from .kb_derive import KbDeriveError, derive_db, derive_suite
        try:
            if args.suite:
                stats = derive_suite(args.suite, args.kb,
                                     config_dir=args.config_dir,
                                     signal_manifests=args.signals,
                                     timeout=args.timeout,
                                     uploads_dir=args.uploads_dir)
            else:
                if not args.event_id:
                    print("[门禁] 单库模式必配 --event-id", file=sys.stderr)
                    return 2
                stats = derive_db(args.source_db, args.event_id, args.kb,
                                  config_dir=args.config_dir,
                                  uploads_dir=args.uploads_dir)
        except KbDeriveError as exc:
            print(f"[kb-derive] {exc}", file=sys.stderr)
            return 1
        print(f"[kb-derive] kb={args.kb} 卡={stats['n_entities']} "
              f"链接={stats['n_links']} 证据={stats['n_evidence']} "
              f"锚={stats['n_anchors']} 规则卡={stats.get('n_rules', 0)}"
              + (f" 错误={stats['n_errors']}" if stats.get("n_errors") else ""))
        for err in stats.get("errors", []):
            print(f"  - [ERR] {err}", file=sys.stderr)
        return 1 if stats.get("n_errors") else 0

    if args.command == "kb-import":
        from .kb_import import (KbImportError, import_doc, import_ledger,
                                import_rules)
        try:
            if args.ledger:
                out = import_ledger(args.ledger, args.upload_dir)
            elif args.rules:
                out = import_rules(args.rules, args.upload_dir,
                                   name=args.rule_name,
                                   config_dir=args.config_dir)
            else:
                out = import_doc(args.doc, args.upload_dir,
                                 sensitivity=args.sensitivity,
                                 title=args.title)
        except (KbImportError, OSError) as exc:
            print(f"[kb-import] {exc}", file=sys.stderr)
            return 2
        kind = ("规则卡" if args.rules else
                "文档卡" if args.doc else "对象卡")
        print(f"[kb-import] {kind}上传件={out}"
              f"(kb-derive 重派生时自动合并;文档默认 internal="
              f"K2 检索永不服务)")
        return 0

    if args.command == "kb-check":
        from .kb_check import KbCheckError, render_check, run_check
        from .kb_derive import KbDeriveError, load_rules
        if not args.db:
            print("[门禁] kb-check 必配 --db(主库,claim 三态现场读正本)",
                  file=sys.stderr)
            return 2
        try:
            kbr = load_rules(Path(args.config_dir) / "kb_rules.json",
                             args.config_dir)
            rows = run_check(args.kb, args.db, args.rule, args.subject,
                             kbr["zones"], config_dir=args.config_dir)
            print(render_check(rows, args.format))
        except (KbCheckError, KbDeriveError) as exc:
            print(f"[kb-check] {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "kb-chain":
        from .kb_chain import (
            KbChainError, chain_conclusion, chain_hypothesis, chain_provenance,
            chain_timeline, chain_topic, close_mains, open_kb_ro, open_mains,
            load_context, render)
        need = {"conclusion": "claim", "timeline": "claim",
                "topic": "entity", "provenance": "fingerprint"}
        key = need.get(args.kind)
        if key and not getattr(args, key):
            print(f"[门禁] kind={args.kind} 必配 --{key}", file=sys.stderr)
            return 2
        if args.kind == "hypothesis" and not (args.event and args.hypothesis):
            print("[门禁] kind=hypothesis 必配 --event 与 --hypothesis",
                  file=sys.stderr)
            return 2
        if not args.db:
            print("[门禁] kb-chain 必配 --db(主库,可多个)", file=sys.stderr)
            return 2
        mains = {}
        kb = None
        try:
            mains = open_mains(args.db, args.config_dir)
            kb = open_kb_ro(args.kb) if Path(args.kb).exists() else None
            ctx = load_context(args.config_dir)
            kw = {"ess_rules": ctx["ess_rules"], "auth_roots": ctx["auth_roots"],
                  "near_dup_dist": SIMHASH_NEAR_DUP_DIST
                  if getattr(args, "t1_independence", False) else None}
            if args.kind == "conclusion":
                data = chain_conclusion(mains, kb, args.claim,
                                        as_of_round=args.as_of_round, **kw)
            elif args.kind == "timeline":
                data = chain_timeline(mains, kb, args.claim,
                                      as_of_round=args.as_of_round, **kw)
            elif args.kind == "topic":
                data = chain_topic(mains, kb, args.entity,
                                   as_of_round=args.as_of_round, **kw)
            elif args.kind == "provenance":
                data = chain_provenance(mains, kb, args.fingerprint, **kw)
            else:
                data = chain_hypothesis(mains, kb, args.event, args.hypothesis,
                                        as_of_round=args.as_of_round, **kw)
            print(render(args.kind, data, args.format))
        except KbChainError as exc:
            print(f"[kb-chain] {exc}", file=sys.stderr)
            return 1
        finally:
            if kb is not None:
                kb.close()
            close_mains(mains)
        return 0

    if args.command == "chain-graph":
        from .chain_web_payload import ChainWebError   # web 契约(strict 校验)
        from .chain_graph import (ChainGraphError, build_chain_graph,
                                  graph_section_md, payload_to_json)
        from .chain_svg import (ChainSvgError, main_view_html, render_chain_png,
                                render_chain_svg, render_md_chain)
        from .kb_chain import close_mains, load_context, open_kb_ro, open_mains
        if not args.db or not args.event:
            print("[门禁] chain-graph 必配 --db 与 --event", file=sys.stderr)
            return 2
        if args.format == "png" and not args.out:
            print("[门禁] --format png 必配 --out <路径>(位图写文件,"
                  "不走 stdout)", file=sys.stderr)
            return 2
        mains = {}
        kb = None
        try:
            mains = open_mains(args.db, args.config_dir)
            kb = open_kb_ro(args.kb) if Path(args.kb).exists() else None
            ctx = load_context(args.config_dir)
            graph = build_chain_graph(
                mains, kb, args.event,
                ess_rules=ctx["ess_rules"], auth_roots=ctx["auth_roots"],
                near_dup_dist=(SIMHASH_NEAR_DUP_DIST
                               if args.t1_independence else None),
                config_dir=args.config_dir,
                seeds_path=(args.seeds or str(Path(args.config_dir)
                                              / "hypothesis_seeds.json")))
            if args.format == "json":
                body = payload_to_json(graph)
            elif args.format == "svg":
                body = render_chain_svg(graph)
            elif args.format == "html":
                body = main_view_html(graph)
            elif args.format == "md-chain":
                body = render_md_chain(graph)
            elif args.format == "web":
                from .chain_web_payload import from_graph as _web_from
                body = payload_to_json(_web_from(
                    graph, mains, kb, args.event, config_dir=args.config_dir))
            elif args.format == "png":
                out_p = render_chain_png(graph, args.out)
                print(f"[chain-graph] 已写 {out_p}(dot 直出位图;节点 "
                      f"{len(graph['nodes']['claims'])} 条/桥 "
                      f"{graph['meta'].get('n_bridges_drawn', 0)} 座)")
                return 0
            else:
                body = graph_section_md(graph, args.claim or None)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(body, encoding="utf-8")
                print(f"[chain-graph] 已写 {out}(节点 "
                      f"{len(graph['nodes']['claims'])} 条/证据 "
                      f"{graph['meta']['n_evidence_drawn']} 件/缺口 "
                      f"{sum(1 for c in graph['nodes']['claims'] if c['gap'])} 个)")
                if args.format == "web":
                    print(f"[chain-graph] web 契约校验通过(strict;"
                          f"节点/边计数见 meta.web)")
            else:
                print(body)
        except (ChainGraphError, ChainSvgError, ChainWebError) as exc:
            print(f"[chain-graph] {exc}", file=sys.stderr)
            return 1
        finally:
            if kb is not None:
                kb.close()
            close_mains(mains)
        return 0

    if args.command == "chain-gen":
        from .chain_gen import ChainGenError, run_chain_gen
        from .kb_chain import close_mains, load_context, open_mains
        if not args.db or not args.event:
            print("[门禁] chain-gen 必配 --db 与 --event", file=sys.stderr)
            return 2
        mains = {}
        try:
            mains = open_mains(args.db, args.config_dir)
            ctx = load_context(args.config_dir)
            from .llm import DeepSeekLLM
            llm = DeepSeekLLM()   # 需 DEEPSEEK_API_KEY(生成期=真实模型专用)
            seeds = args.seeds or str(Path(args.config_dir)
                                      / "hypothesis_seeds.json")
            stats = run_chain_gen(mains, args.kb, args.event, provider=llm,
                                  auth_roots=ctx["auth_roots"],
                                  config_dir=args.config_dir, seeds_path=seeds)
            import json as _json
            print(_json.dumps(stats, ensure_ascii=False, sort_keys=True))
            errs = ("polarity_error", "channel_error", "ev_summary_error",
                    "grouping_error", "narrative_error", "interp_error",
                    "fusion_error")
            return 1 if any(stats.get(k) for k in errs) else 0
        except (ChainGenError, ValueError) as exc:
            print(f"[chain-gen] {exc}", file=sys.stderr)
            return 1
        finally:
            close_mains(mains)

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
    elif args.envelope:
        if args.envelope == "-":   # stdin(bridge worker 流式传入;2026-11-09 bridge 适配)
            _env_text = sys.stdin.read()
        else:
            _env_text = Path(args.envelope).read_text(encoding="utf-8-sig")
        envelope = InvestigationEnvelope.model_validate(json.loads(_env_text))
    else:
        envelope = None   # resume 模式(--event):信封在下方从台账读回
    # resume 复查模式(动态报告②):--event <id> 不带 --envelope → 从台账读回
    # 登记信封续查(register_event 重开路径);信封在库=确定性,不重装配。
    # --envelope-batch 装配的信封在上方已就位,此处豁免。
    _resume_id = getattr(args, "event_resume", "") or ""
    if _resume_id:
        if args.event_id and args.event_id != _resume_id:
            print("[门禁] --event(复查)与 --event-id 指向不同事件:"
                  f"{_resume_id} vs {args.event_id}", file=sys.stderr)
            return 2
        from .store.db import connect as _connect
        _conn = _connect(args.db)
        try:
            _row = _conn.execute(
                "SELECT envelope_json FROM events WHERE event_id=?",
                (_resume_id,)).fetchone()
        finally:
            _conn.close()
        if _row is None:
            print(f"[resume] 事件 {_resume_id} 不在库 {args.db}(先 investigate"
                  " 创建;或检查 --db 路径)", file=sys.stderr)
            return 2
        envelope = InvestigationEnvelope.model_validate(json.loads(_row["envelope_json"]))
        print(f"[resume] 续查 {_resume_id}:信封自台账读回"
              f"({envelope.signal_label()[:40]})")
        args.event_id = _resume_id
    elif not args.envelope and not getattr(args, "envelope_batch", None):
        print("[门禁] investigate 需要 --envelope <信封> 或 --event <事件id>(复查)",
              file=sys.stderr)
        return 2
    event_id = args.event_id or envelope.derive_event_id()
    refresh_plan: dict[int, list[dict]] = {}
    if args.refresh_plan:
        raw = json.loads(Path(args.refresh_plan).read_text(encoding="utf-8-sig"))
        refresh_plan = {int(k): v for k, v in raw.items()}

    # 采集档位门禁(方案B双回放,2026-10-25 解禁):--replay(LLM 段)×
    # --collect-replay(采集段)=全链冻结复现——egress 按 URL 指纹弹出录制
    # 响应,证据编号天然与录制一致;18 案实证 claims+sections 逐条一致
    # (tests/_planb_replay.py)。仍禁 --replay×--crawler:真实采集证据编号
    # 不可预测,LLM 段无法回放。
    if args.replay and args.crawler and not args.collect_replay:
        print("[门禁] --replay(LLM 回放)只配 --seed 证据种子或"
              "--collect-replay 采集回放(双回放);"
              "真实采集请配 --live(证据编号不可预测,无法回放 LLM)",
              file=sys.stderr)
        return 2
    if args.record_collection and not args.crawler:
        print("[门禁] --record-collection 必须配合 --crawler 使用", file=sys.stderr)
        return 2
    if args.crawler and not args.live and not (
            args.replay and args.collect_replay):
        print("[门禁] --crawler 必须配合 --live(真实模型)或"
              "--replay×--collect-replay(双回放)使用:"
              "证据编号不可预测,LLM 回放无法对齐", file=sys.stderr)
        return 2
    # K2 第四通道门禁(--kb 默认 off):库必须存在;版本配对在 replay 块
    if getattr(args, "kb", "off") == "on" and not Path(args.kb_path).exists():
        print(f"[门禁] --kb on 需 KB 库存在: {args.kb_path}"
              f"(先派生:python -m sense2act kb-derive ... --kb {args.kb_path})",
              file=sys.stderr)
        return 2
    if getattr(args, "assets", False) and args.crawler != "local":
        print("[门禁] --assets 需配合 --crawler local(附件经本地出网点下载)",
              file=sys.stderr)
        return 2
    if getattr(args, "no_dry_run", False) and not getattr(args, "snapshots", False):
        print("[门禁] --no-dry-run 只配合 --snapshots 使用(截图真拍开关)",
              file=sys.stderr)
        return 2
    if (getattr(args, "snapshots", False)
            and getattr(args, "no_dry_run", False) and not args.live):
        print("[门禁] 截图真拍(--no-dry-run)需配合 --live:"
              "回放/离线零网络纪律(浏览器出网也是出网)",
              file=sys.stderr)
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
            # KB 配对(K2,2026-10-27):录制未开 kb 而回放开=检索件凭空多出,
            # 证据编号全体平移;录制开了而版本漂移=kb 内容不一致(照
            # ask_answers 半配对先例;旧录制件无 kb 键且当前 off=照常放行)。
            if getattr(args, "kb", "off") == "on":
                if meta.get("kb") is not True:
                    print("[参数配对] 录制件未开 --kb,回放开=检索件错位"
                          "(证据编号平移,回放静默失真)。", file=sys.stderr)
                    return 2
                from .kb_retrieve import kb_version_of
                meta_kbver = meta.get("kb_version")
                if meta_kbver and meta_kbver != kb_version_of(args.kb_path):
                    print(f"[参数配对] KB 版本漂移: 录制={meta_kbver} "
                          f"当前={kb_version_of(args.kb_path)}(kb 内容不一致,"
                          f"须用录制同版 kb.db 或重录)。", file=sys.stderr)
                    return 2
            ask_answers = meta.get("ask_answers") or []

    try:
        llm, recorder = _build_llm(args)
        # 线索转生(三阶段 §六.3):仅 resume 会话装配;--replay 不装配
        # (回放 fixture 零触碰,§八红线);kb 库不存在=如实跳过。
        reborn = None
        if (_resume_id and not args.replay
                and Path(getattr(args, "kb_path", "") or "").exists()):
            from . import chain_gen as _cgen
            from .chain_graph import load_present_rules as _lpr
            _rules = _lpr(args.config_dir)
            _kb_path = args.kb_path

            def _reborn_hook(led, eid, rnd, _kb=_kb_path, _r=_rules):
                return _cgen.register_reborn(_kb, led, eid, _r, rnd)
            reborn = _reborn_hook
        pipeline = build_pipeline(args, llm, args.db, ask_answers=ask_answers,
                                  reborn=reborn)
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
            # kb_version=KB 版本快照(K2):回放配对消费(--kb on 时必写)。
            extra = None
            if getattr(args, "kb", "off") == "on":
                from .kb_retrieve import kb_version_of
                extra = {"kb_version": kb_version_of(args.kb_path)}
            if getattr(args, "ask_mode", "headless") != "headless":
                answers = getattr(pipeline._clarifier, "answers", None)  # noqa: SLF001
                extra = (extra or {})
                extra["ask_answers"] = answers if answers is not None else []
            write_run_meta(args.record, args, extra=extra)
        if gate is not None and hasattr(gate, "save"):
            gate.save()   # P0 录制完整性:成败都落盘(I3)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _add_investigate_parser(sub) -> argparse.ArgumentParser:
    """investigate 子命令参数注册(main 与 bridge worker 共用同一参数面
    ——2026-11-09 bridge 适配:参数面单源,不再两处漂移)。"""
    p = sub.add_parser("investigate", help="对一份信号信封展开调查")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--envelope", help="信封 JSON 路径(与 --envelope-batch/--event 三选一)")
    src.add_argument("--envelope-batch",
                     help="上游 Top-K 批量信号 JSON(就地装配第 --signal-index 条信封)")
    src.add_argument("--event", dest="event_resume", default="",
                     help="复查模式:从台账读回该事件的登记信封续查(动态报告)")
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
    p.add_argument("--red-team", default="off", choices=["on", "off"],
                   help="反驳模式(方案三):判决后对抗复核——对已证实∧世界断言∧"
                        "单根的脆弱结论生成反驳假设+定向查询,反证走既有 S6 复验,"
                        "空手不加分只留痕(默认 off)")
    p.add_argument("--red-team-budget", type=int, default=2,
                   help="反驳模式每 claim 定向查询上限(默认 2)")
    p.add_argument("--flip-guard", default="off", choices=["on", "off"],
                   help="D4 维持偏置闸(默认 off 入 META_KEYS):复验场景降级"
                        "需硬反证/冷却窗外拦+升回滞后(2:1)+抖动报警;规则表"
                        " data/config/flip_rules.json 版本化改表即生效;on 时旧"
                        "录制件回放须同开(meta 配对,照 --predicate-check 先例)")
    p.add_argument("--predicate-form", default="off", choices=["on", "off"],
                   help="谓词形态标注(手术包件 4,2026-10-22):S1b 出生时标注"
                        " predicate_form,判决单源例外加法保守合并 blocklist∨LLM"
                        "(只能加拦;E2 三判据已过闸;默认 off=纯 blocklist,"
                        "重录波以全开录制后转正)")
    p.add_argument("--predicate-check", default="off", choices=["on", "off"],
                   help="谓词充分性核对(2026-10-25):判决梯权威放行路径前"
                        "核对证据内容是否载明 claim 谓词,不载明→维持待证实"
                        "(治过度断言:权威单源例外便车+断言超出;触发正清单"
                        "只进病根族,双重标注一致性门;默认 off=不装配零痕迹;"
                        "on 时旧录制件回放须同开[meta 配对])")
    p.add_argument("--corpus-bound-code", default="off", choices=["on", "off"],
                   help="corpus_bound 代码判定化(手术包件 6,2026-10-22):"
                        "claim×信封语料规范化子串匹配,代码覆写模型标注"
                        "(保守取向:对不上落 world 慢路;PRJ- 门不受影响;"
                        "默认 off=模型标注现行为)")
    p.add_argument("--t1-independence", default="off", choices=["on", "off"],
                   help="T1 根独立性查检(手术包件 5,2026-10-22):支持根"
                        "内容级近重复折并(判决侧与 ESS 侧同折口径一致;"
                        "只折逐字级转载,宁漏折不可误折;默认 off=数根现行为)")
    p.add_argument("--kb", default="off", choices=["on", "off"],
                   help="S3 第四通道(K2,2026-10-27):KB 检索(蓝图 §四;"
                        "确定性三通道,只服务正卡区∧public;lineage 继承="
                        "复曝不涨独立根;默认 off=不装配=全链零字节差异;"
                        "泄漏墙:bench 主读数永远 KB-off;配 --kb-path)")
    p.add_argument("--kb-path", default="data/kb.db",
                   help="KB 库路径(--kb on 必须存在;版本入 meta 配对,"
                        "路径不入 META_KEYS 照 --db 先例)")
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
    p.add_argument("--ess", default="on", choices=["on", "off"],
                   help="ESS 证据强度分旁车(默认 on;零行为参数,不入 META_KEYS):"
                        "仅控 finalize 末尾是否落 runs/<event>/ess.json 与"
                        " decision_log 概要(kind='其他');分数永不进判决/排序")
    p.add_argument("--assets", action="store_true",
                   help="报告附件(A 线,默认关):S7 前对 official_website 证据"
                        "下载产品图入报告(仅呈现,不进判决链);需 --crawler local")
    p.add_argument("--snapshots", action="store_true",
                   help="招标公告截图(B 线,默认关):对已证实且挂判决的 ccgp/"
                        "天津政采关键证据整页截图入报告附件;browser_auto 隔离通道"
                        "(决策 #14),默认 dry-run,硬限协议强制")
    p.add_argument("--no-dry-run", action="store_true",
                   help="配合 --snapshots:真拍(默认 dry-run 只打印计划);"
                        "真拍需 --live(回放/离线零网络纪律)且需已装 playwright")
    p.add_argument("--no-interleave", action="store_true",
                   help="退化为查完再写(对照实验)")
    p.add_argument("--deployment-mode", default="report_only",
                   choices=["report_only", "action_facing"])
    p.add_argument("--event-id", default="", help="留空则按信封派生")
    p.add_argument("--refresh-plan", default="",
                   help="状态刷新计划 JSON(测试用;{round:[{draft_id,new_status,evidence}]})")

    return p


def build_parser() -> argparse.ArgumentParser:
    """兼容入口(sense2act/bridge/worker.py 装配 investigate 用;
    2026-11-09 bridge 适配:main() 重构后模块级 build_parser 消失,
    此函数恢复同名额——只注册 investigate 子命令,参数面与 main()
    完全一致,经 _add_investigate_parser 单源)。"""
    parser = argparse.ArgumentParser(prog="sense2act")
    sub = parser.add_subparsers(dest="command")
    _add_investigate_parser(sub)
    return parser
