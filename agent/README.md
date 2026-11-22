# Sense2act · 企业情报调查 Agent

> 面向企业直接使用的企业情报调查系统:竞品监测 / 供应商风险 / 商机研判。
> 一句话定位:**市面产品回答"发生了什么",Sense2act 回答"这件事查实了没有、
> 哪些还没查清、结论何时被推翻过"。**

## 当前状态速览(2026-10-21;10-26 增补知识库卡层)

- **测试门禁:657 项全绿**(零网络,`python -m unittest discover -s tests -p "test_*.py"`);
- **知识库卡层(K1)与链层(C1)已落地**:kb-derive 派生"对象卡+案卷卡"进独立 kb.db;kb-chain 查询时现算五投影(结论/时序/话题/履历/假设)
  (主库只读;四则入卡门槛全机械;20 案验收全过;见第十七节);
- **ESS 证据强度分阶段一已落地**:判决后只读派生旁车(`--ess on|off`,默认 on;
  条件在代码、数值在 `data/config/ess_rules.json`;分数永不进判决,S6 隔离负测试锁死);
  呈现=优先级门控(2026-10-26:附录按 priority 降序,P≥4 重点条给徽章,其余行
  只列条目;旧 kind 分流退役,两类信封统一形态);
- **V1 强度金标拆两半**:结构半 151 条已确认并入 Bench golden(strength 字段);
  供给半 15 条押后统一重录波终审;
- 历次 live 运行保持 **0 过度断言 0 矛盾反转**(有不变量门回归兜底,见第九节);
- **招标垂直源已通**:ccgp 定向检索 + sourceUrl 直取一发命中 + 1649 字正文全文入证
  (2026-10-16 live 验收);商机字段判据待一处适配(见第十五节);
- **Sense2act-Bench v0.5**:9 件标准案例入册 + 3 件待人工终审;
- 主动澄清(问人)全链落地:四档终端 + 模拟用户 + 停前兜底,防重复问询 A/B 置换 p=0.00015;
- 报告双形态:运营者六节报告 + 企业简报 brief.md + 证据图谱(mermaid)。

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
  -> 查证循环[S2 规划(决策单+验收缺口链+区分度) -> S3 执行(工具路由/平台选择/
       探索器/回执即观察) -> S4 证据加工(实体/可信度/谱系根) -> S5 七环记账
       (查重/锚定/入账/矛盾检测/派生 claim+调查圈/状态刷新) -> S6 五步裁定
       (卷宗/意见/验票/判决/归档) -> D 钩子(稳定即起草/漂移即重写)]
  -> S7 报告:六节报告 + 企业简报 brief.md + 证据图谱 + report.html + result.json
     + audit.json 审计包
```

分权纪律:触发/钩子/判决一律确定性代码;LLM 只出意见;失败不伪装;
问人不豁免轮数;Open Questions 兜底一切查不到的东西。
claim 三态 = **待证实 / 已证实 / 矛盾**,只属 claim,不属报告语气。

## 三、采集:四档模式 + 本地爬虫框架

```
sense2act/crawler/            # 自研采集框架:新源长在这里
├── egress.py                 # 唯一出网点:P0 录制/回放挂真出网边界+运行内 URL 缓存
├── base.py / registry.py     # SourceAdapter ABC(8 态回执)/显式注册表
├── service.py                # 扇出->去重(精确+simhash 近重复)->回执汇总
└── adapters/                 # ccgp(国家政采:静态列表+定向检索+详情补抓+
                              #   翻页回走+正文全文)/tianjin_zfcg(天津政采,谱系归并
                              #   ccgp)/yifangbao(乙方宝聚合,不入默认扇出防双计)/
                              #   official_site(通用企业官网:档案驱动+trafilatura)/
                              #   toutiao(跳转剥皮+时间信号)/south_weekend(SSR)/
                              #   sogou_wechat(诊断式,摘要级)/cctv_news(全栏目,
                              #   已移出默认路由,显式指定仍可用)/tender_meta(招标域共享)
```

| 模式 | 命令 | 说明 |
|---|---|---|
| 离线 fixture | `--seed`(默认) | 零网络零 key |
| 本地自研爬虫 | `--crawler local` | 真出网;配 `--record-collection` 录原始 HTTP |
| 远程 crawler 服务 | `--crawler http://127.0.0.1:8301` | 服务边界,录回执 |
| 采集回放 | `--collect-replay` | 零网络,同请求字节级同响应 |

定向检索契约(2026-10 起生效):信封路由键确定性注入查询
(`||pno=` 项目编号 / `||buyer=` 采购人 / `||since=` 时间窗);
**信封自带 sourceUrl 时 ccgp 首跳直抓详情**(非本站不消费,404 回退扫查,
不做拦截页对抗);详情字段(预算/截止/代理机构)与正文全文入证。
默认扇出:搜索型=头条+南周;R1(官方口径)=official_website;
R6(招投标)=ccgp+tianjin_zfcg+official_website(2026-10-30 并入:当事方
官网通道,A 线产品图+自述证据;company_web 根中档不进权威根[无单源例外
资格],未登记档案机构=empty 不伪装;乙方宝为聚合源不入 R6,防与 ccgp
双计独立性;B 线截图选靶仍只对政采公告两站,不含自述页)。
新增一个源三步:`adapters/` 加一个类(只依赖注入的 `http_get`)→
`crawler/__init__.py` 注册一行 → fixture 单测。
红线:验证码对抗/签名逆向/代理池不做;页面改版如实报 parser_changed。

## 四、环境准备与安装

- Python **3.11+**(开发环境为 3.11,Windows/Linux 均可);
- 安装依赖(4 个,无重依赖,不需要 playwright/浏览器):

```powershell
pip install -r requirements.txt
```

- live 模式需配置 API key(离线模式**不需要任何 key**):

```powershell
copy .env.example .env        # 填 DEEPSEEK_API_KEY;.env 已被 .gitignore 排除,永不入库
```

  `.env` 由代码内置解析,无需 python-dotenv;`DEEPSEEK_MODEL`/`DEEPSEEK_BASE_URL` 可选覆盖;
- Windows 中文乱码时(个别工具脚本读文件):设 `PYTHONUTF8=1` 重跑;
- 本文档所有命令均为 **PowerShell 单行**(换行续行符是反引号,容易踩坑,建议直接复制单行)。

## 五、快速开始(离线,零 key 零网络,clone 后即可跑)

```powershell
# 1) 全量测试(573 项,约 1 分钟)
python -m unittest discover -s tests -p "test_*.py"

# 2) 离线回放:供应商 B 风险事件(认识论核心样例:矛盾翻转/时效翻转/状态刷新)
python -m sense2act investigate --envelope examples/supplier_b/envelope.json --replay examples/supplier_b/responses.json --seed examples/supplier_b/evidence_seed.json --refresh-plan examples/supplier_b/refresh_plan.json --event-id evt_supplier_b --db data/demo.db

# 3) 离线回放:医疗采购招标样例
python -m sense2act investigate --envelope examples/tender_medical_ai/envelope.json --replay examples/tender_medical_ai/responses.json --seed examples/tender_medical_ai/evidence_seed.json --event-id evt_tender_demo --db data/t.db

# 4) 评测:官方四案例回放+确定性打分,产物 eval/report.md(离线两案例恒 1.0=回归基线)
python -m sense2act evaluate --suite eval/cases

# 5) 台账诊断(任何一步结果异常,先查账再猜)
python tests/_inspect.py data/demo.db
```

产物在 `runs/<event_id>/`:report.md(六节报告)/ brief.md(企业简报)/
report.html / result.json / audit.json(全程审计)。

## 六、live 实测(真模型+真采集)

```powershell
# 第 1 档:真模型 + 种子证据(不出网)
python -m sense2act investigate --envelope examples/xhs_opensource/envelope.json --live --record runs/recorded/l1.json --seed examples/xhs_opensource/evidence_seed.json --event-id evt_xhs --db data/x.db

# 第 2 档:真模型 + 本地爬虫真采集(完整链路)
python -m sense2act investigate --envelope examples/news_ev/envelope.json --live --crawler local --assoc entity_text --record-collection runs/recorded/eg1.json --event-id evt_news --db data/n.db
```

- 成本:tender 类信号通常几十次结构化调用;rumor/新闻类信号会顶到预算上限
  (约百次 LLM+80 次采集),**成本按信号类型分开评估**;
  `--max-llm-calls` / `--max-collect-calls` 预算护栏,耗尽优雅停止出部分报告;
- **录制件必须与管道版本+运行参数配对**:录制自动写 `runs/recorded/<名>.meta.json`
  参数旁车,回放参数不一致会硬报错(rc=2)——这是防"拿旧录制件测新管道"的机器闸门;
- 观察点:S1b claim 质量 / S2 决策单合法性(幻觉 id 被硬校验拒绝)/
  S6 引文回验与"孤证不立"/ 报告口径(待证实的有没有被写成事实)。

## 七、investigate 开关全表

| 分组 | 开关 | 说明 |
|---|---|---|
| 基础 | `--envelope` / `--db` / `--event-id` / `--out-dir` | 信封、数据库、事件 id、产物目录 |
| 离线 | `--replay` / `--seed` / `--refresh-plan` | 回放 LLM 响应/证据种子/刷新计划(`--replay` 配 `--seed` 或双回放) |
| 采集 | `--live` / `--crawler` / `--record` / `--record-collection` / `--collect-replay` | 真模型;采集档;双录制;采集回放(`--crawler` 必配 `--live` 或双回放) |
| 双回放 | `--replay × --collect-replay × --crawler local` | 全链冻结复现(LLM 段+采集段,零网络;`--event-id` 必须与录制一致=证据编号前缀;bench 主读数通道,2026-10-25 解禁) |
| 关联 | `--assoc entity\|entity_text` | 回放基线用 entity,live 建议 entity_text(词面重合宽进) |
| 分解 | `--variant 粗\|中\|细` | S1b 粒度三档(默认中) |
| 规划 | `--disc on\|off` | S2 区分度因子(默认 on) |
| 停止 | `--dry-stop-rounds N` | 死轮兜底停(连续 N 轮零增量强制停,默认 3,0=关) |
| 校准 | `--calibration on\|off` | S2 验收标准判型回填(默认 off) |
| 澄清 | `--ask-mode headless\|interactive\|mixed\|simulated` | 问人档位:静默/交互/mixed(非 TTY 自动回退)/模拟用户 |
| 澄清 | `--sim-persona` / `--sim-seed` | 模拟用户 6 人格+随机种子(cooperative/careless/irrelevant/refuse/regret/silence) |
| 澄清 | `--ask-gate on\|off` | 问前分叉实在性门(默认 on;off=消融对照) |
| 澄清 | `--ask-final on\|off` | 停前问人兜底(默认关:死轮停且存在未裁决分叉→停前问一次) |
| rumor | `--rumor-meta-seed on\|off` | rumor 信封元数据入账为自证证据(默认关;**全新 live 无冻结件可显式 on**) |
| 判决 | `--predicate-form on\|off` | 谓词形态标注(手术包件 4):S1b 出生时标 predicate_form,单源例外加法保守合并 blocklist∨LLM 只能加拦(默认 off=纯 blocklist;E2 三判据已过闸,重录波全开录制后转正) |
| 判决 | `--t1-independence on\|off` | T1 根独立性查检(手术包件 5):支持根内容级近重复折并,判决侧与 ESS 侧同折口径一致(默认 off;只折逐字级同文转载,宁漏折不可误折) |
| 判决 | `--predicate-check on\|off` | 谓词充分性核对(2026-10-25):权威放行路径前核对证据内容是否载明 claim 谓词,不载明→维持待证实(治过度断言=权威单源例外便车+断言超出;items+overall 双通道互证+双重标注一致性门,触发正清单只进病根族[credibility_rules.json 版本化];默认 off=不装配零痕迹;定标探针 tests/_pc_probe.py 七轮定标:over 19/19 折回、存活哨兵存活、清单外零错杀) |
| 判决 | `--flip-guard on\|off` | D4 维持偏置闸(2026-11-08,默认 off 入 META_KEYS):复验场景降级需硬反证(当事方自述反证只观察)+冷却窗 7 天+升回需 2 独立根(滞后 2:1)+抖动报警;规则表 data/config/flip_rules.json 改表即生效;on 时旧录制件回放须同开(meta 配对) |
| 拆条 | `--corpus-bound-code on\|off` | corpus_bound 代码判定化(手术包件 6):claim×信封语料规范化子串匹配,代码覆写模型标注(默认 off;保守取向:对不上落 world 慢路;PRJ- 门不受影响) |
| 批量 | `--envelope-batch` / `--signal-index` | 直读上游批量文件第 n 条信号 |
| 预算 | `--max-llm-calls` / `--max-rounds` / `--max-collect-calls` | 预算护栏 |

交互/模拟录制的人答回填在 `<record>.meta.json` 的 `ask_answers` 旁车,
回放自动接管不重问;终端演示件:`examples/ask_demo/try_terminal.py`。

## 八、上游信号对接(装配器)

上游信号发现层产出两种格式,装配器按条目形状自动分派:

- **v1**(0.1-draft Top-K 批量):`python -m sense2act assemble --batch examples/upstream_topk/agent_top_k_samples.json` → 信封落盘 `runs/assembled/` + manifest;
- **v2**(统一提交外层 `{signal_id, company_id, kind, payload}`):tender 单层载荷 / rumor 近直通,样例 `examples/upstream_submit/`;
- 白名单映射(extra=forbid):上游未建模键直接拒绝,不整块直灌;
- 过程量(priority/weights/rank/reasons)不进信封只进 manifest——理由可入账,聚合结论不继承;
- 直查:`python -m sense2act investigate --envelope-batch examples/upstream_topk/agent_top_k_samples.json --signal-index 0 --live --crawler local ...`

## 九、评测层与 Bench(零 LLM 评委,全部确定性打分)

| 套件 | 命令 | 内容 |
|---|---|---|
| 官方四案例 | `python -m sense2act evaluate --suite eval/cases` | 两套离线(恒 1.0 回归基线)+两套真实 |
| 不变量门 | `python -m sense2act evaluate --suite eval/invariants` | **0 过度断言 0 反转断言门,违例退出码 1**(防"修好又复发") |
| TopK 五条 ⚠已归档 | `python -m sense2act evaluate --suite eval/topk` | **历史套件,回放 rc=1 属预期勿当回归门**(录制件未随 2026-10-17 迁仓入库);同五件已入 Bench v0.5 取代,注记见 `eval/topk/README.md` |
| Bench v0.5 | `python -m sense2act evaluate --suite eval/bench` | 9 件标准案例(录制件+meta 自带) |

指标口径:**A_w 金标准一致率**(漏报 1/过度断言 2/反转 2 加权)拆成
**对齐率**(仪器健康)×**条件一致率**(判决质量)分列,不互相掩盖;
另有假设级研判传导一致性 / bootstrap CI / 引用抽检 / E1 故障注入。
对齐器三通道:draft_id 主对齐(带文本下限闸防跨 run 撞号)→ 文本相似度+谓词类型
次对齐 → 要素化金标(elements 声明)。评测自动读取录制 meta 对齐运行参数。
注意:**跨 run 的 A_w 数字不可直接横比**(对齐效度有已知天花板),正式对比须同 run 派生金标。

## 十、报告产物

| 文件 | 内容 |
|---|---|
| `report.md` | 运营者六节报告(执行摘要/claim 台账/竞争假设/溯源建议/Open Questions 等);续查时头部出「较上次报告的修正」条件句 |
| `brief.md` | **企业简报**:一句话研判/商机卡片(字段状态角标:已证实[Ex]/信封载明/待证实/信封未提供)/红旗/建议/内部待办分流;头部一行修正总览 |
| `report.html` | 演示包:红绿灯/查账视图 + **证据图谱**(mermaid:实体关系三态边/复合事实⇒竞争假设/证据⇒复合事实)+ 证据强度/修正卡/资质核对(K5)三节 |
| `result.json` | 结构化结果 |
| `audit.json` | 全程审计包 |
| `kb_check.json` | K5 资质核对旁车(--kb on ∧ kb.db 有规则卡∧自我卡时;比对格三态) |
| `last_finalize.json` | 结算锚(渲染批次起:次跑 delta 修正条件句的时点锚) |

离线重渲染简报:`python tests/_render_brief.py <旧事件db>`(改渲染逻辑后不重跑调查即可重出 brief)。
渲染层批次真实数据演示:`PYTHONUTF8=1 python tests/_render_batch_demo.py`(dalian 台账副本三连结算:K5 挂载+delta 修正条件句)。

## 十一、目录导览

| 位置 | 职责 |
|---|---|
| `sense2act/models/` | pydantic 模型(信封/决策单/观察包/意见)与领域枚举 |
| `sense2act/store/` | SQLite schema + 台账仓库(唯一写入口) |
| `sense2act/stages/` | S1-S7 + clarify/askgate/adjudicate/calibrate/sim_user + D 钩子 |
| `sense2act/collect/` | 工具/平台路由、FakeCollector、远程服务闸门、词族重建 |
| `sense2act/crawler/` | 本地爬虫框架(egress/base/registry/service/adapters) |
| `sense2act/assemble.py` | 上游信号→信封装配器(v1/v2 双格式) |
| `sense2act/evaluate.py` / `ask_metrics.py` / `run_meta.py` | 评测打分/澄清指标/录制参数旁车 |
| `sense2act/credibility.py` | ESS 证据强度分(判决后只读派生;条件在代码,数值在 ess_rules.json;S6 隔离负测试锁死) |
| `sense2act/ess_present.py` | ESS 呈现层(阶段二:决策卡/呈现分流/概率禁词/构成审计;append-only 尾部落盘,off=零痕迹) |
| `sense2act/evidence_graph.py` | 证据图谱(mermaid 档1;payload JSON 可序列化=档2 复用) |
| `sense2act/browser_auto/` | 浏览器捕获隔离通道(默认 dry-run,硬限协议) |
| `data/config/` | 域配置(可信度规则/**ESS 强度分规则表**/谱系表/词表/假设种子/平台规则/watchlist/官网档案)——换行业只换这里 |
| `examples/` | 端到端 fixture(离线/真实/上游/澄清演示) |
| `eval/` | 评测套件(cases/invariants/bench + 实验套件;topk 已归档注记)与金标 |
| `tests/` | 573 项测试 + 工具:`_inspect.py`(台账诊断)/`_render_brief.py`/`_review_golden.py`(金标核对定位表)/`_convert_news_signal.py`(上游 news_signal.v1→v2 转换)/`_v1_label.py`+`_v1_confirm.py`(V1 强度金标标注与确认)/`_ess_scan.py`+`_ess_v2v3.py`(ESS 前置实验与 V2/V3 读数)/`_v1_score.py`(V1 序一致率)/`_ess_aw_2x2.py`(ESS×A_w 2×2)/`_e4_e5_baseline.py`(E4/E5 预注册基线) |
| `CLAUDE.md` | 开发约定与陷阱清单(**新成员动手前必读**) |

## 十二、浏览器捕获通道(browser_auto,默认关)

```powershell
python -m sense2act capture --platform toutiao --url <文章URL> --event <已存在事件ID>
```

红线例外通道:不进 S3 自动扇出,适配层不驱动浏览器;硬限协议强制
(单次<=5/同词每日<=2/间隔>=60s/遇阻当日自锁);捕获证据带 [browser_auto] 标记。
环境装好 playwright 后加 `--no-dry-run` 真跑(默认环境未装,保持 dry-run 即可)。

**报告附件通道(2026-10-27 合入图片线)**:A 线 `--assets`(默认关)在 S7 前
对 official_website 证据下载企业官网产品图入报告附件;B 线 `--snapshots`
(默认关)对已证实且挂判决的 ccgp/天津政采关键证据整页截图入附件
(kind=screenshot,仅呈现非证据,assets 列判决链 S4-S6 不读=物理隔离)。
B 线走 browser_auto 隔离通道+本机 playwright 执行器,**有头真浏览器模式**
(headless 被 ccgp/天津指纹墙拦;拦截页如实放弃不做伪装对抗;真拍时短暂
弹出浏览器窗口属正常)。门禁:`--assets` 需 `--crawler local`;
`--no-dry-run` 只配 `--snapshots`;真拍需 `--live`(回放/离线零网络纪律,
浏览器出网也是出网);硬限额度与 capture 通道共用。三开关均入 META_KEYS
(录制件参数配对)。

## 十三、安全与隐私

- **永不入库**:`.env`(API key)/ `data/*.db` / `runs/` / 捕获状态文件 / `__pycache__`(.gitignore 已覆盖);
- 录制文件(`runs/recorded/`)只含采集交互与模型响应,不含凭据,但可能含业务查询词,对外公开前自行审查;
- 域配置为演示用值;接入真实监测名单时请同步检查敏感主体信息;
- eval 下各录制件与金标为项目实验数据,公开前请确认无业务敏感主体。

## 十四、开发纪律(团队约定,详见 CLAUDE.md)

1. 全量测试绿才提交,测试**只增不减**;新源/新环节必带 fixture 测试;
2. 回放 fixture 语义冻结:改了要说明,它是回归基线;
3. 录制件必须与管道版本+运行参数配对(改变 claim 集的改动会使旧录制件回放错位);
4. 分权纪律:LLM 只出意见,触发/钩子/判决一律确定性代码;失败不伪装(8 态回执);
5. live 结果异常:先 `python tests/_inspect.py <db>` 查账,不要凭感觉猜;
6. prompt 只能当行为约束的第一道,代码兜底才是第二道(三次实证的经验法则)。

## 十五、当前边界(诚实,2026-10-17)

- 商机字段 claim 的生成门当前仅识别 `PRJ-` 前缀编号,真实代理机构编号
  (HCZB-/中大招(货)类)不触发,声明式 submit_version 适配已排期;
- ccgp 检索端点存在出口 IP 软封,当前走静态公告列表+定向检索通道;
  翻页回走可救回滚出扫查深度的近期公告,更早公告靠信封 sourceUrl 直取;
- rumor 线索卡 URL 直取未做(招标线已通,rumor 期待接);
- 信封证据种子字段覆盖不完整(漏 contentSourceNote/companyProfile 等),
  TopK 案例残留约 11-15% 漏报;
- S6 裁定存在意见方差(同型语料内自证 claim 偶见滞留待证实),属已知弱点,
  靠金标人工核对发现,修复排期中;
- 仍坚硬、红线不对抗的:ggzy 检索接口 TLS 指纹墙、cebpubservice 验证码、
  乙方宝登录墙、J5 动态渲染站(确需采集走 browser_auto 隔离通道);
- 截图通道指纹边界(2026-10-23 实测,如实):ccgp/天津政采对 headless
  浏览器一律指纹拦截(403/"系统限制"页),有头真浏览器正常放行——B 线
  截图默认有头模式,拦截页如实放弃不做伪装对抗;
- 天津政采详情页匿名不可达,金额/截止仅 ccgp 源可补;
- 窗口边界:ccgp=公开招标两栏目(定向检索+翻页回走),天津政采=首页滚动窗口,
  乙方宝=首页+所配频道第一页。

## 十六、更新日志(摘要)

- **2026-11-10(证据链线条渲染修正批,方案=《实施方案/证据链线条渲染修正方案
  -20261110.md》,实录=其 §七;纯前端,agent 零改动)**:`ChainRelEdge` 自建
  `buildRelPath` 唯一几何源(path 与标签点序一次生成,positions 透传,治 40/40
  起笔垂直与标签错位);同列边列间通道贪心分道(行位区间着色,竖缝不进卡);
  长线(泳道差≥2)`zIndex:1` 压卡细线穿行;hover 邻域精准淡化替换 CSS 全量淡化
  (邻边 0.45,无关边不动)。**计划外根治 Phase 1 遗留 bug**:边样式误读
  `data.style`(实际在 Edge 顶层)致线色/粗细/虚实/箭头全程未渲染(全灰默认线),
  探针实锤后透传修复,RELATION_STYLE 全表首次上屏。新增 `frontend/scripts/
  verify-lines.mjs` 回归验收(playwright-core+本机 Edge,五断言×demo 默认/
  demo+同源/忻州 r1 三场 20/20;起笔水平率 100%/可见数/竖缝不挤/标签贴合
  0.00px/hover 邻域四查)。typecheck+build(主+离线 IIFE)过,全量 1122 OK
  零扰动。挂账:MiniMap 全白(nodeColor CSS var 在 SVG fill 不解析,Phase 1
  起观感问题,另批)。
- **2026-11-09(chain_web.v1 交互契约 Phase 0,方案=《实施方案/证据链交互化
  与专业化-设计方案-20261109.md》§四)**:前端证据工作台数据契约先行。新增
  `sense2act/chain_web_payload.py` 纯投影适配层(`build_chain_web`/`from_graph`
  双入口+`validate_chain_web` 九条不变量校验),`chain-graph --format web` 出件
  (strict 校验不过不出件)。五泳道 source/fact/verification/reasoning/conclusion,
  节点带 `provenance_ids` 反查、边带 evidence/claim/ruling 三向回指;`details`
  顶级键按 ref 索引延迟加载(摘录≤160 字,全文留台账);internal 敏感证据压净
  只留定位符(title〔internal〕材料,内容/链接/时间/平台/附件全空);LLM 旁车
  `sidecar` 诚实标注永不冒充裁定,漂移计数进 `omissions` 显式呈现;材料数≠
  独立根数三字段统计;折叠标记照行式贴身链人看语义(家族非代表/小区命题/
  被融合材料)。确定性全排序,同输入两次序列化逐字节一致;graph/ledger 零改动
  台账零写入。23 新测(test_chain_web:Schema/五泳道/边映射/溯源闭环/敏感压净/
  确定性/零 mutate/台账零写入/校验器/旁车诚实/降级/时间线/引文反查/注入/
  CLI 冒烟)。门禁:全量 1121 绿+双回放全 OK+eval 三套 rc=0+KB 幂等 76/628/390
  +demo/忻州 r1-r2 web 出件目检(strict 过+双跑字节一致+敏感压净+legacy 回落
  如实)。Phase 1(React Flow 前端,宿主=本仓 frontend/)另批施工。

- **2026-11-09(行式链与缺口退场,施工单=《实施方案/证据链卷面改造-行式链与
  缺口退场-施工单-20261109.md》,数据层 chain_graph.py 零改动)**:用户目检
  「证据链是显示结论凭什么」,拍板方案 B 行式贴身链升主引擎 + 缺口退场。①**缺口
  退出人看主图**——撤待查证小区(dot cluster_zone/fallback 小区带)、四型徽章
  (〔待查证·孤证〕→〔待查证〕+缺口型短语)、补证通道行(尚差…·通道…)、矛盾
  翻回条件行;总览计数、OQ、`md-chain` 待查证清单与 JSON `gap` 全量保留,证据
  形态行保留(=论证语义)。②**行式贴身链升主引擎**——`render_chain_svg` 恒走
  自研行式(每行一条推理线,证据堆贴身本行左侧,论点/事实组圆角框包命题行+
  框名条+框右缘收束结论,`group` 独立列撤销,列头退役);Graphviz dot 只保留
  `render_chain_png` PNG 直出,「降级布局」注记退役。③**「裁定明细」改名
  「支撑明细」**——`ruling_list_lines` 只列支持/矛盾/时效刷新(DRAW_RULINGS
  同口径),未裁/无关不逐条(总览计数与 JSON `rulings` 全量不动)。零 LLM 零
  录制件;测试 1090 绿+五门禁(双回放 18/18+eval 三套件 rc=0+kb 幂等 76/628/390
  +忻州 r1-r3+demo 重出图目检)。挂账:支撑明细人话化(全文+来源)+PDF 输出。

- **2026-11-09(证据链呈现完整化 + I2 研判说明,同批两线)**:①**链呈现
  完整化**(施工单=《实施方案/证据链呈现完整化-施工单-20261109.md》,拍板
  件=B 完整链+裁定逐条清单;用户准绳「证据链是显示结论凭什么」=图=纯推理
  结构,覆盖度披露走注记+清单):**B-1 虚框卡注记三行**=待证实命题卡状态行
  升四型徽章「〔待查证·孤证〕单一来源·已查 N 次·挂证 M 件未裁」+证据形态行
  (独立根数+同源反证,evidence_morph 纯函数)+补证通道行「尚差…·通道…」
  +矛盾卡翻回条件行,五载体同源(dot/fallback/md-chain/json);**B-2 待查证
  小区**=无边命题上图不上线(极简卡+四型徽章+一句话注记,零边隔离;收
  『查证排除/未触及』两型,挡除类继续全隐藏;dot 版面=簇声明置主体前定稿,
  小区落主链尾部);**B-3 缺口图退役**(信息收编虚框卡+小区,render_gap_svg
  函数保留可回溯;总览加未裁计数行「另有 N 份在案材料未入裁定」);**B-4
  裁定逐条清单**=全量挂账对按主图命题序(md-chain 方向标「→ 支持」+引文+
  同根折并标注+未入裁定注记行;html 新 section「裁定明细」同源函数产文本;
  件 5 引文落账=s6 意见 payload judgments 补 quote/reason,旧台账自动回落
  只列方向)。19 新测。②**I2 研判说明**(施工单=《实施方案/报告结论研判
  说明-I2施工单-20261109.md》;治「结论过于机械」):判据×本案具体化=LLM
  措辞层(分权终点:素材代码算 verdict_brief[判据首句/量级基线/反向状态/
  落选原因]→措辞 LLM 写[S7_DRAFT_PROMPT 结论节正反例定标]→五闸机器验票
  [锚有效/数值保真/禁升格/实体保真/形态+段落形态;拒收粒度=单句撤/整段
  回落零占位])。定标探针 tests/_insight_probe.py(--llm 忻州 r2+dalian
  异主题双轮 4/4 PASS 零翻转,判据被本案数值具体化[100 万/4.5 万量级一致]
  术语原文引用零)。12 新测;live 落库挂 P6 批。合计 **1090 绿**+四门禁
  (双回放 18/18+eval 三套件 rc=0 dalian 0.7368 零扰动+kb 幂等 76/628/390
  +忻州 r1-r3+demo 重出图目检全过)。

- **2026-11-08/09(D4 维持偏置闸 + I1 结论启发式,同批两线)**:①**D4
  flip-guard**(动态信念修正收口,施工单=《实施方案/动态信念修正-D4+D2收口
  施工单-20261108.md》):新开关 `--flip-guard` 默认 off(入 META_KEYS 配对,
  off=全链零行为)——维持偏置=宁漏翻不误翻:复验场景的降级需硬反证(权威
  反证单源/一手官方高可信;当事方自述=利益相关陈述只观察不翻转)+冷却窗
  7 天内同强度不二次翻转(权威反证豁免)+升回已证实需 2 独立根(滞后 2:1,
  时钟降级后的升回同走);抖动 30 天>2 次报警(判决 payload 挂标,不加新
  kind);规则表 `data/config/flip_rules.json` 版本化改表即生效。D2 缺口1
  实证=同 event 复验链路已通(挂账面向全部 claim/复验无终态过滤),收口=
  闸本身+三分支 fixture。22 新测+震荡序列单翻转验收。②**I1 结论启发式**
  (方案=《实施方案/报告结论启发式呈现-设计方案-20261108.md》):结论卡两段式
  洞察「我们的判断」+「这意味着什么」,零 LLM 模板句直出——T6 观点演化
  (调查初期的定性 vs 查证后的证据方向,三态=站住/打折/逆转;neutral 保守
  永不出逆转)+T1 基线对照(检测器倍数)+固定四拍(窗口→有利→不利→待决);
  全空=零痕迹,旧台账离线重渲染立即可看(忻州 r1 站住/r2 打折指向排斥条款/
  r3 站住,目检件 runs/_render_preview/)。模式表新键 report_stance_words
  三键映射表,改表即生效。14 新测;合计 1056 绿+五门禁(双回放 18/18+eval
  三套件 rc=0 dalian 0.7368 零扰动+kb 幂等 76/628/390)。

- **2026-11-08(报告文风线 P 波:prompt 口径根治=闸2)**:三层闸收口——
  闸1 输入过滤(第二批)+闸3 渲染机械件(第一/三批)治**已有台账**,
  P 波 prompt 治**未来 live** 从源头不复读。S7_DRAFT_PROMPT 从「台账逐条
  投影」改「面向企业管理者的研判串讲」:背景节单独定标(以项目/采购方
  为主语,禁信封/解读/信号主语,禁英文字段名);待证实合并概括禁分号
  连排;结论节首段 80-150 字串讲(围绕竞争假设证据结构讲「为什么当前
  研判更支持它」,渲染期提取挂机械卡上方,旧台账零变化);建议 rationale
  三步归纳(前提→为何现在做→前提条件,禁 claim id)。claim 输入侧附
  gist/去壳直陈文本(仅已证实剥壳=三态纪律;finalize/轮间钩子/终稿核对/
  建议/探针五处同源,轮间钩子 M 漏滤收口)。配套 P0 定标探针
  `tests/_s7_probe.py`(默认干跑=旧台账病根禁词扫基线;--llm=真模型
  定标+runtime 模型冒烟)——忻州定标两轮机器闸 9/9 零翻转;
  deepseek-v4-pro 调用成功(模型名实锤)。9 新测 1012→1021 绿+五门禁;
  新录制件 live 挂下次 live 批(与 gist/K2 同批)。

- **2026-11-08(报告文风线第一批:读者不知情原则落地)**:report.md/html
  纯渲染层改造(零 LLM 零 fixture)——**最高纪律=读者不知情**:报告读者
  不了解调查过程与 agent loop,过程性机器表述(判决梯判据/轮次/"立项"
  台账动词/兜底机制名)撤出人看层,机器口径全文保留在附录 B 判决证书与
  result.json,两层各锁。四件落地:①**OQ 条目卡三层化**(〔已查证/待查证〕
  徽章+短名/全文;材料行=判决规则→用户短语模式表映射+证据锚 [E2] 可点回;
  历史行=MM-DD 结论变更链,零变更整行省略);②**附录 A 未引用证据折叠**
  (未被任何结论引用的关联检索件折成一行,编号不重排,附附件不折叠);
  ③**附录 B 条目全文**(60 字截断退役,项目编号/金额实锚不腰斩,html
  证书卡补全文列);④**头部清扫**(停止原因用户话全枚举映射、成本行去
  内部架构名、"主导假设(交付时快照)"→"当前领先假设"去得分)。配套
  `tests/_render_report.py` 离线重渲染工具(旧台账→新格式预览,忻州 r1-r3
  重渲染目检通过);brief.md 本批零改动。17 新测 966→983;双回放 18/18、
  eval 三套件、kb 幂等全过零扰动。

- **2026-11-08(报告文风线第二批:结论三段卡+附录 E 换证据链)**:治总根因
  "claim 台账机器口径被当人看口径直接消费"——M 波纯渲染层(零 LLM 零录制件)
  六件落地:**M1 输入三档分流**(信封字段元陈述与解读类 claim 挡除/转写/融入
  三档,确定性前缀模式表,挡除类不进正文 LLM 输入不上 OQ 卡,计数注记交代;
  挡除≠抹平,台账三态/附录 B/result.json 全量照旧);**M2 依据行人话化**
  (行动建议机械行"依据: c1, c2…"退役→已查证事实列举,gist 优先+剥壳
  fallback,禁裸原文禁 id,report 与 brief 同源同改);**M3 转写注入**(结论卡
  局限说明合并句);**M4 结论三段卡(主菜)**:结论节渲染期整节替换为四问结构
  ——**凭什么**(当前研判+支撑结构+根数人话)/**比过谁**(竞争假设落选原因)/
  **差什么**(缺口列点)/**何时变**(flip 人话,机器推导表述撤出),卡面剥壳
  纪律 gist 优先,禁 claim id/假设编号/裸原文,并列主导两列对照不偏袒,无支撑
  降级一行;台账 sections 照存原文=双回放对照不受影响;**M5 附录 E 换证据链**
  (mermaid 三图退役→md=文字版证据链/html=链卷面 svg 内嵌,CDN 退役=离线零
  依赖);**M6 ev_fusion 消费**(OQ 卡材料行有有效融合旁车时升"获 N 路独立
  材料共同印证",未开 kb 零痕迹)。新增 13 测 983→996 绿;双回放 18/18、eval
  三套件 rc=0(dalian 0.7368 零扰动)、kb 幂等 76/628/390、忻州 r1-r3 离线
  重渲染目检(四问齐/附录 E 链/mermaid 清零/依据行 id 清零/禁词零命中)全过;
  施工自抓真 bug 两件(链 svg 兜底 KeyError/离线工具回退路径 None)。第二批
  P 波(prompt 口径+新录制件 live)待下一会话照施工单 §七施工。

- **2026-11-08(报告文风线第三批:正文去机器词+时间轴+徽章逐条+md 对齐)**:
  承接用户目检第二批产物后 6 点反馈,全机械层零 LLM 零录制件六件落地:
  **M1 正文节文口径转写**(S0-S2 正文节渲染期转写=字段名→人话模式表
  `report_field_phrases`[驼峰/蛇形/子字段全枚举]+壳词剥离[信封/解读/种子系
  前缀]+元陈述转写模式表 `report_meta_phrases`;台账 sections 照存原文=双回放
  安全);**M2 S1 时间轴+关联方卡**(S1 节渲染期替换=纵向时间轴[信封结构化日期
  +已证实 claim 带日期事件,用户话表述]+关联方关系卡[采购人/监测对象+代理机构],
  无日期降级一行);**M3 结论卡支撑结构徽章逐条**(「- 支撑结构:N 项已查证」+
  逐条「- X 〔已查证〕」,超 3 折叠,根数人话行保留);**M4 OQ 去 id**(head 去
  draft_id 只留优先级,附录 B id 仍留=对账不受影响);**M5 建议依据逐条徽章**
  (「- 依据(N 项,其中 M 项已查实)」+逐条「- X 〔已查证/待查证〕」,rationale
  原文照旧);**M6 md 证据链对齐**(证据标题来源去重[词典短语把平台拼进 summary
  尾部→剥离,不再「(ccgp)(ccgp)」]+标题裁断 30 字[全文在附录 A]+统一缩进)。
  新增 11 测 996→1007 绿;双回放 18/18、eval 三套件 rc=0、kb 幂等 76/628/390、
  忻州 r1-r3 离线重渲染目检(正文字段名/壳词禁词扫清零+时间轴+关联方+支撑结构
  逐条+OQ 无 id+建议逐条)全过。

- **2026-11-08(报告文风线第三批复查:用户目检后机械层四件)**:①**时间轴事件名
  提取**(20 字机械截断退役→模式表 `report_timeline_events` 事件关键词→事件名
  [投标截止/投标文件递交截止],无事件词 fallback 全文不截断;日期正则补空格形态
  +归一);②**链图 dot 检测修复+附 png**(根因=winget 装的 Graphviz 只写新终端
  PATH,离线重渲染探测落空走了降级布局→`_find_dot` 加常见安装路径兜底;html
  内嵌 dot svg 不变+同时直出 `chain.png` 落报告目录供单独取用+注记「另存图片」);
  ③**依据缩进+壳词变体**(`_md_block` 加嵌套列表渲染——「  - 」子项此前平级无
  缩进,现在嵌进父项;shells 表补半角冒号「信封载明:」/无摘要「信封正文载明」
  两变体,治「:项目编号」冒号残留与壳词漏剥);④**结论卡并列态逐条徽章**(「已查证
  +缺口挤一行」退役→每个假设支撑结构逐条〔已查证〕+尚差逐条〔待查证〕,与单一
  领先同风格)。+5 测 1007→1012 绿;五门禁全过(双回放 18/18、eval rc=0、kb 幂等、
  忻州 r1-r3 重渲染目检[事件名在位/嵌套缩进/chain.png/dot 无降级])。

- **2026-11-07(卷面批 2 用户复查二改:布线定稿+融合改命题级)**:用户
  目检两反馈当轮施工。**线条重叠**=生图大模型方案否决(与确定性逐字节/
  文字保真/JSON 对账/离线零依赖四纪律冲突),改用 **ortho+xlabel 定稿**
  ——批 1 退样条的标签飘离根因=裸 label 吸附线体,全部边标签改 xlabel
  外置(标签贴节点旁固定位),交叉弧线清零,demo 两版 png 对比目检定稿。
  **融合=罗列**根治=融合对象从「组」改「**命题级多源印证**」:同一命题
  的独立来源共同印证(「B 公司中标事实获 2 路独立材料共同印证」),同根
  转载折一根不虚高路数(卡面「2路·3件」=独立路数·折叠件数);反证/
  sensitive/时效刷新材料全排除(E11 反证混入问题根治);新「非共同归纳」
  闸=来源逐条复述或无共同印证收束词→拒(治换标点罗列);**人看主图替代
  式折叠**=被融合材料卡与逐条边不再画(融合才真正省线),JSON supports
  与证据附录全量不动。demo 重跑仅 1 张真卡(C3 两件材料无共同交集模型按
  纪律整条省略=失败不伪装);huawei 真实案探针两轮写通零翻转。测试 966
  绿+四门禁全过。
- **2026-11-07(证据链卷面改造·批 2 推理呈现+融合线)**:R 线(零 LLM,
  装配侧判定渲染纯消费)=**极性上收束线**(组内含反向→收束线标「● 含
  反向 ×N」+线尾红点,消费 conveys 现成极性=人工>旁车>无,漂移不标不
  冒充)+**关系强度分级线**(支撑边粗细三档:直接证明 3px/印证 2px/
  佐证 1.2px,反证红线不靠粗细,「线越粗=支撑越硬」)+**结论卡披露**
  (竞争假设「X」·已证实 N·居次+票型「支撑组 a ·拆台组 b ·在查组 c」,
  无落选假设=零痕迹)。F 线(chain-gen-4)=**组内材料融合 ev_fusion**:
  触发=组内在案材料≥fusion_min_members(模式表首批 2),LLM 概括过
  **六重机器闸**(成员幻觉/全覆盖/不重复/最小件数/数字保真/**形态闸=
  反机械拼接**:分号罗列或序号列表→整组拒收)→融合卡「▸ 融合 N 件」
  徽章+[En] 锚列表+实线框(材料卡照画,锚=交叉引用);sensitive 件不
  参与融合(内容不出境);拒收=零落库=无卡(失败不伪装)。定标探针
  tests/_fusion_probe.py(三真实案×2 轮:同主题写通/异主题与时间线
  罗列两轮一致拒=形态闸语义正确;**探针实抓全角分号计数笔误已根治**)。
  新 demo=14 项链形态全覆盖(转生/撞号家族/汇流/缺口四型/票型/融合/
  sensitive/gist 两态),chain-gen 真模型段零 error 零 invalid(fusion
  3/3 写通)。chain-graph 新增 --seeds。21 新测 944→965 绿+四门禁
  (双回放 18/18+三套件 rc=0 dalian 0.7368 零扰动+kb 幂等 76/628/390)
  +四格式出图目检。遗留=live 验收并入下次 live 批。
- **2026-11-07(证据链卷面改造·批 1.5 线务)**:用户目检两反馈当日施工
  (方案=《实施方案/证据链卷面改造-批1.5线务方案-20261107.md》)。**前提
  边退役下图**=命题间依赖不再画虚线(穿插根治),信息收编命题卡「前提:」
  小字行(gist 优先/短 id 兜底),JSON derives 全量保留;**框附着纪律**=
  汇流/桥边 lhead=cluster 吸附组框边(线尾不再扎框);**边标签定稿**=
  关系词上图(直接证明/印证/佐证/反证/时效更新),翻转条件文案(「唯一
  根失效→回待证实」)撤出图面=过程性机器表述去除,flip 字段 JSON 全量
  (对账与 D 线呈现底座不变)。5 新测 939→944 绿+忻州重出图目检(前提
  虚线清零/线头线尾贴框)。残余取舍=汇流边绕行路径偏长(dot 路由边界,
  如实交代不再调)。
- **2026-11-07(证据链卷面改造·四阶段批 1)**:呈现减线与事实保真——用户
  目检三反馈(事实截断/线挤互穿/证据应融合)的机械四药当日施工(方案=
  《实施方案/证据链卷面改造-四阶段改造方案-20261107.md》§九,零 LLM)。
  **A 断行卫生**=编号/金额/时刻/域名整词不断行(超宽内部硬断不溢出)+
  标点避头尾,dot/兜底/纵向/缺口图四载体共用;**B 全文呈现**=无 gist
  命题全文(裁断退役,「…」清零),机器对账投影裁断保留(两态各锁);
  **C 同文谱系归并**=撞号家族 1 卡(代表=纯最新代,史行全轨迹已证实
  历史不丢),JSON/rows 全量不动+总览注记,真派生/真异文不连坐(忻州
  实账备考见方案 §十一);**D 扇出汇流**=(证据,关系词,目标组)≥
  fanin_merge_min(模式表首批 3,改表即生效)→「佐证 ×N」汇流边入组
  锚点,JSON supports 全量;**布线**=ortho 试点标签飘离退回默认样条
  (方案 §6.1 预案),nodesep/ranksep 加大。24 新测 915→939 绿+四门禁
  (双回放 18/18+三套件 rc=0 dalian 零扰动+kb 幂等 76/628/390)+忻州
  重出图目检(0 破词 0「…」/家族卡 1 张/佐证 ×5 汇流/互穿消失)。
  批 2(LLM 事实融合)按拍板另起会话探针先行。
- **2026-11-07(证据链卷面改造·三阶段)**:横向证据链+insights 解读推理
  集合+解读桥+dot 引擎(方案=《实施方案/证据链卷面改造-三阶段施工方案-
  终版-20261106.md》§九清单,拍板 P1-P9 当日施工)。**呈现形态**(P1)=
  横向证据链:证据→命题→论点→结论自左向右推理,每行≈一条推理线,边带
  **关系词机械六词**(直接证明=权威源/印证=双根/佐证=单根/反证=红/
  时效更新/前提=虚线);独立事实止于命题层。**布局引擎**(P2)=Graphviz
  dot 为主(winget install graphviz;定位顺序 GRAPHVIZ_DOT>PATH>
  tools/graphviz),自研横向渲染兜底+图上「降级布局」注记(失败不伪装),
  两引擎共用五类卡语义层(证据卡/命题卡/解读桥卡/论点名条/结论卡)。
  **insights 集合**(P7/P8/P9)=kb.db 第七表升格正名(chain_sidecar→
  insights,老库双名兼容照读照写;status/source/legacy_claim_id 三新列;
  轻状态机 received→anchored→derived);存量迁移=chain-gen 首步机械扫描
  (解读型 claims 复制入 insights,source=legacy 回指,主库零改动一字
  不动);**呈现治理**=去壳(已证实「信封载明:X」→「X」,待证实保留壳)
  +分流(解读型不进主图命题层不进缺口图,三支=缺口建议→线索卡/研判定性
  →解读桥/元信息→挡除+计数)+**论证区资格收紧**(P4:成色披露只数事实
  成员,忻州实账 3/5→2/7)。**解读桥**(P3)=上游解读与事实链的结构桥:
  零 LLM 纯结构(interpretation 旁车指纹有效∧机器闸∧门槛 bridge_min_
  verified 参数表首批 1→画),桥上披露「依据 N 项事实·已查实 M」。
  **生成期**(chain-gen-3)三新步:存量迁移+上游解读抽取(零 LLM,信封
  解读段逐句入 insights)+interpretation 锚点(LLM,机器闸=conclusion 在
  台账∧basis 在链,幻觉拒收)+**转生闭环**(P6:缺口建议类 insights→
  去壳命题草稿,resume 续查自动注册新 claim,claims.reborn_from 谱系列,
  幂等)。**格式矩阵**:html 卷面整页/svg 单图/**png=dot 直出**(报告嵌入
  /Word/PPT)/**md-chain=文字版证据链**(报告正文嵌入)/json 第一公民/
  md=mermaid 对账附录。**33 新测 882→915 绿**+四门禁(双回放 18/18+
  eval 三套件 rc=0+kb 幂等 76卡/628链接/390证据)+live 验收=忻州真模型
  一次通(存量迁移 7 条+老 kb.db 自动升格)+demo 全形态件(上游抽取 3 句+
  interpretation 1/1+转生 RB1/RB2 实账+四格式出图);demo 工具=
  tests/_chain_demo_v3.py。
  遗留=真实上游解读段信封待上游对接后补一次
  live(现验收用仿上游格式合成信封,已如实标注)/语义注记(LLM 关系词)
  进路保留第一版不做。

- **2026-11-06(证据链卷面改造·二阶段)**:卷面与呈现重做(方案=
  《实施方案/证据链卷面改造-设计方案-20261105.md》,用户目检一阶段出图后
  逐点拍板,当日施工)——一阶段判据/旁车/指纹/白名单/CLI 全部保留复用,
  重做的是**图的定义与渲染载体**。**一卷三件**:卷首总览(假设资格逐行
  交代:挣到论证区/亦已立居次/未立起/无材料触及+命题与材料计数+无边命题
  计数)+主图+缺口图(独立附录)。**主图=论证区+机械事实组框+材料层**:
  论证区=挣来的区域(有已证实成员的假设才立区[候审不算],成员数降序→
  记分降序→id 升序取第 1,每卷至多 1,落选者进总览一行;区名带成色披露
  「部分立住:n/m 条已证实」;无够格假设→无论证区不冒充,总图恒在不降级
  砍图);机械事实组框=claim_entities 主语实体确定性聚类(框名「关于X的
  事实」,零研判零 LLM);命题卡三层人话(gist 标题/状态行〔已证实〕锚点
  人话〔待查证〕缺口型短语/状态史行「立项 10-30 → 查实 11-08」)。
  **无边不画**:零证明关系命题不进主图(去向=缺口图+总览计数);信封
  种子件不入材料层(「未提供 X 字段」类机器元陈述不产卡不产线,仅信封
  种子查实的自证条目总览注记交代,不冒充删证——机器对账通道全量在)。
  **缺口图独立**(用户拍板撤出主图):待证实行「命题 ──◇〔四型徽章〕缺
  什么 → 通道建议」+矛盾行「命题 ──◇〔已反对〕→ 翻回条件(两独立正向
  方可翻回)」。**三线系统**(用户拍板:太多太复杂被否):灰实线=支撑
  (默认款不带字,分层位置已表达语义)/红实线=反证(全图唯一颜色)/
  灰虚线+「前提」=派生(跨框弧合法);共据不画线=材料卡注记「同根 N 件
  互不为独立佐证」;长解释进卡片。**废 mermaid 人看主载体**(自动布局=
  横向爆炸/裁字/拖拽根因):自研确定性分层布局渲染器 chain_svg.py(纯
  Python 零依赖,固定 1000px 纵向卷,泳道分栏+走廊布线,中文宽度自算
  不再裁字,XML 五字符转义注入防御,同输入两次渲染逐字节一致;mermaid
  函数保留仅作机器对账投影,CDN/fonts.ready 退役=**离线双击即看**)。
  **生成期三新 kind**(chain-gen,PROMPT_VERSION chain-gen-2):ev_summary
  材料概括(材料集与装配同判据=裁定在案∧非信封种子;失败=词典短语兜底
  不留空不伪装 LLM)/grouping 论点分组(**覆盖完整性机械校验**:漏/多/
  重复/虚构 id→拒收退机械平铺[按主语类目];hypothesis_seeds.json 加法式
  人工 grouping 覆写优先[draft_id 解析,与 polarity map 同款])/narrative
  章首研判+论证区串讲(只述证据结构禁真相断言;无论证区=零落库)。
  **LLM 产物永不回流判决评分**(负测试延续;章首研判只述结构不下结论;
  呈现不标 AI 照一阶段)。CLI:`chain-graph --format html`(卷面整页,人看
  主载体)/`--format svg`(独立主图,可打印/嵌报告)/json(第一公民)/
  md(mermaid 机器对账附录);kb-chain CLI 保留不动(三链人看退役,机器
  对账通道不变);tests/_chain_view.py 改造为卷面视图。**40 新测 839→879
  绿**;真实验证=忻州(论证区「可投标商机」3/5 挣到+两假设居次+9 条无边
  去缺口图+3 条自证元陈述注记)与 evt_t(全卷无已证实→无论证区不冒充,
  降级出总图)双案出图目检;
- **2026-11-05(证据链形态图)**:链呈现从「附录拼接」改图形态(方案=
  《实施方案/证据链形态图-设计方案-20261105.md》,当日施工)——四类节点
  (研判假设/命题[gist 做标签,立住实心没立住虚线]/证据材料[同
  lineage_root 聚类框=十家转载一根的可视化]/缺口虚位[标缺什么+走哪个
  通道])+边属性(载明=权威根加粗+独立根编号+翻转条件边标注/派生=
  parent_claim_id 直接消费[前置核实已落库,零 schema 改动]/共据=冗余
  拓扑/传导=方向极性正反线型);**graph JSON 第一公民**(nodes/edges/
  clusters/meta,md 只是投影);缺口四型判据=镜像锚点表待证实分支(未
  触及/查证排除/孤证/谓词不足,与 s6 判决梯自动同源);**噪音纪律**=
  无关票/挂账未裁证据不产节点,分计数注记(已查 N 次≠挂证 N 件未裁);
  **LLM 两处生成期**(方向极性+缺口通道建议)=独立命令 `chain-gen`
  落 kb.db chain_sidecar 旁车(model/指纹/时间戳,漂移弃用重跑即刷;
  hypothesis_seeds.json 加法式 polarity map 人工优先;通道白名单=
  platform_rules 机器闸两侧同校验;**呈现不标 AI,LLM 产物永不回流
  判决评分**[负测试锁]);出图=`chain-graph` 确定性零 LLM 零网络
  (json=graph JSON/md=mermaid 投影);kb_chain 五投影零改动(chain_topic
  加法式 with_graph 键);挂载=tests/_chain_view.py(两层图+证据附录
  [En] 锚,浏览器即看)+独立产物 --out;32 新测 807→839 绿+双回放
  18/18+eval 三套件 rc=0+kb 幂等零扰动(76卡/628链接/390指纹);
  live 验收=evt_t 台账真模型生成期一次通(polarity 3/3 通道 4/5 全在
  白名单)+忻州/evt_t 双案出图目检;
- **2026-11-01(动态报告)**:时钟复查链——resume 续查/到期降级/报告版本化/
  演变时间轴(队友 chenyiming0324 直推主仓库 fix,本地 11-03 拉取;11-05
  同步核查=全量 807 绿+双回放 18/18+eval 三套件 rc=0)——①ledger:
  claims_due_for_recheck(纯时钟到期选择器)+event_envelope_json+
  register_event 重开路径+downgrade_untouched_due(**护栏=纯时钟只降级
  不判终态**);②CLI:`investigate --event <id>` 复查模式(信封从台账
  读回续查);③S2 event_summary 加 due_recheck 键(非空才出现=旧回放
  字节不变)+prompt 一句;④finalize 前时钟降级(到期且本会话未触及→
  状态刷新降回待证实);⑤s7:report_history 三件(md/html/brief)同档
  存档+结论演变时间轴(transitions 带 ts 日期化,跨会话分隔线区分重号
  轮次);**认识论护栏=翻转终态必须挂证据,纯时钟只做降级**;9 新测
  797→807;live 验收挂下次 live 批;
- **2026-10-30(ccgp)**:定向精确匹配 AND→OR 评分(队友,同步自旧仓库
  824b59a)——信封 project_id 为合成 ID(标题无真实编号回退)时,AND
  语义把采购人已命中的目标公告一并排除(哈医大附一院 2026-10-21 live
  实证:定向检索空手而归);改 OR 评分:双命中>单命中>零分,零分剔除,
  单键命中即入选;+1 回归测试(buyer 命中+合成编号仍入选);
- **2026-10-30(R6)**:官网路由并入——platform_rules 正本 R6(招投标公告,
  恒先命中)扇出 ccgp+tianjin_zfcg → 末位加 official_website,tender 案
  当事方官网通道打通(10-27 发现的不可达问题,施工单=kb-dyn-plans 补记五)。
  认识论语义=官网证据进 S3 台账:company_web 根中档**不进权威根**(发布主体
  不分离+无法定公示效力,无单源例外资格),可参与独立根计数与矛盾翻转;
  official_sites.json 档案驱动,未登记机构=empty 附名单不伪装;前置核对=
  两档案域名均已登记 source_lineage→company_web 映射(零补数据);2 处扇出
  精确断言随语义反转同步(test_ccgp/test_tianjin_zfcg),B 线截图选靶
  _SNAPSHOT_PLATFORMS 不含官网=截图只对政采公告不对自述页(设计如此);
  797 全绿+eval 三套件 rc=0+bench 逐案读数零扰动(dalian 0.7368 吻合);
  验收=厦大附一演示信封 live 跑(runs/evt_r6_official_demo):官网 7 条证据
  入账[lineage_root=company_web/credibility=中/author_type=当事方]+
  A 线产品图 2 张带附件+6 条已证实 claim 纪律核查全过(无一仅 company_web
  单根定案);data/config_demo 演示副本与 .gitignore 行清理(正本已含
  同款能力);
- **2026-10-30**:gist 短名方案落地(治 claim 呈现冗长的根治,渲染消费位
  10-27 已预留)——S1b 出生短名:`ClaimDraft.gist` 可选字段(旧 fixture
  兼容)+claims.gist 列加法式迁移+`S1B_PROMPT` 基座写作纪律(**①对象名
  与关键数字/金额/日期完整保留——公告原文数据不得变形省略;②其余表述
  直接省略不压缩改写;③20 字上下软参考**+few-shot 五条拍板示例;零行为
  参数不加开关照 ESS 先例,三档 variant 都带);finalize 查
  `claim_gists` 一次全产物消费:正文 OQ 节/brief 红旗·已核实·OQ 行/
  ESS 附录(内容列+决策卡)/delta 头部 md+html;**附录 B 核对面不消费**
  (摘要不得顶替核对正文,用户拍板);空表=fallback 截断=旧台账逐字节
  零变化;超长 gist 同过消费侧截断=版面防线;10 新测 787→797 全绿+
  双回放 18/18 零错位+_pc_probe 基线健康;新 prompt 录制件并入下次
  live 批(与 K2 live 验收同批);
- **2026-10-27(深夜)**:渲染层批次落地(K5 呈现+delta 头部+html ESS 节,
  s7 写权区合并批次单人串行)——①ESS 计算上移 `_ess_compute`(finalize
  交付前算一次,html 与 md 落盘同源消费),report.html 决策卡区补证据
  强度节(回退风险警示+优先级门控表格,与 md 附录同源文本过白名单转换);
  ②delta 呈现接线(delta_present.py:finalize 读锚算 diff→report.md 头部
  修正条件句[旧态→新态+成因+依据+来源]/brief 一行总览/html 修正卡→末位
  写秒级结算锚 last_finalize.json;首跑无锚=三产物零痕迹;**锚粒度必须=
  台账秒级**:微秒锚会让秒级 transitions 字符串比较恒判「锚前」,锚后同秒
  修正被漏,demo 实证);③K5 资质核对表呈现(kb_present.py:门控=--kb on∧
  kb.db 有规则卡∧有自我卡否则零痕迹;report.md/brief.md 双挂+html K5 节;
  结论句三行机械模板[合计/红线硬伤逐条点名/待证实 Open Questions 指引];
  敏感过滤=自我卡 internal 时依据列显式值不出境只出定位符;kb_check.json
  旁车;呈现零写台账);24 新测 762→786 全绿+三套件 rc=0+双回放 18/18+
  KB 验收 76卡/628链/390指纹零扰动;真实数据演示=tests/_render_batch_demo.py;
- **2026-10-27(晚)**:S7 报告 HTML 正文渲染修复——正文此前 md 源码直显
  (`#` 标题/`**粗体**`原样),新增零依赖确定性转换器(标题/列表/表格/
  引用块/粗体;`[n]` 引用标上标化并锚到证据表对应行=可点击查账;escape
  优先,正文不可注入 HTML)+节标题重复剥离;A/B 双通道演示跑通(A 线官网
  产品图+B 线 ccgp 公告整页+关键行截图);发现待拍板:platform_rules 正本
  R6 恒先命中且扇出不含 official_website→tender 案官网通道不可达;
- **2026-10-27(晚)**:图片线合入(队友 screenshot-assets 分支 cherry-pick
  7 笔,历史不相连跳过根快照)——A 线官网产品图+ B 线招标公告截图
  (s7_assets 附件收集器,assets 列判决链 S4-S6 不读=物理隔离;browser_auto
  隔离通道+有头模式+硬限额度共用,默认 dry-run);三门禁(--assets 需
  --crawler local/--no-dry-run 只配 --snapshots/真拍需 --live);
  --assets/--snapshots/--no-dry-run 入 META_KEYS 疫苗链(照 --kb 先例:
  run_meta 归一[dry_run 正向键反推]+ALIGN_KEYS 同源+evaluate store_true
  特例透传+_planb_replay 同步,3 新测锁死);README §十二/§十五并入;
  playwright=requirements 可选依赖(离线/CI 不装不影响测试);
- **2026-10-27(晚)**:知识库 K5 规则卡比对落地(K 线数据层收官)——
  独立规则表 rule_card(kb.db 第六表,尺子≠证据不入 card_kinds)+查表
  比对纯函数(kb_check.py:确定性零 LLM,每格走三态=金标口径,待证实
  不伪装满足;holds 分层语义=attributes 显式记录可判异/claim 沉默≠否认;
  数值谓词只消费结构化属性;谓词白名单查 kb_rules.json v kb-0.2 开放
  注册改表即生效);自我卡种子=上传件 entity 带 self:true 归 ent_self
  (§三要点5);kb-import --rules+kb-check CLI;14 新测 710→724;
  呈现并入渲染批次(§十一-4);
- **2026-10-27**:知识库 K2+K3v1 落地+LLM 两件套定标——K2=S3 第四通道
  (`--kb` 检索:确定性三通道/正卡区∧public 限定/lineage 继承预设原根
  [复曝不涨独立根零 S6 改动]/--kb-path 版本配对闸/kb.db 自包含[内容快照
  +关联快照];kb_retrieve.py;680→701);K3v1=内部上传(kb-import:台账
  →对象卡确定性转换器+md 文档档1;上传件=源数据落 data/kb_uploads 重派生
  后存活;敏感级默认 internal=K2 检索永不服务;701→710);两件套探针
  (_sg_probe.py:subject+source_group S1b 出生标注定标,13 案×2 遍+定向
  挽回通道)**读数=subject 定向挽回 0/25(全部合法但无一命中名表=归一
  接不住)+source_group 两遍组划分一致仅 33%——两判据读数交用户拍板**
  (探针不改生产代码);测试 680→710;eval 三套件零扰动;live 验收挂账
  (集成死锁疫苗纪律);
- **2026-10-22(下午)**:手术包件 5/6 落地(t1_independence.py 根独立性折并
  [判决侧+ESS 侧同函数口径一致]+corpus_bound.py 代码判定化[覆写模型标注留审计,
  PRJ- 门保护];--t1-independence/--corpus-bound-code 默认 off 入 META_KEYS;
  测试 545→573;eval 三套件零扰动);
- **2026-10-22**:第二批实验全清(E1①②③ 分裂 0/重语境化真反转仅 2;E3 改写集
  漏拦 33/33;E2 live 标注三判据全过=件 4 闸门通过)+手术包件 4 谓词形态标注落地
  (predicate_form.py 验票+加法保守合并;--predicate-form 默认 off 入 META_KEYS;
  测试 519→545;eval 三套件零扰动);
- **2026-10-21(下午)**:ESS 阶段二呈现落地(ess_present.py:brief/report 追加
  「证据强度」节=决策卡+呈现分流 tender 低调/rumor 显性+概率禁词测试锁死+分数
  构成审计;append-only 不碰渲染层;off=零痕迹);V1 序一致率打分器+ESS×A_w 2×2
  +E4/E5 预注册基线三读数件落地;测试 505→519;eval 三套件逐案例零扰动;
- **2026-10-20/21**:ESS 证据强度分阶段一落地(credibility.py 纯函数+ess_rules.json
  规则表+finalize 挂载旁车;`--ess on|off` 零行为参数,分数永不进判决;
  42 项测试,总数 463→505;eval 三套件零扰动;锚点数值经 E6/E1④ 前置实验定值);
  V1 强度金标拆两半——结构半 151 条确认并入 Bench golden(strength 字段,九案
  读数零扰动),供给半 15 条押后统一重录波;eval/topk 历史套件归档注记
  (录制件未随迁仓入库,rc=1 属预期,已被 Bench 取代);
- **2026-10-16**:采集定向检索改造验收(sourceUrl 直取一发命中,正文全文入证);
  判决梯谓词门(复合断言防单源例外滥用);cctv 移出默认路由;证据图谱四处修复;
  上游 news_signal.v1 转换器;feat 分支 463 测试全绿;
- **2026-10-11~15**:P0 采集定向检索施工(路由键注入/翻页回走/详情字段保全/
  正文全文入证/simhash 去重);Bench v0.5 九件入册;金标核对 SOP;
- **2026-10-09~10**:主动澄清 P0-P3 落地(问人答案落账传导/交互终端/模拟用户/
  反事实门/停前兜底);报告双形态(report+brief);rumor 元数据种子;证据图谱;
- **2026-10-03~08**:S1b 粒度三档+S2 迭代(死轮兜底停/区分度因子);评测层全指标;
  对齐器双闸+要素化金标;上游装配器 v1/v2;ccgp/天津政采/乙方宝/企业官网四源接入;
- **2026-09-27~30**:本地爬虫框架+四新闻源;browser_auto 隔离通道;S2 停止纪律。

## 十七、知识库卡层(K1,2026-10-26;K2/K3/K5 2026-10-27;蓝图=《实施方案/企业知识库-实施方案-20261025.md》)

- **定位**:调查记忆——把一次性调查的产物沉淀为"对象卡+案卷卡",供跨 run
  聚合与(下一期的)链查询;不是 RAG,是台账的跨 run 升格,三态/谱系纪律
  全部继承;**独立 kb.db 对主库纯只读**(mode=ro),卡是派生视图只存引用
  不拷贝状态(claim 三态现场读主库);
- **命令**:
```powershell
# 20 案批量派生(录制库直读=真值口径;离线案自动零 LLM 回放后派生)
python -m sense2act kb-derive --suite eval/bench --kb data/kb.db
# 单库模式;信号 manifest 挂项目卡(可多个)
python -m sense2act kb-derive --source-db data/rerecord/tender_dalian_police_r1.db --event-id evt_tender_dalian_police_r1 --kb data/kb.db --signals runs/assembled/_rerecord_tender5/manifest.json
# 验收(预注册断言:案卷全量对照/门槛互证/幂等 sha256/管道版本标注/主库零写入)
PYTHONUTF8=1 python tests/_kb_verify.py --suite eval/bench
# K5 规则卡:上传规则表→重派生→比对(每格走三态;--subject 默认 ent_self 自我卡)
python -m sense2act kb-import --rules rules.json --upload-dir data/kb_uploads
python -m sense2act kb-check --kb data/kb.db --db <主库...> --rule 测试供应商准入规则 --subject ent_self
```
- **卡种五类**(schema 开放注册,kind 不写死):实体|项目|话题|案卷|文档
  ——v1 派生**实体/项目/案卷**三类(话题/文档待 K3 上传通道);项目锚=
  `events.project_id` 外部稳定 ID,同 pno 跨 run 聚合同一张项目卡;
- **入卡门槛(全机械零 LLM,参数查 `data/config/kb_rules.json`)**:
  关联则(实体归一命中)/来源则(支持根含采集∨人答∨内部历史——信封/
  种子复述族整族出局)/**自证出局**(corpus_bound 代码判定∨框架词前缀
  「信封载明/线索卡称…」的复述型断言不进对象卡——卡上只有世界断言,
  用户 10-26 拍板"彻底出局";案卷卡兜底不变)/状态则(已证实·矛盾→正卡区,
  待证实∧合格根→未决区)/兜底则(案卷卡全量一条不丢);**种子质量闸**:
  病根族文本∧predicate_check off→降权待复核区(清单同源
  credibility_rules.json 指针零复制);
- **确定性纪律**:派生=幂等纯函数,同源两遍逐字节一致(sha256 断言);
  时间戳全取自源产物(禁 now());管道版本标注入 kb_anchor(meta 旁车
  三开关);卡种归一走主库同款 ent_ 哈希 scheme(跨库可对照);
- **OQ 挂载(C1 配套,2026-10-26)**:未过状态则的待证实 claim(查过没有/
  unreachable;已进未决区的不重复)以 ref_kind=oq 挂到主语实体卡——三态
  纪律"查不到→Open Questions"在卡上的投影;判据=锚定 join 从严(子串
  宽进不取,探针实证 bench 182 条待证实:全等 0/锚定 join 134/子串 75);
  只挂已注册实体卡不生噪音卡,案卷兜底不变;`_kb_verify` 互证含 OQ 独立
  口径重算;20 案筛效:602→628 链接(+26 OQ),对象链接零扰动;
- **泄漏墙红线**:派生源=bench 台账(含结论级信息),**bench 主读数永远
  KB-off**;
- **K2=S3 第四通道(`--kb`,2026-10-27)**:KB 检索命中→SourceReport 与
  爬虫同构入账;确定性三通道(字段契约 pno/实体归一/simhash 同文);只服务
  **正卡区∧public**(未决区=S1b 背景通道缓行,internal 永不进 prompt);
  lineage 继承=KB 件预设原根(S4 守卫),复曝不涨独立根="KB 证据不能单独
  支撑已证实"自然涌现零 S6 改动;`--kb-path` 版本入 meta 配对(录制未开
  kb 回放开=rc2,版本漂移=rc2);kb.db 自包含(证据内容快照+claim→证据
  关联快照,zone 同款"快照非状态拷贝");live 验收挂账下次 live 批;
- **K3v1=内部上传通道(2026-10-27)**:`kb-import --ledger <csv/json>`
  台账→对象卡批量(确定性零 LLM)/`--doc <md>` 文档档1 登记(文档卡+
  原件指纹,拆解层押后);上传件=源数据落 `data/kb_uploads/*.json`
  (重派生后存活);敏感级默认 internal=K2 检索永不服务;
- **K5=规则卡比对(2026-10-27)**:蓝图 §三要点6"独立规则表非对象卡
  (尺子≠证据)"——`kb-import --rules <json/csv>` 上传规则表(谓词白名单
  查 kb_rules.json rule_check,开放注册改表即生效),落 kb.db 第六表
  rule_card(全量重建语义,上传件重派生后存活);`kb-check` 查表比对
  纯函数(确定性零 LLM),规则条目×对象卡每格走三态=金标口径:
  **已证实=载明∧真/矛盾=载明∧假/待证实=无载明(不伪装满足)**;
  证据优先级=attributes 显式值>正卡区 claim 载明>未决区不作依据;
  holds 分层语义(attributes 显式记录不含要求值=可判异;claim 文本
  沉默≠否认);数值谓词 v1 只消费结构化属性(文本数值提取不可靠,
  如实待证实);自我卡=上传件 entity 带 `self:true` 归 `ent_self`
  (§三要点5 资质核对表挂靠点);md 出口仅为数据层自检,**资质核对表
  正式呈现并入渲染批次**(§十一-4);
- **本期不含**:LLM 辅助四环节(两件套探针已定标见更新日志,读数待拍板)/
  K3b docx·pdf 提取/K3c OCR。

## 十八、链层(C1,2026-10-26;方案=《实施方案/企业知识库-链compute_chain-设计方案-20261026.md》)

- **定位**:kb.db 的第二种查询视图——对象卡答"把 X 的卡调出来",链答
  "Y 结论的根据是什么、何时判的、何时被推翻过"。**链=查询时现算纯函数,
  kb.db 不存链不存状态**(铁律零改动);全程零 LLM 零判决零记分(链是
  结论的呈现者不是生产者),主库+kb.db 双只读(哈希不变断言);
- **一个内核+五投影**:`_chain_core` 六步(claim 本体/证据集/T1 根折并/
  ESS 强度查表/状态史/卡挂载)产出结构化 dict;投影 A 结论链(全量)/
  B 时序链(翻转点标注)/C 话题链(三段=信号轴+案卷互链+结论分区与 OQ
  行,分区用**现算状态**、zone 快照只作对照+漂移计数)/D 证据履历链
  (指纹反查:复曝史+同根成员指纹数+引用结论)/E 假设链(研判级,
  hypothesis_standings 现成,人裁钉死旗标随行);
- **多主库路由**:kb.db 是跨 run 聚合库、正本数据在每案一个主库——
  `open_mains(paths)` 按 claim 的 event 前缀路由(同 id 多库=显式报错);
- **as_of 语义**:主轴=round(复用 collect_factors 回放语义,None=现势);
  decay 的 as_of 默认取 events.updated_at(源侧时间禁 now);与 ess.json
  对照时传其 as_of 即逐条可复算(强度=score_claim 同函数同参);
- **敏感过滤进渲染器**:sensitivity=internal 的证据只出存在性+定位符,
  内容/标题/链接不出境(md 渲染层;dict 保留标记供 K2 消费侧过滤);
- **命令**:
```powershell
# 结论链(单 claim 审计入口;--format json=机器可读双产出)
python -m sense2act kb-chain --kind conclusion --claim evt_tender_xinzhou_college_r1:c15 --db data/rerecord/tender_xinzhou_college_r1.db --kb data/kb.db
# 时序链(何时被推翻;推翻点标注)
python -m sense2act kb-chain --kind timeline --claim <claim_id> --db <主库> --kb data/kb.db
# 话题链(对象卡现势展开;跨 run 聚合卡 --db 可多个)
python -m sense2act kb-chain --kind topic --entity ent_xxx --db <主库1> <主库2> --kb data/kb.db
# 证据履历链(指纹复曝史+同根折并数给你看)/假设链(研判级记分)
python -m sense2act kb-chain --kind provenance --fingerprint <fp> --db <主库...> --kb data/kb.db
python -m sense2act kb-chain --kind hypothesis --event <evt> --hypothesis <evt>:H1 --db <主库> --kb data/kb.db
#   --as-of-round N=轮内回放;--t1-independence=照录制 flags 复现根折并口径
# 验收工具(PYTHONUTF8=1 前缀)
python tests/_chain_demo.py   # bench 20 案聚合库真实数据演示(话题链+履历链)
```
- **呈现纪律继承**:禁词表/锚点名>数字//100 定语/排序全显式(同输入
  重算逐字节一致)/冲突不评分;证据行按裁定方向排序(支持/矛盾在前,
  渲染层;内核 dict 保持 (round, evidence_id));
- **本期不含**(照方案 §九):LLM 一句话摘要(11b,派生期定位+链留引用
  位,立项须定标探针)/source_group 出生标注(11a,与 subject 前移两件套
  同批探针)/ts 轴时点重算/K2 检索挂载。
