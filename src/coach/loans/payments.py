"""Loan payments seen in the bank data and the alerts built from them (E9-2).

A payment is a debit of any account whose merchant key / description / entity matches the liability's ``payment_match`` (and,
when the liability has an ``amount_match``, whose amount is inside its band): the same linking as the contracts (E8) and the
recurring series (:func:`coach.analytics.recurring.match_share`). Alerts (each says what it is based on, nothing is assumed):

    missed_payment      a due date (from the schedule, else the usual day of the month) + a grace period has passed, the debited
                        account's data are complete through that day and no matching payment was seen in [due - 4 days, due + grace]
    amount_changed      the last payment differs from the previous one, or from the expected amount (schedule / declared payment),
                        by more than 2 % and 1 EUR
    extra_payment       a matching debit away from any due date, or much larger than the instalment: a possible partial prepayment
    wrong_account       a matching payment left an account other than the one the loan is recorded to be debited from

Every threshold is a module constant; a due date whose data are not known to be complete never raises an alert.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import Result, add_months, money_str
from coach.analytics.recurring import tx_matches
from coach.loans import schedule as S

GRACE_DAYS = 5                 # days after the due date before a missing payment is reported
EARLY_DAYS = 4                 # a debit this many days before the due date still counts as that instalment
LOOKBACK_DAYS = 75             # due dates older than this are not reported
AMOUNT_TOLERANCE = 0.02        # relative, and at least MIN_DELTA_C
MIN_DELTA_C = 100
EXTRA_FACTOR = 1.5             # a payment this many times the expected amount (and 100 EUR above it) is "extra"
EXTRA_LOOKBACK_DAYS = 120


@dataclass
class Payment(Result):
    date: dt.date
    amount_c: int                   # positive magnitude
    account: str
    account_label: str
    tx_key: str


@dataclass
class Alert(Result):
    id: str
    type: str                       # missed_payment | amount_changed | extra_payment | wrong_account
    severity: str                   # high | medium | low
    loan: str
    title: str
    body: str
    date: dt.date                   # the date the alert is about (the due date, the payment date)
    amount_c: Optional[int] = None
    expected_date: Optional[dt.date] = None
    expected_amount_c: Optional[int] = None
    evidence: list = field(default_factory=list)       # transaction keys


def tx_matcher(lb):
    """A predicate on a transaction (or None when the liability has no usable ``payment_match``)."""
    lb = S.lax(lb)
    if not lb.payment_match:
        return None
    try:
        rx = re.compile(lb.payment_match, re.I)
    except re.error:
        return None
    am = lb.amount_match

    def match(t) -> bool:
        return t.amount_c < 0 and tx_matches(rx, am, t)          # a payment is a debit; the linking rule is the contracts' one
    return match


def observed(ds, lb) -> list[Payment]:
    """The matching debits, oldest first."""
    m = tx_matcher(lb)
    if m is None:
        return []
    return [Payment(t.date, -t.amount_c, t.account, ds.label(t.account), t.key) for t in ds.whole if m(t)]


def _close(a: int, b: int) -> bool:
    return abs(a - b) <= max(MIN_DELTA_C, int(AMOUNT_TOLERANCE * max(a, b)))


def expected_amounts(lb, sch: S.LoanSchedule) -> list[int]:
    """The amounts a regular debit may have: the declared payment, the schedule's total and its payment without insurance."""
    lb = S.lax(lb)
    out = []
    if lb.monthly_payment:
        out.append(int(round(lb.monthly_payment * 100)))
    if sch.status == "computed" and sch.rows:
        nxt = next((r for r in sch.rows if not r.made), sch.rows[-1])
        out += [nxt.total_c, nxt.payment_c]
    return [x for x in dict.fromkeys(out) if x > 0]


def expected_day(lb, obs: list[Payment]) -> Optional[int]:
    lb = S.lax(lb)
    if lb.payment_day:
        return lb.payment_day
    last = obs[-6:]
    if len(last) >= 3:
        return int(statistics.median(p.date.day for p in last))
    if lb.first_payment_date:
        return lb.first_payment_date.day
    if lb.start_date:
        return lb.start_date.day
    return None


def due_dates(lb, sch: S.LoanSchedule, obs: list[Payment], start: dt.date, end: dt.date) -> list[tuple[dt.date, Optional[int]]]:
    """[(due date, scheduled total or None)] of the instalments due in [start, end]. From the schedule when computed (on the
    habitual day of the month when the day is not recorded), else monthly on the usual day while the loan is not over."""
    lb = S.lax(lb)
    out: list[tuple[dt.date, Optional[int]]] = []
    day = expected_day(lb, obs)
    if sch.status == "computed":
        for r in sch.rows:
            d = r.due
            if not lb.payment_day and day:
                import calendar
                d = d.replace(day=min(day, calendar.monthrange(d.year, d.month)[1]))
            if start <= d <= end:
                out.append((d, r.total_c))
        return out
    if day is None or not obs:
        return out
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        import calendar
        d = cur.replace(day=min(day, calendar.monthrange(cur.year, cur.month)[1]))
        if start <= d <= end and (not lb.end_date or d <= lb.end_date) and (not lb.start_date or d > lb.start_date) \
                and d > obs[0].date:
            out.append((d, None))
        cur = add_months(cur, 1)
    return out


def _aid(*parts) -> str:
    return "ins_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def alerts(ds, lb, sch: S.LoanSchedule, obs: Optional[list[Payment]] = None, today: Optional[dt.date] = None,
           debited_uid: Optional[str] = None) -> list[Alert]:
    lb = S.lax(lb)
    today = today or ds.today
    grace = getattr(ds.settings, "loan_grace_days", GRACE_DAYS)
    obs = observed(ds, lb) if obs is None else obs
    out: list[Alert] = []
    if not obs:
        return out
    if lb.end_date and lb.end_date < today - dt.timedelta(days=grace):
        return out                                           # the loan is over: no alert
    who = lb.lender or lb.id
    exp = expected_amounts(lb, sch)
    # -- missed payment
    acct = debited_uid or obs[-1].account
    cov = ds.coverage.of(acct) if acct in ds.accounts else None
    if cov is not None and cov.last is not None and cov.first is not None:
        win_start = today - dt.timedelta(days=LOOKBACK_DAYS)
        for due, total in due_dates(lb, sch, obs, win_start, today - dt.timedelta(days=grace + 1)):
            if cov.first > due or cov.last < due + dt.timedelta(days=grace):
                continue                                      # data not known to be complete around that day
            lo, hi = due - dt.timedelta(days=EARLY_DAYS), due + dt.timedelta(days=grace)
            if any(lo <= p.date <= hi for p in obs):
                continue
            late = [p for p in obs if p.date > hi and p.date <= today and (p.date - due).days <= 31]
            out.append(Alert(_aid("loan-missed", lb.id, due), "missed_payment", "high", lb.id,
                             f"{who}: expected payment of {due} not seen",
                             f"The instalment due on {due} ({'schedule' if sch.status == 'computed' else 'usual day of the month'}) was "
                             f"not found within {grace} days in the data of the account it leaves"
                             + (f"; a later matching debit was seen on {late[0].date}" if late else "")
                             + ". Check the account or ask the lender: nothing is assumed about why.",
                             due, total, due, total, [p.tx_key for p in late[:1]]))
    # -- amount changed (the latest payment against the one before, else against the expected amounts)
    last = obs[-1]
    prev = obs[-2] if len(obs) >= 2 else None
    if (today - last.date).days <= 62:
        if prev is not None and not _close(last.amount_c, prev.amount_c) and (last.date - prev.date).days <= 45 \
                and last.amount_c < EXTRA_FACTOR * prev.amount_c:
            out.append(Alert(_aid("loan-amount", lb.id, last.date), "amount_changed", "medium", lb.id,
                             f"{who}: payment changed to {money_str(last.amount_c)}",
                             f"The payment of {last.date} is {money_str(last.amount_c)}, the one before ({prev.date}) was "
                             f"{money_str(prev.amount_c)}. A variable rate, a new insurance or a modulation can explain it; "
                             "nothing is assumed.", last.date, last.amount_c, None, prev.amount_c, [last.tx_key, prev.tx_key]))
        elif prev is None and exp and not any(_close(last.amount_c, e) for e in exp):
            out.append(Alert(_aid("loan-amount-sched", lb.id, last.date), "amount_changed", "low", lb.id,
                             f"{who}: payment differs from the expected amount",
                             f"The payment of {last.date} is {money_str(last.amount_c)}; the loan file / schedule expects "
                             f"{' or '.join(money_str(e) for e in exp[:2])}.", last.date, last.amount_c, None, exp[0], [last.tx_key]))
        elif prev is not None and exp and _close(last.amount_c, prev.amount_c) and not any(_close(last.amount_c, e) for e in exp) \
                and sch.status == "computed" and not sch.approximate:
            out.append(Alert(_aid("loan-amount-sched", lb.id, last.date), "amount_changed", "low", lb.id,
                             f"{who}: payments differ from the schedule",
                             f"The last payments are {money_str(last.amount_c)}; the computed schedule expects "
                             f"{' or '.join(money_str(e) for e in exp[:2])} (rate, insurance or term in the file may differ from the contract).",
                             last.date, last.amount_c, None, exp[0], [last.tx_key]))
    # -- extra payment: much larger than the instalment, or a debit away from the usual day when another one already paid that month
    #    (a lone debit that merely moved by a few days is the instalment, not an extra)
    day = expected_day(lb, obs)
    ref = statistics.median(p.amount_c for p in obs[-6:])

    def gap(p) -> Optional[int]:
        return None if day is None else min(abs((p.date - _on(p.date, day, k)).days) for k in (-1, 0, 1))

    regular_months = {(q.date.year, q.date.month) for q in obs if gap(q) is not None and gap(q) <= grace + EARLY_DAYS}
    for p in obs:
        if (today - p.date).days > EXTRA_LOOKBACK_DAYS:
            continue
        big = p.amount_c >= EXTRA_FACTOR * ref and p.amount_c - ref >= 10000
        g = gap(p)
        off_day = g is not None and g > grace + EARLY_DAYS
        second = off_day and ((p.date.year, p.date.month) in regular_months or not _close(p.amount_c, ref))
        if big or second:
            out.append(Alert(_aid("loan-extra", lb.id, p.tx_key), "extra_payment", "medium", lb.id,
                             f"{who}: unexpected payment of {money_str(p.amount_c)}",
                             f"A debit of {money_str(p.amount_c)} on {p.date} matches this loan but is "
                             + ("much larger than the usual instalment" if big else "away from the usual payment day, in a month that "
                                "already has its instalment" if (p.date.year, p.date.month) in regular_months else
                                "away from the usual payment day and of another size")
                             + ". It may be a partial early repayment: if so, ask the lender for the new schedule and update the "
                               "capital still due.", p.date, p.amount_c, None, ref, [p.tx_key]))
    # -- wrong account
    if debited_uid:
        seen: set[str] = set()
        for p in reversed(obs):
            if p.account != debited_uid and p.account not in seen and (today - p.date).days <= EXTRA_LOOKBACK_DAYS:
                seen.add(p.account)
                out.append(Alert(_aid("loan-account", lb.id, p.account, p.date), "wrong_account", "medium", lb.id,
                                 f"{who}: paid from another account",
                                 f"The payment of {p.date} ({money_str(p.amount_c)}) left {p.account_label}, not "
                                 f"{ds.label(debited_uid)}, the account the loan is recorded to be debited from.",
                                 p.date, p.amount_c, None, None, [p.tx_key]))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda a: (order[a.severity], a.date), reverse=False)


def _on(d: dt.date, day: int, offset_months: int) -> dt.date:
    import calendar
    first = add_months(d.replace(day=1), offset_months)
    return first.replace(day=min(day, calendar.monthrange(first.year, first.month)[1]))


def summary(obs: list[Payment]) -> dict:
    if not obs:
        return {"count": 0, "first": None, "last": None, "last_amount": None, "median_amount": None}
    amts = [p.amount_c for p in obs]
    return {"count": len(obs), "first": obs[0].date.isoformat(), "last": obs[-1].date.isoformat(),
            "last_amount": money_str(obs[-1].amount_c), "median_amount": money_str(int(statistics.median(amts))),
            "accounts": sorted({p.account_label for p in obs})}
