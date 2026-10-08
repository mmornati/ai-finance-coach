"""Income vs spending and savings rate per month (E4-2).

Definitions (all amounts positive magnitudes, ``net`` signed; see :mod:`coach.analytics.common` for the money format)
    * income      = transactions in ``income.*`` (salary, rental, bonus, interest, gift, other) EXCEPT ``income.refund``.
    * refunds     = positive amounts in a spending category (card refunds, reimbursements netted against the category,
                    E2-14) + ``income.refund``. They are NEGATIVE SPENDING, never income.
    * spending    = gross outflows in spending categories minus refunds. Spending categories are every category except
                    ``transfer.*`` and ``income.*``: internal transfers (own accounts, children's accounts, savings
                    pockets) and transfers to / from people are never spending nor income; their totals are listed
                    under ``transfers`` so nothing disappears silently.
    * saved       = net outflow of transactions tagged ``savings`` / ``investment`` (e.g. a monthly contribution to a
                    life-insurance savings plan). They are not spending, whatever their category; a withdrawal tagged
                    the same way lowers `saved`.
    * net         = income - spending (the money that was neither spent nor refunded: it includes `saved`).
    * savings_rate = net / income (None when income is 0). saved_rate = saved / income.
    * One-offs are INCLUDED in `spending` (it is the real cash flow); `one_off_spending` shows the part tagged
      one_off / exclude_from_averages / capital, and `spending_ex_one_offs` the rest.
Context lines (they do not change the figures above):
    * ``debt_service``  instalments of the memory liabilities (payments matched by ``payment_match``). ``loan_principal`` is
      the principal part of those instalments: from the loan's amortization schedule (E9-3, the exact split of the instalment
      due nearest to the payment) when it is computable, else the estimate instalment - outstanding x nominal rate / 12 when
      the rate, the outstanding capital and the instalment are known; null when a loan paid that month has neither.
      ``savings_rate_incl_principal`` = (net + principal) / income: what the household really put into its wealth.
    * ``drawn_unconnected`` / ``sent_unconnected``  internal transfers whose other leg is not in a connected account (no
      same-amount counterpart on another account within 3 days): money drawn from, or sent to, accounts the coach does
      not see. A large "drawn" line means the month's spending was partly financed from elsewhere.
A month is ``complete`` when every account in the scope has data for the whole month (account coverage model); the
totals are given over complete months (`totals_complete`) and over all listed months (`totals_all`).
Breakdowns: ``by_purpose`` (account purpose: main, cards, rental, kids, savings...) and ``by_owner`` (joint / member id)
are the same figures restricted to those accounts; the household figure is the sum of everything.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import (to_cents, CoverageInfo, Result, Scope, add_months_key, last_closed_month, month_key,
                                    months_between, pct)
from coach.analytics.common import non_eur_note, note
from coach.analytics.dataset import Dataset, Tx, is_income, is_spending, is_transfer
from coach.loans import service as loans_service


@dataclass
class TransferFlows(Result):
    internal_in_c: int = 0
    internal_out_c: int = 0
    people_in_c: int = 0
    people_out_c: int = 0


@dataclass
class MonthFlow(Result):
    month: str
    income_c: int
    refunds_c: int
    spending_gross_c: int
    spending_c: int
    one_off_spending_c: int
    spending_ex_one_offs_c: int
    saved_c: int
    net_c: int
    savings_rate: Optional[float]
    saved_rate: Optional[float]
    uncategorized_c: int
    transfers: TransferFlows
    n_tx: int
    complete: bool
    missing_accounts: list = field(default_factory=list)
    debt_service_c: int = 0                         # loan instalments (payments matched by a liability's payment_match)
    loan_principal_c: Optional[int] = None          # estimated principal part of those instalments (None: not estimable)
    savings_rate_incl_principal: Optional[float] = None   # (net + principal) / income, only when every instalment is estimable
    drawn_unconnected_c: int = 0                    # internal inflows whose other leg is not in a connected account
    sent_unconnected_c: int = 0                     # internal outflows to an account that is not connected


@dataclass
class FlowTotals(Result):
    n_months: int
    income_c: int
    refunds_c: int
    spending_c: int
    one_off_spending_c: int
    spending_ex_one_offs_c: int
    saved_c: int
    net_c: int
    savings_rate: Optional[float]
    saved_rate: Optional[float]
    debt_service_c: int = 0
    loan_principal_c: Optional[int] = None
    savings_rate_incl_principal: Optional[float] = None
    drawn_unconnected_c: int = 0
    sent_unconnected_c: int = 0


@dataclass
class FlowBlock(Result):
    accounts: list                                  # labels
    months: list                                    # [MonthFlow]
    totals_complete: Optional[FlowTotals]
    totals_all: Optional[FlowTotals]


@dataclass
class CashflowResult(Result):
    as_of: object
    scope: dict
    household: FlowBlock
    by_purpose: dict
    by_owner: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)    # tx keys of the income, saved and one-off items in the period


def _loan_matchers(ds: Dataset) -> list:
    """[(liability, compiled payment_match, principal part of one instalment or None)]. principal = instalment - interest,
    interest = outstanding x nominal rate / 12 (a constant approximation around the `outstanding` figure of the memory);
    None when the rate, the outstanding capital or the instalment is unknown."""
    out = []
    for _rel, lb in ds.memory.liabilities:
        if not lb.payment_match:
            continue
        try:
            rx = re.compile(lb.payment_match, re.I)
        except re.error:
            continue
        part = None
        nominal = lb.rate.nominal if lb.rate else None
        if lb.monthly_payment and lb.outstanding and nominal is not None:
            part = max(0, to_cents(lb.monthly_payment) - to_cents(lb.outstanding * nominal / 1200))
        sch = loans_service.schedule_of(ds, lb)               # E9-3: the exact split of each instalment when computable
        out.append((lb, rx, part, sch if sch.status == "computed" else None))
    return out


def _scheduled_part(sch, day: dt.date) -> Optional[int]:
    """Principal part of the schedule's instalment nearest to `day` (within 20 days), else None."""
    best = min(sch.rows, key=lambda r: abs((r.due - day).days), default=None)
    if best is None or abs((best.due - day).days) > 20:
        return None
    return max(0, best.principal_c)


def unconnected_internal(ds: Dataset, window_days: int = 3) -> set:
    """Keys of internal-transfer legs with no counterpart (same amount, other account, within the window) in the
    connected accounts: money drawn from / sent to accounts the coach does not see (a savings book, a parent's account)."""
    got = ds._cache.get("unconnected")
    if got is None:
        legs = [t for t in ds.whole if t.category == "transfer.internal" and not t.is_saved and not t.is_one_off]
        # counterparts are looked for among ALL transfer legs of the connected accounts, whatever their category: the
        # other side of an own-account transfer is often classified transfer.to_people / from_people
        pool = [t for t in ds.whole if t.category.startswith("transfer.") and not t.is_saved]
        used: set = set()
        matched: set = set()
        for o in sorted(legs, key=lambda t: (t.date, t.key)):
            cands = [c for c in pool if c.key not in used and c.key != o.key and c.account != o.account
                     and c.amount_c == -o.amount_c and abs((c.date - o.date).days) <= window_days]
            if cands:
                c = min(cands, key=lambda c: (abs((c.date - o.date).days), c.date, c.key))     # the nearest date wins
                used.add(c.key)
                used.add(o.key)
                matched.update((o.key, c.key))
        got = {t.key for t in legs} - matched
        ds._cache["unconnected"] = got
    return got


def _month_flow(month: str, txs: list[Tx], complete: bool, missing: list, loans: Optional[list] = None,
                unconnected: frozenset = frozenset()) -> MonthFlow:
    income = refunds = gross = one_off = saved = unc = 0
    tr = TransferFlows()
    for t in txs:
        a, c = t.amount_c, t.category
        if t.is_saved:
            saved += -a
        elif is_transfer(c):
            internal = c == "transfer.internal"
            if a >= 0:
                if internal:
                    tr.internal_in_c += a
                else:
                    tr.people_in_c += a
            elif internal:
                tr.internal_out_c += -a
            else:
                tr.people_out_c += -a
        elif c == "income.refund":
            if a >= 0:
                refunds += a
            else:
                gross += -a
        elif is_income(c):
            income += a
        else:                                              # a spending category
            if a < 0:
                gross += -a
            else:
                refunds += a
            if t.is_one_off:
                one_off += -a
            if c == "other.uncategorized":
                unc += -a
    spending = gross - refunds
    net = income - spending
    debt = principal = 0
    estimable = True
    for t in txs:
        if t.amount_c >= 0 or t.is_saved:
            continue
        for lb, rx, part, sch in loans or []:
            if rx.search(t.mkey or "") or rx.search(t.desc or "") or rx.search(t.entity or ""):
                debt += -t.amount_c
                exact = _scheduled_part(sch, t.date) if sch is not None else None
                if exact is not None:
                    principal += exact
                elif part is None:
                    estimable = False
                elif lb.monthly_payment and abs(-t.amount_c - to_cents(lb.monthly_payment)) <= 0.15 * to_cents(lb.monthly_payment):
                    principal += part
                break
    drawn = sum(t.amount_c for t in txs if t.key in unconnected and t.amount_c > 0)
    sent = -sum(t.amount_c for t in txs if t.key in unconnected and t.amount_c < 0)
    return MonthFlow(month, income, refunds, gross, spending, one_off, spending - one_off, saved, net,
                     pct(net, income), pct(saved, income), unc, tr, len(txs), complete, missing, debt,
                     principal if debt and estimable else None,
                     pct(net + principal, income) if debt and estimable else None, drawn, sent)


def _totals(rows: list[MonthFlow]) -> Optional[FlowTotals]:
    if not rows:
        return None
    inc = sum(r.income_c for r in rows)
    sav = sum(r.saved_c for r in rows)
    sp = sum(r.spending_c for r in rows)
    oo = sum(r.one_off_spending_c for r in rows)
    net = inc - sp
    debt = sum(r.debt_service_c for r in rows)
    est = debt and all(r.loan_principal_c is not None for r in rows if r.debt_service_c)
    princ = sum(r.loan_principal_c or 0 for r in rows) if est else None
    return FlowTotals(len(rows), inc, sum(r.refunds_c for r in rows), sp, oo, sp - oo, sav, net, pct(net, inc),
                      pct(sav, inc), debt, princ, pct(net + princ, inc) if est else None,
                      sum(r.drawn_unconnected_c for r in rows), sum(r.sent_unconnected_c for r in rows))


def _block(ds: Dataset, uids: list[str], months: list[str]) -> FlowBlock:
    ok = set(uids)
    loans = _loan_matchers(ds)
    unconnected = frozenset(unconnected_internal(ds))
    needed = [u for u in uids if u in set(ds.flow_accounts())]
    by_month: dict[str, list[Tx]] = {}
    for t in ds.txs:
        if t.account in ok:
            by_month.setdefault(t.month, []).append(t)
    rows = []
    for m in months:
        missing = [ds.label(u) for u in needed if m not in ds.coverage.covered(u)]
        rows.append(_month_flow(m, by_month.get(m, []), not missing, missing, loans, unconnected))
    return FlowBlock([ds.label(u) for u in uids], rows, _totals([r for r in rows if r.complete]), _totals(rows))


def cashflow(ds: Dataset, scope: Optional[Scope] = None, months: int = 6, end: Optional[str] = None,
             include_current: bool = False, breakdown: bool = True) -> CashflowResult:
    """The last `months` closed months (ending at `end`, default the last closed month) of income / spending / saved.
    Months before the first data of the scope are not listed."""
    scope = scope or Scope()
    uids = ds.uids_in(scope)
    firsts = [ds.coverage.of(u).first for u in uids if ds.coverage.of(u).first]
    last_m = month_key(ds.today) if include_current else (end or last_closed_month(ds.today))
    first_m = add_months_key(last_m, -(months - 1))
    if firsts:
        first_m = max(first_m, month_key(min(firsts)))
    month_list = months_between(first_m, last_m) if firsts and first_m <= last_m else []
    hh = _block(ds, uids, month_list)
    by_purpose, by_owner = {}, {}
    if breakdown:
        for p in sorted({a.purpose or "unset" for a in ds.accounts_in(scope)}):
            by_purpose[p] = _block(ds, [a.uid for a in ds.accounts_in(scope) if (a.purpose or "unset") == p], month_list)
        for o in sorted({a.owner or "unset" for a in ds.accounts_in(scope)}):
            by_owner[o] = _block(ds, [a.uid for a in ds.accounts_in(scope) if (a.owner or "unset") == o], month_list)
    ms = set(month_list)
    ev = sorted({t.key for t in ds.txs_in(scope) if t.month in ms and (is_income(t.category) or t.is_saved
                                                                      or (is_spending(t.category) and t.is_one_off))})
    complete = [r.month for r in hh.months if r.complete]
    notes = []
    if ds.foreign:
        notes.append(non_eur_note(len(ds.foreign)))
    incomplete = [r for r in hh.months if not r.complete]
    if incomplete:
        text = "incomplete months: " + ", ".join(f"{r.month} (no full data for {', '.join(r.missing_accounts)})" for r in incomplete)
        # the web says it shorter: the first and last incomplete months, and every account lacking data (labels, not translated)
        lacking = sorted({lab for r in incomplete for lab in r.missing_accounts})
        notes.append(note("coverage.incompleteMonths", text, count=len(incomplete), first_month=incomplete[0].month,
                          last_month=incomplete[-1].month, accounts=", ".join(lacking)))
    cov = ds.coverage.info(uids, complete, "a month is complete when every account of the scope covers all its days; "
                           "totals_complete sums complete months only, totals_all every listed month", notes)
    cov.skipped_months = [r.month for r in incomplete]
    return CashflowResult(ds.today, scope.describe(), hh, by_purpose, by_owner, cov, ev)
