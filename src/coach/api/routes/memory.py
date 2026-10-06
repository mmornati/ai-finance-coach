"""Memory (E5-13): open questions, household, events, liabilities / assets / contracts forms, proposals, check, history.

Every write goes through :class:`~coach.memory.store.MemoryStore` (validated, recorded in the change history with the
source ``ui``) and can be previewed with ``dry_run=true``. Nothing here edits a memory file directly.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.api import views
from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import edit_out, run_edit, to_dates
from coach.api.state import AppState, ui_source, Snapshot
from coach.memory import check as check_mod, proposals as prop_mod, questions as q_mod
from coach.memory.edit import jsonable
from coach.memory.history import HistoryError

router = APIRouter(tags=["memory"])


# ---------------------------------------------------------------- overview / check

@router.get("/memory/overview", summary="What the memory holds: files, counts, open questions, pending proposals, check summary")
def overview(state: AppState = Depends(get_state)):
    store, cfg = state.store, state.cfg
    _, summary = state.memory_check()
    qs = store.questions()
    return {"files": store.files(), "history_enabled": cfg.memory_history,
            "counts": {"members": len(store.members()), "events": len(views.events_list(store)),
                       "liabilities": len(store.liabilities()), "contracts": len(store.contracts()),
                       "assets": len(store.assets()), "annotations": len(store.annotations()),
                       "budgets": len(store.budgets()), "goals": len(store.goals()),
                       "open_questions": sum(q.status == "open" for q in qs),
                       "pending_proposals": len(prop_mod.listing(store, "pending"))},
            "check": summary}


@router.get("/memory/check", summary="`coach memory check`: schema, semantic and database consistency issues")
def check(state: AppState = Depends(get_state)):
    issues, summary = state.memory_check()
    return {"summary": summary, "issues": [i.to_dict() for i in issues]}


# ---------------------------------------------------------------- questions

def _q(q) -> dict:
    d = jsonable(q.model_dump(mode="python", exclude_none=True))
    if q.stake is not None:
        d["stake"] = views.money_str(views.to_cents(q.stake))
    return d


@router.get("/questions", summary="Open questions the coach asks (the inbox)")
def questions(status: str = Query("open", pattern="^(open|answered|dismissed|all)$"), state: AppState = Depends(get_state)):
    allq = state.store.questions()
    qs = q_mod.listing(state.store, None if status == "all" else status)
    return {"questions": [_q(q) for q in qs],
            "counts": {s: sum(1 for q in allq if q.status == s) for s in ("open", "answered", "dismissed")}}


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DismissQuestion(BaseModel):
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/questions/{qid}/answer", summary="Record the answer (it is only recorded; changing memory is a separate, previewed step)")
def answer(qid: str, req: AnswerRequest, state: AppState = Depends(get_state)):
    q = q_mod.answer(state.store, qid, req.text, source=ui_source())
    state.touch()
    return _q(q)


@router.post("/questions/{qid}/dismiss", summary="Dismiss a question (it is not asked again)")
def dismiss(qid: str, req: Optional[DismissQuestion] = None, state: AppState = Depends(get_state)):
    q = q_mod.dismiss(state.store, qid, req.reason if req else None, source=ui_source())
    state.touch()
    return _q(q)


@router.post("/questions/{qid}/reopen", summary="Reopen an answered or dismissed question")
def reopen(qid: str, state: AppState = Depends(get_state)):
    q = q_mod.reopen(state.store, qid, source=ui_source())
    state.touch()
    return _q(q)


# ---------------------------------------------------------------- household and events

@router.get("/household", summary="Household members (names stay on this machine)")
def household(state: AppState = Depends(get_state)):
    return {"members": [{"id": m.id, "name": m.name, "role": m.role, "birth_year": m.birth_year, "aliases": m.aliases}
                        for m in state.store.members()]}


@router.get("/events", summary="Events (trips, works, one-off projects) that annotations can point to")
def events(state: AppState = Depends(get_state)):
    return {"events": views.events_list(state.store)}


# ---------------------------------------------------------------- item forms

Kind = Literal["liabilities", "contracts", "assets", "members", "events"]
LISTS = {"assets": ("assets.yaml", "assets"), "members": ("household.yaml", "members"), "events": ("events.yaml", "events")}
TEMPLATES = {
    "liabilities": {"kind": None, "lender": None, "asset": None, "start_date": None, "end_date": None, "principal": None,
                    "outstanding": None, "outstanding_as_of": None, "rate": {"type": None, "nominal": None, "taeg": None},
                    "monthly_payment": None, "payment_match": None, "documents": [], "notes": ""},
    "contracts": {"provider": None, "kind": None, "merchant_match": None, "renewal": None,
                  "billing": {"amount": None, "period": None}, "notice_period_days": None, "documents": [], "notes": ""},
}


class ItemRequest(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict, description="fields to set (nested objects such as rate: {nominal: 2.1} are merged)")
    unset: list[str] = []
    reason: Optional[str] = None


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, Any]]:
    out = []
    for k, v in d.items():
        if isinstance(v, dict) and v:
            out += _flatten(v, f"{prefix}{k}.")
        else:
            out.append((f"{prefix}{k}", v))
    return out


@router.put("/memory/{kind}/{item_id}", summary="Create or update a liability, contract, asset, member or event (dry_run=true: preview)")
def upsert(kind: Kind, item_id: str, req: ItemRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    store = state.store
    fields = to_dates(req.fields)
    if "id" in fields and fields["id"] != item_id:
        raise ApiError(422, "id_mismatch", "the id of an item cannot be changed")
    fields.pop("id", None)
    ops: list = []
    if kind in ("liabilities", "contracts"):
        rel = f"{kind}/{item_id}.yaml"
        if not store.exists(rel):
            if kind == "liabilities" and not fields.get("kind"):
                raise ApiError(422, "missing_kind", "a new liability needs a kind (mortgage, car_loan, loa, lld, consumer_loan, bnpl)")
            base = {"id": item_id, **TEMPLATES[kind]}
            base.update({k: v for k, v in fields.items() if not isinstance(v, dict)})
            for k, v in fields.items():
                if isinstance(v, dict):
                    base[k] = {**(base.get(k) or {}), **v}
            ops = [{"op": "create", "value": base}]
            fields = {}
        for path, v in _flatten(fields):
            ops.append({"op": "set", "path": path, "value": v})
        for path in req.unset:
            ops.append({"op": "unset", "path": path})
    else:
        rel, lk = LISTS[kind]
        ids = {i for i in (store.index().get(item_id) or [])}
        exists_item = any(r == rel for r, _, _ in ids)
        if not store.exists(rel):
            ops.append({"op": "create", "value": {lk: []}})
        if exists_item:
            for path, v in _flatten(fields):
                ops.append({"op": "set", "path": f"{lk}[{item_id}].{path}", "value": v})
            for path in req.unset:
                ops.append({"op": "unset", "path": f"{lk}[{item_id}].{path}"})
        else:
            ops.append({"op": "append", "path": lk, "value": {"id": item_id, **{k: v for k, v in fields.items() if v is not None}}})
    if not ops:
        raise ApiError(422, "nothing_to_change", "no field was given")
    return run_edit(state, rel, ops, action=f"upsert-{kind[:-1] if kind.endswith('s') else kind}", reason=req.reason,
                    detail=item_id, dry_run=dry_run, replace_inline_comments=True, extra={"id": item_id, "file": rel})


# ---------------------------------------------------------------- proposals

def _prop(state: AppState, p) -> dict:
    d = prop_mod.public_dict(state.store, p)
    d["suspicious_paths"] = prop_mod.suspicious_paths(p)
    # accepting is a human decision made in a terminal (`coach memory accept`): the page shows the exact command
    d["accept_command"] = f"uv run coach memory accept {p.id}"
    return jsonable(d)


@router.get("/proposals", summary="Memory proposals queued by the coach or by a document extraction (accepted only in a terminal)")
def proposals(status: str = Query("pending", pattern="^(pending|all)$"), state: AppState = Depends(get_state)):
    ps = prop_mod.listing(state.store, status)
    return {"proposals": [_prop(state, p) for p in ps]}


class RejectRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=300)


@router.post("/proposals/{pid}/reject", summary="Reject a proposal (it is closed, nothing is written)")
def reject(pid: str, req: Optional[RejectRequest] = None, state: AppState = Depends(get_state)):
    p = prop_mod.reject(state.store, pid, req.note if req else None)
    state.touch()
    return {"id": p.id, "status": p.status}


# ---------------------------------------------------------------- history

@router.get("/memory/history", summary="The change history of the memory (read-only)")
def history(limit: int = Query(50, ge=1, le=200), file: Optional[str] = None, state: AppState = Depends(get_state)):
    if not state.cfg.memory_history:
        return {"enabled": False, "changes": []}
    ch = state.store.history(file, limit)
    return {"enabled": True, "changes": [c.__dict__ for c in ch]}


@router.get("/memory/history/{change_id}/diff", summary="The patch of one recorded change")
def history_diff(change_id: str, state: AppState = Depends(get_state)):
    try:
        return {"id": change_id, "diff": state.store.diff(change_id), "revert_command": f"uv run coach memory revert {change_id}"}
    except HistoryError as e:
        raise ApiError(404, "not_found", str(e))
