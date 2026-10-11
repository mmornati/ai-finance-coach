"""A bank entry stored twice: once under a content key, once under its reference key.

A sync stores an entry under a content key when its reference key already holds a different transaction. When that
other row later left `transactions` (migration 0023 moved dateless non-booked entries out), the next sync found the
reference key free and stored the same entry again under it. Each such pair - same account, same bank reference, same
booking date, amount and normalised description - is merged: the reference row stays (it is what the sync finds), the
user's data of the content-key row follows it, and old -> new is logged in `tx_key_remap`. The sync no longer creates
such pairs (`ingest.sync.parked_row`). Idempotent: afterwards no pair is left.
"""
from coach.ingest.fingerprint import content_key, is_position_ref, replace_tx_key


def run(con) -> None:
    rows = con.execute("SELECT tx_key, account_uid, entry_reference, booking_date, amount, description FROM transactions "
                       "WHERE tx_key LIKE '%:cfp:%' AND entry_reference IS NOT NULL AND entry_reference != ''").fetchall()
    for key, uid, ref, bdate, amount, desc in rows:
        if is_position_ref(ref) or not key.startswith(f"{uid}:cfp:"):
            continue
        kept = con.execute("SELECT booking_date, amount, description FROM transactions WHERE tx_key=?",
                           (f"{uid}:ref:{ref}",)).fetchone()
        if kept and content_key(kept[0] or "", kept[1], kept[2]) == content_key(bdate or "", amount, desc):
            replace_tx_key(con, key, f"{uid}:ref:{ref}", "duplicate of the bank reference row")
