-- 心理科普短片策划平台 数据库模式
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  publisher TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_versions (          -- 来源的不可变版本，导出成果固定引用
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(source_id, version)
);
CREATE TABLE IF NOT EXISTS reviewers (                -- 审阅人
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reviewer',
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  audience TEXT,                                      -- 适用人群
  expression_limits TEXT,                             -- 表述限制
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_sources (
  project_id INTEGER NOT NULL REFERENCES projects(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  PRIMARY KEY (project_id, source_id)
);
CREATE TABLE IF NOT EXISTS characters (               -- 角色（支持替换）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL,
  replaced_by INTEGER REFERENCES characters(id),
  replaced_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (                 -- 片段：字幕/旁白/角色台词
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  position INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('subtitle','narration','character_line')),
  character_id INTEGER REFERENCES characters(id),
  source_id INTEGER REFERENCES sources(id),
  source_version INTEGER,
  revision_no INTEGER NOT NULL DEFAULT 1,
  text TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segment_revisions (        -- 片段修订历史
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id INTEGER NOT NULL REFERENCES segments(id),
  revision_no INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(segment_id, revision_no)
);
CREATE TABLE IF NOT EXISTS glossary_terms (           -- 词表（命中仅为复核线索）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT 'general',
  note TEXT
);
CREATE TABLE IF NOT EXISTS help_channels (            -- 求助渠道：地区/有效期/核验依据
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  region TEXT NOT NULL,
  contact TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  verification_basis TEXT NOT NULL,
  maintainer TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_channels (
  project_id INTEGER NOT NULL REFERENCES projects(id),
  channel_id INTEGER NOT NULL REFERENCES help_channels(id),
  PRIMARY KEY (project_id, channel_id)
);
CREATE TABLE IF NOT EXISTS snapshots (                -- 审阅快照：字幕/旁白/角色台词共用
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  context_hash TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (                  -- 审阅：段落级 / 整片
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  reviewer_id INTEGER NOT NULL REFERENCES reviewers(id),
  scope TEXT NOT NULL CHECK(scope IN ('segment','whole')),
  segment_id INTEGER REFERENCES segments(id),
  revision_no INTEGER,
  context_prev INTEGER,                               -- 批准时的前后文（含邻段修订号）
  context_prev_rev INTEGER,
  context_next INTEGER,
  context_next_rev INTEGER,
  decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','needs_changes')),
  comment_internal TEXT,                              -- 内部意见：不进入公开包
  comment_public TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn')),
  created_at TEXT NOT NULL,
  withdrawn_at TEXT
);
CREATE TABLE IF NOT EXISTS exports (                  -- 导出：已排队/就绪/失效/已发布
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','ready','invalidated','published')),
  invalid_reason TEXT,
  package_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_grants (          -- 可撤销的站内下载权限
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  export_id INTEGER NOT NULL REFERENCES exports(id),
  user_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  UNIQUE(export_id, user_token)
);
CREATE TABLE IF NOT EXISTS publish_requests (         -- 发布请求：幂等去重
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  export_id INTEGER NOT NULL REFERENCES exports(id),
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'accepted',
  created_at TEXT NOT NULL,
  UNIQUE(project_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS jobs (                     -- 工作器任务
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done','failed')),
  created_at TEXT NOT NULL,
  processed_at TEXT
);
