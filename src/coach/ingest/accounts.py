"""Accounts: storing a bank session's accounts, stable ids across reconnects, labels/owner/purpose.

``accounts.uid`` is the STABLE internal id (what transactions, balances and sync_log refer to).
``accounts.api_uid`` is the uid the current Enable Banking session uses in API calls. They differ after a
reconnect that made the bank return a new uid for an account we already know: the new uid is matched to the
existing record by ``identification_hash`` (sha256 of the normalised IBAN), so history continues in the same
record and nothing is duplicated.
"""
from __future__ import annotations

import hashlib
import json
import re

from coach.db import now_iso
from coach.ingest.fingerprint import rekey_tables

PURPOSES = ("main", "cards", "rental", "kids", "savings")


class AccountError(Exception):
    pass


def normalize_iban(iban: str | None) -> str | None:
    iban = re.sub(r"\s+", "", iban or "").upper()
    return iban or None


def ident_hash(iban: str | None, other: str | None = None) -> str | None:
    iban = normalize_iban(iban)
    if iban:
        return hashlib.sha256(f"iban:{iban}".encode()).hexdigest()
    if other:
        return hashlib.sha256(f"other:{other.strip().upper()}".encode()).hexdigest()
    return None


def payload_ident(acc: dict) -> tuple[str | None, str | None]:
    """(iban, ident_hash) from an Enable Banking account object."""
    aid = acc.get("account_id") or {}
    iban = aid.get("iban")
    other = (aid.get("other") or {}).get("identification") if isinstance(aid.get("other"), dict) else None
    return iban, ident_hash(iban, other)


def mask_iban(iban: str | None) -> str:
    return f"…{iban[-4:]}" if iban else ""


def ensure_hashes(con) -> None:
    """Backfill identification_hash for accounts stored before the column existed."""
    for uid, iban in con.execute(
            "SELECT uid, iban FROM accounts WHERE identification_hash IS NULL AND iban IS NOT NULL").fetchall():
        h = ident_hash(iban)
        if h:
            con.execute("UPDATE accounts SET identification_hash=? WHERE uid=?", (h, uid))
    con.commit()


def api_uid_of(con, uid: str) -> str:
    row = con.execute("SELECT COALESCE(api_uid, uid) FROM accounts WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else uid


def _fold(x: str | None) -> str:
    return re.sub(r"\s+", " ", (x or "").strip().upper())


def eligible_sessions(con, sid: str, bank: str, country: str, replaces: str | None) -> set[str]:
    """Sessions whose accounts a new session may take over: itself (re-finish), the one it explicitly replaces,
    and dead (expired / revoked / replaced) sessions of the same bank and country. Never another live consent."""
    from coach.ingest import consent as consent_mod
    ids = {sid}
    if replaces:
        ids.add(replaces)
    for osid, name, ctry, until, status, live in con.execute(
            "SELECT session_id, aspsp_name, aspsp_country, valid_until, status, live_status FROM sessions "
            "WHERE session_id<>?", (sid,)).fetchall():
        if _fold(name) != _fold(bank) or _fold(ctry) != _fold(country):
            continue
        if consent_mod.classify(until, status, live)[0] in (*consent_mod.DEAD, "replaced"):
            ids.add(osid)
    return ids


def _match_existing(con, acc: dict, h: str | None, claimed: set[str], eligible: set[str]):
    """One-to-one match of a session account to an existing record.
    Returns (uid, None) for a unique match, (None, [uids]) when ambiguous, (None, []) when there is none.
    Key = same uid, else (IBAN hash | name for IBAN-less accounts) + currency + cash_account_type when both known,
    among accounts of the eligible sessions that no other account of this session has claimed yet."""
    new_uid = acc["uid"]
    row = con.execute("SELECT uid FROM accounts WHERE (uid=? OR api_uid=?) AND source='api'",
                      (new_uid, new_uid)).fetchone()
    if row and row[0] not in claimed:
        return row[0], None
    marks = ",".join("?" * len(eligible))
    rows = con.execute(
        f"SELECT uid, name, currency, cash_account_type, identification_hash FROM accounts "
        f"WHERE source='api' AND session_id IN ({marks})", tuple(eligible)).fetchall()
    cur, typ = acc.get("currency"), acc.get("cash_account_type")
    name = _fold(acc.get("name") or acc.get("product"))
    exact, wild = [], []
    for uid, n, c, t, oh in rows:
        if uid in claimed or (cur and c and cur != c) or (typ and t and typ != t):
            continue
        if h:
            if oh != h:
                continue
        elif oh is not None or not name or _fold(n) != name:   # no IBAN: only the same product name matches
            continue
        (exact if (c, t) == (cur, typ) else wild).append(uid)  # a missing type/currency is a wildcard of last resort
    cands = exact or wild
    if len(cands) == 1:
        return cands[0], None
    return None, cands


def store_session(con, s: dict, replaces_session_id: str | None = None, fallback: dict | None = None) -> dict:
    """Persist a session returned by POST /sessions plus its accounts; retire the sessions it supersedes.

    Matching onto existing records is one-to-one and conservative (see :func:`_match_existing`): several
    accounts of one IBAN (currency pockets) stay separate, and an ambiguous match never merges silently: the
    new account is created with ``needs_review=1`` (and so are the candidates), which excludes them from
    analytics and from sync until `coach accounts merge OLD NEW` / `accounts set --resolve`.

    Returns {"session_id", "bank", "country", "valid_until", "accounts": [{uid, api_uid, name, remapped,
    new, needs_review}], "retired": [session ids], "orphans": [stable uids left on a retired session]}.
    """
    ensure_hashes(con)
    fb = fallback or {}
    sid = s.get("session_id")
    if not sid:
        raise AccountError("the bank's session response has no session_id")
    aspsp = s.get("aspsp") or {}
    bank, country = aspsp.get("name") or fb.get("bank"), aspsp.get("country") or fb.get("country")
    if not bank:
        raise AccountError("the session response names no bank and none was recorded at connect time")
    valid_until = (s.get("access") or {}).get("valid_until") or fb.get("valid_until")
    con.execute(
        """INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw, status)
           VALUES (?,?,?,?,?,?, 'active')
           ON CONFLICT(session_id) DO UPDATE SET aspsp_name=excluded.aspsp_name,
             aspsp_country=excluded.aspsp_country, valid_until=excluded.valid_until, raw=excluded.raw,
             status='active', replaced_by=NULL""",
        (sid, bank, country, valid_until, now_iso(), json.dumps(s)))
    eligible = eligible_sessions(con, sid, bank, country, replaces_session_id)
    out, touched_sessions, claimed = [], set(), set()
    skipped = []
    for acc in s.get("accounts") or []:
        if not isinstance(acc, dict) or not acc.get("uid"):
            skipped.append(acc)
            continue
        new_uid = acc["uid"]
        iban, h = payload_ident(acc)
        name = acc.get("name") or acc.get("product")
        existing, ambiguous = _match_existing(con, acc, h, claimed, eligible)
        if existing:
            claimed.add(existing)
            old_sid = con.execute("SELECT session_id FROM accounts WHERE uid=?", (existing,)).fetchone()[0]
            if old_sid and old_sid != sid:
                touched_sessions.add(old_sid)
            con.execute(
                """UPDATE accounts SET session_id=?, api_uid=?, name=COALESCE(?, name), iban=COALESCE(?, iban),
                   currency=COALESCE(?, currency), cash_account_type=COALESCE(?, cash_account_type), raw=?,
                   bank=?, identification_hash=COALESCE(?, identification_hash) WHERE uid=?""",
                (sid, None if new_uid == existing else new_uid, name, iban, acc.get("currency"),
                 acc.get("cash_account_type"), json.dumps(acc), bank, h, existing))
            out.append({"uid": existing, "api_uid": new_uid, "name": name, "remapped": new_uid != existing,
                        "new": False, "needs_review": False})
        else:
            review = int(bool(ambiguous))
            con.execute(
                """INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw, bank,
                   source, identification_hash, needs_review) VALUES (?,?,?,?,?,?,?,?, 'api', ?, ?)""",
                (new_uid, sid, name, iban, acc.get("currency"), acc.get("cash_account_type"),
                 json.dumps(acc), bank, h, review))
            claimed.add(new_uid)
            for u in ambiguous or []:
                con.execute("UPDATE accounts SET needs_review=1 WHERE uid=?", (u,))
            out.append({"uid": new_uid, "api_uid": new_uid, "name": name, "remapped": False, "new": True,
                        "needs_review": bool(review), "candidates": ambiguous or []})
    retired = []
    candidates = set(touched_sessions)
    if replaces_session_id and replaces_session_id != sid:
        candidates.add(replaces_session_id)
    for old in sorted(candidates):
        if not con.execute("SELECT 1 FROM sessions WHERE session_id=?", (old,)).fetchone():
            continue
        left = con.execute("SELECT COUNT(*) FROM accounts WHERE session_id=?", (old,)).fetchone()[0]
        if left == 0 or old == replaces_session_id:
            con.execute("UPDATE sessions SET status='replaced', replaced_by=? WHERE session_id=?", (sid, old))
            retired.append(old)
    # accounts the new session did not return, still sitting on the sessions it took accounts from / replaced
    orphans = [r[0] for old in sorted(candidates)
               for r in con.execute("SELECT uid FROM accounts WHERE session_id=?", (old,))]
    con.commit()
    return {"session_id": sid, "bank": bank, "country": country, "valid_until": valid_until,
            "accounts": out, "retired": retired, "orphans": orphans, "skipped_accounts": len(skipped)}


def merge_accounts(con, old_ref: str, new_ref: str) -> str:
    """Fold account NEW into OLD (OLD keeps its stable uid, label, owner, purpose and history): NEW's bank
    uid/session become OLD's, NEW's rows move to OLD (duplicates by key are dropped), NEW disappears."""
    old, new = resolve_account(con, old_ref), resolve_account(con, new_ref)
    if old == new:
        raise AccountError("OLD and NEW are the same account")
    o = con.execute("SELECT currency, source FROM accounts WHERE uid=?", (old,)).fetchone()
    n = con.execute("SELECT session_id, COALESCE(api_uid, uid), currency, source, iban FROM accounts WHERE uid=?",
                    (new,)).fetchone()
    if o[1] != n[3]:
        raise AccountError("cannot merge an API account with a manual one")
    if o[0] and n[2] and o[0] != n[2]:
        raise AccountError(f"currencies differ ({o[0]} vs {n[2]}): not the same account")
    try:
        for key, in con.execute("SELECT tx_key FROM transactions WHERE account_uid=?", (new,)).fetchall():
            nk = f"{old}:{key.split(':', 1)[1]}" if key.startswith(new + ":") else key
            dup = nk != key and con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (nk,)).fetchone()
            if dup:
                # the kept row inherits the duplicate's user data when it has none of its own
                con.execute("UPDATE tx_splits SET tx_key=? WHERE tx_key=? AND NOT EXISTS "
                            "(SELECT 1 FROM tx_splits WHERE tx_key=?)", (nk, key, nk))
                con.execute("UPDATE OR IGNORE tx_overrides SET tx_key=? WHERE tx_key=?", (nk, key))
                con.execute("INSERT OR REPLACE INTO tx_key_remap VALUES (?,?,'account merge: duplicate of the kept row',"
                            "datetime('now'))", (key, nk))
                for t in ("tx_enriched", "tx_overrides", "import_rows", "tx_parse_meta", "tx_splits"):
                    con.execute(f"DELETE FROM {t} WHERE tx_key=?", (key,))
                con.execute("DELETE FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?", (key, key))
                con.execute("DELETE FROM transactions WHERE tx_key=?", (key,))
            elif nk != key:
                rekey_tables(con, key, nk)       # every table that refers to the key follows (E2: splits, parse meta...)
                con.execute("INSERT OR REPLACE INTO tx_key_remap VALUES (?,?,'account merge',datetime('now'))",
                            (key, nk))
                con.execute("UPDATE transactions SET account_uid=? WHERE tx_key=?", (old, nk))
            else:
                con.execute("UPDATE transactions SET account_uid=? WHERE tx_key=?", (old, key))
        for t in ("balances", "sync_log", "pending_transactions", "imports"):
            con.execute(f"UPDATE {t} SET account_uid=? WHERE account_uid=?", (old, new))
        con.execute("DELETE FROM accounts WHERE uid=?", (new,))
        con.execute("UPDATE accounts SET session_id=?, api_uid=?, needs_review=0, iban=COALESCE(iban, ?), "
                    "identification_hash=COALESCE(identification_hash, ?) WHERE uid=?",
                    (n[0], None if n[1] == old else n[1], n[4], ident_hash(n[4]), old))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return old


# ---------------------------------------------------------------- lookup / listing / editing

def resolve_account(con, ident: str) -> str:
    """Stable uid for a uid, api_uid, label (case-insensitive) or unique uid prefix."""
    row = con.execute("SELECT uid FROM accounts WHERE uid=? OR api_uid=?", (ident, ident)).fetchone()
    if row:
        return row[0]
    rows = con.execute("SELECT uid FROM accounts WHERE LOWER(label)=LOWER(?)", (ident,)).fetchall()
    if not rows and len(ident) >= 4:
        rows = con.execute("SELECT uid FROM accounts WHERE uid LIKE ? ESCAPE '\\'",
                           (ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        raise AccountError(f"{ident!r} is ambiguous: {', '.join(r[0] for r in rows)}")
    raise AccountError(f"No account matches {ident!r} (see `coach accounts`)")


def list_accounts(con) -> list[dict]:
    rows = con.execute("""
        SELECT a.uid, a.api_uid, a.bank, a.label, a.name, a.iban, a.currency, a.owner, a.purpose, a.exclude,
               a.source, a.session_id, s.status, s.valid_until, a.needs_review,
               (SELECT COUNT(*) FROM transactions t WHERE t.account_uid=a.uid)
        FROM accounts a LEFT JOIN sessions s ON s.session_id=a.session_id
        ORDER BY COALESCE(a.bank, ''), COALESCE(a.label, a.name, a.uid)""").fetchall()
    keys = ["uid", "api_uid", "bank", "label", "name", "iban", "currency", "owner", "purpose", "exclude",
            "source", "session_id", "session_status", "valid_until", "needs_review", "tx_count"]
    return [dict(zip(keys, r)) for r in rows]


def set_account(con, ident: str, label=None, owner=None, purpose=None, exclude=None, resolve=False, people=None) -> str:
    """`people` (E14-2, a :class:`coach.household.people.People`): when the household declares members, an owner must be 'joint' or one of
    them (id, name or alias) and is stored as the member id, so one person is never two spellings."""
    uid = resolve_account(con, ident)
    if purpose is not None and purpose not in PURPOSES:
        raise AccountError(f"purpose must be one of {', '.join(PURPOSES)}")
    if owner is not None:
        owner = owner.strip()
        if not owner:
            raise AccountError("owner must be 'joint' or a member name")
        if owner.lower() == "joint":
            owner = "joint"
        elif people:
            resolved = people.resolve(owner)
            if resolved is None:
                raise AccountError(f"owner {owner!r} is neither 'joint' nor a household member ({', '.join(people.ids())}): "
                                   "declare the member first (`coach memory member add`)")
            owner = resolved
    if label is not None:
        label = label.strip() or None
        if label and (other := con.execute("SELECT uid FROM accounts WHERE LOWER(label)=LOWER(?) AND uid<>?",
                                           (label, uid)).fetchone()):
            raise AccountError(f"label {label!r} is already used by account {other[0]}")
    sets, args = [], []
    for col, val in (("label", label), ("owner", owner), ("purpose", purpose)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    if exclude is not None:
        sets.append("exclude=?")
        args.append(int(bool(exclude)))
    if resolve:
        sets.append("needs_review=0")
    if not sets:
        raise AccountError("nothing to set: pass --label, --owner, --purpose, --exclude, --include or --resolve")
    con.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE uid=?", (*args, uid))
    con.commit()
    return uid


def format_accounts(rows: list[dict]) -> str:
    head = f"{'uid':<38}{'bank':<26}{'label':<22}{'owner':<10}{'purpose':<9}{'src':<7}{'tx':>6}  notes"
    lines = [head]
    for r in rows:
        notes = []
        if r["exclude"]:
            notes.append("EXCLUDED from analytics")
        if r["needs_review"]:
            notes.append("NEEDS REVIEW (ambiguous after reconnect: `accounts merge OLD NEW` or `set --resolve`)")
        if r["api_uid"] and r["api_uid"] != r["uid"]:
            notes.append(f"api uid {r['api_uid']}")
        if r["source"] == "api" and r["session_status"] not in (None, "active"):
            notes.append(f"session {r['session_status']}")
        if r["iban"]:
            notes.append(mask_iban(r["iban"]))
        lines.append(f"{r['uid']:<38}{(r['bank'] or '-')[:25]:<26}{(r['label'] or r['name'] or '-')[:21]:<22}"
                     f"{(r['owner'] or '-'):<10}{(r['purpose'] or '-'):<9}{r['source']:<7}{r['tx_count']:>6}"
                     f"  {'; '.join(notes)}")
    return "\n".join(lines)
