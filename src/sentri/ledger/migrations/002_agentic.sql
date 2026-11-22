-- Agentic mode: app map from recon, the strategy, every exploration step, and findings with repro steps.

CREATE TABLE app_maps (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES runs(id),
  created_at TEXT,
  data TEXT                              -- AppMap JSON
);

CREATE TABLE strategies (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES runs(id),
  created_at TEXT,
  source TEXT,                           -- llm | none
  data TEXT                              -- Strategy JSON (app kind, sensitive areas, hypotheses)
);

CREATE TABLE exploration_steps (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES runs(id),
  step INTEGER,                          -- 1-based order within the run
  parent_step INTEGER,                   -- previous step on the same path (attack tree)
  hypothesis_id TEXT,
  identity TEXT,
  thought TEXT,                          -- the model's reasoning, display only
  action TEXT,                           -- validated action JSON
  action_text TEXT,                      -- one-line description
  ok INTEGER,
  error TEXT,
  url_after TEXT,
  signals TEXT,                          -- JSON: oracles that fired on this step (plain code)
  ts TEXT
);
CREATE INDEX exploration_steps_run ON exploration_steps(run_id, step);

CREATE TABLE findings (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES runs(id),
  hypothesis_id TEXT,
  title TEXT,
  oracle_ids TEXT,
  repro TEXT,                            -- JSON list of actions (with identities) that reproduce it
  from_step INTEGER,
  to_step INTEGER,
  verdict TEXT CHECK (verdict IN ('fail','flaky','unconfirmed','pass')),
  attempts INTEGER,
  check_id INTEGER REFERENCES checks(id),
  created_at TEXT
);
CREATE INDEX findings_run ON findings(run_id);
