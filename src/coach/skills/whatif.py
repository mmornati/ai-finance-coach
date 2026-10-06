"""what_if(scenario) (E7-9): a deterministic scenario engine on top of the forecast and the coverage-aware averages.

A scenario is a list of changes (strict JSON schema below). The BASELINE is the household forecast (E4-6: latest balances +
recurring series replayed + the variable spend spread evenly), and the baseline monthly savings is the average of
(income - spending without one-offs) over the last complete months of the cash flow. The SCENARIO applies the changes to
exactly those figures, nothing else is re-estimated:

    cancel_recurring   the series' future occurrences are removed from the forecast (an income series: its income is lost)
    adjust_category    monthly average of the category (or group) x percent, spread evenly over the days
    set_category_level the category's monthly average moves to a target; the difference is spread evenly
    add_monthly        a new monthly outflow (day of the month of ``start_date``, default the 1st)
    remove_monthly     a monthly outflow that disappears (the same shape, with the opposite sign)
    one_off            one amount on a date (out by default)
    prepay_loan        a partial prepayment of a memory liability on a date. With the loan's amortization schedule computable (E9-3:
                       the capital due on that date, the instalment and the instalments left): exact schedules before / after (months saved, interest saved, the instalment if the term is kept). Otherwise a
                       zero-interest APPROXIMATION, flagged ``approximate``. A possible IRA is reported apart, not added.
    change_income      the salary series (income.salary) x percent, or a fixed monthly amount, from a date

Result: baseline vs scenario for the forecast (start, minimum balance and date, balance at the horizon and at 90 days, first
negative / at-risk day), the monthly savings and the yearly impact (recurring effects x 12 + one-offs inside the next 12
months), with the evidence (series ids, categories, liability ids) and the assumptions. Money is cents internally.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.analytics.averages import category_averages
from coach.analytics.cashflow import cashflow
from coach.analytics.common import add_months, div_cents, money_str
from coach.analytics.dataset import Dataset
from coach.analytics.forecast import DAYS_PER_MONTH, forecast
from coach.loans import schedule as loan_sched, service as loans_service
from coach.analytics.recurring import RecurringResult, detect_recurring, occurrences
from coach.skills import loans as L
from coach.skills.money import cents, pct_of_c

DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "YYYY-MM-DD"}
AMOUNT = {"type": "number", "exclusiveMinimum": 0, "maximum": 10_000_000, "description": "EUR, positive"}


def _change(kind: str, props: dict, required: tuple) -> dict:
    return {"type": "object", "properties": {"type": {"const": kind}, **props}, "required": ["type", *required],
            "additionalProperties": False}


CHANGE_SCHEMAS = [
    _change("cancel_recurring", {"series": {"type": "string", "pattern": r"^rec_[0-9a-f]{6,}$", "description": "a `rec_` id from `recurring`"},
                                 "from_date": DATE}, ("series",)),
    _change("adjust_category", {"category": {"type": "string", "maxLength": 80, "description": "a category (food.groceries) or a group (food)"},
                                "percent": {"type": "number", "minimum": -100, "maximum": 500}}, ("category", "percent")),
    _change("set_category_level", {"category": {"type": "string", "maxLength": 80},
                                   "monthly_target": {"type": "number", "minimum": 0, "maximum": 1_000_000}}, ("category", "monthly_target")),
    _change("add_monthly", {"amount": AMOUNT, "label": {"type": "string", "maxLength": 40, "description": "free note of the caller; never echoed back"}, "start_date": DATE, "end_date": DATE}, ("amount",)),
    _change("remove_monthly", {"amount": AMOUNT, "label": {"type": "string", "maxLength": 40, "description": "free note of the caller; never echoed back"}, "start_date": DATE, "end_date": DATE}, ("amount",)),
    _change("one_off", {"amount": AMOUNT, "date": DATE, "direction": {"type": "string", "enum": ["out", "in"]}, "label": {"type": "string", "maxLength": 40, "description": "free note of the caller; never echoed back"}},
            ("amount", "date")),
    _change("prepay_loan", {"liability": {"type": "string", "maxLength": 80, "description": "a liability id as shown by `memory_context`"},
                            "amount": AMOUNT, "date": DATE, "keep": {"type": "string", "enum": ["payment", "term"],
                                                                    "description": "payment: same instalment, shorter loan (default); term: lower instalment"}},
            ("liability", "amount", "date")),
    _change("change_income", {"percent": {"type": "number", "minimum": -100, "maximum": 500},
                              "monthly_delta": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000}, "from_date": DATE}, ()),
]
SCENARIO_SCHEMA = {"type": "object", "properties": {
    "days": {"type": "integer", "minimum": 30, "maximum": 365, "description": "forecast horizon (default 90)"},
    "changes": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"oneOf": CHANGE_SCHEMAS}}},
    "required": ["changes"], "additionalProperties": False}


def _m(c): return money_str(c)


def _date(v, default: Optional[dt.date] = None) -> Optional[dt.date]:
    if v is None:
        return default
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{v!r} is not a date (YYYY-MM-DD)") from None


class _Effects:
    """What one change does: dated cash events, a daily spread, and the recurring monthly / one-off equivalents."""

    def __init__(self, kind: str):
        self.kind = kind
        self.events: dict[dt.date, int] = {}
        self.daily_c = 0.0                 # cents per day, from `daily_from`
        self.daily_from: Optional[dt.date] = None
        self.monthly_c = 0                 # recurring effect per month on the household's cash (+ = more cash)
        self.one_off_c = 0                 # dated one-off (+ = more cash) inside the horizon
        self.yearly_c = 0
        self.evidence: dict = {}
        self.notes: list[str] = []
        self.approximate = False
        self.extra: dict = {}

    def add(self, day: dt.date, c: int) -> None:
        self.events[day] = self.events.get(day, 0) + c


def _monthly_dates(first: dt.date, last: dt.date) -> list[dt.date]:
    out, k = [], 0
    while True:
        d = add_months(first, k)
        if d > last:
            return out
        out.append(d)
        k += 1
        if k > 400:
            return out


def _category_avg(ds: Dataset, category: str) -> tuple[int, list[str], bool]:
    """(monthly average in cents, leaves used, low_confidence) of a category or group, coverage-aware, one-offs left out."""
    res = category_averages(ds)
    cat = category.strip().rstrip(".")
    rows = [r for r in res.categories if r.category == cat or r.category.startswith(cat + ".")]
    if not rows:
        known = any(u["category"] == cat or u["category"].startswith(cat + ".") for u in res.unavailable)
        raise ValueError(f"no coverage-aware monthly average for {category!r}" +
                         (": no month is fully covered by the accounts carrying it" if known else ": unknown category or no spending"))
    return sum(r.monthly_avg_c for r in rows), [r.category for r in rows], any(r.low_confidence for r in rows)


def _apply(ds: Dataset, ch: dict, rec: RecurringResult, today: dt.date, end: dt.date) -> _Effects:
    t = ch["type"]
    e = _Effects(t)
    if t == "cancel_recurring":
        x = next((s for s in rec.series if s.id == ch["series"]), None)
        if x is None:
            raise ValueError("unknown series: use a `rec_` id returned by `recurring`")
        start = max(_date(ch.get("from_date"), today + dt.timedelta(days=1)), today + dt.timedelta(days=1))
        sign = 1 if x.direction == "out" else -1
        for dd, _ov in occurrences(x, start, end):
            e.add(dd, sign * abs(x.expected_amount_c))
        e.monthly_c = sign * div_cents(x.yearly_cost_c, 12)
        e.yearly_c = sign * x.yearly_cost_c
        e.evidence = {"series": x.id, "entity": x.entity, "category": x.category, "cadence": x.cadence}
        if x.status != "active":
            e.notes.append("the series is already ended: nothing changes")
            e.monthly_c = e.yearly_c = 0
        if x.direction == "in":
            e.notes.append("this is an income: cancelling it reduces the household's cash")
    elif t in ("adjust_category", "set_category_level"):
        avg, leaves, low = _category_avg(ds, ch["category"])
        if t == "adjust_category":
            delta = -pct_of_c(avg, ch["percent"])
        else:
            delta = avg - cents(ch["monthly_target"])
        e.monthly_c, e.yearly_c = delta, delta * 12
        e.daily_c, e.daily_from = delta / DAYS_PER_MONTH, today + dt.timedelta(days=1)
        e.evidence = {"category": ch["category"], "usual_monthly": _m(avg), "leaves": leaves[:6]}
        e.notes.append("the effect is the change of the category's usual monthly spending, spread evenly over the days")
        if low:
            e.notes.append("the category average rests on few months: low confidence")
    elif t in ("add_monthly", "remove_monthly"):
        sign = -1 if t == "add_monthly" else 1
        amt = cents(ch["amount"])
        first = _date(ch.get("start_date"), today + dt.timedelta(days=1))
        last = min(_date(ch.get("end_date"), end), end)
        for dd in _monthly_dates(first, last):
            if dd > today:
                e.add(dd, sign * amt)
        n12 = sum(1 for dd in _monthly_dates(first, min(_date(ch.get("end_date"), add_months(today, 12)), add_months(today, 12)))
                  if dd > today)
        e.monthly_c, e.yearly_c = sign * amt, sign * amt * n12
        e.evidence = {}                       # the model-written `label` is NOT echoed: it is model text, not data
    elif t == "one_off":
        sign = 1 if ch.get("direction") == "in" else -1
        day = _date(ch["date"])
        amt = cents(ch["amount"]) * sign
        if today < day <= end:
            e.add(day, amt)
        if today < day <= add_months(today, 12):
            e.one_off_c = amt
        e.evidence = {"date": day}
        if day <= today:
            e.notes.append("the date is not in the future: nothing is projected")
    elif t == "prepay_loan":
        _prepay(ds, ch, e, today, end)
    elif t == "change_income":
        if ("percent" in ch) == ("monthly_delta" in ch):
            raise ValueError("change_income needs exactly one of percent or monthly_delta")
        sal = [s for s in rec.series if s.kind == "income" and s.category == "income.salary" and s.status == "active"]
        base = sum(div_cents(s.yearly_cost_c, 12) for s in sal)
        delta = pct_of_c(base, ch["percent"]) if "percent" in ch else cents(ch["monthly_delta"])
        if "percent" in ch and not sal:
            raise ValueError("no active salary series found: use monthly_delta instead")
        first = _date(ch.get("from_date"), today + dt.timedelta(days=1))
        dom = sal[0].day_of_month if sal and sal[0].day_of_month else first.day
        for dd in _monthly_dates(first.replace(day=1), end):
            dd = dd.replace(day=min(dom, 28))
            if first <= dd <= end and dd > today:
                e.add(dd, delta)
        e.monthly_c, e.yearly_c = delta, delta * 12
        e.evidence = {"series": [s.id for s in sal][:3], "usual_monthly_salary": _m(base) if sal else None}
        e.notes.append("a raise is shown before tax changes and social contributions are re-computed: it is the net amount received")
    else:                                                          # pragma: no cover - the schema forbids it
        raise ValueError(f"unknown change type {t!r}")
    return e


def _prepay(ds: Dataset, ch: dict, e: _Effects, today: dt.date, end: dt.date) -> None:
    lb = next((m for _r, m in ds.memory.liabilities if m.id == ch["liability"]), None)
    if lb is None:
        raise ValueError("unknown liability: use an id from `memory_context`")
    f = L.facts_of(lb)
    st = L.loan_state(f, today)
    cap = st.pop("_remaining_capital_c")
    amount = cents(ch["amount"])
    day = _date(ch["date"])
    keep = ch.get("keep") or "payment"
    e.evidence = {"liability": lb.id, "kind": lb.kind}
    if today < day <= end:
        e.add(day, -amount)
    if today < day <= add_months(today, 12):
        e.one_off_c = -amount
    am = st.get("amortization") or {}
    rate = f.rate_pct if f.rate_pct is not None else (st.get("implied_rate") and L.dec(st["implied_rate"]["nominal_pct"]))
    pay = f.payment_c
    if am.get("status") == "computed":
        pay = cents(am["payment_theoretical"])
    months_left = st.get("remaining_months_to_end_date")
    # E9-3: the loan's own amortization schedule (deferral, first due date, insurance) when it is computable
    sch = loans_service.schedule_of(ds, lb)
    sched_used = sch.status == "computed" and bool(lb.rate and lb.rate.nominal is not None)
    if sched_used:
        at = max(day, today)                       # a prepayment dated on an instalment day is applied before that instalment
        cap = loan_sched.balance_on(sch, at - dt.timedelta(days=1))
        months_left = sum(1 for r in sch.rows if r.due >= at)
        pay = sch.payment_c
        rate = L.dec(lb.rate.nominal)
        e.notes.append("the capital, the instalment and the months left come from the loan's amortization schedule (E9-3)"
                       + ("; variable rate: approximate" if sch.approximate else ""))
    if cap is None or cap <= amount:
        e.approximate = True
        e.extra = {"needs": ["outstanding (capital still due) - or principal, rate, start_date and end_date"] if cap is None else
                   ["the prepayment is not smaller than the capital still due"]}
        e.notes.append("only the cash movement is shown: the capital still due is unknown (see the missing fields)" if cap is None
                       else "the amount covers the whole capital: use a payoff quote from the lender")
        return
    pe = L.prepayment_effect(capital_c=cap, amount_c=amount, rate_pct=rate, payment_c=pay, remaining_months=months_left, keep=keep,
                             ira_possible=lb.kind == "mortgage")
    e.approximate = pe.approximate or (f.rate_pct is None and not sched_used) or (sched_used and sch.approximate)
    saved_monthly = -pe.monthly_effect_c
    if saved_monthly:
        e.monthly_c, e.yearly_c = saved_monthly, saved_monthly * 12
        for dd in _monthly_dates(add_months(day, 1), end):
            if dd > today:
                e.add(dd, saved_monthly)
    e.extra = {"capital_before": _m(pe.capital_before_c), "capital_after": _m(pe.capital_after_c), "keep": keep,
               "instalment_before": _m(pe.payment_before_c), "instalment_after": _m(pe.payment_after_c),
               "months_saved": pe.months_saved, "lifetime_interest_saved": _m(pe.interest_saved_c),
               "possible_penalty": _m(pe.penalty_estimate_c), "method": pe.method}
    e.notes += pe.notes
    if f.rate_pct is None and rate is not None and not sched_used:
        e.notes.append("the rate used is back-solved from the capital and the instalment: confirm it")
    if keep == "payment":
        e.notes.append("the instalment stays the same: the benefit is a shorter loan and less interest, not a higher monthly cash flow")


def _summary(points: list, start_c: Optional[int], days: int) -> dict:
    if not points:
        return {"available": False}
    mn = min(points, key=lambda p: (p["bal"], p["date"]))
    last = points[-1]
    p90 = points[89] if len(points) >= 90 and days >= 90 else None
    neg = next((p["date"] for p in points if p["bal"] < 0), None)
    risk = next((p["date"] for p in points if p["low"] < 0), None)
    return {"available": True, "start_balance": _m(start_c), "min_balance": _m(mn["bal"]), "min_date": mn["date"],
            "end_balance": _m(last["bal"]), "end_date": last["date"], "balance_90d": _m(p90["bal"]) if p90 else None,
            "first_negative": neg, "first_at_risk": risk}


def what_if(ds: Dataset, scenario: dict, recurring: Optional[RecurringResult] = None) -> dict:
    changes = scenario.get("changes") or []
    if not 1 <= len(changes) <= 6:
        raise ValueError("a scenario has 1 to 6 changes")
    days = int(scenario.get("days") or 90)
    today = ds.today
    end = today + dt.timedelta(days=days)
    rec = recurring or detect_recurring(ds)
    effects = [_apply(ds, ch, rec, today, end) for ch in changes]
    fc = forecast(ds, days=days, points=True, recurring=rec).household
    base_pts = [{"date": p.date, "bal": p.balance_c, "low": p.low_c, "high": p.high_c} for p in fc.points]
    events: dict[dt.date, int] = {}
    for e in effects:
        for dd, c in e.events.items():
            events[dd] = events.get(dd, 0) + c
    scen_pts, cum, cum_daily = [], 0, 0.0
    for p in base_pts:
        cum += events.get(p["date"], 0)
        cum_daily += sum(e.daily_c for e in effects if e.daily_from is not None and p["date"] >= e.daily_from)
        shift = cum + int(round(cum_daily))
        scen_pts.append({"date": p["date"], "bal": p["bal"] + shift, "low": p["low"] + shift, "high": p["high"] + shift})
    # monthly savings: the cash flow's own figure over the complete months
    cf = cashflow(ds, months=6, breakdown=False).household.totals_complete
    base_savings = div_cents(cf.income_c - cf.spending_ex_one_offs_c, cf.n_months) if cf and cf.n_months else None
    monthly_delta = sum(e.monthly_c for e in effects)
    one_off = sum(e.one_off_c for e in effects)
    yearly = sum(e.yearly_c for e in effects) + one_off
    base = _summary(base_pts, fc.start_balance_c, days)
    scen = _summary(scen_pts, fc.start_balance_c, days)
    base["monthly_savings"] = _m(base_savings)
    scen["monthly_savings"] = _m(base_savings + monthly_delta) if base_savings is not None else None
    delta = {"monthly_savings": _m(monthly_delta), "yearly_impact": _m(yearly), "one_off_next_12_months": _m(one_off),
             "end_balance": _m(scen_pts[-1]["bal"] - base_pts[-1]["bal"]) if base_pts else None,
             "min_balance": _m(min(p["bal"] for p in scen_pts) - min(p["bal"] for p in base_pts)) if base_pts else None}
    flags = list(fc.flags)
    notes = ["baseline = the household forecast (see `forecast`); the scenario shifts it by the changes and re-estimates nothing else",
             "monthly savings baseline = average of (income - spending without one-offs) over the last complete months"]
    if not base_pts:
        notes.append("no balance snapshot: the forecast part is unavailable, the monthly and yearly effects are still computed")
    if base_savings is None:
        notes.append("no complete month in the cash flow: the baseline monthly savings is unknown")
    out_changes = []
    for ch, e in zip(changes, effects):
        row = {"type": e.kind, "monthly_effect": _m(e.monthly_c), "yearly_effect": _m(e.yearly_c), "one_off": _m(e.one_off_c) if e.one_off_c else None,
               "approximate": e.approximate, "evidence": e.evidence, "notes": e.notes}
        row.update(e.extra)
        out_changes.append(row)
    return {"horizon_days": days, "as_of": today, "baseline": base, "scenario": scen, "delta": delta, "changes": out_changes,
            "forecast_flags": flags, "notes": notes}
