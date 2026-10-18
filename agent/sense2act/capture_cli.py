"""capture 子命令:浏览器捕获的显式入口(默认 dry-run,硬限协议强制)。"""
from __future__ import annotations

import argparse
import json
import sys

from .browser_auto import (
    CaptureBlocked,
    CaptureEnvelopeError,
    CaptureGate,
    CaptureLimits,
    CaptureRunner,
    RunnerConfig,
    toutiao_detail_to_lite,
    xhs_items_to_lite,
)
from .store import LedgerRepository, connect


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="sense2act capture",
        description="浏览器捕获(红线例外通道;默认 dry-run,硬限协议强制)")
    parser.add_argument("--platform", required=True,
                        choices=["toutiao", "xiaohongshu"])
    parser.add_argument("--url", help="toutiao: 文章页 URL(全文回填)")
    parser.add_argument("--query", help="xiaohongshu: 搜索词")
    parser.add_argument("--db", default="data/sense2act.db")
    parser.add_argument("--event", required=True,
                        help="捕获证据挂入的事件 id(须已存在)")
    parser.add_argument("--state", default="data/browser_capture_state.json")
    parser.add_argument("--no-dry-run", action="store_true",
                        help="真实执行(需先装好 playwright 环境)")
    parser.add_argument("--wsl-distro", default="Ubuntu-22.04")
    args = parser.parse_args(argv)

    word = args.query or args.url or ""
    gate = CaptureGate(args.state)
    try:
        gate.check(word)
    except CaptureBlocked as exc:
        print(f"[捕获拒绝] {exc}", file=sys.stderr)
        return 2

    runner = CaptureRunner(RunnerConfig(
        wsl_distro=args.wsl_distro, dry_run=not args.no_dry_run))
    if runner.cfg.dry_run:
        runner.run(args.platform, **({"url": args.url} if args.platform == "toutiao"
                                     else {"query": args.query}))
        print("[dry-run] 仅演练:打印了将执行的命令,未捕获、未入账、未计入硬限。"
              "环境就绪后加 --no-dry-run 真跑。")
        return 0
    try:
        if args.platform == "toutiao":
            envelope = runner.run("toutiao", url=args.url)
            lite = toutiao_detail_to_lite(envelope)
            items = [lite]
        else:
            envelope = runner.run("xiaohongshu", query=args.query)
            items = xhs_items_to_lite(envelope)
    except CaptureEnvelopeError as exc:
        gate.record(word, blocked=True)
        print(f"[信封拒绝] {exc}(已按协议当日自锁)", file=sys.stderr)
        return 2
    except Exception as exc:
        gate.record(word, blocked=True)
        print(f"[捕获失败] {exc}(已按协议当日自锁)", file=sys.stderr)
        return 2
    gate.record(word)

    conn = connect(args.db)
    ledger = LedgerRepository(conn, "data/config")
    if not ledger.event_exists(args.event):
        print(f"[错误] 事件 {args.event} 不存在(先跑一次 investigate 建立事件)",
              file=sys.stderr)
        return 2
    saved = []
    for lite in items:
        eid = ledger.insert_evidence(args.event, {
            "platform": lite.platform, "source": lite.source,
            "url": lite.url, "title": lite.title, "content": lite.content,
            "published_at": lite.published_at,
            "author_type": (lite.author_type + " " if lite.author_type else "")
            + "[browser_auto]",
            "lineage_root": lite.source,
            "dedup_key": lite.dedup_key,
        }, round_no=0)
        if eid:
            saved.append(eid)
    print(json.dumps({"captured": len(items), "ingested": len(saved),
                      "evidence_ids": saved, "event": args.event},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
