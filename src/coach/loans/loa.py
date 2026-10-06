"""LOA / LLD end of contract (E9-6): when it ends, what to decide, whether the mileage will exceed the limit, and what to do on return.

Mileage projection (stated in the result): the household records readings (``odometer``: date + km). The pace is the distance driven
between the start of the contract (``start_date`` at ``initial_km``, a new car = 0) and the latest reading, per day; the projected
odometer on the end date is the latest reading + pace x the days left; the km counted against the limit are that minus ``initial_km``.
``excess_km`` = projected - ``mileage_limit_km`` (>= 0) and the cost estimate is excess_km x ``excess_km_fee``. Without ``initial_km`` the
pace comes from the readings alone (two are needed) and the limit cannot be compared exactly: the result says so. Nothing is invented:
a missing fee, limit or reading is listed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

from coach.analytics.common import add_months, money_str
from coach.loans.schedule import lax
from coach.skills.money import cents

MIN_SPAN_DAYS = 30
RESTITUTION_CHECKLIST = [
    "read the end-of-contract clauses: the notice period to announce the return, the return place and the date window",
    "book the pre-return inspection if the lender offers one: damage is billed against its grid (wear and tear is allowed)",
    "gather the documents: registration card, service book and invoices of the maintenance, both keys, the manuals, the charging "
    "cables / accessories delivered with the car",
    "check the tyres, the windscreen, the bodywork and the interior against the grid; repair small damages where it is cheaper than "
    "the lender's price",
    "record the final mileage (photo of the odometer) and compare it with the contractual limit; each km above it costs the excess fee",
    "end the car insurance and any direct debit only after the lender confirms the return; keep the return receipt (PV de restitution)",
    "if you buy the car: the option price is the residual value; ask the lender for the payoff letter and the deadline to exercise it",
]


def is_lease(lb) -> bool:
    return lb.kind in ("loa", "lld")


def end_status(lb, today: dt.date, reminder_months: int = 6) -> dict:
    if lb.end_date is None:
        return {"end_date": None, "known": False}
    left = (lb.end_date - today).days
    remind = add_months(lb.end_date, -reminder_months)
    return {"end_date": lb.end_date.isoformat(), "known": True, "days_left": left, "ended": left < 0,
            "reminder_date": remind.isoformat(), "reminder_active": remind <= today <= lb.end_date,
            "reminder_months": reminder_months}


def mileage(lb, today: dt.date) -> dict:
    lb = lax(lb)
    out: dict = {"limit_km": lb.mileage_limit_km,
                 "excess_km_fee": money_str(cents(lb.excess_km_fee)) if lb.excess_km_fee is not None else None,
                 "readings": len(lb.odometer), "status": "needs_readings", "needs": []}
    readings = sorted(lb.odometer, key=lambda o: o.date)
    if lb.end_date is None:
        out["needs"].append("end_date")
    if lb.mileage_limit_km is None:
        out["needs"].append("mileage_limit_km")
    if lb.initial_km is None:
        out["needs"].append("initial_km (the odometer at the start of the contract; 0 for a new car)")
    if not readings:
        out["needs"].append("an odometer reading (date + km)")
        return out
    last = readings[-1]
    out["latest"] = {"date": last.date.isoformat(), "km": last.km, "age_days": (today - last.date).days}
    if lb.initial_km is not None and lb.start_date is not None:
        ref_date, ref_km = lb.start_date, lb.initial_km
    elif len(readings) >= 2:
        ref_date, ref_km = readings[0].date, readings[0].km
        out["basis"] = "the first and the latest reading (initial_km is not recorded)"
    else:
        out["needs"].append("a second reading, or start_date + initial_km")
        return out
    span = (last.date - ref_date).days
    if span < MIN_SPAN_DAYS:
        out["needs"].append(f"readings at least {MIN_SPAN_DAYS} days apart from the start of the measure")
        return out
    per_day = (last.km - ref_km) / span
    out["pace"] = {"km_per_year": round(per_day * 365.25), "km_per_month": round(per_day * 30.4375), "since": ref_date.isoformat()}
    if lb.end_date is None:
        return out
    days_left = max((lb.end_date - last.date).days, 0)
    projected_odo = round(last.km + per_day * days_left)
    out["projected_odometer_at_end"] = projected_odo
    if lb.initial_km is not None:
        contract_km = projected_odo - lb.initial_km
        out["projected_contract_km"] = contract_km
        if lb.mileage_limit_km is not None:
            excess = max(0, contract_km - lb.mileage_limit_km)
            out["excess_km"] = excess
            out["status"] = "over_limit" if excess else "within_limit"
            if lb.mileage_limit_km and lb.start_date:
                total_days = max((lb.end_date - lb.start_date).days, 1)
                out["allowed_km_per_year"] = round(lb.mileage_limit_km / total_days * 365.25)
            if excess and lb.excess_km_fee is not None:
                out["excess_cost"] = money_str(cents(excess * lb.excess_km_fee))
            elif excess:
                out["needs"].append("excess_km_fee (to price the excess)")
            return out
    out["status"] = "pace_only"
    return out


def decision(lb, market_value: Optional[float] = None) -> dict:
    """Buy (option price = residual value) versus return. The market value is the user's, never looked up here."""
    out: dict = {"residual_value": money_str(cents(lb.residual_value)) if lb.residual_value is not None else None}
    if lb.residual_value is None:
        out["needs"] = ["residual_value (the purchase-option price in the contract)"]
        return out
    if market_value is not None:
        gap = cents(market_value) - cents(lb.residual_value)
        out["market_value"] = money_str(cents(market_value))
        out["market_minus_option_price"] = money_str(gap)
        out["reading"] = ("the market value is above the option price: buying and reselling would leave the difference, before fees"
                          if gap > 0 else "the option price is not below the market value: returning the car is not worse on price alone")
    else:
        out["note"] = "give the market value of the same car (a dated quote you looked up) to compare it with the option price"
    out["other_factors"] = ["the financing of the option price (cash or a new loan)", "the condition and the mileage against the contract",
                            "the cost of a replacement vehicle"]
    return out


def status(lb, today: dt.date, reminder_months: int = 6, market_value: Optional[float] = None) -> Optional[dict]:
    """Everything about one lease; None for a loan that is not a lease."""
    lb = lax(lb)
    if not is_lease(lb):
        return None
    end = end_status(lb, today, reminder_months)
    missing = [f for f, v in (("end_date", lb.end_date), ("residual_value", lb.residual_value),
                              ("mileage_limit_km", lb.mileage_limit_km), ("excess_km_fee", lb.excess_km_fee),
                              ("monthly_payment", lb.monthly_payment)) if v is None]
    return {"id": lb.id, "kind": lb.kind, "end": end, "decision": decision(lb, market_value), "mileage": mileage(lb, today),
            "checklist": RESTITUTION_CHECKLIST, "missing": missing}


def _iid(*p) -> str:
    return "ins_" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:10]


def cards(lb, today: dt.date, reminder_months: int = 6) -> list[dict]:
    """Insight cards (the shape of the insights feed) of one lease: the end-of-contract reminder, the mileage warning."""
    lb = lax(lb)
    st = status(lb, today, reminder_months)
    if st is None or not st["end"]["known"] or st["end"]["ended"]:
        return []
    who = lb.lender or lb.id
    end = st["end"]
    out = []
    if end["reminder_active"]:
        d = end["days_left"]
        out.append({"id": _iid("loa-end", lb.id, lb.end_date), "kind": "loan", "subtype": "loa_end",
                    "severity": "high" if d <= 90 else "medium", "title": f"{who} ends in {d} days: buy or return?",
                    "body": f"The contract ends on {lb.end_date}. Decide between buying the car (option price "
                            f"{st['decision']['residual_value'] or 'unknown: record residual_value'}) and returning it; the return checklist "
                            "and the mileage projection are on the loan page. Verify the notice period in your contract.",
                    "amount": st["decision"]["residual_value"], "date": end["reminder_date"], "subject": who, "evidence": [lb.id],
                    "persist": "ui"})
        mi = st["mileage"]
        if mi.get("status") == "over_limit":
            out.append({"id": _iid("loa-km", lb.id, lb.end_date, mi.get("excess_km")), "kind": "loan", "subtype": "loa_mileage",
                        "severity": "high", "title": f"{who}: about {mi['excess_km']:,} km over the limit at the end",
                        "body": f"At the current pace the projected mileage is {mi['projected_contract_km']:,} km against a limit of "
                                f"{mi['limit_km']:,} km"
                                + (f", an estimated {mi['excess_cost']} EUR of excess-mileage fees." if mi.get("excess_cost")
                                   else ". Record the excess fee to price it."),
                        "amount": mi.get("excess_cost"), "date": today.isoformat(), "subject": who, "evidence": [lb.id],
                        "persist": "ui"})
        elif mi.get("status") in ("needs_readings", "pace_only") and (
                not lb.odometer or (today - max(o.date for o in lb.odometer)).days > 90):
            out.append({"id": _iid("loa-km-missing", lb.id, today.strftime("%Y-%m")), "kind": "loan", "subtype": "loa_mileage_missing",
                        "severity": "low", "title": f"{who}: record the current mileage",
                        "body": "Within 6 months of the end of the contract the mileage matters: record the odometer (date and km) so the "
                                "coach can project the excess-mileage cost.", "amount": None, "date": today.isoformat(), "subject": who,
                        "evidence": [lb.id], "persist": "ui"})
    return out
