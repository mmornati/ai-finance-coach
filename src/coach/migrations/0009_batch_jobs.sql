-- One row per request of an Anthropic Message Batch: which merchants it asked about, and whether its results were
-- stored. A batch is collected from this mapping (custom_id -> merchants), never from a prompt fingerprint.
CREATE TABLE IF NOT EXISTS llm_batch_jobs (
  batch_id TEXT NOT NULL, custom_id TEXT NOT NULL, keys_json TEXT NOT NULL, model TEXT, purpose TEXT, items INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending | stored | failed
  error TEXT, PRIMARY KEY (batch_id, custom_id));
