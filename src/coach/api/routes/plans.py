"""Budgets and goals (E5-7): read from the analytics, written through the memory store (source ``ui``)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.analytics import budgets as budgets_mod, goals as goals_mod
from coach.analytics.common import money_str, parse_money
from coach.api import views
from coach.api.deps import ScopeParams, get_member_snapshot, get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import num, run_edit
from coach.api.state import AppState, Snapshot
from coach.classify.rules import CATEGORIES, TAXONOMY

router = APIRouter(tags=["plans"])


@router.get("/budgets", summary="Budgets with progress, projection and status; the biggest unbudgeted categories")
def budgets_(snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    st = snap.once("budget_status", lambda: budgets_mod.budget_status(ds, recurring=snap.recurring()))
    d = st.to_dict()
    by_id = {b.id: b for b in ds.memory.budgets}
    for row in d["budgets"]:
        b = by_id.get(row["id"])
        if b:
            row["category"], row["group"] = b.category, b.group
            row["note"], row["start"] = b.note, b.start.isoformat() if b.start else None
    d["problems"] = ds.memory.budget_problems
    return d


@router.get("/budgets/suggestions", summary="Suggested monthly amounts: the median of the last covered months, rounded")
def suggestions(months: Optional[int] = Query(None, ge=2, le=24), limit: Optional[int] = Query(20, ge=1, le=100),
                sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    return budgets_mod.suggest_budgets(ds, months, sp.scope(ds), limit).to_dict()


class BudgetRequest(BaseModel):
    target: str = Field(description="a category (food.groceries), a group (food) or 'group:food'")
    monthly: str = Field(description="EUR, decimal string such as '350' or '49.90'")
    id: Optional[str] = None
    rollover: Optional[bool] = None
    owner: Optional[str] = None
    account: Optional[str] = None
    start: Optional[dt.date] = None
    note: Optional[str] = None
    replace: bool = False
    reason: Optional[str] = None


def _check_owner_account(snap: Snapshot, owner, account) -> None:
    ds = snap.ds
    if account and ds.resolve_account(account) is None:
        raise ApiError(422, "unknown_account", f"unknown account {account!r}")
    members = {m.id for m in ds.memory.members}
    if owner and owner != "joint" and owner not in members and owner not in {a.owner for a in ds.accounts.values()}:
        raise ApiError(422, "unknown_owner", f"unknown owner {owner!r}: 'joint', an account owner or a household member id")


@router.post("/budgets", summary="Create or update a budget (dry_run=true: preview the diff only)")
def set_budget(req: BudgetRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot),
               state: AppState = Depends(get_state)):
    store = state.store
    target = req.target
    if target.startswith("group:") or (target in TAXONOMY and "." not in target):
        group = target.removeprefix("group:")
        if group not in TAXONOMY:
            raise ApiError(422, "unknown_group", f"unknown category group {group!r}")
        key = {"group": group}
    else:
        if target not in CATEGORIES:
            raise ApiError(422, "unknown_category", f"unknown category {target!r}")
        key = {"category": target}
    try:
        amount = float(money_str(parse_money(req.monthly)))
    except Exception:
        raise ApiError(422, "bad_amount", f"bad amount {req.monthly!r}")
    if amount <= 0:
        raise ApiError(422, "bad_amount", "the monthly amount must be positive")
    _check_owner_account(snap, req.owner, req.account)
    items = store.budgets()
    same = next((b for b in items if (b.category == key.get("category") and b.group == key.get("group"))
                 and b.owner == req.owner and b.account == req.account), None)
    ids = {b.id for b in items}
    bid = req.id or (same.id if same else budgets_mod.budget_id_for(target, ids))
    clash = next((b for b in items if b.id == bid), None)
    retarget = clash is not None and (clash.category, clash.group) != (key.get("category"), key.get("group"))
    if retarget and not req.replace:
        raise ApiError(409, "budget_exists", f"budget {bid!r} already exists for {clash.category or 'group:' + clash.group}")
    fields: dict = {**key, "monthly": num(amount)}
    if bid in ids and not retarget:
        fields = {"monthly": num(amount)}
    if req.rollover is not None:
        fields["rollover"] = req.rollover
    if req.owner:
        fields["owner"] = req.owner
    if req.account:
        fields["account"] = req.account
    if req.start:
        fields["start"] = req.start
    elif req.rollover and not (same and same.start) and not any(b.id == bid and b.start for b in items):
        fields["start"] = state.clock().replace(day=1)
    if req.note:
        fields["note"] = req.note
    exists = store.exists("budgets.yaml")
    if retarget:
        ops = [{"op": "remove", "path": f"budgets[{bid}]"}] + budgets_mod.budget_ops(ids - {bid}, True, bid, fields)
    else:
        ops = budgets_mod.budget_ops(ids, exists, bid, fields)
    return run_edit(state, "budgets.yaml", ops, action="set-budget", reason=req.reason or f"budget {bid}: {amount:.2f} EUR/month",
                    detail=bid, dry_run=dry_run, extra={"id": bid})


@router.post("/budgets/{budget_id}/delete", summary="Remove a budget (dry_run=true: preview)")
def delete_budget(budget_id: str, dry_run: bool = False, state: AppState = Depends(get_state)):
    if budget_id not in {b.id for b in state.store.budgets()}:
        raise ApiError(404, "not_found", f"no budget {budget_id!r}")
    return run_edit(state, "budgets.yaml", [{"op": "remove", "path": f"budgets[{budget_id}]"}], action="remove-budget",
                    reason=f"budget {budget_id} removed", detail=budget_id, dry_run=dry_run, extra={"id": budget_id})


# ---------------------------------------------------------------- goals

@router.get("/goals", summary="Savings goals with progress, pace and projected date")
def goals(snap: Snapshot = Depends(get_member_snapshot)):
    return snap.once("goals", lambda: goals_mod.goal_progress(snap.ds)).to_dict()


class GoalRequest(BaseModel):
    id: str
    target: Optional[str] = Field(None, description="EUR, decimal string; required for a new goal")
    title: Optional[str] = None
    asset: Optional[str] = None
    account: Optional[str] = None
    tag: Optional[str] = None
    date: Optional[dt.date] = None
    monthly: Optional[str] = None
    baseline: Optional[str] = None
    start: Optional[dt.date] = None
    reason: Optional[str] = None


def _amount(text: str, what: str):
    try:
        v = float(money_str(parse_money(text)))
    except Exception:
        raise ApiError(422, "bad_amount", f"bad {what} {text!r}")
    if v < 0:
        raise ApiError(422, "bad_amount", f"{what} must not be negative")
    return num(v)


@router.post("/goals", summary="Create or update a goal (dry_run=true: preview)")
def set_goal(req: GoalRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    store = state.store
    ids = {g.id for g in store.goals()}
    fields: dict = {}
    if req.target is not None:
        fields["target_amount"] = _amount(req.target, "target")
    elif req.id not in ids:
        raise ApiError(422, "missing_target", "a new goal needs a target amount")
    for k in ("asset", "account", "tag", "title"):
        if getattr(req, k):
            fields[k] = getattr(req, k)
    if req.date:
        fields["target_date"] = req.date
    if req.monthly is not None:
        fields["monthly_contribution"] = _amount(req.monthly, "monthly contribution")
    if req.baseline is not None:
        fields["baseline"] = _amount(req.baseline, "baseline")
    if req.start:
        fields["start"] = req.start
    elif req.tag and req.id not in ids:
        fields["start"] = state.clock()
    ops = goals_mod.goal_ops(ids, store.exists("goals.yaml"), req.id, fields)
    return run_edit(state, "goals.yaml", ops, action="set-goal", reason=req.reason or f"goal {req.id}", detail=req.id,
                    dry_run=dry_run, extra={"id": req.id})
