-- V4:调查生命周期(E3)。表结构见 docs/data-model.md §2。
-- evidences 表随本迁移先行建表(E4 再加登记 API):E3-4 的 questions.evidence_ids 引用校验需要真实存在的目标表。

-- 步骤留痕:seq 即 SSE 事件 id,UNIQUE(investigation_id, seq) 兜底"单调不重"。
CREATE TABLE investigation_steps (
    id               VARCHAR(30) PRIMARY KEY,          -- ist_ + ULID
    investigation_id VARCHAR(30) NOT NULL REFERENCES investigations(id),
    seq              INT         NOT NULL,             -- 从 1 起单调递增
    round            INT         NOT NULL DEFAULT 0,
    type             VARCHAR(20) NOT NULL CHECK (type IN ('question', 'tool_select', 'tool_call', 'reflection', 'status_change')),
    content          JSONB       NOT NULL,             -- {event, ...载荷},SSE 按 content.event 还原事件名
    token_usage      INT         NOT NULL DEFAULT 0,   -- 该步骤消耗的 token,累加进 investigations.token_used
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (investigation_id, seq)
);

CREATE INDEX idx_steps_investigation ON investigation_steps (investigation_id, seq);

-- 待确认问题:Agent 提问、PATCH 推进状态;evidence_ids 引用 evidences(E3-4 校验归属同调查)
CREATE TABLE questions (
    id               VARCHAR(30) PRIMARY KEY,          -- q_ + ULID
    investigation_id VARCHAR(30) NOT NULL REFERENCES investigations(id),
    text             VARCHAR(1000) NOT NULL,
    raised_in_round  INT         NOT NULL DEFAULT 1,
    status           VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'clarified', 'unresolved', 'abandoned')),
    answer_summary   VARCHAR(2000),
    evidence_ids     JSONB,                             -- ["ev_..."],引用本调查已登记证据
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_questions_investigation ON questions (investigation_id);

-- 证据(E4 的登记 API 落地;E3 先建表供引用校验)
CREATE TABLE evidences (
    id               VARCHAR(30) PRIMARY KEY,           -- ev_ + ULID
    source_type      VARCHAR(30) NOT NULL,              -- announcement/policy/news/company_record/web/internal_stat
    title            VARCHAR(500),
    url              VARCHAR(1000),
    excerpt          TEXT,
    published_at     TIMESTAMPTZ,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash     VARCHAR(64),
    snapshot_key     VARCHAR(200),
    doc_id           VARCHAR(30) REFERENCES documents(id),
    investigation_id VARCHAR(30) NOT NULL REFERENCES investigations(id)
);

CREATE INDEX idx_evidences_investigation ON evidences (investigation_id);
