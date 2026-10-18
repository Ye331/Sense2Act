-- V7:S8 缓冲项(E5-4 回测任务)。评估在外部执行:后端建任务、出数据集、存结果。
CREATE TABLE backtest_runs (
    id         VARCHAR(30) PRIMARY KEY,      -- bt_ + ULID
    rule_id    VARCHAR(32) NOT NULL,         -- 引 signal_rules 的 id(版本间同 id;不设 FK,历史版本行不删)
    param_grid JSONB       NOT NULL DEFAULT '{}'::jsonb,
    date_from  DATE        NOT NULL,
    date_to    DATE        NOT NULL,
    status     VARCHAR(20) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    result     JSONB,                        -- 外部评估写回:{precision, recall, evaluated_at, ...}
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (date_to >= date_from)
);

CREATE INDEX idx_backtests_rule ON backtest_runs (rule_id, created_at DESC);
