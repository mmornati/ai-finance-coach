"""The alert engine (E10-2, E10-4): ``evaluate`` turns the signals into stored events; ``dispatch`` hands the ones a channel has not
had yet to that channel. ``run`` does both; the daily job and ``coach alerts check`` call it.

Rules (each has a test):
* One event per situation: the id is a hash of the signal's dedupe key, evaluation upserts. Running the check twice changes nothing.
* A signal that disappears marks its event ``resolved`` (kept as history). If it comes back within 7 days (a flapping signal) it is
  the SAME event, unresolved and not re-sent; after 7 days it is a new occurrence.
* An event is sent to a channel once; its severity is the highest it reached (it resets with a new occurrence). It is sent again only when its severity goes UP (escalation); an acked event comes back as new
  on an escalation, a snoozed or muted one does not.
* Noise control: kinds in ``disabled_kinds`` are not evaluated; muted kinds and events under ``min_severity`` are kept as
  ``suppressed``; ``digest_only_kinds`` are shown in the app but never sent one by one; quiet hours hold every channel back (the events
  stay pending and go out at the first check after the window); ``max_per_week`` bounds the messages per channel in a rolling 7 days.
* The in-app feed is the table itself. Everything outside the app is opt-in (see channels.py) and goes through messages.py (minimal
  and guarded for the external ones).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from coach.alerts import channels as ch_mod, messages as msg_mod, signals, store
from coach.alerts.settings import CHANNELS, EXTERNAL, LOCAL_ONLY_KINDS, RANK, AlertSettings

REOPEN_DAYS = 7
WEEK = dt.timedelta(days=7)


def _utc(now: Optional[dt.datetime]) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)


def in_quiet_hours(s: AlertSettings, now: dt.datetime, tz: Optional[dt.tzinfo] = None) -> bool:
    q = s.quiet()
    if q is None:
        return False
    local = _utc(now).astimezone(tz)                     # tz=None: the machine's local zone
    m = local.hour * 60 + local.minute
    a, b = q
    return (a <= m < b) if a < b else (m >= a or m < b)


# ---------------------------------------------------------------- evaluate

def _initial_status(kind: str, sev: str, s: AlertSettings, prefs: dict, today: dt.date) -> tuple[str, Optional[str]]:
    p = prefs.get(kind) or {}
    if p.get("muted") or RANK[sev] < RANK[s.min_severity]:
        return "suppressed", None
    if p.get("snoozed_until") and p["snoozed_until"] >= today.isoformat():
        return "snoozed", p["snoozed_until"]
    return "new", None


def sync_events(con, cands: list[signals.Candidate], s: AlertSettings, *, now: Optional[dt.datetime] = None,
                today: Optional[dt.date] = None) -> dict:
    """Upsert the candidates and resolve the events whose signal is gone. Does not commit (the caller does, or rolls back for a dry run)."""
    now = _utc(now)
    today = today or now.date()
    ts = store.now_iso(now)
    prefs = store.kind_prefs(con)
    have = {d["id"]: d for d in (store._row(r) for r in con.execute(f"SELECT {', '.join(store.COLS)} FROM alert_events"))}
    wanted = [c for c in cands if c.kind not in s.disabled_kinds]
    seen = {c.id for c in wanted}
    res = {"new": [], "escalated": [], "reopened": [], "resolved": [], "unchanged": []}
    for c in wanted:
        old = have.get(c.id)
        # the title / body messages ride in the payload (local, for the web's translation): no schema change, and an older row
        # without them shows its English title / body
        msgs = {k: v for k, v in (("title_msg", c.title_msg), ("body_msg", c.body_msg)) if v}
        payload = json.dumps({**c.payload, **msgs}, ensure_ascii=False, default=str)
        if old is None:
            status, until = _initial_status(c.kind, c.severity, s, prefs, today)
            con.execute("INSERT INTO alert_events(id, kind, severity, created, updated, last_seen, title, body, payload, status, "
                        "snoozed_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (c.id, c.kind, c.severity, ts, ts, ts, c.title[:300], c.body[:1500], payload, status, until))
            res["new"].append(c.id)
            continue
        status, until, sev, esc = old["status"], old["snoozed_until"], old["severity"], old["escalations"]
        created, sent = old["created"], json.dumps(old["channels_sent"])
        changed = None
        if old["resolved"]:
            age = (now - dt.datetime.fromisoformat(old["resolved_at"])).days
            if age >= REOPEN_DAYS:                              # a new occurrence of the situation
                status, until = _initial_status(c.kind, c.severity, s, prefs, today)
                sev, esc, created, sent, changed = c.severity, 0, ts, "{}", "reopened"
            else:
                changed = "unresolved"
        if RANK[c.severity] > RANK[sev]:
            sev, esc, changed = c.severity, esc + 1, changed or "escalated"
            if status in ("acked", "sent"):
                status = "new"
            elif status == "suppressed" and not (prefs.get(c.kind) or {}).get("muted") and RANK[sev] >= RANK[s.min_severity]:
                status, until = _initial_status(c.kind, sev, s, prefs, today)
        if status == "suppressed" and not (prefs.get(c.kind) or {}).get("muted") and RANK[sev] >= RANK[s.min_severity]:
            status, until = _initial_status(c.kind, sev, s, prefs, today)      # min_severity was lowered, or the kind was unmuted
            changed = changed or "unsuppressed"
        if status == "snoozed" and (until or "") < today.isoformat():          # the snooze has run out
            status, until = ("sent" if old["channels_sent"] else "new"), None
        con.execute("UPDATE alert_events SET severity=?, created=?, updated=?, last_seen=?, resolved_at=NULL, title=?, body=?, payload=?, "
                    "status=?, snoozed_until=?, channels_sent=?, escalations=?, acked_at=CASE WHEN ?='acked' THEN acked_at END WHERE id=?",
                    (sev, created, ts if changed else old["updated"], ts, c.title[:300], c.body[:1500], payload, status, until, sent,
                     esc, status, c.id))
        (res["escalated"] if changed == "escalated" else res["reopened"] if changed == "reopened" else res["unchanged"]).append(c.id)
    for aid, old in have.items():
        if aid not in seen and not old["resolved"]:
            con.execute("UPDATE alert_events SET resolved_at=?, updated=? WHERE id=?", (ts, ts, aid))
            res["resolved"].append(aid)
    return res


def evaluate(con, cfg, ds, s: AlertSettings, *, now: Optional[dt.datetime] = None, dry_run: bool = False) -> dict:
    now = _utc(now)
    cands = signals.all_candidates(con, cfg, ds, s, now)
    try:
        res = sync_events(con, cands, s, now=now, today=ds.today)
        if dry_run:
            con.rollback()
        else:
            con.commit()
    except BaseException:
        con.rollback()
        raise
    res["candidates"] = len(cands)
    return res


# ---------------------------------------------------------------- dispatch

def _pending_for(events: list[dict], channel: str, s: AlertSettings) -> list[dict]:
    out = []
    for e in events:
        if e["status"] not in ("new", "sent") or e["resolved"] or e["kind"] in s.digest_only_kinds or e["kind"] in LOCAL_ONLY_KINDS:
            continue
        if RANK[e["severity"]] < max(RANK[s.min_severity], RANK[s.external_min_severity]):
            continue
        got = e["channels_sent"].get(channel)
        if got is None or RANK[got["severity"]] < RANK[e["severity"]]:
            out.append(e)
    return out


def gate(con, s: AlertSettings, channel: str, now: dt.datetime, tz=None) -> Optional[str]:
    """Why nothing may be sent to the channel right now (None = go): quiet hours, the weekly budget."""
    if in_quiet_hours(s, now, tz):
        return "quiet hours"
    used = store.deliveries_since(con, channel, now - WEEK)
    if used >= s.max_per_week:
        return f"weekly limit reached ({used}/{s.max_per_week} messages in 7 days)"
    return None


def compose(channel: str, events: list[dict], s: AlertSettings, guard, title: Optional[str] = None) -> Optional[msg_mod.Message]:
    if channel == "macos":
        return msg_mod.compose_local(events)
    return msg_mod.compose_external(events, s.external_detail, guard, s.app_url, title)


def dispatch(con, cfg, s: AlertSettings, *, now: Optional[dt.datetime] = None, today: Optional[dt.date] = None, dry_run: bool = False,
             transports: Optional[ch_mod.Transports] = None, tz=None) -> list[dict]:
    """One result per enabled channel: {channel, status: sent|nothing|deferred|failed|not_ready|would_send, reason, n, message, request}."""
    now = _utc(now)
    today = today or now.astimezone(tz).date()
    t = transports or ch_mod.Transports()
    events = store.listing(con, today=today)
    guard = None
    out = []
    for name in CHANNELS:
        if not s.channel(name).enabled:
            continue
        r = {"channel": name, "status": "nothing", "reason": None, "n": 0, "message": None, "request": None}
        out.append(r)
        probs = ch_mod.problems(s, name, t, cfg)
        if probs:
            r.update(status="not_ready", reason="; ".join(probs))
            continue
        pend = _pending_for(events, name, s)
        if not store.baselined(con, name):                       # first time this channel is enabled: the existing alerts are history, not a backlog
            old = [e for e in pend if e["created"] < store.now_iso(now)]
            pend = [e for e in pend if e not in old]
            r["baselined"] = len(old)
            if not dry_run:
                for e in old:
                    sent = {**e["channels_sent"], name: {"at": store.now_iso(now), "severity": e["severity"], "baseline": True}}
                    e["channels_sent"] = sent
                    con.execute("UPDATE alert_events SET channels_sent=?, updated=? WHERE id=?", (json.dumps(sent), store.now_iso(now), e["id"]))
                store.mark_baselined(con, name, len(old), now)
                con.commit()
        r["n"] = len(pend)
        if not pend:
            continue
        why = gate(con, s, name, now, tz)
        if why:
            r.update(status="deferred", reason=why)
            continue
        if name in EXTERNAL and guard is None:
            guard = msg_mod.external_guard(cfg, con)
        m = compose(name, pend, s, guard)
        if m is None:
            r.update(status="failed", reason="the message was refused by the privacy filter")
            continue
        r["message"] = {"title": m.title, "body": m.body, "detail": m.detail, "note": m.note}
        r["request"] = ch_mod.render(s, name, m, t)
        if dry_run:
            r["status"] = "would_send"
            continue
        try:
            ch_mod.send(s, name, m, t, cfg)
        except ch_mod.ChannelError as e:
            store.record_delivery(con, name, "alert", len(pend), False, str(e)[:120], now=now)
            r.update(status="failed", reason=str(e)[:120])
            continue
        store.record_delivery(con, name, "alert", len(pend), True, now=now)
        for e in pend:
            sent = {**e["channels_sent"], name: {"at": store.now_iso(now), "severity": e["severity"]}}
            e["channels_sent"] = sent                      # the same dict is read for the next channel
            con.execute("UPDATE alert_events SET channels_sent=?, status=CASE WHEN status='new' THEN 'sent' ELSE status END, updated=? "
                        "WHERE id=?", (json.dumps(sent), store.now_iso(now), e["id"]))
        con.commit()
        r["status"] = "sent"
    return out


def run(con, cfg, ds, *, s: Optional[AlertSettings] = None, now: Optional[dt.datetime] = None, dry_run: bool = False, send: bool = True,
        transports: Optional[ch_mod.Transports] = None, tz=None) -> dict:
    """evaluate + dispatch. dry_run: nothing is written and nothing is sent (the dispatch shows what WOULD go)."""
    s = s or cfg.alert_settings
    now = _utc(now)
    cands = signals.all_candidates(con, cfg, ds, s, now)
    try:
        ev = sync_events(con, cands, s, now=now, today=ds.today)
        ev["candidates"] = len(cands)
        disp = dispatch(con, cfg, s, now=now, today=ds.today, dry_run=dry_run, transports=transports, tz=tz) if send else []
        events = store.listing(con, today=ds.today)
        if dry_run:
            con.rollback()                       # the preview saw the new events; nothing of it is kept
        else:
            con.commit()
    except BaseException:
        con.rollback()
        raise
    return {"evaluated": ev, "dispatch": disp, "events": events}
