"""Coverage-aware monthly averages per category (E4-1, with the coverage prerequisite of E4-2).

Rule (see :mod:`coach.analytics.coverage`): the average of a category is its total over the months fully covered by
EVERY account that ever carries the category, divided by the number of those months (at most the last
`average_window_months`). Never over months with no data.

* The run-rate figure excludes transactions tagged ``one_off`` / ``exclude_from_averages`` and ``capital``
  (an investment in an asset: shown separately, in ``excluded`` with their tags); the "with one-offs" figure keeps them.
* Spending is positive here; a refund / reimbursement in the category lowers it.
* ``saved``-tagged items (savings / investment) are never spending.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import CoverageInfo, Result, Scope, div_cents
from coach.analytics.coverage import last_n
from coach.analytics.dataset import CAPITAL_TAG, NO_AVERAGE_TAGS, Dataset, Tx, is_spending


@dataclass
class CategoryAverage(Result):
    category: str
    monthly_avg_c: int
    monthly_avg_with_one_offs_c: int
    total_c: int
    excluded_total_c: int
    n_months: int
    months: list = field(default_factory=list)
    accounts: list = field(default_factory=list)          # labels of the accounts whose coverage sets the window
    ignored_accounts: list = field(default_factory=list)  # minor carriers (< coverage_min_share of the money): not limiting
    lumpy: bool = False                                    # seasonal / lumpy category: needs >= lumpy_min_months
    low_confidence: bool = False

    @property
    def net_refund(self) -> bool:
        """Refunds exceed the spending of the window: money BACK, not a spending line (a negative monthly average)."""
        return self.total_c < 0


@dataclass
class AccountAverage(Result):
    account: str
    label: str
    monthly_avg_c: int
    n_months: int


@dataclass
class ExcludedItem(Result):
    tx_key: str
    date: dt.date
    amount_c: int
    category: str
    event: Optional[str]
    tags: list = field(default_factory=list)
    account: str = ""


@dataclass
class AveragesResult(Result):
    as_of: dt.date
    window_months: int
    household_monthly_avg_c: Optional[int]
    household_monthly_avg_with_one_offs_c: Optional[int]
    household_months: list
    household_by_account: list                             # [AccountAverage]: each account over its OWN covered months
    household_sum_c: Optional[int]                         # their sum: the alternative household estimate
    household_mismatch: bool                               # the two estimates differ by more than household_mismatch_pct
    categories: list                                       # [CategoryAverage], biggest first
    unavailable: list                                      # [{category, reason, total}] categories with no common covered month
    excluded: list                                         # [ExcludedItem] one_off / exclude_from_averages
    capital: list                                          # [ExcludedItem] capital items not already in `excluded`
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)           # tx keys of the excluded + capital items


def is_lumpy(cat: str, names: tuple) -> bool:
    return any(cat == n or (n.endswith(".") and cat.startswith(n)) for n in names)


def spending_txs(ds: Dataset, scope: Optional[Scope] = None) -> list[Tx]:
    """Spending categories, plus ``income.refund``: a refund that cannot be tied to a category is negative spending
    (the one rule everywhere: cash flow, averages, budgets' unallocated refunds)."""
    return [t for t in ds.txs_in(scope) if (is_spending(t.category) or t.category == "income.refund") and not t.is_saved]


def category_averages(ds: Dataset, scope: Optional[Scope] = None, window: Optional[int] = None) -> AveragesResult:
    s = ds.settings
    window = window or s.average_window_months
    sp = spending_txs(ds, scope)
    by_cat: dict[str, list[Tx]] = {}
    for t in sp:
        by_cat.setdefault(t.category, []).append(t)
    rows, unavailable = [], []
    used_accounts: set[str] = set()
    for cat in sorted(by_cat):
        carriers, minor = ds.carriers_split(cat, scope)
        lumpy = is_lumpy(cat, s.lumpy_categories)
        months = last_n(ds.coverage.common_months(carriers), max(window, s.lumpy_min_months) if lumpy else window)
        txs = by_cat[cat]
        if not months:
            unavailable.append({"category": cat, "reason": "no month fully covered by all accounts carrying it",
                                "accounts": [ds.label(u) for u in carriers], "n_tx": len(txs)})
            continue
        ms = set(months)
        in_m = [t for t in txs if t.month in ms]
        run = -sum(t.amount_c for t in in_m if not t.is_one_off)
        allv = -sum(t.amount_c for t in in_m)
        n = len(months)
        need = s.lumpy_min_months if lumpy else s.average_min_months
        rows.append(CategoryAverage(cat, div_cents(run, n), div_cents(allv, n), run, allv - run, n, months,
                                    [ds.label(u) for u in carriers], [ds.label(u) for u in minor], lumpy, n < need))
        used_accounts.update(carriers)
    rows.sort(key=lambda r: (-r.monthly_avg_c, r.category))
    # household figure: months covered by every account that carries any spending
    carriers = sorted({t.account for t in sp})
    hh_months = last_n(ds.coverage.common_months(carriers), window)
    hh_run = hh_all = None
    if hh_months:
        ms = set(hh_months)
        in_m = [t for t in sp if t.month in ms]
        hh_run = div_cents(-sum(t.amount_c for t in in_m if not t.is_one_off), len(hh_months))
        hh_all = div_cents(-sum(t.amount_c for t in in_m), len(hh_months))
    # alternative household estimate: every account over its OWN covered months, then summed
    by_acc, total_alt = [], 0
    for u in sorted({t.account for t in sp}):
        ms_u = last_n(sorted(ds.coverage.covered(u)), window)
        if not ms_u:
            continue
        mset = set(ms_u)
        run_u = -sum(t.amount_c for t in sp if t.account == u and t.month in mset and not t.is_one_off)
        by_acc.append(AccountAverage(u, ds.label(u), div_cents(run_u, len(ms_u)), len(ms_u)))
        total_alt += by_acc[-1].monthly_avg_c
    mismatch = bool(hh_run is not None and by_acc and abs(total_alt - hh_run) > s.household_mismatch_pct * max(abs(hh_run), 1))
    excluded = [_item(t) for t in sp if t.tags & NO_AVERAGE_TAGS]
    capital = [_item(t) for t in sp if CAPITAL_TAG in t.tags and not t.tags & NO_AVERAGE_TAGS]
    notes = [f"{len(ds.foreign)} non-EUR transaction(s) left out"] if ds.foreign else []
    notes += [f"memory: {w}" for w in ds.memory.warnings[:3]]
    if mismatch:
        notes.append(f"the household estimate ({hh_run / 100:.2f}) and the sum of per-account averages ({total_alt / 100:.2f}) "
                     f"differ by more than {s.household_mismatch_pct:.0%}: the accounts' histories do not overlap enough")
    cov = ds.coverage.info(used_accounts | set(carriers), hh_months,
                           "per category: months covered by every account that carries the category; "
                           "household: months covered by every account that carries any spending", notes)
    return AveragesResult(ds.today, window, hh_run, hh_all, hh_months, by_acc, total_alt if by_acc else None, mismatch, rows, unavailable, excluded, capital,
                          (scope or Scope()).describe(), cov, sorted({e.tx_key for e in excluded + capital}))


def _item(t: Tx) -> ExcludedItem:
    return ExcludedItem(t.key, t.date, t.amount_c, t.category, t.event, sorted(t.tags), t.account)


def monthly_matrix(txs: list[Tx]) -> dict[str, dict[str, int]]:
    """category -> month -> spending in cents (positive = money out), ALL spending tx (one-offs included)."""
    out: dict[str, dict[str, int]] = {}
    for t in txs:
        row = out.setdefault(t.category, {})
        row[t.month] = row.get(t.month, 0) - t.amount_c
    return out
