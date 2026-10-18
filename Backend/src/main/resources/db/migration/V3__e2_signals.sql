-- V3:信号通道(E2)。表结构见 docs/data-model.md §2。
-- signal_rules 表随 E2-6(规则版本管理,S5/S8)再建;signals.hits 为 JSONB,不依赖 FK。
-- investigations 表本冲刺只写 created(E2-4 自动触发),状态机全集 E3 落地。

-- 关注画像(D13:单独建表,不放 policies 的 JSONB)
CREATE TABLE watch_profiles (
    id         VARCHAR(30)  PRIMARY KEY,                     -- wpr_ + ULID
    name       VARCHAR(100) NOT NULL UNIQUE,                 -- 如"医疗AI",semantic_match 的 detail.profile 引用
    enabled    BOOLEAN      NOT NULL DEFAULT true,
    note       VARCHAR(500),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 调查策略:单行配置表(id 恒为 1),admin GET/PUT
CREATE TABLE investigation_policies (
    id                            INT          PRIMARY KEY CHECK (id = 1),
    auto_investigate_threshold    NUMERIC(4,3) NOT NULL DEFAULT 0.85,
    max_concurrent_investigations INT          NOT NULL DEFAULT 3,
    default_max_rounds            INT          NOT NULL DEFAULT 8,
    default_token_budget          INT          NOT NULL DEFAULT 60000,
    updated_at                    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

INSERT INTO investigation_policies (id) VALUES (1);

-- 演示用默认画像(api-design §9.2 示例口径)
INSERT INTO watch_profiles (id, name, enabled, note) VALUES
    ('wpr_seed01', '医疗AI', true, 'AI辅助诊断、影像AI、临床决策支持类采购'),
    ('wpr_seed02', '数据平台', true, '数据中台、大数据平台、科研数据服务'),
    ('wpr_seed03', '医疗网络安全', true, '等保测评、安全整改、数据安全');

CREATE TABLE investigations (
    id            VARCHAR(30)  PRIMARY KEY,                   -- inv_ + ULID
    signal_id     VARCHAR(30)  NOT NULL UNIQUE,               -- 一信号至多一调查,唯一约束兜底并发(E2-4)
    status        VARCHAR(20)  NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'investigating', 'reporting', 'completed', 'failed', 'stopped')),
    current_round INT          NOT NULL DEFAULT 0,
    max_rounds    INT          NOT NULL DEFAULT 8,
    token_budget  INT          NOT NULL DEFAULT 60000,
    token_used    INT          NOT NULL DEFAULT 0,
    cost_estimate NUMERIC(12,4) NOT NULL DEFAULT 0,
    judgment      TEXT,
    error         TEXT,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_investigations_status_created ON investigations (status, created_at);

CREATE TABLE signals (
    id               VARCHAR(30)  PRIMARY KEY,               -- sig_ + ULID
    document_id      VARCHAR(30)  NOT NULL REFERENCES documents(id),
    org_id           VARCHAR(30)  REFERENCES organizations(id),
    detector         VARCHAR(100) NOT NULL,                  -- 推送方标识,幂等键的一部分
    hits             JSONB        NOT NULL,                  -- [{rule_id,rule_version,rule_type,weight,detail}],原样落库
    score            NUMERIC(4,3) NOT NULL,                  -- 外部产出,后端只消费不重算
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'investigating', 'confirmed', 'dismissed')),
    investigation_id VARCHAR(30)  REFERENCES investigations(id),
    decided_by       VARCHAR(30)  REFERENCES users(id),
    decided_at       TIMESTAMPTZ,
    decision_reason  VARCHAR(500),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- 幂等:同 document + detector + hits 重复推,命中即返回已有 signal_id(jsonb 相等忽略键序)
    UNIQUE (document_id, detector, hits)
);

CREATE INDEX idx_signals_status_created ON signals (status, created_at DESC);
CREATE INDEX idx_signals_org            ON signals (org_id);
CREATE INDEX idx_signals_score          ON signals (score DESC);

CREATE TABLE feedback_events (
    id          VARCHAR(30)  PRIMARY KEY,                     -- fbe_ + ULID
    user_id     VARCHAR(30)  NOT NULL REFERENCES users(id),
    target_type VARCHAR(20)  NOT NULL CHECK (target_type IN ('signal', 'report', 'claim', 'suggestion')),
    target_id   VARCHAR(30)  NOT NULL,
    action      VARCHAR(30)  NOT NULL CHECK (action IN ('confirm', 'dismiss', 'adopt', 'reject', 'flag_false_positive')),
    reason      VARCHAR(1000),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_feedback_target ON feedback_events (target_type, target_id);
