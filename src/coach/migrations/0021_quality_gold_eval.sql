-- E12-2 / E12-3 / E12-5: the gold set (transactions whose category the USER has confirmed) and every evaluation run.
-- Additive only. gold_labels never changes a category of the pipeline: it is read by `coach eval ...` and the "Gold set" page.
CREATE TABLE IF NOT EXISTS gold_labels (
  tx_key TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  labeled_by TEXT NOT NULL CHECK (labeled_by IN ('user', 'memory', 'correction')),
  labeled_at TEXT NOT NULL,
  note TEXT,
  origin TEXT NOT NULL DEFAULT 'manual'        -- manual | merchant_label | annotation | override | split  (where the truth came from)
);
CREATE INDEX IF NOT EXISTS idx_gold_origin ON gold_labels(labeled_by, origin);

CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,                          -- classify | models | coach
  label TEXT,                                  -- model alias, 'offline', 'live' ...
  backend TEXT,
  model TEXT,
  n INTEGER NOT NULL DEFAULT 0,                -- rows scored
  fingerprint TEXT,                            -- hash of what the result depends on (taxonomy, rules, prompt, gold set)
  cost_usd REAL,
  duration_s REAL,
  summary TEXT NOT NULL DEFAULT '{}',          -- JSON: the headline numbers
  result TEXT NOT NULL DEFAULT '{}');          -- JSON: the full result
CREATE INDEX IF NOT EXISTS idx_eval_runs_kind ON eval_runs(kind, ts);
