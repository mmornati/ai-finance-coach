-- E8-6: what the household decided about a recurring cost (cancelled, renegotiated, switched, downgraded, kept) with the
-- monthly cost before and after, so the realised savings can be counted. Additive only. Whether the recurring series really
-- stopped / changed is checked at read time against the bank data; nothing here is a verdict.
-- state: 'confirmed' rows are counted; a row written by the coach is only 'proposed' until the user confirms it.
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,                       -- dec_<10 hex>
  contract_id TEXT,
  series_id TEXT,                            -- rec_...; at least one of the two refs is set
  name TEXT,                                 -- display name (stays on this machine)
  decision TEXT NOT NULL CHECK (decision IN ('cancelled', 'renegotiated', 'switched', 'downgraded', 'kept')),
  decided_on TEXT NOT NULL,                  -- YYYY-MM-DD
  effective_on TEXT,                         -- YYYY-MM-DD when it takes effect (default: decided_on)
  before_monthly_c INTEGER NOT NULL CHECK (before_monthly_c >= 0),
  after_monthly_c INTEGER NOT NULL CHECK (after_monthly_c >= 0),
  note TEXT,
  source TEXT NOT NULL,                      -- cli | ui | coach-llm
  state TEXT NOT NULL DEFAULT 'confirmed' CHECK (state IN ('proposed', 'confirmed', 'rejected')),
  created_at TEXT NOT NULL,
  confirmed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_decisions_state ON decisions(state, decided_on);
CREATE INDEX IF NOT EXISTS idx_decisions_series ON decisions(series_id);
CREATE INDEX IF NOT EXISTS idx_decisions_contract ON decisions(contract_id);
