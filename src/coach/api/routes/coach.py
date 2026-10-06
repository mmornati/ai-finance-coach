"""Ask the coach (E5-12 contract, E6-3 runtime): a question starts ONE background job that runs the configured backend with the
finance tools; its events stream over SSE and are kept so a page can reconnect.

Streaming contract (``POST /coach/stream`` starts a job and streams it; ``GET /coach/jobs/{id}/stream?after=N`` replays /
continues one). ``text/event-stream``; each event is ``id: <n>`` + ``event: <name>`` + ``data: <JSON>``:

* ``meta``        ``{job_id, configured, backend, model}``                  first event
* ``status``      ``{state, backend, model, session_id, max_tool_calls}``   the run started
* ``tool_call``   ``{id, name, args, n, max}``                              the coach looked at something (args are short)
* ``tool_result`` ``{id, name, ok, chars, suspicious}``
* ``delta``       ``{text}``                                                answer text, in order
* ``proposal``    ``{id, file, command}``                                   the coach PROPOSED a memory change (nothing applied)
* ``citation``    ``{label, ref, kind}``                                    an evidence ref the answer cites (resolve it with ``POST /coach/resolve``)
* ``usage``       ``{backend, model, tokens_in, tokens_out, cache_read_tokens, cost_usd, cost_is_estimate, tool_calls, duration_s}``
* ``answer``      ``{insight_id, suspicious, proposals, unverified_numbers, finish_reason, ai_generated, compliance}``; ``compliance`` is
                  ``{label, lang, flagged, codes, banner}``: show the AI-generated label, and the banner when ``flagged`` (E11-5)
* ``notice``      ``{code, message}``                                       non-fatal (not configured, cancelled, suspicious text...)
* ``error``       ``{code, message}``                                       fatal, then ``done``
* ``done``        ``{finish_reason, job_id, insight_id}``                   last event
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from coach.analytics.common import money_str
from coach.analytics.privacy import stable_hash
from coach.agent import prompt as P
from coach.api.coachjobs import CoachBusy, CoachJob, availability
from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.state import AppState, Snapshot
from coach.mcp.tools import TOOL_NAMES

router = APIRouter(tags=["coach"])

QUICK_PROMPTS = [
    {"id": "month-high", "text": "Why was last month's spending so high?"},
    {"id": "save-more", "text": "Where could we save the most each month?"},
    {"id": "subscriptions", "text": "Which subscriptions should we review?"},
    {"id": "afford", "text": "Can we afford a 3,000 EUR expense in the next two months?"},
    {"id": "year", "text": "Give me the year in review."},
    # the E7 skills (docs/skills.md): a prompt that runs a skill with its own tools and output format
    *[{"id": sid, "text": info["quick"], "skill": sid} for sid, info in P.SKILLS.items() if info["spec"] is not None and info["quick"]],
]
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@router.get("/coach/status", summary="Is the coach available? Which backend, model and limits")
def status(state: AppState = Depends(get_state)):
    cfg = state.cfg
    ok, msg = availability(cfg)
    cur = state.coach_jobs.current()
    note = {"claude-code": "Runs headless `claude -p` on your Claude subscription: personal, low-frequency use only. For "
                           "automation or sharing use an API key ([coach] backend = \"anthropic-api\").",
            "anthropic-api": "Billed per token on your API key.",
            "ollama": "Runs on this machine; needs a model with tool support."}[cfg.coach_backend]
    return {"configured": ok, "backend": cfg.coach_backend, "model": cfg.coach_model_effective, "message": msg or note,
            "max_tool_calls": cfg.coach_max_tool_calls, "timeout_seconds": cfg.coach_timeout, "busy": cur is not None,
            "current_job": cur.id if cur else None, "tools": list(TOOL_NAMES)}


@router.get("/coach/prompts", summary="Quick prompts for the chat panel")
def prompts():
    return {"prompts": QUICK_PROMPTS}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    skill: Optional[str] = Field(None, max_length=40, description="a skill id (see GET /coach/prompts): runs that skill's prompt with this text as the request")


def _sse(event: str, data: dict, eid: Optional[int] = None) -> str:
    return (f"id: {eid}\n" if eid is not None else "") + f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream(job: CoachJob, after: int = 0):
    def gen():
        i = after
        while True:
            events, over = job.wait_events(i)
            for idx, ev, data in events:
                yield _sse(ev, data, idx)
                i = idx + 1
            if over:
                return
            if not events:
                yield ": keepalive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


def _start(state: AppState, question: str, skill: Optional[str] = None) -> CoachJob:
    if skill is not None and skill not in P.SKILL_SPECS:
        raise ApiError(422, "unknown_skill", f"unknown skill {skill!r}; known: {', '.join(P.SKILL_SPECS)}")
    try:
        return state.coach_jobs.start(question, skill)
    except CoachBusy as e:
        raise ApiError(409, "coach_busy", str(e), {"job_id": (state.coach_jobs.current() or CoachJob("")).id})


@router.post("/coach/stream", summary="Ask the coach; the answer streams as server-sent events (one job at a time)",
             response_class=StreamingResponse)
def stream(req: AskRequest, state: AppState = Depends(get_state)):
    return _stream(_start(state, req.question, req.skill))


@router.post("/coach/jobs", summary="Start a coach job without streaming (returns its id)", status_code=202)
def start_job(req: AskRequest, state: AppState = Depends(get_state)):
    job = _start(state, req.question, req.skill)
    return job.snapshot()


def _job(state: AppState, jid: str) -> CoachJob:
    job = state.coach_jobs.get(jid)
    if job is None:
        raise ApiError(404, "not_found", f"no coach job {jid!r} (jobs are kept in memory while the app runs)")
    return job


@router.get("/coach/jobs/{job_id}", summary="State of a coach job and the answer so far")
def get_job(job_id: str, state: AppState = Depends(get_state)):
    return _job(state, job_id).snapshot()


@router.get("/coach/jobs/{job_id}/stream", summary="Replay and follow the events of a coach job (after = first event index)",
            response_class=StreamingResponse)
def job_stream(job_id: str, after: int = 0, state: AppState = Depends(get_state)):
    return _stream(_job(state, job_id), max(0, after))


@router.post("/coach/jobs/{job_id}/cancel", summary="Cancel a running coach job")
def cancel_job(job_id: str, state: AppState = Depends(get_state)):
    job = state.coach_jobs.cancel(job_id)
    if job is None:
        raise ApiError(404, "not_found", f"no coach job {job_id!r}")
    return job.snapshot()


class ResolveRequest(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=50)


@router.post("/coach/resolve", summary="Resolve the evidence refs of an answer (h_ transaction hashes, rec_ / anm_ / chg_ ids)")
def resolve(req: ResolveRequest, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    """The mapping h_ -> transaction stays on the server: only the user's own page asks for it (the model never could)."""
    ds = snap.ds
    hashes = snap.once("tx-hashes", lambda: {stable_hash(t.key): t for t in ds.whole})
    out = {}
    for ref in dict.fromkeys(req.refs):
        if ref.startswith("h_"):
            t = hashes.get(ref)
            out[ref] = None if t is None else {"kind": "transaction", "tx_key": t.key, "date": t.date.isoformat(),
                                               "amount": money_str(t.amount_c), "category": t.category,
                                               "merchant": t.entity or t.mkey}
        elif ref.startswith("rec_"):          # a ref the model wrote must exist: an invented id resolves to nothing
            ok = ref in snap.once("rec-ids", lambda: {x.id for x in snap.recurring().series})
            out[ref] = {"kind": "series", "link": "/subscriptions"} if ok else None
        elif ref.startswith("anm_"):
            with state.read() as con:
                ok = bool(con.execute("SELECT 1 FROM anomalies WHERE id=?", (ref,)).fetchone())
            out[ref] = {"kind": "anomaly", "link": "/insights"} if ok else None
        elif ref.startswith("chg_"):
            from coach.analytics import pricechanges
            ids = snap.once("chg-ids", lambda: {c.id for c in pricechanges.price_changes(
                snap.ds, recurring=snap.recurring(), include_dismissed=True).changes})
            out[ref] = {"kind": "price_change", "link": "/insights"} if ref in ids else None
        else:
            out[ref] = None
    return {"refs": out}
