"""本地爬虫框架:新适配器一律长在这里(自研采集,与远程 crawler 服务并存)。

分层:adapters(SourceAdapter,只拿 http_get) -> registry(显式注册)
   -> service(扇出/去重/8态回执,接口与闸门同构) -> egress(唯一出网点,
   P0 录制/回放挂在这里——真出网边界)。
新增一个真实源 = 在 adapters/ 加一个类 + registry 工厂注册一行 + fixture 测试。
已移植:cctv_news(全栏目扫查)/ toutiao(跳转剥皮+时间信号)/
south_weekend(SSR 面板)/ sogou_wechat(诊断式分类,摘要级)/
tianjin_zfcg(天津政采首页扫查)/ ccgp(国家政采静态公告列表+限额详情
补抓:预算/投标截止,列表自带地域与采购人;检索端点被软封但静态通道可达,
2026-10-04 实测)。招标域共享件在 adapters/tender_meta.py(阶段/商机语义/
类别词纪律/统一过滤排序)。official_site=通用企业官网适配器(R1 缺口):
data/config/official_sites.json 档案驱动 + 通用启发式兜底 + trafilatura
纯解析正文(决策#20 轻量路线;J5 动态渲染站如实排除走 browser_auto)。
坚硬平台(微博/小红书/抖音/微信正文)按架构纪律不移植。乙方宝(yfbzb.com)
2026-10-03 曾判坚硬(SafeLine JS 挑战),2026-10-04 复勘发现按 TLS 指纹
放行 Python urllib(curl 被挑战)——egress 即 urllib,天然过墙,已接入
(yifangbao:首页区块+zbzt 频道;聚合源·中档·独立根·不入 R6 防与 ccgp 双计;
详情深度字段在登录墙后不补抓)。如需更多聚合源数据走商业 API(远程
crawler 服务档)仍是合规路径。
"""
from .adapters.cctv_news import CctvNewsAdapter
from .adapters.ccgp import CcgpAdapter
from .adapters.official_site import OfficialSiteAdapter, load_official_profiles
from .adapters.sogou_wechat import SogouWechatAdapter
from .adapters.south_weekend import SouthWeekendAdapter
from .adapters.tianjin_zfcg import TianjinZfcgAdapter
from .adapters.toutiao import ToutiaoAdapter
from .adapters.yifangbao import YifangbaoAdapter
from .base import AdapterResult, SourceAdapter
from .egress import EgressHttpClient, EgressResponse, egress_fingerprint
from .registry import SourceRegistry
from .service import LocalCollectService


def build_default_registry(egress) -> SourceRegistry:
    """组合根:已移植的真实源;新适配器在此追加一行。"""
    return SourceRegistry(
        factories={
            "cctv_news": lambda: CctvNewsAdapter(http_get=egress.get),
            "toutiao": lambda: ToutiaoAdapter(http_get=egress.get),
            "south_weekend": lambda: SouthWeekendAdapter(http_get=egress.get),
            "sogou_wechat": lambda: SogouWechatAdapter(http_get=egress.get),
            "tianjin_zfcg": lambda: TianjinZfcgAdapter(http_get=egress.get),
            "ccgp": lambda: CcgpAdapter(http_get=egress.get, detail_cap=3),
            # 乙方宝:聚合源(中可信,独立根);不进 R6 默认扇出,防与 ccgp 双计
            "yifangbao": lambda: YifangbaoAdapter(http_get=egress.get),
            # 通用企业官网(档案驱动+trafilatura;R1 已引用,注册即生效)
            "official_website": lambda: OfficialSiteAdapter(
                http_get=egress.get, profiles=load_official_profiles()),
        },
        # 默认扇出=头条+南周(用户拍板 2026-09-27):搜索型源,对查询词有真实响应;
        # cctv 为全栏目列表型源;2026-10-16 起不进 R1 默认路由(实战对行业信号
        # 不对题、产出无关证据),显式指定 platforms 时仍可用(同 tianjin 先例)
        defaults=["toutiao", "south_weekend"])


__all__ = [
    "AdapterResult", "SourceAdapter", "CctvNewsAdapter", "ToutiaoAdapter",
    "SouthWeekendAdapter", "SogouWechatAdapter", "TianjinZfcgAdapter",
    "CcgpAdapter", "YifangbaoAdapter", "OfficialSiteAdapter",
    "load_official_profiles", "EgressHttpClient", "EgressResponse",
    "egress_fingerprint", "SourceRegistry", "LocalCollectService",
    "build_default_registry",
]
