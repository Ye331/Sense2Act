"""python -m signals.news.cli：处理真实采集 JSON 或重试待提交信号。"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import urljoin

import httpx

from .service import NewsService
from .signal_detector import DEFAULT_CONFIG, NewsSignalDiscovery


def collect_live(args, plan, output):
    """复用现有爬虫搜索与 RSS 解析；这里只编排采集和保存响应。"""
    if args.crawler_root:
        root = Path(args.crawler_root).resolve()
        if not (root / 'crawler_tool/__init__.py').is_file():
            raise ValueError('crawler-root 应为包含 crawler_tool 包的目录')
        sys.path.insert(0, str(root))
    from crawler_tool.monitoring.agent_bridge import SearchContentCollectionTool
    from crawler_tool.sources.news_rss import NewsRssAdapter
    from .publication import ArticlePublicationReader, response_record

    collector = SearchContentCollectionTool(base_url=args.base_url, platforms=plan['platforms'],
        max_pages=args.max_pages, max_requests=args.max_source_requests,
        archive_path=output / 'search_responses.jsonl')
    reports = []
    for query in plan['queries']:
        result = collector.search_with_reports({'query': query, 'limit': args.limit})
        reports.append({'query': query, **{key: result[key] for key in ('status', 'sourceReports', 'requestCount')}})
        print(json.dumps({'progress': 'search', 'queriesCompleted': len(reports),
                          'records': len(collector.collected_items)}, ensure_ascii=False), flush=True)
    items = list(collector.collected_items.values())
    feed_reports, feed_requests = [], 0
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        for feed in plan.get('feeds', []):
            report = {'feedUrl': feed, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'responses': []}
            try:
                target = feed
                for _ in range(4):
                    if not ArticlePublicationReader._public_url(target):
                        raise ValueError('非公开订阅地址')
                    if collector.request_count + feed_requests >= args.max_source_requests:
                        raise RuntimeError('采集请求预算已用完')
                    feed_requests += 1
                    response = client.get(target)
                    report['responses'].append(response_record(response))
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get('location'):
                        target = urljoin(target, response.headers['location'])
                        continue
                    response.raise_for_status()
                    rows = NewsRssAdapter().parse_feed(response.content)
                    for row in rows:
                        raw = row.payload
                        items.append({'contentId': 'rss_' + raw['sourceItemId'], 'dedupKey': raw['url'],
                            'platform': 'other', 'title': raw['title'], 'summary': raw.get('summary'),
                            'content': None, 'url': raw['url'], 'publishedAt': raw.get('publishedAt'),
                            'quality': {'hasFullContent': False, 'warnings': ['rss_summary_only']},
                            'ext': {**raw.get('ext', {}), 'feedUrl': feed}})
                    report.update(status='success', count=len(rows))
                    break
                else:
                    raise RuntimeError('订阅重定向次数过多')
            except Exception as exc:
                report.update(status='failed', count=0, errorType=type(exc).__name__)
            with (output / 'feed_responses.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(report, ensure_ascii=False) + '\n')
            feed_reports.append({key: value for key, value in report.items() if key != 'responses'})
            print(json.dumps({'progress': 'feed', **feed_reports[-1]}, ensure_ascii=False), flush=True)
    urls = {row.get('canonicalUrl') or row.get('url') for row in items if row.get('url')}
    return items, {'queries': reports, 'feeds': feed_reports,
        'requestCount': collector.request_count + feed_requests, 'collectedItems': len(items),
        'uniqueUrls': len(urls), 'targetItems': plan.get('minItems', 1000),
        'targetUniqueUrls': plan.get('minUniqueUrls', 300),
        'targetReached': len(items) >= plan.get('minItems', 1000) and len(urls) >= plan.get('minUniqueUrls', 300)}


def main():
    parser = argparse.ArgumentParser(description='新闻信号检测与统一任务提交')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('detect', 'retry'):
        cmd = sub.add_parser(command)
        cmd.add_argument('--company-id', required=True, help='项目企业 ID，不使用 enterpriseId 替代')
        cmd.add_argument('--config')
        cmd.add_argument('--data-dir', help='原始输出与跨轮状态目录，默认 outputs/news')
        cmd.add_argument('--completed-signals', action='append', default=[], help='只读导入已完成调查的旧 signals.json')
        if command == 'detect':
            cmd.add_argument('--input', required=True, help='采集端 ContentItem / Evidence 数组 JSON')
            cmd.add_argument('--read-originals', action='store_true', help='补读缺少原文时间的公开报道')
    crawl = sub.add_parser('crawl', help='调用已有爬虫批量采集，逐篇读取原文时间；默认不提交调查')
    crawl.add_argument('--config', default=str(DEFAULT_CONFIG))
    crawl.add_argument('--data-dir', required=True, help='新的专属输出目录，不使用旧 runs 目录')
    crawl.add_argument('--crawler-root', help='未安装 crawler_tool 时指定现有爬虫包的目录')
    crawl.add_argument('--base-url', default='http://127.0.0.1:8301')
    crawl.add_argument('--limit', type=int, default=100)
    crawl.add_argument('--max-pages', type=int, default=3)
    crawl.add_argument('--max-source-requests', type=int, default=400)
    crawl.add_argument('--max-article-requests', type=int, default=1200)
    crawl.add_argument('--workers', type=int, default=6)
    crawl.add_argument('--completed-signals', action='append', default=[])
    crawl.add_argument('--company-id', help='后端实际企业编号，不从 enterpriseId 推断')
    crawl.add_argument('--submit', action='store_true', help='显式提交新信号；必须提供真实 company-id')
    args = parser.parse_args()
    reader = None
    try:
        if args.command == 'crawl':
            from .publication import ArticlePublicationReader
            if not 1 <= args.limit <= 100 or not 1 <= args.max_pages <= 10 or not 1 <= args.max_source_requests <= 500:
                raise ValueError('采集条数、分页或请求预算不合法')
            if args.submit and not args.company_id:
                raise ValueError('正式提交必须提供后端实际 company-id')
            if Path(args.data_dir).exists():
                raise ValueError('批量真实测试请使用新目录，旧原始输出不覆盖')
            config = Path(args.config)
            plan = json.loads(config.read_text(encoding='utf-8')).get('search', {})
            if not plan.get('queries') or not plan.get('platforms'):
                raise ValueError('真实采集需配置 search.queries 和 search.platforms')
            reader = ArticlePublicationReader(max_requests=args.max_article_requests, workers=args.workers)
            fetcher = lambda output, tick: collect_live(args, plan, output)
            if args.submit:
                result = NewsService(config, args.data_dir, publication_reader=reader,
                    completed_signals=args.completed_signals).detect(None, args.company_id,
                    fetcher=fetcher, collection_mode='live_sources').model_dump(mode='json')
            else:
                discovery = NewsSignalDiscovery(config, args.data_dir, publication_reader=reader,
                    completed_signals=args.completed_signals)
                result = discovery.run(fetcher=fetcher, collection_mode='live_sources')
                result = {key: value for key, value in result.items() if key not in ('monitor', 'collection')}
                result['publication'] = reader.summary()
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command == 'detect' and args.read_originals:
            from .publication import ArticlePublicationReader
            reader = ArticlePublicationReader()
        service = NewsService(args.config, args.data_dir, publication_reader=reader,
                              completed_signals=args.completed_signals)
        if args.command == 'detect':
            items = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
            result = service.detect(items, args.company_id, collection_mode='saved_news').model_dump(mode='json')
        else:
            result = [row.model_dump(mode='json') for row in service.retry_pending(args.company_id)]
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    finally:
        if reader is not None:
            reader.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
