-- Haifa (docs/HAIFA_PLAN.md §4). Scans get a city and a text building key ("tel-aviv:<id_binyan>",
-- "haifa:<street code>:<house number>"); building_id becomes Tel Aviv only. New status "filling": a Haifa scan that
-- is usable while its remaining documents arrive. SQLite can't relax NOT NULL in place, so the table is rebuilt.
PRAGMA defer_foreign_keys = true;

CREATE TABLE scans_new (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  city TEXT NOT NULL DEFAULT 'tel-aviv',
  building_key TEXT NOT NULL,
  building_id INTEGER,
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

INSERT INTO scans_new (
  id, user_id, city, building_key, building_id, address_json, building_json, label_he, label_en, origin, status,
  stages_json, summary_json, error, attempts, cached_from, queued_at, dispatched_at, last_progress_at, finished_at,
  expires_at
)
SELECT
  id, user_id, 'tel-aviv', 'tel-aviv:' || building_id, building_id, address_json, building_json, label_he, label_en,
  origin, status, stages_json, summary_json, error, attempts, cached_from, queued_at, dispatched_at, last_progress_at,
  finished_at, expires_at
FROM scans;

DROP TABLE scans;
ALTER TABLE scans_new RENAME TO scans;

CREATE INDEX scans_status ON scans(status, queued_at);
CREATE INDEX scans_user ON scans(user_id, queued_at);
CREATE INDEX scans_building ON scans(building_key, status, expires_at);
CREATE INDEX scans_city ON scans(city, status, queued_at);

-- One row per Haifa archive document still to fetch (or fetched). requested_at: someone opened it, so it goes first.
CREATE TABLE haifa_documents (
  scan_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  guid TEXT,
  priority INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | ready | failed | skipped
  requested_at INTEGER,
  done_at INTEGER,
  PRIMARY KEY (scan_id, doc_id)
);
CREATE INDEX haifa_documents_queue ON haifa_documents(status, requested_at, priority);

-- "complot": cool-down after a 429 and the current pace. "haifa-worker": when the worker was last heard from.
CREATE TABLE source_state (
  source TEXT PRIMARY KEY,
  blocked_until INTEGER,
  interval_s REAL,
  slow_until INTEGER,
  last_seen INTEGER,
  reason TEXT
);
