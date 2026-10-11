"""The review queue (E2-9 in the UI): merchants whose label is uncertain, by money at stake. Confirming or correcting a
label goes through :mod:`coach.classify.corrections`, the functions behind ``classify review --accept`` / ``correct``."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.analytics.common import money_str, to_cents
from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.state import AppState
from coach.classify import corrections
from coach.classify.commands import review_rows

router = APIRouter(tags=["classify"])


def queue(state: AppState, max_conf: float) -> list:
    """The review queue (all of it), computed once per dataset snapshot and threshold."""
    def load():
        with state.heavy() as con:
            return review_rows(con, state.cfg, max_conf, 10 ** 9)
    return state.snapshot().once(("review", max_conf), load)


@router.get("/review", summary="Merchants to review: low-confidence, uncategorized or not labelled, by money at stake")
def review(limit: int = Query(40, ge=1, le=200), max_conf: float = Query(0.7, ge=0, le=1), state: AppState = Depends(get_state)):
    rows = queue(state, max_conf)
    shown = rows[:limit]
    return {"total": len(rows), "items": [{
        "key": r["key"], "name": r["name"] or r["key"], "category": r["category"], "confidence": r["confidence"], "source": r["source"],
        "reason": r["reason"], "n": r["n"], "total": money_str(to_cents(r["total"])), "at_stake": money_str(to_cents(r["at_stake"])),
        "banks": r["banks"], "accounts": r["accounts"]} for r in shown]}


class KeyBody(BaseModel):
    key: str
    max_conf: float = Field(0.7, ge=0, le=1, description="the threshold of the queue being viewed")


class CorrectBody(BaseModel):
    key: str
    category: str


@router.post("/review/confirm", summary="Confirm the current label of a merchant as yours")
def confirm(req: KeyBody, state: AppState = Depends(get_state)):
    if req.key not in {r["key"] for r in queue(state, req.max_conf)}:
        raise ApiError(404, "not_in_queue", "this merchant is not in the review queue (already decided by you, a rule or memory)")
    with state.write() as con:
        cat = corrections.confirm_label(con, req.key)
    return {"key": req.key, "category": cat}


@router.post("/review/correct", summary="Set the category of a merchant (every transaction with this merchant key)")
def correct(req: CorrectBody, state: AppState = Depends(get_state)):
    with state.write() as con:
        rows = corrections.correct_merchant(con, req.key, req.category, exact=True)
    return {"key": req.key, "category": req.category, "merchants": [k for k, _ in rows]}


@router.get("/review/ai", summary="Whether the AI suggestion of the review queue is available, with web search or without")
def ai_status(state: AppState = Depends(get_state)):
    from coach.classify.suggest import label_status, web_status
    out = {}
    for name, fn in (("web", web_status), ("label", label_status)):
        ok, code, why = fn(state.cfg)
        out[name] = {"allowed": ok, "code": code, "why": why}
    return out


class SuggestBody(BaseModel):
    key: str
    max_conf: float = Field(0.7, ge=0, le=1, description="the threshold of the queue being viewed")


@router.post("/review/suggest", summary="Ask an AI for the category of one merchant of the queue (a proposal: nothing is stored)")
def suggest_start(req: SuggestBody, state: AppState = Depends(get_state)):
    from coach.api.suggestjobs import SuggestBusy
    row = next((r for r in queue(state, req.max_conf) if r["key"] == req.key), None)
    if row is None:
        raise ApiError(404, "not_in_queue", "this merchant is not in the review queue (already decided by you, a rule or memory)")
    if row["reason"] == "held_back_person_like":
        raise ApiError(409, "person_like", "this merchant may be a person: it is never sent to an AI; choose its category yourself")
    try:
        job = state.suggest_jobs.start(req.key)
    except SuggestBusy as e:
        raise ApiError(409, "busy", str(e))
    return job.to_dict()


@router.get("/review/suggest/{job_id}", summary="The state of an AI suggestion, and the proposal when it is done")
def suggest_get(job_id: str, state: AppState = Depends(get_state)):
    job = state.suggest_jobs.get(job_id)
    if job is None:
        raise ApiError(404, "not_found", "no such suggestion (the server may have restarted): ask again")
    return job.to_dict()
