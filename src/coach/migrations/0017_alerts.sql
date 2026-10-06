-- E10: alert events, their deliveries and the per-kind preferences. Additive only; nothing else reads or depends on it.
-- An event has a STABLE id (alr_ + a hash of its dedupe key, so no merchant / account name is in it): the engine upserts by id,
-- so the same signal is one event however often the check runs. `payload` is structured and stays on this machine; an external
-- channel never gets it (see coach.alerts.messages). `channels_sent` records, per channel, the severity it was last sent at: an
-- event is sent again to a channel only when its severity goes UP (escalation).
CREATE TABLE IF NOT EXISTS alert_events (
  id TEXT PRIMARY KEY,                       -- alr_<12 hex>
  kind TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  last_seen TEXT NOT NULL,                   -- the last check that still saw the signal
  resolved_at TEXT,                          -- the signal disappeared (kept as history)
  title TEXT NOT NULL,                       -- local wording: may name a merchant or a bank
  body TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',        -- JSON: structured, local
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'sent', 'acked', 'snoozed', 'suppressed')),
  snoozed_until TEXT,                        -- YYYY-MM-DD
  acked_at TEXT,
  channels_sent TEXT NOT NULL DEFAULT '{}',  -- JSON {channel: {"at": iso, "severity": "high"}}
  escalations INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_alert_events_status ON alert_events(status, severity);
CREATE INDEX IF NOT EXISTS idx_alert_events_kind ON alert_events(kind, created);

-- One row per message a channel was handed (rate limit: rows of the last 7 days; a digest is sent once per channel).
CREATE TABLE IF NOT EXISTS alert_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sent_at TEXT NOT NULL,
  channel TEXT NOT NULL,
  what TEXT NOT NULL CHECK (what IN ('alert', 'digest', 'test')),
  ref TEXT,                                  -- the digest's insight id
  n_events INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT);                               -- the exception class only, never a message that could hold a secret
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_channel ON alert_deliveries(channel, sent_at);

-- Runtime noise control (`coach alerts mute-kind`, `snooze <kind>`, the web app): the config file holds the defaults.
CREATE TABLE IF NOT EXISTS alert_kind_prefs (
  kind TEXT PRIMARY KEY,
  muted INTEGER NOT NULL DEFAULT 0,
  snoozed_until TEXT,
  updated TEXT NOT NULL);
