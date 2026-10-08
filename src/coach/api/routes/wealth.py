"""Loans, assets, contracts and net worth (E5-9, E9): bank balances, the manual memory, the amortization schedules and the history."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends

from coach.analytics.common import money_str, to_cents
from coach.analytics.forecast import resolve_liability_account
from coach.api.deps import get_snapshot, get_state
from coach.api.state import AppState, Snapshot
from coach.i18n_msg import server_msg
from coach.loans import history as nw_history, networth as nw_mod, service as loans_service
from coach.memory import qgen

router = APIRouter(tags=["wealth"])


def _m(x) -> Optional[str]:
    return None if x is None else money_str(to_cents(x))


def _date(x) -> Optional[str]:
    return x.isoformat() if x else None


def _stale(as_of, months: int, today: dt.date) -> bool:
    return as_of is None or as_of < qgen.months_ago(today, months)


def asset_dict(a, today: dt.date, months: int) -> dict:
    amount = a.amount
    return {"id": a.id, "kind": a.kind, "provider": a.provider, "holder": a.holder, "holders": a.holders,
            "value": _m(amount), "value_field": "balance" if a.balance is not None else "value", "as_of": _date(a.as_of), "stale": amount is not None and _stale(a.as_of, months, today),
            "unknown_value": amount is None, "liquidity": a.liquidity, "connected": a.connected,
            "contribution_monthly": _m(a.contribution_monthly), "description": a.description,
            "purchase_price": _m(a.purchase_price), "purchase_date": _date(a.purchase_date), "scheme": a.scheme,
            "bank": a.bank, "rent_monthly": _m(a.rent_monthly), "notes": a.notes, "count": a.count,
            "bank_match": a.bank_match}


LIABILITY_FIELDS = (("monthly_payment", "monthly payment"), ("outstanding", "outstanding capital"),
                    ("outstanding_as_of", "date of the outstanding capital"), ("end_date", "end date"),
                    ("debited_account", "debited account"), ("payment_match", "how its payments are recognised"))
RATE_FIELD = ("rate", "interest rate")
# the English label of each field a loan card says is missing; the web translates `labels.loanField.<code>` (server.json)
LOAN_FIELD_LABEL = dict(LIABILITY_FIELDS + (RATE_FIELD,))


def _loan_extras(ds, rec, l) -> dict:
    """E9: the schedule summary, the capital still due and where it comes from, the payment alerts, the lease view and the suggestions."""
    o = loans_service.overview_of(ds, rec, l)
    sch = o["schedule"]
    summary = {k: sch.get(k) for k in ("status", "mode", "missing", "missing_msg", "alternative", "alternative_msg", "approximate", "payment",
                                       "remaining_capital", "next_due", "first_due", "last_due", "remaining_instalments", "payments_made",
                                       "term_instalments", "total_interest", "total_insurance", "total_cost", "interest_paid", "remaining_interest",
                                       "payment_check", "outstanding_check", "assumptions", "assumptions_msg", "source") if k in sch}
    summary["by_year"] = sch.get("by_year", [])
    cap, source = (sch.get("remaining_capital"), sch.get("source") or "schedule") if sch["status"] == "computed" else (
        (_m(l.outstanding), "declared") if l.outstanding is not None else (None, None))
    inf = o["inference"]
    return {"schedule": summary, "remaining_capital": cap, "remaining_capital_source": source, "alerts": o["alerts"],
            "payments_seen": o["payments"]["count"], "lease": o["lease"],
            "inferred": [{k: f[k] for k in ("field", "value", "confidence", "method", "method_msg", "note", "note_msg") if k in f}
                         for f in inf.get("fields", [])]}


def liability_dict(ds, rec, questions, rel: str, l, months: int) -> dict:
    today = ds.today
    codes = [f for f, _label in LIABILITY_FIELDS if getattr(l, f) in (None, "")]
    if l.rate is None or l.rate.nominal is None:
        codes.append(RATE_FIELD[0])
    missing = [LOAN_FIELD_LABEL[c] for c in codes]
    uid = resolve_liability_account(ds, l)
    series = [x for x in rec.series if any(k.kind == "liability" and k.id == l.id for k in x.links)]
    s = max(series, key=lambda x: x.last_date, default=None)
    qs = [q.id for q in questions if q.status == "open" and (
        (q.suggested_target and q.suggested_target.file == rel) or l.id in " ".join(str(v) for v in q.evidence.values())
        or l.id in q.question)]
    ins = l.insurance
    return {"id": l.id, "file": rel, "kind": l.kind, "lender": l.lender, "asset": l.asset, "holder": l.holder, "start_date": _date(l.start_date),
            "end_date": _date(l.end_date), "first_payment_date": _date(l.first_payment_date), "term_months": l.term_months,
            "payment_day": l.payment_day, "principal": _m(l.principal), "outstanding": _m(l.outstanding),
            "outstanding_as_of": _date(l.outstanding_as_of),
            "outstanding_stale": l.outstanding is not None and _stale(l.outstanding_as_of, months, today),
            "rate": {"type": l.rate.type, "nominal": l.rate.nominal, "taeg": l.rate.taeg, "index": l.rate.index, "margin": l.rate.margin,
                     "cap": l.rate.cap} if l.rate else None,
            "monthly_payment": _m(l.monthly_payment),
            "insurance": ({"provider": ins.provider, "monthly": _m(ins.monthly), "delegated": ins.delegated, "rate_pct": ins.rate_pct,
                           "basis": ins.basis} if ins else None),
            "deferral": {"months": l.deferral.months, "kind": l.deferral.kind} if l.deferral else None,
            "debited_account": l.debited_account or l.debited_from, "debited_uid": uid, "debited_account_label": ds.label(uid) if uid else None,
            "debited_account_configured": l.debited_account or l.debited_from,
            "payment_match": l.payment_match, "early_repayment_penalty": l.early_repayment_penalty,
            "first_payment": _m(l.first_payment), "residual_value": _m(l.residual_value), "mileage_limit_km": l.mileage_limit_km,
            "excess_km_fee": _m(l.excess_km_fee), "initial_km": l.initial_km,
            "odometer": [{"date": o.date.isoformat(), "km": o.km} for o in sorted(l.odometer, key=lambda o: o.date)], "notes": l.notes,
            "missing": missing, "missing_codes": codes, "open_questions": qs,
            "payments": ({"series_id": s.id, "last_date": s.last_date.isoformat(), "next_expected": _date(s.next_expected),
                          "amount": money_str(abs(s.expected_amount_c)), "status": s.status} if s else None),
            **_loan_extras(ds, rec, l)}


@router.get("/liabilities", summary="Loans and other liabilities, with what is missing and the matching bank payments")
def liabilities(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    months = state.cfg.memory_stale_months
    qs = state.store.questions()
    rows = [liability_dict(ds, snap.recurring(), qs, rel, l, months) for rel, l in ds.memory.liabilities]
    owed = sum(to_cents(r["remaining_capital"]) for r in rows if r["remaining_capital"] is not None)
    return {"as_of": ds.today.isoformat(), "liabilities": rows,
            "totals": {"outstanding_known": money_str(owed),
                       "monthly_payments": money_str(sum(to_cents(r["monthly_payment"]) for r in rows if r["monthly_payment"])),
                       "n_unknown_outstanding": sum(1 for r in rows if r["remaining_capital"] is None and r["kind"] not in ("loa", "lld"))},
            **_note(server_msg("netWorth.liabilitiesNote", "Remaining capital comes from the amortization schedule when it is computable, else "
                                                          "the declared figure."))}


def _note(m: dict, field: str = "note") -> dict:
    """``{note: <English>, note_msg: <message>}`` (docs/i18n.md, "Server text")."""
    return {field: m["text"], f"{field}_msg": m}


@router.get("/assets", summary="Assets that are not synced (savings, property, vehicles) with stale value flags")
def assets(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    months = state.cfg.memory_asset_stale_months
    rows = [asset_dict(a, ds.today, months) for a in ds.memory.assets]
    return {"as_of": ds.today.isoformat(), "assets": rows, "stale_after_months": months}


@router.get("/contracts", summary="Contracts on file (provider, renewal, notice, keep decision)")
def contracts(snap: Snapshot = Depends(get_snapshot)):
    out = []
    for rel, c in snap.ds.memory.contracts:
        out.append({"id": c.id, "file": rel, "provider": c.provider, "kind": c.kind, "merchant_match": c.merchant_match,
                    "start_date": _date(c.start_date), "renewal": _date(c.renewal),
                    "billing": {"amount": _m(c.billing.amount), "period": c.billing.period} if c.billing else None,
                    "commitment_end": _date(c.commitment_end), "notice_period_days": c.notice_period_days,
                    "usage": c.usage, "keep": c.keep, "notes": c.notes})
    return {"contracts": out}


@router.get("/net-worth", summary="Net worth from balances, manual assets and liabilities: unknowns are listed, never guessed")
def net_worth(history: bool = False, months: int = 24, snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    ds, cfg = snap.ds, state.cfg
    nw = nw_mod.build(ds, ds.today, asset_stale_months=cfg.memory_asset_stale_months, liability_stale_months=cfg.memory_stale_months,
                      schedules=loans_service.schedules(ds))
    accts = {a.uid: a for a in ds.accounts_in(None)}
    bank, bank_total = [], 0
    for c in nw.components:
        if c.type != "account":
            continue
        a = accts[c.id]
        if c.status == "known":
            bank_total += c.amount_c
        bank.append({"uid": a.uid, "label": a.label, "bank": a.bank, "owner": a.owner, "purpose": a.purpose,
                     "balance": money_str(c.amount_c) if c.status == "known" else None, "as_of": _date(c.as_of),
                     "balance_type": c.balance_type, "stale": c.stale})
    a_rows, a_total, a_connected = [], 0, 0
    by_id = {c.id: c for c in nw.components if c.type == "asset"}
    for a in ds.memory.assets:
        row = asset_dict(a, ds.today, cfg.memory_asset_stale_months)
        c = by_id.get(a.id)
        row["counted"] = bool(c and c.status == "known")
        row["category"] = nw_mod.category_of_asset(a.kind)
        if a.connected:
            row.update(_note(server_msg("netWorth.syncedAsset", "synced from a bank: counted through its balance")))
            a_connected += 1
        elif c and c.status == "known":
            a_total += c.amount_c
        a_rows.append(row)
    l_rows, l_total = [], 0
    for c in nw.components:
        if c.type != "liability":
            continue
        lb = next(m for _r, m in ds.memory.liabilities if m.id == c.id)
        if c.status == "known":
            l_total += c.amount_c
        l_rows.append({"id": c.id, "kind": lb.kind, "lender": lb.lender,
                       "outstanding": money_str(c.amount_c) if c.amount_c is not None else None, "as_of": _date(c.as_of),
                       "stale": c.stale, "counted": c.status == "known", "source": c.source, "excluded": c.status == "excluded",
                       "note": c.note, "note_msg": c.note_msg})
    out = {"as_of": ds.today.isoformat(), "net_worth": money_str(nw.net_worth_c), "complete": nw.complete,
           "unknown": [{"kind": u["type"], "id": u["id"], "label": u["label"], "reason": u["reason"], "reason_msg": u.get("reason_msg")}
                       for u in nw.unknown],
           "stale": [{"kind": x["type"], "id": x["id"]} for x in nw.stale],
           "bank": {"total": money_str(bank_total), "accounts": bank},
           "assets": {"total": money_str(a_total), "items": a_rows, "connected_not_counted": a_connected},
           "liabilities": {"total": money_str(l_total), "items": l_rows},
           "by_category": {k: money_str(v) for k, v in nw.by_category_c.items()},
           "by_owner": {o: {"assets": money_str(v["assets_c"]), "liabilities": money_str(v["liabilities_c"]),
                            "net_worth": money_str(v["net_worth_c"]), "n_unknown": v["n_unknown"]} for o, v in nw.by_owner.items()},
           "n_unknown": nw.n_unknown,
           **_note(server_msg("netWorth.totalPartial", f"The total counts only what is known; {len(nw.unknown)} item(s) have no value and are NOT "
                              "included, so the real figure differs. Liabilities come from the amortization schedule when it is computable, else "
                              "the declared capital.", count=len(nw.unknown)) if nw.unknown else
                   server_msg("netWorth.total", "The total counts only what is known. Liabilities come from the amortization schedule when it "
                              "is computable, else the declared capital."))}
    if history:
        out["history"] = _history(state, months)
    return out


def _history(state: AppState, months: int) -> list:
    try:
        with state.read() as con:
            return nw_history.series(con, max(1, min(months, 120)))
    except Exception:                                    # noqa: BLE001 - e.g. migration 0016 not applied yet ([db] auto_migrate = false)
        return []


@router.get("/net-worth/history", summary="Monthly net worth: stored snapshots plus months rebuilt from the data (unknowns counted per month)")
def net_worth_history(months: int = 24, state: AppState = Depends(get_state)):
    return {"history": _history(state, months),
            **_note(server_msg("netWorth.historyNote", "'snapshot' = recorded on that day; 'backfill' = rebuilt from bank balances and transactions "
                               "(bank accounts only), amortization schedules and manual assets from their own as_of date. A month with unknown "
                               "items is the known part only."))}


@router.post("/net-worth/snapshot", summary="Record today's net worth snapshot and back-fill the past months (database only)")
def net_worth_snapshot(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    ds, cfg = snap.ds, state.cfg
    sch = loans_service.schedules(ds)
    nw = nw_mod.build(ds, ds.today, asset_stale_months=cfg.memory_asset_stale_months, liability_stale_months=cfg.memory_stale_months,
                      schedules=sch)
    with state.write() as con:
        nw_history.record_snapshot(con, nw)
        n = nw_history.backfill(con, ds, ds.today, sch)
    return {"as_of": ds.today.isoformat(), "net_worth": money_str(nw.net_worth_c), "n_unknown": nw.n_unknown, "backfilled_months": n}
