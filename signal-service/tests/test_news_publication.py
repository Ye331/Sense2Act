"""原文时间解析边界；页面结构片段只供单元测试，不进入真实采集。"""
from copy import deepcopy
from base64 import b64decode
from hashlib import sha256
import json

import httpx
import pytest

from signals.news.publication import ArticlePublicationReader, parse_publication, response_record
from signals.news.schema import Evidence


def evidence(url='https://www.ithome.com/1/012/006.htm', **extra):
    return Evidence(evidence_id=sha256(url.encode()).hexdigest(), title='原文时间解析回归',
                    source='other', url=url, **extra)


def reader(get, **extra):
    return ArticlePublicationReader(http_get=get, validate_url=lambda u: u.startswith('https://'), **extra)


def response(url, body='', status=200, **headers):
    return httpx.Response(status, content=body.encode(), request=httpx.Request('GET', url), headers=headers)


@pytest.mark.parametrize('page,expected,precision', [
    ('<meta property="article:published_time" content="2026-10-12T17:26:08+08:00">', '2026-10-12T17:26:08+08:00', 'second'),
    ('<div class="date-source">2026-10-12 17:26</div>', '2026-10-12T17:26+08:00', 'minute'),
    ('<span id="pubDate">2026年10月12日</span>', '2026-10-12', 'day'),
    ('<meta itemprop="datePublished" content="12 October 2026">', '2026-10-12', 'day'),
    ('<span class="nfzm-content__publish" data-time="2026-10-12 17:26:08">10-12</span>', '2026-10-12T17:26:08+08:00', 'second'),
    ('<meta name="pubdate" content="2026-10-12T09:26:08.123Z">', '2026-10-12T09:26:08.123000+00:00', 'second'),
])
def test_original_fields_keep_native_precision(page, expected, precision):
    value = parse_publication(page, 'https://www.ithome.com/1/012/006.htm')
    assert (value.status, value.value, value.precision) == ('verified', expected, precision)
    assert value.raw and value.locator


@pytest.mark.parametrize('page', [
    '<meta name="pubdate" content="昨天">',
    '<meta name="pubdate" content="10-12 17:26">',
    '<meta property="article:modified_time" content="2026-10-12T17:26:08+08:00">',
    '<article>2026年10月12日发生</article><aside><time>2026-10-13</time></aside>',
    '<footer><div class="date-source">2026-10-12</div></footer>',
])
def test_relative_body_modified_and_footer_times_are_not_reporting_time(page):
    value = parse_publication(page, 'https://www.ithome.com/1/012/006.htm')
    assert value.status == 'missing' and value.value is None


def test_same_instant_across_timezones_is_not_a_false_conflict():
    page = '<meta name="pubdate" content="2026-10-12T16:30:00Z"><div class="date-source">2026-10-13 00:30:00</div>'
    assert parse_publication(page, 'https://www.ithome.com/1/012/006.htm').status == 'verified'
    page = '<meta name="pubdate" content="2026-10-12"><div class="date-source">2026-10-13 00:30:00</div>'
    assert parse_publication(page, 'https://www.ithome.com/1/012/006.htm').status == 'conflicting'


def test_unknown_site_timezone_is_not_guessed():
    value = parse_publication('<meta name="pubdate" content="2026-10-12 17:26">', 'https://example.org/article/1')
    assert value.status == 'missing'


def test_jsonld_cross_article_and_modified_fields_are_ignored():
    nodes = [
        {'@type': 'NewsArticle', 'url': 'https://foreign.org/article/1', 'datePublished': '2025-01-01'},
        {'@type': 'NewsArticle', 'url': 'https://example.org/other', 'datePublished': '2025-01-01'},
        {'@type': 'NewsArticle', 'url': 'https://example.org/article/1', 'datePublished': '2026-10-12', 'dateModified': '2026-10-13'},
    ]
    value = parse_publication('<script type="application/ld+json">' + json.dumps(nodes) + '</script>', 'https://example.org/article/1')
    assert value.value == '2026-10-12'


def test_one_forbidden_article_does_not_disable_other_articles(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, '<meta name="pubdate" content="2026-10-12">', status=403 if url.endswith('/1') else 200)
    values = reader(get, workers=1).read_evidence([evidence('https://example.org/1'), evidence('https://example.org/2')], tmp_path / 'raw.jsonl')
    assert len(calls) == 2 and values[0].publication.status == 'blocked'
    assert values[1].publication.status == 'verified'


def test_rate_limit_stops_without_retry_or_bypass(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, status=429, **{'retry-after': '60'})
    values = reader(get, workers=1).read_evidence([evidence('https://example.org/1'), evidence('https://example.org/2')], tmp_path / 'raw.jsonl')
    assert len(calls) == 1 and all(value.publication.status == 'blocked' for value in values)


def test_source_budget_is_fair_and_cannot_invent_time(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, '<meta name="pubdate" content="2026-10-12">')
    sources = [evidence(f'https://a.example.org/{i}') for i in range(6)] + [evidence('https://b.example.org/1')]
    check = reader(get, workers=1, max_requests=2)
    values = check.read_evidence(sources, tmp_path / 'raw.jsonl')
    assert calls == ['https://a.example.org/0', 'https://b.example.org/1']
    assert values[-1].publication.status == 'verified'
    assert sum(v.publication.status == 'budget_exhausted' for v in values) == 5
    assert all(v.published_at is None for v in values)  # 原文只有日期，不能补零点。


def test_redirect_updates_original_url_and_keeps_input_and_archive(tmp_path):
    source = {'contentId': 'original', 'title': '原文时间解析回归', 'platform': 'other',
              'url': 'https://www.ithome.com/short', 'publishedAt': '2030-01-01T00:00:00Z',
              'quality': {'hasFullContent': False, 'warnings': ['published_at_relative']}}
    before = deepcopy(source)
    page = '<meta name="pubdate" content="2026-10-12 17:26:08"><div id="paragraph" class="post_content"><p>原文段落</p></div><aside>推荐内容</aside>'
    def get(url, **kwargs):
        return response(url, status=302, location='/1/012/006.htm') if url.endswith('/short') else response(url, page)
    archive = tmp_path / 'raw.jsonl'
    check = reader(get, workers=1)
    result = check.read_items([source], archive)[0]
    assert source == before
    assert result['url'] == result['ext']['publication']['sourceUrl'] == 'https://www.ithome.com/1/012/006.htm'
    assert result['content'] == '原文段落' and result['quality']['hasFullContent']
    assert result['ext']['requestedArticleUrl'] == before['url']
    row = json.loads(archive.read_text())
    assert len(row['responses']) == 2 and row['responses'][-1]['body'] == page
    assert result['ext']['publication']['responseSha256'] == sha256(page.encode()).hexdigest()
    assert check.read_items([source], archive)[0] == result
    assert check.request_count == 2


def test_failed_original_read_clears_search_time(tmp_path):
    original = evidence(published_at='2030-01-01T00:00:00Z')
    result = reader(lambda url, **kw: response(url, '<article>没有发布字段</article>')).read_evidence([original], tmp_path / 'raw.jsonl')[0]
    assert result.published_at is None and result.publication.status == 'missing'
    assert original.published_at is not None


def test_cctv_native_header_and_turnstile_are_handled_exactly(tmp_path):
    page = '<div class="info">来源：央视网 | 2026年10月14日 13:21:02</div>'
    publication = parse_publication(page, 'https://news.cctv.com/article/1.shtml')
    assert publication.value == '2026-10-14T13:21:02+08:00'
    assert publication.raw == '来源：央视网 | 2026年10月14日 13:21:02'
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, '<title>GSMArena Turnstile check</title>')
    values = reader(get, workers=1).read_evidence([evidence('https://example.org/1'), evidence('https://example.org/2')], tmp_path / 'raw.jsonl')
    assert len(calls) == 1 and all(v.publication.status == 'blocked' for v in values)


def test_declared_gbk_preserves_original_bytes_and_readable_time(tmp_path):
    page = '<meta charset="gbk"><span id="pubDate" class="">2026年10月14日 13:21:02</span><div id="paragraph" class="post_content"><p>原文编码回归</p></div>'
    raw = page.encode('gbk')
    def get(url, **kwargs):
        return httpx.Response(200, content=raw, request=httpx.Request('GET', url))
    archive = tmp_path / 'raw.jsonl'
    value = reader(get).read_evidence([evidence()], archive)[0]
    assert value.publication.value == '2026-10-14T13:21:02+08:00'
    assert value.content == '原文编码回归'
    recorded = json.loads(archive.read_text())['responses'][0]
    assert recorded['encoding'] == 'gbk' and recorded['decodingValid']
    assert recorded['body'] == page and b64decode(recorded['rawBytesBase64']) == raw
    assert sha256(raw).hexdigest() == recorded['sha256'] == value.publication.response_sha256


def test_undeclared_invalid_encoding_does_not_produce_guessed_time(tmp_path):
    raw = '<meta name="pubdate" content="2026-10-12 17:26">无编码声明'.encode('gbk')
    def get(url, **kwargs):
        return httpx.Response(200, content=raw, request=httpx.Request('GET', url))
    value = reader(get).read_evidence([evidence()], tmp_path / 'raw.jsonl')[0]
    assert value.publication.status == 'unavailable' and value.published_at is None
