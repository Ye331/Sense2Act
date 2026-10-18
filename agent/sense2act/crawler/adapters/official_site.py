"""通用企业官网适配器(official_website):档案驱动 + 通用启发式 + trafilatura 正文。

面向"不同用户企业各自官网"的通用型静态爬虫(偏静态网页;JS 渲染站=J5,
如实不可达走 browser_auto 通道,不进本适配器):
  1. 名称路由:查询词命中档案名/别名 -> 定位企业官网(查谁由配置决定);
  2. 列表发现:档案 news_paths 优先;缺省则启发式(根页找『新闻/公告/动态/
     资讯/通知』栏目链,同域,取前两个);
  3. 条目启发式:title 属性锚点,或『含数字的详情链 + 长文本』锚点;
  4. 详情补抓(上限 detail_cap):trafilatura 纯解析模式(egress 抓取,唯一
     出网点保持;纯函数=回放安全)——正文(截 1500 字)+ 元数据日期
     (htmldate 对中文站有效,2026-10-04 厦门大学附属第一医院实测)。

认识论:企业官网=当事方自述,谱系 company_web 根(official_sites.json 的
域名需同步 source_lineage.json),中档,不进权威根(无单源例外资格)。
查询词只剩企业名(无主题词)=『该官网最新公告』,按语义返回最新 N 条,
不算全量伪装(所问即所得);未命中任何档案=empty(附已配置名单)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import trafilatura
from lxml import etree, html

from ..base import AdapterResult
from .tender_meta import GENERIC_TOKENS

_CST = timezone(timedelta(hours=8))
_CHALLENGE_HEAD_MARKERS = ("频繁访问", "访问异常", "安全验证", "验证页")
_SECTION_WORDS = ("新闻", "公告", "动态", "资讯", "通知")
_CONTENT_CAP = 1500
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DETAILISH_RE = re.compile(r"info|article|news|detail|content|\d{4}", re.I)


def load_official_profiles(path: str | Path | None = None) -> dict:
    """读官网档案表(data/config/official_sites.json);_schema 元数据行剔除。"""
    p = Path(path) if path else Path(__file__).resolve().parents[3] / \
        "data" / "config" / "official_sites.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8-sig"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class OfficialSiteAdapter:
    platform = "official_website"
    adapter_version = "0.1.0"

    _CONTRACT_KEYS = ("platform", "source", "url", "title", "content",
                      "published_at", "author_type", "dedup_key")

    def __init__(self, http_get, profiles: dict, *, detail_cap: int = 3):
        self._http_get = http_get
        self._profiles = dict(profiles or {})
        self._detail_cap = max(0, detail_cap)

    # ------------------------------------------------------------------
    # 名称路由

    def _route(self, query: str) -> tuple[str | None, dict | None, list[str]]:
        """查询词 -> (档案名, 档案, 剩余主题词)。命中多个取名最长者(最具体)。"""
        tokens = [t for t in str(query or "").split() if t] or [str(query or "")]
        best_name, best_hits = None, []
        for name, profile in self._profiles.items():
            names = [name] + list(profile.get("aliases") or [])
            hits = [t for t in tokens
                    if any(n.casefold() in t.casefold() or
                           t.casefold() in n.casefold() for n in names if n)]
            if hits and (best_name is None or len(name) > len(best_name)):
                best_name, best_hits = name, hits
        topic = [t for t in tokens
                 if t not in best_hits and t not in GENERIC_TOKENS]
        return best_name, self._profiles.get(best_name), topic

    # ------------------------------------------------------------------
    # 对外契约

    def search(self, query: str, limit: int) -> AdapterResult:
        name, profile, topic = self._route(query)
        if not profile:
            configured = "、".join(self._profiles) or "(空)"
            return AdapterResult(
                status="empty",
                message=f"查询未命中已配置的企业官网档案(已配置:{configured});"
                        "请在 data/config/official_sites.json 添加该企业")
        items, failure = self._collect(profile)
        if failure:
            return failure
        if not items:
            return AdapterResult(
                status="empty",
                message=f"{name}官网未识别出新闻/公告条目"
                        "(启发式未命中,建议在档案中补 news_paths/item_xpath)")
        if topic:                      # 有主题词:标题过滤,命中词数优先
            def hits(item):
                t = item["title"].casefold()
                return sum(1 for w in topic if w.casefold() in t)
            matched = [(hits(i), i) for i in items if hits(i) > 0]
            if not matched:
                return AdapterResult(
                    status="empty",
                    message=f"扫查{name}官网 {len(items)} 条,无主题词命中条目")
            matched.sort(key=lambda p: -p[0])
            items = [i for _, i in matched]
        # 无主题词 = 『该官网最新公告』:按页面序取前 N(所问即所得)
        result_items = items[:limit]
        if self._detail_cap:
            for item in result_items[: self._detail_cap]:
                self._enrich_with_detail(item)
        return AdapterResult(status="success",
                             items=[{k: i[k] for k in self._CONTRACT_KEYS}
                                    for i in result_items])

    def sweep(self) -> list[dict]:
        """全档案扫描(不过滤,rich 字段)——供成果转储。"""
        out: list[dict] = []
        for name, profile in self._profiles.items():
            items, _ = self._collect(profile)
            for it in items:
                it["company"] = name
                out.append(it)
        return out

    # ------------------------------------------------------------------
    # 列表发现与条目启发式

    def _collect(self, profile: dict) -> tuple[list[dict], AdapterResult | None]:
        domain = (profile.get("domains") or [""])[0]
        base = f"https://{domain}"
        paths = list(profile.get("news_paths") or [])
        if not paths:                  # 启发式:根页找新闻类栏目
            paths, failure = self._discover_sections(base)
            if failure:
                return [], failure
        items: list[dict] = []
        seen: set[str] = set()
        failure: AdapterResult | None = None
        for path in paths[:4]:         # 栏目上限 4(礼貌)
            url = urljoin(base + "/", path)
            page_items = self._parse_list_page(url, domain)
            if isinstance(page_items, AdapterResult):
                failure = failure or page_items   # 保留首个失败态(如 403)
                continue
            for it in page_items:
                if it["dedup_key"] not in seen:
                    seen.add(it["dedup_key"])
                    items.append(it)
        if not items and failure is not None:
            return [], failure          # 全栏目失败:如实回传具体失败态
        return items, None

    def _discover_sections(self, base: str) -> tuple[list[str], AdapterResult | None]:
        page = self._get(base)
        if isinstance(page, AdapterResult):
            return [], page
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            return [], None
        found = []
        for a in tree.xpath('//a[@href]'):
            text = " ".join(a.text_content().split())
            if not any(w in text for w in _SECTION_WORDS) or len(text) > 20:
                continue
            url = urljoin(base + "/", a.get("href") or "")
            if urlparse(url).netloc != urlparse(base).netloc:
                continue
            path = urlparse(url).path
            if path and path not in found:
                found.append(path)
        return found[:2], None

    def _parse_list_page(self, url: str, domain: str) -> list[dict] | None \
            | AdapterResult:
        page = self._get(url)
        if isinstance(page, AdapterResult):
            return page                # 页面级失败态(403/限流/网络)原样上抛
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            return None
        items: list[dict] = []
        seen: set[str] = set()
        for a in tree.xpath('//a[@href]'):
            href = a.get("href") or ""
            title_attr = " ".join((a.get("title") or "").split())
            text = " ".join(a.text_content().split())
            title = title_attr or (text if len(text) >= 8 else "")
            if not title:
                continue
            absu = urljoin(url, href)
            if urlparse(absu).netloc != urlparse(url).netloc:
                continue
            if not _DETAILISH_RE.search(urlparse(absu).path):
                continue
            # 去重键取路径最后一个数字段(末段=条目ID;首段常是栏目号,
            # 同栏目条目会坍缩——乙方宝 content-id 同类坑)
            nums = re.findall(r"\d{4,}", urlparse(absu).path)
            key = f"official:{domain}:{nums[-1] if nums else absu[-40:]}"
            if key in seen:
                continue
            seen.add(key)
            date_m = _DATE_RE.search(
                " ".join(a.getparent().itertext()) if a.getparent() is not None
                else "")
            items.append({
                "platform": self.platform, "source": domain, "url": absu,
                "title": title, "content": title,
                "published_at": self._date_utc(date_m.group(0)) if date_m else "",
                "author_type": "当事方", "dedup_key": key,
                "company": "", "province": "",
            })
        return items

    # ------------------------------------------------------------------
    # 详情补抓(trafilatura 纯解析;失败保持标题级,不伪装)

    def _enrich_with_detail(self, item: dict) -> None:
        page = self._get(item["url"])
        if isinstance(page, AdapterResult):
            return
        try:
            meta = trafilatura.extract_metadata(page)
        except Exception:
            meta = None
        if meta is not None and meta.date and not item["published_at"]:
            iso = self._date_utc(str(meta.date)[:10])
            if iso:
                item["published_at"] = iso
        try:
            text = trafilatura.extract(page) or ""
        except Exception:
            text = ""
        text = " ".join(text.split())
        if len(text) > 20:             # 过短正文视同失败,保持标题级
            item["content"] = text[:_CONTENT_CAP] + (
                "…" if len(text) > _CONTENT_CAP else "")

    # ------------------------------------------------------------------
    # 基础件

    def _get(self, url: str) -> str | AdapterResult:
        try:
            resp = self._http_get(url)
        except TimeoutError:
            return AdapterResult(status="network_error", retryable=True,
                                 message="官网请求超时")
        except Exception as exc:
            return AdapterResult(status="network_error", retryable=True,
                                 message=f"官网请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return AdapterResult(status="rate_limited", retryable=True,
                                 message="官网被限流")
        if code in (401, 403):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="官网拒绝匿名访问")
        if code is not None and code >= 400:
            return AdapterResult(status="network_error", retryable=code >= 500,
                                 message=f"官网返回 HTTP {code}")
        if any(m in resp.text[:800] for m in _CHALLENGE_HEAD_MARKERS):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="官网返回验证/拦截页,不对抗")
        return resp.text

    @staticmethod
    def _date_utc(day: str) -> str:
        try:
            moment = datetime.fromisoformat(day.strip()).replace(
                hour=12, tzinfo=_CST)   # 无时分,取正午避免跨日歧义
        except ValueError:
            return ""
        return moment.astimezone(timezone.utc).isoformat()
