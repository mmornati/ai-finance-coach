"""Connector health (E1-11): per bank and account, from the local DB only (no network).

Levels: green (fine) | amber (needs attention soon) | red (action needed, `coach health` exits non-zero).
  red    consent expired / revoked / <= 3 days left, or the most recent sync attempt failed
  amber  consent <= 14 days left, never synced, or STALE (no successful sync for more than `stale_days`)
Manual (file import) accounts have no consent or sync: they are always green and show the last import.
"""
from __future__ import annotations

import re

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from coach.ingest import consent as consent_mod
from coach.ingest.sync import local_day_bounds_utc
from coach.db import now_iso
from coach.i18n_msg import server_msg

RANK = {"green": 0, "amber": 1, "red": 2}


@dataclass
class AccountHealth:
    uid: str
    label: str
    bank: str
    source: str
    level: str = "green"
    problems: list[str] = field(default_factory=list)
    problems_msg: list[dict] = field(default_factory=list)     # the same problems for the web app: {code, params, text}, same order
    last_ok_sync: str | None = None
    last_attempt: str | None = None
    last_attempt_ok: bool | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    syncs_today: int = 0
    daily_limit: int = 4
    syncs_left_today: int = 0
    consent_status: str | None = None
    consent_days_left: int | None = None
    tx_count: int = 0
    first_tx_date: str | None = None
    last_tx_date: str | None = None
    pending_count: int = 0
    stale: bool = False
    last_import: str | None = None
    key_conflicts: int = 0
    excluded: bool = False


@dataclass
class BankHealth:
    bank: str
    country: str
    session_id: str | None
    consent_status: str | None
    consent_days_left: int | None
    valid_until: str | None
    level: str = "green"
    accounts: list[AccountHealth] = field(default_factory=list)
    bank_code: str | None = None            # "manual": the group of the file-import accounts (the web's labels.bankGroup.manual)


@dataclass
class HealthReport:
    generated_at: str
    banks: list[BankHealth]
    ok: bool
    level: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _raise(level: str, new: str) -> str:
    return new if RANK[new] > RANK[level] else level


def _problem(h: AccountHealth, level: str, msg: dict) -> None:
    """A problem: the English sentence (CLI, `coach health`) and its message for the web; the `coach ...` commands are params."""
    h.level = _raise(h.level, level)
    h.problems.append(msg["text"])
    h.problems_msg.append(msg)


# the consent statuses that need a reconnect (consent_mod.DEAD): one sentence each
DEAD_CODE = {"expired": "health.consentExpired", "revoked": "health.consentRevoked"}


def _account(con, row, consent, daily_limit, stale_days, now, explicit_now) -> AccountHealth:
    uid, label, bank, source, excluded, needs_review = row
    h = AccountHealth(uid=uid, label=label, bank=bank or "-", source=source, daily_limit=daily_limit,
                      excluded=bool(excluded))
    h.tx_count, h.first_tx_date, h.last_tx_date = con.execute(
        "SELECT COUNT(*), MIN(booking_date), MAX(booking_date) FROM transactions WHERE account_uid=?",
        (uid,)).fetchone()
    h.pending_count = con.execute("SELECT COUNT(*) FROM pending_transactions WHERE account_uid=?",
                                  (uid,)).fetchone()[0]
    if source != "api":
        h.last_import = con.execute("SELECT MAX(imported_at) FROM imports WHERE account_uid=?", (uid,)).fetchone()[0]
        return h
    h.last_ok_sync = con.execute("SELECT MAX(ran_at) FROM sync_log WHERE account_uid=? AND ok=1", (uid,)).fetchone()[0]
    last = con.execute("SELECT ran_at, ok, note FROM sync_log WHERE account_uid=? ORDER BY ran_at DESC, rowid DESC "
                       "LIMIT 1", (uid,)).fetchone()
    if last:
        h.last_attempt, h.last_attempt_ok = last[0], bool(last[1])
    err = con.execute("SELECT ran_at, note FROM sync_log WHERE account_uid=? AND ok=0 ORDER BY ran_at DESC, "
                      "rowid DESC LIMIT 1", (uid,)).fetchone()
    if err:
        h.last_error_at, h.last_error = err
    start, end = local_day_bounds_utc(now if explicit_now else None)
    h.syncs_today = con.execute("SELECT COUNT(*) FROM sync_log WHERE account_uid=? AND ran_at>=? AND ran_at<?",
                                (uid, start, end)).fetchone()[0]
    h.syncs_left_today = max(0, daily_limit - h.syncs_today)
    if needs_review:
        _problem(h, "amber", server_msg("health.needsReview", "needs review after a reconnect (not synced, left out of analytics): "
                                        "`coach accounts merge OLD NEW` or `coach accounts set UID --resolve`",
                                        merge_command="coach accounts merge OLD NEW", resolve_command="coach accounts set UID --resolve"))
    if consent:
        h.consent_status, h.consent_days_left = consent.status, consent.days_left
        reconnect = f'coach reconnect "{bank}"'
        if consent.status in consent_mod.DEAD:
            text = f"consent {consent.status}: `{reconnect}`"
            _problem(h, "red", server_msg(DEAD_CODE.get(consent.status, "health.consentDead"), text, status=consent.status, command=reconnect))
        elif consent.status == "urgent":
            _problem(h, "red", server_msg("health.consentUrgent", f"consent expires in {consent.days_left} day(s): `{reconnect}`",
                                          count=consent.days_left, command=reconnect))
        elif consent.status == "unknown":
            _problem(h, "amber", server_msg("health.consentUnknown", "consent status unknown (live check failed or unrecognised status): "
                                            "`coach consents --refresh`", command="coach consents --refresh"))
        elif consent.status == "expiring":
            _problem(h, "amber", server_msg("health.consentExpiring", f"consent expires in {consent.days_left} days", count=consent.days_left))
    note = con.execute("SELECT note FROM sync_log WHERE account_uid=? AND ok=1 ORDER BY ran_at DESC, rowid DESC LIMIT 1",
                       (uid,)).fetchone()
    if note and (m := re.search(r"key_conflicts=(\d+)", note[0] or "")):
        h.key_conflicts = int(m.group(1))
        _problem(h, "amber", server_msg("health.keyConflicts", f"{h.key_conflicts} transaction(s) reused a bank reference with different content "
                                        "in the last sync (both kept): check the duplicates", count=h.key_conflicts))
    if h.last_attempt_ok is False:
        error = (h.last_error or "")[:120]          # the bank's own text: a param, never translated
        _problem(h, "red", server_msg("health.lastSyncFailed", f"last sync failed ({h.last_error_at}): {error}", at=h.last_error_at or "",
                                      error=error))
    if h.last_ok_sync is None:
        _problem(h, "amber", server_msg("health.neverSynced", "never synced"))
    else:
        ok_at = consent_mod.parse_ts(h.last_ok_sync)
        if ok_at and now - ok_at > timedelta(days=stale_days):
            h.stale = True
            _problem(h, "amber", server_msg("health.stale", f"stale: no successful sync since {h.last_ok_sync} (> {stale_days} days)",
                                            since=h.last_ok_sync, count=stale_days))
    return h


def health(con, daily_limit: int = 4, stale_days: int = 2, now: datetime | None = None) -> HealthReport:
    explicit_now = now is not None      # tests pass an aware `now`; otherwise the system local day is used
    now = now or datetime.now(timezone.utc)
    consents = {c.session_id: c for c in consent_mod.list_consents(con, now)}
    rows = con.execute("""SELECT a.uid, COALESCE(a.label, a.name, a.uid), COALESCE(a.bank, ''), a.source,
                                 a.exclude, a.session_id, a.needs_review FROM accounts a
                          ORDER BY COALESCE(a.bank, ''), COALESCE(a.label, a.name, a.uid)""").fetchall()
    banks: dict[tuple, BankHealth] = {}
    for uid, label, bank, source, excl, sid, nr in rows:
        c = consents.get(sid) if source == "api" else None
        if source != "api":
            key, mk = ("import",), lambda: BankHealth("Manual imports", "", None, None, None, None, bank_code="manual")
        elif c:
            key, mk = (sid,), lambda: BankHealth(bank or c.bank, c.country, sid, c.status, c.days_left, c.valid_until)
        else:  # account left on a replaced session: still reported
            key, mk = (sid,), lambda: BankHealth(bank or "?", "", sid, "replaced", None, None)
        b = banks.setdefault(key, mk())
        a = _account(con, (uid, label, bank, source, excl, nr), c, daily_limit, stale_days, now, explicit_now)
        if source == "api" and not c:
            a.consent_status = "replaced"
            _problem(a, "amber", server_msg("health.sessionReplaced", "session replaced: account no longer synced (not returned by the new session)"))
        b.accounts.append(a)
        b.level = _raise(b.level, a.level)
    out = sorted(banks.values(), key=lambda b: (b.bank.lower(), b.session_id or ""))
    worst = "green"
    for b in out:
        worst = _raise(worst, b.level)
    return HealthReport(generated_at=now_iso(), banks=out, ok=worst != "red", level=worst)


def format_report(r: HealthReport) -> str:
    icon = {"green": "OK   ", "amber": "WARN ", "red": "RED  "}
    lines = [f"connector health ({r.generated_at})  overall: {r.level.upper()}"]
    for b in r.banks:
        cons = ""
        if b.consent_status:
            left = "?" if b.consent_days_left is None else f"{b.consent_days_left}d"
            cons = f"  consent {b.consent_status}, {left} left (until {b.valid_until})"
        lines.append(f"\n[{icon[b.level].strip()}] {b.bank} {b.country}{cons}")
        for a in b.accounts:
            lines.append(f"  [{icon[a.level].strip()}] {a.label}  ({a.uid})" + ("  [excluded from analytics]" if a.excluded else ""))
            rng = f"{a.first_tx_date} -> {a.last_tx_date}" if a.tx_count else "no transactions"
            lines.append(f"      tx: {a.tx_count}  {rng}  pending: {a.pending_count}")
            if a.source == "api":
                lines.append(f"      last ok sync: {a.last_ok_sync or 'never'}   syncs today: {a.syncs_today}/"
                             f"{a.daily_limit} ({a.syncs_left_today} left)")
                if a.last_error:
                    old = "  (resolved by a later success)" if a.last_ok_sync and a.last_ok_sync > a.last_error_at else ""
                    lines.append(f"      last error: {a.last_error_at}  {a.last_error[:140]}{old}")
            else:
                lines.append(f"      manual account; last import: {a.last_import or 'never'}")
            for p in a.problems:
                lines.append(f"      ! {p}")
    return "\n".join(lines)
