"""Household & people (E14): members and who owns what, attribution of transactions, the children's money, kid budgets, shared-cost
allocation, logins and the audit. Adult logins only: a child login is denied every one of these endpoints by the guard middleware (deny by
default, see ``coach.api.app.CHILD_ALLOWED``) and has its own, narrow ``/me`` endpoints.

Rules (attribution, kid budgets, allocations) and members live in ``household.yaml``: every write goes through the memory store
(validated, recorded in the history with the login that made it, ``dry_run=true`` previews the diff). Logins are NOT managed here: they are
created, changed and disabled in a terminal (``coach users``); this page only lists them.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, ValidationError

from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.routes._write import run_edit
from coach.api.state import AppState, ui_source
from coach.household import (allocation as alloc_mod, attribution as attr_mod, kidbudgets, kids as kids_mod, people as people_mod,
                             users as users_mod)
from coach.household.attribution import rule_matches
from coach.memory.edit import jsonable

router = APIRouter(tags=["household"])


def _people(state: AppState):
    return people_mod.load(state.store)


# ---------------------------------------------------------------- overview (E14-1, E14-2)

@router.get("/household/overview", summary="Members, account owners and purposes, attribution rules, kid budgets, allocations and logins")
def overview(state: AppState = Depends(get_state)):
    from coach.ingest.accounts import PURPOSES
    ds = state.snapshot().ds
    people = ds.memory.people or people_mod.People()
    counts: Counter = Counter(t.person or "unassigned" for t in ds.whole)
    members = []
    for m in people.members:
        owned = [a.uid for a in ds.accounts.values() if people.resolve(a.owner) == m.id]
        members.append({"id": m.id, "name": m.name, "role": m.role, "birth_year": m.birth_year, "aliases": list(m.aliases),
                        "pocket_money": jsonable(m.pocket_money.model_dump(exclude_none=True)) if m.pocket_money else None,
                        "accounts": owned, "attributed_transactions": counts.get(m.id, 0)})
    with state.read() as con:
        users = [u.to_dict() for u in users_mod.listing(con)]
        try:
            manual = con.execute("SELECT COUNT(*) FROM tx_person").fetchone()[0]
        except Exception:                                              # noqa: BLE001 - migration pending
            manual = 0
    per_account: dict = {}
    for t in ds.whole:
        per_account.setdefault(t.account, Counter())[t.person or "unassigned"] += 1
    accounts = []
    for uid, a in sorted(ds.accounts.items(), key=lambda kv: (kv[1].bank or "", kv[1].label)):
        who = people.resolve(a.owner)
        accounts.append({"uid": uid, "label": a.label, "bank": a.bank, "owner": a.owner,
                         "owner_member": who if who in people.by_id else None, "joint": who == people_mod.JOINT,
                         "owner_known": who is not None, "purpose": a.purpose, "attributed": dict(per_account.get(uid, {}))})
    warnings = []
    if not people.members:
        warnings.append("no member is declared: add the household's members first (`coach memory member add`)")
    else:
        for a in accounts:
            if not a["owner_known"]:
                warnings.append(f"account {a['label']}: owner {a['owner'] or '(none)'!r} is not 'joint' or a declared member")
    return {"as_of": ds.today.isoformat(), "members": members, "accounts": accounts, "purposes": list(PURPOSES),
            "attribution": {"counts": dict(counts), "manual": manual},
            "rules": [jsonable(r.model_dump(exclude_none=True)) for r in people.attribution],
            "kid_budgets": [jsonable(b.model_dump(exclude_none=True)) for b in people.kid_budgets],
            "allocations": [jsonable(a.model_dump(exclude_none=True)) for a in people.allocations],
            "users": users, "warnings": warnings}


# ---------------------------------------------------------------- generic list edit in household.yaml

def _upsert(state: AppState, key: str, item_id: str, value: dict, *, dry_run: bool, reason: Optional[str], action: str, extra=None) -> dict:
    ops = people_mod.upsert_ops(state.store, key, item_id, value)
    return run_edit(state, "household.yaml", ops, action=action, reason=reason, detail=item_id, dry_run=dry_run,
                    extra={"id": item_id, **(extra or {})}, replace_inline_comments=True)


def _delete(state: AppState, key: str, item_id: str, *, dry_run: bool, action: str) -> dict:
    ops = people_mod.delete_ops(state.store, key, item_id)
    if ops is None:
        raise ApiError(404, "not_found", f"no such item {item_id!r}")
    return run_edit(state, "household.yaml", ops, action=action, reason=None, detail=item_id, dry_run=dry_run, extra={"id": item_id})


# ---------------------------------------------------------------- attribution rules (E14-3)

class RuleRequest(BaseModel):
    member: str
    match: dict[str, Any]
    note: Optional[str] = Field(None, max_length=300)
    reason: Optional[str] = Field(None, max_length=300)


@router.put("/household/attribution/rules/{rule_id}",
            summary="Create or replace an attribution rule (dry_run=true: preview the diff and what it would attribute)")
def put_rule(rule_id: str, req: RuleRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    from coach.memory import schemas
    value = {"id": rule_id, "member": req.member, "match": {k: v for k, v in req.match.items() if v not in (None, "")}}
    if req.note:
        value["note"] = req.note
    try:
        rule = schemas.AttributionRule.model_validate(value)
    except ValidationError as e:
        raise ApiError(422, "invalid_rule", "; ".join(f"{'.'.join(str(x) for x in er['loc']) or 'rule'}: {er['msg'].removeprefix('Value error, ')}"
                                                      for er in e.errors())) from None
    ds = state.snapshot().ds
    matched = changed = 0
    for t in ds.whole:
        acc = ds.accounts.get(t.account)
        names = {people_mod.fold(x) for x in (t.account, acc.label if acc else "") if x}
        ok, _ = rule_matches(rule, account_uid=t.account, account_names=names, desc=t.desc, mkey=t.mkey, amount=t.amount_c / 100)
        if ok:
            matched += 1
            changed += 1 if t.person != rule.member else 0
    value = jsonable(rule.model_dump(exclude_none=True))
    return _upsert(state, "attribution", rule_id, value, dry_run=dry_run, reason=req.reason, action="attribution-rule",
                   extra={"matches": matched, "would_change": changed,
                          "note": "a rule never overrides a manual reassignment, and an earlier rule wins"})


@router.post("/household/attribution/rules/{rule_id}/delete", summary="Remove an attribution rule (dry_run=true: preview)")
def delete_rule(rule_id: str, dry_run: bool = False, state: AppState = Depends(get_state)):
    return _delete(state, "attribution", rule_id, dry_run=dry_run, action="attribution-rule-delete")


# ---------------------------------------------------------------- manual reassignment (E14-3)

@router.get("/transactions/attribution", summary="Whose a transaction is, and why: manual line, every rule, the account owner")
def tx_attribution(tx_key: str, state: AppState = Depends(get_state)):
    people = _people(state)
    with state.read() as con:
        try:
            return attr_mod.explain(con, people, tx_key)
        except attr_mod.HouseholdError as e:
            raise ApiError(404, "not_found", str(e)) from None


class PersonRequest(BaseModel):
    tx_key: str
    member: str
    note: Optional[str] = Field(None, max_length=200)


@router.post("/transactions/person", summary="Reassign a transaction to a member (or 'joint') by hand; recorded and reversible")
def set_person(req: PersonRequest, state: AppState = Depends(get_state)):
    people = _people(state)
    with state.write() as con:
        return attr_mod.assign(con, people, req.tx_key, req.member, by=ui_source(), note=req.note)


class ClearRequest(BaseModel):
    tx_key: str


@router.post("/transactions/person/clear", summary="Remove the manual reassignment: the rules and the account owner decide again")
def clear_person(req: ClearRequest, state: AppState = Depends(get_state)):
    with state.write() as con:
        return attr_mod.clear(con, req.tx_key, by=ui_source())


@router.get("/household/attribution/log", summary="The recorded manual reassignments, newest first")
def attribution_log(tx_key: Optional[str] = None, limit: int = Query(50, ge=1, le=500), state: AppState = Depends(get_state)):
    with state.read() as con:
        return {"entries": attr_mod.log(con, tx_key, limit)}


@router.post("/household/attribution/log/{log_id}/undo", summary="Undo one recorded reassignment (a new line records the revert)")
def revert_attribution(log_id: int, state: AppState = Depends(get_state)):
    people = _people(state)
    with state.write() as con:
        return attr_mod.revert(con, people, log_id, by=ui_source())


# ---------------------------------------------------------------- the children's money (E14-5, E14-6)

@router.get("/household/kids", summary="Every child's money: pocket money, extra top-ups, spending, balance trend, pocket vs extra")
def kids(months: int = Query(6, ge=1, le=24), state: AppState = Depends(get_state)):
    ds = state.snapshot().ds
    with state.read() as con:
        return {"as_of": ds.today.isoformat(), "children": kids_mod.children_overview(ds, con, months)}


@router.get("/household/kids/{member}", summary="One child's money")
def kid(member: str, months: int = Query(6, ge=1, le=24), state: AppState = Depends(get_state)):
    ds = state.snapshot().ds
    people = ds.memory.people
    if people is None or member not in people.by_id:
        raise ApiError(404, "unknown_member", "no such household member")
    with state.read() as con:
        return kids_mod.to_json(kids_mod.kid_report(ds, con, member, months))


@router.get("/household/kid-budgets", summary="Kid budgets with this week's / month's progress")
def kid_budget_status(state: AppState = Depends(get_state)):
    ds = state.snapshot().ds
    return {"as_of": ds.today.isoformat(), "budgets": [kids_mod.to_json(s) for s in kidbudgets.status(ds)]}


class KidBudgetRequest(BaseModel):
    member: str
    period: str = Field(pattern="^(weekly|monthly)$")
    limit: float
    category: Optional[str] = None
    group: Optional[str] = None
    note: Optional[str] = Field(None, max_length=300)
    reason: Optional[str] = Field(None, max_length=300)


@router.put("/household/kid-budgets/{budget_id}", summary="Create or replace a kid budget (dry_run=true: preview)")
def put_kid_budget(budget_id: str, req: KidBudgetRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    value: dict = {"id": budget_id, "member": req.member, "period": req.period, "limit": req.limit}
    for k in ("category", "group", "note"):
        if getattr(req, k):
            value[k] = getattr(req, k)
    return _upsert(state, "kid_budgets", budget_id, value, dry_run=dry_run, reason=req.reason, action="kid-budget")


@router.post("/household/kid-budgets/{budget_id}/delete", summary="Remove a kid budget (dry_run=true: preview)")
def delete_kid_budget(budget_id: str, dry_run: bool = False, state: AppState = Depends(get_state)):
    return _delete(state, "kid_budgets", budget_id, dry_run=dry_run, action="kid-budget-delete")


class PocketRequest(BaseModel):
    amount: Optional[float] = None               # null removes it
    period: Optional[str] = Field(None, pattern="^(weekly|monthly)$")
    day: Optional[int] = Field(None, ge=1, le=31)
    reason: Optional[str] = Field(None, max_length=300)


@router.put("/household/members/{member}/pocket-money", summary="Declare (or clear) the pocket money a child is meant to get (dry_run=true: preview)")
def put_pocket(member: str, req: PocketRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    people = _people(state)
    if member not in people.by_id:
        raise ApiError(404, "unknown_member", "no such household member")
    path = f"members[{member}].pocket_money"
    if req.amount is None:
        ops = [{"op": "unset", "path": path}]
    else:
        if not req.period:
            raise ApiError(422, "missing_period", "weekly or monthly")
        v: dict = {"amount": req.amount, "period": req.period}
        if req.day:
            v["day"] = req.day
        ops = [{"op": "set", "path": path, "value": v}]
    return run_edit(state, "household.yaml", ops, action="pocket-money", reason=req.reason, detail=member, dry_run=dry_run,
                    extra={"id": member})


# ---------------------------------------------------------------- who pays what (E14-9)

@router.get("/household/allocation", summary="Shared costs split by rule: each member's share, what they paid and the settlement")
def allocation(months: int = Query(12, ge=1, le=36), rule: Optional[str] = None, state: AppState = Depends(get_state)):
    ds = state.snapshot().ds
    return alloc_mod.to_json(alloc_mod.who_pays(ds, months, rule))


class AllocationRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=80)
    match: dict[str, Any]
    method: str = Field("equal", pattern="^(equal|income|custom)$")
    among: list[str] = []
    shares: dict[str, float] = {}
    note: Optional[str] = Field(None, max_length=300)
    reason: Optional[str] = Field(None, max_length=300)


@router.put("/household/allocations/{rule_id}", summary="Create or replace an allocation rule (dry_run=true: preview)")
def put_allocation(rule_id: str, req: AllocationRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    value: dict = {"id": rule_id, "match": {k: v for k, v in req.match.items() if v not in (None, "")}, "method": req.method}
    if req.title:
        value["title"] = req.title
    if req.among:
        value["among"] = req.among
    if req.shares:
        value["shares"] = req.shares
    if req.note:
        value["note"] = req.note
    return _upsert(state, "allocations", rule_id, value, dry_run=dry_run, reason=req.reason, action="allocation")


@router.post("/household/allocations/{rule_id}/delete", summary="Remove an allocation rule (dry_run=true: preview)")
def delete_allocation(rule_id: str, dry_run: bool = False, state: AppState = Depends(get_state)):
    return _delete(state, "allocations", rule_id, dry_run=dry_run, action="allocation-delete")


# ---------------------------------------------------------------- logins and audit (E14-8; read-only here)

@router.get("/household/users", summary="The logins of the web app (read-only: they are managed in a terminal with `coach users`)")
def list_users(state: AppState = Depends(get_state)):
    with state.read() as con:
        return {"users": [u.to_dict() for u in users_mod.listing(con)], "manage": "uv run coach users --help"}


@router.get("/household/audit", summary="Who changed what: the web app's audit log and the memory history with its sources")
def audit(limit: int = Query(100, ge=1, le=500), actor: Optional[str] = None, state: AppState = Depends(get_state)):
    with state.read() as con:
        rows = users_mod.audit_rows(con, limit, actor)
    try:
        mem = users_mod.memory_audit(state.store, limit)
    except Exception:                                          # noqa: BLE001 - history off or git missing
        mem = []
    return {"requests": rows, "memory": mem}
