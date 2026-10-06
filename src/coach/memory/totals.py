"""Manual assets and liabilities totals (E3-8): what the bank sync cannot see, for the net worth of E9.

Plain data, no UI. ``assets`` and ``liabilities`` are the figures written by hand in assets.yaml / liabilities/;
accounts synced from the banks are NOT included (their balances come from the database)."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Optional

from coach.memory import qgen
from coach.memory.store import MemoryStore


def manual_totals(store: MemoryStore, today: Optional[dt.date] = None, asset_stale_months: int = 3,
                  stale_months: int = 6) -> dict:
    today = today or dt.date.today()
    a_cut, l_cut = qgen.months_ago(today, asset_stale_months), qgen.months_ago(today, stale_months)
    by_kind: dict[str, float] = defaultdict(float)
    items, unknown, stale = [], [], []
    total = 0.0
    for a in store.assets():
        v = a.amount
        if v is None:
            unknown.append(a.id)
            continue
        total += v
        by_kind[a.kind] += v
        is_stale = a.as_of is None or a.as_of < a_cut
        if is_stale:
            stale.append(a.id)
        items.append({"id": a.id, "kind": a.kind, "value": v, "as_of": a.as_of.isoformat() if a.as_of else None,
                      "stale": is_stale})
    liab, l_unknown, l_stale, monthly = [], [], [], 0.0
    owed = 0.0
    for rel, m in store.liabilities():
        monthly += m.monthly_payment or 0
        if m.outstanding is None:
            l_unknown.append(m.id)
            continue
        owed += m.outstanding
        st = m.outstanding_as_of is None or m.outstanding_as_of < l_cut
        if st:
            l_stale.append(m.id)
        liab.append({"id": m.id, "kind": m.kind, "outstanding": m.outstanding,
                     "as_of": m.outstanding_as_of.isoformat() if m.outstanding_as_of else None, "stale": st})
    return {
        "as_of": today.isoformat(),
        "assets": {"total": round(total, 2), "by_kind": {k: round(v, 2) for k, v in sorted(by_kind.items())},
                   "items": items, "unknown_value": unknown, "stale": stale},
        "liabilities": {"outstanding_total": round(owed, 2), "monthly_payments_total": round(monthly, 2),
                        "items": liab, "unknown_outstanding": l_unknown, "stale": l_stale},
        "net_manual": round(total - owed, 2),
        "complete": not (unknown or l_unknown),
        "note": "manual figures only; synced bank accounts are not included",
    }
