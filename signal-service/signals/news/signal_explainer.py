"""保留新闻规则依据、完整线索及内部 news_signal.v1 输出。"""
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .core.bridge import alert_to_seed_event
from .core.normalize import normalize_text
from .schema import Event


def original_deadline(evidence, hours, calendar_timezone='Asia/Shanghai'):
    """原文时间决定有效期；日期级报道按配置时区的日界失效，不造发布时间。"""
    publication = evidence.get('publication') or {}
    if publication.get('status') != 'verified':
        raise ValueError('缺少原文时间依据')
    source_url, url = publication.get('sourceUrl') or '', evidence.get('url') or ''
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or urlsplit(source_url)._replace(fragment='') != parsed._replace(fragment='')):
        raise ValueError('原文链接与时间依据不一致')
    value = publication.get('value') or ''
    if not all(publication.get(key) for key in ('raw', 'precision', 'locator')):
        raise ValueError('原文时间依据不完整')
    if publication['precision'] == 'day':
        day = date.fromisoformat(value)
        if value != day.isoformat():
            raise ValueError('原文日期必须为 YYYY-MM-DD')
        end = day + timedelta(days=math.floor(hours / 24) + 1)
        return datetime.combine(end, time.min, ZoneInfo(calendar_timezone)).astimezone(timezone.utc)
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('原文时刻未标明时区')
    return (stamp + timedelta(hours=hours)).astimezone(timezone.utc)


def signal_deadline(news, hours, calendar_timezone='Asia/Shanghai'):
    evidence = news.get('evidence') or []
    if not evidence:
        raise ValueError('缺少新闻线索')
    return min(original_deadline(item, hours, calendar_timezone) for item in evidence)


def completed_investigation(signal_id, evidence, history):
    previous = history.get('signals', {}).get(signal_id)
    if previous:
        return previous
    urls = [item.get('url', '').split('#', 1)[0] for item in evidence if item.get('url')]
    matches = [history.get('urls', {}).get(url) for url in urls]
    # 新进展带来新的原文不能因为包含旧线索而被一并压制。
    return matches[0] if matches and all(matches) else None


def build_alerts(profile, tool, alerts, items, decisions):
    originals = {item['contentId']: item for item in items}
    terms = {d.evidence_id: d.business_terms for d in decisions if d.decision == 'kept'}
    prior_ids = {alert.get('alertId') for alert in tool.store.load_alerts()}
    completed = tool.store.load_state().get('newsCompletedHistory', {})
    result = []
    for alert in alerts:
        evidence = {entry['contentId']: originals[entry['contentId']]
                    for entry in alert['evidence'] if entry.get('contentId') in originals}
        if not evidence:
            continue
        alert['evidence'] = list(evidence.values())
        business = sorted({term for key in evidence for term in terms.get(key, [])})
        content = sorted({normalize_text(' '.join(str(item.get(k) or '') for k in ('title', 'summary', 'content')))
                          for item in evidence.values()})
        identity = [profile.enterprise_id, alert.get('subjectEntityId'), alert['matchedRule'],
                    alert['matchedSpans'].get('titleHash'), content]
        alert['alertId'] = 'news_' + sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
        if alert['alertId'] in prior_ids:
            continue
        prior_ids.add(alert['alertId'])
        alert['signalSchema'] = 'news_signal.v1'
        alert['businessTerms'] = business
        alert['why'] += '；与企业业务相关：' + '、'.join(business)
        traceable = True
        for entry in alert_to_seed_event(alert)['evidence']:
            try:
                original_deadline(entry, tool.config.settings.lookback_hours, profile.calendar_timezone)
            except (ValueError, TypeError, OverflowError):
                traceable = False
        alert['triggerEligible'] = bool(business and alert.get('subjectEntityId') and traceable
                                       and not alert.get('flags', {}).get('objectUnverified')
                                       and alert['score'] >= tool.config.settings.agent_trigger_score)
        if not traceable:
            alert['flags']['sourceUnverified'] = True
            alert['why'] += '；缺少可验证的原文链接或时间，不自动提交'
        alert['status'] = 'pending' if alert['triggerEligible'] else 'observed'
        previous = completed_investigation(alert['alertId'], list(evidence.values()), completed)
        if previous:
            alert['status'] = 'completed'
            alert['previousInvestigation'] = previous
            alert['why'] += '；此前已完成调查，仅复用历史结果，不重复提交'
        result.append(alert)
    return result


def news_signal_payload(profile, tool, alert):
    seed_data = alert_to_seed_event(alert)
    primary = alert['evidence'][0]
    seed_data['summary'] = primary.get('summary') or primary.get('title') or seed_data['title']
    seed = Event.model_validate(seed_data)
    subject = next((e for e in tool.config.entities if e.entity_id == alert.get('subjectEntityId')), None)
    signal_type = ('PUBLIC_OPINION' if 'PUBLIC_OPINION' in alert['eventTypes'] else
                   'SELF_EVENT' if subject and subject.type == 'self' else
                   'COMPETITOR_EVENT' if subject and subject.type == 'competitor' else 'INDUSTRY_EVENT')
    return {
        'schemaVersion': 'news_signal.v1', 'signalId': alert['alertId'], 'signalType': signal_type,
        'enterpriseId': profile.enterprise_id, 'enterpriseName': profile.enterprise_name,
        'industry': profile.industry, 'subjectEntityId': alert.get('subjectEntityId'),
        'subjectName': alert.get('subjectName'), 'title': seed.title, 'eventTypes': alert['eventTypes'],
        'score': alert['score'], 'priority': alert['priority'], 'triggerReason': alert['why'],
        'businessTerms': alert['businessTerms'], 'generatedAt': alert['generatedAt'],
        'publishedAt': seed.evidence[0].published_at.isoformat() if seed.evidence[0].published_at else None,
        'flags': alert['flags'], 'evidence': [e.model_dump(mode='json', by_alias=True) for e in seed.evidence],
        'seedEvent': seed.model_dump(mode='json', by_alias=True), 'triggerEligible': alert['triggerEligible'],
        'investigationStatus': 'completed' if alert.get('status') == 'completed' else
                               'pending' if alert['triggerEligible'] else 'observed',
        'investigationDirectory': (alert.get('previousInvestigation') or {}).get('investigationDirectory'),
        **({'previousSignalId': alert['previousInvestigation']['signalId']} if alert.get('previousInvestigation') else {}),
    }
