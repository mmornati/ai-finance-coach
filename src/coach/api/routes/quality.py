"""Quality (E12): the gold set and its labelling, the evaluation runs, the LLM usage and the logs of the scheduled runs.

* ``/gold*``   the gold set: transactions whose category the USER confirmed. Labelling writes ``gold_labels`` and nothing else (no
  merchant label, no override, no memory change): the category the app shows for a transaction never changes because of a gold label.
* ``/eval/*``  the stored evaluation runs; ``POST /eval/classify`` re-scores the gold set (offline: no model is called).
* ``/usage``   what the LLM calls cost (notional on a subscription), per job / model / day, and where they went.
* ``/logs/runs`` the structured logs of the scheduled runs (step names, durations, counts: never a description or a name).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.state import AppState
from coach.quality import classify_eval as CE, gold as G, logs as L, runs as RUNS, usage as U

router = APIRouter(tags=["quality"])


def _need_tables(state: AppState) -> None:
    with state.read() as con:
        ok = con.execute("SELECT 1 FROM sqlite_master WHERE name='gold_labels'").fetchone()
    if not ok:
        raise ApiError(409, "migration_pending", "the quality tables do not exist yet: run `coach db migrate`")


def _latest(con) -> Optional[dict]:
    rows = RUNS.listing(con, "classify", 1)
    return rows[0] if rows else None


@router.get("/gold", summary="The gold set: counts by origin, the latest accuracy run and a sample of transactions to label")
def gold(n: int = Query(8, ge=1, le=50), strategy: str = Query("money", pattern="^(money|stratified)$"), seed: int = Query(7),
         state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.heavy() as con:
        counts = G.counts(con)
        latest = _latest(con)
        items = G.sample(con, state.cfg, n, strategy, seed)
        runs = RUNS.listing(con, "classify", 8)
    return {"counts": counts, "latest": latest, "runs": runs, "sample": items, "strategy": strategy, "seed": seed}


@router.get("/gold/sample", summary="Transactions to label next (never one already in the gold set)")
def sample(n: int = Query(8, ge=1, le=100), strategy: str = Query("money", pattern="^(money|stratified)$"), seed: int = Query(7),
           state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.heavy() as con:
        return {"items": G.sample(con, state.cfg, n, strategy, seed), "strategy": strategy, "seed": seed}


class LabelBody(BaseModel):
    tx_key: str
    category: str
    note: Optional[str] = Field(None, max_length=300)


@router.post("/gold/label", summary="Confirm the category of one transaction (writes the gold set only)")
def label(req: LabelBody, state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.write() as con:
        try:
            G.set_gold(con, req.tx_key, req.category, note=req.note)
        except G.GoldError as e:
            raise ApiError(422, "rejected", str(e)) from None
        counts = G.counts(con)
    return {"tx_key": req.tx_key, "category": req.category, "counts": counts}


class KeyBody(BaseModel):
    tx_key: str


@router.post("/gold/remove", summary="Take one transaction out of the gold set")
def remove(req: KeyBody, state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.write() as con:
        gone = G.remove_gold(con, req.tx_key)
        counts = G.counts(con)
    if not gone:
        raise ApiError(404, "not_found", "this transaction is not in the gold set")
    return {"removed": True, "counts": counts}


@router.post("/gold/bootstrap", summary="Add what the user already decided (labels, annotations, overrides, splits), marked by origin")
def bootstrap(dry_run: bool = Query(False), state: AppState = Depends(get_state)):
    _need_tables(state)
    if dry_run:
        with state.heavy() as con:
            return G.bootstrap(con, state.cfg, dry_run=True)
    with state.write() as con:
        return G.bootstrap(con, state.cfg)


@router.get("/eval/runs", summary="The stored evaluation runs (classify, models, coach)")
def eval_runs(kind: Optional[str] = Query(None, pattern="^(classify|models|coach)$"), limit: int = Query(15, ge=1, le=100),
              state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.read() as con:
        return {"items": RUNS.listing(con, kind, limit)}


@router.get("/eval/runs/{run_id}", summary="One evaluation run with its full result")
def eval_run(run_id: int, state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.read() as con:
        r = RUNS.get(con, run_id)
    if r is None:
        raise ApiError(404, "not_found", f"no evaluation run #{run_id}")
    return r


@router.post("/eval/classify", summary="Re-score the gold set now (offline: no model is called) and store the run")
def eval_classify(state: AppState = Depends(get_state)):
    _need_tables(state)
    with state.write() as con:
        res = CE.run(con, state.cfg, label="web")
    return res


@router.get("/usage", summary="LLM usage: per job / model, per day, where the calls went, the month against the threshold")
def usage(days: int = Query(30, ge=1, le=366), state: AppState = Depends(get_state)):
    with state.read() as con:
        return U.summary(con, state.cfg, days)


@router.get("/logs/runs", summary="The structured logs of the scheduled runs, newest last")
def log_runs(limit: int = Query(10, ge=1, le=100), state: AppState = Depends(get_state)):
    runs = L.runs_summary(L.read_events(state.cfg.log_dir))
    return {"items": runs[-limit:], "file": L.LOG_NAME}
