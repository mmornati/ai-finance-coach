-- E8-4: cheaper alternatives found for a recurring service (by the find-cheaper skill or typed by the user), each with the
-- source URL and the date the price was SEEN. Additive only. A quote older than 30 days is shown as "outdated, re-check" and
-- is never the "current best" (computed at read time; nothing here is a verdict). Prices are integer cents.
CREATE TABLE IF NOT EXISTS alternatives (
  id TEXT PRIMARY KEY,                       -- alt_<10 hex>
  contract_id TEXT,                          -- the memory contract the offer is an alternative to ...
  series_id TEXT,                            -- ... and/or the recurring series (rec_...); at least one is set
  provider TEXT NOT NULL,
  offer_name TEXT NOT NULL,
  monthly_price_c INTEGER NOT NULL CHECK (monthly_price_c > 0),
  switching_costs_c INTEGER NOT NULL DEFAULT 0 CHECK (switching_costs_c >= 0),
  features TEXT,                             -- short summary of what the offer includes
  source_url TEXT,                           -- https page the price was read on
  retrieved_at TEXT NOT NULL,                -- YYYY-MM-DD: when the price was seen
  method TEXT NOT NULL CHECK (method IN ('find-cheaper', 'manual')),
  notes TEXT,
  source TEXT NOT NULL,                      -- who wrote the row: cli | ui | coach-llm
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_alternatives_contract ON alternatives(contract_id);
CREATE INDEX IF NOT EXISTS idx_alternatives_series ON alternatives(series_id);
