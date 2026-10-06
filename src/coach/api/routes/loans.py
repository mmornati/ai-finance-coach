"""Loan detail, early-repayment scenarios, odometer readings and inference proposals (E9-1, E9-3, E9-5, E9-6).

Reads are computed from the snapshot (schedule, linked payments, alerts, suggestions inferred from the payments, lease view). The scenario
endpoint is a calculator: it writes nothing unless ``save`` is true (an insight row). The odometer endpoint writes the memory through the
store (source ``ui``, validated, ``dry_run=true`` previews). Inferred values are never written: ``infer/propose`` queues a memory PROPOSAL
that the user accepts in a terminal.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from coach.agent import insights as ins_mod
from coach.analytics.common import _plain
from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import run_edit
from coach.api.state import UI_SOURCE, AppState, Snapshot
from coach.loans import infer as infer_mod, scenario as sc_mod, service as loans_service
from coach.memory import proposals as prop_mod
from coach.memory.edit import jsonable
from coach.memory.store import MemoryStoreError

router = APIRouter(tags=["loans"])


def _find(snap: Snapshot, loan_id: str):
    for rel, lb in snap.ds.memory.liabilities:
        if lb.id == loan_id:
            return rel, lb
    raise ApiError(404, "not_found", f"no loan with id {loan_id!r}")


@router.get("/loans/{loan_id}", summary="One loan in full: schedule (every instalment), linked payments, alerts, suggestions, lease view")
def loan_detail(loan_id: str, snap: Snapshot = Depends(get_snapshot)):
    rel, lb = _find(snap, loan_id)
    return jsonable(loans_service.overview_of(snap.ds, snap.recurring(), lb, rel, with_rows=True))


@router.get("/loans/{loan_id}/payments", summary="The bank payments linked to the loan (payment_match), oldest first, with its alerts")
def loan_payments(loan_id: str, snap: Snapshot = Depends(get_snapshot)):
    rel, lb = _find(snap, loan_id)
    ds = snap.ds
    obs = loans_service.observed_of(ds, lb)
    return jsonable({"id": lb.id, "payment_match": lb.payment_match,
                     "payments": [{"date": p.date.isoformat(), "amount": p.to_dict()["amount"], "account": p.account_label, "tx_key": p.tx_key}
                                  for p in obs],
                     "alerts": [a.to_dict() for a in loans_service.alerts_of(ds, lb)]})


class ScenarioRequest(BaseModel):
    type: Literal["prepay", "renegotiate", "insurance"]
    amount: Optional[float] = Field(None, gt=0, le=100_000_000)
    on: Optional[dt.date] = None
    new_rate: Optional[float] = Field(None, ge=0, le=25)
    variant: Optional[Literal["renegotiation", "rachat", "surroga"]] = None
    bank_fees: float = Field(0, ge=0, le=1_000_000)
    guarantee_fees: float = Field(0, ge=0, le=1_000_000)
    other_fees: float = Field(0, ge=0, le=1_000_000)
    penalty: Optional[float] = Field(None, ge=0, le=100_000_000)
    alternative: Optional[float] = Field(None, ge=0, le=100_000)
    fees: float = Field(0, ge=0, le=100_000)
    country: Optional[Literal["FR", "IT"]] = None
    save: bool = False


@router.post("/loans/{loan_id}/scenario", summary="Early repayment / renegotiation / insurance scenario on the real schedule (a calculator; save=true stores an insight)")
def loan_scenario(loan_id: str, req: ScenarioRequest, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    rel, lb = _find(snap, loan_id)
    ds = snap.ds
    sch = loans_service.schedule_of(ds, lb)
    country = req.country or ds.memory.country or "FR"
    try:
        if req.type == "prepay":
            if req.amount is None:
                raise ApiError(422, "missing_field", "amount is required for a prepayment")
            res = sc_mod.prepay(lb, sch, ds.today, req.amount, req.on, country, req.penalty)
        elif req.type == "renegotiate":
            if req.new_rate is None:
                raise ApiError(422, "missing_field", "new_rate (the offered nominal rate, %) is required")
            res = sc_mod.renegotiate(lb, sch, ds.today, req.new_rate, country, req.variant, req.bank_fees, req.guarantee_fees, req.other_fees,
                                     req.penalty)
        else:
            if req.alternative is None:
                raise ApiError(422, "missing_field", "alternative (the other policy's monthly premium) is required")
            res = sc_mod.insurance(lb, sch, ds.today, req.alternative, country, req.fees)
    except ValueError as e:
        raise ApiError(422, "bad_scenario", str(e)) from None
    res = _plain(res)
    out = {"scenario": req.type, "result": res, "saved_insight": None}
    if req.save:
        if res.get("status") != "computed":
            raise ApiError(422, "not_computed", "only a computed scenario can be saved")
        title, body = sc_mod.insight_text(req.type, lb.kind, res)
        with state.write() as con:
            out["saved_insight"] = ins_mod.add(con, kind="finding", title=title, body=body, findings=[res], skill="loan-scenario",
                                               backend="local", model="none")
    return out


class OdometerRequest(BaseModel):
    km: int = Field(ge=0, le=2_000_000)
    date: Optional[dt.date] = None
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/loans/{loan_id}/odometer", summary="Record a mileage reading of a leased vehicle (dry_run=true: preview)")
def loan_odometer(loan_id: str, req: OdometerRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot),
                  state: AppState = Depends(get_state)):
    rel, lb = _find(snap, loan_id)
    if lb.kind not in ("loa", "lld"):
        raise ApiError(422, "not_a_lease", "odometer readings are recorded for leases (loa / lld)")
    day = req.date or state.clock()
    readings = {o.date: o.km for o in lb.odometer}
    readings[day] = req.km
    value = [{"date": d, "km": k} for d, k in sorted(readings.items())]
    return run_edit(state, rel, [{"op": "set", "path": "odometer", "value": value}], action="loans-odometer",
                    reason=req.reason or f"odometer {req.km} km on {day}", detail=loan_id, dry_run=dry_run,
                    extra={"id": loan_id, "file": rel})


class InferProposeRequest(BaseModel):
    min_confidence: Literal["low", "medium", "high"] = "medium"


@router.post("/loans/{loan_id}/infer/propose", summary="Queue the terms inferred from the bank payments as a memory PROPOSAL (accepted in a terminal)")
def loan_infer_propose(loan_id: str, req: InferProposeRequest, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    rel, lb = _find(snap, loan_id)
    ds = snap.ds
    inf = infer_mod.infer_loan(lb, loans_service.observed_of(ds, lb), ds.today)
    ops = infer_mod.to_ops(inf, req.min_confidence)
    if not ops:
        raise ApiError(422, "nothing_to_propose", "no suggestion at that confidence: record more of the loan terms first")
    used = [f.field for f in inf.fields if infer_mod.CONF.index(f.confidence) >= infer_mod.CONF.index(req.min_confidence)]
    reason = (f"inferred from {inf.observed.get('count')} observed bank payments (annuity maths); INFERRED values, to be checked against "
              f"the loan contract: {', '.join(used)}")
    try:
        p = prop_mod.create(state.store, rel, ops, reason, source="loans-inference")
    except MemoryStoreError as e:
        raise ApiError(422, "invalid_proposal", str(e)) from None
    state.touch()
    return {"id": p.id, "status": p.status, "fields": used, "accept_command": f"uv run coach memory accept {p.id}", "source": UI_SOURCE}
