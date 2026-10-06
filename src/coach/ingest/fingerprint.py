"""Content fingerprint shared by file imports and the API sync: date + amount + normalised description.

It is what lets an import into an account that is also synced (or two overlapping files) avoid duplicates:
a transaction already present with the same (booking date, amount, normalised description) is the same one.
Limits: bank exports and the PSD2 feed often word the description differently, and a booking date may differ
by a day; in those cases the rows are not recognised as identical (see README "File imports").
"""
from __future__ import annotations

import hashlib
import re
import unicodedata


def norm_desc(text: str | None) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def content_key(booking_date: str, amount: float, description: str | None) -> tuple[str, str, str]:
    return (booking_date, f"{round(float(amount), 2):.2f}", norm_desc(description))


def import_tx_key(account_uid: str, key: tuple[str, str, str], occurrence: int) -> str:
    """Stable tx_key of the n-th (1-based) identical row of an account, independent of file name/order."""
    h = hashlib.sha256("|".join((account_uid, *key, str(occurrence))).encode()).hexdigest()[:24]
    return f"{account_uid}:imp:{h}"


# H1. Some banks (Fortuneo) send an `entry_reference` that is only the booking date plus the transaction's POSITION in
# that day's list ("2025-12-11T00:00:00-3"). A late or back-dated booking shifts every later index, so the reference
# is not an identity: such transactions are keyed by what they contain instead.
POSITION_REF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T00:00:00-\d+$")


def is_position_ref(ref: str | None) -> bool:
    return bool(ref) and POSITION_REF_RE.match(ref) is not None


def content_tx_key(account_uid: str, currency: str | None, key: tuple[str, str, str], occurrence: int) -> str:
    """Stable tx_key of the n-th (1-based) transaction with this content on this account: account, booking date,
    amount, currency, normalised description and the occurrence index among identical ones."""
    # the account uid is the key's prefix, not part of the hash: `accounts merge` renames the prefix and the key
    # stays the one a fresh sync computes
    raw = "|".join((*key, (currency or "").upper(), str(occurrence)))
    return f"{account_uid}:cfp:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def materially_differs(old: tuple, new: tuple) -> bool:
    """(booking_date, amount, description) of a stored row vs the incoming one: a different transaction."""
    return content_key(old[0] or "", old[1], old[2]) != content_key(new[0] or "", new[1], new[2])


def memory_key_warnings(memory_dir, replaced: list[tuple[str, str]]) -> list[str]:
    """Annotations in memory/categorization.yaml that list a tx_key which no longer exists (we never edit memory/)."""
    if not replaced or memory_dir is None:
        return []
    from coach.classify.rules import load_annotations
    old = {o: n for o, n in replaced}
    out = []
    for a in load_annotations(memory_dir):
        for k in (a.get("match") or {}).get("tx_keys", []) or []:
            if k in old:
                out.append(f"memory annotation {a.get('id', '?')!r} lists tx_key {k}, replaced by the bank's own "
                           f"row {old[k]}: update memory/categorization.yaml (it no longer matches)")
    return out


def remap_warnings(con, memory_dir) -> list[str]:
    """Annotations in memory/categorization.yaml that still list a tx_key that was re-keyed (H1 or import
    supersede). They keep matching (the pipeline follows tx_key_remap) but should be updated; we never edit memory/."""
    if memory_dir is None:
        return []
    from coach.classify.rules import load_annotations
    remap = dict(con.execute("SELECT old_key, new_key FROM tx_key_remap"))
    out = []
    for a in load_annotations(memory_dir):
        for k in (a.get("match") or {}).get("tx_keys", []) or []:
            if k in remap:
                out.append(f"memory annotation {a.get('id', '?')!r} lists tx_key {k}, now {remap[k]}: update "
                           "memory/categorization.yaml (it still matches through the remap table)")
    return out


def _move_gold(con, old: str, new: str) -> None:
    """E12-2: the gold label of a transaction follows its new key (the table does not exist yet while migration 0007 runs)."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gold_labels'").fetchone():
        con.execute("UPDATE OR IGNORE gold_labels SET tx_key=? WHERE tx_key=?", (new, old))
        con.execute("DELETE FROM gold_labels WHERE tx_key=?", (old,))


def rekey_tables(con, old: str, new: str) -> None:
    """Point every table that refers to a transaction at its new key (the rows keep their content)."""
    con.execute("UPDATE transactions SET tx_key=? WHERE tx_key=?", (new, old))
    for table in ("tx_enriched", "tx_overrides", "import_rows", "tx_parse_meta"):
        con.execute(f"UPDATE {table} SET tx_key=? WHERE tx_key=?", (new, old))
    _move_gold(con, old, new)
    con.execute("UPDATE tx_splits SET tx_key=? WHERE tx_key=?", (new, old))
    con.execute("UPDATE transfer_links SET out_tx_key=? WHERE out_tx_key=?", (new, old))
    con.execute("UPDATE transfer_links SET in_tx_key=? WHERE in_tx_key=?", (new, old))
    con.execute("UPDATE transfer_rejections SET out_tx_key=? WHERE out_tx_key=?", (new, old))
    con.execute("UPDATE transfer_rejections SET in_tx_key=? WHERE in_tx_key=?", (new, old))


def replace_tx_key(con, old: str, new: str) -> None:
    """An import row was superseded by the API version of the same transaction: carry the user's data over."""
    con.execute("INSERT OR REPLACE INTO tx_key_remap VALUES (?,?,'import superseded by bank feed',datetime('now'))",
                (old, new))
    con.execute("UPDATE tx_splits SET tx_key=? WHERE tx_key=?", (new, old))
    con.execute("UPDATE OR IGNORE tx_overrides SET tx_key=? WHERE tx_key=?", (new, old))
    _move_gold(con, old, new)
    con.execute("UPDATE transfer_links SET out_tx_key=? WHERE out_tx_key=?", (new, old))
    con.execute("UPDATE transfer_links SET in_tx_key=? WHERE in_tx_key=?", (new, old))
    con.execute("UPDATE OR IGNORE transfer_rejections SET out_tx_key=? WHERE out_tx_key=?", (new, old))
    con.execute("UPDATE OR IGNORE transfer_rejections SET in_tx_key=? WHERE in_tx_key=?", (new, old))
    con.execute("DELETE FROM transfer_rejections WHERE out_tx_key=? OR in_tx_key=?", (old, old))
    con.execute("DELETE FROM tx_overrides WHERE tx_key=?", (old,))
    con.execute("DELETE FROM tx_enriched WHERE tx_key=?", (old,))
    con.execute("DELETE FROM tx_parse_meta WHERE tx_key=?", (old,))
    con.execute("DELETE FROM import_rows WHERE tx_key=?", (old,))
    con.execute("DELETE FROM transactions WHERE tx_key=?", (old,))
