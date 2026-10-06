"""Rental property under a tax-incentive scheme (E15): the list of properties, one property's cash flow / P&L / scheme commitment, the tax-year
figures (candidates, no filing), the renegotiate-or-sell indicators, and the three small writes the page offers (the extension decision, a
vacancy period, a market rate the owner typed in).

Reads are computed from the snapshot of the WHOLE household (a property is a household-wide fact: the person switch does not apply, like the
loans). The facts of a property (account, loan, value, rent, scheme and commitment figures) are written with the generic
``PUT /memory/assets/{id}`` endpoint; the three writes below go through the memory store too (source ``ui``, validated, ``dry_run=true`` previews).
Nothing is looked up on the web: a market rate, a rent cap or an income limit is what the owner typed.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import run_edit
from coach.api.state import AppState, Snapshot
from coach.analytics.common import money_str
from coach.memory.edit import jsonable
from coach.rental import cashflow as CF, indicators as IND, model as M, service as RS, taxyear as TX
from coach.rental.render import plain

router = APIRouter(tags=["rental"])


def _prop(snap: Snapshot, prop_id: str) -> M.Property:
    try:
        return M.find(snap.ds, prop_id)
    except LookupError:
        raise ApiError(404, "not_found", f"no rental property with id {prop_id!r}") from None


@router.get("/rental/properties", summary="Every rental property: account and loan links, rent, last month, vacancy, scheme end, missing facts")
def properties(snap: Snapshot = Depends(get_snapshot)):
    ds = snap.ds
    return jsonable(plain({"as_of": ds.today, "properties": [RS.summary_row(ds, p) for p in M.properties(ds)],
                           "unlinked_rental_accounts": [{"uid": u, "label": ds.label(u)} for u in M.unlinked_rental_accounts(ds)],
                           "property_categories": list(M.PROPERTY_CATEGORIES)}))


@router.get("/rental/{prop_id}", summary="One property: links, monthly cash flow, yearly P&L, vacancy, scheme commitment and missing facts")
def detail(prop_id: str, months: int = 12, year: Optional[int] = None, snap: Snapshot = Depends(get_snapshot)):
    ds = snap.ds
    p = _prop(snap, prop_id)
    o = RS.overview(ds, p, months=max(1, min(months, 60)))
    if year:
        o["pnl"] = CF.year_pnl(ds, p, year)
    o["years"] = sorted({int(r.month[:4]) for r in CF.all_rows(ds, p)[0]}, reverse=True)
    o["flows_to_label"] = len(CF.flows_to_review(ds, p))
    a = p.asset
    o["asset"] = {"id": a.id, "kind": a.kind, "account": a.account, "loan": a.loan, "scheme": a.scheme, "value": a.value,
                  "as_of": a.as_of, "rent_monthly": a.rent_monthly, "purchase_price": a.purchase_price, "purchase_date": a.purchase_date,
                  "commitment": a.commitment.model_dump(exclude_none=True) if a.commitment else None,
                  "market_rate": a.market_rate.model_dump(exclude_none=True) if a.market_rate else None,
                  "vacancies": [v.model_dump(exclude_none=True) for v in a.vacancies]}
    o["tag"] = M.tag_of(p.id)
    return jsonable(plain(o))


@router.get("/rental/{prop_id}/tax", summary="Figures of the rental-income return for an income year: micro-foncier vs reel, scheme reduction, documents (candidates, no filing)")
def tax(prop_id: str, year: Optional[int] = None, snap: Snapshot = Depends(get_snapshot)):
    ds = snap.ds
    p = _prop(snap, prop_id)
    y = year or (ds.today.year if ds.today.month >= 10 else ds.today.year - 1)
    if not 2000 <= y <= ds.today.year:
        raise ApiError(422, "bad_year", "the year must be between 2000 and the current year")
    return jsonable(plain(TX.tax_year(ds, p, y)))


@router.get("/rental/{prop_id}/indicators", summary="Renegotiate-or-sell indicators: loan rate vs the market rate you entered, end of the commitment, net equity")
def indicators(prop_id: str, market_rate: Optional[float] = None, market_date: Optional[dt.date] = None, bank_fees: Optional[float] = None,
               guarantee_fees: Optional[float] = None, other_fees: Optional[float] = None, penalty: Optional[float] = None,
               snap: Snapshot = Depends(get_snapshot)):
    ds = snap.ds
    p = _prop(snap, prop_id)
    if market_rate is not None and not 0 <= market_rate <= 25:
        raise ApiError(422, "bad_rate", "the market rate is a percentage between 0 and 25")
    fees = {k: v for k, v in (("bank_fees", bank_fees), ("guarantee_fees", guarantee_fees), ("other_fees", other_fees), ("penalty", penalty))
            if v is not None}
    return jsonable(plain(IND.indicators(ds, p, market_rate_pct=market_rate, market_rate_date=market_date, fees=fees)))


@router.get("/rental/{prop_id}/flows", summary="Flows of the property account that are in no property category (to label)")
def flows(prop_id: str, months: int = 24, snap: Snapshot = Depends(get_snapshot)):
    ds = snap.ds
    p = _prop(snap, prop_id)
    txs = CF.flows_to_review(ds, p, max(1, min(months, 120)))
    return {"id": p.id, "tag": M.tag_of(p.id), "property_categories": list(M.PROPERTY_CATEGORIES),
            "flows": [{"tx_key": t.key, "date": t.date.isoformat(), "amount": money_str(t.amount_c), "category": t.category, "entity": t.entity}
                      for t in txs[:200]], "n": len(txs)}


class ExtensionRequest(BaseModel):
    decision: Literal["undecided", "extend", "not_extend"]
    years: Optional[int] = Field(None, ge=1, le=30)
    additional_rate_pct: Optional[float] = Field(None, ge=0, le=100)
    decided_on: Optional[dt.date] = None
    note: Optional[str] = Field(None, max_length=500)
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/rental/{prop_id}/extension", summary="Record the decision at the end of the commitment (dry_run=true: preview)")
def extension(prop_id: str, req: ExtensionRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    p = _prop(snap, prop_id)
    if req.decision == "extend" and not req.years:
        raise ApiError(422, "missing_field", "the length of the extension (years) is needed to record an extension")
    ext = {"decision": req.decision, "decided_on": req.decided_on or state.clock()}
    for k in ("years", "additional_rate_pct", "note"):
        if getattr(req, k) is not None:
            ext[k] = getattr(req, k)
    return run_edit(state, "assets.yaml", [{"op": "set", "path": f"assets[{p.id}].commitment.extension", "value": ext}], action="rental-extension",
                    reason=req.reason or f"extension decision: {req.decision}", detail=p.id, dry_run=dry_run, extra={"id": p.id})


class VacancyRequest(BaseModel):
    start: dt.date
    end: Optional[dt.date] = None
    note: Optional[str] = Field(None, max_length=300)
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/rental/{prop_id}/vacancy", summary="Declare a period the property was not let (dry_run=true: preview)")
def vacancy(prop_id: str, req: VacancyRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    p = _prop(snap, prop_id)
    if req.end and req.end < req.start:
        raise ApiError(422, "bad_period", "the end of the period is before its start")
    cur = [{"start": v.start, **({"end": v.end} if v.end else {}), **({"note": v.note} if v.note else {})} for v in p.asset.vacancies]
    new = {"start": req.start, **({"end": req.end} if req.end else {}), **({"note": req.note} if req.note else {})}
    return run_edit(state, "assets.yaml", [{"op": "set", "path": f"assets[{p.id}].vacancies", "value": cur + [new]}], action="rental-vacancy",
                    reason=req.reason or "a vacancy period", detail=p.id, dry_run=dry_run, extra={"id": p.id})


class MarketRateRequest(BaseModel):
    rate_pct: float = Field(ge=0, le=25)
    as_of: Optional[dt.date] = None
    source: Optional[str] = Field(None, max_length=200)
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/rental/{prop_id}/market-rate", summary="Record a market rate you looked up, with its date (dry_run=true: preview)")
def market_rate(prop_id: str, req: MarketRateRequest, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot),
                state: AppState = Depends(get_state)):
    p = _prop(snap, prop_id)
    v = {"rate_pct": req.rate_pct, "as_of": req.as_of or state.clock(), **({"source": req.source} if req.source else {})}
    return run_edit(state, "assets.yaml", [{"op": "set", "path": f"assets[{p.id}].market_rate", "value": v}], action="rental-market-rate",
                    reason=req.reason or "market rate entered by the owner", detail=p.id, dry_run=dry_run, extra={"id": p.id})
