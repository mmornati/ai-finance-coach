"""Shared building blocks of the analytics engine: money, months, the result base class.

Money representation (the one rule for every analytics result)
    * Internally and in the result dataclasses every amount is an integer number of CENTS in a field whose name ends
      with ``_c`` (``spent_c``, ``monthly_avg_c``...). No float ever carries money, so sums are exact and reproducible.
    * ``to_dict()`` renders each ``*_c`` field as a Decimal-safe STRING with two decimals under the key without the
      suffix (``"spent": "-123.40"``). JSON consumers (UI, MCP tools, the LLM coach) never receive a float amount;
      they parse the string with a Decimal type. Ratios (rates, percentages, scores) are plain floats rounded to 4
      decimals.
    * Sign: transaction amounts keep the bank's sign (money out is negative). Summary figures named ``income``,
      ``spending``, ``saved``, ``budget``... are positive magnitudes in the direction their name says; ``net`` and
      ``delta`` are signed. Each module documents the exceptions.
Dates are ISO strings ('YYYY-MM-DD'), months 'YYYY-MM'. All date arithmetic is calendar arithmetic (no time zones,
so daylight-saving changes cannot shift a date); adding months clamps to the month end (31 Jan + 1 month = 28/29 Feb).
"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Optional

SCHEMA_VERSION = 1


# ---------------------------------------------------------------- money

def to_cents(x) -> int:
    """float / str / Decimal / int EUR -> integer cents (half-even; goes through repr so 0.1+0.2 noise is gone)."""
    if x is None:
        raise ValueError("amount is None")
    return int((Decimal(repr(float(x))) * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def money_str(cents: Optional[int]) -> Optional[str]:
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    q, r = divmod(abs(int(cents)), 100)
    return f"{sign}{q}.{r:02d}"


def parse_money(s) -> int:
    """'12.34' / '-5' / 12.3 -> cents."""
    return to_cents(Decimal(str(s)))


def div_cents(total: int, n: int) -> int:
    """total / n rounded half-even to a cent (n > 0)."""
    return int((Decimal(total) / Decimal(n)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def mul_cents(cents: int, factor: float) -> int:
    return int((Decimal(cents) * Decimal(repr(float(factor)))).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def round_to(cents: int, step_c: int) -> int:
    """Round to the nearest multiple of step_c (half up on magnitude)."""
    return int((Decimal(cents) / step_c).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step_c


def median_c(values: list[int]) -> int:
    v = sorted(values)
    n = len(v)
    if n == 0:
        raise ValueError("median of nothing")
    if n % 2:
        return v[n // 2]
    return div_cents(v[n // 2 - 1] + v[n // 2], 2)


def pct(num: float, den: float, nd: int = 4) -> Optional[float]:
    return None if not den else round(num / den, nd)


# ---------------------------------------------------------------- dates / months

def d(s) -> dt.date:
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    return dt.date.fromisoformat(str(s)[:10])


def month_key(x) -> str:
    x = d(x)
    return f"{x.year:04d}-{x.month:02d}"


def month_start(key: str) -> dt.date:
    y, m = key.split("-")
    return dt.date(int(y), int(m), 1)


def month_end(key: str) -> dt.date:
    s = month_start(key)
    return dt.date(s.year, s.month, _cal.monthrange(s.year, s.month)[1])


def days_in_month(key: str) -> int:
    return month_end(key).day


def add_months_key(key: str, n: int) -> str:
    s = month_start(key)
    i = s.year * 12 + (s.month - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def add_months(x: dt.date, n: int) -> dt.date:
    """x + n calendar months, day clamped to the end of the target month (Jan 31 + 1 = Feb 28 / 29)."""
    i = x.year * 12 + (x.month - 1) + n
    y, m = i // 12, i % 12 + 1
    return dt.date(y, m, min(x.day, _cal.monthrange(y, m)[1]))


def months_between(first: str, last: str) -> list[str]:
    """Inclusive list of month keys."""
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur = add_months_key(cur, 1)
    return out


def last_closed_month(today: dt.date) -> str:
    """The last month that is entirely in the past (the month before the one containing `today`)."""
    return add_months_key(month_key(today), -1)


# ---------------------------------------------------------------- results

def _plain(v: Any) -> Any:
    if is_dataclass(v) and not isinstance(v, type):
        out = {}
        for f in fields(v):
            val = getattr(v, f.name)
            if f.name.endswith("_c"):
                if isinstance(val, (list, tuple)):
                    out[f.name[:-2]] = [money_str(x) for x in val]
                else:
                    out[f.name[:-2]] = money_str(val)
            else:
                out[f.name] = _plain(val)
        return out
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return [_plain(x) for x in sorted(v)]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 4)
    return v


class Result:
    """Mixin of the result dataclasses: ``to_dict()`` is JSON-serialisable with stable key order (field order)."""

    def to_dict(self) -> dict:
        return _plain(self)


@dataclass
class AccountRef(Result):
    uid: str
    label: str
    owner: Optional[str] = None
    purpose: Optional[str] = None
    first: Optional[dt.date] = None
    last: Optional[dt.date] = None


@dataclass
class CoverageInfo(Result):
    """Which data a figure is based on. Present in every analytics result (key ``coverage``)."""
    rule: str
    months: list = field(default_factory=list)            # month keys the figure used
    n_months: int = 0
    accounts: list = field(default_factory=list)          # list[AccountRef] that contribute
    skipped_months: list = field(default_factory=list)    # closed months NOT used because an account lacked data
    partial_current_month: Optional[str] = None
    notes: list = field(default_factory=list)             # English sentences (CLI, MCP tools)
    notes_msg: list = field(default_factory=list)         # the same notes for the web app: {code, params, text}, or None (see split_notes)


def note(code: str, text: str, **params: Any) -> dict:
    """A coverage note: the English ``text`` and its ``code`` + raw params for the web (``coach.i18n_msg.server_msg``). Pass the result
    in the ``notes`` of ``CoverageModel.info``; the CoverageInfo keeps the English in ``notes`` and the message in ``notes_msg``."""
    from coach.i18n_msg import server_msg
    return server_msg(code, text, **params)


def split_notes(notes) -> tuple[list, list]:
    """(English sentences, messages) of a list of notes: a :func:`note` gives both, a plain string its text and ``None`` (the web shows
    the English)."""
    texts, msgs = [], []
    for n in notes or []:
        if isinstance(n, dict):
            texts.append(n["text"])
            msgs.append(n)
        else:
            texts.append(n)
            msgs.append(None)
    return texts, msgs


def non_eur_note(n: int) -> dict:
    """The note every analytics result carries when non-EUR transactions were left out (no conversion is attempted)."""
    return note("coverage.nonEur", f"{n} non-EUR transaction(s) left out", count=n)


@dataclass(frozen=True)
class Scope:
    """Restricts an analysis to some accounts: by owner ('joint' or a member id), purpose or account uid / label."""
    owners: Optional[frozenset] = None
    purposes: Optional[frozenset] = None
    accounts: Optional[frozenset] = None

    @classmethod
    def make(cls, owners=None, purposes=None, accounts=None) -> "Scope":
        f = lambda x: None if not x else frozenset(x if not isinstance(x, str) else [x])    # noqa: E731
        return cls(f(owners), f(purposes), f(accounts))

    @property
    def is_all(self) -> bool:
        return self.owners is None and self.purposes is None and self.accounts is None

    def accepts(self, acc) -> bool:
        if self.owners is not None and (acc.owner or "") not in self.owners:
            return False
        if self.purposes is not None and (acc.purpose or "") not in self.purposes:
            return False
        if self.accounts is not None and acc.uid not in self.accounts and (acc.label or "") not in self.accounts:
            return False
        return True

    def describe(self) -> dict:
        return {"owners": sorted(self.owners) if self.owners else None,
                "purposes": sorted(self.purposes) if self.purposes else None,
                "accounts": sorted(self.accounts) if self.accounts else None}
