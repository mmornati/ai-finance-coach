-- E6-7: traceable coach insights. One row per finding / answer / digest written by the coach runtime (or by Claude
-- Code through the finance MCP server's `add_insight`). Additive only; nothing else reads or depends on it.
-- Numbers are never computed by the model: `findings` / `body` figures come from tool results of the same session, and
-- the ones that could not be traced there are listed in `unverified_numbers`.
CREATE TABLE IF NOT EXISTS insights (
  id TEXT PRIMARY KEY,                     -- cin_<10 hex>
  created TEXT NOT NULL,
  kind TEXT NOT NULL,                      -- digest | answer | anomaly-explain | finding | ...
  title TEXT NOT NULL,
  body TEXT NOT NULL,                      -- markdown
  findings TEXT NOT NULL DEFAULT '[]',     -- JSON: structured findings
  evidence TEXT NOT NULL DEFAULT '[]',     -- JSON list of refs (h_ transaction hashes, rec_/anm_/chg_ ids, categories)
  skill TEXT,                              -- skill / prompt id (digest-weekly, ask, ...)
  backend TEXT,
  model TEXT,
  usage_ref INTEGER,                       -- llm_usage.id of the run that produced it
  session_id TEXT,                         -- the tool session (one MCP server process / one job)
  status TEXT NOT NULL DEFAULT 'new',      -- new | read | dismissed | done | snoozed
  snoozed_until TEXT,
  unverified_numbers TEXT NOT NULL DEFAULT '[]',   -- JSON list of numbers not found in any tool result of the session
  redacted_content INTEGER NOT NULL DEFAULT 0,   -- the privacy filter masked part of the text the model wrote
  suspicious INTEGER NOT NULL DEFAULT 0,   -- instruction-like text was seen in tool data during the session
  question TEXT,                           -- the user's question (kind = answer)
  data_through TEXT);                      -- data fingerprint at the time (digests: "no new data" check)
CREATE INDEX IF NOT EXISTS idx_insights_status ON insights(status, created);
CREATE INDEX IF NOT EXISTS idx_insights_kind ON insights(kind, created);
CREATE INDEX IF NOT EXISTS idx_insights_session ON insights(session_id);
