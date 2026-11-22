-- Payment provider settlements (E1-12 extension): a bank debit funding a payment
-- on an intermediate provider (PayPal, Lydia...).
-- funding_tx_key: the bank debit (treated as internal transfer / excluded from spending)
-- settled_tx_key: the provider transaction (retains its real merchant and category)
CREATE TABLE IF NOT EXISTS settlement_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  funding_tx_key TEXT NOT NULL UNIQUE,
  settled_tx_key TEXT NOT NULL UNIQUE,
  amount REAL NOT NULL,
  confidence REAL NOT NULL,
  method TEXT NOT NULL,   -- auto | manual
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlement_rejections (
  funding_tx_key TEXT NOT NULL,
  settled_tx_key TEXT NOT NULL,
  rejected_at TEXT NOT NULL,
  PRIMARY KEY (funding_tx_key, settled_tx_key)
);

CREATE INDEX IF NOT EXISTS idx_settlement_links_funding ON settlement_links(funding_tx_key);
CREATE INDEX IF NOT EXISTS idx_settlement_links_settled ON settlement_links(settled_tx_key);

