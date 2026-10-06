"""savings_estimate (E7-7): what switching a recurring service to a cheaper alternative really saves, net of switching costs.

All figures come from the arguments (the alternative's price is a DATED, SOURCED quote found by the find-cheaper skill, never
a guess) and exact integer-cent arithmetic.

    monthly_saving   = current_monthly - alternative_monthly
    yearly_saving    = monthly_saving x 12
    gross_saving     = monthly_saving x months
    net_saving       = gross_saving - switching_costs        (switching_costs: exit fee, set-up, deposit lost, equipment...)
    break_even       = ceil(switching_costs / monthly_saving) months, when the saving is positive
A promotional alternative price (a first-year discount) is NOT modelled: give the full price, or call the tool twice (promo
months, then full price) and add the results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from coach import disclaimers as D
from coach.analytics.common import Result
from coach.skills.money import ceil_div, cents

DISCLAIMER = D.get("savings")      # the wording lives in coach.disclaimers (E11-5)


@dataclass
class SavingsEstimate(Result):
    months: int
    current_monthly_c: int
    alternative_monthly_c: int
    switching_costs_c: int
    monthly_saving_c: int
    yearly_saving_c: int
    gross_saving_c: int
    net_saving_c: int
    break_even_months: Optional[int]
    verdict: str
    notes: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def savings_estimate(current_monthly, alternative_monthly, switching_costs=0, months: int = 12) -> SavingsEstimate:
    if months < 1 or months > 120:
        raise ValueError("months must be between 1 and 120")
    cur, alt, cost = cents(current_monthly), cents(alternative_monthly), cents(switching_costs)
    if cur < 0 or alt < 0 or cost < 0:
        raise ValueError("amounts must be >= 0")
    monthly = cur - alt
    gross = monthly * months
    net = gross - cost
    be = None
    if monthly > 0:
        be = ceil_div(cost, monthly) if cost > 0 else 0
    notes = []
    if monthly <= 0:
        verdict = "no_saving"
        notes.append("the alternative is not cheaper than the current price")
    elif net <= 0:
        verdict = "costs_exceed_saving_over_the_period"
        notes.append(f"the switching costs are paid back after {be} month(s); the period asked is {months}")
    else:
        verdict = "saving"
    if cost and monthly > 0 and be is not None and be > months:
        notes.append("break-even is later than the horizon: a longer horizon would turn the result positive")
    return SavingsEstimate(months, cur, alt, cost, monthly, monthly * 12, gross, net, be, verdict, notes)
