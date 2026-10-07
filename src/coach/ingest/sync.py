"""Transaction sync: pagination, dedup, pending replacement, balances, per-account daily limit."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

from coach.db import now_iso
from coach.i18n_msg import server_msg
from coach.ingest import consent as consent_mod
from coach.ingest.accounts import AccountError, resolve_account
from coach.ingest.client import ApiError
from coach.ingest.fingerprint import (norm_desc, content_key, content_tx_key, is_position_ref, materially_differs,
                                      memory_key_warnings, remap_warnings, replace_tx_key)


# ---------------------------------------------------------------- normalisation

def _amount(tx) -> tuple[float, str]:
    ta = tx.get("transaction_amount") or {}
    amt = float(ta.get("amount", 0))
    # Enable Banking amounts are unsigned; direction is in credit_debit_indicator.
    if tx.get("credit_debit_indicator") == "DBIT":
        amt = -abs(amt)
    return amt, ta.get("currency", "")


def _counterparty(tx, amount: float) -> str:
    party = tx.get("creditor") if amount < 0 else tx.get("debtor")
    return (party or {}).get("name") or ""


def _description(tx) -> str:
    ri = tx.get("remittance_information") or []
    return " | ".join(ri) if isinstance(ri, list) else str(ri)


def _btc(tx) -> str:
    b = tx.get("bank_transaction_code") or {}
    return b.get("code") or b.get("description") or "" if isinstance(b, dict) else str(b)


def enriches(old: str | None, new: str | None) -> bool:
    """The new text is the old one plus detail (a prefix, or every word of the old text is still there): the bank
    enriched the SAME transaction. 'CB NETFLIX' -> 'CB SPOTIFY' is a different transaction."""
    o, n = norm_desc(old), norm_desc(new)
    return bool(o) and (n.startswith(o) or set(o.split()) <= set(n.split()))


def related_text(old: str | None, new: str | None) -> bool:
    """One text contains the other (the bank added a line, or dropped one): the same transaction."""
    return enriches(old, new) or enriches(new, old)


def same_transaction(stored: tuple, booking_date: str | None, amount: float, description: str | None = None) -> bool:
    """Same bank reference, same amount to the cent, booking date within one day AND a description that is the old
    one enriched: the same transaction."""
    from datetime import date as _d
    if round(float(stored[1]) * 100) != round(float(amount) * 100) or not related_text(stored[2], description):
        return False
    try:
        return abs((_d.fromisoformat(str(stored[0])[:10]) - _d.fromisoformat(str(booking_date)[:10])).days) <= 1
    except ValueError:
        return False


class Keyer:
    """Assigns tx_keys during one sync run. A bank reference that is only the booking date + the position in the
    day (Fortuneo "2025-12-11T00:00:00-3", H1) is not an identity: such a transaction is keyed by its content plus
    its occurrence index among identical transactions of the run (a day's list is always returned whole)."""

    def __init__(self, account_uid: str):
        self.uid = account_uid
        self.seen: dict[tuple, int] = {}

    def content(self, tx) -> str:
        amount, cur = _amount(tx)
        ck = content_key(tx.get("booking_date") or "", amount, _description(tx))
        fp = (cur, *ck)
        n = self.seen[fp] = self.seen.get(fp, 0) + 1
        return content_tx_key(self.uid, cur, ck, n)

    def key(self, tx) -> str:
        return self.content(tx) if is_position_ref(tx.get("entry_reference")) else tx_key(self.uid, tx)


def tx_key(account_uid: str, tx) -> str:
    ref = tx.get("entry_reference")
    if ref and not is_position_ref(ref):
        return f"{account_uid}:ref:{ref}"
    if ref:   # position-based reference outside a sync run: first occurrence of its content
        amount, cur = _amount(tx)
        return content_tx_key(account_uid, cur, content_key(tx.get("booking_date") or "", amount,
                                                           _description(tx)), 1)
    amount, cur = _amount(tx)
    fp = "|".join([
        account_uid, tx.get("booking_date") or "", tx.get("value_date") or "",
        f"{amount:.2f}", cur, _counterparty(tx, amount), _description(tx),
    ])
    return f"{account_uid}:fp:{hashlib.sha256(fp.encode()).hexdigest()[:24]}"


# ---------------------------------------------------------------- sync

def local_day_bounds_utc(now: datetime | None = None) -> tuple[str, str]:
    """[start, end) of the *local* calendar day containing `now`, as UTC ISO strings comparable with
    ``sync_log.ran_at`` (always stored as UTC ``YYYY-MM-DDTHH:MM:SS+00:00``)."""
    if now is None:  # system local zone, DST-correct (naive local midnight -> UTC)
        day = date.today()
        start = datetime.combine(day, time.min).astimezone()
        end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    else:            # explicit aware `now` (tests): its own tz defines the local day
        start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        end = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    fmt = lambda d: d.astimezone(timezone.utc).isoformat(timespec="seconds")
    return fmt(start), fmt(end)


def syncs_today(con, uid, now: datetime | None = None) -> int:
    start, end = local_day_bounds_utc(now)
    return con.execute("SELECT COUNT(*) FROM sync_log WHERE account_uid=? AND ran_at>=? AND ran_at<?",
                       (uid, start, end)).fetchone()[0]


def sync_account(con, client, uid: str, full: bool, force: bool, daily_limit: int = 4, out=print) -> dict:
    """Sync one account (`uid` = stable account id). Returns a result dict (status: ok | skipped | failed)."""
    if not force and syncs_today(con, uid) >= daily_limit:
        out(f"  skip {uid}: already {daily_limit} syncs today (use --force)")
        return {"uid": uid, "status": "skipped"}
    api_uid = con.execute("SELECT COALESCE(api_uid, uid) FROM accounts WHERE uid=?", (uid,)).fetchone()
    api_uid = api_uid[0] if api_uid else uid          # the uid the current bank session knows
    last = con.execute("SELECT MAX(booking_date) FROM transactions WHERE account_uid=?",
                       (uid,)).fetchone()[0]
    params: dict = {}
    if full or not last:
        params["strategy"] = "longest"
    else:  # overlap a week to catch late-booked items
        params["date_from"] = (date.fromisoformat(last) - timedelta(days=7)).isoformat()

    pages, booked_seen, pending = 0, 0, []
    ts = now_iso()
    count = lambda: con.execute("SELECT COUNT(*) FROM transactions WHERE account_uid=?", (uid,)).fetchone()[0]
    before = count()
    # rows imported from a file: if the bank feed now brings the same transaction, the API version replaces it
    replaced: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str]] = []
    refreshed: list[str] = []
    known_conflicts: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    first_content: dict[str, tuple] = {}
    keyer = Keyer(uid)
    imported: dict = {}
    for k, d, amt, desc in con.execute(
            "SELECT t.tx_key, t.booking_date, t.amount, t.description FROM transactions t "
            "JOIN import_rows r USING(tx_key) WHERE t.account_uid=?", (uid,)):
        imported.setdefault(content_key(d, amt, desc), []).append(k)
    try:
        while True:
            res = client.call("GET", f"/accounts/{api_uid}/transactions", params=params)
            pages += 1
            for tx in res.get("transactions", []):
                if tx.get("status") == "PDNG":
                    pending.append(tx)
                    continue
                booked_seen += 1
                amount, cur = _amount(tx)
                key = keyer.key(tx)
                stored = con.execute("SELECT booking_date, amount, description FROM transactions WHERE tx_key=?",
                                     (key,)).fetchone()
                dup_in_run = key in seen_keys                    # the same reference twice in ONE response
                this = content_key(tx.get("booking_date") or "", amount, _description(tx))
                if dup_in_run and first_content.get(key) == this:
                    continue                                     # the very same entry listed twice: one row
                seen_keys.add(key)
                first_content.setdefault(key, this)
                if stored and (dup_in_run or materially_differs(stored, (tx.get("booking_date"), amount,
                                                                         _description(tx)))):
                    if not dup_in_run and same_transaction(stored, tx.get("booking_date"), amount, _description(tx)):
                        # the bank enriched the SAME transaction (same reference and amount, date within a day,
                        # old text contained in the new one): refresh its text, do not duplicate it
                        # keep the LONGER text: a bank that drops a line later does not erase detail we have
                        new_text = _description(tx) if enriches(stored[2], _description(tx)) else stored[2]
                        con.execute("UPDATE transactions SET description=?, booking_date=?, value_date=?, "
                                    "counterparty=?, bank_tx_code=?, raw=?, last_seen=? WHERE tx_key=?",
                                    (new_text, tx.get("booking_date"), tx.get("value_date"),
                                     _counterparty(tx, amount), _btc(tx), json.dumps(tx), ts, key))
                        refreshed.append(key)
                        continue
                    # same reference, genuinely different transaction: never overwrite, keep both under a content key
                    old_key = key
                    key = keyer.content(tx)
                    if con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (key,)).fetchone():
                        known_conflicts.append((old_key, key))    # already kept in an earlier sync: stay quiet
                    else:
                        conflicts.append((old_key, key))
                        out(f"  warning: {old_key} now carries different content (bank reference reused?): "
                            f"kept the stored row, stored the incoming one as {key}")
                superseded = None
                if imported and not con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (key,)).fetchone():
                    dup = imported.get(content_key(tx.get("booking_date") or "", amount, _description(tx)))
                    superseded = dup.pop() if dup else None
                con.execute(
                    "INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, "
                    "amount, currency, counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(tx_key) DO UPDATE SET last_seen=excluded.last_seen, raw=excluded.raw",
                    (key, uid, tx.get("entry_reference"), tx.get("booking_date"), tx.get("value_date"),
                     amount, cur, _counterparty(tx, amount), _description(tx),
                     tx.get("merchant_category_code"), _btc(tx), json.dumps(tx), ts, ts))
                if superseded:
                    replace_tx_key(con, superseded, key)
                    replaced.append((superseded, key))
            ck = res.get("continuation_key")
            if not ck:
                break
            params["continuation_key"] = ck  # keep original query params

        new = count() - before
        con.execute("DELETE FROM pending_transactions WHERE account_uid=?", (uid,))
        for tx in pending:
            amount, cur = _amount(tx)
            con.execute("INSERT INTO pending_transactions VALUES (?,?,?,?,?,?,?)", (
                uid, tx.get("booking_date") or tx.get("transaction_date"), amount, cur,
                _counterparty(tx, amount), _description(tx), json.dumps(tx)))

        bal = client.call("GET", f"/accounts/{api_uid}/balances")
        for b in bal.get("balances", []):
            ba = b.get("balance_amount") or {}
            con.execute("INSERT INTO balances VALUES (?,?,?,?,?,?)", (
                uid, ts, b.get("balance_type"), float(ba.get("amount", 0)), ba.get("currency"),
                b.get("reference_date")))

        note = f"booked_seen={booked_seen} pending={len(pending)}"
        if conflicts:
            note += f" key_conflicts={len(conflicts)}"
        if refreshed:
            note += f" refreshed={len(refreshed)}"
        con.execute("INSERT INTO sync_log VALUES (?,?,?,?,?,?)", (uid, ts, 1, new, pages, note))
        con.commit()
        out(f"  {uid}: pages={pages} booked_seen={booked_seen} new={new} pending={len(pending)}"
            + (f" replaced_imports={len(replaced)}" if replaced else "")
            + (f" key_conflicts={len(conflicts)}" if conflicts else ""))
        return {"uid": uid, "status": "ok", "pages": pages, "new": new, "pending": len(pending),
                "replaced": len(replaced), "replaced_keys": replaced, "conflicts": conflicts,
                "refreshed": refreshed}
    except ApiError as e:
        con.rollback()
        note = "rate limited by bank (ASPSP_RATE_LIMIT_EXCEEDED)" if e.status == 429 else str(e)
        con.execute("INSERT INTO sync_log VALUES (?,?,?,?,?,?)", (uid, ts, 0, 0, pages, note))
        con.commit()
        out(f"  {uid}: FAILED - {note}")
        res = {"uid": uid, "status": "failed", "note": note, "http_status": e.status}
        if e.status == 429:                     # the web's translation (docs/i18n.md "Server text"); another error is the bank's own text
            res["note_msg"] = server_msg("sync.rateLimited", note)
        return res


def sync_targets(con, account: str | None = None) -> list[tuple[str, str, str | None]]:
    """(stable uid, bank, session_id) of the accounts to sync: API accounts on a non-replaced session."""
    if account:
        uid = resolve_account(con, account)
        row = con.execute("SELECT source, COALESCE(bank, ''), session_id, needs_review FROM accounts WHERE uid=?",
                          (uid,)).fetchone()
        if row[3]:
            raise AccountError(f"{account!r} needs review after a reconnect: `coach accounts merge OLD NEW` or "
                               "`coach accounts set UID --resolve` first")
        if row[0] != "api":
            raise AccountError(f"{account!r} is a manual (file import) account: nothing to sync")
        return [(uid, row[1], row[2])]
    return [(r[0], r[1], r[2]) for r in con.execute(
        """SELECT a.uid, COALESCE(a.bank, s.aspsp_name, ''), a.session_id FROM accounts a
           JOIN sessions s ON s.session_id=a.session_id
           WHERE a.source='api' AND a.needs_review=0 AND s.status NOT IN ('replaced') ORDER BY s.aspsp_name, a.uid""")]


def sync_all(con, client, account: str | None = None, full: bool = False, force: bool = False,
             daily_limit: int = 4, out=print, refresh: bool = True, memory_dir=None) -> list[dict]:
    """Sync every account of every active bank session. A failure (HTTP error, network, parsing, dead consent)
    on one bank or account is recorded and never stops the others. Live consent status is refreshed first
    (`GET /sessions/{id}`, not a bank call) so a revoked/expired consent is reported instead of retried."""
    targets = sync_targets(con, account)
    if refresh:
        try:
            consent_mod.refresh_live(con, client, out=out)
        except Exception as e:  # never block the sync on the status refresh
            out(f"  consent refresh failed: {type(e).__name__}")
    status = {c.session_id: c for c in consent_mod.list_consents(con)}
    results = []
    for uid, bank, sid in targets:
        c = status.get(sid)
        if c and c.status in consent_mod.DEAD:
            note = f"{bank}: consent {c.status}: run `coach reconnect \"{bank}\"`"
            out(f"  skip {uid}: {note}")
            msg = server_msg("sync.consentExpired" if c.status == "expired" else "sync.consentRevoked", note, bank=bank, status=c.status,
                             command=f'coach reconnect "{bank}"')
            results.append({"uid": uid, "bank": bank, "status": "failed", "note": note, "note_msg": msg, "consent": c.status})
            continue
        try:
            res = sync_account(con, client, uid, full, force, daily_limit, out)
        except Exception as e:  # isolate: unexpected error in one bank must not abort the other banks
            if con.in_transaction:
                con.rollback()
            note = f"{type(e).__name__}: {str(e)[:200]}"
            con.execute("INSERT INTO sync_log VALUES (?,?,?,?,?,?)", (uid, now_iso(), 0, 0, 0, note))
            con.commit()
            out(f"  {uid}: FAILED - {note}")
            res = {"uid": uid, "status": "failed", "note": note}
        res["bank"] = bank
        results.append(res)
    for w in memory_key_warnings(memory_dir, [kv for r in results for kv in r.get("replaced_keys", [])]):
        out(f"  warning: {w}")
    for w in remap_warnings(con, memory_dir):
        out(f"  warning: {w}")
    return results
