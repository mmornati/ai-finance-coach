-- pending_auth: keep the bank's /sessions response until it is safely stored (the authorisation code is
-- single-use: if storing fails, `coach finish --replay STATE` stores the saved response), and remember the
-- requested consent end as a fallback for responses without access.valid_until.
ALTER TABLE pending_auth ADD COLUMN session_response TEXT;
ALTER TABLE pending_auth ADD COLUMN valid_until_requested TEXT;

-- Legacy rows: an authorisation whose bank already has a session created afterwards was in fact completed
-- (e.g. the prototype's Fortuneo login). Others are left alone: they expire by age (24 h) in the code.
UPDATE pending_auth SET completed_at = (
    SELECT MIN(s.created_at) FROM sessions s
    WHERE s.aspsp_name = pending_auth.aspsp_name AND s.aspsp_country = pending_auth.aspsp_country
      AND s.created_at >= pending_auth.created_at)
WHERE completed_at IS NULL AND EXISTS (
    SELECT 1 FROM sessions s
    WHERE s.aspsp_name = pending_auth.aspsp_name AND s.aspsp_country = pending_auth.aspsp_country
      AND s.created_at >= pending_auth.created_at);
