-- Sense2act 持久层(物理口径:5 实表+2 链接表+2 日志+1 视图,快照并入 sections 三列)
-- 跨事件持久: entities/watchlist/source_lineage/entity_aliases;
-- 每事件一份(event_id 区分): events/claims/evidence/sections/hypotheses/
--   hypothesis_claims/claim_entities/claim_transitions/claim_evidence/cohorts;
-- decision_log = 全链路留痕(决策单/卷宗/意见/判决/澄清/钩子)。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS watchlist (
  company_id      TEXT PRIMARY KEY,
  credit_code     TEXT NOT NULL,
  canonical_name  TEXT NOT NULL,
  focus           TEXT DEFAULT '',
  poll_interval_h INTEGER DEFAULT 24,
  last_snapshot_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_lineage (
  domain        TEXT PRIMARY KEY,
  upstream_root TEXT NOT NULL,
  note          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_key      TEXT PRIMARY KEY,      -- NFKC+casefold 归一键
  canonical_name TEXT NOT NULL,
  source         TEXT DEFAULT 'config',
  confirmed      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS entities (
  entity_id      TEXT PRIMARY KEY,      -- ent_ + sha256(规范名/名)[:10]
  name           TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  type           TEXT NOT NULL DEFAULT 'company',   -- company|person|account|organization
  disambiguation TEXT NOT NULL DEFAULT '未锚' CHECK (disambiguation IN ('已锚','未锚','分叉')),
  credit_code    TEXT DEFAULT '',
  anchor_status  TEXT NOT NULL DEFAULT '未锚' CHECK (anchor_status IN ('已锚','未锚','分叉')),
  first_event_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_name);

CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  project_id    TEXT DEFAULT '',        -- 生命周期续查路由键
  kind          TEXT NOT NULL,          -- tender | rumor
  signal_type   TEXT DEFAULT '',
  envelope_json TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);

CREATE TABLE IF NOT EXISTS claims (
  claim_id          TEXT PRIMARY KEY,   -- {event_id}:{draft_id}
  event_id          TEXT NOT NULL,
  draft_id          TEXT NOT NULL,
  text              TEXT NOT NULL,
  category          TEXT NOT NULL CHECK (category IN ('事实','实体','关系','时效','矛盾')),
  status            TEXT NOT NULL DEFAULT '待证实' CHECK (status IN ('待证实','已证实','矛盾')),
  section_id        TEXT NOT NULL,
  priority          INTEGER NOT NULL DEFAULT 3,
  priority_reason   TEXT DEFAULT '',
  created_round     INTEGER NOT NULL DEFAULT 0,
  updated_round     INTEGER NOT NULL DEFAULT 0,
  valid_window_days INTEGER,
  refresh_cycle_days INTEGER,
  relation_type     TEXT,
  parent_claim_id   TEXT,
  unreachable       INTEGER NOT NULL DEFAULT 0,   -- 缺口标不可达(查不到->Open Questions)
  corpus_bound      INTEGER NOT NULL DEFAULT 0,   -- S1b 标注:信封/语料内自证型断言(第一方自证例外的判决输入;模型标注,代码判决)
  FOREIGN KEY (event_id) REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_event_status ON claims(event_id, status);
CREATE INDEX IF NOT EXISTS idx_claims_section ON claims(section_id);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id  TEXT PRIMARY KEY,        -- {event_id}:E{n},按入账顺序(回放可预测)
  event_id     TEXT NOT NULL,
  platform     TEXT DEFAULT '',
  source       TEXT DEFAULT '',
  url          TEXT DEFAULT '',
  title        TEXT DEFAULT '',
  content      TEXT NOT NULL,
  published_at TEXT DEFAULT '',
  fetched_at   TEXT NOT NULL,
  credibility  TEXT DEFAULT '低',       -- 高|中|低(规则表版本化)
  lineage_root TEXT DEFAULT '',         -- 谱系根:S4 入账时打标,S6 只读
  author_type  TEXT DEFAULT '',
  dedup_key    TEXT NOT NULL,           -- NFKC+全半角统一文本哈希+实体id(双键)
  entity_ids   TEXT NOT NULL DEFAULT '[]',  -- 抽取到的实体(JSON 数组;回溯挂账用)
  sensitive    INTEGER NOT NULL DEFAULT 0,  -- 合规脱敏 V1:仅标记
  round        INTEGER NOT NULL DEFAULT 0,
  proj_stage_key TEXT NOT NULL DEFAULT '',  -- 结构化近重复键=项目编号|阶段(空=不参与)
  simhash      TEXT NOT NULL DEFAULT '',    -- 正文 character-bigram simhash(64-bit hex)
  near_dup_of  TEXT NOT NULL DEFAULT '',    -- simhash 近重复指向首条 evidence_id(仅标记,不拒)
  FOREIGN KEY (event_id) REFERENCES events(event_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_dedup ON evidence(event_id, dedup_key);
CREATE INDEX IF NOT EXISTS idx_evidence_root ON evidence(event_id, lineage_root);
-- 注:结构化近重复部分索引(idx_evidence_proj_stage)在 db.connect 迁移逻辑里建
-- (旧库先 ALTER 加列再建索引,顺序见 store/db.py)

CREATE TABLE IF NOT EXISTS sections (
  section_id        TEXT PRIMARY KEY,   -- {event_id}:S{n}
  event_id          TEXT NOT NULL,
  title             TEXT NOT NULL,
  ord               INTEGER NOT NULL,
  kind              TEXT NOT NULL DEFAULT 'standard'
                    CHECK (kind IN ('standard','open_questions','recommendation')),
  state             TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (state IN ('PENDING','STABLE_DRAFTED','STALE','REDRAFTED')),
  snapshot_version  INTEGER NOT NULL DEFAULT 0,
  last_draft_round  INTEGER NOT NULL DEFAULT 0,
  snapshot_json     TEXT NOT NULL DEFAULT '{}',  -- {draft_id: status} 快照(可从 transitions 重算)
  draft_text        TEXT DEFAULT '',
  FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS hypotheses (
  hypothesis_id   TEXT PRIMARY KEY,     -- {event_id}:{H0}
  event_id        TEXT NOT NULL,
  label           TEXT NOT NULL,
  description     TEXT DEFAULT '',
  registered_round INTEGER NOT NULL DEFAULT 0,
  updated_round   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hypothesis_claims (
  hypothesis_id TEXT NOT NULL,
  claim_id      TEXT NOT NULL,
  op            TEXT NOT NULL DEFAULT 'AND' CHECK (op IN ('AND','OR')),
  PRIMARY KEY (hypothesis_id, claim_id)
);

CREATE TABLE IF NOT EXISTS claim_entities (
  claim_id  TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  role      TEXT NOT NULL DEFAULT '普通' CHECK (role IN ('头','尾','普通')),
  PRIMARY KEY (claim_id, entity_id)
);

CREATE TABLE IF NOT EXISTS claim_transitions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_id    TEXT NOT NULL,
  old_status  TEXT NOT NULL,
  new_status  TEXT NOT NULL,
  cause       TEXT CHECK (cause IN ('矛盾翻转','时效翻转','状态刷新')),
              -- 成因三分类属于"翻转";待证实->已证实是澄清而非翻转,记 NULL
  evidence_id TEXT DEFAULT '',
  round       INTEGER NOT NULL,
  ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_claim ON claim_transitions(claim_id);

CREATE TABLE IF NOT EXISTS claim_evidence (
  claim_id    TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  ruling      TEXT CHECK (ruling IN ('支持','矛盾','无关','时效刷新') OR ruling IS NULL),
  round       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS cohorts (           -- 调查圈派生(单事件->群体调查)
  cohort_id     TEXT PRIMARY KEY,
  event_id      TEXT NOT NULL,
  name          TEXT NOT NULL,
  criteria_json TEXT NOT NULL DEFAULT '{}',
  created_round INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cohort_entities (
  cohort_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  PRIMARY KEY (cohort_id, entity_id)
);

CREATE TABLE IF NOT EXISTS decision_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL,
  round        INTEGER NOT NULL DEFAULT 0,
  kind         TEXT NOT NULL CHECK (kind IN
               ('决策单','卷宗','意见','判决','澄清','钩子','入账','刷新','其他')),
  payload_json TEXT NOT NULL,
  degraded     INTEGER NOT NULL DEFAULT 0,
  ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dlog_event ON decision_log(event_id, round);

-- 人答裁决(P1-8,2026-10-09):问人答案落到台账对象的记录(裁决器写,S6/S2 只读)。
-- 不加 decision_log 新 kind(kind CHECK 加值需重建表且旧库崩,成文纪律=并入既有 kind payload);
-- 本表专存结构化裁决,供 standings 覆写与"已裁决分叉不再问"查询。CREATE IF NOT EXISTS 零迁移。
CREATE TABLE IF NOT EXISTS adjudications (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL,
  round        INTEGER NOT NULL,
  target_kind  TEXT NOT NULL CHECK (target_kind IN ('hypothesis','claim','evidence')),
  target_id    TEXT NOT NULL,          -- 全量台账 id {event_id}:H1 / :C1 / :C1R
  choice_text  TEXT NOT NULL,          -- 被选选项原文(否决行=未中选项原文)
  selected     INTEGER NOT NULL DEFAULT 1,  -- 1=该目标被选中;0=候选未中(仅 hypothesis 落否决行)
  ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_adjudications_event ON adjudications(event_id, target_kind);

-- relations = 视图:类目=关系 且 三态=已证实 的 claim 经 claim_entities 头/尾投影(零维护)
CREATE VIEW IF NOT EXISTS relations AS
SELECT
  c.claim_id,
  c.event_id,
  c.relation_type,
  h.entity_id  AS head_entity_id,
  eh.canonical_name AS head_name,
  t.entity_id  AS tail_entity_id,
  et.canonical_name AS tail_name
FROM claims c
JOIN claim_entities h  ON h.claim_id  = c.claim_id AND h.role  = '头'
JOIN entities eh       ON eh.entity_id = h.entity_id
JOIN claim_entities t  ON t.claim_id  = c.claim_id AND t.role  = '尾'
JOIN entities et       ON et.entity_id = t.entity_id
WHERE c.category = '关系' AND c.status = '已证实';
