"""Subscriptions & contracts optimizer (E8): the inventory, contract drafts, usage, alternatives, cancellation letters and the
savings tracker.

Writes to the memory (contract drafts, usage, contact) go through the store with the source ``ui`` and support ``dry_run=true``
(a preview); writes to the database (alternatives, decisions) go through the one write door of the app state. The letter and the
contact block are LOCAL: they hold the household's name and address and are never reachable from a model-facing path.
Nothing here sends a letter or contacts a provider.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.analytics.common import _plain
from coach.api import views
from coach.api.deps import ScopeParams, get_member_snapshot, get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import run_edit
from coach.api.state import AppState, Snapshot
from coach.memory import questions as q_mod
from coach.subs import alternatives as A, decisions as DEC, draft as DR, service as S, usage as U

router = APIRouter(tags=["subscriptions"])
GROUPS = ("streaming_media", "software_cloud", "telecom", "memberships", "other_subscriptions", "insurance", "energy_utilities")


def _bundle(snap: Snapshot, state: AppState, include_ended: bool = False, sp: Optional[ScopeParams] = None):
    scope = sp.scope(snap.ds) if sp else None
    flt = None
    if scope is not None and not scope.is_all:
        def flt(x):
            return views._series_in_scope(snap.ds, x, scope)

    def build():
        with state.read() as con:
            return S.load_bundle(con, state.cfg, snap.ds.today, ds=snap.ds, rec=snap.recurring(), store=state.store,
                                 include_ended=include_ended, series_filter=flt)
    if flt is not None:
        return build()
    return snap.once(("subs_bundle", include_ended), build)


def _row_or_404(b, ref: str) -> dict:
    try:
        return S.resolve(b.inv, ref)
    except S.RefError as e:
        raise ApiError(404, "not_found", str(e)) from None


# ---------------------------------------------------------------- inventory (E8-1, E8-2, E8-3)

@router.get("/subs/inventory", summary="Every recurring cost and contract in one list: yearly cost, contract status, usage, "
                                       "cancellation rules, alternatives, decision")
def inventory(group: Optional[str] = Query(None), include_ended: bool = False, sp: ScopeParams = Depends(),
              snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    if group is not None and group not in GROUPS:
        raise ApiError(422, "bad_group", f"group must be one of {', '.join(GROUPS)}")
    inv = _bundle(snap, state, include_ended, sp).inv
    rows = [r for r in inv["rows"] if not group or r["group"] == group]
    return _plain({**inv, "rows": rows, "groups_meta": [{"id": g, "label": S.INV.GROUP_LABEL[g]} for g in GROUPS]})


class DraftRequest(BaseModel):
    series: str = Field(..., pattern=r"^rec_[0-9a-f]{6,}$", description="the recurring series to draft a contract for")


@router.post("/subs/contracts/draft", summary="Create a contract file from a recurring series (kind, provider, billing, merchant match, start); "
                                              "dry_run=true: preview")
def draft_contract(req: DraftRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    store = state.store
    ds, rec = snap.ds, snap.recurring()
    if not any(x.id == req.series for x in rec.series):
        raise ApiError(404, "not_found", f"no recurring series {req.series!r}")
    drafts = DR.drafts(ds, rec, {c.id for _r, c in store.contracts()}, {req.series})
    if not drafts:
        raise ApiError(409, "not_draftable", "this series already has a contract file, or is not contract-like (loans, rent and taxes are not drafted)")
    d = drafts[0]
    out = run_edit(state, d.rel, DR.ops_for(d), action="draft-contract", reason=f"drafted from recurring series {d.series_id}", detail=d.contract_id,
                   dry_run=dry_run, extra={"contract": d.to_dict(), "id": d.contract_id, "file": d.rel})
    if not dry_run:
        q = DR.question_for(d, state.clock())
        if q.key not in {x.key for x in store.questions()}:
            q_mod.add_many(store, [q], source="ui")
            out["question_added"] = q.id
    return _plain(out)


class UsageRequest(BaseModel):
    frequency: Literal["daily", "weekly", "monthly", "rarely", "never", "unknown"]
    last_used: Optional[dt.date] = None
    note: Optional[str] = Field(None, max_length=300)


@router.put("/subs/contracts/{contract_id}/usage", summary="Record how you use a service (frequency, last used, note); dry_run=true: preview")
def set_usage(contract_id: str, req: UsageRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    rel = S.contract_file(state.store, contract_id)
    if rel is None:
        raise ApiError(404, "not_found", f"no contract {contract_id!r}")
    if req.last_used and req.last_used > state.clock():
        raise ApiError(422, "bad_date", "last_used is in the future")
    return run_edit(state, rel, S.usage_ops(req.frequency, req.last_used, req.note), action="usage", reason="usage recorded in the web app",
                    detail=contract_id, dry_run=dry_run, replace_inline_comments=True, extra={"id": contract_id})


@router.post("/subs/usage-questions", summary="Add the usage questions still to ask to the open questions (never duplicates); dry_run=true: list them")
def usage_questions(dry_run: bool = False, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    with state.read() as con:
        qs, skipped = U.usage_questions(con, state.cfg, state.store, snap.recurring(), state.clock())
    if qs and not dry_run:
        q_mod.add_many(state.store, qs, source="ui")
        state.touch()
    return {"dry_run": dry_run, "questions": [{"id": q.id, "question": q.question, "stake": q.stake} for q in qs], "skipped_already_asked": skipped}


# ---------------------------------------------------------------- alternatives (E8-4)

class AlternativeRequest(BaseModel):
    ref: str = Field(..., max_length=120, description="rec_... id or contract:<id> of the subscription")
    provider: str = Field(..., max_length=80)
    offer_name: str = Field(..., max_length=120)
    monthly_price: float
    features: Optional[str] = Field(None, max_length=400)
    source_url: Optional[str] = Field(None, max_length=500)
    retrieved_at: Optional[dt.date] = None
    switching_costs: float = 0
    method: Literal["find-cheaper", "manual"] = "manual"
    notes: Optional[str] = Field(None, max_length=500)


@router.get("/subs/alternatives", summary="The stored alternatives of one subscription (or all), with savings computed by code and their staleness")
def alternatives(ref: Optional[str] = None, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    b = _bundle(snap, state, True)
    rows = [_row_or_404(b, ref)] if ref else [r for r in b.inv["rows"] if r["alternatives"]["count"]]
    return _plain({"as_of": snap.ds.today, "max_age_days": A.MAX_AGE_DAYS,
                   "subscriptions": [{"ref": r["ref"], "name": r["name"], "monthly": r["monthly"], **r["alternatives"]} for r in rows]})


@router.post("/subs/alternatives", summary="Store an alternative offer (price, https source URL, date seen)")
def add_alternative(req: AlternativeRequest, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    row = _row_or_404(_bundle(snap, state, True), req.ref)
    try:
        with state.write() as con:
            alt = A.add(con, contract_id=row["contract_id"], series_id=row["series_id"], today=state.clock(), provider=req.provider,
                        offer_name=req.offer_name, monthly_price=req.monthly_price, features=req.features, source_url=req.source_url,
                        retrieved_at=req.retrieved_at or state.clock(), method=req.method, notes=req.notes,
                        switching_costs=req.switching_costs, source="ui")
    except A.AlternativeError as e:
        raise ApiError(422, "invalid_alternative", str(e)) from None
    return {"id": alt.id, "stored": True}


@router.delete("/subs/alternatives/{alt_id}", summary="Delete one stored alternative")
def remove_alternative(alt_id: str, state: AppState = Depends(get_state)):
    with state.write() as con:
        ok = A.remove(con, alt_id)
    if not ok:
        raise ApiError(404, "not_found", f"no alternative {alt_id!r}")
    return {"id": alt_id, "removed": True}


# ---------------------------------------------------------------- letters and contact (E8-5)

@router.get("/subs/letter", summary="A cancellation letter / e-mail text for a contract, generated locally from templates (nothing is sent)")
def letter(contract: str = Query(..., max_length=120), lang: Optional[Literal["fr", "it", "en"]] = None,
           channel: Literal["lrar", "email", "online"] = "lrar", holder: Optional[str] = Query(None, max_length=80),
           snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    cid = next((c.id for _r, c in state.store.contracts() if c.id == contract), None)
    if cid is None:
        row = _row_or_404(_bundle(snap, state, True), contract)
        cid = row["contract_id"]
        if cid is None:
            raise ApiError(409, "no_contract", "this subscription has no contract file yet: create one from the subscription first")
    try:
        return _plain(S.render_letter(state.store, snap.ds, cid, lang=lang, channel=channel, holder=holder, today=state.clock()))
    except S.RefError as e:
        raise ApiError(404, "not_found", str(e)) from None


class ContactRequest(BaseModel):
    address: Optional[str] = Field(None, max_length=300)
    email: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)


@router.get("/subs/contact", summary="The contact block used on letters (local only, never sent to a model)")
def get_contact(state: AppState = Depends(get_state)):
    c = S.contact_of(state.store)
    return {"contact": c, "set": bool(c), "local_only": True,
            "members": [{"id": m.id, "name": m.name} for m in state.store.members() if m.role == "adult"]}


@router.put("/subs/contact", summary="Set your postal address / e-mail / phone for letters; dry_run=true: preview")
def set_contact(req: ContactRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    val = {k: " ".join(v.split()) if k != "address" else "\n".join(" ".join(x.split()) for x in v.splitlines() if x.strip())
           for k, v in req.model_dump().items() if v and v.strip()}
    if not val:
        raise ApiError(422, "nothing_to_change", "no field was given")
    ops = []
    if not state.store.exists("household.yaml"):
        ops.append({"op": "create", "value": {"members": []}})
    ops.append({"op": "set", "path": "contact", "value": {**S.contact_of(state.store), **val}})
    return run_edit(state, "household.yaml", ops, action="set-contact", reason="contact details for cancellation letters (local only)",
                    detail=None, dry_run=dry_run)


# ---------------------------------------------------------------- decisions and savings (E8-6)

class DecisionRequest(BaseModel):
    ref: str = Field(..., max_length=120)
    decision: Literal["cancelled", "renegotiated", "switched", "downgraded", "kept"]
    before_monthly: Optional[float] = Field(None, ge=0, description="default: the current monthly cost")
    after_monthly: Optional[float] = Field(None, ge=0)
    decided_on: Optional[dt.date] = None
    effective_on: Optional[dt.date] = None
    note: Optional[str] = Field(None, max_length=500)


@router.get("/subs/savings", summary="Realised savings: verified by the bank data, pending, contradicted")
def savings(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    b = _bundle(snap, state, True)
    out = DEC.savings(b.decisions, b.rec, snap.ds.today)
    out["proposed"] = [{"id": d.id, "decision": d.decision, "name": d.name, "source": d.source, "before_monthly": d.before_c / 100,
                        "after_monthly": d.after_c / 100, "note": d.note} for d in b.decisions if d.state == "proposed"]
    return _plain(out)


@router.post("/subs/decisions", summary="Record what you decided about a subscription; dry_run=true: validate only")
def add_decision(req: DecisionRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    row = _row_or_404(_bundle(snap, state, True), req.ref)
    before = req.before_monthly if req.before_monthly is not None else row["monthly"]
    kw = dict(decision=req.decision, today=state.clock(), contract_id=row["contract_id"], series_id=row["series_id"], name=row["name"],
              decided_on=req.decided_on, effective_on=req.effective_on, before=before, after=req.after_monthly, note=req.note, source="ui")
    try:
        if dry_run:
            v = DEC.check(**kw)
            return {"dry_run": True, "valid": True, "monthly_saving": (v["before_c"] - v["after_c"]) / 100 if req.decision != "kept" else 0}
        with state.write() as con:
            d = DEC.add(con, **kw)
    except DEC.DecisionError as e:
        raise ApiError(422, "invalid_decision", str(e)) from None
    return {"id": d.id, "dry_run": False, "monthly_saving": d.monthly_saving_c / 100}


def _decision_state(dec_id: str, action: str, state: AppState):
    try:
        with state.write() as con:
            (DEC.confirm if action == "confirm" else DEC.reject)(con, dec_id)
    except DEC.DecisionError as e:
        missing = "no decision" in str(e)
        raise ApiError(404 if missing else 409, "not_found" if missing else "not_proposed", str(e)) from None
    return {"id": dec_id, "state": "confirmed" if action == "confirm" else "rejected"}


@router.post("/subs/decisions/{dec_id}/confirm", summary="Confirm a decision the coach proposed (it then counts)")
def confirm_decision(dec_id: str, state: AppState = Depends(get_state)):
    return _decision_state(dec_id, "confirm", state)


@router.post("/subs/decisions/{dec_id}/reject", summary="Reject a decision the coach proposed")
def reject_decision(dec_id: str, state: AppState = Depends(get_state)):
    return _decision_state(dec_id, "reject", state)


@router.delete("/subs/decisions/{dec_id}", summary="Delete a recorded decision")
def remove_decision(dec_id: str, state: AppState = Depends(get_state)):
    with state.write() as con:
        ok = DEC.remove(con, dec_id)
    if not ok:
        raise ApiError(404, "not_found", f"no decision {dec_id!r}")
    return {"id": dec_id, "removed": True}
