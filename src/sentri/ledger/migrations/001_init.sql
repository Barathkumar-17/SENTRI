CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  commit_sha TEXT, branch TEXT,
  app_fingerprint TEXT,
  contract_hash TEXT,
  started_at TEXT, finished_at TEXT,
  status TEXT CHECK (status IN ('running','finished','aborted')),
  readiness INTEGER,
  duration_s REAL, llm_cost_usd REAL DEFAULT 0,
  llm_mode TEXT CHECK (llm_mode IN ('llm','fallback','mixed')),
  breakdown TEXT
);

CREATE TABLE checks (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  flow_id TEXT NOT NULL, attack_id TEXT NOT NULL,
  verdict TEXT CHECK (verdict IN ('pass','fail','flaky','na','error')),
  score_verdict TEXT CHECK (score_verdict IN ('pass','fail','flaky','na','error')),
  oracle_hits TEXT,
  attempts INTEGER, payload TEXT,
  payload_source TEXT CHECK (payload_source IN ('llm','static','none')),
  reason TEXT,
  error_msg TEXT, screenshot_path TEXT,
  evidence_path TEXT,
  duplicate_method TEXT,
  is_probe INTEGER NOT NULL DEFAULT 0,
  reused_from_check_id INTEGER REFERENCES checks(id),
  duration_s REAL
);
CREATE INDEX checks_run ON checks(run_id);
CREATE INDEX checks_cell ON checks(flow_id, attack_id);

CREATE TABLE bugs (
  id TEXT PRIMARY KEY,
  flow_id TEXT, attack_id TEXT,
  oracle_id TEXT,
  signature TEXT UNIQUE,
  status TEXT CHECK (status IN ('open','fixed','flaky','intended')),
  first_commit TEXT, fixed_commit TEXT, last_seen_commit TEXT,
  regressions INTEGER DEFAULT 0, human_note TEXT,
  related_bug_id TEXT REFERENCES bugs(id),
  needs_review INTEGER NOT NULL DEFAULT 0,
  sample_detail TEXT
);

CREATE TABLE bug_hits (
  bug_id TEXT REFERENCES bugs(id), check_id INTEGER REFERENCES checks(id), k INTEGER,
  PRIMARY KEY (bug_id, check_id)
);

CREATE TABLE bug_history (
  id INTEGER PRIMARY KEY, bug_id TEXT REFERENCES bugs(id), ts TEXT,
  from_status TEXT, to_status TEXT, commit_sha TEXT, run_id INTEGER,
  actor TEXT CHECK (actor IN ('system','human')), note TEXT
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES runs(id), ts TEXT, kind TEXT, message TEXT, reason TEXT,
  data TEXT
);
CREATE INDEX events_run ON events(run_id, id);
