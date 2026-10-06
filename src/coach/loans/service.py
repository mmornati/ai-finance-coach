"""Glue of the loans package (E9): cached schedules per Dataset, the payments each loan expects, the feed cards and the per-loan overview
that the CLI, the API and the MCP tools share."""
from __future__ import annotations

import datetime as dt
from typing import Optional

import calendar

from coach.analytics.common import add_months, money_str
from coach.loans import infer as I, loa as LOA, payments as P, schedule as S


def schedules(ds) -> dict:
    """liability id -> LoanSchedule, computed once per Dataset (``ds.today`` is the 'today' of the figures)."""
    got = ds._cache.get("loan_schedules")
    if got is None:
        got = {lb.id: S.compute(lb, ds.today) for _rel, lb in ds.memory.liabilities}
        ds._cache["loan_schedules"] = got
    return got


def schedule_of(ds, lb) -> S.LoanSchedule:
    return schedules(ds).get(lb.id) or S.compute(lb, ds.today)


def observed_of(ds, lb) -> list:
    cache = ds._cache.setdefault("loan_observed", {})
    if lb.id not in cache:
        cache[lb.id] = P.observed(ds, lb)
    return cache[lb.id]


def upcoming_payments(ds, lb, start: dt.date, end: dt.date) -> list[tuple[dt.date, int, str, str]]:
    """The instalments of a liability that no recurring series explains, in [start, end]: [(date, amount_c (positive, what leaves the
    account), certainty, note)]. From the computed schedule (exact dates and amounts, insurance included) when there is one, else
    the flat ``monthly_payment`` on the day of the ``start_date`` (assumed)."""
    lb = S.lax(lb)
    sch = schedule_of(ds, lb)
    if sch.status == "computed":
        return [(r.due, r.total_c, "scheduled",
                 "from the amortization schedule" + (" (approximate: variable rate)" if sch.approximate else ""))
                for r in S.due_items(sch, start, end) if r.total_c > 0 and not (lb.end_date and r.due > lb.end_date)]
    if not lb.monthly_payment or (lb.end_date and lb.end_date < start):
        return []
    day = lb.payment_day or (lb.start_date.day if lb.start_date else 1)
    amount = int(round(lb.monthly_payment * 100))
    out = []
    k = 0
    while True:
        first = dt.date(start.year, start.month, 1)
        mm = add_months(first, k)
        dd = mm.replace(day=min(day, calendar.monthrange(mm.year, mm.month)[1]))
        k += 1
        if dd > end:
            break
        if dd < start or (lb.end_date and dd > lb.end_date):
            continue
        out.append((dd, amount, "assumed", "from memory: monthly_payment on the start_date day"))
    return out


# ---------------------------------------------------------------- the feed

def _card(a: P.Alert, who: str) -> dict:
    return {"id": a.id, "kind": "loan", "subtype": a.type, "severity": a.severity, "title": a.title, "body": a.body,
            "amount": money_str(a.amount_c) if a.amount_c is not None else None, "date": a.date.isoformat(), "subject": who,
            "evidence": list(a.evidence[:5]) + [a.loan], "persist": "ui"}


def alerts_of(ds, lb) -> list[P.Alert]:
    from coach.analytics.forecast import resolve_liability_account
    key = ("loan_alerts", lb.id)
    if key not in ds._cache:
        ds._cache[key] = P.alerts(ds, lb, schedule_of(ds, lb), observed_of(ds, lb), ds.today, resolve_liability_account(ds, lb))
    return ds._cache[key]


def loan_cards(ds) -> list[dict]:
    """Payment alerts (missed / changed / extra / wrong account) and the LOA end-of-contract reminders as insight cards."""
    cards: list[dict] = []
    months = getattr(ds.settings, "loan_reminder_months", 6)
    for _rel, lb in ds.memory.liabilities:
        lb = S.lax(lb)
        who = lb.lender or lb.id
        cards += [_card(a, who) for a in alerts_of(ds, lb)]
        sch = schedule_of(ds, lb)
        if sch.status == "computed" and sch.outstanding_check:
            c = sch.outstanding_check
            cards.append({"id": P._aid("loan-capital", lb.id, c.get("as_of"), c.get("declared")), "kind": "loan", "subtype": "capital_differs", "severity": "medium",
                          "title": f"{who}: the schedule differs from the declared capital",
                          "body": f"Your statement of {c.get('as_of')} says {c.get('declared')} EUR is due; the theoretical table says "
                                  f"{c.get('computed')} EUR on that date. The declared figure is used (rolled forward). Was there an early "
                                  "repayment or a renegotiation? Update the loan (rate, term, capital).",
                          "amount": c.get("declared"), "date": c.get("as_of"), "subject": who, "evidence": [lb.id], "persist": "ui"})
        cards += LOA.cards(lb, ds.today, months)
    return cards


def calendar_reminders(ds, start: dt.date, end: dt.date) -> list[dict]:
    """The date of the 6-month reminder of a lease when it falls in [start, end]: [{date, id, title, note}]."""
    out = []
    months = getattr(ds.settings, "loan_reminder_months", 6)
    for _rel, lb in ds.memory.liabilities:
        lb = S.lax(lb)
        if not LOA.is_lease(lb) or lb.end_date is None:
            continue
        e = LOA.end_status(lb, ds.today, months)
        rd = dt.date.fromisoformat(e["reminder_date"])
        if start <= rd <= end:
            out.append({"date": rd, "id": lb.id, "title": f"{lb.lender or lb.id}: {months} months before the end: buy or return?",
                        "note": f"contract ends {lb.end_date}"})
    return out


# ---------------------------------------------------------------- per-loan overview

def overview_of(ds, rec, lb, rel: Optional[str] = None, with_rows: bool = False) -> dict:
    """Everything the loan page / CLI shows for one liability."""
    from coach.analytics.forecast import resolve_liability_account
    sch = schedule_of(ds, lb)
    obs = observed_of(ds, lb)
    uid = resolve_liability_account(ds, lb)
    series = [x for x in (rec.series if rec is not None else []) if any(k.kind == "liability" and k.id == lb.id for k in x.links)]
    s = max(series, key=lambda x: x.last_date, default=None)
    inf = I.infer_loan(lb, obs, ds.today)
    sd = sch.to_dict()
    rows = sd.pop("rows")
    out = {"id": lb.id, "kind": lb.kind, "schedule": {**sd, "rows_count": len(rows)},
           "payments": {**P.summary(obs), "series_id": s.id if s else None,
                        "next_expected": s.next_expected.isoformat() if s and s.next_expected else None,
                        "debited_uid": uid,
                        "recent": [{"date": p.date.isoformat(), "amount": money_str(p.amount_c), "account": p.account_label}
                                   for p in obs[-12:]]},
           "alerts": [a.to_dict() for a in alerts_of(ds, lb)],
           "inference": inf.to_dict() if inf.status in ("inferred", "insufficient") else {"status": inf.status, "notes": inf.notes},
           "lease": LOA.status(lb, ds.today, getattr(ds.settings, "loan_reminder_months", 6))}
    if with_rows:
        out["schedule"]["rows"] = rows
    return out


# ---------------------------------------------------------------- net worth snapshots (E9-4)

def record_networth(con, cfg, today: Optional[dt.date] = None) -> dict:
    """Store today's net worth snapshot and back-fill the past months (database only; never touches the memory). Called by the
    scheduled run and after a sync; raises on error (the callers wrap it: a failed snapshot never fails a sync)."""
    from coach.analytics import api as analytics_api
    from coach.loans import history as H, networth as NW
    ds = analytics_api.build_dataset(con, cfg, today)
    nw = NW.build(ds, ds.today, asset_stale_months=cfg.memory_asset_stale_months, liability_stale_months=cfg.memory_stale_months,
                  schedules=schedules(ds))
    H.record_snapshot(con, nw)
    n = H.backfill(con, ds, ds.today, schedules(ds))
    return {"as_of": ds.today.isoformat(), "net_worth": money_str(nw.net_worth_c), "n_unknown": nw.n_unknown, "backfilled_months": n}
