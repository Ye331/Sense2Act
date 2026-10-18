"""乙方宝(yfbzb.com)适配器(首页区块 + zbzt 频道扫查;聚合源,非权威)。

通道背景(2026-10-04 实测):全站 SafeLine WAF,但按 TLS 指纹放行 Python
urllib(curl 稳定 468 挑战,urllib 稳定 200)——egress 即 urllib,天然过墙,
非对抗。搜索结果页为 AJAX,静态可达面为:首页四区块(招标/中标/采购意向,
各约 5 条,全国混排)与 /zbzt/{类型}/{区域}/ 频道页(按源站×区域聚合,
search-bottom-item 契约,约 30 条/页,含标题/摘要/类型/地域/日期)。
详情页采购人等深度字段在登录墙后("点击登录查看")——v1 不做详情补抓,
如实留空,不对抗(尊重其商业模式)。

认识论定位:乙方宝是商业聚合平台(千里马),内容转载自政采/公共资源等
官方渠道。谱系为独立根、可信度中、不进权威根(无单源例外资格);注意
与 ccgp(权威源)同时持有同一公告时会按独立源计——故不加入 R6 默认扇出,
仅供成果转储与显式 platforms 使用,防独立性虚高。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from lxml import etree, html

from ..base import AdapterResult
from .tender_meta import OPPORTUNITY_MAP, filter_by_query, parse_tender_meta

BASE_URL = "https://www.yfbzb.com"
_SOURCE = "www.yfbzb.com"
_CST = timezone(timedelta(hours=8))
_CHALLENGE_MARKERS = ("频繁访问", "访问异常", "安全验证")


def _looks_like_challenge(page: str) -> bool:
    """WAF 挑战判定:SafeLine 签名,或拦截词出现在标题/首屏区。

    不得整页宽扫:'验证码'字样在登录表单(placeholder)合法存在,
    整页匹配会把 598KB 正常首页误判成挑战页(2026-10-04 实测踩坑)。
    """
    if "SafeLineChallenge" in page:
        return True
    return any(m in page[:800] for m in _CHALLENGE_MARKERS)

# 首页区块 id -> 阶段(区块名即公告大类)
_SECTION_STAGE = {"invitedBidList": "招标采购", "winBidList": "中标成交",
                  "procurementIntention": "采购意向",
                  "proposeProjectList": "采购意向"}

_DATE_RE_STR = r"20\d{2}-\d{2}-\d{2}"
_DATE_FULL = re.compile(r"20\d{2}-\d{2}-\d{2}")


def _date_to_utc(raw: str) -> str:
    """'2026-10-04'(北京零点)-> UTC ISO;不可解析返回 ''。"""
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw.strip()).replace(tzinfo=_CST)
    except ValueError:
        return ""
    return moment.astimezone(timezone.utc).isoformat()


def _content_id(href: str) -> str | None:
    """URL 尾部 -> 内容 ID(取最长数字段:日期段 8 位,内容 ID 更长)。"""
    tail = href.rstrip("/").rsplit("/", 1)[-1]        # 20261004_636198004.html
    tail = tail.replace(".html", "")
    best = None
    for part in tail.split("_"):
        if part.isdigit() and len(part) >= 6 and (best is None or len(part) > len(best)):
            best = part
    return best


class YifangbaoAdapter:
    platform = "yifangbao"
    adapter_version = "0.1.0"

    def __init__(self, http_get, *, channels: tuple[str, ...] = ()):
        self._http_get = http_get
        self._channels = tuple(channels)

    # EvidenceItemLite 契约键(extra=forbid:rich 字段不得流入 S3 回执)
    _CONTRACT_KEYS = ("platform", "source", "url", "title", "content",
                      "published_at", "author_type", "dedup_key")

    def search(self, query: str, limit: int) -> AdapterResult:
        items, failure = self._fetch_page_items(BASE_URL, self._parse_home)
        if failure:
            return failure
        result = filter_by_query(items, query, limit, label="乙方宝首页",
                                 window_note="首页区块仅最新数条,频道扩窗见 sweep")
        if result.status == "success":
            result.items = [{k: i[k] for k in self._CONTRACT_KEYS}
                            for i in result.items]
        return result

    def harvest(self) -> AdapterResult:
        """全量清单 + 8 态回执(首页区块 + 注入的 zbzt 频道页,不过滤)——供常驻采集。

        单页失败降级(其余页照收);全部页面失败才如实回首个失败态,不伪装成 empty。
        """
        out: list[dict] = []
        seen: set[str] = set()
        failures: list[AdapterResult] = []
        pages = [(BASE_URL, self._parse_home)]
        pages += [(urljoin(BASE_URL, path), self._parse_channel) for path in self._channels]
        for url, parser in pages:
            items, failure = self._fetch_page_items(url, parser)
            if failure:
                failures.append(failure)
            for it in items:
                if it["dedup_key"] not in seen:
                    seen.add(it["dedup_key"])
                    out.append(it)
        if failures and len(failures) == len(pages):
            return failures[0]
        return AdapterResult(status="success" if out else "empty", items=out,
                             message=f"{len(failures)} 个页面失败已跳过" if failures else "")

    def sweep(self) -> list[dict]:
        """全量清单(首页区块 + 注入的 zbzt 频道页,不过滤)——供成果转储。"""
        return self.harvest().items

    # ------------------------------------------------------------------

    def _fetch_page_items(self, url, parser):
        try:
            resp = self._http_get(url)
        except TimeoutError:
            return [], AdapterResult(status="network_error", retryable=True,
                                     message="乙方宝请求超时")
        except Exception as exc:
            return [], AdapterResult(status="network_error", retryable=True,
                                     message=f"乙方宝请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return [], AdapterResult(status="rate_limited", retryable=True,
                                     message="乙方宝被限流")
        if code in (401, 403, 468):
            return [], AdapterResult(status="authentication_required",
                                     retryable=False,
                                     message="乙方宝拒绝匿名访问(WAF 挑战)")
        if code is not None and code >= 400:
            return [], AdapterResult(status="network_error", retryable=code >= 500,
                                     message=f"乙方宝返回 HTTP {code}")
        if _looks_like_challenge(resp.text):
            return [], AdapterResult(status="authentication_required",
                                     retryable=False,
                                     message="乙方宝返回 WAF 挑战页,不对抗")
        try:
            items = parser(resp.text, url)
        except (ValueError, etree.LxmlError):
            items = None
        if items is None:
            return [], AdapterResult(status="parser_changed", retryable=False,
                                     message="乙方宝页面结构缺失(疑似改版)")
        return items, None

    def _parse_home(self, page: str, page_url: str) -> list[dict] | None:
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            return None
        found_any = False
        items: list[dict] = []
        for ul in tree.xpath('//ul[contains(@class, "bids-list")]'):
            section_id = ul.get("id") or ""
            stage = _SECTION_STAGE.get(section_id, "")
            for a in ul.xpath('./li/a[contains(@class, "bid-title")]'):
                href = a.get("href") or ""
                # text_content():跨高亮 <font> 等内联标签不插空格(itertext 会)
                title = " ".join(a.text_content().split())
                cid = _content_id(href)
                if not href or not title or not cid:
                    continue
                found_any = True
                t = a.xpath('following-sibling::time[contains(@class, "bid-date")]/@datetime')
                meta = parse_tender_meta(title)
                stage_now = stage or meta["stage"]      # 区块名优先,标题分类兜底
                url = urljoin(page_url, href)
                items.append({
                    "platform": self.platform, "source": _SOURCE, "url": url,
                    "title": title,
                    "content": f"{title} | 阶段:{stage_now}",
                    "published_at": _date_to_utc(t[0] if t else ""),
                    "author_type": "聚合平台",
                    "dedup_key": f"yifangbao:{cid}",
                    "province": "",                      # 首页区块无地域字段
                    "stage": stage_now,
                    "opportunity": meta["opportunity"] if stage_now == meta["stage"]
                    else self._opp_of(stage_now),
                    "project_no": meta["project_no"], "buyer": meta["buyer"],
                })
        return items if found_any else None

    def _parse_channel(self, page: str, page_url: str) -> list[dict] | None:
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            return None
        lis = tree.xpath('//li[contains(@class, "search-bottom-item")]')
        if not lis:
            return None
        items: list[dict] = []
        for li in lis:
            anchors = li.xpath('.//a[contains(@class, "border-wrapper")][@href]')
            if not anchors:
                continue
            href = anchors[0].get("href") or ""
            cid = _content_id(href)
            h2 = li.xpath('.//h2')
            title = " ".join(h2[0].text_content().split()) if h2 else ""
            if not href or not title or not cid:
                continue
            spans = [" ".join(s.itertext()).strip()
                     for s in li.xpath('.//div[contains(@class, "time-type")]/span')]
            date = next((s for s in spans if _DATE_FULL.match(s)), "")
            # 契约顺序:类型/地域/日期 —— 末位为日期时取倒数第二位为地域
            region = spans[-2] if date and len(spans) >= 2 and spans[-1] == date else ""
            meta = parse_tender_meta(title)
            url = urljoin(page_url, href)
            content = f"{title} | 阶段:{meta['stage']}"
            if region:
                content += f" | 地区:{region}"
            items.append({
                "platform": self.platform, "source": _SOURCE, "url": url,
                "title": title, "content": content,
                "published_at": _date_to_utc(date),
                "author_type": "聚合平台",
                "dedup_key": f"yifangbao:{cid}",
                "province": region,
                "stage": meta["stage"], "opportunity": meta["opportunity"],
                "project_no": meta["project_no"], "buyer": meta["buyer"],
            })
        return items

    @staticmethod
    def _opp_of(stage: str) -> str:
        return OPPORTUNITY_MAP.get(stage, "非商机")
