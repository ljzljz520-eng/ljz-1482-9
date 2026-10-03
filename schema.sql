PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  publisher TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_versions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  publication_date TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  archived_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(source_id, version)
);
CREATE TABLE IF NOT EXISTS reviewers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reviewer',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS help_channels (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL,
  contact TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS help_channel_versions (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES help_channels(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  contact TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  verification_basis TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(channel_id, version)
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  professional_content TEXT NOT NULL DEFAULT '',
  intended_audience TEXT NOT NULL DEFAULT '',
  expression_constraints TEXT NOT NULL DEFAULT '[]',
  source_version_id TEXT REFERENCES source_versions(id) ON DELETE SET NULL,
  channel_version_id TEXT REFERENCES help_channel_versions(id) ON DELETE SET NULL,
  current_snapshot_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  replaced_by TEXT REFERENCES characters(id) ON DELETE SET NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK(kind IN ('subtitle','narration','dialogue','title','note')),
  position INTEGER NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  context_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segment_revisions (
  id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  reviewer_id TEXT REFERENCES reviewers(id) ON DELETE SET NULL,
  old_text TEXT NOT NULL DEFAULT '',
  new_text TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_snapshots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  source_version_id TEXT REFERENCES source_versions(id) ON DELETE SET NULL,
  channel_version_id TEXT REFERENCES help_channel_versions(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  superseded_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshot_segments (
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  segment_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  kind TEXT NOT NULL,
  character_id TEXT,
  text TEXT NOT NULL DEFAULT '',
  context_note TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, segment_id)
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  reviewer_id TEXT NOT NULL REFERENCES reviewers(id) ON DELETE RESTRICT,
  scope TEXT NOT NULL CHECK(scope IN ('segment','whole')),
  segment_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('approved','changes_requested','withdrawn')),
  internal_comment TEXT NOT NULL DEFAULT '',
  context_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  withdrawn_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_lookup ON reviews(snapshot_id, scope, segment_id, status);
CREATE TABLE IF NOT EXISTS review_invalidations (
  id TEXT PRIMARY KEY,
  review_id TEXT REFERENCES reviews(id) ON DELETE CASCADE,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  rule_code TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vocabularies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vocabulary_terms (
  id TEXT PRIMARY KEY,
  vocabulary_id TEXT NOT NULL REFERENCES vocabularies(id) ON DELETE CASCADE,
  term TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(vocabulary_id, term)
);
CREATE TABLE IF NOT EXISTS vocabulary_hits (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  segment_id TEXT,
  term TEXT NOT NULL,
  vocabulary_id TEXT REFERENCES vocabularies(id) ON DELETE SET NULL,
  matched_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  job_type TEXT NOT NULL CHECK(job_type IN ('script_preview','material_list')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','canceled')),
  request_key TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  claimed_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotent_open
ON jobs(snapshot_id, job_type, request_key)
WHERE status IN ('queued','running','completed');
CREATE TABLE IF NOT EXISTS exports (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed','revoked')),
  package_hash TEXT,
  package_path TEXT,
  request_key TEXT NOT NULL DEFAULT 'default',
  readiness_json TEXT NOT NULL DEFAULT '{}',
  failure_reason TEXT,
  created_at TEXT NOT NULL,
  ready_at TEXT,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS download_grants (
  token TEXT PRIMARY KEY,
  export_id TEXT NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
  granted_to TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT,
  created_at TEXT,
  revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_download_grant
ON download_grants(export_id, granted_to)
WHERE active = 1;
CREATE TABLE IF NOT EXISTS publications (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id) ON DELETE CASCADE,
  export_id TEXT REFERENCES exports(id) ON DELETE SET NULL,
  channel_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('queued','published','duplicate_rejected','invalidated','canceled')),
  request_key TEXT NOT NULL,
  result_json TEXT,
  created_at TEXT NOT NULL,
  published_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_dedup
ON publications(project_id, snapshot_id, channel_name, request_key)
WHERE status IN ('queued','published');
