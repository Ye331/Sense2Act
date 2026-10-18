"""中国政府采购网(ccgp)公开招标公告适配器(静态列表通道 + 限额详情补抓)。

选路背景(2026-10-03 实测):search.ccgp.gov.cn 检索端点对当前出口 IP 稳定
软封("频繁访问"页),www 首页 403;但 /cggg/{dfgg,zygg}/gkzb/index.htm
静态公告列表(地方/中央公开招标公告,各 20 条/页)匿名可达(200,UTF-8,
SSR)——本适配器走该静态通道。列表条目自带 完整标题(title 属性)/发布时间
/地域/采购人;预算金额与投标(开标)截止时间在详情页,对排名前 detail_cap
条补抓(礼貌上限,补抓失败不伪装,条目保持标题级)。

详情字段解析移植自用户提供的 Downloads/ccgp_spider.py(requests+bs4 版),
改为 lxml+注入 http_get(零新增依赖),正则经 2026-10-03 真实详情页校验:
预算金额"￥203.000000万元(人民币)"/开标时间"2026年10月28日 08:35"/
项目编号"采购计划-[2026]-05669号-J2026ZCY283"。

v1 边界(如实):仅各栏目最新一页(共约 40 条/次,翻页属后续);列表级即
全国覆盖(权威根 ccgp);详情补抓上限 detail_cap=3(默认),超出部分无金额。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import trafilatura
from lxml import etree, html

from ..base import AdapterResult
from .tender_meta import filter_by_query, parse_tender_meta

LIST_URLS = {
    "local": "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index.htm",
    "central": "https://www.ccgp.gov.cn/cggg/zygg/gkzb/index.htm",
}
_SOURCE = "www.ccgp.gov.cn"
_CST = timezone(timedelta(hours=8))
_CHALLENGE_MARKERS = ("验证码", "安全验证", "频繁访问", "登录后", "访问异常")
_ID_RE = re.compile(r"t\d{8}_(\d+)")
_LIST_TIME_RE = re.compile(r"发布时间[:：]\s*(\d{4}-\d{2}-\d{2}(?:\s*\d{1,2}:\d{2})?)")
_REGION_RE = re.compile(r"地域[:：]\s*([^\s<]+)")
_BUYER_RE = re.compile(r"采购人[:：]\s*([^\s<]+)")

# 详情页字段(移植自 ccgp_spider.py,经真实页面校验)
_BUDGET_RE = re.compile(
    r"预算金额[：:\s]*[￥¥]?\s*([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(亿元|万元|万|元)")
_DEADLINE_RES = (
    re.compile(r"投标截止时间[：:\s]+(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2})"),
    re.compile(r"提交投标文件截止时间[：:\s]+(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2})"),
    re.compile(r"开标时间[：:\s]+(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2})"),
)
_CN_DT_RE = re.compile(
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})")
_DETAIL_NO_RE = re.compile(r"项目编号[：:\s]+([^\s]+)")
_AGENCY_RE = re.compile(r"代理机构名称[：:\s]+(.{2,60}?)(?=\s*代理机构地址)")
_CONTACT_RE = re.compile(r"项目联系人[：:\s]+([^\s]{2,30})")
_PHONE_RE = re.compile(r"项目联系电话[：:\s]+([0-9\-\(\)（）+、]{6,40})")
_SIGNUP_RE = re.compile(r"获取招标文件时间[：:\s]*([^。;；]*?)(?=\s*开标|$)")
_CN_DATE_RE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")


def _page_text(page: str) -> str:
    """HTML -> 规整纯文本(详情字段提取的底料)。"""
    try:
        tree = html.fromstring(page)
    except (ValueError, etree.LxmlError):
        return ""
    for bad in tree.xpath("//script|//style|//noscript"):
        bad.getparent().remove(bad)
    return " ".join(" ".join(tree.itertext()).split())


def _detail_title(page: str) -> str:
    """详情页标题(h2 优先,<title> 兜底,剥站点后缀);缺失返回空串。"""
    try:
        tree = html.fromstring(page)
    except (ValueError, etree.LxmlError):
        return ""
    for xp in ("//h2", "//title"):
        nodes = tree.xpath(xp)
        if nodes:
            title = " ".join(" ".join(nodes[0].itertext()).split())
            title = re.sub(r"_?中国政府采购网$", "", title).strip()
            if title:
                return title
    return ""


def parse_detail_body(page: str) -> str | None:
    """只从公告正文容器取文本，保留段落；容器缺失时交由字段校验后的通用抽取。"""
    try:
        tree = html.fromstring(page)
    except (ValueError, etree.LxmlError):
        return None
    nodes = tree.xpath('//*[@id="noticeArea"]')
    if not nodes:
        nodes = tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" vF_detail_content ")]')
    if not nodes:
        return None
    node = nodes[0]
    for bad in node.xpath(
            './/script|.//style|.//noscript|.//nav|.//header|.//footer|.//aside|'
            './/*[contains(concat(" ", normalize-space(@class), " "), " related ")]'):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    for block in node.xpath('.//p|.//div|.//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//li|.//br'):
        block.text = "\n" + (block.text or "")
    return "\n".join(
        line for raw in "".join(node.itertext()).splitlines()
        if (line := " ".join(raw.split()))
    )


def _parse_list_time(raw: str) -> str:
    """'2026-10-04 14:23'(北京)-> UTC ISO;不可解析返回 ''。"""
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw.strip()).replace(tzinfo=_CST)
    except ValueError:
        return ""
    return moment.astimezone(timezone.utc).isoformat()


def money_to_yuan(text: str | None) -> float | None:
    """'￥203.000000万元' -> 2030000.0(元);不可解析返回 None(不猜)。"""
    if not text:
        return None
    s = str(text).replace(",", "").replace("，", "").replace(" ", "")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|万元|万|元|人民币)", s)
    if not m:
        bare = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
        return float(bare.group(1)) if bare else None
    value = float(m.group(1))
    unit = m.group(2)
    if unit in ("万元", "万"):
        value *= 10000
    elif unit in ("亿元", "亿"):
        value *= 100000000
    return value


def _cn_datetime(raw: str) -> str:
    """'2026年10月28日 08:35' -> '2026-10-28 08:35'(北京,保留字符串)。"""
    m = _CN_DT_RE.search(raw or "")
    if not m:
        return ""
    y, mo, d, hh, mm = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"


def parse_detail_fields(page: str) -> dict:
    """详情页 -> {预算元, 投标截止, 项目编号, 代理机构, 联系人, 电话}。

    纯确定性正则;未命中字段为空值,不猜(移植自 ccgp_spider 并适配)。
    """
    text = _page_text(page)
    budget = None
    m = _BUDGET_RE.search(text)
    if m:
        budget = money_to_yuan(f"{m.group(1)}{m.group(2)}")
    deadline = ""
    for pattern in _DEADLINE_RES:
        m = pattern.search(text)
        if m:
            deadline = _cn_datetime(m.group(1))
            if deadline:
                break
    m = _DETAIL_NO_RE.search(text)
    project_no = m.group(1).rstrip(".。,;") if m else ""
    m = _AGENCY_RE.search(text)
    agency = m.group(1).strip() if m else ""
    m = _CONTACT_RE.search(text)
    contact = m.group(1).strip() if m else ""
    m = _PHONE_RE.search(text)
    phone = m.group(1).strip() if m else ""
    signup = ""
    m = _SIGNUP_RE.search(text)
    if m:
        dates = _CN_DATE_RE.findall(m.group(1))
        if dates:
            y, mo, d = (int(g) for g in dates[-1])   # 区间末端=获取截止
            signup = f"{y:04d}-{mo:02d}-{d:02d}"
    return {"budget_yuan": budget, "deadline": deadline,
            "project_no": project_no, "agency": agency,
            "contact": contact, "phone": phone,
            "signup_deadline": signup}


class CcgpAdapter:
    platform = "ccgp"
    adapter_version = "0.1.0"

    def __init__(self, http_get, *, detail_cap: int = 3, max_pages: int = 3):
        self._http_get = http_get
        self._detail_cap = max(0, detail_cap)
        self._max_pages = max(1, max_pages)   # 翻页回溯上限(静态通道极限约 22 页≈2 天)

    # ------------------------------------------------------------------

    # EvidenceItemLite 契约键(extra=forbid:转储侧 rich 字段不得流入 S3 回执)
    _CONTRACT_KEYS = ("platform", "source", "url", "title", "content",
                      "published_at", "author_type", "dedup_key")

    def search(self, query: str, limit: int, buyer: str = "", project_no: str = "",
               published_date: str = "", source_url: str = "") -> AdapterResult:
        """定向检索:有采购人/项目编号(信封精确字段)时走精确匹配,替代宽词
        过滤(宽词会把无关公告挤进 top-k);否则退回通用词面过滤。

        source_url(信封自带目标公告 URL,2026-10-16)优先 first-hop 直抓:
        列表盲扫够不着的大龄公告由它负责(_sweep_lists 文档分工注记的兑现);
        非本站 URL 或直抓失败回退列表扫查(egress 已录直抓请求,不伪装)。
        """
        if source_url:
            direct = self._fetch_source_url(source_url)
            if direct is not None:
                return direct
        items, failure = self._sweep_lists(target_date=published_date)
        if failure:
            return failure
        if buyer or project_no:
            result = self._precise_match(items, buyer, project_no, limit)
        else:
            result = filter_by_query(
                items, query, limit, label="公开招标列表",
                window_note="仅各栏目最新页(地方+中央公开招标)")
        if result.status == "success":
            for item in result.items[: self._detail_cap]:
                self._enrich_with_detail(item)
            result.items = [{k: item[k] for k in self._CONTRACT_KEYS}
                            for item in result.items]
        return result

    def sweep(self) -> list[dict]:
        """全量清单(不过滤,含地域/采购人/阶段)——供成果转储,不进 S3 回执。"""
        items, _ = self._sweep_lists()
        return items

    # ------------------------------------------------------------------

    def _sweep_lists(self, target_date: str = "") -> tuple[list[dict], AdapterResult | None]:
        """双栏目列表扫查(翻页回溯);单栏目/单页失败降级,双失败如实回失败态。

        翻页 index.htm → index_N.htm,按时间倒序回溯;target_date 提供时,
        翻到覆盖目标日期即提前停(静态通道极限约 22 页≈2 天,更早滚出的公告
        翻页救不回——那是 source_url 直抓的职责,本适配器不追)。
        """
        items: list[dict] = []
        http_fail = parse_fail = 0
        seen: set[str] = set()
        for base in LIST_URLS.values():
            for page in range(1, self._max_pages + 1):
                url = base if page == 1 else base.replace(
                    "index.htm", f"index_{page}.htm")
                try:
                    resp = self._http_get(url)
                except TimeoutError:
                    http_fail += 1
                    break
                except Exception:
                    http_fail += 1
                    break
                code = resp.status_code
                if code == 429:
                    return [], AdapterResult(status="rate_limited", retryable=True,
                                             message="ccgp 列表被限流,礼貌即停")
                if code in (401, 403):
                    return [], AdapterResult(status="authentication_required",
                                             retryable=False,
                                             message="ccgp 拒绝匿名列表请求")
                if code is not None and code >= 500:
                    http_fail += 1      # 网络错误(502/503)
                    break
                if code is not None and code >= 400:
                    break               # 404 翻页到底,正常停止(回退已收集)
                if any(marker in resp.text for marker in _CHALLENGE_MARKERS):
                    return [], AdapterResult(status="authentication_required",
                                             retryable=False,
                                             message="ccgp 返回拦截页(频繁访问),不对抗")
                page_items = self._parse_list(resp.text, url)
                if page_items is None:
                    parse_fail += 1
                    break               # 单页改版不拖垮整体
                oldest = ""
                for it in page_items:
                    if it["dedup_key"] not in seen:
                        seen.add(it["dedup_key"])
                        items.append(it)
                    oldest = it.get("published_at", "") or oldest
                # 已覆盖 target_date(本页最老日期 <= 目标日期)则提前停
                if target_date and oldest and oldest[:10] <= target_date:
                    break
        if not items:
            if parse_fail and parse_fail >= http_fail:
                return [], AdapterResult(status="parser_changed", retryable=False,
                                         message="ccgp 列表容器均缺失(疑似改版)")
            if http_fail:
                return [], AdapterResult(
                    status="network_error", retryable=True,
                    message=f"ccgp 全部 {http_fail} 个栏目请求失败")
            return [], None              # 列表真空 -> 上层按无命中处理
        return items, None

    def _precise_match(self, items: list[dict], buyer: str, project_no: str,
                       limit: int) -> AdapterResult:
        """定向精确匹配:采购人 + 项目编号 子串命中(替代宽词,避免误命中无关
        公告)。采购人优先匹配列表页抽取的 buyer 字段,兜底标题;项目编号剥
        PRJ- 前缀(信封 project_id 带前缀,标题里是无前缀形式)。"""
        matched: list[dict] = []
        for item in items:
            if buyer:
                hay = f"{item.get('buyer') or ''} {item['title']}"
                if buyer not in hay:
                    continue
            if project_no:
                core = project_no.replace("PRJ-", "")
                if core not in item["title"] and project_no not in item["title"]:
                    continue
            matched.append(item)
        if not matched:
            return AdapterResult(
                status="empty",
                message=f"定向检索(采购人『{buyer}』/项目编号『{project_no}』)"
                        f"在窗口内未命中")
        matched.sort(key=lambda i: i["published_at"] or "", reverse=True)
        return AdapterResult(status="success", items=matched[:limit])

    def _parse_list(self, page: str, page_url: str) -> list[dict] | None:
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            return None
        lis = tree.xpath('//ul[contains(@class, "c_list_bid")]/li')
        if not lis:
            return None
        items: list[dict] = []
        for li in lis:
            anchors = li.xpath('./a[@href and @title]')
            if not anchors:
                continue
            href = anchors[0].get("href") or ""
            title = " ".join((anchors[0].get("title") or "").split())
            m = _ID_RE.search(href)
            if not href or not title or not m:
                continue
            li_text = " ".join(" ".join(li.itertext()).split())
            tmatch = _LIST_TIME_RE.search(li_text)
            rmatch = _REGION_RE.search(li_text)
            bmatch = _BUYER_RE.search(li_text)
            meta = parse_tender_meta(title)
            url = urljoin(page_url, href)
            content = title
            if meta["stage"] != "其他":
                content = f"{title} | 阶段:{meta['stage']}"
            items.append({
                "platform": self.platform,
                "source": _SOURCE,
                "url": url,
                "title": title,
                "content": content,
                "published_at": _parse_list_time(tmatch.group(1) if tmatch else ""),
                "author_type": "官方公告",
                "dedup_key": f"ccgp:{m.group(1)}",
                # 以下为转储侧字段(不进 EvidenceItemLite 契约,sweep 用)
                "province": rmatch.group(1) if rmatch else "",
                "buyer": bmatch.group(1) if bmatch else meta["buyer"],
                "stage": meta["stage"],
                "opportunity": meta["opportunity"],
                "project_no": meta["project_no"],
            })
        return items

    def _enrich_with_detail(self, item: dict) -> None:
        """详情补抓:正文全文入证 + 7 字段结构化后缀(抓取薄壳,解析在
        _apply_detail,与 source_url 直抓共用)。"""
        try:
            resp = self._http_get(item["url"])
        except Exception:
            return
        if resp.status_code is None or resp.status_code >= 400:
            return
        if any(marker in resp.text for marker in _CHALLENGE_MARKERS):
            return
        self._apply_detail(item, resp.text)

    def _apply_detail(self, item: dict, page: str) -> None:
        """详情页 -> item:正文全文入证 + 7 字段结构化后缀。

        content 主体 = trafilatura 抽取的公告正文清洗文本(供 S6 逐字 quote,
        "采购内容/标的"类断言从摘要级升正文级);7 字段(预算/投标截止/获取
        文件截止/项目编号/代理机构/联系人/电话)作为结构化后缀拼在末尾
        (供精确检索/呈现回填)。正文抽取失败静默保持标题级,不伪装。
        """
        fields = parse_detail_fields(page)
        body = parse_detail_body(page)
        if body is None:
            # 旧版公告无已知正文容器时，仅在可核实详情字段存在时通用抽取。
            body = ""
            if any(value for value in fields.values()):
                try:
                    body = " ".join((trafilatura.extract(page) or "").split())
                except Exception:
                    body = ""
        parts = []
        if body and item.get("stage") and item["stage"] != "其他":
            parts.append(f"阶段:{item['stage']}")
        if fields["budget_yuan"] is not None:
            wan = fields["budget_yuan"] / 10000
            parts.append(f"预算:{wan:g}万元")
        if fields["deadline"]:
            parts.append(f"投标截止:{fields['deadline']}")
        if fields["signup_deadline"]:
            parts.append(f"获取文件截止:{fields['signup_deadline']}")
        if fields["project_no"]:
            parts.append(f"项目编号:{fields['project_no']}")
        if fields["agency"]:
            parts.append(f"代理机构:{fields['agency']}")
        if fields["contact"]:
            parts.append(f"联系人:{fields['contact']}")
        if fields["phone"]:
            parts.append(f"电话:{fields['phone']}")
        suffix = f" | {' | '.join(parts)}" if parts else ""
        if body:
            # 保留标题和正文供 S6 逐字 quote；阶段与详情字段只写一次。
            prefix = "" if item["title"] in body else f"{item['title']}\n\n"
            item["content"] = f"{prefix}{body}{suffix}"
        elif suffix:
            item["content"] = f"{item['content']}{suffix}"
        item.update({k: v for k, v in fields.items()
                     if k not in ("content",)})

    def _fetch_source_url(self, url: str) -> AdapterResult | None:
        """信封自带目标公告 URL 直抓(发现三,2026-10-16):最强路由键。

        返回 None = 本适配器不消费(非本站)或直抓失败 -> 调用方回退列表
        扫查;拦截页如实返回 authentication_required 不对抗。
        """
        if "ccgp.gov.cn" not in url:
            return None
        try:
            resp = self._http_get(url)
        except Exception:
            return None
        if resp.status_code is None or resp.status_code >= 400:
            return None
        if any(marker in resp.text for marker in _CHALLENGE_MARKERS):
            return AdapterResult(
                status="authentication_required", retryable=False,
                message="ccgp 直抓目标公告遇拦截页,不对抗")
        m = _ID_RE.search(url)
        item = {
            "platform": self.platform,
            "source": _SOURCE,
            "url": url,
            "title": _detail_title(resp.text) or url,
            "content": "",
            "published_at": "",
            "author_type": "官方公告",
            "dedup_key": (f"ccgp:{m.group(1)}" if m
                          else f"ccgp:url:{url.rstrip('/').split('/')[-1]}"),
        }
        text = _page_text(resp.text)
        tmatch = _LIST_TIME_RE.search(text)
        item["published_at"] = _parse_list_time(tmatch.group(1) if tmatch else "")
        self._apply_detail(item, resp.text)
        # 契约投影:rich 字段(budget_yuan 等)不得流入 S3 回执(extra=forbid),
        # 与列表路径 search() 尾部的投影同规
        return AdapterResult(
            status="success",
            items=[{k: item[k] for k in self._CONTRACT_KEYS}])
