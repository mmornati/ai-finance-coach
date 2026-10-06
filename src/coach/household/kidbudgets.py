"""Kid budgets and gentle alerts (E14-6).

A kid budget (``kid_budgets`` in household.yaml) is a weekly (Monday to Sunday) or monthly (calendar month) spending limit of one member,
optionally for one category or group. The status counts the spending ATTRIBUTED to that member (refunds reduce it) in the current period.
It is ``ok`` below 80 % of the limit, ``at_risk`` from 80 %, ``over`` above 100 %.

The alerts go through the E10 engine as the kind ``kid_budget`` and are LOCAL ONLY: no external channel (ntfy, e-mail, Telegram, the macOS
notification) ever receives them, not even as a count (``alerts.settings.LOCAL_ONLY_KINDS``), so no child's name or amount leaves the machine
through an alert. Their wording is gentle and may name the child, because it stays in the local feed.

``unusual`` spending: a payment of the last 7 days that is at least four times the child's median payment and at least 15 EUR, when the
child has at least eight payments in the last six months.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.analytics.common import median_c, money_str, month_end, month_key, month_start
from coach.analytics.dataset import Dataset, is_spending
from coach.household.people import People

WARN_AT = 0.8
UNUSUAL_FACTOR = 4
UNUSUAL_MIN_C = 1500
UNUSUAL_MIN_TXS = 8


def period_bounds(period: str, today: dt.date) -> tuple[dt.date, dt.date]:
    if period == "weekly":
        start = today - dt.timedelta(days=today.weekday())
        return start, start + dt.timedelta(days=6)
    m = month_key(today)
    return month_start(m), month_end(m)


def _counts(t, budget) -> bool:
    if not (is_spending(t.category) or t.category == "income.refund"):
        return False
    if budget.category and t.category != budget.category:
        return False
    if budget.group and t.category.split(".")[0] != budget.group:
        return False
    return True


def status(ds: Dataset, member: Optional[str] = None) -> list[dict]:
    """The status of every kid budget (or those of `member`): spent / limit in the current period."""
    people: People = ds.memory.people
    if people is None:
        return []
    out = []
    for b in people.kid_budgets:
        if member and b.member != member:
            continue
        lo, hi = period_bounds(b.period, ds.today)
        spent = -sum(t.amount_c for t in ds.txs if t.person == b.member and lo <= t.date <= min(hi, ds.today) and _counts(t, b))
        limit_c = round(b.limit * 100)
        spent = max(spent, 0)                                           # refunds can bring a period below zero: nothing spent
        ratio = spent / limit_c if limit_c else 0.0
        state = "over" if spent > limit_c else "at_risk" if ratio >= WARN_AT else "ok"
        elapsed = (min(ds.today, hi) - lo).days + 1
        length = (hi - lo).days + 1
        out.append({"id": b.id, "member": b.member, "period": b.period, "category": b.category, "group": b.group,
                    "limit_c": limit_c, "spent_c": spent, "remaining_c": max(limit_c - spent, 0), "ratio": round(ratio, 4),
                    "status": state, "period_start": lo, "period_end": hi, "days_left": max(length - elapsed, 0),
                    "projected_c": round(spent / elapsed * length) if elapsed else spent, "note": b.note})
    return out


def unusual(ds: Dataset, member: str) -> list:
    """Payments of the last 7 days that stand out against the child's usual ones."""
    mine = [t for t in ds.txs if t.person == member and is_spending(t.category) and t.amount_c < 0]
    lo = ds.today - dt.timedelta(days=183)
    recent = [t for t in mine if t.date >= lo]
    if len(recent) < UNUSUAL_MIN_TXS:
        return []
    med = median_c([-t.amount_c for t in recent])
    week = ds.today - dt.timedelta(days=7)
    return [t for t in recent if t.date > week and -t.amount_c >= max(UNUSUAL_MIN_C, UNUSUAL_FACTOR * med)]


def candidates(ds: Dataset) -> list:
    """The alert candidates (kind ``kid_budget``) of the E10 engine. Local feed only."""
    from coach.alerts.signals import Candidate
    people: People = ds.memory.people
    if people is None or not people:
        return []
    out = []
    for s in status(ds):
        if s["status"] == "ok":
            continue
        name = people.first_name(s["member"])
        what = (s["category"] or s["group"] or "spending").replace("_", " ").replace(".", " / ")
        per = "this week" if s["period"] == "weekly" else "this month"
        if s["status"] == "over":
            title = f"{name} went over the {s['period']} limit ({what})"
            body = (f"{money_str(s['spent_c'])} EUR spent {per} against a limit of {money_str(s['limit_c'])} EUR. "
                    "A friendly chat, not a telling-off: look at it together.")
            sev = "medium"
        else:
            title = f"{name} is close to the {s['period']} limit ({what})"
            body = (f"{money_str(s['spent_c'])} EUR of {money_str(s['limit_c'])} EUR used {per}, {s['days_left']} day(s) left.")
            sev = "low"
        out.append(Candidate("kid_budget", f"kidbudget:{s['id']}:{s['period_start'].isoformat()}", sev, title, body,
                             {"subtype": "limit", "budget": s["id"], "amount_c": s["spent_c"], "period_start": s["period_start"].isoformat()}))
    for m in people.children():
        for t in unusual(ds, m):
            name = people.first_name(m)
            out.append(Candidate("kid_budget", f"kidunusual:{t.key}", "low", f"{name}: an unusually large payment",
                                 f"{money_str(-t.amount_c)} EUR on {t.date.isoformat()}, well above {name}'s usual payments. Maybe worth a quiet word.",
                                 {"subtype": "unusual", "amount_c": -t.amount_c, "evidence": [t.key]}))
    return out
