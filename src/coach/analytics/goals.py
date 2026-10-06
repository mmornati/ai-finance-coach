"""Savings goals (E4-9): ``memory/goals.yaml``, progress and projected completion.

A goal has a target amount, an optional target date and EXACTLY ONE progress source:
    asset    a manual asset of assets.yaml (its value / balance and `as_of`; flagged stale after `asset_stale_months`)
    account  a synced account (uid or label): its latest balance
    tag      the flows carrying a tag (e.g. ``savings``): `baseline` (amount already put aside on `start`) plus the
             net outflow of the tagged transactions since `start` (all history if no start)
Pace (EUR per month) = the AVERAGE of the last 6 covered months for a tag (net tagged flow) or an account (net flow of
the account); for an asset, the planned `monthly_contribution` of the goal (or of the asset). When no actual pace exists
the planned `monthly_contribution` is used. Projected completion = today + ceil(remaining / pace) months.
Status: ``achieved``; ``on_track`` (projected on or before target_date, or no target date and a positive pace);
``behind``; ``overdue`` (target date passed, not achieved); ``no_pace`` (cannot project); ``no_data`` (source unknown or
without a value). ``required_monthly`` = what must be put aside each month to hit the target date.
Goals are memory, written through the store (`coach goals set`, preview first, the coach may only propose).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import (CoverageInfo, Result, add_months, div_cents, pct)
from coach.analytics.coverage import last_n
from coach.analytics.dataset import Dataset


@dataclass
class GoalProgress(Result):
    id: str
    title: Optional[str]
    source: str                         # asset | account | tag
    source_ref: str
    target_c: int
    target_date: Optional[dt.date]
    current_c: Optional[int]
    current_as_of: Optional[dt.date]
    percent: Optional[float]
    remaining_c: Optional[int]
    pace_c: Optional[int]               # actual or planned EUR / month used for the projection
    pace_basis: str                     # actual | planned | none
    planned_c: Optional[int]
    required_monthly_c: Optional[int]
    months_left: Optional[int]
    projected_date: Optional[dt.date]
    status: str
    flags: list = field(default_factory=list)
    evidence: list = field(default_factory=list)


@dataclass
class GoalsResult(Result):
    as_of: dt.date
    goals: list
    counts: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # invalid goal entries: valid ones are still shown


def _cents(x) -> Optional[int]:
    return None if x is None else int(round(x * 100))


def goal_progress(ds: Dataset, goals: Optional[list] = None) -> GoalsResult:
    s = ds.settings
    today = ds.today
    out: list[GoalProgress] = []
    used_accounts: set[str] = set()
    glist = list(goals if goals is not None else ds.memory.goals)
    sources: dict = {}
    for g in glist:
        sources.setdefault(g.asset or g.account or g.tag, []).append(g.id)
    for g in glist:
        flags: list[str] = []
        evidence: list[str] = []
        shared = [i for i in sources.get(g.asset or g.account or g.tag, []) if i != g.id]
        if shared:
            flags.append("shares_source_with:" + ",".join(shared))
        current = as_of = pace = None
        basis = "none"
        planned = _cents(g.monthly_contribution)
        if g.asset:
            src, ref = "asset", g.asset
            a = next((x for x in ds.memory.assets if x.id == g.asset), None)
            if a is None:
                flags.append("unknown_asset")
            else:
                current, as_of = _cents(a.amount), a.as_of
                if a.amount is None:
                    flags.append("asset_has_no_value")
                elif a.as_of is None or a.as_of < add_months(today, -s.asset_stale_months):
                    flags.append("stale_value")
                if planned is None:
                    planned = _cents(a.contribution_monthly)
        elif g.account:
            src, ref = "account", g.account
            acc = ds.resolve_account(g.account)
            if acc is None:
                flags.append("unknown_account")
            else:
                used_accounts.add(acc.uid)
                bal = ds.balance_of(acc.uid)
                if bal is None:
                    flags.append("account_has_no_balance")
                else:
                    current, as_of = bal.amount_c, bal.as_of
                months = last_n(sorted(ds.coverage.covered(acc.uid)), 6)
                if len(months) >= 2:
                    ms = set(months)
                    tot = sum(t.amount_c for t in ds.whole if t.account == acc.uid and t.month in ms and not t.is_one_off)
                    pace, basis = div_cents(tot, len(months)), "actual"
        else:
            src, ref = "tag", g.tag
            tagged = [t for t in ds.whole if g.tag in t.tags and (g.start is None or t.date >= g.start)]
            if g.start is None:
                flags.append("counts_all_history")                 # no start date: every past tagged flow is counted
            carriers = sorted({t.account for t in ds.whole if g.tag in t.tags})
            used_accounts.update(carriers)
            current = (_cents(g.baseline) or 0) + sum(-t.amount_c for t in tagged)
            as_of = max((t.date for t in tagged), default=g.start)
            evidence = [t.key for t in tagged][-20:]
            if not tagged:
                flags.append("no_tagged_flows")
            months = last_n(ds.coverage.common_months(carriers), 6)
            if len(months) >= 2:
                ms = set(months)
                tot = sum(-t.amount_c for t in ds.whole if g.tag in t.tags and t.month in ms)
                pace, basis = div_cents(tot, len(months)), "actual"
        if pace is None and planned:
            pace, basis = planned, "planned"
        target = int(round(g.target_amount * 100))
        remaining = None if current is None else target - current
        projected = months_left = required = None
        if g.target_date:
            months_left = max(1, math.ceil((g.target_date - today).days / 30.4375)) if g.target_date > today else 0
        if current is None:
            status = "no_data"
        elif remaining <= 0:
            status = "achieved"
        else:
            if g.target_date and months_left:
                required = -(-remaining // months_left)
            if pace and pace > 0:
                n = math.ceil(remaining / pace)
                projected = add_months(today, n)
                if g.target_date:
                    status = "on_track" if projected <= g.target_date else "behind"
                else:
                    status = "on_track"
            else:
                status = "no_pace"
            if g.target_date and g.target_date < today:
                status = "overdue"
        out.append(GoalProgress(g.id, g.title, src, ref, target, g.target_date, current, as_of,
                                None if current is None else pct(current, target), remaining, pace, basis, planned,
                                required, months_left, projected, status, flags, evidence))
    order = ["overdue", "behind", "no_pace", "no_data", "on_track", "achieved"]
    out.sort(key=lambda p: (order.index(p.status), p.id))
    counts = {k: sum(1 for p in out if p.status == k) for k in order if any(p.status == k for p in out)}
    cov = ds.coverage.info(used_accounts, [], "pace: last 6 months covered by the accounts involved (tag / account goals)",
                           [] if ds.memory.goals else ["no goals set: `coach goals set`"])
    return GoalsResult(today, out, counts, cov, sorted({k for p in out for k in p.evidence}),
                       [f"goal {p} -- left out; run `coach memory check`" for p in ds.memory.goal_problems])


def goal_ops(existing_ids: set, file_exists: bool, gid: str, fields: dict) -> list[dict]:
    if gid in existing_ids:
        return [{"op": "set", "path": f"goals[{gid}].{k}", "value": v} for k, v in fields.items()]
    ops = [{"op": "append", "path": "goals", "value": {"id": gid, **fields}}]
    return ([{"op": "create", "value": {"goals": []}}] if not file_exists else []) + ops
