# Sense2act · 企业情报调查 Agent

> 面向企业直接使用的企业情报调查系统:竞品监测 / 供应商风险 / 商机研判 / 工商异动。
> 定位一句话:**市面产品回答"发生了什么",Sense2act 回答"这件事查实了没有、哪些还没查清、结论何时被推翻过"。**

## 一、三层职责

```
┌────────────────────────────────────────────────────────────────┐
│ 信号发现层(上游,另档实现)                                    │
│ 答"值不值得查":清洗/项目归一 -> 历史比较 -> 金额/频率/新颖性    │
│ 检测 -> Top-K -> 可解释触发("金额为历史中位数的4.2倍")         │
├────────────────────────────────────────────────────────────────┤
│ Sense2act 调查层(本仓库)                                     │
│ 答"查了是什么":claim 三态台账 + 模型出意见/代码出判决          │
│ + 同源谱系折算 + 调查圈派生 + 全链路留痕                        │
├────────────────────────────────────────────────────────────────┤
│ 报告与建议层:竞争假设式研判 + 溯源式行动建议 + Open Questions   │
└────────────────────────────────────────────────────────────────┘
```

衔接纪律:信号层 detector 解释 = 调查层第一批证据(检测-调查置信贯通);
"依据不足,不强行评分"与三态纪律同构(两层都不伪装确定)。

## 二、骨架结构

```
信封(招标信号/传闻信号) -> S1a 定性+假设种子 -> S1b 调查感知分解
  -> 查证循环[S2 规划(决策单+验收缺口链) -> S3 执行(工具路由/探索器/回执即观察)
       -> S4 证据加工(实体/可信度/谱系根) -> S5 七环记账(查重/锚定/入账/
       矛盾检测/派生 claim+调查圈/状态刷新) -> S6 五步裁定(卷宗/意见/验票/
       判决/归档) -> D 钩子(稳定即起草/漂移即重写)]
  -> S7 六节报告 + report.html 演示包 + result.json + audit.json 审计包
```

分权纪律:触发/钩子/判决一律确定性代码;LLM 只出意见;失败不伪装;
问人不豁免轮数;Open Questions 兜底一切查不到的东西。

## 三、采集:四档模式 + 本地爬虫框架

```
sense2act/crawler/            # 自研采集框架:新源长在这里
├── egress.py                 # 唯一出网点:P0 录制/回放挂真出网边界+运行内 URL 缓存
├── base.py / registry.py     # SourceAdapter ABC(8 态)/显式注册表
├── service.py                # 扇出->去重->回执汇总(接口与闸门同构)
└── adapters/                 # cctv_news(全栏目扫查)/toutiao(跳转剥皮+时间信号)/
                              # south_weekend(SSR)/sogou_wechat(诊断式,摘要级)/
                              # tianjin_zfcg(天津政采首页扫查,谱系归并 ccgp 根)/
                              # ccgp(国家政采静态列表+详情补抓:预算/投标截止)/
                              # yifangbao(乙方宝聚合:首页区块+zbzt频道,urllib过墙)/
                              # official_site(通用企业官网:档案驱动+trafilatura正文)/
                              # tender_meta.py(招标域共享:阶段/商机/类别词/过滤)
```

| 模式 | 命令 | 录制位置 |
|---|---|---|
| 离线 fixture | `--seed`(默认) | — |
| 本地自研爬虫 | `--crawler local` | **真出网点**(`--record-collection` 录原始 HTTP) |
| 远程 crawler 服务 | `--crawler http://127.0.0.1:8301` | 服务边界(录回执) |
| 采集回放 | `--collect-replay`(零网络,同请求字节级同响应) | — |

默认扇出=**头条+南周**(搜索型源);cctv 为全栏目列表型,按 R1 规则按需启用;
R1(官方口径)扇出 cctv_news+official_website,R6(招投标)扇出 ccgp+tianjin_zfcg
(乙方宝为聚合源不入 R6,防与 ccgp 独立性双计)。招标域共享件 tender_meta
含商机语义分层(进行中/已关闭/非商机),支持面向企业投标的商机数据集转储。
新增一个源三步:`adapters/` 加一个类(只依赖注入的 `http_get`)→
registry 注册一行 → fixture 单测。红线:验证码对抗/签名逆向/代理池不做,
页面改版如实报 parser_changed。

## 四、快速开始

```powershell
pip install -r requirements.txt      # pydantic/lxml 必装;openai 仅 live;trafilatura 供官网正文抽取

# 离线回放(零网络零 key):供应商 B 风险事件(矛盾翻转/时效翻转/状态刷新)
python -m sense2act investigate --envelope examples/supplier_b/envelope.json `
  --replay examples/supplier_b/responses.json --seed examples/supplier_b/evidence_seed.json `
  --refresh-plan examples/supplier_b/refresh_plan.json --event-id evt_supplier_b --db data/demo.db

# 真实样例:科大讯飞联合发布化工大模型(真实采集内容)
python -m sense2act investigate --envelope examples/ai_model_release/envelope.json `
  --replay runs/recorded/llm_ai_model.json --seed examples/ai_model_release/evidence_seed.json `
  --event-id evt_ai_model --db data/ai.db

python -m unittest discover -s tests -p "test_*.py"   # 全量测试(138 项,零网络)
```

## 五、live 实测(真模型)

```powershell
copy .env.example .env        # 填 DEEPSEEK_API_KEY(该文件已被 .gitignore 排除)

# 第 1 档:真模型 + 真实采集内容 + 双录制(LLM 响应/采集交互)
python -m sense2act investigate --envelope examples/xhs_opensource/envelope.json `
  --live --record runs/recorded/llm_xhs.json `
  --seed examples/xhs_opensource/evidence_seed.json --event-id evt_xhs_llm --db data/xhs.db

# 第 2 档:真模型 + 本地爬虫真采集(--assoc entity_text 开启词面重合关联)
python -m sense2act investigate --envelope examples/news_ev/envelope.json `
  --live --crawler local --assoc entity_text `
  --record-collection runs/recorded/egress.json --event-id evt_news --db data/n.db
```

观察点:S1b claim 质量 / S2 决策单合法性(幻觉 id 被硬校验拒绝)/
S6 引文回验与"孤证被否决"/ 报告口径(待证实的有没有被写成事实)。
成本:单事件约 25-40 次结构化调用,DeepSeek 价格下个位数人民币;
`--max-llm-calls` 预算护栏耗尽即优雅停止出部分报告。

### 已完成的三轮 live 迭代与两条真实样例

- 瓶颈演进:①执行层(查询词覆盖->探索器 v1 换词重试)→②分解层(锚定与
  解释型 claim->假设可区分性纪律)→③经济层(预算与卷宗上限);
- 真实样例(讯飞化工大模型/小红书开源,examples/ 下):**同源折算连续两次
  正确拒绝单源证实**——4 条头条转载折算 1 独立根,孤证不立,Open Questions
  如实列明解除路径;当时的"检索不对题"能力缺口已由垂直源适配器补上
  (ccgp/天津政采/乙方宝/通用企业官网,见第九节)。

## 六、浏览器捕获通道(browser_auto,默认关)

```powershell
python -m sense2act capture --platform toutiao --url <文章URL> --event <已存在事件ID>   # 默认 dry-run
```

红线例外通道(决策 #14):不进 S3 自动扇出,适配层不驱动浏览器;硬限协议强制
(单次<=5/同词每日<=2/间隔>=60s/遇阻当日自锁);捕获证据带 [browser_auto]
标记入账。环境装好 playwright 后加 `--no-dry-run` 真跑。

## 七、目录导览

| 位置 | 职责 |
|---|---|
| `sense2act/models/` | pydantic 模型(信封/决策单/观察包/意见)与领域枚举 |
| `sense2act/store/` | SQLite schema + 台账仓库(唯一写入口,高频查询) |
| `sense2act/collect/` | 工具/平台路由、FakeCollector、远程服务闸门、词族重建 |
| `sense2act/crawler/` | 本地爬虫框架(egress/base/registry/service/adapters) |
| `sense2act/browser_auto/` | 浏览器捕获隔离通道(硬限协议/信封解析/WSL 执行器) |
| `sense2act/stages/` | S1-S7 + clarify + D 钩子(每文件=一环,Protocol 依赖倒置) |
| `sense2act/llm.py` | StructuredLLM 协议 + Replay/DeepSeek/录制三实现 + 预算 |
| `sense2act/prompts.py` | 各槽位 SYSTEM_PROMPT(企业情报 rubric) |
| `data/config/` | 域配置(词表/规则表/谱系表/假设种子/监测名单/官网档案)——换行业只换这里 |
| `examples/` | 四套端到端 fixture(两套离线精调 + 两套真实数据) |
| `tests/` | 138 项测试:单元(裁定规则/验票协议/硬限协议/适配器解析与 8 态/路由与谱系)+ 四事件 e2e |

## 八、安全与隐私

- `.env`(API key)**不入库**;`data/*.db`、`runs/`、捕获状态文件均已忽略;
- 首次提交前做过密钥扫描;录制文件(`runs/recorded/`)只含采集交互与模型
  响应,不含凭据,但可能含业务查询词,公开仓库前请自行审查;
- 域配置为演示用值;接入真实监测名单时请同步检查敏感主体信息。

## 九、当前边界(诚实)

- 探索器 v1=确定性换词重试;语义级(同义词族+验收自评)属 P1;
- NER v1=词表+信用代码正则,人物/账号消歧难点仍在;
- 交互式澄清未接(HeadlessClarifier 自动作答并落日志);
- 近重复查重(simhash)未接(精确查重已做;同题异文不合并,报告显式标注〔同题 N 稿〕);
- 招标/官网垂直源——**三条曾经的封锁均已找到替代通道解决**:
  - **ccgp 国家政采**:检索端点被出口 IP 软封 → 改走**静态公告列表通道**
    (`/cggg/{dfgg,zygg}/gkzb/`,匿名可达),全国覆盖,列表自带地域/采购人,
    详情补抓预算与投标截止;软封是否为维护期(9/30-10/7)暂时收紧,
    已排 10-07 后复测(定时任务),放行则可再增检索能力;
  - **乙方宝(yfbzb.com)**:曾判坚硬(SafeLine JS 挑战)→ 实测 WAF 按
    TLS 指纹放行 Python urllib(egress 即 urllib,天然过墙,非对抗)→
    已接首页区块+zbzt 频道;聚合源·中档独立根·不入 R6(防与 ccgp 同公告
    双计独立性);详情深度字段在登录墙后,如实不补抓;
  - **企业官网(按用户企业定制)**:official_website 通用适配器
    (official_sites.json 档案+启发式兜底+trafilatura 纯解析正文,决策 #20
    轻量路线;egress 抓取,唯一出网点/录制回放纪律不破);加一家企业=
    档案加一条+谱系加一行;
- 仍坚硬、红线不对抗的:ggzy 检索接口 TLS 指纹墙(404 伪装页)、
  cebpubservice vaptcha 验证码、乙方宝登录墙、J5 动态渲染站(讯飞/格力
  官网实测为 JS 壳)——J5 类如确需采集走 browser_auto 隔离通道(决策 #14,
  默认关);聚合数据如需深度字段,商业 API(远程 crawler 服务档)是合规路径;
- 已接源的窗口边界(如实):ccgp 仅公开招标两栏目最新一页(~40条/次),
  天津政采=首页滚动窗口(~211条/数日),乙方宝=首页+所配频道第一页——
  翻页/跨栏目扩采属后续;天津政采详情页匿名不可达,金额/截止仅 ccgp 源可补。

## 十、接入 Sense2Act 后端(worker)

`python -m sense2act worker` 常驻领取后端调查(`sense2act/bridge/`),全程不改 S1-S7 循环本身:

```
GET created → start(CAS) → context → 装配信封 → pipeline.run
   台账每条决策(ledger.log)实时回写:questions / round_started / tool_selected /
   证据登记 + tool_completed / 判决 → clarified + reflection_updated
→ 未决问题标 unresolved → ReportDraft(三态 → fact/inference/speculation)→ report → complete
```

```powershell
$env:INTERNAL_API_KEY = 'local-dev-internal-key'      # 与后端一致
python -m sense2act worker --mode replay              # 零 key:招投标信号回放 examples/tender_medical_ai,新闻信号回放 examples/ai_model_release
python -m sense2act worker --mode live                # 真模型 + 本地爬虫,需 DEEPSEEK_API_KEY
```

- 后端是预算权威:`max_rounds` 取自调查;超轮次/预算(42201)转为预算护栏优雅停止并交部分报告;用户停止(40901)立即收手。
- 信封:招投标信号(检测器命中)装配 tender 信封;新闻信号(命中 `news_event`,由 signal-service 的新闻分支推送)以 `detail.rumor` 装配 rumor 信封,报告以监测主体为标题主语、事件类型记 `news`。
- 证据:信号层证据以 `doc_id` 登记;外部证据带 URL、发布时间和 Agent 读到的原文(作快照);缺 URL/时间的不登记、不被报告引用。
- 每个调查一份台账库:`data/worker/<investigation_id>/ledger.db`,产物在同目录 `runs/`。
- 字段映射见 `../docs/integration.md`;契约测试 `tests/test_bridge.py`。
