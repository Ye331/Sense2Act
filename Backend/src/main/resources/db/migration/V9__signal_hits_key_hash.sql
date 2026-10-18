-- 信号幂等键改用命中的哈希。原 UNIQUE (document_id, detector, hits) 直接给整列 jsonb 建 B-tree,
-- 单个索引项上限约 2.7KB:命中稍大(如新闻信号带线索原文)整条写入即 500。
-- jsonb 的文本形式已规范化(键排序、去空白),md5(hits::text) 相同即 hits 相同,幂等语义不变。
ALTER TABLE signals DROP CONSTRAINT signals_document_id_detector_hits_key;
CREATE UNIQUE INDEX uniq_signal_natural_key ON signals (document_id, detector, md5(hits::text));
