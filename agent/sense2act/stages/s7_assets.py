"""S7 前置资产步骤(A 线,2026-10-23;B 线截图 2026-10-23)。

认识论边界(用户拍板):图片=附件,不是证据——不进 content、不进验票、
不进谱系折算;evidence.assets 列唯一写口在台账(LedgerRepository.
attach_assets),本步骤只对 official_website 来源的 evidence 附件化(A 线
产品图)与招标关键证据截图(B 线),S4/S5/S6 不读该列,判决链物理隔离。

出网纪律:图片下载必经注入的 egress.get_binary(唯一出网点不破);详情页
重新解析用 egress.get——S3 抓过的 URL 命中运行内 memo,零新增文本出网。
B 线截图走 browser_auto 隔离通道(决策 #14):默认 dry-run(不真拍、不
消耗硬限),真拍受 CaptureGate 硬限协议强制(单次/日额/同词/最小间隔/
遇阻自锁,不放宽)。失败不伪装:任一环节失败=该证据无附件(如实空缺),
不阻交付、不造假图。
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..browser_auto.limits import CaptureBlocked
from ..crawler.adapters.official_site import extract_image_urls
from ..store.ledger import LedgerRepository

log = logging.getLogger("s7_assets")

_CONTENT_TYPE_EXT = {"image/jpeg": "jpg", "image/png": "png",
                     "image/webp": "webp"}

# B 线截图:招标域平台 + 公告域名白名单(只拍政府公告页,不拍第三方)
_SNAPSHOT_PLATFORMS = ("ccgp", "tianjin_zfcg")
_SNAPSHOT_HOSTS = ("ccgp.gov.cn", "ccgp-tianjin.gov.cn")
# 关键行小图定位词(2026-10-23 二代):与 ccgp 解析器字段同源(预算/截止/
# 编号/采购人),每词截其所在段落/表格行;同词多现只截首现
_SNAPSHOT_KEY_PATTERNS = ("预算金额", "投标截止时间", "提交投标文件截止时间",
                          "项目编号", "采购人")


@dataclass(frozen=True)
class AssetConfig:
    per_evidence: int = 3          # 每条证据最多附件图数
    per_event: int = 9             # 每事件总上限(报告别成图库)
    min_bytes: int = 2048          # 下限:小于此多为 spacer/占位图
    max_bytes: int = 5 * 1024 * 1024
    snapshot_cap: int = 3          # B 线:单事件截图上限(硬限 per_run=5 之内)


def _host_allowed(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in _SNAPSHOT_HOSTS)


class AssetCollector:
    """报告附件收集(官网产品图 + 招标公告截图);管线 S7 前调用,默认不装配。"""

    def __init__(self, ledger: LedgerRepository, egress, runs_dir="runs",
                 config: AssetConfig | None = None,
                 snapshot_runner=None, snapshot_gate=None):
        self._ledger = ledger
        self._egress = egress
        self._runs = Path(runs_dir)
        self.cfg = config or AssetConfig()
        self._runner = snapshot_runner   # LocalPlaywrightRunner(B 线;None=不截)
        self._gate = snapshot_gate       # CaptureGate(硬限;真拍必须有)

    def collect(self, event_id: str) -> int:
        """A 线:对 official_website 来源 evidence 下载产品图。

        两段式(2026-10-23 二代):先下载暂存,再做**跨证据 sha256 去重**——
        同一张图出现在 ≥2 条证据 = 站级公共图(页头横幅/二维码,与各条
        内容无关),整组撤下并删文件;同证据内同图也只留一张(用户反馈:
        无关站级图混入报告)。
        """
        if self._egress is None:         # 仅开 B 线时无出网点,如实零图
            return 0
        rows = self._ledger.conn.execute(
            "SELECT evidence_id, url FROM evidence"
            " WHERE event_id=? AND platform='official_website'"
            " ORDER BY rowid", (event_id,)).fetchall()
        if not rows:
            return 0
        out_dir = self._runs / event_id / "assets"
        budget = self.cfg.per_event
        staged: list[tuple[str, dict]] = []          # (evidence_id, asset)
        for row in rows:
            if budget <= 0:
                break
            assets, budget = self._collect_evidence(
                row["evidence_id"], row["url"], out_dir, budget)
            staged += [(row["evidence_id"], a) for a in assets]
        # 跨证据去重:同 sha 挂 ≥2 条证据 → 站级装饰全撤
        owners: dict[str, set[str]] = {}
        for eid, a in staged:
            owners.setdefault(a["sha256"], set()).add(eid)
        junk = {sha for sha, eids in owners.items() if len(eids) > 1}
        # 同证据内去重(同 sha 只留首张)
        seen: set[tuple[str, str]] = set()
        kept: list[tuple[str, dict]] = []
        for eid, a in staged:
            if a["sha256"] in junk:
                self._unlink(out_dir, a["path"])
                continue
            key = (eid, a["sha256"])
            if key in seen:
                self._unlink(out_dir, a["path"])
                continue
            seen.add(key)
            kept.append((eid, a))
        dropped = len(staged) - len(kept)
        if dropped:
            log.info("event %s 撤下与内容无关的站级/重复图 %d 张",
                     event_id, dropped)
        by_evidence: dict[str, list[dict]] = {}
        for eid, a in kept:
            by_evidence.setdefault(eid, []).append(a)
        for eid, assets in by_evidence.items():
            self._ledger.attach_assets(eid, assets)
        total = len(kept)
        if total:
            log.info("event %s assets: %d 张(official_website)", event_id, total)
        return total

    @staticmethod
    def _unlink(out_dir: Path, rel_path: str) -> None:
        try:
            (out_dir / Path(rel_path).name).unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # B 线:招标公告截图(决策 #14 隔离通道)

    def snapshots(self, event_id: str) -> int:
        """确定性选题(零 LLM):招标域 ∧ 被裁定引用 ∧ 所属 claim 已证实,
        入账序取前 snapshot_cap,域白名单过滤;dry-run 只打印不消耗硬限。"""
        if self._runner is None:
            return 0
        rows = self._ledger.conn.execute(
            "SELECT DISTINCT e.evidence_id AS eid, e.url AS url"
            " FROM evidence e"
            " JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
            " JOIN claims c ON c.claim_id=ce.claim_id"
            " WHERE e.event_id=? AND e.platform IN (?,?)"
            "   AND ce.ruling IN ('支持','矛盾','时效刷新')"
            "   AND c.status='已证实'"
            " ORDER BY e.rowid",   # 入账序=rowid;evidence_id 字符串序会 E13<E2(2026-10-23 live 实证)
            (event_id, *_SNAPSHOT_PLATFORMS)).fetchall()
        targets = [r for r in rows
                   if r["url"] and _host_allowed(r["url"])][: self.cfg.snapshot_cap]
        if not targets:
            return 0
        out_dir = self._runs / event_id / "assets"
        if self._runner.dry_run:
            for r in targets:
                tag = r["eid"].rsplit(":", 1)[-1]
                self._runner.capture(r["url"], out_dir / f"{tag}_shot.png",
                                     key_patterns=_SNAPSHOT_KEY_PATTERNS)
            log.info("event %s 截图 dry-run:%d 个目标,未真拍未计硬限",
                     event_id, len(targets))
            return 0
        if self._gate is None:
            log.warning("截图真拍缺少硬限闸门(CaptureGate),整步跳过")
            return 0
        shot = 0
        for r in targets:
            if not self._admit(r["url"]):
                break                   # 硬限即停:日额/自锁/同词上限
            self._gate.record(f"shot:{r['url']}")
            tag = r["eid"].rsplit(":", 1)[-1]
            cap = self._runner.capture(
                r["url"], out_dir / f"{tag}_shot.png",
                key_patterns=_SNAPSHOT_KEY_PATTERNS)
            if cap.full is None:
                continue                # 访问失败如实跳过
            shot += 1
            now = datetime.now(timezone.utc).isoformat()
            note = ("整页(正文容器未命中)" if cap.used_fallback_page
                    else "正文区")
            self._attach(r["eid"], {
                "path": f"assets/{tag}_shot.png",
                "sha256": hashlib.sha256(cap.full.read_bytes()).hexdigest(),
                "source_url": r["url"],
                "content_type": "image/png",
                "bytes": cap.full.stat().st_size,
                "fetched_at": now,
                "kind": "screenshot",
                "caption": f"公告{note}截图",
            })
            for label, kp in cap.keys:   # 关键行小图(判决引用的字段行)
                self._attach(r["eid"], {
                    "path": f"assets/{kp.name}",
                    "sha256": hashlib.sha256(kp.read_bytes()).hexdigest(),
                    "source_url": r["url"],
                    "content_type": "image/png",
                    "bytes": kp.stat().st_size,
                    "fetched_at": now,
                    "kind": "screenshot",
                    "caption": f"关键信息:{label}",
                })
        if shot:
            log.info("event %s 截图: %d 张(含关键行小图)", event_id, shot)
        return shot

    def _admit(self, url: str) -> bool:
        """过闸:最小间隔类受阻则等满再试一次;其余硬限即停(不放宽)。

        间隔等待上限=协议间隔本身+5s(时钟漂移余量),超出视为异常即停,
        不无限睡。判定靠 CaptureBlocked message 的"距上次捕获"子串——
        limits.py 按方案零改动,子串为其稳定文案。
        """
        word = f"shot:{url}"
        try:
            self._gate.check(word)
            return True
        except CaptureBlocked as exc:
            if "距上次捕获" not in str(exc):
                log.info("截图通道硬限停止: %s", exc)
                return False
            last = datetime.fromisoformat(self._gate.state.last_ts)
            wait = (self._gate.limits.min_interval_s
                    - (datetime.now(timezone.utc) - last).total_seconds() + 1)
            if wait <= 0 or wait > self._gate.limits.min_interval_s + 5:
                return False
            log.info("截图通道按硬限协议等待 %.0fs", wait)
            time.sleep(wait)
            try:
                self._gate.check(word)
                return True
            except CaptureBlocked as exc2:
                log.info("截图通道等待后仍受限: %s", exc2)
                return False

    def _attach(self, evidence_id: str, asset: dict) -> None:
        """追加附件(与 A 线图片合并,非覆盖)。"""
        existing = self._ledger.evidence_assets(
            evidence_id.rsplit(":", 1)[0]).get(evidence_id, [])
        self._ledger.attach_assets(evidence_id, existing + [asset])

    # ------------------------------------------------------------------

    def _collect_evidence(self, evidence_id: str, url: str,
                          out_dir: Path, budget: int) -> tuple[list[dict], int]:
        """单条证据:重取详情页(memo 命中零网)-> 抽图 -> 下载落盘。

        只暂存不入账(入账与站级图过滤在 collect() 两段式收口)。
        """
        try:
            page = self._page(url)
        except Exception as exc:  # noqa: BLE001 —— 附件失败不阻交付
            log.warning("assets %s 详情页不可得: %s", evidence_id, exc)
            return [], budget
        if page is None:
            return [], budget
        tag = evidence_id.rsplit(":", 1)[-1]
        assets: list[dict] = []
        for img_url in extract_image_urls(page, url,
                                          limit=self.cfg.per_evidence):
            if len(assets) >= self.cfg.per_evidence or budget <= 0:
                break
            asset = self._download(tag, img_url, url, out_dir,
                                   index=len(assets) + 1)
            if asset is not None:
                assets.append(asset)
                budget -= 1
        return assets, budget

    def _page(self, url: str) -> str | None:
        """详情页文本:经 egress(S3 已抓过的 URL 命中 memo,零新增出网)。"""
        try:
            resp = self._egress.get(url)
        except Exception:  # noqa: BLE001 —— 回放未命中/网络失败=如实无图
            return None
        if resp.status_code is None or resp.status_code >= 400:
            return None
        return resp.text

    def _download(self, tag: str, img_url: str, referer: str,
                  out_dir: Path, index: int) -> dict | None:
        """单图下载与校验;返回资产记录(失败返回 None,不伪装)。"""
        try:
            resp = self._egress.get_binary(
                img_url, headers={"Referer": referer})
        except Exception as exc:  # noqa: BLE001 —— 防盗链/未录制=跳过
            log.info("assets 跳过 %s: %s", img_url, exc)
            return None
        if resp.status_code is None or resp.status_code >= 400:
            return None
        ctype = (resp.content_type or "").split(";")[0].strip().lower()
        ext = _CONTENT_TYPE_EXT.get(ctype)
        if ext is None:                      # 非图片类型如实拒收
            return None
        size = len(resp.data)
        if not self.cfg.min_bytes <= size <= self.cfg.max_bytes:
            return None
        name = f"{tag}_img{index}.{ext}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_bytes(resp.data)
        return {
            "path": f"assets/{name}",
            "sha256": hashlib.sha256(resp.data).hexdigest(),
            "source_url": img_url,
            "content_type": ctype,
            "bytes": size,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "kind": "image",
        }
