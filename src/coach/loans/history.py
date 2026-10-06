"""Net worth history (E9-4): the ``net_worth_history`` table (migration 0016).

``record_snapshot`` stores the net worth of a day (called at each scheduled run and by ``coach networth --record``): one row per day,
replaced when run again the same day. ``backfill`` rebuilds month-end figures for the past, and only where they can be known:

    bank accounts   balance(d) = latest balance - the transactions dated after d (up to the balance date), only when the account's data
                    start on or before d and the balance is not older than d; else UNKNOWN for that month
    manual assets   the recorded value counts from its own ``as_of`` date on (nothing is invented for earlier months); an asset
                    whose value or date is missing is UNKNOWN
    liabilities     the capital of the amortization schedule on that date (0 before the loan starts, 0 after its last instalment);
                    a loan whose schedule is not computable is UNKNOWN

Back-fill rows never replace a snapshot of the same month, and are recomputed each time. Unknown items are counted in ``n_unknown``
and listed in ``detail``; a month's figure is then "the known part".
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from coach.analytics.common import add_months_key, last_closed_month, month_end, month_key, money_str
from coach.loans import networth as NW, schedule as S

COLS = ("as_of", "month", "source", "net_worth_c", "assets_c", "liabilities_c", "cash_c", "savings_c", "investments_c",
        "real_estate_c", "vehicles_c", "other_c", "n_unknown", "complete", "detail", "created_at")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _put(con, as_of: dt.date, source: str, assets_by_cat: dict, liabilities_c: int, unknown: list, by_owner: dict, non_booked: int = 0) -> None:
    assets = sum(assets_by_cat.get(c, 0) for c in NW.CATEGORIES)
    detail = {"unknown": unknown, "by_owner": {o: {k: v for k, v in x.items()} for o, x in by_owner.items()}, "non_booked_accounts": non_booked}
    con.execute(f"INSERT OR REPLACE INTO net_worth_history({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                (as_of.isoformat(), month_key(as_of), source, assets - liabilities_c, assets, liabilities_c,
                 *(assets_by_cat.get(c, 0) for c in NW.CATEGORIES), len(unknown), int(not unknown),
                 json.dumps(detail, ensure_ascii=False), _now()))


def record_snapshot(con, nw: NW.NetWorth) -> None:
    """Store `nw` as the snapshot of its day (replacing the one already stored for that day)."""
    unknown = [{k: u[k] for k in ("type", "id", "label", "reason")} for u in nw.unknown]
    _put(con, nw.as_of, "snapshot", {c: nw.by_category_c.get(c, 0) for c in NW.CATEGORIES}, nw.liabilities_c, unknown,
         {o: v for o, v in nw.by_owner.items()}, sum(1 for c in nw.components if c.type == "account" and c.balance_type and c.balance_type not in NW.BOOKED))
    con.commit()


def _balance_on(ds, uid: str, day: dt.date) -> Optional[int]:
    b = ds.balance_of(uid)
    cov = ds.coverage.of(uid)
    if b is None or cov.first is None or day > b.as_of or day < cov.first - dt.timedelta(days=1):
        return None
    after = sum(t.amount_c for t in ds.whole if t.account == uid and day < t.date <= b.as_of)
    return b.amount_c - after


def month_figures(ds, day: dt.date, schedules: dict) -> tuple[dict, int, list, dict]:
    """(assets by category, liabilities, unknown items, by owner) of the month-end `day`, from the data available now."""
    cats = {c: 0 for c in NW.CATEGORIES}
    liab = 0
    unknown: list[dict] = []
    owners: dict[str, dict] = {}

    def own(o):
        return owners.setdefault(o, {"assets_c": 0, "liabilities_c": 0, "net_worth_c": 0, "n_unknown": 0})

    for acc in ds.accounts_in(None):
        v = _balance_on(ds, acc.uid, day)
        o = own(acc.owner or NW.UNASSIGNED)
        if v is None:
            unknown.append({"type": "account", "id": acc.uid, "label": acc.label, "reason": "balance not known on that date"})
            o["n_unknown"] += 1
        else:
            cats[NW.account_category(acc)] += v
            o["assets_c"] += v
    for a in ds.memory.assets:
        if a.connected:
            continue
        o = own(NW._holder(a))
        label = a.description or a.id
        if a.amount is None:
            unknown.append({"type": "asset", "id": a.id, "label": label, "reason": "no value recorded"})
            o["n_unknown"] += 1
        elif a.as_of is None or day < a.as_of:
            unknown.append({"type": "asset", "id": a.id, "label": label, "reason": "value only known from " + (str(a.as_of) if a.as_of else "an unknown date")})
            o["n_unknown"] += 1
        else:
            v = int(round(a.amount * 100))
            cats[NW.category_of_asset(a.kind)] += v
            o["assets_c"] += v
    for _rel, lb in ds.memory.liabilities:
        sch = schedules.get(lb.id)
        o = own(NW._holder(lb))
        label = lb.lender or lb.id
        if sch is not None and sch.status == "not_applicable":
            continue                                          # a lease owes no capital
        if sch is None or sch.status != "computed":
            unknown.append({"type": "liability", "id": lb.id, "label": label, "reason": "no amortization schedule"})
            o["n_unknown"] += 1
            continue
        if sch.mode == "from_principal" and lb.start_date and day < lb.start_date:
            continue
        if sch.mode == "from_outstanding" and (lb.outstanding_as_of is None or day < lb.outstanding_as_of):
            bal = None                                       # the table starts at the declared capital: nothing is known before its date
        else:
            bal = S.balance_on(sch, day)
        if bal is None:
            unknown.append({"type": "liability", "id": lb.id, "label": label, "reason": "capital not known on that date"})
            o["n_unknown"] += 1
            continue
        liab += bal
        o["liabilities_c"] += bal
    for o in owners.values():
        o["net_worth_c"] = o["assets_c"] - o["liabilities_c"]
    return cats, liab, unknown, owners


def backfill(con, ds, today: dt.date, schedules: Optional[dict] = None, max_months: int = 60) -> int:
    """Rebuild the month-end rows of the past months (no snapshot of that month). Returns the number of months written."""
    firsts = [ds.coverage.of(u).first for u in ds.accounts if ds.coverage.of(u).first]
    if not firsts:
        return 0
    schedules = schedules if schedules is not None else {lb.id: S.compute(lb, today) for _r, lb in ds.memory.liabilities}
    last = last_closed_month(today)
    first = max(month_key(min(firsts)), add_months_key(last, -(max_months - 1)))
    have = {r[0] for r in con.execute("SELECT DISTINCT month FROM net_worth_history WHERE source='snapshot'")}
    non_booked = sum(1 for u in ds.accounts if (b := ds.balance_of(u)) is not None and b.type not in NW.BOOKED)
    n, m = 0, first
    while m <= last:
        if m not in have:
            cats, liab, unknown, owners = month_figures(ds, month_end(m), schedules)
            _put(con, month_end(m), "backfill", cats, liab, unknown, owners, non_booked)
            n += 1
        m = add_months_key(m, 1)
    con.commit()
    return n


def series(con, months: Optional[int] = None) -> list[dict]:
    """One point per month: the latest snapshot of the month, else its back-fill. Oldest first."""
    rows = con.execute(f"SELECT {', '.join(COLS)} FROM net_worth_history ORDER BY month, (source='snapshot'), as_of").fetchall()
    best: dict[str, dict] = {}
    for r in rows:                                    # later rows of a month win; snapshot sorts after backfill
        d = dict(zip(COLS, r))
        best[d["month"]] = d
    out = []
    for m in sorted(best):
        d = best[m]
        try:
            detail = json.loads(d["detail"] or "{}")
        except ValueError:
            detail = {}
        nb = detail.get("non_booked_accounts", 0)
        out.append({"month": m, "as_of": d["as_of"], "non_booked_accounts": nb,
                    "caveat": (f"{nb} account balance(s) are available / expected balances (ITAV, XPCD...), not booked ones: pending items can make "
                               "this month differ slightly from the bank's own figure") if nb else None, "source": d["source"], "net_worth": money_str(d["net_worth_c"]),
                    "assets": money_str(d["assets_c"]), "liabilities": money_str(d["liabilities_c"]),
                    "by_category": {c: money_str(d[f"{c}_c"]) for c in NW.CATEGORIES}, "n_unknown": d["n_unknown"],
                    "complete": bool(d["complete"]), "unknown": detail.get("unknown", [])})
    for prev, cur in zip(out, out[1:]):               # an item known from this month on: the jump is not a change in wealth
        was = {(u["type"], u["id"]): u for u in prev["unknown"]}
        now = {(u["type"], u["id"]) for u in cur["unknown"]}
        cur["newly_counted"] = [{"type": k[0], "id": k[1], "label": u["label"], "reason_before": u["reason"]} for k, u in was.items() if k not in now]
    if out:
        out[0].setdefault("newly_counted", [])
    return out[-months:] if months else out
