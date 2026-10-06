"""Account coverage model: which months each account has data for (prerequisite of every average).

Why: the accounts do not share a history window (one bank has 24 months, another 8, another 6). Dividing a 12-month
total by 12 for a category that only exists on an 8-month account understates it by a third.

Definitions
    * ``first``  = date of the account's first booked transaction.
    * ``last``   = the later of its last booked transaction and its last successful sync (an account that is synced
      but quiet is still covered), never after `today`.
    * A calendar month is COVERED by an account when ``first <= first day of the month`` and
      ``last >= last day of the month`` and the month ended before `today`. The first and the current month are
      therefore partial and never used (the same convention as the former report, now per account).
    * Per category the months used are the months covered by EVERY account that ever carries that category
      (an account carries a category if it has at least one transaction in it, whatever the date): the comparison base
      is then complete for that category. If an account that merely carried one stray transaction of the category
      shortens the base, the result says so (``accounts`` / ``months``) and is flagged ``low_confidence`` below
      `average_min_months` months.
    * Months with no covered account are never averaged over.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Optional

from coach.analytics.common import (AccountRef, CoverageInfo, Result, last_closed_month, month_end, month_key,
                                    month_start, months_between)


@dataclass
class AccountCoverage(Result):
    account: str
    label: str
    bank: Optional[str]
    owner: Optional[str]
    purpose: Optional[str]
    first: Optional[dt.date]
    last: Optional[dt.date]
    last_booked: Optional[dt.date]
    last_sync: Optional[dt.date]
    n_tx: int
    n_months: int
    months: list = field(default_factory=list)            # covered (complete) months
    partial_months: list = field(default_factory=list)    # months with some data but not complete


class CoverageModel:
    def __init__(self, accounts: dict, txs_by_account: dict, last_sync: dict, today: dt.date):
        """accounts: uid -> AccountInfo; txs_by_account: uid -> (first date, last date, count); last_sync: uid -> date."""
        self.today = today
        self._cov: dict[str, AccountCoverage] = {}
        self._months: dict[str, frozenset] = {}
        closed = last_closed_month(today)
        for uid, a in sorted(accounts.items()):
            first, last_b, n = txs_by_account.get(uid, (None, None, 0))
            sync = last_sync.get(uid)
            last = max([x for x in (last_b, sync) if x], default=None)
            if last is not None:
                last = min(last, today)
            months: list[str] = []
            partial: list[str] = []
            if first is not None and last is not None:
                for m in months_between(month_key(first), min(month_key(last), month_key(today))):
                    if first <= month_start(m) and last >= month_end(m) and m <= closed:
                        months.append(m)
                    else:
                        partial.append(m)
            self._months[uid] = frozenset(months)
            self._cov[uid] = AccountCoverage(uid, a.label, a.bank, a.owner, a.purpose, first, last, last_b, sync, n,
                                             len(months), months, partial)

    def subset(self, uids: Iterable[str]) -> "CoverageModel":
        """The same model restricted to some accounts (a person's view of the data, E14-4)."""
        keep = set(uids)
        new = object.__new__(CoverageModel)
        new.today = self.today
        new._cov = {u: c for u, c in self._cov.items() if u in keep}
        new._months = {u: m for u, m in self._months.items() if u in keep}
        return new

    # -- access
    def table(self) -> list[AccountCoverage]:
        return [self._cov[u] for u in sorted(self._cov)]

    def of(self, uid: str) -> AccountCoverage:
        return self._cov[uid]

    def covered(self, uid: str) -> frozenset:
        return self._months.get(uid, frozenset())

    def common_months(self, uids: Iterable[str]) -> list[str]:
        """Closed months fully covered by every one of `uids` (sorted). Empty when `uids` is empty."""
        uids = list(uids)
        if not uids:
            return []
        s = set(self.covered(uids[0]))
        for u in uids[1:]:
            s &= self.covered(u)
        return sorted(s)

    def closed_months(self, uids: Iterable[str]) -> list[str]:
        """Every closed month from the earliest first date of `uids` to the last closed month."""
        firsts = [self._cov[u].first for u in uids if u in self._cov and self._cov[u].first]
        if not firsts:
            return []
        lo, hi = month_key(min(firsts)), last_closed_month(self.today)
        return months_between(lo, hi) if lo <= hi else []

    def ref(self, uid: str) -> AccountRef:
        c = self._cov[uid]
        return AccountRef(uid, c.label, c.owner, c.purpose, c.first, c.last)

    def info(self, uids: Iterable[str], months: list, rule: str, notes: Optional[list] = None) -> CoverageInfo:
        uids = sorted(set(uids))
        lo = months[0] if months else None
        skipped = [m for m in self.closed_months(uids) if m not in months and (lo is None or m >= lo)]
        return CoverageInfo(rule=rule, months=list(months), n_months=len(months), accounts=[self.ref(u) for u in uids],
                            skipped_months=skipped, partial_current_month=month_key(self.today),
                            notes=list(notes or []))


def last_n(months: list[str], n: int) -> list[str]:
    return months[-n:] if n and len(months) > n else list(months)
