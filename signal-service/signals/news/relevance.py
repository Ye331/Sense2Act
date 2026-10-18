"""标题、相邻段落中的业务关联；不把全文任意两处关键词硬凑成关联。"""
from html import unescape
import re

from .core.matching import match_subjects
from .core.normalize import normalize_text


def plain_text(value):
    value = str(value or '')
    if re.search(r'</?[a-zA-Z][^>]*>', value):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(value, 'html.parser')
        for node in soup.select('script, style, aside, nav, footer'):
            node.decompose()
        value = soup.get_text('\n', strip=True)
    return unescape(value)


def word_present(word, text):
    word = normalize_text(word)
    if re.fullmatch(r'[a-z ]+', word):
        return bool(re.search(r'(?<![a-z])' + re.escape(word) + r'(?:s)?(?![a-z])', text))
    return bool(word and word in text)


def article_contexts(item, *, window=400, lead_chars=600):
    """保留句号和段落边界；逗号不切断同一事件。"""
    title = normalize_text(plain_text(item.get('title')))
    body = plain_text(item.get('content') or item.get('summary'))
    # 标题引出正文的首段很常见；长篇正文不全部继承标题主体。
    paragraphs = [normalize_text(p) for p in re.split(r'[。！？!?；;\n\r]+', body) if p.strip()]
    contexts = [title] if title else []
    for index, paragraph in enumerate(paragraphs):
        if len(paragraph) <= window:
            contexts.append(paragraph)
        else:
            contexts.extend(paragraph[start:start + window] for start in range(0, len(paragraph), window // 2))
        if index + 1 < len(paragraphs) and len(paragraph) + len(paragraphs[index + 1]) <= window:
            contexts.append(paragraph + ' ' + paragraphs[index + 1])
    lead = ' '.join(paragraphs)[:lead_chars]
    if title and lead:
        contexts.extend(title + ' ' + lead[start:start + window]
                        for start in range(0, len(lead), window // 2))
    return list(dict.fromkeys(contexts))


def business_relation(item, profile, entities):
    """返回实际关联主体、业务词和能复查的原文片段。"""
    contexts = article_contexts(item, window=profile.relevance_window_chars,
                                lead_chars=profile.relevance_lead_chars)
    all_hits = match_subjects(contexts, entities)
    title = normalize_text(plain_text(item.get('title')))
    title_business = any(word_present(word, title) for word in profile.business_keywords)
    lead = normalize_text(plain_text(item.get('content') or item.get('summary')))[:profile.relevance_lead_chars]
    if (not title_business and (any(word_present(word, title) for word in profile.off_topic_title_words)
            or not any(word_present(word, lead) for word in profile.business_keywords))):
        return [], [], [], all_hits
    related, terms, anchors = {}, [], []
    for context in contexts:
        words = [word for word in profile.business_keywords if word_present(word, context)]
        if not words:
            continue
        hits = match_subjects([context], entities)
        for hit in hits:
            # 标题继承的主体不能借用另一个企业明确所属的业务段落。
            own_terms = [word for word in words if any(
                abs(context.find(normalize_text(word)) - context.find(normalize_text(surface))) <= profile.relevance_window_chars
                for surface, _ in hit.entity.surfaces() if normalize_text(surface) in context)]
            if not own_terms:
                continue
            for word in own_terms:
                position = context.find(normalize_text(word))
                local = context[max(0, position - 100):position + len(normalize_text(word)) + 50]
                local_hits = match_subjects([local], entities)
                if local_hits and hit.entity.entity_id not in {h.entity.entity_id for h in local_hits}:
                    continue
                related.setdefault(hit.entity.entity_id, hit)
                terms.append(word)
                anchors.append(context)
    return list(related.values()), list(dict.fromkeys(terms)), list(dict.fromkeys(anchors)), all_hits
