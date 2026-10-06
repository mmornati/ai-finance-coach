"""Cash-flow forecast, 30 / 60 / 90 days, per account and for the household (E4-6).

Model (deterministic, documented so the coach can quote it):
    balance(day) = latest booked balance
                   + the expected recurring items due until that day        (income, debits, internal transfers, savings plans)
                   + loan instalments from memory/liabilities not already seen as a recurring series
                   - the variable spend, spread evenly over the days.
* Start: the latest balance snapshot of the account (type preference CLBD > ITBD > XPCD > ITAV...). If the snapshot is
  older than `forecast_balance_stale_days` it is flagged and the days since the snapshot are projected too.
* Recurring items: every ACTIVE series of :mod:`coach.analytics.recurring` is replayed at its cadence from its next
  expected date (anchored on that date, so a monthly payment keeps its day of the month), with its expected amount.
  A series that is already overdue is placed on the first projected day. Internal transfers between own accounts are
  series like any other, one on each account: they cancel at household level when both legs are detected; a leg
  towards an account the coach does not see (e.g. a life-insurance plan) is a real outflow at household level.
* Liabilities: a memory liability with no recurring series linked to it (via payment_match) is added from its amortization
  schedule (E9-3: exact due dates and amounts, insurance included, ``scheduled``) when it is computable, else as a monthly
  debit of `monthly_payment` on the day of `start_date` (or `payment_day`, or the 1st) until `end_date` (``assumed``), on the
  account named in `debited_account` / `debited_from` (unresolved = household only).
* Variable spend: the average monthly net outflow NOT covered by a recurring series - spending categories plus the
  irregular transfers (own accounts: top-ups, people; so an account that is regularly refilled by hand is not shown
  as draining) - with one-offs, capital and savings tags left out, over the last `forecast_variable_months` months
  fully covered by that account, spread evenly per day. Non-recurring income is NOT projected (conservative). No covered month = no variable spend + a flag.
* Confidence band: expected -/+ z x sigma where sigma is the standard deviation of the monthly variable spend scaled
  by sqrt(days / 30.44) (z = `forecast_band_z`, default 1.28 = about 80 %), widened by the amount range (min / max of
  the last 6 payments) of each variable-amount series. Household sigma adds the accounts' variances (independence).
* Accounts with purpose 'savings' get no variable flows (their movements are deliberate).
* Flags: ``projected_negative`` (expected balance < 0 at some day), ``at_risk`` (only the low band < 0),
  ``balance_stale``, ``no_balance`` (the account has no balance snapshot: events listed, no projection),
  ``no_variable_history``.
All amounts in cents (see :mod:`coach.analytics.common`); balances are signed.
"""
from __future__ import annotations

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from coach.analytics.common import CoverageInfo, Result, Scope, add_months, median_c
from coach.analytics.coverage import last_n
from coach.analytics.dataset import Dataset, is_spending, is_transfer
from coach.analytics.recurring import RecurringResult, detect_recurring, occurrences

DAYS_PER_MONTH = 30.4375


@dataclass
class ForecastEvent(Result):
    date: date
    account: Optional[str]
    account_label: str
    label: str
    amount_c: int                  # signed
    low_c: int                     # range of the amount, signed (same as amount for a fixed one)
    high_c: int
    source: str                    # recurring | liability
    ref: str                       # series id or liability id
    certainty: str                 # observed | assumed
    overdue: bool = False


@dataclass
class ForecastPoint(Result):
    date: date
    balance_c: int
    low_c: int
    high_c: int


@dataclass
class Milestone(Result):
    days: int
    date: date
    balance_c: int
    low_c: int
    high_c: int


@dataclass
class AccountForecast(Result):
    account: Optional[str]
    label: str
    purpose: Optional[str]
    owner: Optional[str]
    start_balance_c: Optional[int]
    start_date: Optional[date]
    balance_type: Optional[str]
    variable_monthly_c: int        # expected variable spend per month (positive)
    variable_sigma_c: int
    milestones: list               # [Milestone] at 30 / 60 / 90 (those within the horizon)
    min_balance_c: Optional[int]
    min_date: Optional[date]
    first_negative: Optional[date]
    first_at_risk: Optional[date]
    flags: list = field(default_factory=list)
    points: list = field(default_factory=list)      # [ForecastPoint] one per day
    events: list = field(default_factory=list)      # [ForecastEvent]


@dataclass
class ForecastResult(Result):
    as_of: date
    horizon_days: int
    household: AccountForecast
    accounts: list                 # [AccountForecast]
    assumptions: list
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)    # series ids and liability ids used


def fold(text: str) -> str:
    """Accent- and case-folded text (NFKD): "Caisse d'Épargne" == "caisse d'epargne"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def resolve_liability_account(ds: Dataset, lb) -> Optional[str]:
    """The account a liability is debited from: the exact `debited_account` (uid or label) first, else a match of the
    free text `debited_from` against account labels / banks."""
    exact = getattr(lb, "debited_account", None)
    if exact:
        a = ds.resolve_account(exact)
        return a.uid if a else None
    return _resolve_account(ds, getattr(lb, "debited_from", None))


def _resolve_account(ds: Dataset, text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    low = fold(text)
    hits = [a.uid for a in ds.accounts.values() if (a.label and fold(a.label) in low)
            or (a.label and low in fold(a.label))]
    if len(hits) != 1:
        hits = [a.uid for a in ds.accounts.values() if a.bank and fold(a.bank) in low
                and (a.purpose or "") in low]
    if len(hits) != 1:
        banks = {a.uid for a in ds.accounts.values() if a.bank and re.sub(r"[^a-z]", "", fold(a.bank))[:8]
                 in re.sub(r"[^a-z]", "", low)}
        main = [u for u in banks if (ds.accounts[u].purpose or "") in ("main", "cards", "")]
        hits = main if len(main) == 1 else (list(banks) if len(banks) == 1 else [])
    return hits[0] if len(hits) == 1 else None


def _not_habits(ds: Dataset, rec: RecurringResult) -> frozenset:
    """Transactions that must not feed the variable-spend habit: payments flagged large or to a new merchant, and
    payments that look like the first instalments of an active series (same account, direction and amount, dated before
    the series started to be detected: counted once, as the series)."""
    from coach.analytics.anomalies import large_transactions, new_merchants
    out = {k for a in large_transactions(ds, None, rec) + new_merchants(ds, None) for k in a.evidence}
    for x in rec.series:
        if x.status != "active":
            continue
        ref = abs(x.expected_amount_c)
        for t in ds.whole:
            if (t.account == x.account and t.date < x.first_date and (t.amount_c < 0) == (x.expected_amount_c < 0)
                    and abs(abs(t.amount_c) - ref) <= 0.01 * ref):
                out.add(t.key)
    return frozenset(out)


def _variable_stats(ds: Dataset, uid: str, member_keys: set, months_n: int, skip: frozenset = frozenset()) -> tuple[int, int, bool]:
    """-> (monthly mean, sigma, has_history) of the non-recurring net spend of one account."""
    if (ds.accounts[uid].purpose or "") == "savings":      # deliberate deposits / withdrawals: not predictable
        return 0, 0, True
    months = last_n(sorted(ds.coverage.covered(uid)), months_n)
    if len(months) < 2:                                   # one month tells nothing about a typical month
        return 0, 0, False
    ms = set(months)
    if not any(t.account == uid and t.month in ms for t in ds.txs):
        return 0, 0, True                                 # dormant account: nothing to project
    per = {m: 0 for m in months}
    for t in ds.whole:
        if (t.account == uid and t.month in ms and (is_spending(t.category) or is_transfer(t.category))
                and not t.is_saved and not t.is_one_off and t.key not in member_keys and t.key not in skip):
            per[t.month] -= t.amount_c
    vals = [per[m] for m in months]
    mean = median_c(vals)                                  # robust: one lumpy month does not become the habit
    sigma = int(round(statistics.stdev(vals))) if len(vals) >= 2 else 0
    return mean, sigma, True


def _account_forecast(ds: Dataset, uid: Optional[str], label: str, events: list[ForecastEvent], mean: int, sigma: int,
                      has_hist: bool, start_c: Optional[int], start: date, horizon: int, z: float, flags: list,
                      bal) -> AccountForecast:
    acc = ds.accounts.get(uid) if uid else None
    per_day = mean / DAYS_PER_MONTH
    pts, lo_ev, hi_ev = [], 0, 0
    by_day: dict[date, list[ForecastEvent]] = {}
    for e in events:
        by_day.setdefault(e.date, []).append(e)
    today = ds.today
    end = today + timedelta(days=horizon)
    first_neg = first_risk = None
    min_c = min_d = None
    cum_var = 0.0
    day = start
    t_days = 0
    expected = float(start_c) if start_c is not None else 0.0
    while day <= end and start_c is not None:
        for e in by_day.get(day, []):
            expected += e.amount_c
            lo_ev += min(0, e.low_c - e.amount_c)          # worst case of a variable-amount payment
            hi_ev += max(0, e.high_c - e.amount_c)
        cum_var += per_day
        t_days += 1
        if day > today:
            band = z * sigma * math.sqrt(t_days / DAYS_PER_MONTH)
            exp_c = int(round(expected - cum_var))
            low = int(round(expected - cum_var - band + lo_ev))
            high = int(round(expected - cum_var + band + hi_ev))
            pts.append(ForecastPoint(day, exp_c, min(low, exp_c), max(high, exp_c)))
            if min_c is None or exp_c < min_c:
                min_c, min_d = exp_c, day
            if exp_c < 0 and first_neg is None:
                first_neg = day
            if low < 0 and first_risk is None:
                first_risk = day
        day += timedelta(days=1)
    miles = []
    for n in (30, 60, 90):
        if n <= horizon and pts:
            p = pts[n - 1]
            miles.append(Milestone(n, p.date, p.balance_c, p.low_c, p.high_c))
    if first_neg:
        flags.append("projected_negative")
    elif first_risk:
        flags.append("at_risk")
    return AccountForecast(uid, label, acc.purpose if acc else None, acc.owner if acc else None, start_c,
                           bal.as_of if bal else None, bal.type if bal else None, mean, sigma, miles, min_c, min_d,
                           first_neg, first_risk, flags, pts, events)


def forecast(ds: Dataset, days: int = 90, scope: Optional[Scope] = None, recurring: Optional[RecurringResult] = None,
             points: bool = True) -> ForecastResult:
    s = ds.settings
    # series are always detected unscoped: a liability <-> series link must not depend on the scope asked for
    rec = recurring or detect_recurring(ds)
    allowed = ds.balance_uids(scope)                    # a person's view forecasts the accounts they own (E14-4)
    warnings: list[str] = []
    today = ds.today
    end = today + timedelta(days=days)
    member_keys = {o.tx_key for x in rec.series for o in x.occurrences}
    skip = _not_habits(ds, rec)
    linked_liab = {l.id for x in rec.series if x.status == "active" for l in x.links if l.kind == "liability"}
    ev_by_acc: dict[Optional[str], list[ForecastEvent]] = {u: [] for u in allowed}
    ev_by_acc[None] = []
    used: set[str] = set()
    for u in allowed:
        bal = ds.balance_of(u)
        start = (min(bal.as_of, today) if bal else today) + timedelta(days=1)
        for x in rec.series:
            if x.account != u:
                continue
            for dd, overdue in occurrences(x, start, end):
                ev_by_acc[u].append(ForecastEvent(dd, u, ds.label(u), x.entity, x.expected_amount_c,
                                                  x.expected_amount_c if x.amount_mode == "fixed" else
                                                  (x.amount_high_c if x.expected_amount_c < 0 else x.amount_low_c),
                                                  x.expected_amount_c if x.amount_mode == "fixed" else
                                                  (x.amount_low_c if x.expected_amount_c < 0 else x.amount_high_c),
                                                  "recurring", x.id, "observed", overdue))
                used.add(x.id)
    from coach.loans import service as loans_service
    for _rel, lb in ds.memory.liabilities:
        if lb.id in linked_liab:
            continue
        if lb.end_date and lb.end_date < today:
            continue
        uid = resolve_liability_account(ds, lb)
        if uid is not None and uid not in ev_by_acc:
            continue
        if uid is None and scope is not None and not scope.is_all:
            warnings.append(f"liability {lb.id}: debited account unknown (set `debited_account`), left out of this scoped forecast")
            continue
        for dd, amount, cert, _note in loans_service.upcoming_payments(ds, lb, today + timedelta(days=1), end):
            ev_by_acc[uid].append(ForecastEvent(dd, uid, ds.label(uid) if uid else "(account unresolved)", lb.id,
                                                -amount, -amount, -amount, "liability", lb.id,
                                                "assumed" if cert == "assumed" else "scheduled"))
            used.add(lb.id)
    accs = []
    hh_events: list[ForecastEvent] = []
    hh_start, hh_mean, hh_var = 0, 0, 0.0
    for u in allowed:
        bal = ds.balance_of(u)
        fl = []
        mean, sigma, has = _variable_stats(ds, u, member_keys, s.forecast_variable_months, skip)
        if not has:
            fl.append("no_variable_history")
        if bal is None:
            fl.append("no_balance")
        elif (today - bal.as_of).days > s.forecast_balance_stale_days:
            fl.append("balance_stale")
        start = (min(bal.as_of, today) if bal else today) + timedelta(days=1)
        evs = sorted(ev_by_acc[u], key=lambda e: (e.date, e.ref))
        af = _account_forecast(ds, u, ds.label(u), evs, mean, sigma, has, bal.amount_c if bal else None, start, days,
                               s.forecast_band_z, fl, bal)
        accs.append(af)
        if bal is not None:
            hh_start += bal.amount_c
            hh_mean += mean
            hh_var += float(sigma) ** 2
            hh_events += evs
    hh_events += ev_by_acc[None]
    hh_events.sort(key=lambda e: (e.date, e.account or "", e.ref))
    # household = the accounts with a balance + the household-level liability debits
    with_bal = [a for a in accs if a.start_balance_c is not None]
    hh_flags = [f"{len(accs) - len(with_bal)} account(s) without balance left out"] if len(with_bal) != len(accs) else []
    if any("balance_stale" in a.flags for a in with_bal):
        hh_flags.append("balance_stale")
    pts = []
    first_neg = first_risk = min_c = min_d = None
    if with_bal and all(a.points for a in with_bal):
        extra: dict[date, int] = {}
        for e in ev_by_acc[None]:                              # debits that belong to no account (unresolved liability)
            extra[e.date] = extra.get(e.date, 0) + e.amount_c
        cum_extra = 0
        for i in range(min(len(a.points) for a in with_bal)):
            day = with_bal[0].points[i].date
            cum_extra += extra.get(day, 0)
            exp_c = sum(a.points[i].balance_c for a in with_bal) + cum_extra
            # the accounts' bands (variable spend + payment ranges) are combined in quadrature
            lo_gap = math.sqrt(sum((a.points[i].balance_c - a.points[i].low_c) ** 2 for a in with_bal))
            hi_gap = math.sqrt(sum((a.points[i].high_c - a.points[i].balance_c) ** 2 for a in with_bal))
            pts.append(ForecastPoint(day, exp_c, int(round(exp_c - lo_gap)), int(round(exp_c + hi_gap))))
            if min_c is None or exp_c < min_c:
                min_c, min_d = exp_c, day
            if exp_c < 0 and first_neg is None:
                first_neg = day
            if pts[-1].low_c < 0 and first_risk is None:
                first_risk = day
    miles = [Milestone(k, pts[k - 1].date, pts[k - 1].balance_c, pts[k - 1].low_c, pts[k - 1].high_c)
             for k in (30, 60, 90) if k <= days and len(pts) >= k]
    if first_neg:
        hh_flags.append("projected_negative")
    elif first_risk:
        hh_flags.append("at_risk")
    household = AccountForecast(None, "household", None, None, hh_start if with_bal else None,
                                today if with_bal else None, None, hh_mean, int(round(math.sqrt(hh_var))), miles, min_c,
                                min_d, first_neg, first_risk, hh_flags, pts, hh_events)
    if not points:
        household.points = []
        for a in accs:
            a.points = []
    assumptions = [
        "start = latest balance snapshot per account; recurring series replayed from their next expected date",
        "variable spend = average monthly non-recurring spend over the account's own covered months, spread evenly",
        f"band = +-{s.forecast_band_z} sigma of the monthly variable spend (sqrt-time) plus the amount range of "
        "variable series; non-recurring income is not projected",
    ]
    if any(e.certainty == "assumed" for e in hh_events):
        assumptions.append("liability instalments without a matching recurring series and without a computable amortization "
                           "schedule are assumed on the day of the loan's start_date, at the declared monthly_payment")
    if any(e.certainty == "scheduled" for e in hh_events):
        assumptions.append("liability instalments without a matching recurring series use the amortization schedule: exact due "
                           "dates and amounts (insurance included; a variable-rate loan uses its current rate)")
    notes = [f"{len(ds.foreign)} non-EUR transaction(s) left out"] if ds.foreign else []
    cov = ds.coverage.info([a.account for a in accs if a.account], [],
                           "variable spend: each account's own last covered months", notes)
    assumptions += warnings
    return ForecastResult(today, days, household, accs, assumptions, (scope or Scope()).describe(), cov, sorted(used))
