-- E4: derived analytics results kept between runs (recurring series, anomalies with their dismissal).
-- Additive only. Everything here can be rebuilt from transactions + memory: `coach recurring refresh`,
-- `coach anomalies refresh`. Amounts are integer cents.

-- one row per detected recurring series. id is stable: hash of direction, group key, account and cadence
CREATE TABLE IF NOT EXISTS recurring_series (
  id TEXT PRIMARY KEY,
  direction TEXT NOT NULL,            -- out | in
  kind TEXT NOT NULL,                 -- expense | income | transfer | saving | inflow
  group_key TEXT NOT NULL,            -- entity / merchant key / SEPA creditor id the occurrences share
  account_uid TEXT,
  entity TEXT,
  category TEXT,
  cadence TEXT NOT NULL,              -- weekly | biweekly | monthly | bimonthly | quarterly | semiannual | yearly
  amount_mode TEXT NOT NULL,          -- fixed | variable
  n_occurrences INTEGER NOT NULL,
  first_date TEXT NOT NULL,
  last_date TEXT NOT NULL,
  next_expected TEXT,
  expected_amount_c INTEGER NOT NULL,
  amount_low_c INTEGER NOT NULL,
  amount_high_c INTEGER NOT NULL,
  yearly_cost_c INTEGER NOT NULL,
  status TEXT NOT NULL,               -- active | ended
  confidence REAL NOT NULL,
  payload TEXT NOT NULL,              -- full JSON of the series (links, evidence...)
  first_detected_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_recurring_status ON recurring_series(status, account_uid);

-- the transactions of each series (evidence, price-change history)
CREATE TABLE IF NOT EXISTS recurring_members (
  series_id TEXT NOT NULL,
  tx_key TEXT NOT NULL,
  date TEXT NOT NULL,
  amount_c INTEGER NOT NULL,
  PRIMARY KEY (series_id, tx_key));
CREATE INDEX IF NOT EXISTS idx_recurring_members_tx ON recurring_members(tx_key);

-- anomalies found by the last refresh. dismissed_at is set by `coach anomalies dismiss` and survives refreshes
CREATE TABLE IF NOT EXISTS anomalies (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  subject TEXT,
  period TEXT,
  amount_c INTEGER,
  payload TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  dismissed_at TEXT,
  dismiss_note TEXT);
CREATE INDEX IF NOT EXISTS idx_anomalies_dismissed ON anomalies(dismissed_at);
