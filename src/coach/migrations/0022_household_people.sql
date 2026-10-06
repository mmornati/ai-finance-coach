-- E14: people. Additive only: new tables, nothing existing is touched.
--
-- tx_person: a MANUAL attribution of one transaction to a household member (or to 'joint' = the household as a whole). Absent row =
-- the memory rules and then the account owner decide. Removing the row restores them (reversible); every change is logged below.
CREATE TABLE IF NOT EXISTS tx_person (
  tx_key TEXT PRIMARY KEY,
  member TEXT NOT NULL,                 -- a household member id, or 'joint'
  set_at TEXT NOT NULL,
  set_by TEXT NOT NULL,                 -- who: 'cli', 'ui:<user>' ...
  note TEXT);
CREATE TABLE IF NOT EXISTS tx_person_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_key TEXT NOT NULL,
  old_member TEXT,                      -- NULL = there was no manual attribution
  new_member TEXT,                      -- NULL = the manual attribution was removed
  action TEXT NOT NULL,                 -- set | clear | revert
  at TEXT NOT NULL,
  by TEXT NOT NULL,
  note TEXT);
CREATE INDEX IF NOT EXISTS idx_tx_person_log_tx ON tx_person_log(tx_key, id);

-- ui_users: per-person logins of the web app (E14-8). Created and changed ONLY from the terminal (`coach users ...`).
-- role adult = all data; role child = the own data of member_id only (enforced server-side on every endpoint).
CREATE TABLE IF NOT EXISTS ui_users (
  id TEXT PRIMARY KEY,
  member_id TEXT,                       -- the household member this login is (required for a child)
  role TEXT NOT NULL CHECK (role IN ('adult', 'child')),
  created_at TEXT NOT NULL,
  disabled_at TEXT,
  prefs TEXT NOT NULL DEFAULT '{}');    -- JSON, whitelisted keys (locale, theme, default_member ...)

-- audit_log: who made which change through the web app. Method, endpoint, status and the actor: never a payload.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  status INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at);
