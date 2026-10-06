-- E11-1: the local egress journal. One row per outbound call (or refused attempt): when, which registered path, the destination HOST,
-- how many bytes were sent, the purpose, the redaction mode and the outcome. NEVER a payload, URL path, query, name or token.
-- Additive only; nothing else depends on it.
CREATE TABLE IF NOT EXISTS egress_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,          -- enable_banking | llm.claude-code | llm.anthropic-api | llm.ollama | alerts.* | mcp.finance
  host TEXT NOT NULL DEFAULT '',
  bytes INTEGER NOT NULL DEFAULT 0,
  purpose TEXT NOT NULL DEFAULT '',   -- classify.label, classify.enrich, coach.ask, sync ...
  redaction TEXT NOT NULL DEFAULT '', -- item-redact | doc-redact | analytics-pseudonym | alert-guard | none
  outcome TEXT NOT NULL DEFAULT 'allowed',   -- allowed | denied
  reason TEXT NOT NULL DEFAULT '',    -- policy code of a denial (local_only, offline, web_enrich_off ...)
  web INTEGER NOT NULL DEFAULT 0);    -- 1 when the call let a model search the web
CREATE INDEX IF NOT EXISTS idx_egress_ts ON egress_journal(ts);
CREATE INDEX IF NOT EXISTS idx_egress_kind ON egress_journal(kind, ts);
