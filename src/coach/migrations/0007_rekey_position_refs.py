"""H1 data migration: re-key transactions whose bank `entry_reference` is only "<booking date>T00:00:00-<index>".

The index is the transaction's position within that day, so a late booking shifts it. Each such row gets a content
key (account, booking date, amount, currency, normalised description, occurrence among identical rows, the
occurrence following the old index order). Nothing but the key changes: no content is rewritten. Every table that
refers to tx_key follows, and old -> new is logged in `tx_key_remap` (memory annotations that list an old key keep
matching through it and are reported). Idempotent: re-keyed rows no longer have a ``:ref:`` key.
"""
import re

from coach.ingest.fingerprint import content_key, content_tx_key, is_position_ref, rekey_tables


def _index(ref: str) -> int:
    return int(re.search(r"-(\d+)$", ref).group(1))


def run(con) -> None:
    rows = con.execute("SELECT tx_key, account_uid, entry_reference, booking_date, amount, currency, description "
                       "FROM transactions WHERE tx_key LIKE '%:ref:%'").fetchall()
    groups: dict[tuple, list] = {}
    for key, uid, ref, bdate, amount, cur, desc in rows:
        if key != f"{uid}:ref:{ref}" or not is_position_ref(ref):
            continue
        groups.setdefault((uid, cur or "", *content_key(bdate or "", amount, desc)), []).append((_index(ref), key))
    plan = []
    for (uid, cur, *ck), members in groups.items():
        for occ, (_, old) in enumerate(sorted(members), 1):
            plan.append((old, content_tx_key(uid, cur, tuple(ck), occ)))
    existing = {r[0] for r in con.execute("SELECT tx_key FROM transactions")}
    new_keys = [n for _, n in plan]
    if len(set(new_keys)) != len(new_keys) or any(n in existing for n in new_keys):
        raise RuntimeError("re-keying would collide with existing keys; aborting the migration")
    for old, new in plan:
        rekey_tables(con, old, new)
        con.execute("INSERT OR REPLACE INTO tx_key_remap VALUES (?,?,'position-based entry_reference',"
                    "datetime('now'))", (old, new))
