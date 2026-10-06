-- E9-4: monthly history of the net worth. One row per (day, source): 'snapshot' rows are written at each sync / schedule run
-- (the whole net worth of that day), 'backfill' rows are month-end figures rebuilt for the past from the bank balances and the
-- transactions (bank accounts only), from the amortization schedules (liabilities that are computable) and from the manual
-- assets (only from their own as_of date on). A figure that cannot be known for a past month is counted in n_unknown, never
-- as zero. Additive only; nothing else reads or depends on it.
CREATE TABLE IF NOT EXISTS net_worth_history (
  as_of TEXT NOT NULL,                      -- YYYY-MM-DD (the run day for a snapshot, the month end for a backfill)
  month TEXT NOT NULL,                      -- YYYY-MM
  source TEXT NOT NULL CHECK (source IN ('snapshot', 'backfill')),
  net_worth_c INTEGER NOT NULL,             -- known assets - known liabilities (cents)
  assets_c INTEGER NOT NULL,
  liabilities_c INTEGER NOT NULL,
  cash_c INTEGER NOT NULL DEFAULT 0,
  savings_c INTEGER NOT NULL DEFAULT 0,
  investments_c INTEGER NOT NULL DEFAULT 0,
  real_estate_c INTEGER NOT NULL DEFAULT 0,
  vehicles_c INTEGER NOT NULL DEFAULT 0,
  other_c INTEGER NOT NULL DEFAULT 0,
  n_unknown INTEGER NOT NULL DEFAULT 0,     -- items whose value is not known for that day (not counted)
  complete INTEGER NOT NULL DEFAULT 0,      -- 1 when n_unknown = 0
  detail TEXT NOT NULL DEFAULT '{}',        -- JSON: by_owner and the unknown items {type, id, label, reason}
  created_at TEXT NOT NULL,
  PRIMARY KEY (as_of, source));
CREATE INDEX IF NOT EXISTS idx_nwh_month ON net_worth_history(month, as_of);
