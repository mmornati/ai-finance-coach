"""Amortization schedule of a memory liability (E9-3). Deterministic, integer cents, built on :mod:`coach.skills.loans`.

Conventions (stated in every result, see ``assumptions``)
    * Instalment k is due ``first_payment_date`` + (k - 1) months (default first due date: ``start_date`` + 1 month), on
      ``payment_day`` when given (clamped to the month end).
    * Number of instalments: ``term_months``, else the whole months from ``start_date`` to ``end_date``.
    * Interest of an instalment = capital before it x nominal rate / 12, to the cent (half up); the last instalment clears
      the capital. The regular instalment is the annuity of the capital after the deferral over the remaining instalments.
    * Deferral (``deferral.months``): ``partial`` = interest only, the capital does not move; ``total`` = nothing is paid and
      the interest is added to the capital. The borrower insurance is paid during a deferral in both cases.
    * Insurance: ``insurance.monthly`` (flat), else ``insurance.rate_pct`` / 12 of the initial capital (``basis: initial``) or
      of the capital before each instalment (``basis: outstanding``). It is NOT part of the interest or the capital; the
      schedule shows it separately and the total debited (payment + insurance).
    * Variable / mixed rate: the schedule uses the current nominal rate for the whole term and is flagged ``approximate``.
    * Two modes. ``from_principal``: principal, nominal rate, start date and a term are known: the whole table. ``from_outstanding``:
      the capital still due on a date + the rate + (the end date or the instalment) are known: the FUTURE table only.
Nothing is invented: when neither mode is possible the result lists exactly the missing fields.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from coach.analytics.common import Result, add_months, money_str
from coach.skills import loans as L
from coach.skills.money import cents, dec, round_c

MODES = ("from_principal", "from_outstanding")


class _Lax:
    """A liability whose missing attributes read as None (a test double or an older object with fewer fields)."""

    def __init__(self, o):
        self._o = o

    def __getattr__(self, name):
        v = getattr(self._o, name, None)
        return [] if v is None and name in ("odometer", "documents") else v


def lax(lb):
    return lb if isinstance(lb, _Lax) else _Lax(lb)


@dataclass
class Instalment(Result):
    k: int
    due: dt.date
    kind: str                       # regular | deferral
    interest_c: int
    principal_c: int
    insurance_c: int
    payment_c: int                  # interest + principal actually paid (0 during a total deferral)
    total_c: int                    # payment + insurance: what leaves the account
    balance_c: int                  # capital still due after this instalment
    made: bool                      # due on or before today


@dataclass
class YearTotals(Result):
    year: int
    instalments: int
    interest_c: int
    principal_c: int
    insurance_c: int
    total_c: int
    partial: bool = False           # the part of the year before the first row of the table is not known (from_outstanding)


@dataclass
class LoanSchedule(Result):
    id: str
    kind: str
    status: str                     # computed | not_computable | not_applicable (a lease) | invalid
    mode: Optional[str] = None
    missing: list = field(default_factory=list)       # fields needed (mode from_principal) when not computable
    alternative: Optional[str] = None
    approximate: bool = False
    term_instalments: Optional[int] = None
    payment_c: Optional[int] = None                   # the regular instalment (without insurance)
    first_due: Optional[dt.date] = None
    last_due: Optional[dt.date] = None
    capital_c: Optional[int] = None                   # the capital the table starts from
    remaining_capital_c: Optional[int] = None         # today
    payments_made: int = 0
    remaining_instalments: int = 0
    interest_paid_c: Optional[int] = None
    insurance_paid_c: Optional[int] = None
    remaining_interest_c: Optional[int] = None
    total_interest_c: Optional[int] = None
    total_insurance_c: Optional[int] = None
    total_cost_c: Optional[int] = None                # interest + insurance over the whole table
    next_due: Optional[dt.date] = None
    by_year: list = field(default_factory=list)       # [YearTotals]
    rows: list = field(default_factory=list)          # [Instalment]
    assumptions: list = field(default_factory=list)
    payment_check: Optional[dict] = None
    outstanding_check: Optional[dict] = None
    source: Optional[str] = None                      # schedule (the table from the principal) | declared_rolled (the declared capital rolled forward) | declared


def due_date(first_due: dt.date, k: int, payment_day: Optional[int]) -> dt.date:
    """Instalment k counted from the date of the first one: first_due + (k - 1) months, on `payment_day` when given."""
    d = add_months(first_due, k - 1)
    if payment_day:
        import calendar
        d = d.replace(day=min(payment_day, calendar.monthrange(d.year, d.month)[1]))
    return d


def instalment_due(lb, k: int) -> dt.date:
    """Due date of instalment k of a liability: ``first_payment_date`` + (k - 1) months, else ``start_date`` + k months (so a loan that
    starts on the 31st is due on each month's last day, like :func:`coach.skills.loans.schedule`), on ``payment_day`` when given."""
    d = add_months(lb.first_payment_date, k - 1) if lb.first_payment_date else add_months(lb.start_date, k)
    if lb.payment_day:
        import calendar
        d = d.replace(day=min(lb.payment_day, calendar.monthrange(d.year, d.month)[1]))
    return d


def term_of(lb) -> Optional[int]:
    if lb.term_months:
        return lb.term_months
    if lb.start_date and lb.end_date:
        n = L.months_between(lb.start_date, lb.end_date)
        return n if n > 0 else None
    return None


def missing_for_table(lb) -> list[str]:
    out = []
    if lb.principal is None:
        out.append("principal")
    if lb.rate is None or lb.rate.nominal is None:
        out.append("rate.nominal")
    if lb.start_date is None:
        out.append("start_date")
    if term_of(lb) is None and not (lb.end_date and lb.start_date is None):      # an end date without a start date: start_date is listed
        out.append("end_date or term_months")
    return out


def insurance_c_for(lb, principal_c: int, balance_before_c: int) -> int:
    ins = lb.insurance
    if ins is None:
        return 0
    if ins.monthly is not None:
        return cents(ins.monthly)
    if ins.rate_pct is not None:
        base = balance_before_c if ins.basis == "outstanding" else principal_c
        return round_c(Decimal(base) * dec(ins.rate_pct) / Decimal(1200))
    return 0


def _years(rows: list[Instalment], partial_first: bool) -> list[YearTotals]:
    by: dict[int, list[int]] = {}
    for r in rows:
        a = by.setdefault(r.due.year, [0, 0, 0, 0])
        a[0] += 1
        a[1] += r.interest_c
        a[2] += r.principal_c
        a[3] += r.insurance_c
    first = min(by) if by else None
    return [YearTotals(y, v[0], v[1], v[2], v[3], v[1] + v[2] + v[3], partial_first and y == first and rows[0].due.month > 1)
            for y, v in sorted(by.items())]


def _payment_check(lb, regular_c: int, ins_c: int) -> Optional[dict]:
    if lb.monthly_payment is None:
        return None
    declared = cents(lb.monthly_payment)

    def close(a, b):
        return abs(a - b) <= max(100, int(0.01 * b))
    if close(declared, regular_c + ins_c):
        return {"status": "matches", "declared": money_str(declared), "computed_total": money_str(regular_c + ins_c)}
    if ins_c and close(declared, regular_c):
        return {"status": "matches_without_insurance", "declared": money_str(declared), "computed": money_str(regular_c)}
    return {"status": "differs", "declared": money_str(declared), "computed_total": money_str(regular_c + ins_c),
            "hint": "variable rate, modulated instalments, fees or a wrong figure: check the loan table"}


def _variable(lb) -> bool:
    return bool(lb.rate and lb.rate.type in ("variable", "mixed"))


def _finish(res: LoanSchedule, lb, rows: list[Instalment], today: dt.date, partial_first: bool) -> LoanSchedule:
    res.rows = rows
    res.first_due, res.last_due = rows[0].due, rows[-1].due
    made = [r for r in rows if r.made]
    res.payments_made = len(made)
    res.remaining_instalments = len(rows) - len(made)
    res.next_due = next((r.due for r in rows if not r.made), None)
    res.interest_paid_c = sum(r.interest_c for r in made)
    res.insurance_paid_c = sum(r.insurance_c for r in made)
    res.remaining_interest_c = sum(r.interest_c for r in rows if not r.made)
    res.total_interest_c = sum(r.interest_c for r in rows)
    res.total_insurance_c = sum(r.insurance_c for r in rows)
    res.total_cost_c = res.total_interest_c + res.total_insurance_c
    res.by_year = _years(rows, partial_first)
    res.approximate = _variable(lb)
    if res.approximate:
        res.assumptions.append("variable / mixed rate: the current nominal rate is applied to the whole term (the bank's table "
                               "will differ when the index moves)")
    return res


def compute(lb, today: dt.date) -> LoanSchedule:
    """The schedule of one memory liability, or the exact list of what is missing."""
    lb = lax(lb)
    res = LoanSchedule(lb.id, lb.kind, "not_computable")
    if lb.kind in ("loa", "lld"):
        res.status = "not_applicable"
        res.alternative = ("a lease has rents and a residual value, not an amortization table and no capital owed: see the end-of-contract "
                           "view (`coach loans lease`)")
        return res
    miss = missing_for_table(lb)
    rate = None if lb.rate is None or lb.rate.nominal is None else dec(lb.rate.nominal)
    if not miss:
        res = _from_principal(lb, res, rate, today)
        if res.status == "computed" and res.outstanding_check:
            return _declared_wins(lb, res, rate, today)
        return res
    res.missing = miss
    # the alternative: the capital still due on a date, the rate and an end date or an instalment
    if rate is not None and lb.outstanding is not None and lb.outstanding_as_of is not None and lb.outstanding > 0 \
            and (lb.end_date or lb.monthly_payment or (lb.start_date and term_of(lb))):
        out = _from_outstanding(lb, res, rate, today)
        if out is not None:
            return out
    elif lb.outstanding is not None and rate is not None:
        res.alternative = ("outstanding + outstanding_as_of + rate.nominal + (end_date or monthly_payment) give the FUTURE table: "
                           "missing " + ", ".join(x for x in ("outstanding_as_of", "end_date or monthly_payment")
                                                  if (x == "outstanding_as_of" and lb.outstanding_as_of is None)
                                                  or (x != "outstanding_as_of" and not (lb.end_date or lb.monthly_payment))))
    return res


def _declared_wins(lb, theory: LoanSchedule, rate: Decimal, today: dt.date) -> LoanSchedule:
    """The declared capital (from a statement, dated) differs from the theoretical table beyond the tolerance: an early repayment, a
    renegotiation or a figure of the file that is wrong. The declared figure is the fact: it is rolled forward (``declared_rolled``), or used
    as it is when that is impossible (``declared``); the theoretical table stays only as the check (``outstanding_check``)."""
    check = {**theory.outstanding_check, "theoretical_capital_today": money_str(theory.remaining_capital_c),
             "hint": "the declared capital wins over the theoretical table: was there an early repayment or a renegotiation? update the loan"}
    rolled = _from_outstanding(lb, LoanSchedule(lb.id, lb.kind, "not_computable"), rate, today) if lb.outstanding and lb.outstanding > 0 else None
    if rolled is not None:
        rolled.outstanding_check = check
        rolled.assumptions.insert(0, "the declared capital differs from the theoretical table: the declared figure is rolled forward from its date")
        return rolled
    theory.outstanding_check = check
    theory.source = "declared"
    theory.remaining_capital_c = cents(lb.outstanding)
    theory.assumptions.append("the declared capital differs from the table and cannot be rolled forward: the declared figure is used as it is; "
                              "the instalments listed are the theoretical ones")
    return theory


def _from_principal(lb, res: LoanSchedule, rate: Decimal, today: dt.date) -> LoanSchedule:
    n_total = term_of(lb)
    principal_c = cents(lb.principal)
    if principal_c <= 0:
        res.status, res.missing = "invalid", ["principal (must be positive)"]
        return res
    first_due = instalment_due(lb, 1)
    d = lb.deferral
    d_n = d.months if d else 0
    if d_n >= n_total:
        res.status, res.missing = "invalid", ["deferral (not shorter than the term)"]
        return res
    r = L.monthly_rate(rate)
    rows: list[Instalment] = []
    bal = principal_c
    for k in range(1, d_n + 1):
        interest = round_c(Decimal(bal) * r)
        ins = insurance_c_for(lb, principal_c, bal)
        due = instalment_due(lb, k)
        if d.kind == "partial":
            rows.append(Instalment(k, due, "deferral", interest, 0, ins, interest, interest + ins, bal, due <= today))
        else:
            bal += interest
            rows.append(Instalment(k, due, "deferral", interest, -interest, ins, 0, ins, bal, due <= today))
    n_am = n_total - d_n
    am = L.schedule(bal, rate, n_am)
    for row in am:
        k = d_n + row.k
        before = row.balance_c + row.principal_c
        ins = insurance_c_for(lb, principal_c, before)
        due = instalment_due(lb, k)
        rows.append(Instalment(k, due, "regular", row.interest_c, row.principal_c, ins, row.payment_c, row.payment_c + ins,
                               row.balance_c, due <= today))
    res.status, res.mode, res.missing, res.source = "computed", "from_principal", [], "schedule"
    res.term_instalments, res.capital_c = len(rows), principal_c
    res.payment_c = am[0].payment_c
    res.assumptions = ["annuity loan, interest = capital x nominal rate / 12 per instalment",
                       f"first instalment due {first_due}" + (f", debited on day {lb.payment_day}" if lb.payment_day else "")]
    if d:
        res.assumptions.append(f"{'interest-only' if d.kind == 'partial' else 'total'} deferral of {d.months} month(s): "
                               + ("the capital does not move" if d.kind == "partial" else "the interest is added to the capital")
                               + "; the insurance is still paid")
    made = [x for x in rows if x.made]
    res.remaining_capital_c = made[-1].balance_c if made else principal_c
    res.payment_check = _payment_check(lb, am[0].payment_c, rows[d_n].insurance_c)
    _finish(res, lb, rows, today, False)
    # declared capital against the table
    if lb.outstanding is not None and lb.outstanding_as_of is not None:
        at = [x for x in rows if x.due <= lb.outstanding_as_of]
        comp = at[-1].balance_c if at else principal_c
        decl = cents(lb.outstanding)
        if abs(decl - comp) > max(5000, int(0.02 * comp)):
            res.outstanding_check = {"status": "differs", "as_of": lb.outstanding_as_of.isoformat(), "declared": money_str(decl),
                                     "computed": money_str(comp),
                                     "hint": "the table differs from the declared capital: the rate, the term, a deferral or an "
                                             "early repayment may not be what the memory says"}
    return res


def _from_outstanding(lb, res: LoanSchedule, rate: Decimal, today: dt.date) -> Optional[LoanSchedule]:
    as_of = lb.outstanding_as_of
    bal = cents(lb.outstanding)
    day = lb.payment_day or (lb.first_payment_date or lb.start_date or lb.end_date or as_of).day
    anchor = as_of.replace(day=1)
    first_due = None
    for k in range(0, 3):                         # the first due date after the date of the capital
        c = due_date(add_months(anchor, k), 1, day)
        if c > as_of:
            first_due = c
            break
    ins_flat = cents(lb.insurance.monthly) if lb.insurance and lb.insurance.monthly is not None else 0
    notes = [f"the capital still due on {as_of} is the starting point; earlier instalments are not known",
             f"instalments assumed on day {day}" + ("" if lb.payment_day else " (payment_day is not recorded)")]
    end = lb.end_date
    if end is None and lb.start_date and term_of(lb):
        end = instalment_due(lb, term_of(lb))
    if end:
        n = 0
        while due_date(first_due, n + 1, day) <= end:
            n += 1
            if n > 600:
                return None
        if n <= 0:
            return None
        am = L.schedule(bal, rate, n)
    elif lb.monthly_payment:
        pay = cents(lb.monthly_payment) - ins_flat
        notes.append("end_date unknown: the term follows from monthly_payment" + (" minus the flat insurance" if ins_flat else
                     " (assumed to exclude insurance when the insurance amount is unknown)"))
        try:
            am = L.schedule_fixed_payment(bal, rate, pay)
        except ValueError:
            return None
    else:
        return None
    rows = []
    for row in am:
        due = due_date(first_due, row.k, day)
        before = row.balance_c + row.principal_c
        ins = insurance_c_for(lb, cents(lb.principal) if lb.principal is not None else bal, before)
        rows.append(Instalment(row.k, due, "regular", row.interest_c, row.principal_c, ins, row.payment_c, row.payment_c + ins,
                               row.balance_c, due <= today))
    res.status, res.mode, res.source = "computed", "from_outstanding", "declared_rolled"
    res.alternative = None
    res.term_instalments, res.capital_c, res.payment_c = len(rows), bal, am[0].payment_c
    res.assumptions = notes
    made = [x for x in rows if x.made]
    res.remaining_capital_c = made[-1].balance_c if made else bal
    res.payment_check = _payment_check(lb, am[0].payment_c, rows[0].insurance_c)
    _finish(res, lb, rows, today, True)
    # without the past, the whole-table totals are not known: only the future ones are
    res.interest_paid_c = res.insurance_paid_c = None
    res.total_interest_c = res.total_insurance_c = res.total_cost_c = None
    res.assumptions.append("totals and interest paid to date are unknown (the table starts at the declared capital); "
                           "remaining interest and the yearly figures of the future are exact")
    return res                                       # payments_made = the instalments due since the declared date: made + left = the rows


def months_left_instalments(lb, today: dt.date) -> int:
    """Whole months from today to the end date (a lease's remaining rents)."""
    return max(L.months_between(today, lb.end_date), 0) if lb.end_date else 0


def balance_on(sch: LoanSchedule, day: dt.date) -> Optional[int]:
    """Capital still due on `day` (after the instalments due on or before it), or None before the table / without one."""
    if sch.status != "computed" or not sch.rows:
        return None
    if day < sch.rows[0].due:
        return sch.capital_c             # before the first instalment of the table: the capital the table starts from
    last = None
    for r in sch.rows:
        if r.due <= day:
            last = r
        else:
            break
    return last.balance_c if last else sch.capital_c


def due_items(sch: LoanSchedule, start: dt.date, end: dt.date) -> list[Instalment]:
    """The instalments due in [start, end] (inclusive) when the schedule is computed."""
    if sch.status != "computed":
        return []
    return [r for r in sch.rows if start <= r.due <= end]


def interest_in_year(sch: LoanSchedule, year: int) -> Optional[dict]:
    for y in sch.by_year:
        if y.year == year:
            return {"year": year, "interest": money_str(y.interest_c), "insurance": money_str(y.insurance_c),
                    "principal": money_str(y.principal_c), "instalments": y.instalments, "partial": y.partial}
    return None
