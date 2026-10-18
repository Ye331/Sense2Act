# CLAUDE.md · Sense2act 开发约定(新会话/新成员必读)

> 本目录:Sense2Act monorepo 的调查 Agent(S1-S7 调查循环 + claim 三态台账 + 本地爬虫框架)。
> 全栈架构见根目录 `../README.md`,跨组件字段映射见 `../docs/integration.md`;权威边界说明见本目录 README 第九节。
> 采集服务 `../collector` 复用本目录的 `sense2act/crawler` 适配器:改适配器时两边的测试都要跑。

## 目录速览

- `sense2act/models/` pydantic 模型与枚举(中文域值:待证实/已证实/矛盾;五类目;四方向)
- `sense2act/store/` schema.sql + ledger.py(台账仓库=全系统唯一写入口)
- `sense2act/stages/` s1_signal/clarify/s2_planner/s3_executor/s4_observer/s5_ledger/
  s6_verdict/d_hooks/s7_report(每文件=一环,Protocol 依赖倒置)
- `sense2act/collect/` rules(平台路由)/fake/expansion(词族)/crawler_gate(服务边界闸门)
- `sense2act/crawler/` 本地爬虫:egress(唯一出网点+录制)/base/registry/service/adapters
- `sense2act/browser_auto/` 浏览器捕获隔离通道(默认 dry-run,硬限协议强制)
- `data/config/` 域配置(可信度规则/谱系表/词表/假设种子/平台规则/watchlist/别名/官网档案)
- `examples/` 回放与演示样例(worker replay 用 tender_medical_ai 与 ai_model_release);`tests/` 475 项(_inspect.py 为台账诊断工具)

## 开发门禁

1. 全量测试绿才提交:`python -m unittest discover -s tests -p "test_*.py"`(只增不减);
2. 改循环机制(S1-S7/钩子/判决规则)前先读 README 第二、九节与相关代码里的决策注记——20 条架构决策
   勿轻易推翻(原《舆情调查Agent架构》与 2026-09-23 交接文档不在本仓库,需要时向原作者索取);
3. 改 prompt(prompts.py)后:跑真实样例→`--record` 录制固化→回归;
4. live 结果异常:先 `python tests/_inspect.py <db>` 查账(claims/transitions/sections/
   判决/降级计数),不要凭感觉猜;
5. 新采集源三步:adapters/ 加类(只依赖注入 http_get,8 态,失败不伪装)→
   `crawler/__init__.py` 注册一行 → fixture 单测。

## 陷阱清单(踩过的坑,勿再踩)

- **回放错位**(`ReplayLLM` 报"期望 stage=X 下一条是 Y")= fixture 与流程不一致:
  流程变了(如关联策略/证据编号/复验轮)fixture 必须同步;意见必须覆盖该 claim
  **当时全部**证据(含复验轮、含信封基础字段 E1);
- 证据编号 = 入账顺序(`{event}:E{n}`);信封种子插最前(招标信封先基础字段后检测器);
  改信封字段会平移全部编号,fixture 引用需脚本化平移;
- `SourceReport` 必须带 `message` 字段(远程回执含它,extra=forbid);
- S6 意见 provider 的 retry 必须=0(验票重试只在引擎外层,两层相乘会放大消耗);
- 关联有两档:`--assoc entity`(回放基线)/`entity_text`(live 用,词面重合宽进);
- toutiao 时间解析所有分支必须返回 isoformat 字符串(漏一个 datetime 会炸 pydantic);
- 工具路由的 history 关键词要窄("历史中标"而非"历史"),否则误路由;
- Windows 控制台 GBK:读中文先 `sys.stdout.reconfigure(encoding='utf-8')`;
  CLI 已内置,临时脚本要手加;
- PowerShell 续行符是反引号 ` 不是 ^;用户常被截断命令坑,长命令给单行版;
- 双权威冲突判矛盾(双口径),不得压成已证实;
- 关联/挂账面向**全部** claim(已证实也可被新证据触及复验——矛盾翻转的语义通道);
- egress 有运行内 URL 记忆缓存(探索器换词会反复打同一列表 URL,勿删);
- 同题异文不合并(独立稿件),报告标〔同题 N 稿〕;近重复(simhash)是 P1 待办。

## 运行速查

```powershell
# 离线(供应商B 认识论核心:三成因翻转+H0→H1+REDRAFTED)
python -m sense2act investigate --envelope examples/supplier_b/envelope.json --replay examples/supplier_b/responses.json --seed examples/supplier_b/evidence_seed.json --refresh-plan examples/supplier_b/refresh_plan.json --event-id evt_supplier_b --db data/t.db

# 真模型(需 .env 填 DEEPSEEK_API_KEY;证据仍可用种子)
python -m sense2act investigate --envelope examples/xhs_opensource/envelope.json --live --record runs/recorded/l.json --seed examples/xhs_opensource/evidence_seed.json --event-id evt_x --db data/t.db

# 门禁:--replay 只配 --seed;--crawler 必配 --live;--record-collection 必配 --crawler
```

产物在 `runs/<event_id>/`:report.md / report.html(浏览器看) / result.json / audit.json。

## 红线(不可协商)

验证码对抗/签名逆向/代理池不做;页面改版如实报 parser_changed;坚硬平台
(微博/小红书/抖音/微信正文)不走 S3;browser_auto 只走隔离通道且默认关;
`.env` 与 `runs/`、`data/*.db` 永不入库。
