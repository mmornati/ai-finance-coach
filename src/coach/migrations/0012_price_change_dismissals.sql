-- E4-4: price changes the user does not want to hear about again (the id is a hash of the series and the payment)
CREATE TABLE IF NOT EXISTS price_change_dismissals (
  id TEXT PRIMARY KEY,
  dismissed_at TEXT NOT NULL,
  note TEXT);
