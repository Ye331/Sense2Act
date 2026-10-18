# tender_linlu_linye 金标逐条核对定位表

核对源文件:envelope.json / _detail_fields.json / _detail_body.txt / runs/evt_tender_linlu_linye/audit.json / runs/evt_tender_linlu_linye/report.md(附录 B)

## [c1] 已证实(一致)
- claim:信封载明本次信号 kind 为 tender，即公开招标公告。
  - 核对源:envelope.kind = tender
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c10] 已证实(一致)
- claim:信封载明正文未抓取，摘要由列表与详情字段生成。
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c11] 已证实(**改判**)
- claim:信封中 amountHistory、noveltyHistory、frequencyHistory 三项历史字段均为空（null）。
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c12] 已证实(**改判**)
- claim:信封中 amountAnomaly、frequencyAnomaly、noveltyAnomaly 三项异常标记字段均为空（null）。
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c13] 已证实(一致)
- claim:解读将本信号定性为“商机”，理由为信封 kind 属公开招标公告，构成潜在投标/合作机会。
  - 核对源:envelope.kind = tender
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机';reason 首 60 字 = 信封 kind 为 tender,属公开招标公告:项目名称“海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示…
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c14] 已证实(**改判**)
- claim:解读选中的假设种子标签为“正常大额采购”与“真实经营动态”。
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → selectedSeedLabels = ['正常大额采购', '真实经营动态']
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c15] 已证实(一致)
- claim:解读登记的基础缺口包括：正文未抓取，需先补齐招标文件正文与具体服务范围、资格条件。
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c16] 已证实(**改判**)
- claim:解读登记的基础缺口包括：该采购单位历史采购记录（amountHistory/noveltyHistory/frequencyHistory）均为空，需先补齐历史采购基线以判断本次预算与频次是否异常。
  - 核对源:envelope.tender.amountYuan = 2461952.0 | _detail_fields.budget_yuan = 2461952.0
  - 核对源:解读台账 runs/evt_tender_linlu_linye/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c17] 已证实(一致)
- claim:本次采购的代理机构“海标工程管理有限公司”与项目名称前缀中的“海标工程管理有限公司”为同一名称主体。
  - 核对源:envelope.tender.projectName = 海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目（桐庐县）-2027至2028年作业设计和2026至2…
  - 核对源:_detail_fields.agency = 海标工程管理有限公司
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c18] 已证实(一致)
- claim:桐庐县林业技术推广中心与海标工程管理有限公司之间存在本次招标代理委托关系。
  - 核对源:正文原文命中:…海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目…
  - 系统判决:系统判「已证实」| ccgp | - | 权威登记源单源例外 -> 已证实 

## [c19] 已证实(一致)
- claim:本次采购项目名称显示服务内容包含 2027 至 2028 年作业设计，以及 2026 至 2028 年检查验收及绩效评价。
  - 核对源:envelope.tender.projectName = 海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目（桐庐县）-2027至2028年作业设计和2026至2…
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c2] 已证实(一致)
- claim:信封载明项目编号为 330122261030090000005。
  - 核对源:envelope.tender.projectId = 330122261030090000005
  - 系统判决:系统判「已证实」| ccgp, signal_layer | - | 独立谱系根 2 个(>=2) -> 已证实 

## [c20] 已证实(一致)
- claim:本次采购项目名称显示其属于“杭州市‘三生共荣’中央财政国土绿化示范项目（桐庐县）”的组成部分。
  - 核对源:envelope.tender.projectName = 海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目（桐庐县）-2027至2028年作业设计和2026至2…
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c21] 已证实(一致)
- claim:本次采购预算 246.195 万元与投标截止 2026-11-02 09:30 之间的时间窗口为自公告发布日 2026-10-11 起 22 天。
  - 核对源:envelope.tender.amountYuan = 2461952.0 | _detail_fields.budget_yuan = 2461952.0
  - 核对源:_detail_fields.deadline = 2026-11-02 09:30
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c3] 已证实(一致)
- claim:信封载明项目名称为“海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目（桐庐县）-2027至2028年作业设计和2026至2028年检查验收及绩效评价项目的公开招标公告”。
  - 核对源:envelope.tender.projectName = 海标工程管理有限公司关于杭州市“三生共荣”中央财政国土绿化示范项目（桐庐县）-2027至2028年作业设计和2026至2…
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c4] 已证实(一致)
- claim:信封载明采购方为桐庐县林业技术推广中心。
  - 核对源:envelope.tender.purchaser = 桐庐县林业技术推广中心
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c5] 已证实(一致)
- claim:信封载明本次采购预算金额为 2461952.0 元（246.195 万元）。
  - 核对源:envelope.tender.amountYuan = 2461952.0 | _detail_fields.budget_yuan = 2461952.0
  - 系统判决:系统判「已证实」| ccgp, signal_layer | - | 独立谱系根 2 个(>=2) -> 已证实 

## [c6] 已证实(一致)
- claim:信封载明公告发布日期为 2026-10-11。
  - 核对源:envelope.tender.publishedDate = 2026-10-11
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c7] 已证实(一致)
- claim:信封载明投标截止时间为 2026-11-02 09:30。
  - 核对源:_detail_fields.deadline = 2026-11-02 09:30
  - 系统判决:系统判「已证实」| ccgp, signal_layer | - | 独立谱系根 2 个(>=2) -> 已证实 

## [c8] 已证实(一致)
- claim:信封载明代理机构为海标工程管理有限公司。
  - 核对源:_detail_fields.agency = 海标工程管理有限公司
  - 系统判决:系统判「已证实」| ccgp, signal_layer | - | 独立谱系根 2 个(>=2) -> 已证实 

## [c9] 已证实(**改判**)
- claim:信封载明来源链接为 https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261011_27467396.htm。
  - 核对源:envelope.tender.sourceUrl = https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261011_27467396.htm (该 URL 在 collection_raw.json 中状态 200)
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [g-add-1] 已证实(**改判**)
- claim:获取招标文件截止时间为 2026-11-02。
  - 核对源:_detail_fields.signup_deadline = 2026-11-02

## [g-add-2] 已证实(**改判**)
- claim:采购项目联系人樊袁烽,联系电话 13588383174。
  - 核对源:_detail_fields.contact = 樊袁烽 | phone = 13588383174
