"""Monthly cash flow and yearly P&L of a rental property, effort d'epargne and vacancy (E15-2).

Definitions (amounts are positive magnitudes in the direction their name says, ``net`` is signed; money format as in ``analytics.common``)
    * ``rent``  = the rent received (``income.rental``) of the month; a refund of rent lowers it.
    * costs     = ``loan`` (the instalments, principal included: it is a CASH flow), ``charges`` (co-ownership), ``fees`` (management),
                  ``taxes``, ``insurance`` (PNO, GLI), ``works`` and ``other`` (anything else on the account, never dropped). A refund in
                  a cost category lowers that cost.
    * ``net``   = rent - the costs. ``effort`` = the shortfall of a month the owner has to fund: ``max(0, -net)``.
    * ``owner_in`` / ``owner_out``  the owner's own transfers into / out of the property account: shown, never counted as rent or cost.
    * ``loan`` also counts the payments of the linked loan that leave ANOTHER account (the loan files' ``payment_match``).
    * The P&L of a year is the sum of its CLOSED months. ``economic`` takes the principal repaid out of the loan line (the schedule of E9-3 gives
      the split of the year): rent - the other costs - the interest and the borrower insurance.
Rent status of a closed month once the property is let (the first month with a rent, or the commitment start):
    received      the rent is at least half of the expected rent       partial          some rent, under half
    late_paid     none, but the next month holds about two rents       declared_vacancy a period the owner declared (``vacancies``)
    missing       none, and nothing explains it (the data are complete) unknown          the account data do not cover the month
    not_let_yet   before the first rent / the commitment start
The expected rent is the declared ``rent_monthly``, else the median of the rents seen (at least two months).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import (Result, add_months_key, div_cents, last_closed_month, month_end, month_key, month_start, months_between,
                                    to_cents)
from coach.loans import service as LS
from coach.rental import model as M

RECEIVED_SHARE = 0.5
LATE_FACTOR = 1.6                     # the next month holds at least this many expected rents: the missing one was paid late
MIN_RENTS_FOR_MEDIAN = 2
MAX_EVIDENCE = 3


@dataclass
class MonthRow(Result):
    month: str
    rent_c: int
    loan_c: int
    charges_c: int
    fees_c: int
    taxes_c: int
    insurance_c: int
    works_c: int
    other_c: int
    costs_c: int
    net_c: int
    effort_c: int
    owner_in_c: int
    owner_out_c: int
    complete: bool
    rent_status: str
    expected_rent_c: Optional[int] = None
    rent_evidence: list = field(default_factory=list)       # transaction keys of the rent of the month
    n_tx: int = 0


@dataclass
class Rent:
    expected_c: Optional[int]
    source: str                         # declared | observed_median | unknown
    start: Optional[str]                # first month the property counts as let
    usual_day: Optional[int]


def _declared_rent_c(prop) -> Optional[int]:
    v = getattr(prop.asset, "rent_monthly", None)
    return to_cents(v) if v else None


def _covered(ds, prop, m: str) -> bool:
    uids = prop.accounts
    if not uids:
        return False
    return m in ds.coverage.common_months(uids)


def _loan_extra(ds, prop, counted_keys: set) -> list:
    """(date, cost_c, key) of the linked loans' payments that are not on a transaction already attributed to the property."""
    out = []
    for lb in prop.loans:
        for p in LS.observed_of(ds, lb):
            if p.tx_key not in counted_keys:
                out.append((p.date, p.amount_c, p.tx_key))
    return out


def all_rows(ds, prop) -> tuple[list, Rent]:
    """Every closed month from the first one with data to the last closed month (cached per Dataset)."""
    key = ("rental_rows", prop.id)
    got = ds._cache.get(key)
    if got is not None:
        return got
    txs = M.attributed(ds).get(prop.id, [])
    counted = {t.key for t in txs}
    extra = _loan_extra(ds, prop, counted)
    last = last_closed_month(ds.today)
    firsts = [month_key(t.date) for t in txs] + [month_key(d) for d, _c, _k in extra]
    if prop.accounts:
        firsts += [month_key(ds.coverage.of(u).first) for u in prop.accounts if ds.coverage.of(u).first]
    if not firsts or min(firsts) > last:
        res = ([], Rent(_declared_rent_c(prop), "declared" if _declared_rent_c(prop) else "unknown", None, None))
        ds._cache[key] = res
        return res
    months = months_between(min(firsts), last)
    acc = {m: {b: 0 for b in M.BUCKETS} | {"owner_in": 0, "owner_out": 0, "n": 0, "rent_ev": []} for m in months}
    for t in txs:
        m = month_key(t.date)
        if m not in acc:
            continue
        b = M.bucket_of(t.category)
        r = acc[m]
        r["n"] += 1
        if b == "rent":
            r["rent"] += t.amount_c
            if t.amount_c > 0:
                r["rent_ev"].append((t.amount_c, t.key))
        elif b == "transfer":
            if t.amount_c >= 0:
                r["owner_in"] += t.amount_c
            else:
                r["owner_out"] += -t.amount_c
        else:
            r[b] += -t.amount_c
    for d, c, k in extra:
        m = month_key(d)
        if m in acc:
            acc[m]["loan"] += c
            acc[m]["n"] += 1
    rents = [(m, acc[m]["rent"]) for m in months if acc[m]["rent"] > 0]
    declared = _declared_rent_c(prop)
    if declared:
        expected, src = declared, "declared"
    elif len(rents) >= MIN_RENTS_FOR_MEDIAN:
        expected, src = int(statistics.median(v for _m, v in rents[-12:])), "observed_median"
    else:
        expected, src = None, "unknown"
    com = getattr(prop.asset, "commitment", None)
    starts = [month_key(com.start_date)] if com and com.start_date else []
    if rents:
        starts.append(rents[0][0])
    start = min(starts) if starts else None
    days = [t.date.day for t in txs if M.bucket_of(t.category) == "rent" and t.amount_c > 0][-12:]
    rent_info = Rent(expected, src, start, int(statistics.median(days)) if days else None)
    vac = list(getattr(prop.asset, "vacancies", []) or [])

    def in_vacancy(m: str) -> bool:
        a, b = month_start(m), month_end(m)
        return any(v.start <= b and (v.end is None or v.end >= a) for v in vac)

    def received(m: str) -> bool:
        r = acc[m]["rent"]
        return r > 0 if expected is None else r >= RECEIVED_SHARE * expected
    rows = []
    for i, m in enumerate(months):
        a = acc[m]
        complete = _covered(ds, prop, m)
        if start is None or m < start:
            status = "not_let_yet"
        elif received(m):
            status = "received"
        elif a["rent"] > 0:
            status = "partial"
        elif in_vacancy(m):
            status = "declared_vacancy"
        elif not complete:
            status = "unknown"
        elif expected and i + 1 < len(months) and acc[months[i + 1]]["rent"] >= LATE_FACTOR * expected:
            status = "late_paid"
        else:
            status = "missing"
        costs = sum(a[b] for b in M.COST_BUCKETS)
        net = a["rent"] - costs
        ev = [k for _v, k in sorted(a["rent_ev"], reverse=True)[:MAX_EVIDENCE]]
        rows.append(MonthRow(m, a["rent"], a["loan"], a["charges"], a["fees"], a["taxes"], a["insurance"], a["works"], a["other"], costs,
                             net, max(0, -net), a["owner_in"], a["owner_out"], complete, status, expected, ev, a["n"]))
    res = (rows, rent_info)
    ds._cache[key] = res
    return res


def monthly(ds, prop, months: int = 12) -> dict:
    """The last `months` closed months with the summary figures (the CLI, the API and the tool share it)."""
    rows, rent = all_rows(ds, prop)
    shown = rows[-months:] if months else rows
    done = [r for r in shown if r.complete]
    out = {"months": shown, "rent": {"expected_c": rent.expected_c, "source": rent.source, "first_month": rent.start, "usual_day": rent.usual_day},
           "n_months": len(shown), "n_complete": len(done)}
    if done:
        out["average_c"] = {"rent_c": div_cents(sum(r.rent_c for r in done), len(done)),
                            "costs_c": div_cents(sum(r.costs_c for r in done), len(done)),
                            "net_c": div_cents(sum(r.net_c for r in done), len(done)),
                            "effort_c": div_cents(sum(r.effort_c for r in done), len(done))}
    out["vacancy"] = vacancy(rows)
    return out


def vacancy(rows: list) -> dict:
    """Months without rent since the property was let: ``missing`` (nothing explains them), ``declared`` (the owner's periods), unknown
    months are not counted either way."""
    missing = [r.month for r in rows if r.rent_status == "missing"]
    declared = [r.month for r in rows if r.rent_status == "declared_vacancy"]
    late = [r.month for r in rows if r.rent_status == "late_paid"]
    unknown = [r.month for r in rows if r.rent_status == "unknown"]
    let = [r for r in rows if r.rent_status not in ("not_let_yet",)]
    return {"missing_months": missing, "declared_months": declared, "late_paid_months": late, "unknown_months": unknown,
            "n_missing": len(missing), "n_declared": len(declared), "months_since_let": len(let),
            "occupancy_rate": round(1 - (len(missing) + len(declared)) / len(let), 4) if let else None}


def current_month(ds, prop, grace_days: int = 7) -> dict:
    """The month in progress: has this month's rent arrived? (late = the usual day plus the grace days has passed)."""
    rows, rent = all_rows(ds, prop)
    cur = month_key(ds.today)
    txs = M.attributed(ds).get(prop.id, [])
    got = sum(t.amount_c for t in txs if month_key(t.date) == cur and M.bucket_of(t.category) == "rent")
    exp = rent.expected_c
    out = {"month": cur, "rent_c": got, "expected_c": exp, "status": "not_let_yet"}
    if rent.start is None or cur < rent.start:
        return out
    if got > 0 and (exp is None or got >= RECEIVED_SHARE * exp):
        out["status"] = "received"
        return out
    vac = list(getattr(prop.asset, "vacancies", []) or [])
    a, b = month_start(cur), month_end(cur)
    if any(v.start <= b and (v.end is None or v.end >= a) for v in vac):
        out["status"] = "declared_vacancy"
        return out
    due_day = (rent.usual_day or 1) + grace_days
    out["status"] = "late" if ds.today.day > due_day and exp else "pending"
    return out


# ---------------------------------------------------------------- yearly P&L

def _loan_split(ds, prop, year: int) -> Optional[dict]:
    interest = insurance = principal = 0
    found = False
    partial = False
    for lb in prop.loans:
        sch = LS.schedule_of(ds, lb)
        if sch.status != "computed":
            continue
        for y in sch.by_year:
            if y.year == year:
                interest += y.interest_c
                insurance += y.insurance_c
                principal += y.principal_c
                partial = partial or y.partial
                found = True
    if not found:
        return None
    return {"interest_c": interest, "insurance_c": insurance, "principal_c": principal, "partial": partial, "source": "amortization schedule"}


def year_pnl(ds, prop, year: int) -> dict:
    rows, rent = all_rows(ds, prop)
    sel = [r for r in rows if r.month.startswith(f"{year:04d}-")]
    last = last_closed_month(ds.today)
    expected_months = [m for m in months_between(f"{year:04d}-01", f"{year:04d}-12") if m <= last]
    tot = {k: sum(getattr(r, k) for r in sel) for k in ("rent_c", "loan_c", "charges_c", "fees_c", "taxes_c", "insurance_c", "works_c",
                                                         "other_c", "costs_c", "net_c", "effort_c", "owner_in_c", "owner_out_c")}
    n = len(sel)
    out = {"year": year, "totals": tot, "n_months": n, "months_expected": len(expected_months),
           "months_incomplete": [r.month for r in sel if not r.complete],
           "months_missing_data": [m for m in expected_months if m not in {r.month for r in sel}],
           "year_in_progress": year >= ds.today.year, "complete": n == 12 and all(r.complete for r in sel),
           "monthly_average_effort_c": div_cents(tot["effort_c"], n) if n else None,
           "vacancy": vacancy(sel)}
    split = _loan_split(ds, prop, year)
    out["loan_split"] = split
    if split:
        non_loan = tot["costs_c"] - tot["loan_c"]
        out["economic"] = {"result_c": tot["rent_c"] - non_loan - split["interest_c"] - split["insurance_c"],
                             "note": "rent - costs other than the loan - interest - borrower insurance (the principal repaid is not a cost)"}
    value = getattr(prop.asset, "value", None)
    if value and tot["rent_c"] and n == 12:
        out["gross_yield_pct"] = round(tot["rent_c"] / 100 / value * 100, 2)
    return out


def flows_to_review(ds, prop, since_months: int = 24) -> list:
    """Transactions of the property that are in no property category (bucket ``other``), newest first: the ones to label. A category that is
    already specific (the account's own bank fees) stays in ``other flows`` but is not asked about."""
    cut = add_months_key(last_closed_month(ds.today), -since_months + 1) + "-01"
    txs = [t for t in M.attributed(ds).get(prop.id, []) if M.bucket_of(t.category) == "other" and t.category not in M.REVIEW_EXEMPT
           and t.date.isoformat() >= cut]
    return sorted(txs, key=lambda t: (t.date, t.key), reverse=True)
