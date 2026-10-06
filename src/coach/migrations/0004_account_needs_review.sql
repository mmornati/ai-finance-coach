-- An account that could not be matched unambiguously after a reconnect is created/kept with needs_review=1:
-- it is left out of analytics and not synced until the user resolves it (`coach accounts merge OLD NEW`
-- or `coach accounts set UID --resolve`).
ALTER TABLE accounts ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0;
