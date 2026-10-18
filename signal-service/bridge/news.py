"""新闻文档 → 新闻事件检测 → 信号回推(契约 Backend/docs/api-design.md §9.2;信号仍以文档为锚)。

doc_type=news 的文档来自采集服务的新闻源。检测复用 signals.news 的 NewsSignalDiscovery:主体与业务
关联、事件归组、多来源共振评分。原文发布时间从文档快照(采集时存下的原页面)里读,读不到的只观察、
不立项。达到调查资格的告警推成一条信号:锚在该事件的首条新闻文档上,命中 rule_type=news_event,
detail 带 score / reliable / explanation(与招投标命中同约定),detail.rumor 带齐 Agent 的
RumorSignal 载荷(sense2act/bridge/envelope.py 据此装配 rumor 信封)。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from signals.news.publication import parse_publication
from signals.news.signal_detector import NewsSignalDiscovery
from signals.news.signal_explainer import signal_deadline
from signals.news.submission import news_to_submission

log = logging.getLogger("signal.bridge")
DETECTOR = "signal-service/news-v0.1"
RULE_TYPE = "news_event"


class NewsBridge:
    def __init__(self, client, config_path: str | Path, data_dir: str | Path, company_id: str = "platform",
                 clock=None):
        self.client, self.company_id = client, company_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        # 本进程独占新闻数据目录:上次异常退出留下的锁文件直接清掉,否则检测永久卡住
        for lock in ("news_monitor.lock", "news_service.lock"):
            (Path(data_dir) / lock).unlink(missing_ok=True)
        self.discovery = NewsSignalDiscovery(config_path, data_dir)
        self.settings = self.discovery.tool.config.settings

    def detect(self, docs: list[dict]) -> int:
        """一批新闻文档入检测,再把所有待提交的可调查告警推成信号;返回推送条数。"""
        now = self._clock()
        if docs:
            self.discovery.run([self._content_item(doc) for doc in docs], now=now)
        return self._push_pending(now)

    def _content_item(self, doc: dict) -> dict:
        """后端文档 → 新闻检测的 ContentItem;来源平台/媒体/作者类型取自采集时写入的 raw。"""
        detail = self.client.document(doc["id"])
        raw = detail.get("raw") or {}
        url = doc.get("url") or detail.get("url") or ""
        text = (doc.get("content_text") or detail.get("content_text") or "").strip()
        page = self.client.snapshot(doc["id"])
        ext = {"sourceName": raw.get("source_name") or raw.get("site") or urlsplit(url).hostname,
               "authorType": raw.get("author_type") or "未知"}
        if page and url:   # 原文时间只认快照里的发布字段,不用采集时的列表时间
            ext["publication"] = parse_publication(page, url).model_dump(mode="json", by_alias=True)
        return {"contentId": doc["id"], "dedupKey": doc["id"], "title": doc.get("title") or doc["id"],
                "platform": raw.get("platform") or "other", "url": url or None,
                "summary": text[:300] or None, "content": text or None,
                "quality": {"hasFullContent": bool(raw.get("detail_fetched", len(text) >= 200))},
                "ext": ext}

    def _push_pending(self, now: datetime) -> int:
        store, pushed = self.discovery.tool.store, 0
        timezone_name = self.discovery.profile.calendar_timezone
        for alert in store.load_alerts():
            if (alert.get("signalSchema") != "news_signal.v1" or alert.get("status") != "pending"
                    or not alert.get("triggerEligible")):
                continue
            alert_id = alert["alertId"]
            news = self.discovery.signal_payload(alert)
            try:
                expired = signal_deadline(news, self.settings.lookback_hours, timezone_name) <= now
                rumor = news_to_submission(news, self.company_id).model_dump(mode="json")["payload"]
            except (ValueError, TypeError, OverflowError) as exc:
                store.update_alert_status(alert_id, "observed", f"调查载荷校验失败,保留观察:{exc}")
                continue
            if expired:
                store.update_alert_status(alert_id, "expired", "报道超过新闻时间窗,不再提交")
                continue
            # 引擎分按其红色阈值归一到 [0,1]:红=1.0,调查门槛(agentTriggerScore)约 0.5
            score = round(min(1.0, float(news["score"]) / self.settings.red_threshold), 4)
            documents = [e["evidenceId"] for e in news["evidence"]]
            hit = {"rule_type": RULE_TYPE, "rule_id": None, "rule_version": None, "weight": 1.0, "detail": {
                "score": score, "reliable": True, "explanation": news["triggerReason"],
                "news_signal_id": alert_id, "signal_type": news["signalType"], "event_types": news["eventTypes"],
                "priority": news["priority"], "raw_score": round(float(news["score"]), 3),
                "subject_name": news["subjectName"], "document_ids": documents, "rumor": rumor}}
            result = self.client.push_signal(documents[0], [hit], score, detector=DETECTOR)
            store.update_alert_status(alert_id, "submitted", f"已推送平台信号 {result['signal_id']}")
            log.info("news signal %s doc=%s score=%.3f subject=%s auto_investigated=%s", result["signal_id"],
                     documents[0], score, news["subjectName"], result.get("auto_investigated"))
            pushed += 1
        return pushed
