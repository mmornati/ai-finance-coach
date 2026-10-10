-- E16: passkeys (WebAuthn) of the web app. Additive only. A row is one credential of one login: the credential's PUBLIC key (the private
-- key never leaves the person's device), the signature counter, the relying-party id it was made for and the name the person gave it.
-- 'owner' is the login of the person at the machine (a session without a user); any other login is a row of ui_users.
CREATE TABLE IF NOT EXISTS ui_passkeys (
  id TEXT PRIMARY KEY,                  -- credential id, base64url
  login TEXT NOT NULL,                  -- 'owner' or a ui_users.id
  public_key BLOB NOT NULL,             -- COSE public key as the authenticator returned it
  sign_count INTEGER NOT NULL DEFAULT 0,
  transports TEXT NOT NULL DEFAULT '[]',   -- JSON list of the transports the browser reported
  rp_id TEXT NOT NULL,                  -- the host name the passkey is bound to
  label TEXT NOT NULL,                  -- the name the person gave ("my phone")
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  backed_up INTEGER NOT NULL DEFAULT 0);   -- the authenticator says the credential is synced (a passkey) rather than device-bound
CREATE INDEX IF NOT EXISTS idx_ui_passkeys_login ON ui_passkeys(login);
