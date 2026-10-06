"""Subscriptions (recurring series), price changes, anomalies and the insights feed (E5-8, E5-11)."""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.analytics import anomalies as an_mod, budgets as budgets_mod, forecast as forecast_mod, pricechanges
from coach.agent import insights as ins_mod
from coach.api import views
from coach.api.coachjobs import availability
from coach.api.deps import ScopeParams, get_member_snapshot, get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.state import AppState, Snapshot

router = APIRouter(tags=["insights"])


class DismissRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=300)


class SnoozeRequest(BaseModel):
    days: int = Field(7, ge=1, le=180)


# ---------------------------------------------------------------- helpers

def _dismissed_anomalies(state: AppState) -> set:
    with state.read() as con:
        return {r[0] for r in con.execute("SELECT id FROM anomalies WHERE dismissed_at IS NOT NULL")}


def _all_anomalies(snap: Snapshot):
    return snap.once("anomalies_all", lambda: an_mod.detect_anomalies(snap.ds, None, snap.recurring(), frozenset(), True))


def anomalies_view(snap: Snapshot, state: AppState, include_dismissed: bool = False):
    dism = _dismissed_anomalies(state)
    res = _all_anomalies(snap)
    out = []
    for a in res.anomalies:
        d = a.id in dism
        if d and not include_dismissed:
            continue
        out.append(dc.replace(a, dismissed=d))
    return res, out, len(dism)


def _all_pcs(snap: Snapshot):
    return snap.once("pcs_all", lambda: pricechanges.price_changes(snap.ds, None, snap.recurring(), include_dismissed=True))


def pcs_view(snap: Snapshot, state: AppState, include_dismissed: bool = False):
    with state.read() as con:
        dism = pricechanges.dismissed_ids(con)
    res = _all_pcs(snap)
    out = []
    for c in res.changes:
        d = c.id in dism
        if d and not include_dismissed:
            continue
        out.append(dc.replace(c, dismissed=d))
    return res, out


# ---------------------------------------------------------------- subscriptions

@router.get("/subscriptions", summary="Recurring payments: cost per month and year, next charge, price changes, contract link")
def subscriptions(status: str = Query("active", pattern="^(active|ended|all)$"),
                  kind: str = Query("expense", pattern="^(expense|income|saving|transfer|inflow|all)$"),
                  cadence: Optional[str] = None, contracts_only: bool = False,
                  sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    _, pcs = pcs_view(snap, state, include_dismissed=True)
    # keep the analytics rendering (to_dict) of each change, with the dismissed flag from the database
    pcs_ns = SimpleNamespace(changes=[_PC(c) for c in pcs])
    return views.subscriptions(ds, snap.recurring(), pcs_ns, sp.scope(ds), status, kind, cadence, contracts_only)


class _PC:
    def __init__(self, c):
        self._c = c
        self.series_id = c.series_id

    def to_dict(self) -> dict:
        return _plain(self._c)


@router.get("/price-changes", summary="Price changes of recurring payments")
def price_changes_(include_dismissed: bool = False, snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    res, out = pcs_view(snap, state, include_dismissed)
    d = res.to_dict()
    d["changes"] = [_PC(c).to_dict() for c in out]
    return d


@router.post("/price-changes/{change_id}/dismiss", summary="Dismiss a price change (kept dismissed across refreshes)")
def dismiss_price_change(change_id: str, req: Optional[DismissRequest] = None, snap: Snapshot = Depends(get_snapshot),
                         state: AppState = Depends(get_state)):
    if change_id not in {c.id for c in _all_pcs(snap).changes}:
        raise ApiError(404, "not_found", f"no current price change {change_id!r}")
    with state.write() as con:
        pricechanges.dismiss_price_change(con, change_id, req.note if req else None)
    return {"id": change_id, "dismissed": True}


# ---------------------------------------------------------------- anomalies

@router.get("/anomalies", summary="Anomalies: category spikes, duplicate charges, new merchants, large payments")
def anomalies(include_dismissed: bool = False, snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    res, out, n_dismissed = anomalies_view(snap, state, include_dismissed)
    d = res.to_dict()
    d["anomalies"] = [{**_plain(a)} for a in out]
    d["dismissed"] = n_dismissed
    return d


def _plain(a) -> dict:
    from coach.analytics.common import _plain as p
    return p(a)


@router.post("/anomalies/{anomaly_id}/dismiss", summary="Dismiss an anomaly")
def dismiss_anomaly(anomaly_id: str, req: Optional[DismissRequest] = None, snap: Snapshot = Depends(get_snapshot),
                    state: AppState = Depends(get_state)):
    ds = snap.ds
    with state.write() as con:
        ok = an_mod.dismiss_anomaly(con, anomaly_id, req.note if req else None, ds=ds)
    if not ok:
        raise ApiError(404, "not_found", f"no anomaly {anomaly_id!r}")
    return {"id": anomaly_id, "dismissed": True}


@router.post("/anomalies/{anomaly_id}/undismiss", summary="Bring a dismissed anomaly back")
def undismiss_anomaly(anomaly_id: str, state: AppState = Depends(get_state)):
    with state.write() as con:
        ok = an_mod.undismiss_anomaly(con, anomaly_id)
    if not ok:
        raise ApiError(404, "not_found", f"no anomaly {anomaly_id!r}")
    return {"id": anomaly_id, "dismissed": False}


# ---------------------------------------------------------------- insights feed

def _cards(snap: Snapshot, state: AppState) -> list[dict]:
    ds = snap.ds
    _, anoms, _ = anomalies_view(snap, state)
    _, pcs = pcs_view(snap, state)
    fc = snap.once(("forecast", 90, True, None), lambda: forecast_mod.forecast(ds, 90, None, recurring=snap.recurring(), points=True))
    bs = snap.once("budget_status", lambda: budgets_mod.budget_status(ds, recurring=snap.recurring()))
    return views.build_insights(ds, SimpleNamespace(anomalies=anoms), SimpleNamespace(changes=pcs), fc, bs, snap.recurring())


def _coach_items(state: AppState, include_hidden: bool = False) -> tuple[list[dict], int]:
    try:
        with state.read() as con:
            return ins_mod.listing(con, today=state.clock(), include_hidden=include_hidden)
    except Exception:                                    # noqa: BLE001 - e.g. migration 0013 not applied yet ([db] auto_migrate = false)
        return [], 0


def _alert_counts(state: AppState) -> dict:
    """The alerts center (E10) in the feed: how many alerts are open, so the page can point at it."""
    from coach.alerts import store as alert_store
    try:
        with state.read() as con:
            if alert_store.tables_present(con):
                c = alert_store.counts(con, state.clock())
                return {"open": c["open"], "high": c["high"]}
    except Exception:                                    # noqa: BLE001 - migration 0017 not applied yet
        pass
    return {"open": 0, "high": 0}


@router.get("/insights", summary="Insights feed: anomalies, price changes, forecast flags and budget overruns, plus the coach's own insights (E6-7)")
def insights(include_snoozed: bool = False, snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    cards = _cards(snap, state)
    today = state.clock()
    snoozed, dismissed = state.ui.snoozed(today), state.ui.dismissed()
    shown, hidden = [], 0
    for c in cards:
        if c["id"] in dismissed:
            hidden += 1
            continue
        until = snoozed.get(c["id"])
        if until and not include_snoozed:
            hidden += 1
            continue
        c["snoozed_until"] = until
        shown.append(c)
    items, coach_hidden = _coach_items(state, include_snoozed)
    ok, msg = availability(state.cfg)
    return {"as_of": snap.ds.today.isoformat(), "cards": shown, "hidden": hidden, "alerts": _alert_counts(state),
            "counts": {k: sum(1 for c in shown if c["kind"] == k) for k in ("anomaly", "price_change", "forecast", "budget", "subscription", "loan", "rental")},
            "coach": {"configured": ok, "items": items, "hidden": coach_hidden,
                      "message": msg or "Nothing written by the coach yet: ask a question, or enable the weekly digest "
                                        "([coach] schedule_weekly)."}}


def _is_coach(insight_id: str) -> bool:
    return insight_id.startswith(ins_mod.ID_PREFIX)


def _coach_set(state: AppState, insight_id: str, status: str, until: Optional[str] = None) -> dict:
    with state.write() as con:
        if not ins_mod.set_status(con, insight_id, status, until):
            raise ApiError(404, "not_found", f"no coach insight {insight_id!r}")
    return {"id": insight_id, "status": status, "snoozed_until": until}


@router.post("/insights/{insight_id}/dismiss", summary="Dismiss an insight (persisted)")
def dismiss_insight(insight_id: str, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    if _is_coach(insight_id):
        return {**_coach_set(state, insight_id, "dismissed"), "dismissed": True}
    card = next((c for c in _cards(snap, state) if c["id"] == insight_id), None)
    if card is None:
        raise ApiError(404, "not_found", f"no current insight {insight_id!r}")
    if card["persist"] == "anomaly":
        with state.write() as con:
            an_mod.dismiss_anomaly(con, insight_id, None, ds=snap.ds)
    elif card["persist"] == "price_change":
        with state.write() as con:
            pricechanges.dismiss_price_change(con, insight_id)
    else:
        state.ui.dismiss(insight_id)
    return {"id": insight_id, "dismissed": True}


@router.post("/insights/{insight_id}/snooze", summary="Hide an insight for some days (persisted)")
def snooze_insight(insight_id: str, req: SnoozeRequest, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    if _is_coach(insight_id):
        return _coach_set(state, insight_id, "snoozed", (state.clock() + dt.timedelta(days=req.days)).isoformat())
    if insight_id not in {c["id"] for c in _cards(snap, state)}:
        raise ApiError(404, "not_found", f"no current insight {insight_id!r}")
    until = state.clock() + dt.timedelta(days=req.days)
    state.ui.snooze(insight_id, until)
    return {"id": insight_id, "snoozed_until": until.isoformat()}


@router.post("/insights/{insight_id}/restore", summary="Undo a snooze or a dismissal")
def restore_insight(insight_id: str, state: AppState = Depends(get_state)):
    if _is_coach(insight_id):
        return {**_coach_set(state, insight_id, "new"), "restored": True}
    state.ui.unsnooze(insight_id)
    with state.write() as con:
        an_mod.undismiss_anomaly(con, insight_id)
        con.execute("DELETE FROM price_change_dismissals WHERE id=?", (insight_id,))
        con.commit()
    return {"id": insight_id, "restored": True}


@router.post("/insights/{insight_id}/done", summary="Mark an insight as done (the coach's: status done; a card: dismissed)")
def done_insight(insight_id: str, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    if _is_coach(insight_id):
        return _coach_set(state, insight_id, "done")
    return dismiss_insight(insight_id, snap, state)


@router.post("/insights/{insight_id}/read", summary="Mark a coach insight as read")
def read_insight(insight_id: str, state: AppState = Depends(get_state)):
    if not _is_coach(insight_id):
        raise ApiError(404, "not_found", "only the coach's insights have a read state")
    return _coach_set(state, insight_id, "read")
