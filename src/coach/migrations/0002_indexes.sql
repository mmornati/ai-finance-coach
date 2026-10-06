-- Indexes for the access paths used by sync, classification and analytics.
CREATE INDEX IF NOT EXISTS idx_transactions_account_date ON transactions(account_uid, booking_date);
CREATE INDEX IF NOT EXISTS idx_transactions_booking_date ON transactions(booking_date);
CREATE INDEX IF NOT EXISTS idx_tx_enriched_merchant_key ON tx_enriched(merchant_key);
CREATE INDEX IF NOT EXISTS idx_tx_enriched_tx_type ON tx_enriched(tx_type);
CREATE INDEX IF NOT EXISTS idx_sync_log_account_ran ON sync_log(account_uid, ran_at);
CREATE INDEX IF NOT EXISTS idx_balances_account_fetched ON balances(account_uid, fetched_at);
CREATE INDEX IF NOT EXISTS idx_merchants_source ON merchants(source);
