-- V6:反馈与看板(E5)+ 顺延项 E2-6 规则版本管理 / E2-7 全局通知流。
-- 表结构见 docs/data-model.md §2/§4。signal_rules 复合主键 (id, version),
-- "同 id 只有一个版本 enabled" 用部分唯一索引兜底(应用层先停旧再启新)。

-- 信号规则(版本化):id 在版本间不变,rule_ + ULID(5+26=31 字符,列宽取 32;契约示例 "rule_01H..." 即此口径)
CREATE TABLE signal_rules (
    id              VARCHAR(32)  NOT NULL,
    version         INT          NOT NULL,
    name            VARCHAR(200) NOT NULL,
    type            VARCHAR(50)  NOT NULL CHECK (type IN ('amount_anomaly', 'frequency_burst', 'semantic_match', 'composite')),
    params          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    weight          NUMERIC(4,3) NOT NULL DEFAULT 0.500,
    enabled         BOOLEAN      NOT NULL DEFAULT false,
    backtest_result JSONB,                         -- 回测指标,外部评估写回(E5-4,S8)
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

-- 同 id 至多一个版本 enabled(部分唯一索引:NULL 语义替代,这里用布尔过滤)
CREATE UNIQUE INDEX uniq_signal_rules_enabled ON signal_rules (id) WHERE enabled;

-- 种子:三条默认规则(参数口径与 api-design §8 示例一致;语义匹配引用种子画像"医疗AI")
INSERT INTO signal_rules (id, version, name, type, params, weight, enabled) VALUES
    ('rule_seed01', 1, '采购金额异常', 'amount_anomaly',
     '{"k": 2.0, "min_samples": 5, "window_days": 730}', 0.800, true),
    ('rule_seed02', 1, '采购频率突增', 'frequency_burst',
     '{"burst_ratio": 3.0, "window_days": 30, "min_docs": 5}', 0.600, true),
    ('rule_seed03', 1, '语义匹配-医疗AI', 'semantic_match',
     '{"profile": "医疗AI", "threshold": 0.78}', 0.700, true);

-- 全局通知流的可追溯存储(E2-7):SSE id 即本表 id,重连带 Last-Event-ID 从此补发
CREATE TABLE global_events (
    id         BIGSERIAL PRIMARY KEY,
    event      VARCHAR(50) NOT NULL,              -- signal_created / investigation_completed / report_ready / source_degraded
    payload    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 报告级反馈需要记录评语(E5-1):action 词表增补 'comment'(纯增量,旧值不受影响)
ALTER TABLE feedback_events DROP CONSTRAINT feedback_events_action_check;
ALTER TABLE feedback_events ADD CONSTRAINT feedback_events_action_check
    CHECK (action IN ('confirm', 'dismiss', 'adopt', 'reject', 'flag_false_positive', 'comment'));
