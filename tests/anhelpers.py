"""Builders for the analytics tests: a Dataset assembled by hand from synthetic transactions (no database), so each
test states its inputs and hand-computed outputs. Every name, merchant and amount here is invented."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from coach.analytics.common import to_cents
from coach.analytics.coverage import CoverageModel
from coach.analytics.dataset import AccountInfo, Balance, Dataset, MemorySnapshot, Tx
from coach.analytics.settings import AnalyticsSettings

TODAY = dt.date(2026, 10, 4)
_n = [0]


def D(s) -> dt.date:
    return dt.date.fromisoformat(s)


def tx(date, amount, category="food.groceries", account="a", entity=None, tags=(), event=None, key=None, desc="",
       mkey=None, type_="card", source="rule", split=None) -> Tx:
    """amount in EUR (negative = money out)."""
    _n[0] += 1
    ent = entity or category.split(".")[-1].upper()
    return Tx(key or f"t{_n[0]:05d}", D(date) if isinstance(date, str) else date, to_cents(amount), category, source,
              frozenset(tags), event, ent, ent, mkey or ent.upper(), account, type_, desc or ent.lower(),
              split, None)


def account(uid="a", label="Main", owner="joint", purpose="main", bank="Test Bank") -> AccountInfo:
    return AccountInfo(uid, label, bank, owner, purpose, "EUR")


def make_ds(txs, accounts=None, today=TODAY, settings=None, balances=None, memory=None, last_sync=None, meta=None,
            consents=None, history: dict | None = None) -> Dataset:
    """`history`: uid -> (first, last) date overrides for the coverage model (an account whose history starts before
    its first listed transaction, or that was synced after its last one)."""
    accounts = accounts or [account()]
    accs = {a.uid: a for a in accounts}
    stats = {}
    for t in txs:
        f, l_, n = stats.get(t.account, (t.date, t.date, 0))
        stats[t.account] = (min(f, t.date), max(l_, t.date), n + 1)
    for uid, (f, l_) in (history or {}).items():
        f0, l0, n = stats.get(uid, (None, None, 0))
        stats[uid] = (D(f) if f else f0, D(l_) if l_ else l0, n)
    sync = {k: D(v) for k, v in (last_sync or {}).items()}
    bal = {}
    for uid, v in (balances or {}).items():
        amount, as_of = v if isinstance(v, tuple) else (v, today)
        bal[uid] = Balance(uid, to_cents(amount), "CLBD", f"{as_of}T08:00:00+00:00", D(as_of) if isinstance(as_of, str) else as_of)
    txs = sorted(txs, key=lambda t: (t.date, t.key))
    return Dataset(today, settings or AnalyticsSettings(), accs, txs, CoverageModel(accs, stats, sync, today),
                   memory or MemorySnapshot(), bal, meta or {}, consents or [], [])


def monthly(start: str, n: int, amount, category="food.groceries", day=None, account="a", entity=None, **kw) -> list[Tx]:
    """n monthly transactions starting at `start` (same day of the month, clamped)."""
    from coach.analytics.common import add_months
    s = D(start)
    return [tx(add_months(s, i), amount, category, account, entity, **kw) for i in range(n)]


def consent(bank, valid_until, status="ok", sid="s1"):
    return SimpleNamespace(session_id=sid, bank=bank, country="FR", valid_until=valid_until, days_left=None,
                           status=status, stored_status="active", live_status=None, live_checked_at=None, accounts=1)
