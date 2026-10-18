"""程序读取原文发布时间；不使用搜索相对时间、文章编号或模型猜测。"""
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque
from base64 import b64encode
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
import socket
from threading import Lock, Semaphore
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .schema import Evidence, Publication

_CST = timezone(timedelta(hours=8))
_CST_SITES = ('toutiao.com', 'infzm.com', 'nfzm.com', 'cctv.com', 'qq.com', 'sohu.com', 'ithome.com', 'cnmo.com', 'pconline.com.cn', 'zol.com.cn', 'sina.com.cn', 'pcpop.com', 'mydrivers.com', '163.com')
_META = {'article:published_time', 'og:published_time', 'datepublished', 'pubdate', 'publishdate', 'publish_time', 'pubtime', 'firstpublishedtime', 'dc.date.issued'}
_DATE = re.compile(r'(?<!\d)(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})(?:日)?(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(?:\s*(Z|[+-]\d{2}:?\d{2}))?)?(?!\d)')


def response_record(response):
    """按响应/页面声明解码；非 UTF-8 字节另存，校验不依赖再编码猜测。"""
    content = response.content
    header = response.headers.get('content-type', '')
    head = content[:16384].decode('ascii', errors='ignore')
    declared = [re.search(r'charset\s*=\s*[\"\x27]?\s*([\w.-]+)', value, re.I) for value in (header, head)]
    encodings = list(dict.fromkeys([match[1] for match in declared if match] + ['utf-8']))
    body, encoding, valid = response.text, response.encoding, False
    for choice in encodings:
        try:
            body, encoding = content.decode(choice), choice
            valid = True
            break
        except (UnicodeDecodeError, LookupError):
            continue
    row = {'url': str(response.url), 'httpStatus': response.status_code,
           'sha256': sha256(content).hexdigest(), 'body': body, 'encoding': encoding,
           'bodySha256': sha256(body.encode('utf-8')).hexdigest(), 'decodingValid': valid}
    if content != body.encode('utf-8'):
        row['rawBytesBase64'] = b64encode(content).decode('ascii')
    return row


def _parse_value(raw, url, locator, *, epoch=False):
    text = str(raw).strip()
    host = urlsplit(url).hostname or ''
    zone = _CST if any(host == domain or host.endswith('.' + domain) for domain in _CST_SITES) else None
    try:
        if epoch and re.fullmatch(r'\d{10}(?:\d{3})?', text):
            number = int(text) / (1000 if len(text) == 13 else 1)
            moment = datetime.fromtimestamp(number, timezone.utc).astimezone(zone or timezone.utc)
            return Publication(status='verified', value=moment.isoformat(), raw=text, precision='second', source_url=url, locator=locator)
        match = _DATE.search(text)
        if not match:
            # 英文日期同样只接受带完整年份的原文字段，不推断省略的年份。
            for fmt in ('%d %B %Y', '%B %d, %Y', '%b %d, %Y', '%d %b %Y'):
                try:
                    moment = datetime.strptime(text, fmt)
                    return Publication(status='verified', value=moment.date().isoformat(), raw=text,
                                       precision='day', source_url=url, locator=locator)
                except ValueError:
                    continue
            return None
        if len(_DATE.findall(text)) != 1:
            return None
        year, month, day, hour, minute, second, fraction, offset = match.groups()
        stamp = f'{int(year):04d}-{int(month):02d}-{int(day):02d}'
        if hour is None:
            datetime.fromisoformat(stamp)
            precision = 'day'
        else:
            stamp += f'T{int(hour):02d}:{minute}' + (f':{second}' if second else '')
            if fraction:
                stamp += '.' + fraction
            moment = datetime.fromisoformat(stamp + (offset or '').replace('Z', '+00:00'))
            if moment.tzinfo is None and zone:
                moment = moment.replace(tzinfo=zone)
            if moment.tzinfo is None:
                return None  # 不根据浏览器或运行机器时区估算未知网站的时刻。
            stamp = moment.isoformat(timespec='auto' if fraction else 'seconds' if second else 'minutes')
            precision = 'second' if second else 'minute'
        return Publication(status='verified', value=stamp, raw=text, precision=precision, source_url=url, locator=locator)
    except (ValueError, OverflowError, OSError):
        return None


def parse_publication(page: str, url: str) -> Publication:
    """只读文章元数据及标题附近的发布字段，不扫描正文和推荐文章日期。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, 'html.parser')
    candidates = []
    for meta in soup.select('meta[content]'):
        name = str(meta.get('property') or meta.get('name') or meta.get('itemprop') or '').casefold()
        if name in _META:
            candidates.append((meta['content'], f'meta[{name}]', False))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            nodes = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        nodes = nodes if isinstance(nodes, list) else [nodes]
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get('@graph'), list):
                nodes.extend(node['@graph'])
            if not isinstance(node, dict):
                continue
            types = node.get('@type', [])
            types = types if isinstance(types, list) else [types]
            if not any(t in ('NewsArticle', 'Article', 'BlogPosting', 'ReportageNewsArticle') for t in types):
                continue
            identity = node.get('url') or node.get('mainEntityOfPage')
            if isinstance(identity, dict):
                identity = identity.get('@id')
            if isinstance(identity, str):
                identity_parts, page_parts = urlsplit(urljoin(url, identity)), urlsplit(url)
                if ((identity_parts.hostname or '').removeprefix('www.') != (page_parts.hostname or '').removeprefix('www.')
                        or identity_parts.path.rstrip('/') != page_parts.path.rstrip('/')):
                    continue
            if node.get('datePublished'):
                candidates.append((node['datePublished'], 'jsonld.datePublished', False))
    for node in soup.select('article > header time, h1 ~ time, [itemprop="datePublished"], .article-meta .date, .article-meta .time, .article-meta .publish-time, .nfzm-content__publish[data-time], #pubtime_baidu, #pubDate, #news-time, .info > span, .info .time, .article-info .time, .article-info .date, .date-source, .time-source, .pub-time, .publish-time, .publish_time, .article-header .time, .news-info .date'):
        if node.find_parent(['aside', 'footer', 'nav']) or any(re.search(r'recommend|related|suggest', str(parent.get('class', [])), re.I) for parent in node.parents):
            continue
        text = node.get('datetime') or node.get('data-time') or node.get_text(' ', strip=True)
        # 修改时间、直播更新及页脚版权年份均不是首次发布时间。
        if not re.search(r'更新|修改|updated|modified', text, re.I):
            candidates.append((text, 'header.' + ((node.get('class') or ['time'])[0]), False))
    if (urlsplit(url).hostname or '').endswith('.cctv.com'):
        for node in soup.select('.info, .info1'):
            text = node.get_text(' ', strip=True)
            if not re.search(r'更新|修改', text):
                candidates.append((text, 'header.cctv.info', False))
    # 部分文章把发布时间放在原文自带的 JSON 状态中；不执行网页脚本。
    article_id = re.search(r'/(?:article/|a|contents/)?(\d{6,})(?:/|$)', urlsplit(url).path)
    def article_nodes(value, primary=False):
        if isinstance(value, dict):
            identity = value.get('group_id') or value.get('item_id') or value.get('id')
            if primary or (article_id and str(identity) == article_id[1]):
                for field in ('publish_time', 'publishTime', 'published_time', 'datePublished'):
                    if field in value:
                        candidates.append((value[field], 'articleState.' + field, True))
            for key, child in value.items():
                if not re.search('recommend|related|suggest|feed', key, re.I):
                    article_nodes(child, key in ('articleInfo', 'articleDetail'))
        elif isinstance(value, list):
            for child in value:
                article_nodes(child)
    for script in soup.select('script'):
        raw = script.string or script.get_text()
        if script.get('id') in ('RENDER_DATA', '__NEXT_DATA__'):
            try:
                article_nodes(json.loads(unquote(raw)))
            except (ValueError, TypeError, RecursionError):
                pass
        else:
            match = re.search(r'(?:window\.)?__INITIAL_STATE__\s*=\s*', raw)
            if match:
                try:
                    article_nodes(json.JSONDecoder().raw_decode(raw[match.end():])[0])
                except (ValueError, TypeError, RecursionError):
                    pass
    parsed = [_parse_value(value, url, locator, epoch=epoch) for value, locator, epoch in candidates]
    parsed = [value for value in parsed if value]
    if not parsed:
        return Publication(status='missing', source_url=url)
    days = {value.value for value in parsed if value.precision == 'day'}
    instants = {datetime.fromisoformat(value.value) for value in parsed if value.precision != 'day'}
    # 秒级时间与同一分钟的无秒显示相容；不同真实日期/时刻则不择一猜测。
    minute_instants = {moment.replace(second=0, microsecond=0) for moment in instants}
    seconds = {datetime.fromisoformat(value.value) for value in parsed if value.precision == 'second'}
    calendar_days = {value.value[:10] for value in parsed if value.precision != 'day'}
    if len(days) > 1 or (days and instants and not days.issubset(calendar_days)) or len(minute_instants) > 1 or len(seconds) > 1:
        return Publication(status='conflicting', source_url=url)
    return max(parsed, key=lambda value: {'day': 0, 'minute': 1, 'second': 2}[value.precision])


def _apply(evidence: Evidence, publication: Publication) -> Evidence:
    stamp = None
    calendar = None
    if publication.status == 'verified':
        calendar = datetime.fromisoformat(publication.value).date()
        if publication.precision != 'day':
            stamp = datetime.fromisoformat(publication.value)
            stamp = stamp.astimezone(timezone.utc) if stamp.tzinfo else None
        # 日期字段没有时刻，publishedAt 保持空；不补零点。
    warnings = [w for w in evidence.quality_warnings if not w.startswith('published_at_')]
    if publication.status != 'verified':
        warnings.append('published_at_original_missing')
    return evidence.model_copy(update={'publication': publication, 'published_at': stamp,
                                      'published_calendar_date': calendar, 'quality_warnings': warnings})


class ArticlePublicationReader:
    """限量、去重的原文读取器；原始页面和取值定位由程序追加保存。"""

    def __init__(self, *, max_requests=500, workers=4, http_get=None, validate_url=None):
        import httpx
        if not 1 <= max_requests <= 2500 or not 1 <= workers <= 8:
            raise ValueError('原文请求预算或并发数不合法')
        self.max_requests, self.workers = max_requests, workers
        self.client = httpx.Client(timeout=12, follow_redirects=False, trust_env=False) if http_get is None else None
        self.http_get = http_get or self.client.get
        self.validate_url = validate_url or self._public_url
        self.cache, self.blocked_hosts, self.request_count = {}, {}, 0
        self.forbidden_counts = defaultdict(int)
        self.host_limits = defaultdict(lambda: Semaphore(2))
        self.article_text = {}
        self._lock, self._lines = Lock(), {}

    @staticmethod
    def _public_url(url):
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                return False
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            return bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_global for row in addresses)
        except (ValueError, OSError):
            return False

    @staticmethod
    def _key(url):
        parts = urlsplit(url or '')
        # 仅去掉片段；不删除可能承载文章身份的查询参数。
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))

    def _fetch(self, url):
        row = {'requestedUrl': url, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'responses': []}
        target = url
        if urlsplit(url).scheme == 'http' and any((urlsplit(url).hostname or '').endswith('.' + site) or
                (urlsplit(url).hostname or '') == site for site in _CST_SITES):
            target = 'https:' + url[len('http:'):]
        for _ in range(4):
            if not self.validate_url(target):
                return row, Publication(status='invalid_url', source_url=url)
            host = urlsplit(target).hostname or ''
            if host in self.blocked_hosts:
                row['skippedReason'] = self.blocked_hosts[host]
                return row, Publication(status='blocked', source_url=url)
            try:
                with self.host_limits[host]:
                    with self._lock:
                        if host in self.blocked_hosts:
                            row['skippedReason'] = self.blocked_hosts[host]
                            return row, Publication(status='blocked', source_url=url)
                        if self.request_count >= self.max_requests:
                            return row, Publication(status='budget_exhausted', source_url=url)
                        self.request_count += 1
                    response = self.http_get(target, timeout=12)
                    if response.status_code in (401, 429):
                        self.blocked_hosts[host] = 'authentication_required' if response.status_code == 401 else 'rate_limited'
            except Exception as exc:
                row['errorType'] = type(exc).__name__
                return row, Publication(status='unavailable', source_url=url)
            recorded = response_record(response)
            body = recorded['body']
            row['responses'].append(recorded)
            if response.status_code in (401, 429):
                self.blocked_hosts[host] = 'authentication_required' if response.status_code == 401 else 'rate_limited'
                row['retryAfter'] = response.headers.get('retry-after')
                return row, Publication(status='blocked', source_url=url)
            if response.status_code == 403:
                # 单篇无权限不误伤整个网站；连续拒绝时仍停止，避免反复撞访问限制。
                with self._lock:
                    self.forbidden_counts[host] += 1
                    if self.forbidden_counts[host] >= 5:
                        self.blocked_hosts[host] = 'repeated_forbidden'
                return row, Publication(status='blocked', source_url=url)
            self.forbidden_counts[host] = 0
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get('location'):
                target = urljoin(target, response.headers['location'])
                if any(marker in urlsplit(target).path for marker in ('/antispider/', '/captcha/', '/passport/', '/login')):
                    self.blocked_hosts[host] = 'verification_or_login_redirect'
                    return row, Publication(status='blocked', source_url=url)
                continue
            if response.status_code != 200 or len(response.content) > 4_000_000:
                return row, Publication(status='unavailable', source_url=url)
            if not recorded['decodingValid']:
                return row, Publication(status='unavailable', source_url=url)
            # 不解验证码、不使用凭据；普通 HTML 读取不到就记录缺失。
            if re.search(r'<title>[^<]*(?:验证码|安全验证|登录|turnstile check|just a moment|checking your browser|attention required)', body, re.I) or ('_$jsvmprt' in body and not re.search(r'<(?:h1|article)\b', body, re.I)):
                self.blocked_hosts[host] = 'verification_page'
                return row, Publication(status='blocked', source_url=url)
            publication = parse_publication(body, str(response.url))
            publication.response_sha256 = row['responses'][-1]['sha256']
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, 'html.parser')
            article = soup.select_one('#paragraph.post_content, #artibody, #articleContent, .article-content, .article-content-wrap, .article_body, .article-body, .entry-content, [itemprop="articleBody"], article')
            if article is not None:
                for node in article.select('script, style, noscript, aside, nav, footer, header, .related, .recommend, .advertisement'):
                    node.decompose()
                paragraphs = [node.get_text(' ', strip=True) for node in article.select('p')]
                self.article_text[url] = '\n'.join(p for p in paragraphs if p) if paragraphs else article.get_text('\n', strip=True)
            return row, publication
        return row, Publication(status='unavailable', source_url=url)

    def read_evidence(self, evidence: list[Evidence], archive_path: Path) -> list[Evidence]:
        pending = list(dict.fromkeys(self._key(e.url) for e in evidence if self._key(e.url) not in self.cache))
        # 来源交替排队，预算不会被同一搜索平台的几百个链接先占光。
        hosts = defaultdict(deque)
        for url in pending:
            hosts[urlsplit(url).hostname].append(url)
        pending = []
        while any(hosts.values()):
            for urls in hosts.values():
                if urls:
                    pending.append(urls.popleft())
        archive_path = Path(archive_path)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=self.workers) as pool, archive_path.open('a', encoding='utf-8') as stream:
            line = self._lines.get(archive_path, 0)
            for url, (row, publication) in zip(pending, pool.map(self._fetch, pending)):
                line += 1
                publication.response_ref = str(archive_path.resolve()) + ':' + str(line)
                row['publication'] = publication.model_dump(mode='json', by_alias=True)
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                self.cache[url] = publication
            self._lines[archive_path] = line
        result = []
        for item in evidence:
            key = self._key(item.url)
            updated = _apply(item, self.cache[key])
            if updated.publication.status == 'verified' and updated.publication.source_url:
                updated = updated.model_copy(update={'url': updated.publication.source_url})
            if self.article_text.get(key):
                updated = updated.model_copy(update={'content': self.article_text[key], 'has_full_content': True,
                    'quality_warnings': [w for w in updated.quality_warnings if w not in ('search_result_summary_only', 'rss_summary_only', 'content_missing')]})
            result.append(updated)
        return result

    def read_items(self, items: list[dict], archive_path: Path) -> list[dict]:
        from .core.bridge import item_to_evidence
        evidence = self.read_evidence([Evidence.model_validate(item_to_evidence(item)) for item in items], archive_path)
        result = []
        for raw, item in zip(items, evidence):
            updated = deepcopy(raw)
            updated['publishedAt'] = item.published_at.isoformat() if item.published_at else None
            updated['url'] = item.url
            updated['publishedAtConfidence'] = 1.0 if item.published_at else 0.0
            updated['quality'] = {**updated.get('quality', {}), 'publishedAtConfidence': updated['publishedAtConfidence'],
                                  'warnings': item.quality_warnings, 'hasFullContent': item.has_full_content}
            if item.has_full_content:
                updated['content'] = item.content
            updated['ext'] = {**updated.get('ext', {}), 'requestedArticleUrl': raw.get('url'),
                              'publication': item.publication.model_dump(mode='json', by_alias=True)}
            result.append(updated)
        return result

    def summary(self):
        counts = {}
        for value in self.cache.values():
            counts[value.status] = counts.get(value.status, 0) + 1
        return {'httpRequests': self.request_count, 'uniqueUrls': len(self.cache), 'statuses': counts,
                'maxRequests': self.max_requests, 'stoppedHosts': dict(self.blocked_hosts)}

    def close(self):
        if self.client:
            self.client.close()
