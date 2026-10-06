-- E10 follow-up: when a channel is first seen enabled, the alerts that already exist are marked as sent to it (a baseline) instead of
-- being sent as a backlog. One row per channel, written by the first dispatch that sees the channel enabled. Additive only.
CREATE TABLE IF NOT EXISTS alert_channel_state (
  channel TEXT PRIMARY KEY,
  baselined_at TEXT NOT NULL,
  n_events INTEGER NOT NULL DEFAULT 0);
