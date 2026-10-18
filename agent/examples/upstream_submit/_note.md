# _note · upstream_submit(v2 统一提交外格式样例)

- **来源**:《D:\大创参考\信号输出字段与Agent对接格式.md》(2026-10-04,统一提交
  外层 {signal_id, company_id, kind, payload};本文声明"建议新增的接口约定,尚未实现"),
  数值取自早期真实样例以保持连续性;
- **内容**:`tender_sample.json`(单层载荷:procurement_project_id 为调查关联键;
  历史明细上游内部化,依据在 signal_reason 散文)+ `rumor_sample.json`
  (payload 与 RumorSignal 近一一对应);
- **冻结语义**:assemble 装配器 v2 路径的测试 fixture,不得修改;样例中的链接为
  占位(example.org),金额分数仅示格式;
- **消费方式**:`python -m sense2act assemble --batch examples/upstream_submit/tender_sample.json`
  (单条提交容器);`investigate --envelope-batch <文件> --signal-index 0` 同样可直读;
  rumor 的 event_id 由 signal_id 预填(evt_<signal_id>),tender 留空按
  procurement_project_id 派生。
