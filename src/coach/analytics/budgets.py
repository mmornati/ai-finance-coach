"""Budgets (E4-7): envelopes stored in ``memory/budgets.yaml``, suggestions and monthly progress.

The file is memory: it is written only through the memory store (validated, atomic, recorded in the history with
``--source``) by `coach budget set`, after a preview; the coach / LLM can only PROPOSE (`--propose`).

Suggestions
    For each spending category: the MEDIAN of the monthly net spend (refunds netted, one-offs / capital left out) over
    the last `budget_suggest_months` (6) months fully covered by every account that carries the category (coverage
    model), rounded to the nearest 5 EUR (< 100), 10 EUR (< 1,000) or 50 EUR. Categories whose median is under 10 EUR
    are skipped; fewer than `average_min_months` covered months = ``low_confidence``.

Progress of the current month (``budget_status``, `as_of` = today)
    spent      = net spend of the budget's categories in the month, on the accounts the budget applies to
                 (owner / account filters), one-offs, capital and savings left out (shown in `excluded`).
    available  = monthly + carry-over. With ``rollover: true`` the carry-over is the unspent part of each earlier
                 month since `start` (at most 12 months, only months not overspent count; overspending is not
                 carried), so an under-used month raises the next envelope. A rollover budget without `start` carries
                 nothing (`coach budget set --rollover` writes the start of the current month).
    remaining  = available - spent;  percent_used = spent / available.
    projected  = spent + recurring payments of the categories still expected before month end + the pace of the
                 NON-recurring spending so far (spent excluding recurring payments already made, per elapsed day)
                 over the remaining days. Without recurring information, or in the first 3 days, the plain linear
                 pace spent / elapsed x days is used (``method``).
    status     = ``over`` (spent > available), ``at_risk`` (projected > available) or ``ok``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import (CoverageInfo, Result, Scope, add_months_key, days_in_month, div_cents, median_c,
                                    month_end, month_key, month_start, months_between, pct, round_to)
from coach.analytics.coverage import last_n
from coach.analytics.dataset import Dataset, Tx, is_spending
from coach.analytics.recurring import RecurringResult, detect_recurring, occurrences


def suggestion_step(c: int) -> int:
    return 500 if c < 10000 else 1000 if c < 100000 else 5000


@dataclass
class BudgetSuggestion(Result):
    category: str
    suggested_c: int
    median_c: int
    mean_c: int
    n_months: int
    months: list = field(default_factory=list)
    accounts: list = field(default_factory=list)
    existing_c: Optional[int] = None       # monthly amount of a budget already set for this category
    low_confidence: bool = False


@dataclass
class SuggestionsResult(Result):
    as_of: dt.date
    suggestions: list                      # biggest first
    skipped: list                          # [{category, reason}]
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)


@dataclass
class BudgetProgress(Result):
    id: str
    target: str                            # category or 'group:<g>'
    month: str
    monthly_c: int
    carry_c: int
    available_c: int
    spent_c: int
    excluded_c: int                        # one-off / capital spend left out
    remaining_c: int
    percent_used: Optional[float]
    projected_c: int
    projected_percent: Optional[float]
    method: str                            # pace | pace+recurring
    status: str                            # ok | at_risk | over
    rollover: bool
    owner: Optional[str]
    account: Optional[str]
    flags: list = field(default_factory=list)
    evidence: list = field(default_factory=list)


@dataclass
class BudgetStatusResult(Result):
    as_of: dt.date
    month: str
    budgets: list                          # [BudgetProgress]
    counts: dict
    unbudgeted: list                       # [{category, spent}] top spending categories this month with no budget
    coverage: CoverageInfo
    unallocated_refunds_c: int = 0         # income.refund of the month: negative spending that belongs to no budget
    warnings: list = field(default_factory=list)   # invalid budget entries: valid ones are still shown
    evidence: list = field(default_factory=list)


def _problem_lines(problems: list, what: str) -> list[str]:
    return [f"{what} {p} -- left out; run `coach memory check`" for p in problems]


# ---------------------------------------------------------------- suggestions

def suggest_budgets(ds: Dataset, months: Optional[int] = None, scope: Optional[Scope] = None,
                    limit: Optional[int] = None) -> SuggestionsResult:
    s = ds.settings
    n = months or s.budget_suggest_months
    by_cat: dict[str, list[Tx]] = {}
    for t in ds.txs_in(scope):
        if is_spending(t.category) and not t.is_saved and not t.is_one_off:
            by_cat.setdefault(t.category, []).append(t)
    existing = {b.category: b.monthly for b in ds.memory.budgets if b.category and not b.owner and not b.account}
    rows, skipped = [], []
    used: set[str] = set()
    for cat in sorted(by_cat):
        if cat == "other.uncategorized":
            skipped.append({"category": cat, "reason": "not a category: classify these transactions first"})
            continue
        carriers = ds.carrying_accounts(cat, scope)
        ms = last_n(ds.coverage.common_months(carriers), n)
        if not ms:
            skipped.append({"category": cat, "reason": "no month fully covered by all accounts carrying it"})
            continue
        per = {m: 0 for m in ms}
        for t in by_cat[cat]:
            if t.month in per:
                per[t.month] -= t.amount_c
        vals = [per[m] for m in ms]
        med = median_c(vals)
        if med < 1000:
            skipped.append({"category": cat, "reason": f"median {med / 100:.2f} EUR per month is below 10 EUR"})
            continue
        sug = round_to(med, suggestion_step(med))
        ex = existing.get(cat)
        rows.append(BudgetSuggestion(cat, sug, med, div_cents(sum(vals), len(vals)), len(ms), ms,
                                     [ds.label(u) for u in carriers], int(round(ex * 100)) if ex else None,
                                     len(ms) < s.average_min_months))
        used.update(carriers)
    rows.sort(key=lambda r: (-r.suggested_c, r.category))
    if limit:
        rows = rows[:limit]
    cov = ds.coverage.info(used, last_n(sorted({m for r in rows for m in r.months}), n),
                           "median of the last N months covered by every account carrying the category",
                           ["one-offs, capital and savings left out"])
    return SuggestionsResult(ds.today, rows, skipped, (scope or Scope()).describe(), cov)


# ---------------------------------------------------------------- status

def _budget_scope(b) -> Scope:
    return Scope.make(owners=[b.owner] if b.owner else None, accounts=[b.account] if b.account else None)


def _matches(b, category: str) -> bool:
    return category == b.category if b.category else category.split(".")[0] == b.group


def _month_spend(ds: Dataset, b, month: str, uids: set) -> tuple[int, int, list[Tx]]:
    spent = excl = 0
    txs = []
    for t in ds.txs:
        if t.month != month or t.account not in uids or not is_spending(t.category) or t.is_saved \
                or not _matches(b, t.category):
            continue
        if t.is_one_off:
            excl -= t.amount_c
        else:
            spent -= t.amount_c
            txs.append(t)
    return spent, excl, txs


def budget_status(ds: Dataset, as_of: Optional[dt.date] = None, recurring: Optional[RecurringResult] = None,
                  budgets: Optional[list] = None) -> BudgetStatusResult:
    as_of = as_of or ds.today
    month = month_key(as_of)
    elapsed = as_of.day
    dim = days_in_month(month)
    rec = recurring
    if rec is None and ds.memory.budgets:
        rec = detect_recurring(ds)
    out: list[BudgetProgress] = []
    all_uids: set[str] = set()
    for b in (budgets if budgets is not None else ds.memory.budgets):
        start_m = month_key(b.start) if b.start else None
        if start_m and start_m > month:
            continue
        uids = set(ds.uids_in(_budget_scope(b)))
        all_uids |= uids
        flags = []
        if not uids:
            flags.append("no_matching_account")
        monthly = int(round(b.monthly * 100))
        spent, excl, txs = _month_spend(ds, b, month, uids)
        carry = 0
        if b.rollover and start_m:                         # no start date = nothing to carry (the budget did not exist before)
            first_m = max(start_m, add_months_key(month, -12))
            # only months the contributing accounts cover count: a month without data is not an unspent month
            carriers = sorted({t.account for t in ds.txs if t.account in uids and _matches(b, t.category)})
            usable = set(ds.coverage.common_months(carriers))
            last_m = add_months_key(month, -1)
            for m in months_between(first_m, last_m) if first_m <= last_m else []:
                if m in usable:
                    sp, _e, _t = _month_spend(ds, b, m, uids)
                    carry += max(0, monthly - sp)
        available = monthly + carry
        remaining = available - spent
        # projection
        method = "pace"
        proj = div_cents(spent * dim, elapsed) if elapsed else spent
        if rec is not None and elapsed >= 3:
            later = 0
            paid = 0
            for x in rec.series:
                if x.kind != "expense" or x.account not in uids or not _matches(b, x.category):
                    continue
                paid += sum(-o.amount_c for o in x.occurrences if month_key(o.date) == month)
                if x.status == "active" and x.next_expected:
                    later += sum(-x.expected_amount_c for dd, _ov in occurrences(x, as_of + dt.timedelta(days=1),
                                                                                  month_end(month)))
            non_rec = max(0, spent - paid)
            proj = spent + later + div_cents(non_rec * (dim - elapsed), elapsed)
            method = "pace+recurring"
        status = "over" if spent > available else "at_risk" if proj > available else "ok"
        last_tx = max((t.date for t in ds.txs if t.account in uids), default=None)
        if last_tx is not None and (as_of - last_tx).days > 10:
            flags.append("stale_data")
        if b.rollover and carry:
            flags.append("includes_carry_over")
        out.append(BudgetProgress(b.id, b.category or f"group:{b.group}", month, monthly, carry, available, spent, excl,
                                  remaining, pct(spent, available), proj, pct(proj, available), method, status,
                                  b.rollover, b.owner, b.account, flags,
                                  [t.key for t in sorted(txs, key=lambda t: (t.amount_c, t.key))[:10]]))
    out.sort(key=lambda p: (["over", "at_risk", "ok"].index(p.status), -(p.percent_used or 0), p.id))
    counts = {k: sum(1 for p in out if p.status == k) for k in ("over", "at_risk", "ok")}
    budgeted = [b for b in (budgets if budgets is not None else ds.memory.budgets)]
    month_spend: dict[str, int] = {}
    for t in ds.txs:
        if t.month == month and is_spending(t.category) and not t.is_saved and not t.is_one_off:
            month_spend[t.category] = month_spend.get(t.category, 0) - t.amount_c
    unbudgeted = [{"category": c, "spent_c": v} for c, v in sorted(month_spend.items(), key=lambda x: (-x[1], x[0]))
                  if v > 0 and not any(_matches(b, c) for b in budgeted)][:10]
    for u in unbudgeted:
        u["spent"] = f"{u['spent_c'] // 100}.{u['spent_c'] % 100:02d}"
        del u["spent_c"]
    cov = ds.coverage.info(all_uids, [], f"month to date: {month_start(month)} to {as_of}",
                           [] if ds.memory.budgets else ["no budgets set: see `coach budget suggest`"])
    refunds = sum(t.amount_c for t in ds.txs if t.month == month and t.category == "income.refund" and t.amount_c > 0)
    return BudgetStatusResult(as_of, month, out, counts, unbudgeted, cov, refunds, warnings=_problem_lines(ds.memory.budget_problems, "budget"),
                              evidence=sorted({k for p in out for k in p.evidence}))


# ---------------------------------------------------------------- writing (via the memory store)

def budget_id_for(target: str, existing: set) -> str:
    base = ("group-" + target.removeprefix("group:") if target.startswith("group:") or "." not in target
            else target.replace(".", "-").replace("_", "-"))
    cand, n = base, 1
    while cand in existing:
        n += 1
        cand = f"{base}-{n}"
    return cand


def budget_ops(existing_ids: set, file_exists: bool, bid: str, fields: dict) -> list[dict]:
    """Edit operations for the memory store: update the fields of an existing budget or append a new one."""
    if bid in existing_ids:
        return [{"op": "set", "path": f"budgets[{bid}].{k}", "value": v} for k, v in fields.items()]
    value = {"id": bid, **fields}
    ops = [{"op": "append", "path": "budgets", "value": value}]
    return ([{"op": "create", "value": {"budgets": []}}] if not file_exists else []) + ops
