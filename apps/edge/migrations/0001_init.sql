-- Users sign in with a one-time email link. Times are unix seconds.
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  locale TEXT NOT NULL DEFAULT 'he',
  created_at INTEGER NOT NULL
);

CREATE TABLE login_tokens (
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  locale TEXT NOT NULL DEFAULT 'he',
  return_to TEXT,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);

CREATE TABLE sessions (
  id_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);

-- status: queued -> starting -> running -> done | failed; done -> expired after retention.
CREATE TABLE scans (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  building_id INTEGER NOT NULL,
  address_json TEXT NOT NULL,
  building_json TEXT NOT NULL,
  label_he TEXT NOT NULL,
  label_en TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'web',
  status TEXT NOT NULL,
  stages_json TEXT NOT NULL DEFAULT '{}',
  summary_json TEXT,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  cached_from TEXT,
  queued_at INTEGER NOT NULL,
  dispatched_at INTEGER,
  last_progress_at INTEGER,
  finished_at INTEGER,
  expires_at INTEGER
);
CREATE INDEX scans_status ON scans(status, queued_at);
CREATE INDEX scans_user ON scans(user_id, queued_at);
CREATE INDEX scans_building ON scans(building_id, status, expires_at);

-- One row per scan that counted against a quota (cached reuse does not).
CREATE TABLE quota_events (
  scan_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX quota_user ON quota_events(user_id, created_at);
CREATE INDEX quota_time ON quota_events(created_at);
