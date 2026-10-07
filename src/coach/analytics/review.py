"""Year in review (E4-10).

For a calendar year Y (default: the year of the last closed month):
    totals       income / spending / saved / net / savings rate from the monthly cash flow (E4-2), ONE-OFFS INCLUDED
                 (`one_off_spending` shows their share, `spending_ex_one_offs` the rest). Closed months only: the year
                 is ``partial`` when it is not over or when some of its months lack data for an account that carries
                 income / spending (`months_complete` of `months_listed`).
    by_event     for every event of memory/events (annotations' ``event``): spending, income, number of transactions.
    top_entities the merchants (canonical entities) with the largest net spend, one-offs included, with their one-off part.
    changes      per spending category, the monthly run-rate average of Y against Y-1 (E4-1 rules: one-offs, capital and
                 savings left out), each average over the months fully covered by the accounts carrying the category in
                 that year (coverage model). A category with no covered month in one of the years is listed under
                 ``not_comparable``; averages over fewer than 12 months are flagged ``partial``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.cashflow import FlowBlock, cashflow
from coach.analytics.common import (CoverageInfo, Result, Scope, div_cents, last_closed_month, pct)
from coach.analytics.common import non_eur_note, note
from coach.analytics.dataset import Dataset, Tx, is_income, is_spending


@dataclass
class EventTotals(Result):
    event: str
    spending_c: int
    income_c: int
    n_tx: int
    first: dt.date
    last: dt.date
    evidence: list = field(default_factory=list)


@dataclass
class EntityTotal(Result):
    entity: str
    spending_c: int
    one_off_c: int
    n_tx: int
    category: str


@dataclass
class CategoryChange(Result):
    category: str
    avg_year_c: int
    avg_prev_c: int
    delta_c: int
    pct: Optional[float]
    months_year: int
    months_prev: int
    partial: bool


@dataclass
class YearReview(Result):
    year: int
    prev_year: int
    partial_year: bool
    months_listed: int
    months_complete: int
    flow: FlowBlock
    by_event: list
    top_entities: list
    increases: list
    decreases: list
    not_comparable: list
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)


def _year_avg(ds: Dataset, txs: list[Tx], cat: str, year: int, scope: Optional[Scope]) -> Optional[tuple[int, int]]:
    carriers = ds.carrying_accounts(cat, scope)
    months = [m for m in ds.coverage.common_months(carriers) if m.startswith(f"{year:04d}-")]
    if not months:
        return None
    ms = set(months)
    tot = -sum(t.amount_c for t in txs if t.category == cat and t.month in ms and not t.is_one_off)
    return div_cents(tot, len(months)), len(months)


def year_review(ds: Dataset, year: Optional[int] = None, scope: Optional[Scope] = None, top: int = 10) -> YearReview:
    last_closed = last_closed_month(ds.today)
    year = year or int(last_closed[:4])
    end_m = min(f"{year:04d}-12", last_closed)
    n = int(end_m[5:]) if end_m[:4] == f"{year:04d}" else 0
    cf = cashflow(ds, scope, months=max(n, 1), end=end_m, breakdown=False) if n else None
    rows = [r for r in (cf.household.months if cf else []) if r.month.startswith(f"{year:04d}-")]
    flow = FlowBlock(cf.household.accounts, rows, None, None) if cf else FlowBlock([], [], None, None)
    from coach.analytics.cashflow import _totals                      # noqa: PLC0415
    flow.totals_complete = _totals([r for r in rows if r.complete])
    flow.totals_all = _totals(rows)
    complete = sum(1 for r in rows if r.complete)
    partial = end_m < f"{year:04d}-12" or complete < 12
    txs = [t for t in ds.txs_in(scope) if t.date.year == year]
    # events
    ev: dict[str, list[Tx]] = {}
    for t in txs:
        if t.event and (is_spending(t.category) or is_income(t.category)) and not t.is_saved:
            ev.setdefault(t.event, []).append(t)
    by_event = []
    for name, ts in sorted(ev.items()):
        by_event.append(EventTotals(name, -sum(t.amount_c for t in ts if is_spending(t.category)),
                                    sum(t.amount_c for t in ts if is_income(t.category)), len(ts),
                                    min(t.date for t in ts), max(t.date for t in ts),
                                    [t.key for t in sorted(ts, key=lambda t: (t.amount_c, t.key))[:10]]))
    by_event.sort(key=lambda e: (-e.spending_c, e.event))
    # top merchants
    ents: dict[str, list[Tx]] = {}
    for t in txs:
        if is_spending(t.category) and not t.is_saved:
            ents.setdefault(t.entity or t.mkey, []).append(t)
    tops = []
    for name, ts in ents.items():
        tot = -sum(t.amount_c for t in ts)
        if tot > 0:
            cat = max({t.category for t in ts}, key=lambda c: (sum(-t.amount_c for t in ts if t.category == c), c))
            tops.append(EntityTotal(name, tot, -sum(t.amount_c for t in ts if t.is_one_off), len(ts), cat))
    tops.sort(key=lambda e: (-e.spending_c, e.entity))
    # category changes
    spend_all = [t for t in ds.txs_in(scope) if is_spending(t.category) and not t.is_saved]
    cats = sorted({t.category for t in spend_all if t.date.year in (year, year - 1)})
    changes, notcomp = [], []
    for c in cats:
        a, b = _year_avg(ds, spend_all, c, year, scope), _year_avg(ds, spend_all, c, year - 1, scope)
        mn = ds.settings.average_min_months
        if a is None or b is None or a[1] < mn or b[1] < mn:
            notcomp.append({"category": c, "months_year": a[1] if a else 0, "months_prev": b[1] if b else 0,
                            "reason": f"fewer than {mn} fully covered months in one of the two years"})
            continue
        changes.append(CategoryChange(c, a[0], b[0], a[0] - b[0], pct(a[0] - b[0], b[0]) if b[0] else None, a[1], b[1],
                                      a[1] < 12 or b[1] < 12))
    inc = sorted((c for c in changes if c.delta_c > 0), key=lambda c: (-c.delta_c, c.category))[:top]
    dec = sorted((c for c in changes if c.delta_c < 0), key=lambda c: (c.delta_c, c.category))[:top]
    uids = ds.uids_in(scope)
    notes = []
    if partial:
        text = (f"{year} is partial: {complete} complete month(s) of {len(rows)} listed"
                + ("" if end_m >= f"{year:04d}-12" else f", year not over (data to {end_m})"))
        notes.append(note("coverage.yearPartial", text, year=year, count=complete, listed=len(rows)) if end_m >= f"{year:04d}-12"
                     else note("coverage.yearPartialOngoing", text, year=year, count=complete, listed=len(rows), end_month=end_m))
    if rows:
        lacking = {}
        for r in rows:
            for lab in r.missing_accounts:
                lacking[lab] = lacking.get(lab, 0) + 1
        if lacking:
            notes.append(note("coverage.accountsLacking",
                              "accounts without full data: " + ", ".join(f"{k} ({v} of {len(rows)} months)" for k, v in sorted(lacking.items())),
                              # labels (not translated) with their number of months lacking data, out of `listed`
                              accounts=", ".join(f"{k} ({v}/{len(rows)})" for k, v in sorted(lacking.items())), listed=len(rows)))
        if complete == 0:
            notes.append(note("coverage.yearNoCompleteMonth",
                              "no month of the year is complete: income and net are NOT meaningful (an account that carries "
                              "income or spending has no data)"))
    if ds.foreign:
        notes.append(non_eur_note(len(ds.foreign)))
    cov = ds.coverage.info(uids, [r.month for r in rows if r.complete],
                           "totals over closed months of the year; category averages over covered months of each year",
                           notes)
    evidence = sorted({k for e in by_event for k in e.evidence} | {t.key for t in txs if t.is_one_off})
    return YearReview(year, year - 1, partial, len(rows), complete, flow, by_event, tops[:top], inc, dec, notcomp, cov,
                      evidence)
