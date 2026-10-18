"""news_signal.v1 → 文档规定的四字段外层与 RumorSignal 载荷。"""
from datetime import date, datetime
from urllib.parse import urlsplit

from signals.common.schemas import SignalSubmission, validate_signal


def news_to_submission(news: dict, company_id: str) -> SignalSubmission:
    """公司 ID 由调用方显式提供；保留每条线索，不传新闻分数给 Agent。"""
    if news.get('schemaVersion') != 'news_signal.v1' or news.get('triggerEligible') is not True:
        raise ValueError('只转换已获调查资格的 news_signal.v1')
    clues = []
    for evidence in news.get('evidence', []):
        full = evidence.get('hasFullContent') is True and bool(str(evidence.get('content') or '').strip())
        content = (evidence.get('content') if full else None) or evidence.get('snippet') or evidence.get('content') or evidence.get('title') or ''
        if content and not full and not content.startswith('【摘要'):
            content = '【摘要，未取得完整正文】' + content
        publication = evidence.get('publication')
        if publication is not None:
            published = publication.get('value') if publication.get('status') == 'verified' else ''
        else:
            published = ''  # 搜索/旧记录中的时间没有原文依据，不作为报道时间。
        if isinstance(published, (date, datetime)):
            published = published.isoformat()
        source = evidence.get('sourceName') or urlsplit(evidence.get('url') or '').hostname or evidence.get('source')
        clues.append({
            'clue_id': evidence.get('evidenceId'),
            'source': source,
            'platform': evidence.get('source') or 'other',
            'title': evidence.get('title') or '', 'content': content,
            'url': evidence.get('url'), 'published_at': published or '',
            'author_type': evidence.get('authorType') or '未知',
        })
    reason = news.get('triggerReason') or ''
    summary = (news.get('seedEvent') or {}).get('summary') or news.get('title') or ''
    if not summary.startswith('【摘要') and not any(e.get('hasFullContent') is True and str(e.get('content') or '').strip() for e in news.get('evidence', [])):
        summary = '【摘要，未取得完整正文】' + summary
    payload = {
        'subject_name': news.get('subjectName'), 'subject_credit_code': news.get('subjectCreditCode') or '',
        'summary': summary,
        'source': clues[0]['source'] if clues else '',
        'signal_time': news.get('generatedAt'),
        'watch_focus': news.get('watchFocus') or (reason + '；待核实：原文真实性、核心事件事实及后续进展。' if reason else ''),
        'clues': clues,
    }
    # 公司 ID 来自共同企业标识，不推断 enterpriseId 与后端 company_id 相同。
    if isinstance(company_id, int) and not isinstance(company_id, bool):
        company_id = str(company_id)
    return validate_signal(SignalSubmission(signal_id=news.get('signalId'), company_id=company_id,
                                           kind='rumor', payload=payload))
