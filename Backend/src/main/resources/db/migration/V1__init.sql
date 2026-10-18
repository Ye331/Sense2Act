-- V1:pgvector 扩展 + users 表。表按史诗分批落迁移(决策 D8),结构见 docs/data-model.md。
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id            VARCHAR(30)  PRIMARY KEY,               -- 带前缀 ULID:usr_ + 26 位
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,                  -- {pbkdf2}... 格式,决策 D11
    name          VARCHAR(100) NOT NULL,
    role          VARCHAR(20)  NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer'))
);
