-- E2: richer parser output, LLM usage log, canonical merchants, transaction splits, tx_key remap log.
-- Additive only. tx_enriched keeps its 7 columns; the extra parser fields live in tx_parse_meta.

-- extra fields returned by the per-bank parsers (E2-2)
CREATE TABLE IF NOT EXISTS tx_parse_meta (
  tx_key TEXT PRIMARY KEY, parser TEXT, counterparty TEXT, mandate_ref TEXT, creditor_id TEXT, reference TEXT);

-- one row per LLM call (E2-6, E12-4)
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, backend TEXT NOT NULL, model TEXT, purpose TEXT,
  items INTEGER, tokens_in INTEGER, tokens_out INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  cost_usd REAL, cost_is_estimate INTEGER NOT NULL DEFAULT 1, duration_s REAL);

-- canonical merchants (E2-12)
CREATE TABLE IF NOT EXISTS merchant_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, norm_name TEXT NOT NULL, category TEXT,
  source TEXT NOT NULL DEFAULT 'auto', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_entities_norm ON merchant_entities(norm_name);
CREATE TABLE IF NOT EXISTS merchant_aliases (
  merchant_key TEXT PRIMARY KEY, entity_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'auto',
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON merchant_aliases(entity_id);

-- splits (E2-13): the parts of one transaction; amounts sum exactly to the transaction amount
CREATE TABLE IF NOT EXISTS tx_splits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tx_key TEXT NOT NULL, amount REAL NOT NULL, category TEXT NOT NULL,
  note TEXT);
CREATE INDEX IF NOT EXISTS idx_splits_tx ON tx_splits(tx_key);

-- tx_key changes (H1 re-keying of position-based references): lets memory annotations that still list an
-- old key keep matching, and tells the user which ones to update.
CREATE TABLE IF NOT EXISTS tx_key_remap (
  old_key TEXT PRIMARY KEY, new_key TEXT NOT NULL, reason TEXT, remapped_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_remap_new ON tx_key_remap(new_key);
