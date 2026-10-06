"""The children's money (E14-5): pocket money, extra top-ups, spending, the balance trend, and "pocket money versus extra".

Everything is computed from the transactions ATTRIBUTED to the child (:mod:`coach.household.attribution`: the child's own accounts, a card
on a shared account, a rule or a manual reassignment) and from the balances of the accounts the child OWNS.

* **Inflows** are the credits that are transfers or income (a card refund is negative spending, not an inflow). A credit that sits in a
  transfer link (E1-12 / E14-7: the parent's debit paired with the child's credit) is "an internal transfer within the household": excluded
  from the household's income and spending, but it IS the child's income here, with its SOURCE (the member who owns the paying account,
  or ``joint``). Without a link, a member named in the description gives the source; otherwise ``other`` (income.*) or ``unknown``.
* **Pocket money** is a REGULAR inflow: three or more credits of nearly the same amount (within 5 %, at least 0.50 EUR) from the same source
  at a weekly, fortnightly or monthly rhythm. The amount a child is meant to get may also be declared (``pocket_money`` on the member in
  household.yaml): a credit of about that amount then counts as pocket money even if the rhythm is not yet clear. Everything else is an
  **extra top-up**, listed with its source.
* **Balance trend**: the balance of the child's own accounts at each month end, rebuilt BACKWARDS from the newest balance snapshot with the
  transactions after it. It is an estimate (``estimated: true``) and a month before the first known transaction is left out.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from typing import Optional

from coach.analytics.common import (add_months, add_months_key, last_closed_month, median_c, money_str, month_end, month_key, months_between, pct)
from coach.analytics.dataset import Dataset, is_income, is_spending, is_transfer
from coach.household.people import JOINT, People

WINDOW_MONTHS = 6
OWN_MOVE_TYPES = frozenset({"savings_internal", "fx_exchange"})   # moves between the child's own pockets / vaults and currency exchanges: not money received
CADENCES = (("weekly", 7, 5, 9), ("fortnightly", 14, 12, 16), ("monthly", 30, 25, 37))   # name, nominal days, min, max interval


def _inflow(t) -> bool:
    return t.amount_c > 0 and (is_transfer(t.category) or is_income(t.category))


def _sources(con, people: People, keys: list[str]) -> dict:
    """in-leg tx_key -> the member (or 'joint') who owns the account the money left, for the linked transfers."""
    if not keys:
        return {}
    out: dict = {}
    marks = ",".join("?" * len(keys))
    sql = ("SELECT l.in_tx_key, a.owner FROM transfer_links l JOIN transactions o ON o.tx_key=l.out_tx_key "
           f"LEFT JOIN accounts a ON a.uid=o.account_uid WHERE l.in_tx_key IN ({marks})")
    try:
        for k, owner in con.execute(sql, keys):
            out[k] = people.resolve(owner) or "unknown"
    except Exception:                                                  # noqa: BLE001 - no transfer table / no connection: no links
        pass
    return out


def _counterparties(con, keys: list[str]) -> dict:
    """tx_key -> the counterparty the bank parser read (a name for a transfer), local only."""
    out: dict = {}
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        try:
            for k, c in con.execute(f"SELECT tx_key, counterparty FROM tx_parse_meta WHERE tx_key IN ({','.join('?' * len(chunk))})", chunk):
                if c:
                    out[k] = c
        except Exception:                                              # noqa: BLE001 - no parser metadata: the description alone
            return out
    return out


def source_of(t, linked: dict, people: People, member: str = "", counterparty: str = "") -> tuple[str, bool]:
    """(source, linked): who the money came from. A link gives the owner of the paying account; otherwise the members a text names (the
    description and the parsed counterparty): one other member is the source, two or more adults together are ``joint``, only the child
    themselves is ``self`` (a move between their own accounts)."""
    if t.key in linked:
        return linked[t.key], True
    named = people.mentioned(f"{t.desc} {counterparty}")
    others = sorted(named - {member})
    if len(others) == 1:
        return others[0], False
    if len(others) > 1:
        return (JOINT if all(people.role(o) == "adult" for o in others) else others[0]), False
    if member in named:
        return "self", False
    return ("other" if is_income(t.category) else "unknown"), False


def _cluster(items: list) -> list[list]:
    """Group (date, cents, key) items whose amounts are within max(50 cents, 5 %) of the group's first amount."""
    groups: list[list] = []
    for it in sorted(items, key=lambda x: x[1]):
        for g in groups:
            base = g[0][1]
            if abs(it[1] - base) <= max(50, int(base * 0.05)):
                g.append(it)
                break
        else:
            groups.append([it])
    return groups


def _cadence(dates: list) -> Optional[str]:
    ds = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    if len(gaps) < 2:
        return None
    gaps_sorted = sorted(gaps)
    med = gaps_sorted[len(gaps_sorted) // 2]
    for name, _, lo, hi in CADENCES:
        if lo <= med <= hi and sum(1 for g in gaps if lo <= g <= hi) / len(gaps) >= 0.6:
            return name
    return None


def detect_pocket_money(items: list, member: str) -> list[dict]:
    """items: [(date, cents, tx_key, source)] -> the regular series (see the module doc)."""
    by_source: dict = defaultdict(list)
    for it in items:
        by_source[it[3]].append(it)
    series = []
    for source, its in sorted(by_source.items()):
        for g in _cluster([(d, c, k) for d, c, k, _ in its]):
            if len(g) < 3:
                continue
            cad = _cadence([d for d, _, _ in g])
            if cad is None:
                continue
            g = sorted(g, key=lambda x: (x[0], x[2]))
            amount = median_c([c for _, c, _ in g])
            last = g[-1][0]
            days = {"weekly": 7, "fortnightly": 14}.get(cad)
            nxt = last + dt.timedelta(days=days) if days else add_months(last, 1)
            sid = "pm_" + hashlib.sha1(f"{member}|{source}|{cad}|{amount}".encode()).hexdigest()[:8]
            series.append({"id": sid, "source": source, "amount_c": amount, "cadence": cad, "count": len(g), "first": g[0][0],
                           "last": last, "next_expected": nxt, "day": last.day if cad == "monthly" else last.isoweekday(),
                           "keys": [k for _, _, k in g]})
    return series


def _owned_accounts(ds: Dataset, member: str) -> list:
    people = ds.memory.people
    return [a for a in ds.accounts.values() if people is not None and people.resolve(a.owner) == member]


def balance_trend(ds: Dataset, member: str, months: list[str]) -> dict:
    """Month-end balances of the member's own accounts, rebuilt backwards from the newest balance with the later transactions."""
    owned = _owned_accounts(ds, member)
    known = [a for a in owned if ds.balance_of(a.uid)]
    unknown = [a.label for a in owned if not ds.balance_of(a.uid)]
    if not known:
        return {"current": None, "as_of": None, "trend": [], "unknown": unknown, "estimated": True}
    by_acc: dict = defaultdict(list)
    for t in ds.txs:
        by_acc[t.account].append(t)
    current = sum(ds.balance_of(a.uid).amount_c for a in known)
    as_of = max(ds.balance_of(a.uid).as_of for a in known)
    trend = []
    for m in months:
        end = month_end(m)
        if end >= as_of:
            continue
        total, ok = 0, True
        for a in known:
            b = ds.balance_of(a.uid)
            first = ds.stats.get(a.uid, (None,))[0] if ds.stats else None
            if first is not None and first > end:                   # no history that far back for this account
                ok = False
                break
            after = sum(t.amount_c for t in by_acc.get(a.uid, ()) if end < t.date <= b.as_of)
            total += b.amount_c - after
        if ok:
            trend.append({"month": m, "end_balance_c": total})
    trend.append({"month": month_key(as_of), "end_balance_c": current, "as_of": as_of})
    return {"current_c": current, "as_of": as_of, "trend": trend, "unknown": unknown, "estimated": True}


def kid_report(ds: Dataset, con, member: str, months: int = WINDOW_MONTHS) -> dict:
    """The children's-money report of one member (usually a child). `ds` is the WHOLE-household dataset."""
    people = ds.memory.people
    if people is None or member not in people.by_id:
        raise ValueError(f"{member!r} is not a household member")
    closed = last_closed_month(ds.today)
    win = months_between(add_months_key(closed, -(max(1, months) - 1)), closed)
    mine = [t for t in ds.whole if t.person == member]
    inflows_all = [t for t in mine if _inflow(t)]
    in_win = [t for t in inflows_all if t.month in win]
    linked = _sources(con, people, [t.key for t in in_win])
    cps = _counterparties(con, [t.key for t in in_win if t.key not in linked])
    rows, own_moves = [], []
    for t in in_win:
        if t.type in OWN_MOVE_TYPES:
            own_moves.append(t)
            continue
        src, is_linked = source_of(t, linked, people, member, cps.get(t.key, ""))
        if src == "self":                                           # the child's own money moving between their own accounts: not received
            own_moves.append(t)
            continue
        rows.append((t, src, is_linked))
    series = detect_pocket_money([(t.date, t.amount_c, t.key, src) for t, src, _ in rows], member)
    pocket_keys = {k for s in series for k in s["keys"]}
    declared = next((m.pocket_money for m in people.members if m.id == member), None)
    declared_d = None
    if declared is not None:
        tol = max(50, int(declared.amount * 100 * 0.05))
        declared_d = {"amount_c": round(declared.amount * 100), "period": declared.period, "day": declared.day}
        for t, src, _ in rows:
            if t.key not in pocket_keys and abs(t.amount_c - round(declared.amount * 100)) <= tol:
                pocket_keys.add(t.key)
        declared_d["matches"] = sum(1 for t, _, _ in rows if t.key in pocket_keys)
    pocket_c = sum(t.amount_c for t, _, _ in rows if t.key in pocket_keys)
    extra_rows = [(t, src, lk) for t, src, lk in rows if t.key not in pocket_keys]
    extra_c = sum(t.amount_c for t, _, _ in extra_rows)
    by_source: dict = defaultdict(int)
    for t, src, _ in extra_rows:
        by_source[src] += t.amount_c
    total_in = pocket_c + extra_c
    spend = [t for t in mine if t.month in win and (is_spending(t.category) or t.category == "income.refund")]
    spent_c = -sum(t.amount_c for t in spend)
    cats: dict = defaultdict(lambda: [0, 0])
    per_month: dict = defaultdict(int)
    for t in spend:
        cats[t.category][0] += -t.amount_c
        cats[t.category][1] += 1
        per_month[t.month] += -t.amount_c
    cur = month_key(ds.today)
    to_date = -sum(t.amount_c for t in mine if t.month == cur and (is_spending(t.category) or t.category == "income.refund"))
    owned = _owned_accounts(ds, member)
    bal = balance_trend(ds, member, win)
    n = len(win)
    notes = []
    if not owned:
        notes.append("the child owns no account: only the transactions attributed to them by a rule or by hand are counted")
    if bal["unknown"]:
        notes.append("no balance known for: " + ", ".join(bal["unknown"]))
    return {
        "member": member, "as_of": ds.today, "window": {"months": win, "from": win[0] + "-01", "to": month_end(win[-1])},
        "accounts": [{"account": a.uid, "label": a.label, "balance_c": ds.balance_of(a.uid).amount_c if ds.balance_of(a.uid) else None,
                      "balance_as_of": ds.balance_of(a.uid).as_of if ds.balance_of(a.uid) else None} for a in owned],
        "balance": bal,
        "pocket_money": {"series": series, "declared": declared_d, "total_c": pocket_c,
                         "monthly_equivalent_c": pocket_c // n if n else 0},
        "extra_topups": {"total_c": extra_c, "count": len(extra_rows), "by_source": dict(by_source),
                         "items": [{"tx_key": t.key, "date": t.date, "amount_c": t.amount_c, "source": src, "linked": lk,
                                    "category": t.category} for t, src, lk in sorted(extra_rows, key=lambda r: (r[0].date, r[0].key), reverse=True)]},
        "inflow": {"total_c": total_in, "pocket_c": pocket_c, "extra_c": extra_c},
        "own_moves": {"count": len(own_moves), "total_c": sum(t.amount_c for t in own_moves)},     # credits left out: pocket / vault / exchange moves of the child
        "ratio": {"pocket_share": pct(pocket_c, total_in), "extra_share": pct(extra_c, total_in),
                  "pocket_to_extra": pct(pocket_c, extra_c) if extra_c else None},
        "spending": {"total_c": spent_c, "monthly_avg_c": spent_c // n if n else 0, "this_month_to_date_c": to_date,
                     "by_category": [{"category": c, "total_c": v[0], "n": v[1], "share": pct(v[0], spent_c)}
                                     for c, v in sorted(cats.items(), key=lambda kv: -kv[1][0])],
                     "by_month": [{"month": m, "total_c": per_month.get(m, 0)} for m in win]},
        "notes": notes,
    }


def to_json(report: dict) -> dict:
    """Money fields (``*_c``) rendered as decimal strings under the key without the suffix; dates as ISO strings."""
    def conv(v):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if isinstance(k, str) and k.endswith("_c") and (isinstance(x, int) or x is None) and not isinstance(x, bool):
                    out[k[:-2]] = money_str(x)
                elif k == "by_source":
                    out[k] = {str(a): money_str(b) for a, b in x.items()}
                elif k == "keys":
                    out["evidence"] = list(x)
                else:
                    out[k] = conv(x)
            return out
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        return v
    return conv(report)


def children_overview(ds: Dataset, con, months: int = WINDOW_MONTHS) -> list[dict]:
    people = ds.memory.people
    if people is None:
        return []
    return [to_json(kid_report(ds, con, c, months)) for c in people.children()]
