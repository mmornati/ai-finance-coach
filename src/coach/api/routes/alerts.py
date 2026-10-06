"""Alerts center (E10): the events, ack / snooze / mute, the read-only channel view and the weekly digest preview.

Everything the web app does here is LOCAL: it changes alert state in the database and never sends anything outside this machine.
``POST /alerts/check`` evaluates the signals and keeps the events but does not dispatch to a channel; ``POST /alerts/channels/{name}/test``
is a DRY RUN that returns the exact message a send would produce. Enabling a channel, its address and its secret are set by the user in
config.toml and the secrets store, not through this API.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from coach.alerts import channels as ch_mod, digest as digest_mod, engine, messages as msg_mod, store
from coach.alerts.settings import CHANNELS, KINDS, SEVERITIES, warnings_of
from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.state import AppState, Snapshot

router = APIRouter(tags=["alerts"])


class SnoozeRequest(BaseModel):
    days: int = Field(7, ge=1, le=180)


def _ready(state: AppState) -> bool:
    with state.read() as con:
        return store.tables_present(con)


def _need_tables(state: AppState) -> None:
    if not _ready(state):
        raise ApiError(409, "migration_pending", "the alerts tables do not exist yet: run `coach db migrate`")


def _event_or_404(state: AppState, aid: str) -> dict:
    with state.read() as con:
        e = store.get(con, aid)
    if e is None:
        raise ApiError(404, "not_found", f"no alert {aid!r}")
    return e


def _kind_or_404(kind: str) -> None:
    if kind not in KINDS:
        raise ApiError(404, "not_found", f"unknown alert kind {kind!r}")


def _settings(state: AppState):
    return state.cfg.alert_settings


def _public(e: dict) -> dict:
    return {k: e[k] for k in ("id", "kind", "severity", "created", "updated", "last_seen", "resolved", "resolved_at", "title", "body",
                              "payload", "status", "snoozed_until", "acked_at", "channels_sent", "escalations")}


# ---------------------------------------------------------------- the list and the badge

@router.get("/alerts", summary="Alerts: the events the engine kept, with their state (ack / snooze / mute)")
def alerts(status: Optional[str] = None, kind: Optional[str] = None, severity: Optional[str] = None, include_resolved: bool = False,
           state: AppState = Depends(get_state)):
    if status is not None and status not in store.STATUSES:
        raise ApiError(422, "bad_status", f"status must be one of {', '.join(store.STATUSES)}")
    if kind is not None and kind not in KINDS:
        raise ApiError(422, "bad_kind", f"kind must be one of {', '.join(KINDS)}")
    if severity is not None and severity not in SEVERITIES:
        raise ApiError(422, "bad_severity", f"severity must be one of {', '.join(SEVERITIES)}")
    s = _settings(state)
    if not _ready(state):
        return {"ready": False, "items": [], "counts": {"open": 0, "new": 0, "high": 0}, "kinds": [], "kinds_known": list(KINDS),
                "message": "The alerts tables do not exist yet: run `coach db migrate`."}
    with state.read() as con:
        items = store.listing(con, status=status, kind=kind, severity=severity, include_resolved=include_resolved, today=state.clock())
        counts = store.counts(con, state.clock())
        prefs = store.kind_prefs(con)
    return {"ready": True, "enabled": s.enabled, "items": [_public(e) for e in items], "counts": counts,
            "kinds": [{"kind": k, "label": msg_mod.KIND_LABEL[k], "muted": bool((prefs.get(k) or {}).get("muted")),
                       "snoozed_until": (prefs.get(k) or {}).get("snoozed_until"), "disabled_in_config": k in s.disabled_kinds,
                       "digest_only": k in s.digest_only_kinds} for k in KINDS],
            "settings": {"min_severity": s.min_severity, "external_detail": s.external_detail, "quiet_hours": s.quiet_hours,
                         "max_per_week": s.max_per_week}}


@router.get("/alerts/summary", summary="Open alerts count for the dashboard badge")
def summary(state: AppState = Depends(get_state)):
    if not _ready(state):
        return {"ready": False, "open": 0, "new": 0, "high": 0}
    with state.read() as con:
        c = store.counts(con, state.clock())
    return {"ready": True, "open": c["open"], "new": c["new"], "high": c["high"]}


# ---------------------------------------------------------------- state changes (local only)

@router.post("/alerts/check", summary="Evaluate the signals now and keep the events (stores only: nothing is sent outside the app)")
def check(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    _need_tables(state)
    s = _settings(state)
    if not s.enabled:
        raise ApiError(409, "alerts_disabled", "alerts are disabled ([alerts] enabled = false in config.toml)")
    with state.write() as con:
        res = engine.evaluate(con, state.cfg, snap.ds, s)
    return {"candidates": res["candidates"], "new": len(res["new"]), "escalated": len(res["escalated"]),
            "reopened": len(res["reopened"]), "resolved": len(res["resolved"]), "unchanged": len(res["unchanged"]), "sent": False}


@router.post("/alerts/{alert_id}/ack", summary="Acknowledge an alert (it returns only if its severity goes up)")
def ack(alert_id: str, state: AppState = Depends(get_state)):
    _event_or_404(state, alert_id)
    with state.write() as con:
        store.ack(con, alert_id)
    return {"id": alert_id, "status": "acked"}


@router.post("/alerts/{alert_id}/snooze", summary="Hold an alert for some days")
def snooze(alert_id: str, req: SnoozeRequest, state: AppState = Depends(get_state)):
    _event_or_404(state, alert_id)
    until = state.clock() + dt.timedelta(days=req.days)
    with state.write() as con:
        store.snooze(con, alert_id, until)
    return {"id": alert_id, "status": "snoozed", "snoozed_until": until.isoformat()}


@router.post("/alerts/{alert_id}/restore", summary="Undo an ack, a snooze or a suppression")
def restore(alert_id: str, state: AppState = Depends(get_state)):
    e = _event_or_404(state, alert_id)
    with state.write() as con:
        store.restore(con, alert_id)
    return {"id": alert_id, "status": "sent" if e["channels_sent"] else "new"}


@router.post("/alerts/kinds/{kind}/mute", summary="Mute a kind of alert until it is unmuted")
def mute_kind(kind: str, state: AppState = Depends(get_state)):
    _kind_or_404(kind)
    _need_tables(state)
    with state.write() as con:
        store.set_muted(con, kind, True)
    return {"kind": kind, "muted": True}


@router.post("/alerts/kinds/{kind}/unmute", summary="Unmute a kind of alert")
def unmute_kind(kind: str, state: AppState = Depends(get_state)):
    _kind_or_404(kind)
    _need_tables(state)
    with state.write() as con:
        store.set_muted(con, kind, False)
    return {"kind": kind, "muted": False}


@router.post("/alerts/kinds/{kind}/snooze", summary="Hold a whole kind of alert for some days (new ones too)")
def snooze_kind(kind: str, req: SnoozeRequest, state: AppState = Depends(get_state)):
    _kind_or_404(kind)
    _need_tables(state)
    until = state.clock() + dt.timedelta(days=req.days)
    with state.write() as con:
        n = store.snooze_kind(con, kind, until)
    return {"kind": kind, "snoozed_until": until.isoformat(), "events": n}


@router.post("/alerts/kinds/{kind}/wake", summary="End the snooze of a kind")
def wake_kind(kind: str, state: AppState = Depends(get_state)):
    _kind_or_404(kind)
    _need_tables(state)
    with state.write() as con:
        n = store.snooze_kind(con, kind, None)
    return {"kind": kind, "snoozed_until": None, "events": n}


# ---------------------------------------------------------------- channels (read-only) and the digest preview

@router.get("/alerts/channels", summary="The channels: enabled, ready, where they send (read-only: enable them in config.toml)")
def channels(state: AppState = Depends(get_state)):
    s = _settings(state)
    rows = ch_mod.status(s, cfg=state.cfg)
    used, recent = {}, []
    if _ready(state):
        now = dt.datetime.now(dt.timezone.utc)
        with state.read() as con:
            used = {c: store.deliveries_since(con, c, now - engine.WEEK) for c in CHANNELS}
            recent = store.recent_deliveries(con, 10)
    for r in rows:
        r["sent_last_7_days"] = used.get(r["channel"], 0)
    return {"in_app": {"enabled": True, "note": "The in-app feed is always on."}, "channels": rows,
            "external_detail": s.external_detail, "min_severity": s.min_severity, "quiet_hours": s.quiet_hours,
            "max_per_week": s.max_per_week, "weekly_digest": s.weekly_digest, "weekly_digest_to_channels": s.weekly_digest_to_channels, "warnings": warnings_of(s, state.cfg.ui_allowed_hosts),
            "recent_deliveries": recent,
            "how_to_enable": "Edit config.toml ([alerts.<channel>] enabled = true), store the secret with `coach config set-secret`, then "
                             "check it with `coach alerts test-channel <name> --dry-run`. Nothing is enabled from this page."}


@router.post("/alerts/channels/{name}/test", summary="DRY RUN: the exact message a test send would produce (nothing is sent)")
def test_channel(name: str, state: AppState = Depends(get_state)):
    if name not in CHANNELS:
        raise ApiError(404, "not_found", f"unknown channel {name!r}")
    s = _settings(state)
    guard = None
    if name != "macos":
        with state.read() as con:
            guard = msg_mod.external_guard(state.cfg, con)
    m = engine.compose(name, msg_mod.sample_events(), s, guard, title="Coach (test)")
    if m is None:
        raise ApiError(409, "refused", "the message was refused by the privacy filter")
    t = ch_mod.Transports()
    c = s.channel(name)
    probs = ch_mod.problems(s, name, t, state.cfg) if c.enabled else []
    return {"dry_run": True, "sent": False, "channel": name, "enabled": c.enabled, "ready": c.enabled and not probs, "problems": probs,
            "detail": m.detail, "note": m.note, "message": {"title": m.title, "body": m.body}, "request": ch_mod.render(s, name, m, t, mask_target=True),
            "text": ch_mod.format_render(ch_mod.render(s, name, m, t, mask_target=True)),
            "sample": "built from invented sample events (nothing of your data)"}


@router.get("/alerts/digest", summary="Preview of the weekly summary (rendered locally, nothing stored or sent)")
def digest(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    s = _settings(state)
    with state.read() as con:
        d = digest_mod.build(con, state.cfg, snap.ds, s, today=snap.ds.today)
    return d
