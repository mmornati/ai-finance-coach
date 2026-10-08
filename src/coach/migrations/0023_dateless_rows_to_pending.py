"""A sync stored entries a bank sent with a status other than BOOK / PDNG (e.g. OTHR) and no date at all as booked
transactions with a NULL booking_date, and the analytics could not load them. Such a row is not a booked transaction:
it moves to `pending_transactions` (as the fixed sync now does; the next sync of that account rewrites that table) and
leaves `transactions` together with every row that refers to its tx_key. A NULL-date row that has a value or
transaction date in its raw payload takes that date instead and stays. Idempotent: afterwards no row has a NULL date."""
import json

_REFS = ("tx_key", "out_tx_key", "in_tx_key")


def _referring(con) -> list[tuple[str, str]]:
    out = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
                                "('transactions', 'tx_key_remap')").fetchall():
        out += [(table, r[1]) for r in con.execute(f"PRAGMA table_info({table})") if r[1] in _REFS]
    return out


def run(con) -> None:
    rows = con.execute("SELECT tx_key, account_uid, amount, currency, counterparty, description, raw "
                       "FROM transactions WHERE booking_date IS NULL OR booking_date = ''").fetchall()
    if not rows:
        return
    refs = _referring(con)
    for key, uid, amount, cur, party, desc, raw in rows:
        try:
            tx = json.loads(raw or "{}")
        except ValueError:
            tx = {}
        day = tx.get("value_date") or tx.get("transaction_date") if isinstance(tx, dict) else None
        if day and (tx.get("status") in (None, "", "BOOK")):
            con.execute("UPDATE transactions SET booking_date=? WHERE tx_key=?", (str(day)[:10], key))
            continue
        con.execute("INSERT INTO pending_transactions VALUES (?,?,?,?,?,?,?)",
                    (uid, day, amount, cur, party, desc, raw))
        for table, col in refs:
            con.execute(f"DELETE FROM {table} WHERE {col}=?", (key,))
        con.execute("DELETE FROM transactions WHERE tx_key=?", (key,))
