"""新闻检测入口：筛选、规则评分、归组及原始输出；不运行调查 Agent。"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .core.bridge import MonitoringTool, item_to_evidence
from .core.config_loader import load_monitor_config
from .core.models import MonitorConfig, MonitoredItem
from .core.normalize import normalize_text
from .event_identity import NewsMonitorEngine
from .relevance import business_relation, plain_text, word_present
from .schema import Evidence, NewsDecision, NewsProfile
from .signal_explainer import build_alerts, news_signal_payload, original_deadline

DEFAULT_CONFIG = Path(__file__).with_name('config.json')


def write_json(path, payload):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


@contextmanager
def directory_lock(directory, name):
    path = Path(directory) / name
    try:
        handle = path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise RuntimeError('新闻目录正在使用；异常中断后先确认进程停止，再移除锁文件') from exc
    try:
        with handle:
            handle.write(datetime.now(timezone.utc).isoformat())
        yield
    finally:
        path.unlink()


def content_item(raw):
    """接受 ContentItem 或 Evidence；不修改采集端传入对象。"""
    if not isinstance(raw, dict):
        raise ValueError('新闻条目必须为对象')
    if 'evidenceId' in raw and 'source' in raw:
        evidence = Evidence.model_validate(raw)
        return {
            'contentId': evidence.evidence_id, 'title': evidence.title or '', 'platform': evidence.source,
            'summary': evidence.snippet, 'content': evidence.content, 'url': evidence.url,
            'publishedAt': raw.get('publishedAt'), 'dedupKey': evidence.dedup_key or evidence.evidence_id,
            'quality': {'hasFullContent': evidence.has_full_content, 'warnings': evidence.quality_warnings},
            'ext': {'sourceName': evidence.source_name, 'authorType': evidence.author_type,
                    **({'publication': evidence.publication.model_dump(mode='json', by_alias=True)}
                       if evidence.publication else {})},
        }
    item = MonitoringTool._flatten_content_item(deepcopy(raw))
    if not item.get('dedupKey'):
        item['dedupKey'] = item.get('contentId')
    return item


class NewsSignalDiscovery:
    def __init__(self, config_path=DEFAULT_CONFIG, data_dir='outputs/news', *, publication_reader=None,
                 completed_signals=()):
        self.config_path = Path(config_path)
        if self.config_path.is_dir():
            self.config_path /= 'config.json'
        payload = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.profile = NewsProfile.model_validate(payload['profile'])
        overrides = payload['monitor']
        if 'entities' not in overrides:
            raise ValueError('新闻监测需显式配置 monitor.entities')
        config = MonitorConfig.model_validate({**load_monitor_config().model_dump(by_alias=True), **overrides})
        if not any(entity.enabled for entity in config.entities):
            raise ValueError('至少配置一个启用的监测主体')
        settings = config.settings
        values = (settings.agent_trigger_score, settings.lookback_hours, settings.cooldown_hours, settings.tau_hours)
        if (not all(math.isfinite(v) for v in values) or settings.agent_trigger_score < 0
                or min(settings.lookback_hours, settings.cooldown_hours, settings.tau_hours) <= 0):
            raise ValueError('新闻分数与时间窗配置不合法')
        if settings.agent_auto_trigger:
            raise ValueError('新闻检测不直接执行 Agent，请设置 monitor.settings.agentAutoTrigger=false')
        self.tool = MonitoringTool(data_dir=data_dir)
        self.tool.config = config
        self.tool.engine = NewsMonitorEngine(config, self.tool.store)
        self.data_dir = Path(data_dir)
        self.publication_reader = publication_reader
        self.completed_signals = tuple(Path(path) for path in completed_signals)

    def screen(self, items, now, *, check_time=True):
        accepted, decisions, seen, seen_urls = [], [], set(), set()
        for index, raw in enumerate(items):
            try:
                item = content_item(raw)
                MonitoredItem.model_validate(item)
                evidence = Evidence.model_validate(item_to_evidence(item))
                if not item.get('contentId') or not str(item.get('title') or '').strip():
                    raise ValueError('缺少标识或标题')
            except (ValueError, TypeError, AttributeError):
                decisions.append(NewsDecision(input_index=index, decision='dropped', reason='条目缺少有效标识、来源或标题'))
                continue
            decision = dict(input_index=index, evidence_id=item['contentId'])
            verified = evidence.publication and evidence.publication.status == 'verified'
            if check_time and verified:
                try:
                    deadline = original_deadline(evidence.model_dump(mode='json', by_alias=True),
                                                 self.tool.config.settings.lookback_hours, self.profile.calendar_timezone)
                except (ValueError, TypeError, OverflowError):
                    decisions.append(NewsDecision(**decision, decision='observed', reason='原文链接或时间依据无法验证'))
                    continue
                day = evidence.published_calendar_date
                future = (evidence.published_at and evidence.published_at > now + timedelta(minutes=5))
                if not evidence.published_at and day:
                    future = day > now.astimezone(ZoneInfo(self.profile.calendar_timezone)).date()
                if deadline <= now or future:
                    decisions.append(NewsDecision(**decision, decision='dropped', reason='报道超出监测时间窗或时间在未来'))
                    continue
            elif check_time and self.profile.require_published_at:
                decisions.append(NewsDecision(**decision, decision='observed', reason='缺少原文发布时间，不自动提交'))
                continue
            if item['dedupKey'] in seen or (item.get('url') and item['url'] in seen_urls):
                decisions.append(NewsDecision(**decision, decision='duplicate', reason='本轮重复条目或同一原文链接'))
                continue
            text = normalize_text(' '.join(plain_text(item.get(k)) for k in ('title', 'summary', 'content')))
            excluded = next((w for w in self.profile.exclude_words if normalize_text(w) in text), None)
            excluded = excluded or next((w for w in self.profile.exclude_title_words
                                         if word_present(w, normalize_text(plain_text(item.get('title'))))), None)
            if excluded:
                decisions.append(NewsDecision(**decision, decision='dropped', reason=f'命中排除词：{excluded}'))
                continue
            hits, terms, anchors, all_hits = business_relation(item, self.profile, self.tool.config.entities)
            if not hits or not terms:
                observed = bool(all_hits or any(word_present(w, text) for w in self.profile.business_keywords))
                decisions.append(NewsDecision(**decision, decision='observed' if observed else 'dropped',
                    reason='标题及相邻段落未找到主体与业务关联' if observed else '无目标主体及业务关联'))
                continue
            seen.add(item['dedupKey'])
            if item.get('url'):
                seen_urls.add(item['url'])
            item['ext'] = {**item.get('ext', {}), 'newsRelatedSubjects': [h.entity.entity_id for h in hits],
                           'newsRelationAnchors': anchors}
            accepted.append(item)
            decisions.append(NewsDecision(**decision, decision='kept', reason='标题或相邻段落中主体与企业业务相关',
                                          business_terms=terms, subject_ids=[h.entity.entity_id for h in hits]))
        return accepted, decisions

    def publication_candidates(self, items):
        """摘要不充分时仍读原文；不能用旧的严格筛选把正文补读机会先删掉。"""
        candidates = []
        for raw in items:
            try:
                item = content_item(raw)
                MonitoredItem.model_validate(item)
                text = normalize_text(' '.join(plain_text(item.get(k)) for k in ('title', 'summary', 'content')))
                if any(normalize_text(w) in text for w in self.profile.exclude_words):
                    continue
                if any(word_present(w, normalize_text(plain_text(item.get('title')))) for w in self.profile.exclude_title_words):
                    continue
                _, terms, _, hits = business_relation(item, self.profile, self.tool.config.entities)
                if item.get('url') and (hits or terms or any(word_present(w, text) for w in self.profile.business_keywords)):
                    candidates.append(item)
            except (ValueError, TypeError, AttributeError):
                continue
        return candidates

    def run(self, items=None, *, now=None, fetcher=None, collection_mode='saved_news'):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('监测时间必须带时区')
        now = now.astimezone(timezone.utc)
        with directory_lock(self.data_dir, 'news_monitor.lock'):
            state = self.tool.store.load_state()
            if state.get('newsEnterpriseId') not in (None, self.profile.enterprise_id):
                raise ValueError('更换目标企业请使用新的监测目录')
            self._import_completed(state)
            self.tool.store.save_state(state)
            output = self.data_dir / 'ticks' / (now.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex[:8])
            output.mkdir(parents=True, exist_ok=False)
            collection = {}
            if fetcher is not None:
                items, collection = fetcher(output, int(state.get('tickCount', 0)))
            if not isinstance(items, list):
                raise ValueError('新闻输入必须是列表')
            write_json(output / 'input.json', items)
            screened = items
            if self.publication_reader is not None:
                candidates = self.publication_candidates(items)
                refreshed = self.publication_reader.read_items(candidates, output / 'article_responses.jsonl')
                by_id = {item['contentId']: item for item in refreshed}
                screened = [by_id.get(raw.get('contentId') or raw.get('evidenceId'), raw)
                            if isinstance(raw, dict) else raw for raw in items]
                collection['publication'] = self.publication_reader.summary()
                write_json(output / 'enriched.json', screened)
            accepted, decisions = self.screen(screened, now)
            write_json(output / 'screening.json', [d.model_dump(mode='json', by_alias=True) for d in decisions])
            observations = {(row.get('dedupKey'), row.get('reason')) for row in self.tool.store.load_observation()}
            self.tool.store.append_observation([
                {'observedAt': now.isoformat(), 'dedupKey': d.evidence_id, 'reason': d.reason, 'inputIndex': d.input_index}
                for d in decisions if d.decision == 'observed' and (d.evidence_id, d.reason) not in observations
            ])
            views = []
            for item in accepted:
                evidence = Evidence.model_validate(item_to_evidence(item))
                views.append({**item, 'summary': ' '.join(str(item.get(k) or '') for k in ('summary', 'content')),
                              'publishedAt': evidence.published_at.isoformat() if evidence.published_at else None,
                              'publishedAtConfidence': 1 if evidence.published_at else None})
            monitor = self.tool.engine.run_tick(views, now=now)
            alerts = build_alerts(self.profile, self.tool, monitor.pop('alerts'), accepted, decisions)
            self.tool.store.append_alerts(alerts)
            state = self.tool.store.load_state()
            state['newsEnterpriseId'] = self.profile.enterprise_id
            self.tool.store.save_state(state)
            signals = [self.signal_payload(alert) for alert in alerts]
            write_json(output / 'signals.json', signals)
            eligible = sum(alert.get('triggerEligible') and alert.get('status') == 'pending'
                           for alert in self.tool.store.load_alerts())
            summary = {
                'schemaVersion': 'news_monitor.v1', 'generatedAt': now.isoformat(),
                'enterpriseId': self.profile.enterprise_id, 'collectionMode': collection_mode,
                'inputItems': len(items), 'keptItems': len(accepted),
                'screeningCounts': {kind: sum(d.decision == kind for d in decisions)
                                    for kind in ('kept', 'dropped', 'observed', 'duplicate')},
                'signalsCreated': len(signals), 'pendingEligible': eligible,
                'completedHistoryLoaded': len(state.get('newsCompletedHistory', {}).get('signals', {})),
                'completedHistoryReused': sum(s['investigationStatus'] == 'completed' for s in signals),
                'agentTrigger': {'due': eligible, 'dispatched': 0, 'skippedReason': 'auto_trigger_disabled'},
                'investigations': [], 'monitor': monitor, 'collection': collection,
                'outputDirectory': str(output.resolve()), 'dataDirectory': str(self.data_dir.resolve()),
            }
            write_json(output / 'summary.json', summary)
            return summary

    def signal_payload(self, alert):
        return news_signal_payload(self.profile, self.tool, alert)

    def _import_completed(self, state):
        history = state.setdefault('newsCompletedHistory', {'signals': {}, 'urls': {}})
        for path in self.completed_signals:
            records = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(records, list):
                raise ValueError('已调查信号文件必须为数组')
            for record in records:
                if (record.get('enterpriseId') != self.profile.enterprise_id
                        or record.get('schemaVersion') != 'news_signal.v1'
                        or record.get('investigationStatus') != 'completed' or not record.get('signalId')):
                    continue
                detail = {'signalId': record['signalId'], 'investigationDirectory': record.get('investigationDirectory')}
                history['signals'][record['signalId']] = detail
                for evidence in record.get('evidence', []):
                    if evidence.get('url'):
                        history['urls'][evidence['url'].split('#', 1)[0]] = detail
