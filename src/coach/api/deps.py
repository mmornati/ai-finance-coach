"""FastAPI dependencies shared by the routers."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Query, Request

from coach.analytics.common import Scope
from coach.api import views
from coach.api.errors import ApiError
from coach.api.state import AppState, Snapshot


def get_state(request: Request) -> AppState:
    return request.app.state.coach


def _child_member(request: Request) -> Optional[str]:
    """The member a CHILD login is confined to (None for any other login). A child that belongs to no member is refused outright."""
    user = getattr(request.state, "user", None)
    if user is not None and user.is_child:
        if not user.member_id:
            raise ApiError(403, "forbidden_scope", "this login belongs to no member")
        return user.member_id
    return None


def get_snapshot(request: Request, state: AppState = Depends(get_state)) -> Snapshot:
    """The analytics dataset of the moment for the WHOLE household: what loans, contracts, net worth, alerts, the review queue and every
    write use. It never takes a person filter (a write or a household-wide figure computed on one member's view would be wrong), except
    that a child login is ALWAYS narrowed to its own member (the guard middleware also denies the endpoints a child may not call)."""
    snap = state.snapshot()
    child = _child_member(request)
    return snap.view(child) if child else snap


def get_member_snapshot(request: Request,
                        member: Optional[str] = Query(None, description="E14-4: one household member id (or 'joint'): every figure then "
                                                                        "covers only what is attributed to that person"),
                        state: AppState = Depends(get_state)) -> Snapshot:
    """Like :func:`get_snapshot`, for the READ endpoints of the analytics (cash flow, averages, forecast, categories, budgets, subscriptions,
    anomalies, insights, calendar, balances, transactions ...): ``?member=`` shows the dataset as ONE member. A child login is narrowed to
    its own member whatever the request says."""
    snap = state.snapshot()
    child = _child_member(request)
    if child:
        return snap.view(child)
    if not member:
        return snap
    people = snap.ds.memory.people
    if member != "joint" and (people is None or member not in people.by_id):
        raise ApiError(404, "unknown_member", "no such household member")
    return snap.view(member)


class ScopeParams:
    """E14: every analytics endpoint can be narrowed to an owner ('joint' or a member id), a purpose or an account."""

    def __init__(self, owner: Optional[str] = Query(None, description="'joint' or a household member id"),
                 purpose: Optional[str] = Query(None, description="main, cards, rental, kids, savings"),
                 account: Optional[str] = Query(None, description="account uid or label")):
        self.owner, self.purpose, self.account = owner, purpose, account

    def scope(self, ds) -> Scope:
        return views.scope_of(ds, self.owner, self.purpose, self.account)


def page_params(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> tuple[int, int]:
    return limit, offset
