"""The ``alert_events`` / ``alert_deliveries`` / ``alert_kind_prefs`` tables (migration 0017): reads and the small state changes the
user makes (ack, snooze, mute). The engine (``coach.alerts.engine``) does the upserts."""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from coach.alerts.settings import KINDS, RANK, SEVERITIES

STATUSES = ("new", "sent", "acked", "snoozed", "suppressed")
COLS = ("id", "kind", "severity", "created", "updated", "last_seen", "resolved_at", "title", "body", "payload", "status",
        "snoozed_until", "acked_at", "channels_sent", "escalations")


def now_iso(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(zip(COLS, r))
    for k in ("payload", "channels_sent"):
        try:
            d[k] = json.loads(d[k] or "{}")
        except ValueError:
            d[k] = {}
    d["resolved"] = d["resolved_at"] is not None
    return d


def tables_present(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_events'").fetchone() is not None


def get(con, aid: str) -> Optional[dict]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM alert_events WHERE id=?", (aid,)).fetchone()
    return _row(r) if r else None


def find(con, ref: str) -> list[dict]:
    """An event by its full id or an unambiguous prefix."""
    if not ref:
        return []
    rows = con.execute(f"SELECT {', '.join(COLS)} FROM alert_events WHERE id=? OR id LIKE ?", (ref, ref + "%")).fetchall()
    exact = [r for r in rows if r[0] == ref]
    return [_row(r) for r in (exact or rows)]


def listing(con, *, status: Optional[str] = None, kind: Optional[str] = None, severity: Optional[str] = None,
            include_resolved: bool = False, today: Optional[dt.date] = None, limit: int = 500) -> list[dict]:
    """Newest first, high severity first. A snoozed event whose date has passed is shown as it will be handled (not snoozed)."""
    today_s = (today or dt.date.today()).isoformat()
    out = []
    for r in con.execute(f"SELECT {', '.join(COLS)} FROM alert_events ORDER BY created DESC LIMIT ?", (limit,)):
        d = _row(r)
        if d["status"] == "snoozed" and (d["snoozed_until"] or "") < today_s:
            d["status"] = "sent" if d["channels_sent"] else "new"
            d["snoozed_until"] = None
        if d["resolved"] and not include_resolved:
            continue
        if status and d["status"] != status:
            continue
        if kind and d["kind"] != kind:
            continue
        if severity and d["severity"] != severity:
            continue
        out.append(d)
    out.sort(key=lambda d: -RANK[d["severity"]])
    return out


def open_events(con, today: Optional[dt.date] = None) -> list[dict]:
    """What needs the user: not resolved, not acked, not suppressed, not snoozed."""
    return [d for d in listing(con, today=today) if d["status"] in ("new", "sent")]


def counts(con, today: Optional[dt.date] = None) -> dict:
    rows = listing(con, today=today)
    open_ = [d for d in rows if d["status"] in ("new", "sent")]
    return {"open": len(open_), "new": sum(1 for d in open_ if d["status"] == "new"),
            "high": sum(1 for d in open_ if d["severity"] == "high"),
            "snoozed": sum(1 for d in rows if d["status"] == "snoozed"),
            "acked": sum(1 for d in rows if d["status"] == "acked"),
            "suppressed": sum(1 for d in rows if d["status"] == "suppressed"),
            "by_kind": {k: sum(1 for d in open_ if d["kind"] == k) for k in KINDS if any(d["kind"] == k for d in open_)},
            "by_severity": {s: sum(1 for d in open_ if d["severity"] == s) for s in SEVERITIES}}


# ---------------------------------------------------------------- user state changes (local state only)

def ack(con, aid: str, now: Optional[dt.datetime] = None) -> bool:
    cur = con.execute("UPDATE alert_events SET status='acked', acked_at=?, snoozed_until=NULL, updated=? WHERE id=?",
                      (now_iso(now), now_iso(now), aid))
    con.commit()
    return cur.rowcount > 0


def snooze(con, aid: str, until: dt.date, now: Optional[dt.datetime] = None) -> bool:
    cur = con.execute("UPDATE alert_events SET status='snoozed', snoozed_until=?, updated=? WHERE id=?",
                      (until.isoformat(), now_iso(now), aid))
    con.commit()
    return cur.rowcount > 0


def restore(con, aid: str, now: Optional[dt.datetime] = None) -> bool:
    """Undo an ack, a snooze or a suppression: back to new (or sent when a channel already got it)."""
    d = get(con, aid)
    if d is None:
        return False
    status = "sent" if d["channels_sent"] else "new"
    con.execute("UPDATE alert_events SET status=?, acked_at=NULL, snoozed_until=NULL, updated=? WHERE id=?",
                (status, now_iso(now), aid))
    con.commit()
    return True


# ---------------------------------------------------------------- kind preferences

def kind_prefs(con) -> dict:
    return {k: {"muted": bool(m), "snoozed_until": s} for k, m, s in
            con.execute("SELECT kind, muted, snoozed_until FROM alert_kind_prefs")}


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind!r} (known: {', '.join(KINDS)})")


def set_muted(con, kind: str, muted: bool, now: Optional[dt.datetime] = None) -> None:
    _check_kind(kind)
    con.execute("""INSERT INTO alert_kind_prefs(kind, muted, snoozed_until, updated) VALUES (?,?,NULL,?)
                   ON CONFLICT(kind) DO UPDATE SET muted=excluded.muted, updated=excluded.updated""",
                (kind, int(muted), now_iso(now)))
    if muted:
        con.execute("UPDATE alert_events SET status='suppressed', updated=? WHERE kind=? AND status IN ('new','sent')",
                    (now_iso(now), kind))
    else:
        con.execute("UPDATE alert_events SET status=CASE WHEN channels_sent='{}' THEN 'new' ELSE 'sent' END, updated=? "
                    "WHERE kind=? AND status='suppressed'", (now_iso(now), kind))
    con.commit()


def snooze_kind(con, kind: str, until: Optional[dt.date], now: Optional[dt.datetime] = None) -> int:
    """Snooze (or, with until=None, wake) a whole kind: its events and the ones that arrive meanwhile. Returns the events changed."""
    _check_kind(kind)
    con.execute("""INSERT INTO alert_kind_prefs(kind, muted, snoozed_until, updated) VALUES (?,0,?,?)
                   ON CONFLICT(kind) DO UPDATE SET snoozed_until=excluded.snoozed_until, updated=excluded.updated""",
                (kind, until.isoformat() if until else None, now_iso(now)))
    if until:
        cur = con.execute("UPDATE alert_events SET status='snoozed', snoozed_until=?, updated=? WHERE kind=? AND resolved_at IS NULL "
                          "AND status IN ('new','sent')", (until.isoformat(), now_iso(now), kind))
    else:
        cur = con.execute("UPDATE alert_events SET status=CASE WHEN channels_sent='{}' THEN 'new' ELSE 'sent' END, snoozed_until=NULL, "
                          "updated=? WHERE kind=? AND status='snoozed'", (now_iso(now), kind))
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------- deliveries

def deliveries_since(con, channel: str, since: dt.datetime, what: tuple = ("alert", "digest")) -> int:
    q = ",".join("?" * len(what))
    return con.execute(f"SELECT COUNT(*) FROM alert_deliveries WHERE channel=? AND ok=1 AND sent_at>=? AND what IN ({q})",
                       (channel, now_iso(since), *what)).fetchone()[0]


def record_delivery(con, channel: str, what: str, n_events: int, ok: bool, error: Optional[str] = None, ref: Optional[str] = None,
                    now: Optional[dt.datetime] = None) -> None:
    con.execute("INSERT INTO alert_deliveries(sent_at, channel, what, ref, n_events, ok, error) VALUES (?,?,?,?,?,?,?)",
                (now_iso(now), channel, what, ref, n_events, int(ok), error))
    con.commit()


def digest_sent(con, channel: str, ref: str) -> bool:
    return con.execute("SELECT 1 FROM alert_deliveries WHERE channel=? AND what='digest' AND ref=? AND ok=1", (channel, ref)).fetchone() is not None


def recent_deliveries(con, limit: int = 20) -> list[dict]:
    cols = ("sent_at", "channel", "what", "ref", "n_events", "ok", "error")
    return [dict(zip(cols, r)) | {"ok": bool(r[5])} for r in
            con.execute(f"SELECT {', '.join(cols)} FROM alert_deliveries ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- channel baseline

def baselined(con, channel: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM alert_channel_state WHERE channel=?", (channel,)).fetchone() is not None
    except Exception:                                                   # noqa: BLE001 - migration 0018 not applied yet
        return True


def mark_baselined(con, channel: str, n: int, now: Optional[dt.datetime] = None) -> None:
    con.execute("INSERT OR IGNORE INTO alert_channel_state(channel, baselined_at, n_events) VALUES (?,?,?)", (channel, now_iso(now), n))
