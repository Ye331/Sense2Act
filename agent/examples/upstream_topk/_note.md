# _note · agent_top_k_samples.json 冻结说明

- **来源**:`D:\大创参考\agent_top_k_samples.json`(上游信号发现层 trigger_agent 的
  Top-K 批量导出样例,schema_version 0.1-draft),2026-10-03 原样字节复制(md5 f0811b9c64bd);
- **内容**:5 条真实 tender 信号(project/scores/historical_context/trigger/sources/
  scoring_context 六块),医疗设备采购域;
- **冻结语义**:本文件是 assemble 装配器与 `investigate --envelope-batch` 的测试 fixture,
  不得修改;上游格式演进时应新增新版本文件而非就地改;
- **消费方式**:`python -m sense2act assemble --batch examples/upstream_topk/agent_top_k_samples.json`;
  装配产物落 `runs/assembled/`(不入库);过程量(priority_score/weights/daily_rank/
  reasons 等)按"上游置信不继承"纪律不进信封,只在 manifest 留档。
