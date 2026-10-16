-- needl-service SQLite schema, version 1.
-- Timestamps are UTC ISO 8601 strings.

CREATE TABLE IF NOT EXISTS verifications (
  id            TEXT PRIMARY KEY,
  source_kind   TEXT NOT NULL CHECK (source_kind IN ('url', 'screenshots')),
  canonical_url TEXT,
  name          TEXT NOT NULL,
  price         TEXT NOT NULL,
  store_name    TEXT,
  trust_score   INTEGER NOT NULL,
  risk_level    TEXT NOT NULL CHECK (risk_level IN ('safe', 'suspicious', 'fraud')),
  finding_count INTEGER NOT NULL,
  result_json   TEXT NOT NULL,
  checked_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_url ON verifications (canonical_url, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_verifications_time ON verifications (checked_at DESC, id);

CREATE TABLE IF NOT EXISTS verification_jobs (
  id              TEXT PRIMARY KEY,
  status          TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  progress        INTEGER NOT NULL DEFAULT 0,
  input_kind      TEXT NOT NULL CHECK (input_kind IN ('url', 'screenshots')),
  input_url       TEXT,
  canonical_url   TEXT,
  input_json      TEXT NOT NULL,
  locale          TEXT NOT NULL,
  preview_json    TEXT,
  verification_id TEXT REFERENCES verifications (id),
  from_cache      INTEGER NOT NULL DEFAULT 0,
  error_code      TEXT,
  error_message   TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON verification_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  key          TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL,
  job_id       TEXT NOT NULL REFERENCES verification_jobs (id),
  created_at   TEXT NOT NULL
);
