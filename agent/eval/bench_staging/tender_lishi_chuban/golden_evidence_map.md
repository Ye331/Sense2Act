# tender_lishi_chuban 金标逐条核对定位表

核对源文件:envelope.json / _detail_fields.json / _detail_body.txt / runs/evt_tender_lishi_chuban/audit.json / runs/evt_tender_lishi_chuban/report.md(附录 B)

## [c1] 已证实(一致)
- claim:信封载明本次招标项目编号为 HCZB-2026-ZB0931。
  - 核对源:envelope.tender.projectId = HCZB-2026-ZB0931
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c10] 已证实(一致)
- claim:解读将本信号定性为"商机"，理由为信封 kind 为 tender 且含明确招标项目。
  - 核对源:envelope.kind = tender
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机';reason 首 60 字 = 信封 kind 为 tender,含明确招标项目:projectName 为"中国历史研究院《（新编）中国通史》纂修工程…
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c11] 已证实(**改判**)
- claim:解读选定的假设种子标签为"正常大额采购"与"真实经营动态"。
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → selectedSeedLabels = ['正常大额采购', '真实经营动态']
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c12] 已证实(**改判**)
- claim:信封中 amountHistory、noveltyHistory、frequencyHistory 三项历史基线字段均为 null。
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c13] 已证实(**改判**)
- claim:解读指出该采购单位历史采购记录与同类项目金额基线不足，需先补齐历史基线以判断本次 1684 万元预算是否异常。
  - 核对源:envelope.tender.amountYuan = 16840000.0 | _detail_fields.budget_yuan = 16840000.0
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c14] 已证实(一致)
- claim:解读指出正文未抓取，需补齐招标文件正文以核实服务范围与投标资格要求。
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 核对源:解读台账 runs/evt_tender_lishi_chuban/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c15] 待证实(一致)
- claim:本次招标的采购方"中国历史研究院"与代理机构"华采招标集团有限公司"之间存在招标代理委托关系。
  - 核对源:envelope.tender.purchaser = 中国历史研究院
  - 核对源:_detail_fields.agency = 华采招标集团有限公司
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 系统判决:系统判「待证实」| - | - | - 

## [c2] 已证实(一致)
- claim:信封载明本次招标项目名称为"中国历史研究院《（新编）中国通史》纂修工程出版服务项目公开招标公告"。
  - 核对源:envelope.tender.projectName = 中国历史研究院《（新编）中国通史》纂修工程出版服务项目公开招标公告…
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c3] 已证实(一致)
- claim:信封载明本次招标的采购方为"中国历史研究院"。
  - 核对源:envelope.tender.purchaser = 中国历史研究院
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c4] 已证实(一致)
- claim:信封载明本次招标预算金额为 16840000.0 元（1684 万元）。
  - 核对源:envelope.tender.amountYuan = 16840000.0 | _detail_fields.budget_yuan = 16840000.0
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c5] 已证实(一致)
- claim:信封载明本次招标公告的发布日期为 2026-10-11。
  - 核对源:envelope.tender.publishedDate = 2026-10-11
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c6] 已证实(一致)
- claim:信封载明本次招标的投标截止时间为 2026-11-02 13:30。
  - 核对源:_detail_fields.deadline = 2026-11-02 13:30
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c7] 已证实(一致)
- claim:信封载明本次招标的代理机构为"华采招标集团有限公司"。
  - 核对源:_detail_fields.agency = 华采招标集团有限公司
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c8] 已证实(**改判**)
- claim:信封载明本次招标公告的来源链接为 https://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261011_27467046.htm。
  - 核对源:envelope.tender.sourceUrl = https://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261011_27467046.htm (该 URL 在 collection_raw.json 中状态 200)
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c9] 已证实(一致)
- claim:信封载明本次招标正文未抓取，摘要由列表与详情字段生成。
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [g-add-1] 已证实(**改判**)
- claim:获取招标文件截止时间为 2026-10-16。
  - 核对源:_detail_fields.signup_deadline = 2026-10-16

## [g-add-2] 已证实(**改判**)
- claim:采购项目联系人为姚冲、贾东敏、崔丽洁等,联系电话 010-63509799。
  - 核对源:_detail_fields.contact = 姚冲、贾东敏、崔丽洁、金珊、刘金秀、赵娜、马凯、白敏娜、谭仕 | phone = 010-63509799-8037、8077
