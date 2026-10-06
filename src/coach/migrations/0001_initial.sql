-- Initial schema = the prototype schema of eb.py (ingest) and classify.py (classification).
-- IF NOT EXISTS keeps it idempotent and lossless on an existing prototype database.

-- ingest
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, aspsp_name TEXT, aspsp_country TEXT,
  valid_until TEXT, created_at TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS accounts (
  uid TEXT PRIMARY KEY, session_id TEXT, name TEXT, iban TEXT, currency TEXT,
  cash_account_type TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS transactions (
  tx_key TEXT PRIMARY KEY,            -- entry_reference or fallback fingerprint
  account_uid TEXT, entry_reference TEXT, booking_date TEXT, value_date TEXT,
  amount REAL, currency TEXT, counterparty TEXT, description TEXT,
  mcc TEXT, bank_tx_code TEXT, raw TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS pending_transactions (
  account_uid TEXT, booking_date TEXT, amount REAL, currency TEXT,
  counterparty TEXT, description TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS balances (
  account_uid TEXT, fetched_at TEXT, balance_type TEXT, amount REAL, currency TEXT,
  reference_date TEXT);
CREATE TABLE IF NOT EXISTS sync_log (
  account_uid TEXT, ran_at TEXT, ok INTEGER, new_tx INTEGER, pages INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS pending_auth (
  state TEXT PRIMARY KEY, aspsp_name TEXT, aspsp_country TEXT, created_at TEXT);

-- classification
CREATE TABLE IF NOT EXISTS tx_enriched (
  tx_key TEXT PRIMARY KEY, tx_type TEXT, op_date TEXT, merchant_raw TEXT,
  merchant_key TEXT, fx_amount REAL, fx_currency TEXT);
CREATE TABLE IF NOT EXISTS merchants (
  merchant_key TEXT PRIMARY KEY, merchant_name TEXT, category TEXT, confidence REAL,
  recurring_hint INTEGER, source TEXT, model TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tx_overrides (tx_key TEXT PRIMARY KEY, category TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS merchant_eval (
  merchant_key TEXT, model TEXT, category TEXT, confidence REAL, run_at TEXT,
  PRIMARY KEY (merchant_key, model));
