# tender_zhongshan_yiqi 金标逐条核对定位表

核对源文件:envelope.json / _detail_fields.json / _detail_body.txt / runs/evt_tender_zhongshan_yiqi/audit.json / runs/evt_tender_zhongshan_yiqi/report.md(附录 B)

## [c1] 已证实(一致)
- claim:信封载明本次信号 kind 为 tender（招标），项目编号为「中大招（货）[2026]209号（代理机构编号：0835-260Z13805421）」
  - 核对源:envelope.kind = tender
  - 核对源:envelope.tender.projectId = 中大招（货）[2026]209号（代理机构编号：0835-260Z13805421）
  - 核对源:_detail_fields.agency = 广东元正招标采购有限公司
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c10] 已证实(**改判**)
- claim:解读选中的假设种子标签为「正常大额采购」与「真实经营动态」
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → selectedSeedLabels = ['正常大额采购', '真实经营动态']
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c11] 已证实(**改判**)
- claim:解读登记的基础缺口包括：该采购单位历史采购记录（amountHistory/noveltyHistory/frequencyHistory）均为空，需先补齐历史采购基线以判断本次预算与频次是否异常
  - 核对源:envelope.tender.amountYuan = 2118000.0 | _detail_fields.budget_yuan = 2118000.0
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c12] 已证实(一致)
- claim:解读登记的基础缺口包括：正文未抓取，仅有列表与详情字段生成的摘要，需先补齐招标文件正文以核实采购需求与投标资格条件
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c13] 已证实(一致)
- claim:本次招标的采购标的为「高分辨显微成像系统」，采购方为中山大学柔性电子学院
  - 核对源:envelope.tender.purchaser = 中山大学
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c14] 已证实(一致)
- claim:本次公告的预算金额 211.8 万元与投标截止时间 2026-11-03 09:30 之间不存在任何历史基线可比对，无法据此判定金额或频次是否异常
  - 核对源:envelope.tender.amountYuan = 2118000.0 | _detail_fields.budget_yuan = 2118000.0
  - 核对源:_detail_fields.deadline = 2026-11-03 09:30
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c2] 已证实(一致)
- claim:信封载明采购方为「中山大学」，项目名称为「中山大学柔性电子学院高分辨显微成像系统采购项目公开招标公告」
  - 核对源:envelope.tender.purchaser = 中山大学
  - 核对源:envelope.tender.projectName = 中山大学柔性电子学院高分辨显微成像系统采购项目公开招标公告…
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c3] 已证实(一致)
- claim:信封载明本项目预算金额为 2118000.0 元（211.8 万元）
  - 核对源:envelope.tender.amountYuan = 2118000.0 | _detail_fields.budget_yuan = 2118000.0
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c4] 已证实(一致)
- claim:信封载明公告发布日期为 2026-10-10，投标截止时间为 2026-11-03 09:30
  - 核对源:envelope.tender.publishedDate = 2026-10-10
  - 核对源:_detail_fields.deadline = 2026-11-03 09:30
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c5] 已证实(一致)
- claim:信封载明代理机构为「广东元正招标采购有限公司」
  - 核对源:_detail_fields.agency = 广东元正招标采购有限公司
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c6] 已证实(一致)
- claim:信封 projectSummary 载明「正文未抓取,摘要由列表与详情字段生成」
  - 核对源:envelope.tender.contentSourceNote = '(空)' | projectSummary 内含「正文未抓取」字样
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [c7] 已证实(**改判**)
- claim:信封载明来源 URL 为 https://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261010_27466373.htm
  - 核对源:envelope.tender.sourceUrl = https://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261010_27466373.htm (该 URL 在 collection_raw.json 中状态 200)
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c8] 已证实(**改判**)
- claim:信封中 amountHistory、noveltyHistory、frequencyHistory 三项历史基线字段均为 null，amountAnomaly、frequencyAnomaly、noveltyAnomaly 三项异常标记字段亦均为 null
  - 核对源:envelope.tender.amountHistory/noveltyHistory/frequencyHistory = None/None/None
  - 核对源:envelope.tender.amountAnomaly/... = None/None/None
  - 系统判决:系统判「待证实」| - | - | 支持根 0 个(不足 2 且非权威登记源) -> 维持待证实 

## [c9] 已证实(一致)
- claim:解读将本信号定性为「商机」，理由为信封 kind 属招标信号且构成潜在投标/合作机会
  - 核对源:envelope.kind = tender
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机';reason 首 60 字 = 信封 kind 为 tender,属招标信号;tender 字段显示采购单位中山大学、项目名称"中山大学柔性电子学院高分…
  - 核对源:解读台账 runs/evt_tender_zhongshan_yiqi/audit.json → decision_log 中 kind='其他' 的 s1a 条目 → signalType = '商机'
  - 系统判决:系统判「已证实」| signal_layer | - | 自证例外(语料/信封内可核对,单根放行) -> 已证实 

## [g-add-1] 已证实(**改判**)
- claim:获取招标文件截止时间为 2026-10-16。
  - 核对源:_detail_fields.signup_deadline = 2026-10-16

## [g-add-2] 已证实(**改判**)
- claim:采购项目联系人为孔嘉欣、龙贵彬、何法岚等,联系电话 020-87258495。
  - 核对源:_detail_fields.contact = 孔嘉欣、龙贵彬、何法岚、华雪怡、齐翔 | phone = 020-87258495-108
