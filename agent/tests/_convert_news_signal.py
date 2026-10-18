"""上游 news_signal.v1 -> v2 统一提交外层(rumor)转换器(诊断/对接工具)。

用法:
    python tests/_convert_news_signal.py <signals.json> <out_v2.json>

映射(信号发现层 camelCase -> 装配器 snake_case;extra=forbid 白名单):
    signalId -> signal_id   enterpriseId -> company_id   kind 固定 "rumor"
    subjectName -> subject_name          publishedAt -> signal_time
    title -> summary                     evidence[] -> clues[](线索卡)
    watch_focus 由监测企业/主体生成(喂 S1 调查指令)。
platform 从线索 url 域名推导;authorType 缺省"媒体"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")


def _domain_head(url: str) -> str:
    try:
        host = urlparse(url or "").netloc.lower().removeprefix("www.")
    except Exception:
        return "other"
    parts = [p for p in host.split(".") if p]
    if not parts:
        return "other"
    return parts[0] if parts[0] not in ("com", "cn", "org", "net") else (
        parts[1] if len(parts) > 1 else "other")


def convert(signals: list[dict]) -> list[dict]:
    out = []
    for x in signals:
        evs = x.get("evidence") or []
        if not evs:
            continue
        e0 = evs[0]
        subject = x.get("subjectName") or ""
        watcher = x.get("enterpriseName") or ""
        clues = [{
            "clue_id": e.get("evidenceId") or f"clue-{i}",
            "source": e.get("sourceName") or e.get("source") or "",
            "platform": _domain_head(e.get("url") or ""),
            "title": e.get("title") or "",
            "content": (e.get("content") or e.get("snippet") or "")[:3000],
            "url": e.get("url") or "",
            "published_at": e.get("publishedAt") or "",
            "author_type": e.get("authorType") or "媒体",
        } for i, e in enumerate(evs)]
        out.append({
            "signal_id": x.get("signalId") or "",
            "company_id": x.get("enterpriseId") or "",
            "kind": "rumor",
            "payload": {
                "subject_name": subject,
                "subject_credit_code": "",
                "summary": (x.get("title") or "").strip(),
                "source": e0.get("source") or "",
                "signal_time": x.get("publishedAt")
                or x.get("generatedAt") or "",
                "watch_focus": (f"核实{subject}该传闻是否属实;确认官方信源与"
                                f"发布时间;评估对监测企业{watcher}的影响。"),
                "clues": clues,
            },
        })
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    out = convert(src if isinstance(src, list) else [src])
    Path(sys.argv[2]).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[convert] {len(src)} 条 news_signal.v1 -> {len(out)} 条 v2 rumor"
          f" 提单 -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
