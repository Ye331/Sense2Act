# -*- coding: utf-8 -*-
"""
E1-7 演示链路①(采集):空库一键灌入 10 个源 + 30+ 篇文档(含重复/多机构/多类别)。
幂等:源按名字去重,文档靠后端 URL/content_hash 去重 —— 重复执行全部计 duplicates,不新增。

用法:
  python scripts/seed_s3.py                    # 默认 http://127.0.0.1:8080
  BASE=http://host:port INTERNAL_API_KEY=xxx python scripts/seed_s3.py

(可选)先起 embedding 演示端点再灌库,语义检索有数据:
  python scripts/embedding_stub.py &   # 端口 8901,compose 里配 EMBEDDING_ENDPOINT=http://host.docker.internal:8901/embed
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8080").rstrip("/")
INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "demo-internal-key-2026")
ADMIN = ("admin@sense2act.local", os.environ.get("SEED_ADMIN_PASSWORD", "admin123"))

# 绕过系统代理,本地直连
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def call(method, path, body=None, token=None, internal=False):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if internal:
        req.add_header("X-Internal-Key", INTERNAL_KEY)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login():
    r = call("POST", "/api/v1/auth/token", {"email": ADMIN[0], "password": ADMIN[1]})
    return r["data"]["token"]


# ---------- 10 个源 ----------

SOURCES = [
    {"name": "中国政府采购网-医疗设备", "type": "web_page", "url": "http://www.ccgp.gov.cn/medical/",
     "adapter": "ccgp", "schedule_cron": "0 0 6 * * *",
     "config": {"list_selector": ".list li", "detail_selector": ".content"}},
    {"name": "中国政府采购网-医疗信息化", "type": "web_page", "url": "http://www.ccgp.gov.cn/health-it/",
     "adapter": "ccgp", "schedule_cron": "0 20 6,18 * * *", "config": {"list_selector": ".list li"}},
    {"name": "某省采购网-全省公告", "type": "web_page", "url": "http://cgw.example-province.gov.cn/",
     "adapter": "province", "schedule_cron": "0 40 7 * * *", "config": {"region": "华东/某省"}},
    {"name": "某省卫生健康委-政策文件", "type": "web_page", "url": "http://wjw.example-province.gov.cn/zcwj/",
     "adapter": "gov_policy", "schedule_cron": "0 10 8 * * MON-FRI", "config": {}},
    {"name": "某省医保局-采购公示", "type": "api", "url": "http://ybj.example-province.gov.cn/api/announces",
     "adapter": "ybj_api", "schedule_cron": "0 30 9 * * *", "config": {"page_size": 50}},
    {"name": "某市公共资源交易中心-医疗", "type": "web_page", "url": "http://ggzy.example-city.gov.cn/medical/",
     "adapter": "city_ggzy", "schedule_cron": "0 0 7,19 * * *", "config": {"city": "某市"}},
    {"name": "医疗器械行业资讯", "type": "rss", "url": "http://news.med-device.example/rss.xml",
     "adapter": "rss", "schedule_cron": "0 0 8 * * *", "config": {}},
    {"name": "医疗信息化观察", "type": "rss", "url": "http://news.health-it.example/feed.xml",
     "adapter": "rss", "schedule_cron": "0 0 9 * * *", "config": {}},
    {"name": "华北某市卫健专栏", "type": "web_page", "url": "http://wjw.example-north.gov.cn/col/",
     "adapter": "gov_policy", "schedule_cron": "0 0 10 * * *", "config": {"region": "华北/北京市"}},
    {"name": "医疗网络安全动态", "type": "rss", "url": "http://news.med-sec.example/rss.xml",
     "adapter": "rss", "schedule_cron": "", "config": {}},   # 无 cron = 仅手动触发
]


def ensure_sources(token):
    existing = set()
    page = 1
    while True:
        r = call("GET", "/api/v1/sources?page=%d&page_size=100" % page, token=token)
        data = r["data"]
        existing.update(s["name"] for s in data["items"])
        if page * data["page_size"] >= data["total"]:
            break
        page += 1
    created = skipped = 0
    for s in SOURCES:
        if s["name"] in existing:
            skipped += 1
            continue
        call("POST", "/api/v1/sources", s, token=token)
        created += 1
    return created, skipped


# ---------- 30+ 篇文档(同一批里故意混重复) ----------

# (源名, 文档列表);机构名故意带全半角/空白变体,验证归一
DOCS = [
    ("中国政府采购网-医疗设备", [
        {"url": "http://www.ccgp.gov.cn/notice/1001.htm", "doc_type": "announcement",
         "title": "某市人民医院AI辅助诊断系统采购项目公开招标公告", "org_name": "（某市）人民医院",
         "amount": "3250000.00", "publish_date": "2026-09-24", "deadline": "2026-10-15",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "本项目为AI辅助诊断系统建设,含影像AI分析模块与临床决策支持,预算金额325万元。",
         "raw_html": "<html><body>某市人民医院AI辅助诊断系统采购项目公开招标公告……</body></html>"},
        {"url": "http://www.ccgp.gov.cn/notice/1002.htm", "doc_type": "announcement",
         "title": "某市人民医院CT设备采购公告", "org_name": "  (某市)人民医院  ",
         "amount": "9800000.00", "publish_date": "2026-09-18", "deadline": "2026-10-08",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "采购64排螺旋CT一台,含五年维保。", "raw_html": "<html><body>CT设备采购公告……</body></html>"},
        {"url": "http://www.ccgp.gov.cn/notice/1003.htm", "doc_type": "announcement",
         "title": "某市第二人民医院智慧病房建设项目公告", "org_name": "某市第二人民医院",
         "amount": "4600000.00", "publish_date": "2026-09-12", "deadline": "2026-10-10",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "智慧病房含床旁交互、生命体征采集与护理呼叫系统。"},
        {"url": "http://www.ccgp.gov.cn/notice/1004.htm", "doc_type": "announcement",
         "title": "某市中医院中药煎药中心设备采购公告", "org_name": "某市中医院",
         "amount": "1200000.00", "publish_date": "2026-08-30", "deadline": "2026-09-20",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "自动煎药包装线两条及中药配方颗粒调剂设备。"},
        {"url": "http://www.ccgp.gov.cn/notice/1005.htm", "doc_type": "announcement",
         "title": "某市妇幼保健院妇幼健康管理平台采购公告", "org_name": "某市妇幼保健院",
         "amount": "2150000.00", "publish_date": "2026-09-02", "deadline": "2026-09-25",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "妇幼健康管理平台含产前筛查管理与儿童健康档案。"},
        # URL 重复(带 fragment,归一后同 1001)
        {"url": "http://www.ccgp.gov.cn/notice/1001.htm#detail", "doc_type": "announcement",
         "title": "某市人民医院AI辅助诊断系统采购项目公开招标公告", "org_name": "某市人民医院",
         "amount": "3250000.00", "publish_date": "2026-09-24",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "本项目为AI辅助诊断系统建设,含影像AI分析模块与临床决策支持,预算金额325万元。"},
    ]),
    ("中国政府采购网-医疗信息化", [
        {"url": "http://www.ccgp.gov.cn/notice/2001.htm", "doc_type": "announcement",
         "title": "某省人民医院互联网医院平台升级项目公告", "org_name": "某省人民医院",
         "amount": "5800000.00", "publish_date": "2026-09-20", "deadline": "2026-10-18",
         "region": "华东/某省", "category": "医疗信息化",
         "content_text": "互联网医院平台升级,新增在线复诊、电子处方流转与药品配送对接。"},
        {"url": "http://www.ccgp.gov.cn/notice/2002.htm", "doc_type": "announcement",
         "title": "某市人民医院数据中台建设采购公告", "org_name": "（某市）人民医院",
         "amount": "4100000.00", "publish_date": "2026-09-08", "deadline": "2026-09-30",
         "region": "华东/某省某市", "category": "数据平台",
         "content_text": "建设医院数据中台,含主数据管理、指标体系与科研数据服务。"},
        {"url": "http://www.ccgp.gov.cn/notice/2003.htm", "doc_type": "announcement",
         "title": "某市第二人民医院网络安全等级保护整改项目", "org_name": "某市第二人民医院",
         "amount": "890000.00", "publish_date": "2026-08-25", "deadline": "2026-09-15",
         "region": "华东/某省某市", "category": "网络安全",
         "content_text": "按等保三级要求开展边界防护、日志审计与数据脱敏改造。"},
        {"url": "http://www.ccgp.gov.cn/notice/2004.htm", "doc_type": "announcement",
         "title": "某市中医院电子病历评级改造公告", "org_name": "某市中医院",
         "amount": "1560000.00", "publish_date": "2026-07-28", "deadline": "2026-08-20",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "电子病历系统四级评级改造,含质控闭环与临床知识库。"},
        {"url": "http://www.ccgp.gov.cn/notice/2005.htm", "doc_type": "announcement",
         "title": "某市妇幼保健院出生医学证明系统迁移公告", "org_name": "某市妇幼保健院",
         "amount": "320000.00", "publish_date": "2026-07-20", "deadline": "2026-08-10",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "出生医学证明系统向省级平台迁移对接。"},
    ]),
    ("某省采购网-全省公告", [
        {"url": "http://cgw.example-province.gov.cn/n/3001", "doc_type": "announcement",
         "title": "某省人民医院手术机器人采购公告", "org_name": "某省人民医院",
         "amount": "42000000.00", "publish_date": "2026-09-22", "deadline": "2026-10-20",
         "region": "华东/某省", "category": "医疗设备",
         "content_text": "采购腔镜手术机器人系统一套,含培训与耗材。"},
        {"url": "http://cgw.example-province.gov.cn/n/3002", "doc_type": "announcement",
         "title": "某市人民医院DSA血管造影设备采购", "org_name": "某市人民医院",
         "amount": "17500000.00", "publish_date": "2026-09-15", "deadline": "2026-10-12",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "平板DSA一台,含介入手术室配套。"},
        {"url": "http://cgw.example-province.gov.cn/n/3003", "doc_type": "announcement",
         "title": "某市第二人民医院检验科流水线采购公告", "org_name": "某市第二人民医院",
         "amount": "8300000.00", "publish_date": "2026-09-05", "deadline": "2026-09-28",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "全自动生化免疫流水线,前处理模块与信息管理系统。"},
        {"url": "http://cgw.example-province.gov.cn/n/3004", "doc_type": "announcement",
         "title": "某市中医院康复设备批量采购公告", "org_name": "某市中医院",
         "amount": "2650000.00", "publish_date": "2026-08-18", "deadline": "2026-09-08",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "下肢康复机器人、冲击波治疗仪等康复设备一批。"},
        {"url": "http://cgw.example-province.gov.cn/n/3005", "doc_type": "announcement",
         "title": "某省人民医院科研大数据平台采购公告", "org_name": "某省人民医院",
         "amount": "6900000.00", "publish_date": "2026-08-10", "deadline": "2026-09-01",
         "region": "华东/某省", "category": "数据平台",
         "content_text": "科研大数据平台含专病库建设与随访管理系统。"},
    ]),
    ("某省卫生健康委-政策文件", [
        {"url": "http://wjw.example-province.gov.cn/zcwj/p4001", "doc_type": "policy",
         "title": "某省卫生健康信息化建设三年行动计划(2026-2028)", "org_name": "某省卫生健康委员会",
         "publish_date": "2026-09-10",
         "region": "华东/某省", "category": "医疗信息化",
         "content_text": "到2028年实现三级医院电子病历评级平均四级,推进医学影像云与AI辅助诊断应用。"},
        {"url": "http://wjw.example-province.gov.cn/zcwj/p4002", "doc_type": "policy",
         "title": "关于推进医疗机构网络安全建设的指导意见", "org_name": "某省卫生健康委员会",
         "publish_date": "2026-08-05",
         "region": "华东/某省", "category": "网络安全",
         "content_text": "二级以上医院2027年底前完成等保三级测评,数据出境需安全评估。"},
        {"url": "http://wjw.example-province.gov.cn/zcwj/p4003", "doc_type": "policy",
         "title": "某省医学检查检验结果互认实施方案", "org_name": "某省卫生健康委员会",
         "publish_date": "2026-07-15",
         "region": "华东/某省", "category": "医疗信息化",
         "content_text": "检查检验结果互认项目清单与质控要求。"},
    ]),
    ("某省医保局-采购公示", [
        {"url": "http://ybj.example-province.gov.cn/api/announces/5001", "doc_type": "announcement",
         "title": "医保基金智能监管系统采购公示", "org_name": "某省医疗保障局",
         "amount": "9600000.00", "publish_date": "2026-09-16", "deadline": "2026-10-16",
         "region": "华东/某省", "category": "数据平台",
         "content_text": "医保基金智能监管,含大数据反欺诈模型与飞检线索分析。"},
        {"url": "http://ybj.example-province.gov.cn/api/announces/5002", "doc_type": "announcement",
         "title": "DRG/DIP 支付方式改革信息化配套采购", "org_name": "某省医疗保障局",
         "amount": "5400000.00", "publish_date": "2026-08-28", "deadline": "2026-09-26",
         "region": "华东/某省", "category": "医疗信息化",
         "content_text": "DRG分组器升级、病案质控与运营分析平台。"},
    ]),
    ("某市公共资源交易中心-医疗", [
        {"url": "http://ggzy.example-city.gov.cn/medical/6001", "doc_type": "announcement",
         "title": "某市人民医院核医学科设备采购结果公示", "org_name": "某市人民医院",
         "amount": "6200000.00", "publish_date": "2026-09-26",
         "region": "华东/某省某市", "category": "医疗设备",
         "content_text": "PET-CT中标公示。"},
        {"url": "http://ggzy.example-city.gov.cn/medical/6002", "doc_type": "announcement",
         "title": "某市中医院信息化运维服务项目", "org_name": "某市中医院",
         "amount": "760000.00", "publish_date": "2026-09-06",
         "region": "华东/某省某市", "category": "医疗信息化",
         "content_text": "信息系统三年驻场运维,含安全加固。"},
        {"url": "http://ggzy.example-city.gov.cn/medical/6003", "doc_type": "announcement",
         "title": "某市第二人民医院停车引导与安防系统改造", "org_name": "某市第二人民医院",
         "amount": "540000.00", "publish_date": "2026-08-22",
         "region": "华东/某省某市", "category": "网络安全",
         "content_text": "车牌识别、视频安防与园区网络改造。"},
    ]),
    ("医疗器械行业资讯", [
        {"url": "http://news.med-device.example/a/7001", "doc_type": "news",
         "title": "国产影像设备中标率持续提升", "publish_date": "2026-09-19",
         "region": "华东/某省", "category": "医疗设备",
         "content_text": "统计显示本季度国产影像设备在三甲医院中标率提升至六成。"},
        {"url": "http://news.med-device.example/a/7002", "doc_type": "news",
         "title": "手术机器人进入集采视野", "publish_date": "2026-09-01",
         "region": "华东/某省", "category": "医疗设备",
         "content_text": "多省探索手术机器人耗材集采,降价预期明显。"},
    ]),
    ("医疗信息化观察", [
        {"url": "http://news.health-it.example/a/8001", "doc_type": "news",
         "title": "医院数据中台建设进入普及期", "publish_date": "2026-09-14",
         "region": "华东/某省", "category": "数据平台",
         "content_text": "调研显示头部医院数据中台建成率过半,主数据治理成重点。"},
        {"url": "http://news.health-it.example/a/8002", "doc_type": "news",
         "title": "AI辅助诊断收费试点扩围", "publish_date": "2026-08-16",
         "region": "华东/某省", "category": "医疗信息化",
         "content_text": "多个省份将AI辅助诊断纳入医疗服务价格试点。"},
    ]),
    ("华北某市卫健专栏", [
        {"url": "http://wjw.example-north.gov.cn/col/9001", "doc_type": "policy",
         "title": "北京市某区基层医疗信息化提升方案", "org_name": "北京市某区卫生健康委员会",
         "publish_date": "2026-09-03",
         "region": "华北/北京市", "category": "医疗信息化",
         "content_text": "社区卫生服务中心信息系统统一改造,与区域影像云对接。"},
        {"url": "http://wjw.example-north.gov.cn/col/9002", "doc_type": "announcement",
         "title": "北京市某区社区卫生服务设备更新公告", "org_name": "北京市某区卫生健康委员会",
         "amount": "1800000.00", "publish_date": "2026-08-12",
         "region": "华北/北京市", "category": "医疗设备",
         "content_text": "基层医疗设备更新一批,含便携超声与心电图机。"},
    ]),
    ("医疗网络安全动态", [
        {"url": "http://news.med-sec.example/a/10001", "doc_type": "news",
         "title": "医疗机构勒索病毒攻击事件季度通报", "publish_date": "2026-09-23",
         "region": "华北/北京市", "category": "网络安全",
         "content_text": "本季度通报医疗机构网络安全事件同比下降,但数据泄露占比上升。"},
        # 内容重复(与 2003 同 title/类型/日期/正文,URL 不同 → content_hash 命中)
        {"url": "http://news.med-sec.example/a/10002", "doc_type": "announcement",
         "title": "某市第二人民医院网络安全等级保护整改项目", "org_name": "某市第二人民医院",
         "amount": "890000.00", "publish_date": "2026-08-25",
         "region": "华东/某省某市", "category": "网络安全",
         "content_text": "按等保三级要求开展边界防护、日志审计与数据脱敏改造。"},
    ]),
]


def push_docs(source_name_to_id):
    total = {"accepted": 0, "duplicates": 0, "failed": 0}
    for source_name, items in DOCS:
        r = call("POST", "/api/v1/internal/documents",
                 {"source_id": source_name_to_id[source_name], "items": items}, internal=True)
        d = r["data"]
        total["accepted"] += d["accepted"]
        total["duplicates"] += d["duplicates"]
        total["failed"] += d["failed"]
        print("  源[%s] accepted=%d duplicates=%d failed=%d"
              % (source_name, d["accepted"], d["duplicates"], d["failed"]))
    return total


def main():
    print("BASE =", BASE)
    token = login()
    created, skipped = ensure_sources(token)
    print("源: 新建 %d,已存在跳过 %d" % (created, skipped))

    name_to_id = {}
    page = 1
    while True:
        r = call("GET", "/api/v1/sources?page=%d&page_size=100" % page, token=token)
        data = r["data"]
        for s in data["items"]:
            name_to_id[s["name"]] = s["id"]
        if page * data["page_size"] >= data["total"]:
            break
        page += 1
    missing = [s["name"] for s in SOURCES if s["name"] not in name_to_id]
    if missing:
        print("缺源,中止:", missing)
        sys.exit(1)

    print("推送 %d 组文档…" % len(DOCS))
    total = push_docs(name_to_id)
    print("合计: accepted=%d duplicates=%d failed=%d" % (total["accepted"], total["duplicates"], total["failed"]))

    r = call("GET", "/api/v1/documents?page=1&page_size=1", token=token)
    print("库内文档总数: %d" % r["data"]["total"])
    print("幂等验证: 再跑一次,全部应计 duplicates。")


if __name__ == "__main__":
    main()
