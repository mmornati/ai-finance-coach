"""monthly_review(month) and explain_spike(category | group | account, month) (E7-3, E7-4).

Both compose the existing analytics; the only new rule is how a month is compared with "usual".

USUAL (the baseline) of a category = the average net spending, one-offs / capital / exclude_from_averages left out, over the
last ``average_window_months`` months BEFORE the reviewed month that are fully covered by every major account carrying the
category (the coverage model). Fewer than ``average_min_months`` such months = ``low_confidence``; none = no baseline (the
category is listed apart, never compared with a made-up figure). The reviewed month itself must be covered by those accounts
to count as complete (``month_complete``).

monthly_review: the month's cash flow (income, spending, saved, debt service, drawn from savings), the biggest movers against
usual with their evidence (transactions, recurring series, anomalies, price changes), the one-offs listed apart, the budgets as
they stood at the end of the month, and the forecast flags as of today.

explain_spike: the month's spending in the scope split by transaction class - one-off (tagged), recurring (a detected
series), new merchant (first ever payment in that month), habitual - and by merchant, each merchant's change against its own
usual, the top transactions, the same month a year before when covered, and the coverage notes. Per merchant:
``delta = month - usual`` and the class sums of those deltas add up to the scope's excess (to the cent, rounding aside).
"""
from __future__ import annotations

import re
from typing import Optional

from coach.analytics.anomalies import detect_anomalies
from coach.analytics.averages import spending_txs
from coach.analytics.budgets import budget_status
from coach.analytics.cashflow import cashflow
from coach.analytics.common import (add_months_key, div_cents, last_closed_month, money_str, month_end, month_key,
                                    month_start)
from coach.analytics.coverage import last_n
from coach.analytics.dataset import Dataset
from coach.analytics.forecast import forecast
from coach.analytics.pricechanges import price_changes
from coach.analytics.recurring import RecurringResult, detect_recurring

MOVER_MIN_C = 2000              # a category moves "for real" from 20 EUR of difference
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _m(c: Optional[int]) -> Optional[str]:
    return money_str(c)


def _pct(delta: int, base: int) -> Optional[float]:
    return round(delta / base, 4) if base else None


def _check_month(ds: Dataset, month: Optional[str]) -> str:
    month = month or last_closed_month(ds.today)
    if not MONTH_RE.match(month):
        raise ValueError("month must look like 2026-09")
    if month > month_key(ds.today):
        raise ValueError("that month has not started yet")
    return month


def _window(ds: Dataset, cat: str) -> int:
    from coach.analytics.averages import is_lumpy
    s = ds.settings
    return max(s.average_window_months, s.lumpy_min_months) if is_lumpy(cat, s.lumpy_categories) else s.average_window_months


def baseline_months(ds: Dataset, carriers: list, month: str, window: int) -> list[str]:
    return last_n([m for m in ds.coverage.common_months(carriers) if m < month], window)


# ---------------------------------------------------------------- monthly_review

def monthly_review(ds: Dataset, month: Optional[str] = None, recurring: Optional[RecurringResult] = None,
                   top: int = 8) -> dict:
    s = ds.settings
    month = _check_month(ds, month)
    cur = month_key(ds.today)
    rec = recurring or detect_recurring(ds)
    sp = spending_txs(ds)
    by_cat: dict[str, list] = {}
    for t in sp:
        by_cat.setdefault(t.category, []).append(t)
    notes: list[str] = []

    # -- cash flow of the month
    cf = cashflow(ds, months=1, end=month, breakdown=False)
    mf = cf.household.months[0] if cf.household.months else None
    flow = None
    if mf is not None:
        flow = mf.to_dict()
        for k in ("n_tx",):
            flow.pop(k, None)
        sav = {u for u, a in ds.accounts.items() if a.purpose == "savings"}
        inflow = [t for t in ds.txs if t.month == month and t.account in sav and t.category == "transfer.internal"]
        flow["deposited_to_savings_accounts"] = _m(sum(t.amount_c for t in inflow if t.amount_c > 0))
        flow["drawn_from_savings_accounts"] = _m(-sum(t.amount_c for t in inflow if t.amount_c < 0))
        flow["missing_accounts"] = [uid for uid, a in sorted(ds.accounts.items()) if a.label in set(mf.missing_accounts)]
        if not mf.complete:
            notes.append(f"the month is not fully covered by every account ({len(mf.missing_accounts)} lack data): figures are partial")
    else:
        notes.append("no data for this month")
    if month == cur:
        notes.append("the month is still running: figures are month to date and 'usual' is a full month")

    # -- categories against usual
    rows, nobase = [], []
    for cat, txs in sorted(by_cat.items()):
        carriers = ds.carrying_accounts(cat)
        covered = set(ds.coverage.common_months(carriers))
        bm = baseline_months(ds, carriers, month, _window(ds, cat))
        bset = set(bm)
        in_m = [t for t in txs if t.month == month]
        run = -sum(t.amount_c for t in in_m if not t.is_one_off)
        one = -sum(t.amount_c for t in in_m if t.is_one_off)
        usual = div_cents(-sum(t.amount_c for t in txs if t.month in bset and not t.is_one_off), len(bm)) if bm else None
        if not in_m and (usual is None or usual < MOVER_MIN_C):
            continue
        row = {"category": cat, "spent": _m(run), "one_off": _m(one), "n_tx": len(in_m),
               "month_complete": month in covered, "_run": run}
        if usual is None:
            row["baseline"] = None
            nobase.append(row)
            continue
        delta = run - usual
        row.update({"usual": _m(usual), "delta": _m(delta), "pct": _pct(delta, usual), "usual_months": len(bm),
                    "low_confidence": len(bm) < s.average_min_months, "_delta": delta, "_usual": usual})
        rows.append(row)
    movers = sorted((r for r in rows if abs(r["_delta"]) >= MOVER_MIN_C), key=lambda r: (-abs(r["_delta"]), r["category"]))[:top]
    anm = detect_anomalies(ds, recurring=rec)
    chg = price_changes(ds, recurring=rec)
    for r in movers:
        cat = r["category"]
        r["direction"] = "up" if r["_delta"] > 0 else "down"
        tops = sorted((t for t in by_cat[cat] if t.month == month and not t.is_one_off), key=lambda t: (t.amount_c, t.key))[:3]
        r["evidence"] = [t.key for t in tops]
        r["series"] = [x.id for x in rec.series if x.category == cat and x.status == "active"
                       and any(o.date.isoformat().startswith(month) for o in x.occurrences)][:5]
        r["anomalies"] = [a.id for a in anm.anomalies if a.subject == cat and str(a.period).startswith(month)][:3]
        r["price_changes"] = [c.id for c in chg.changes if c.category == cat and c.date.isoformat().startswith(month)][:3]
    month_total = sum(r["_run"] for r in rows + nobase)
    usual_total = sum(r["_usual"] for r in rows)
    comparable = sum(r["_run"] for r in rows)
    for r in rows + nobase:
        for k in ("_delta", "_run", "_usual"):
            r.pop(k, None)
    oneoffs = sorted((t for t in sp if t.month == month and t.is_one_off), key=lambda t: (t.amount_c, t.key))
    anomalies_month = [{"id": a.id, "type": a.type, "severity": a.severity, "subject": a.subject, "amount": _m(a.amount_c),
                        "evidence": a.evidence[:3]} for a in anm.anomalies if str(a.period).startswith(month)][:6]

    # -- budgets as they stood at the end of the month
    as_of = month_end(month) if month < cur else ds.today
    bs = budget_status(ds, as_of=as_of, recurring=rec)
    budgets = [{"id": b.id, "target": b.target, "monthly": _m(b.monthly_c), "available": _m(b.available_c), "spent": _m(b.spent_c),
                "remaining": _m(b.remaining_c), "percent_used": b.percent_used, "status": ("over" if b.spent_c > b.available_c else "ok")
                if month < cur else b.status, "flags": b.flags} for b in bs.budgets]

    # -- forecast flags (from today, not from the reviewed month)
    fc = forecast(ds, days=60, points=False, recurring=rec).household
    forecast_out = {"as_of": ds.today, "start_balance": _m(fc.start_balance_c), "min_balance": _m(fc.min_balance_c),
                    "min_date": fc.min_date, "first_negative": fc.first_negative, "first_at_risk": fc.first_at_risk,
                    "flags": fc.flags, "milestones": [{"days": x.days, "balance": _m(x.balance_c), "low": _m(x.low_c)} for x in fc.milestones]}
    if month != cur and month != last_closed_month(ds.today):
        notes.append("the forecast looks ahead from today, not from the reviewed month")
    cov = ds.coverage.info(sorted(ds.accounts), [month] if mf and mf.complete else [],
                           "category baselines: the months before the reviewed month that every major account of the category covers",
                           notes)
    return {"month": month, "partial_month": month == cur, "cash_flow": flow,
            "against_usual": {"month_spending_ex_one_offs": _m(month_total), "comparable_month_spending": _m(comparable),
                              "usual_for_those_categories": _m(usual_total), "delta": _m(comparable - usual_total),
                              "pct": _pct(comparable - usual_total, usual_total), "categories_without_baseline": len(nobase)},
            "movers": [{k: v for k, v in r.items()} for r in movers],
            "categories_without_baseline": sorted(nobase, key=lambda r: r["category"])[:5],
            "one_offs": {"total": _m(-sum(t.amount_c for t in oneoffs)), "n": len(oneoffs),
                         "items": [{"ref": t.key, "date": t.date, "amount": _m(t.amount_c), "category": t.category,
                                    "tags": sorted(t.tags), "entity": t.entity} for t in oneoffs[:8]]},
            "anomalies": anomalies_month,
            "budgets": {"as_of": as_of, "counts": {"over": sum(1 for b in budgets if b["status"] == "over"),
                                                   "at_risk": sum(1 for b in budgets if b["status"] == "at_risk"),
                                                   "ok": sum(1 for b in budgets if b["status"] == "ok")}, "items": budgets,
                        "unbudgeted_top": bs.unbudgeted[:5]},
            "forecast": forecast_out,
            "coverage": cov.to_dict(), "notes": notes}


# ---------------------------------------------------------------- explain_spike

def _scope_txs(ds: Dataset, sp: list, category: Optional[str], account: Optional[str]) -> tuple[list, list, str]:
    """(transactions of the scope, accounts that carry it, description)."""
    if category:
        from coach.classify import rules as R
        cat = category.strip().rstrip(".")
        leaves = [c for c in R.CATEGORIES if c == cat or c.startswith(cat + ".")]
        if not leaves:
            raise ValueError(f"unknown category or group {category!r}")
        txs = [t for t in sp if t.category in set(leaves)]
        carriers = sorted({a for c in leaves for a in ds.carrying_accounts(c)} or {t.account for t in txs})
        return txs, carriers, cat
    acc = ds.resolve_account(account or "")
    if acc is None:
        raise ValueError("unknown account")
    return [t for t in sp if t.account == acc.uid], [acc.uid], acc.uid


def explain_spike(ds: Dataset, *, category: Optional[str] = None, account: Optional[str] = None, month: Optional[str] = None,
                  recurring: Optional[RecurringResult] = None, top: int = 8) -> dict:
    if bool(category) == bool(account):
        raise ValueError("give exactly one of category (a category or a group) or account")
    month = _check_month(ds, month)
    rec = recurring or detect_recurring(ds)
    sp = spending_txs(ds)
    txs, carriers, scope_name = _scope_txs(ds, sp, category, account)
    covered = set(ds.coverage.common_months(carriers))
    bm = last_n([m for m in sorted(covered) if m < month], ds.settings.average_window_months)
    bset, n = set(bm), len(bm)
    in_m = [t for t in txs if t.month == month]
    members = {o.tx_key: x.id for x in rec.series for o in x.occurrences}
    first_seen: dict[str, object] = {}
    for t in sp:
        if t.amount_c < 0:
            f = first_seen.get(t.entity)
            first_seen[t.entity] = t.date if f is None or t.date < f else f
    m0, m1 = month_start(month), month_end(month)

    def new(t) -> bool:
        f = first_seen.get(t.entity)
        return f is not None and m0 <= f <= m1

    def klass(t) -> str:
        return "one_off" if t.is_one_off else "recurring" if t.key in members else "new_merchant" if new(t) else "habitual"

    classes = {"one_off": 0, "recurring": 0, "new_merchant": 0, "habitual": 0}
    for t in in_m:
        classes[klass(t)] -= t.amount_c
    month_run = classes["recurring"] + classes["new_merchant"] + classes["habitual"]
    base_run_sum = -sum(t.amount_c for t in txs if t.month in bset and not t.is_one_off)
    usual = div_cents(base_run_sum, n) if n else None
    # merchants: delta numerator = n x month - base total (exact), delta = numerator / n
    ent: dict[str, dict] = {}
    for t in in_m:
        if t.is_one_off:
            continue
        e = ent.setdefault(t.entity, {"run": 0, "n": 0, "rec": False, "new": False})
        e["run"] -= t.amount_c
        e["n"] += 1
        e["rec"] = e["rec"] or t.key in members
        e["new"] = e["new"] or new(t)
    base_by_ent: dict[str, int] = {}
    for t in txs:
        if t.month in bset and not t.is_one_off:
            base_by_ent[t.entity] = base_by_ent.get(t.entity, 0) - t.amount_c
    merchants = []
    if n:
        for name in set(ent) | set(base_by_ent):
            e = ent.get(name, {"run": 0, "n": 0, "rec": False, "new": False})
            base = base_by_ent.get(name, 0)
            num = n * e["run"] - base
            cls = "recurring" if e["rec"] else "new_merchant" if e["new"] else ("stopped" if e["run"] == 0 else "habitual")
            merchants.append({"entity": name, "class": cls, "spent": _m(e["run"]), "n_tx": e["n"], "usual": _m(div_cents(base, n)),
                              "delta": _m(div_cents(num, n)), "_num": num})
        merchants.sort(key=lambda r: (-r["_num"], r["entity"]))
    excess_by_class = {}
    for r in merchants:
        excess_by_class[r["class"]] = excess_by_class.get(r["class"], 0) + r["_num"]
    for r in merchants:
        r.pop("_num")
    # categories (useful for a group or an account)
    cats: dict[str, list] = {}
    for t in txs:
        if t.month == month or t.month in bset:
            cats.setdefault(t.category, []).append(t)
    cat_rows = []
    if n:
        for c, ts in cats.items():
            run = -sum(t.amount_c for t in ts if t.month == month and not t.is_one_off)
            ub = div_cents(-sum(t.amount_c for t in ts if t.month in bset and not t.is_one_off), n)
            cat_rows.append({"category": c, "spent": _m(run), "usual": _m(ub), "delta": _m(run - ub), "_d": run - ub})
        cat_rows.sort(key=lambda r: (-r["_d"], r["category"]))
        for r in cat_rows:
            r.pop("_d")
    toptx = sorted(in_m, key=lambda t: (t.amount_c, t.key))[:top]
    # the same month a year before
    ly = add_months_key(month, -12)
    ly_out: dict
    if ly in covered:
        ly_all = -sum(t.amount_c for t in txs if t.month == ly)
        ly_run = -sum(t.amount_c for t in txs if t.month == ly and not t.is_one_off)
        ly_out = {"month": ly, "available": True, "spent": _m(ly_all), "spent_ex_one_offs": _m(ly_run),
                  "delta_ex_one_offs": _m(month_run - ly_run), "pct": _pct(month_run - ly_run, ly_run)}
    else:
        ly_out = {"month": ly, "available": False,
                  "reason": "no data" if not any(t.month == ly for t in txs) and not any(
                      a for a in carriers if ds.coverage.of(a).first and ds.coverage.of(a).first <= month_start(ly))
                  else "that month is not fully covered by every account carrying this spending"}
    notes = []
    if month not in covered:
        notes.append("the reviewed month is not fully covered by every account carrying this spending: figures are partial")
    if not n:
        notes.append("no earlier month is fully covered: there is no baseline, the breakdown is shown without a comparison")
    elif n < ds.settings.average_min_months:
        notes.append(f"the baseline rests on {n} month(s) only: low confidence")
    cov = ds.coverage.info(carriers, bm, "baseline: months before the reviewed month that every account carrying the scope covers",
                           notes)
    return {"scope": {"kind": "category" if category else "account", "target": scope_name}, "month": month,
            "month_complete": month in covered,
            "totals": {"spent": _m(month_run + classes["one_off"]), "spent_ex_one_offs": _m(month_run), "usual_ex_one_offs": _m(usual),
                       "excess_ex_one_offs": _m(month_run - usual) if usual is not None else None,
                       "pct": _pct(month_run - usual, usual) if usual is not None else None, "baseline_months": n},
            "by_class": {k: _m(v) for k, v in classes.items()},
            "excess_by_class": {k: _m(div_cents(v, n)) for k, v in sorted(excess_by_class.items())} if n else None,
            "merchants": merchants[:top], "largest_decreases": [r for r in merchants[::-1] if not r["delta"].startswith("0") and r["delta"].startswith("-")][:3],
            "categories": cat_rows[:top],
            "top_transactions": [{"ref": t.key, "date": t.date, "amount": _m(t.amount_c), "category": t.category, "entity": t.entity,
                                  "class": klass(t), "tags": sorted(t.tags)} for t in toptx],
            "same_month_last_year": ly_out, "coverage": cov.to_dict(), "notes": notes}
