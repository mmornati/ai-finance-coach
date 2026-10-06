"""``/me``: the endpoints of the logged-in person (E14-8).

These are the ONLY analytics-like endpoints a child login may call (``coach.api.app.CHILD_ALLOWED``). Everything here is derived from the
LOGIN of the session (``request.state.user``), never from a parameter of the request: a child cannot ask for another member's data, and
there is no ``member`` parameter to tamper with. A child sees their own balance, their own pocket money and top-ups (the source is only
"a parent" / "family" / "someone else", never a name), their own spending and budgets and their own recent payments. Nothing about the other
members, the household's accounts, the memory, the coach or the connections.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from coach.analytics.common import money_str, parse_money
from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.state import AppState
from coach.household import kidbudgets, kids as kids_mod, people as people_mod, users as users_mod

router = APIRouter(tags=["me"])


def _user(request: Request):
    u = getattr(request.state, "user", None)
    if u is None:
        raise ApiError(401, "unauthorized", "no valid session")
    return u


def _member(request: Request, state: AppState) -> tuple:
    u = _user(request)
    people = people_mod.load(state.store)
    if not u.member_id or u.member_id not in people.by_id:
        raise ApiError(404, "no_member", "this login is not linked to a household member")
    return u, people, u.member_id


def _kind(source: str, people) -> str:
    """What a child is told about where a top-up came from: never a name."""
    if source == people_mod.JOINT or people.role(source) == "adult":
        return "a parent"
    if people.role(source) == "child":
        return "family"
    return "someone else"


@router.get("/me", summary="Who this session is: the login, its role and (when linked) its member")
def me(request: Request, state: AppState = Depends(get_state)):
    u = _user(request)
    people = people_mod.load(state.store)
    m = people.by_id.get(u.member_id) if u.member_id else None
    return {"user": u.to_dict(), "member": {"id": m.id, "name": m.name, "role": m.role} if m else None}


@router.get("/me/summary", summary="The own money of the member this login belongs to: balance, pocket money, spending, budgets")
def summary(request: Request, state: AppState = Depends(get_state)):
    u, people, member = _member(request, state)
    ds = state.snapshot().ds
    with state.read() as con:
        rep = kids_mod.to_json(kids_mod.kid_report(ds, con, member, 6))
    for item in rep["extra_topups"]["items"]:
        item["source"] = _kind(item["source"], people)
        item.pop("tx_key", None)
    for s in rep["pocket_money"]["series"]:
        s["source"] = _kind(s["source"], people)
        s.pop("evidence", None)
        s.pop("id", None)
    by_kind: dict = {}
    for src, amount in rep["extra_topups"]["by_source"].items():
        k = _kind(src, people)
        by_kind[k] = money_str(parse_money(amount) + (parse_money(by_kind[k]) if k in by_kind else 0))
    rep["extra_topups"]["by_source"] = by_kind
    rep.pop("accounts", None)
    budgets = [kids_mod.to_json({k: v for k, v in s.items() if k != "member"}) for s in kidbudgets.status(ds, member)]
    return {"member": member, "name": people.by_id[member].name, "report": rep, "budgets": budgets}


@router.get("/me/transactions", summary="The own payments of the member this login belongs to (newest first)")
def my_transactions(request: Request, limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0),
                    state: AppState = Depends(get_state)):
    _, people, member = _member(request, state)
    ds = state.snapshot().ds
    rows = sorted((t for t in ds.whole if t.person == member), key=lambda t: (t.date, t.key), reverse=True)
    page = rows[offset:offset + limit]

    def who(t) -> str:
        """A payment's name for a child: the merchant, unless it names a household member or is a person-to-person transfer (never a name)."""
        named = sorted(people.mentioned(f"{t.entity} {t.merchant or ''} {t.desc}") - {member})
        if named:
            return _kind(named[0], people)
        if t.category.startswith("transfer.") or t.type in ("person_transfer_in", "person_transfer_out", "internal_transfer", "wero_in", "wero_out"):
            return "someone else" if "to_people" in t.category or "from_people" in t.category else "a transfer"
        return t.entity
    return {"total": len(rows), "limit": limit, "offset": offset,
            "items": [{"date": t.date.isoformat(), "amount": money_str(t.amount_c), "category": t.category, "merchant": who(t)} for t in page]}


class PrefsBody(BaseModel):
    prefs: dict[str, Any]


@router.get("/me/preferences", summary="The preferences of this login")
def get_prefs(request: Request):
    u = _user(request)
    return {"prefs": dict(u.prefs), "allowed": {k: list(v) if v else None for k, v in users_mod.PREFERENCES.items()},
            "stored": u.id != users_mod.LEGACY_ID}


@router.put("/me/preferences", summary="Change the preferences of this login (locale, theme, default view, landing page)")
def put_prefs(body: PrefsBody, request: Request, state: AppState = Depends(get_state)):
    u = _user(request)
    if u.id == users_mod.LEGACY_ID:
        raise ApiError(409, "no_stored_login", "the owner login has no stored preferences: create a login with `coach users add`")
    people = people_mod.load(state.store)
    with state.write(quiet=True) as con:
        nu = users_mod.set_prefs(con, people, u.id, body.prefs)
    return {"prefs": dict(nu.prefs)}
