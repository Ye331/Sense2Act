"""SQLite 连接与初始化(stdlib sqlite3,无 ORM)。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    try:   # 旧库平滑迁移(CREATE IF NOT EXISTS 不改已存在的表;列已在则静默跳过)
        conn.execute("ALTER TABLE claims ADD COLUMN"
                     " corpus_bound INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # 近重复去重三列迁移(2026-10-05 加 simhash/结构化键;旧 evidence 表补列,幂等)
    for ddl in (
        "ALTER TABLE evidence ADD COLUMN proj_stage_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE evidence ADD COLUMN simhash TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE evidence ADD COLUMN near_dup_of TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass    # 列已存在(新建库 schema 已含)则跳过
    # 结构化近重复部分唯一索引(须在补列之后建;旧库 executescript 阶段列不存在)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_proj_stage"
            " ON evidence(event_id, proj_stage_key) WHERE proj_stage_key != ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """显式事务:块内异常回滚(台账写入的唯一管线语义)。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
