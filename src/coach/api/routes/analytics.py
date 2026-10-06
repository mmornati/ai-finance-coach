"""Cash flow, averages, forecast, categories, calendar."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from coach.analytics import averages, cashflow as cashflow_mod, forecast as forecast_mod, upcoming
from coach.api import views
from coach.api.deps import ScopeParams, get_member_snapshot, get_snapshot
from coach.api.errors import ApiError
from coach.api.state import Snapshot

router = APIRouter(tags=["analytics"])


@router.get("/analytics/cashflow", summary="Income, spending, saved, debt service and savings rate per month")
def cashflow(months: int = Query(6, ge=1, le=36), include_current: bool = True, by: Optional[str] = Query(None, pattern="^(owner|purpose|all)$"),
             sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    d = cashflow_mod.cashflow(ds, sp.scope(ds), months=months, include_current=include_current).to_dict()
    if by is None:                       # the per-owner / per-purpose blocks are large: only on request
        d["by_purpose"], d["by_owner"] = {}, {}
    return d


@router.get("/analytics/averages", summary="Coverage-aware monthly averages per category and for the household")
def averages_(window: Optional[int] = Query(None, ge=1, le=36), sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    return averages.category_averages(ds, sp.scope(ds), window).to_dict()


@router.get("/analytics/month-categories", summary="One month's spending per category next to its average")
def month_categories(month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), sp: ScopeParams = Depends(),
                     snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    return views.month_categories(ds, sp.scope(ds), month)


@router.get("/analytics/forecast", summary="Balance forecast with band, events and at-risk flags")
def forecast(days: int = Query(90, ge=7, le=365), points: bool = True, sp: ScopeParams = Depends(),
             snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    scope = sp.scope(ds)
    key = ("forecast", days, points, scope)
    return snap.once(key, lambda: forecast_mod.forecast(ds, days, scope, recurring=snap.recurring(), points=points).to_dict())


@router.get("/categories", summary="Every spending category with its average, this month and last month")
def categories(sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    return views.category_overview(ds, sp.scope(ds))


@router.get("/categories/{category_id}", summary="Drill-down of a category or a group: monthly chart, merchants, trend, one-offs")
def category(category_id: str, months: int = Query(24, ge=3, le=60), sp: ScopeParams = Depends(),
             snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    try:
        return views.category_detail(ds, sp.scope(ds), category_id, months)
    except KeyError:
        raise ApiError(404, "unknown_category", f"no category or group {category_id!r}")


@router.get("/calendar", summary="Upcoming payments, renewals, consents and reminders (a month, or the next N days)")
def calendar(month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), days: Optional[int] = Query(None, ge=1, le=400),
             snap: Snapshot = Depends(get_member_snapshot)):
    return views.calendar_for(snap.ds, snap.recurring(), month, days)


@router.get("/calendar.ics", summary="The calendar as an iCalendar file", response_class=Response)
def calendar_ics(days: int = Query(90, ge=1, le=400), snap: Snapshot = Depends(get_member_snapshot)):
    res = upcoming.calendar_items(snap.ds, days, recurring=snap.recurring())
    return Response(upcoming.to_ics(res), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="finance-coach.ics"'})
