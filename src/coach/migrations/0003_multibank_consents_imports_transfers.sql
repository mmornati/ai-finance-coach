-- E1-7..E1-12: consent lifecycle, multi-bank accounts, file imports, internal transfers.
-- Additive only: no existing column is touched, existing rows keep working (defaults below).

-- sessions: lifecycle. status is what WE decided (active | replaced | revoked); the bank's live status
-- is cached in live_status. "expired" is derived from valid_until / live_status, never stored.
ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE sessions ADD COLUMN replaced_by TEXT;
ALTER TABLE sessions ADD COLUMN live_status TEXT;
ALTER TABLE sessions ADD COLUMN live_checked_at TEXT;

-- accounts: `uid` stays the STABLE internal id that transactions/balances/sync_log refer to.
-- `api_uid` is the uid the current Enable Banking session uses in API calls (NULL = same as uid); it differs
-- after a reconnect when the bank returned a new uid for the same IBAN.
ALTER TABLE accounts ADD COLUMN bank TEXT;
ALTER TABLE accounts ADD COLUMN owner TEXT;               -- 'joint' or a household member
ALTER TABLE accounts ADD COLUMN purpose TEXT;             -- main | cards | rental | kids | savings
ALTER TABLE accounts ADD COLUMN label TEXT;
ALTER TABLE accounts ADD COLUMN exclude INTEGER NOT NULL DEFAULT 0;   -- 1 = left out of analytics
ALTER TABLE accounts ADD COLUMN source TEXT NOT NULL DEFAULT 'api';   -- api | import
ALTER TABLE accounts ADD COLUMN api_uid TEXT;
ALTER TABLE accounts ADD COLUMN identification_hash TEXT;             -- sha256 of the normalised IBAN
UPDATE accounts SET bank = (SELECT aspsp_name FROM sessions s WHERE s.session_id = accounts.session_id)
  WHERE bank IS NULL;

-- pending_auth: which session a reconnect replaces, and whether the redirect was already consumed.
ALTER TABLE pending_auth ADD COLUMN replaces_session_id TEXT;
ALTER TABLE pending_auth ADD COLUMN completed_at TEXT;

-- consent warnings already notified (one notification per session and threshold)
CREATE TABLE IF NOT EXISTS consent_alerts (
  session_id TEXT NOT NULL, threshold TEXT NOT NULL, notified_at TEXT NOT NULL,
  PRIMARY KEY (session_id, threshold));

-- file imports: one row per imported file, and the tx_keys it created
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_uid TEXT NOT NULL, file_name TEXT, file_sha256 TEXT,
  format TEXT, profile TEXT, imported_at TEXT NOT NULL, rows_total INTEGER, rows_new INTEGER);
CREATE TABLE IF NOT EXISTS import_rows (tx_key TEXT PRIMARY KEY, import_id INTEGER NOT NULL);

-- internal transfers: a debit leg and a credit leg of the household's own accounts
CREATE TABLE IF NOT EXISTS transfer_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  out_tx_key TEXT NOT NULL UNIQUE, in_tx_key TEXT NOT NULL UNIQUE,
  amount REAL NOT NULL, confidence REAL NOT NULL, method TEXT NOT NULL,   -- method: auto | manual
  created_at TEXT NOT NULL);
-- pairs the user unlinked: the matcher never proposes them again
CREATE TABLE IF NOT EXISTS transfer_rejections (
  out_tx_key TEXT NOT NULL, in_tx_key TEXT NOT NULL, rejected_at TEXT NOT NULL,
  PRIMARY KEY (out_tx_key, in_tx_key));

CREATE INDEX IF NOT EXISTS idx_accounts_session ON accounts(session_id);
CREATE INDEX IF NOT EXISTS idx_accounts_ident ON accounts(identification_hash);
