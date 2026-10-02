-- V2:采集与文档库(E1)。表结构见 docs/data-model.md §2。
-- documents.embedding 列先建好(决策 D12,S3 经 EMBEDDING_ENDPOINT 补算),实体暂不映射该列。

CREATE TABLE sources (
    id                  VARCHAR(30)  PRIMARY KEY,            -- src_ + ULID
    name                VARCHAR(200) NOT NULL,
    type                VARCHAR(20)  NOT NULL CHECK (type IN ('web_page', 'api', 'rss')),
    url                 VARCHAR(1000) NOT NULL,
    adapter             VARCHAR(50),
    schedule_cron       VARCHAR(100),                        -- Spring cron 六段式;空 = 仅手动触发
    config              JSONB        NOT NULL DEFAULT '{}', -- 适配器私有配置,原样存取
    enabled             BOOLEAN      NOT NULL DEFAULT true,
    health              VARCHAR(20)  NOT NULL DEFAULT 'ok' CHECK (health IN ('ok', 'degraded', 'down')),
    last_run_at         TIMESTAMPTZ,                         -- NULL = 从未跑过,视为到期(D2 手动触发即置空)
    last_run_status     VARCHAR(20),                         -- ok / failed
    consecutive_failures INT          NOT NULL DEFAULT 0,    -- D3 降级阈值用
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE organizations (
    id         VARCHAR(30)  PRIMARY KEY,                     -- org_ + ULID
    name       VARCHAR(200) NOT NULL UNIQUE,                 -- 规范化名(全半角统一、空白折叠)
    aliases    JSONB        NOT NULL DEFAULT '[]',           -- 归一别名,匹配时 @> 命中即视为同一机构
    type       VARCHAR(20)  CHECK (type IN ('buyer', 'supplier', 'gov')),
    uscc       VARCHAR(50),
    region     VARCHAR(100),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id             VARCHAR(30)  PRIMARY KEY,                  -- doc_ + ULID
    source_id      VARCHAR(30)  REFERENCES sources(id),
    doc_type       VARCHAR(20)  NOT NULL CHECK (doc_type IN ('announcement', 'policy', 'news')),
    title          VARCHAR(500) NOT NULL,
    content_text   TEXT,
    org_id         VARCHAR(30)  REFERENCES organizations(id), -- 采购单位,可空
    amount         NUMERIC(18,2),                             -- 元,可空
    publish_date   DATE,
    deadline       DATE,
    region         VARCHAR(100),
    category       VARCHAR(100),
    url            VARCHAR(1000) NOT NULL UNIQUE,             -- 归一化后唯一
    raw            JSONB,                                     -- 源站原始字段(除 raw_html 外整个条目)
    content_hash   VARCHAR(64)  NOT NULL UNIQUE,              -- SHA-256,内容去重键
    snapshot_key   VARCHAR(200),                              -- raw_html 快照文件键
    embedding      vector(512),                               -- D12:S3 补算
    signal_scanned BOOLEAN      NOT NULL DEFAULT false,       -- 检测幂等位(E2)
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_documents_type_date        ON documents (doc_type, publish_date DESC);
CREATE INDEX idx_documents_org_date         ON documents (org_id, publish_date DESC);
CREATE INDEX idx_documents_region_category  ON documents (region, category);
CREATE INDEX idx_documents_fts              ON documents USING GIN (to_tsvector('simple', title || ' ' || coalesce(content_text, '')));
CREATE INDEX idx_documents_embedding_hnsw   ON documents USING hnsw (embedding vector_cosine_ops);

CREATE TABLE org_stats (
    org_id        VARCHAR(30)  NOT NULL REFERENCES organizations(id),
    category      VARCHAR(100) NOT NULL,
    window_days   INT          NOT NULL DEFAULT 730,
    sample_count  INT          NOT NULL DEFAULT 0,
    amount_mean   NUMERIC(18,2),
    amount_std    NUMERIC(18,2),
    amount_p95    NUMERIC(18,2),
    freq_mean_30d NUMERIC(10,4),
    last_doc_at   DATE,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, category)
);

CREATE TABLE ingest_errors (
    id          VARCHAR(30)  PRIMARY KEY,                     -- ier_ + ULID
    source_id   VARCHAR(30)  NOT NULL REFERENCES sources(id),
    url         VARCHAR(1000),
    stage       VARCHAR(20)  NOT NULL CHECK (stage IN ('fetch', 'normalize', 'dedup', 'persist')),
    error       TEXT,
    occurred_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_ingest_errors_source ON ingest_errors (source_id, occurred_at DESC);
