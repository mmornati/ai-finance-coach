"""Small exact-money helpers shared by the skills (integer cents, Decimal arithmetic, no float on money)."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Optional

from coach.analytics.common import money_str, to_cents  # noqa: F401  (re-exported)

D = Decimal


def dec(x) -> Decimal:
    """float / str / int -> Decimal without binary noise."""
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(float(x))) if isinstance(x, float) else Decimal(str(x))


def cents(x) -> int:
    """EUR amount (number, string or Decimal) -> integer cents, rounded half up."""
    return int((dec(x) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_c(x: Decimal) -> int:
    """Decimal cents -> integer cents, half up."""
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct_of_c(c: int, pct) -> int:
    """pct % of an amount in cents."""
    return round_c(Decimal(c) * dec(pct) / Decimal(100))


def ceil_div(a: int, b: int) -> int:
    return int((Decimal(a) / Decimal(b)).to_integral_value(rounding=ROUND_CEILING))


def eur(c: Optional[int]) -> Optional[str]:
    return money_str(c)
