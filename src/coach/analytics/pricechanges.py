"""Price changes of recurring payments (E4-4).

For each recurring series (expenses and income) the sequence of absolute amounts is walked in date order. The current
price level starts at the first amount. An amount that differs from the level by at least `price_change_threshold_pct`
(default 3 %) AND `price_change_min_abs` (default 0.50 EUR) is a change when the NEXT occurrence stays near the new
amount (``confirmed``) or when it is the latest occurrence (``confirmed = false``: seen once, not yet confirmed). An
amount followed by a return to the old level is a blip (a one-time extra charge) and is ignored. The new level is then
the changed amount.

Variable-amount series, and every series of a category where bills naturally vary (`recurring_variable_categories`:
energy, water, telecom...; their amounts move a little each time), are compared over the last 3 occurrences against
the 3 before, with the wide `price_change_variable_pct` threshold (default 20 %); the result is always unconfirmed and
flagged ``variable``.

``delta`` is the change of the magnitude of the payment: positive = the payment got bigger. ``effect`` says what that
means for the household: ``costs_more`` / ``costs_less`` for an expense, ``pays_more`` / ``pays_less`` for an income.
``yearly_impact`` = delta x occurrences per year (same sign convention as delta; for an expense positive = costs more).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from coach.analytics.common import CoverageInfo, Result, Scope, median_c, mul_cents
from coach.analytics.dataset import Dataset
from coach.analytics.recurring import CADENCES, RecurringResult, RecurringSeries, detect_recurring, levels


@dataclass
class PriceChange(Result):
    id: str
    series_id: str
    entity: str
    account_label: str
    category: str
    cadence: str
    date: date
    tx_key: str
    old_c: int                     # previous absolute amount (positive)
    new_c: int
    delta_c: int
    pct: float
    direction: str                 # increase | decrease
    effect: str                    # costs_more | costs_less | pays_more | pays_less
    yearly_impact_c: int
    confirmed: bool
    variable: bool = False
    dismissed: bool = False


@dataclass
class PriceChangesResult(Result):
    as_of: date
    changes: list                  # [PriceChange], most recent first
    counts: dict
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)       # series ids


def _change(x: RecurringSeries, i: int, old: int, new: int, confirmed: bool, variable: bool = False) -> PriceChange:
    o = x.occurrences[i]
    delta = new - old
    up = delta > 0
    effect = ("costs_more" if up else "costs_less") if x.direction == "out" else ("pays_more" if up else "pays_less")
    cid = "chg_" + hashlib.sha1(f"{x.id}|{o.tx_key}".encode()).hexdigest()[:10]
    return PriceChange(cid, x.id, x.entity, x.account_label, x.category, x.cadence, o.date, o.tx_key, old, new, delta,
                       round(delta / old, 4) if old else 0.0, "increase" if up else "decrease", effect,
                       mul_cents(delta, CADENCES[x.cadence][3]), confirmed, variable)


def changes_of(x: RecurringSeries, pct: float, min_abs_c: int, var_pct: float, variable_like: bool = False) -> list[PriceChange]:
    amts = [abs(o.amount_c) for o in x.occurrences]
    if x.amount_mode == "variable":
        # consumption-driven bills: the median of the last 3 against the 3 before, wide threshold, never confirmed
        out: list[PriceChange] = []
        if len(amts) >= 6:
            prev, last = median_c(amts[-6:-3]), median_c(amts[-3:])
            if prev and abs(last - prev) / prev >= var_pct / 100 and abs(last - prev) >= min_abs_c:
                out.append(_change(x, len(amts) - 3, prev, last, False, True))
        return out
    # fixed amounts: steps of the price level (wide threshold for bills that move a little each time)
    return [_change(x, i, old, new, ok) for i, old, new, ok in levels(amts, var_pct if variable_like else pct, min_abs_c)]


def price_changes(ds: Dataset, scope: Optional[Scope] = None, recurring: Optional[RecurringResult] = None,
                  since: Optional[date] = None, only_confirmed: bool = False, dismissed: frozenset = frozenset(),
                  include_dismissed: bool = False) -> PriceChangesResult:
    s = ds.settings
    rec = recurring or detect_recurring(ds, scope)
    min_abs_c = int(round(s.price_change_min_abs * 100))
    found: list[PriceChange] = []
    for x in rec.series:
        if x.kind not in ("expense", "income") or x.status != "active":      # an ended series has no price any more
            continue
        for c in changes_of(x, s.price_change_threshold_pct, min_abs_c, s.price_change_variable_pct,
                            x.category in s.recurring_variable_categories):
            if x.kind == "income" and not (c.confirmed and abs(c.pct) * 100 >= s.price_change_income_pct):
                continue                                                      # pay moves a little every month: only real raises
            c.dismissed = c.id in dismissed
            if (since is None or c.date >= since) and (c.confirmed or not only_confirmed) \
                    and (include_dismissed or not c.dismissed):
                found.append(c)
    found.sort(key=lambda c: (c.date, c.series_id), reverse=True)
    counts = {"increase": sum(1 for c in found if c.direction == "increase"),
              "decrease": sum(1 for c in found if c.direction == "decrease"),
              "unconfirmed": sum(1 for c in found if not c.confirmed)}
    cov = rec.coverage
    return PriceChangesResult(ds.today, found, counts, (scope or Scope()).describe(), cov,
                              sorted({c.series_id for c in found}))


def dismiss_price_change(con, change_id: str, note: Optional[str] = None, now_iso: Optional[str] = None) -> None:
    """Remember that the user has seen a price change (it is hidden from the lists; `include_dismissed` shows it)."""
    import datetime as _dt
    con.execute("INSERT OR REPLACE INTO price_change_dismissals(id, dismissed_at, note) VALUES (?,?,?)",
                (change_id, now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), note))
    con.commit()


def dismissed_ids(con) -> frozenset:
    return frozenset(r[0] for r in con.execute("SELECT id FROM price_change_dismissals"))
