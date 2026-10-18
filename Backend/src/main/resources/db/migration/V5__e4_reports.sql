-- V5:证据与报告(E4)。表结构见 docs/data-model.md §2。
-- reports.event_id 与 events.created_by_report 互指,FK 分两步建(先表后约束)。

-- 主体(跨报告沉淀):name 唯一,重复抽取按名复用(ON CONFLICT DO NOTHING)
CREATE TABLE entities (
    id         VARCHAR(30) PRIMARY KEY,          -- ent_ + ULID
    name       VARCHAR(500) NOT NULL,
    type       VARCHAR(20)  NOT NULL CHECK (type IN ('org', 'project', 'product', 'region', 'policy')),
    ref_org_id VARCHAR(30) REFERENCES organizations(id),
    aliases    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- 事件:title/type 来自 ReportDraft.event_extraction;created_by_report 等 reports 建好后补 FK
CREATE TABLE events (
    id                VARCHAR(30) PRIMARY KEY,   -- evt_ + ULID
    title             VARCHAR(500) NOT NULL,
    type              VARCHAR(50)  NOT NULL,     -- construction_wave/policy/procurement 等开放词表
    summary           VARCHAR(2000),
    start_date        DATE,
    status            VARCHAR(20) NOT NULL DEFAULT 'ongoing' CHECK (status IN ('ongoing', 'closed', 'speculative')),
    created_by_report VARCHAR(30),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reports (
    id               VARCHAR(30) PRIMARY KEY,    -- rep_ + ULID
    investigation_id VARCHAR(30) NOT NULL UNIQUE REFERENCES investigations(id),
    signal_id        VARCHAR(30) REFERENCES signals(id),
    event_id         VARCHAR(30) REFERENCES events(id),
    title            VARCHAR(500) NOT NULL,
    summary          VARCHAR(2000),
    status           VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    meta             JSONB NOT NULL,             -- D5:轮次/工具数/token/费用,后端从 steps 计算,不信任报告自报
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE events ADD FOREIGN KEY (created_by_report) REFERENCES reports(id);

CREATE INDEX idx_reports_created ON reports (created_at);

-- 结论:seq 为报告内顺序,UNIQUE(report_id, seq)
CREATE TABLE claims (
    id         VARCHAR(30) PRIMARY KEY,          -- cl_ + ULID
    report_id  VARCHAR(30) NOT NULL REFERENCES reports(id),
    seq        INT NOT NULL,
    text       TEXT NOT NULL,
    nature     VARCHAR(20) NOT NULL CHECK (nature IN ('fact', 'inference', 'speculation')),
    confidence NUMERIC(4,3) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_id, seq)
);

CREATE INDEX idx_claims_report ON claims (report_id);

-- claim ↔ evidence 多对多;ReportDraft 的 claims 只带 evidence_ids,relation 先统一落"直接依据"
CREATE TABLE evidence_links (
    claim_id    VARCHAR(30) NOT NULL REFERENCES claims(id),
    evidence_id VARCHAR(30) NOT NULL REFERENCES evidences(id),
    relation    VARCHAR(20) NOT NULL DEFAULT '直接依据' CHECK (relation IN ('直接依据', '背景', '互证')),
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE action_suggestions (
    id         VARCHAR(30) PRIMARY KEY,          -- as_ + ULID
    report_id  VARCHAR(30) NOT NULL REFERENCES reports(id),
    text       VARCHAR(1000) NOT NULL,
    priority   VARCHAR(10) NOT NULL DEFAULT 'medium' CHECK (priority IN ('high', 'medium', 'low')),
    adopted_at TIMESTAMPTZ,                      -- 反馈回填(E5)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_suggestions_report ON action_suggestions (report_id);

-- 事件-主体关联
CREATE TABLE event_entities (
    event_id  VARCHAR(30) NOT NULL REFERENCES events(id),
    entity_id VARCHAR(30) NOT NULL REFERENCES entities(id),
    role      VARCHAR(20) NOT NULL CHECK (role IN ('participant', 'object', 'scope', 'beneficiary')),
    since     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, entity_id)
);

CREATE INDEX idx_event_entities_entity ON event_entities (entity_id);

-- 事件关系:节点可为 event/entity/evidence(ReportDraft 引用形如 "entity:0"/"event")
CREATE TABLE event_relations (
    id          VARCHAR(30) PRIMARY KEY,         -- er_ + ULID
    source_type VARCHAR(10) NOT NULL CHECK (source_type IN ('event', 'entity', 'evidence')),
    source_id   VARCHAR(30) NOT NULL,
    target_type VARCHAR(10) NOT NULL CHECK (target_type IN ('event', 'entity', 'evidence')),
    target_id   VARCHAR(30) NOT NULL,
    relation    VARCHAR(10) NOT NULL CHECK (relation IN ('前置', '参与', '佐证', '互证', '同属')),
    evidence_id VARCHAR(30) REFERENCES evidences(id),
    since       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, target_type, target_id, relation)
);

CREATE INDEX idx_event_relations_source ON event_relations (source_type, source_id);
CREATE INDEX idx_event_relations_target ON event_relations (target_type, target_id);
