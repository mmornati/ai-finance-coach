"""Consent lifecycle (E1-7): expiry tracking, status thresholds, live refresh, warnings.

Status of a bank session, in decreasing severity:
  revoked   we or the bank ended it (sessions.status='revoked', or live status REVOKED/CANCELLED/CLOSED)
  expired   valid_until is in the past, or the bank reports EXPIRED/INVALID
  urgent    <= 3 days left      expiring  <= 14 days left      ok  otherwise
  unknown   the bank's live status is neither AUTHORIZED nor a known dead one (or the live check itself failed,
            e.g. "error 404"): we cannot vouch for it, amber until a refresh says AUTHORIZED
``replaced`` sessions (superseded by a reconnect) are history and never listed as active.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from coach.db import now_iso
from coach.ingest.client import ApiError

WARN_DAYS = 14
URGENT_DAYS = 3
REVOKED_LIVE = {"REVOKED", "CANCELLED", "CLOSED"}
EXPIRED_LIVE = {"EXPIRED", "INVALID"}
SEVERITY = {"ok": 0, "unknown": 1, "expiring": 1, "urgent": 2, "expired": 3, "revoked": 3, "replaced": -1}
DEAD = ("expired", "revoked")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def days_left(valid_until: str | None, now: datetime | None = None) -> int | None:
    """Whole days left (floor of the exact difference); negative once expired; None if unknown."""
    d = parse_ts(valid_until)
    if d is None:
        return None
    now = now or datetime.now(timezone.utc)
    return int((d - now).total_seconds() // 86400)


def classify(valid_until, status="active", live_status=None, now=None) -> tuple[str, int | None]:
    """(status, days_left) for one session row."""
    left = days_left(valid_until, now)
    if status == "replaced":
        return "replaced", left
    live = (live_status or "").upper()
    if status == "revoked" or live in REVOKED_LIVE:
        return "revoked", left
    if live in EXPIRED_LIVE or (left is not None and left < 0):
        return "expired", left
    unverified = bool(live) and live != "AUTHORIZED"       # live check failed or unknown status
    if left is None:
        return ("unknown" if unverified else "ok"), None
    if left <= URGENT_DAYS:
        return "urgent", left
    if unverified:
        return "unknown", left
    if left <= WARN_DAYS:
        return "expiring", left
    return "ok", left


@dataclass
class Consent:
    session_id: str
    bank: str
    country: str
    valid_until: str | None
    days_left: int | None
    status: str
    stored_status: str
    live_status: str | None
    live_checked_at: str | None
    accounts: int


def list_consents(con, now: datetime | None = None, include_replaced: bool = False) -> list[Consent]:
    out = []
    rows = con.execute("""SELECT s.session_id, s.aspsp_name, s.aspsp_country, s.valid_until, s.status,
                                 s.live_status, s.live_checked_at,
                                 (SELECT COUNT(*) FROM accounts a WHERE a.session_id=s.session_id)
                          FROM sessions s ORDER BY s.aspsp_name, s.created_at""").fetchall()
    for sid, name, ctry, until, stored, live, checked, n in rows:
        st, left = classify(until, stored, live, now)
        if st == "replaced" and not include_replaced:
            continue
        out.append(Consent(sid, name or "?", ctry or "", until, left, st, stored, live, checked, n))
    return out


def active_consents(con, now=None) -> list[Consent]:
    return [c for c in list_consents(con, now)]


def refresh_live(con, client, now: datetime | None = None, out=print) -> list[dict]:
    """GET /sessions/{id} for every non-replaced session; store the bank's status and valid_until.
    A failure on one session (network, HTTP error) never stops the others."""
    results = []
    for sid, name, stored in con.execute(
            "SELECT session_id, aspsp_name, status FROM sessions WHERE status <> 'replaced'").fetchall():
        try:
            live = client.call("GET", f"/sessions/{sid}")
        except ApiError as e:
            # 401/403/404 on the session itself means the bank/EB no longer knows it
            note = f"error {e.status}"
            con.execute("UPDATE sessions SET live_status=?, live_checked_at=? WHERE session_id=?",
                        (note, now_iso(), sid))
            results.append({"session_id": sid, "bank": name, "ok": False, "note": note})
            out(f"  consent {name}: live check failed ({note})")
            continue
        except Exception as e:  # network down, bad JSON...
            note = f"{type(e).__name__}"
            results.append({"session_id": sid, "bank": name, "ok": False, "note": note})
            out(f"  consent {name}: live check failed ({note})")
            continue
        status = str(live.get("status") or "").upper() or None
        until = (live.get("access") or {}).get("valid_until")
        con.execute("UPDATE sessions SET live_status=?, live_checked_at=?, "
                    "valid_until=COALESCE(?, valid_until) WHERE session_id=?",
                    (status, now_iso(), until, sid))
        results.append({"session_id": sid, "bank": name, "ok": True, "live_status": status})
    con.commit()
    return results


def warnings(con, now=None) -> list[tuple[str, Consent]]:
    """(level, consent) for every active session that needs attention: expiring | urgent | expired | revoked."""
    return [(c.status, c) for c in list_consents(con, now) if c.status != "ok"]


def describe(c: Consent) -> str:
    if c.status == "revoked":
        return f"{c.bank} ({c.country}): consent REVOKED - run `coach reconnect \"{c.bank}\"`"
    if c.status == "expired":
        return f"{c.bank} ({c.country}): consent EXPIRED - run `coach reconnect \"{c.bank}\"`"
    if c.status == "unknown":
        return (f"{c.bank} ({c.country}): consent status UNKNOWN (bank says {c.live_status!r}), "
                f"{c.days_left} day(s) left by date: run `coach consents --refresh`")
    left = f"{c.days_left} day(s) left" if c.days_left is not None else "expiry unknown"
    return f"{c.bank} ({c.country}): consent {c.status.upper()}, {left} (until {c.valid_until})"


def alert_thresholds(c: Consent) -> list[str]:
    """Thresholds a notification should have been sent for ('d14', 'd3', 'expired')."""
    if c.status in DEAD:
        return ["expired"]
    out = []
    if c.days_left is not None and c.days_left <= WARN_DAYS:
        out.append("d14")
    if c.days_left is not None and c.days_left <= URGENT_DAYS:
        out.append("d3")
    return out


def pending_alerts(con, now=None) -> list[tuple[str, Consent]]:
    """(threshold, consent) not yet notified. Only the most severe crossed threshold per session is returned
    (a session first seen at D-2 gets one 'd3' alert, not also 'd14')."""
    done = {(s, t) for s, t in con.execute("SELECT session_id, threshold FROM consent_alerts")}
    out = []
    for c in list_consents(con, now):
        crossed = alert_thresholds(c)
        if not crossed:
            continue
        top = crossed[-1]
        if (c.session_id, top) not in done:
            out.append((top, c))
    return out


def mark_alerted(con, session_id: str, threshold: str) -> None:
    con.execute("INSERT OR IGNORE INTO consent_alerts VALUES (?,?,?)", (session_id, threshold, now_iso()))
    con.commit()


def format_table(consents: list[Consent]) -> str:
    lines = [f"{'bank':<34}{'cty':<5}{'status':<10}{'days left':>10}  valid until / session"]
    for c in consents:
        left = "?" if c.days_left is None else str(c.days_left)
        extra = f"  [bank says: {c.live_status}]" if c.live_status and c.live_status != "AUTHORIZED" else ""
        lines.append(f"{c.bank:<34}{c.country:<5}{c.status:<10}{left:>10}  {c.valid_until}  {c.session_id}{extra}")
    return "\n".join(lines)


def to_json(consents: list[Consent]) -> str:
    return json.dumps([c.__dict__ for c in consents], indent=2)
