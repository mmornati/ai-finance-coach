-- Anthropic Message Batches run in flight (resumable: a re-run after a timeout must not pay twice)
CREATE TABLE IF NOT EXISTS llm_batches (
  fingerprint TEXT PRIMARY KEY, batch_id TEXT NOT NULL, backend TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', jobs INTEGER);
-- 1 = the category of the entity was set by the user (beats LLM/kNN labels of its keys, never user labels or rules)
ALTER TABLE merchant_entities ADD COLUMN category_user INTEGER NOT NULL DEFAULT 0;
