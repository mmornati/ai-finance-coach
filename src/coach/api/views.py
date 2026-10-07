"""Read models of the web app: thin compositions of the analytics results (all numbers computed by
:mod:`coach.analytics` / :mod:`coach.memory`; this module only selects, joins and renders strings).

Money is a decimal STRING with two decimals (``"-12.34"``), exactly as the analytics ``to_dict()`` renders it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

from coach.analytics.averages import category_averages, spending_txs
from coach.analytics.common import (Scope, add_months_key, div_cents, last_closed_month, money_str, month_key,
                                    month_start, note, split_notes, to_cents)
from coach.analytics.coverage import last_n
from coach.analytics.dataset import Dataset, is_income, is_spending, is_transfer
from coach.classify import rules as R
from coach.i18n_msg import server_msg


# ---------------------------------------------------------------- scope / meta

def scope_of(ds: Dataset, owner=None, purpose=None, account=None) -> Scope:
    accounts = None
    if account:
        uids = []
        for ref in ([account] if isinstance(account, str) else account):
            a = ds.resolve_account(ref)
            uids.append(a.uid if a else ref)
        accounts = uids
    return Scope.make(owner or None, purpose or None, accounts)


def taxonomy() -> dict:
    return {"groups": [{"id": g, "categories": [{"id": f"{g}.{leaf}", "description": desc}
                                                  for leaf, desc in leaves.items()]}
                       for g, leaves in R.TAXONOMY.items()]}


def label_of_category(cat: str) -> str:
    return cat


def kind_of_category(cat: str) -> str:
    if is_transfer(cat):
        return "transfer"
    if cat.startswith("income.") and cat != "income.refund":
        return "income"
    return "spending"


def account_rows(ds: Dataset) -> list[dict]:
    out = []
    for uid, a in sorted(ds.accounts.items(), key=lambda kv: (kv[1].bank or "", kv[1].label)):
        out.append({"uid": uid, "label": a.label, "bank": a.bank, "owner": a.owner, "purpose": a.purpose})
    return out


def filters_meta(ds: Dataset, con, store) -> dict:
    members = [{"id": m.id, "name": m.name, "role": m.role} for m in ds.memory.members]
    owners = sorted({a.owner for a in ds.accounts.values() if a.owner} | {m.id for m in ds.memory.members} | {"joint"})
    purposes = sorted({a.purpose for a in ds.accounts.values() if a.purpose})
    used_tags = sorted({t for tx in ds.txs for t in tx.tags})
    from coach.memory.schemas import KNOWN_TAGS
    tags = sorted(set(KNOWN_TAGS) | set(used_tags))
    sources = sorted({tx.source for tx in ds.txs} | {"override", "split", "memory", "user", "rule"})
    events = events_list(store)
    return {"accounts": account_rows(ds), "owners": owners, "members": members, "purposes": purposes, "tags": tags,
            "sources": sources, "events": events, "groups": sorted(R.TAXONOMY),
            "today": ds.today.isoformat()}


def events_list(store) -> list[dict]:
    seen = {}
    for e in store._items("events.yaml", "events"):
        seen[e.id] = {"id": e.id, "title": e.title, "start": e.start.isoformat() if e.start else None,
                      "end": e.end.isoformat() if e.end else None, "budget": money_str(to_cents(e.budget)) if e.budget is not None else None,
                      "status": e.status, "note": e.note, "source": "events.yaml"}
    for i in sorted(store.event_ids()):
        seen.setdefault(i, {"id": i, "title": None, "start": None, "end": None, "budget": None, "status": None,
                            "note": None, "source": "events.md"})
    return sorted(seen.values(), key=lambda e: e["id"])


# ---------------------------------------------------------------- balances

# the web shows labels.balanceType.<code> (web/src/locales/<lang>/server.json); this English label is its fallback
BALANCE_TYPE = {"CLBD": "booked", "ITBD": "booked (interim)", "XPCD": "expected", "CLAV": "available (closing)",
                "ITAV": "available (interim)", "OPBD": "booked (opening)", "OPAV": "available (opening)", "FWAV": "forward available"}
BOOKED = ("CLBD", "ITBD")


def balances(ds: Dataset, scope: Optional[Scope] = None) -> dict:
    """One balance per account: the most 'booked' type the bank gave (the order CLBD, ITBD, XPCD, CLAV, ITAV, OPBD, OPAV,
    FWAV: see ``analytics.dataset.BALANCE_PREFERENCE``). The type is shown, and the total says when it mixes types."""
    stale_days = ds.settings.forecast_balance_stale_days
    rows, total = [], 0
    for a in [x for x in ds.accounts_in(scope) if ds.owned is None or x.uid in ds.owned]:
        b = ds.balance_of(a.uid)
        row = {"uid": a.uid, "label": a.label, "bank": a.bank, "owner": a.owner, "purpose": a.purpose,
               "balance": money_str(b.amount_c) if b else None, "balance_type": b.type if b else None,
               "balance_type_label": BALANCE_TYPE.get(b.type, b.type) if b else None, "booked": bool(b and b.type in BOOKED),
               "as_of": b.as_of.isoformat() if b else None,
               "age_days": (ds.today - b.as_of).days if b else None,
               "stale": bool(b and (ds.today - b.as_of).days > stale_days)}
        if b:
            total += b.amount_c
        rows.append(row)
    rows.sort(key=lambda r: -(to_cents(r["balance"]) if r["balance"] is not None else -10**12))
    non_booked = [r["label"] for r in rows if r["balance"] is not None and not r["booked"]]
    note = "One balance per account, the most booked type the bank provides."
    if non_booked:
        accounts = ", ".join(non_booked[:4])
        note += f" {len(non_booked)} account(s) only give a non-booked balance ({accounts}): the total mixes types."
        note_msg = server_msg("balances.mixedTypes", note, count=len(non_booked), accounts=accounts)
    else:
        note_msg = server_msg("balances.oneBalance", note)
    return {"as_of": ds.today.isoformat(), "household_total": money_str(total),
            "n_accounts": len(rows), "n_without_balance": sum(1 for r in rows if r["balance"] is None),
            "mixed_types": bool(non_booked), "non_booked": non_booked, "accounts": rows, "note": note, "note_msg": note_msg}


# ---------------------------------------------------------------- categories

def _month_sums(txs, cats_filter) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t in txs:
        if cats_filter(t.category):
            row = out.setdefault(t.category, {})
            row[t.month] = row.get(t.month, 0) - t.amount_c
    return out


def month_categories(ds: Dataset, scope: Optional[Scope], month: Optional[str] = None, window: Optional[int] = None) -> dict:
    """Spending of one month per category next to the coverage-aware monthly average."""
    month = month or month_key(ds.today)
    av = category_averages(ds, scope, window)
    avg = {c.category: c for c in av.categories}
    sp = [t for t in spending_txs(ds, scope) if t.month == month]
    per: dict[str, list[int]] = {}
    for t in sp:
        row = per.setdefault(t.category, [0, 0, 0])
        row[0] -= t.amount_c
        row[1] -= t.amount_c if t.is_one_off else 0
        row[2] += 1
    rows = []
    total = 0
    for cat, (spent, one_off, n) in per.items():
        a = avg.get(cat)
        total += spent
        rows.append({"category": cat, "group": cat.split(".")[0], "spent": money_str(spent),
                     "one_off": money_str(one_off), "n_tx": n,
                     "monthly_avg": money_str(a.monthly_avg_c) if a else None,
                     "avg_months": a.n_months if a else 0, "low_confidence": bool(a and a.low_confidence)})
    rows.sort(key=lambda r: -to_cents(r["spent"]))
    groups: dict[str, int] = {}
    for r in rows:
        groups[r["group"]] = groups.get(r["group"], 0) + to_cents(r["spent"])
    return {"month": month, "partial": month == month_key(ds.today), "as_of": ds.today.isoformat(),
            "total_spent": money_str(total), "household_monthly_avg": money_str(av.household_monthly_avg_c)
            if av.household_monthly_avg_c is not None else None,
            "household_avg_months": len(av.household_months),
            "categories": rows,
            "groups": sorted(({"group": g, "spent": money_str(v)} for g, v in groups.items()),
                             key=lambda x: -to_cents(x["spent"])),
            "coverage": av.coverage.to_dict()}


def group_average(ds: Dataset, scope: Optional[Scope], group: str) -> Optional[dict]:
    """The coverage-aware monthly average of a whole group (the same rule as a category: months covered by every account
    carrying the group's money, one-offs left out), computed here once for the overview and the drill-down."""
    leaves = {f"{group}.{leaf}" for leaf in R.TAXONOMY[group]}
    if kind_of_category(next(iter(leaves))) != "spending":
        return None
    txs = [t for t in ds.txs_in(scope) if t.category in leaves and not t.is_saved]
    carriers: set[str] = set()
    for leaf in leaves:
        carriers |= set(ds.carriers_split(leaf, scope)[0])
    covered = ds.coverage.common_months(sorted(carriers)) if carriers else []
    ms = last_n(covered, ds.settings.average_window_months)
    if not ms:
        return None
    mset = set(ms)
    run = -sum(t.amount_c for t in txs if t.month in mset and not t.is_one_off)
    allv = -sum(t.amount_c for t in txs if t.month in mset)
    return {"monthly": money_str(div_cents(run, len(ms))), "monthly_with_one_offs": money_str(div_cents(allv, len(ms))),
            "n_months": len(ms), "months": ms, "accounts": [ds.label(u) for u in sorted(carriers)], "ignored_accounts": [],
            "lumpy": False, "low_confidence": len(ms) < ds.settings.average_min_months}


def category_overview(ds: Dataset, scope: Optional[Scope]) -> dict:
    av = category_averages(ds, scope)
    cur, prev = month_key(ds.today), last_closed_month(ds.today)
    sp = spending_txs(ds, scope)
    this_m: dict[str, int] = {}
    last_m: dict[str, int] = {}
    for t in sp:
        if t.month == cur:
            this_m[t.category] = this_m.get(t.category, 0) - t.amount_c
        elif t.month == prev:
            last_m[t.category] = last_m.get(t.category, 0) - t.amount_c
    rows = []
    seen = set()
    for c in av.categories:
        seen.add(c.category)
        rows.append({"category": c.category, "group": c.category.split(".")[0], "monthly_avg": money_str(c.monthly_avg_c),
                     "monthly_avg_with_one_offs": money_str(c.monthly_avg_with_one_offs_c), "n_months": c.n_months,
                     "low_confidence": c.low_confidence, "lumpy": c.lumpy, "this_month": money_str(this_m.get(c.category, 0)),
                     "last_month": money_str(last_m.get(c.category, 0)), "accounts": c.accounts})
    for cat in sorted(set(this_m) | set(last_m)):
        if cat not in seen:
            rows.append({"category": cat, "group": cat.split(".")[0], "monthly_avg": None, "monthly_avg_with_one_offs": None,
                         "n_months": 0, "low_confidence": True, "lumpy": False, "this_month": money_str(this_m.get(cat, 0)),
                         "last_month": money_str(last_m.get(cat, 0)), "accounts": []})
    gm_this: dict[str, int] = {}
    gm_last: dict[str, int] = {}
    for cat, v in this_m.items():
        gm_this[cat.split(".")[0]] = gm_this.get(cat.split(".")[0], 0) + v
    for cat, v in last_m.items():
        gm_last[cat.split(".")[0]] = gm_last.get(cat.split(".")[0], 0) + v
    groups = []
    for g in sorted({r["group"] for r in rows}):
        ga = group_average(ds, scope, g)
        groups.append({"group": g, "monthly_avg": ga["monthly"] if ga else None, "n_months": ga["n_months"] if ga else 0,
                       "low_confidence": bool(ga and ga["low_confidence"]), "this_month": money_str(gm_this.get(g, 0)),
                       "last_month": money_str(gm_last.get(g, 0))})
    groups.sort(key=lambda x: -(to_cents(x["monthly_avg"]) if x["monthly_avg"] else 0))
    return {"as_of": ds.today.isoformat(), "categories": rows, "groups": groups, "unavailable": av.unavailable,
            "household_monthly_avg": money_str(av.household_monthly_avg_c) if av.household_monthly_avg_c is not None else None,
            "household_months": av.household_months, "coverage": av.coverage.to_dict()}


def category_detail(ds: Dataset, scope: Optional[Scope], cat_id: str, months: int = 24) -> dict:
    """Drill-down of a category (``food.groceries``) or a whole group (``food``)."""
    is_group = cat_id in R.TAXONOMY
    if not is_group and cat_id not in R.CATEGORIES:
        raise KeyError(cat_id)
    leaves = [f"{cat_id}.{leaf}" for leaf in R.TAXONOMY[cat_id]] if is_group else [cat_id]
    kind = kind_of_category(leaves[0])
    sign = 1 if kind == "income" else -1                      # income is shown positive, spending positive too
    txs = [t for t in ds.txs_in(scope) if t.category in set(leaves)
           and (kind != "spending" or not t.is_saved)]
    if kind == "spending":
        txs = [t for t in txs]
    cur = month_key(ds.today)
    # coverage: months fully covered by the accounts that carry the category (major ones narrow the window)
    carriers: set[str] = set()
    for leaf in leaves:
        carriers |= set(ds.carriers_split(leaf, scope)[0])
    covered = ds.coverage.common_months(sorted(carriers)) if carriers else []
    cset = set(covered)
    first = min((t.month for t in txs), default=None)
    series = []
    if first:
        lo = max(first, add_months_key(cur, -(months - 1)))
        m = lo
        by_m: dict[str, list] = {}
        for t in txs:
            by_m.setdefault(t.month, []).append(t)
        while m <= cur:
            items = by_m.get(m, [])
            tot = sign * sum(t.amount_c for t in items)
            one = sign * sum(t.amount_c for t in items if t.is_one_off)
            series.append({"month": m, "total": money_str(tot), "run_rate": money_str(tot - one), "one_off": money_str(one),
                           "n_tx": len(items), "covered": m in cset, "partial": m == cur})
            m = add_months_key(m, 1)
    # average (leaf: the coverage-aware engine; group: the same rule over the group's months)
    avg = None
    window = ds.settings.average_window_months
    if kind == "spending":
        if not is_group:
            row = next((c for c in category_averages(ds, scope).categories if c.category == cat_id), None)
            if row:
                avg = {"monthly": money_str(row.monthly_avg_c), "monthly_with_one_offs": money_str(row.monthly_avg_with_one_offs_c),
                       "n_months": row.n_months, "months": row.months, "accounts": row.accounts,
                       "ignored_accounts": row.ignored_accounts, "lumpy": row.lumpy, "low_confidence": row.low_confidence}
        else:
            avg = group_average(ds, scope, cat_id)
    # trend: last 3 covered months vs the 3 before
    trend = None
    cov_series = [s for s in series if s["covered"]]
    if len(cov_series) >= 6:
        recent, prior = cov_series[-3:], cov_series[-6:-3]
        r = sum(to_cents(s["run_rate"]) for s in recent)
        p = sum(to_cents(s["run_rate"]) for s in prior)
        trend = {"recent_avg": money_str(div_cents(r, 3)), "prior_avg": money_str(div_cents(p, 3)),
                 "delta": money_str(div_cents(r - p, 3)), "pct": round((r - p) / p, 4) if p else None,
                 "recent_months": [s["month"] for s in recent], "prior_months": [s["month"] for s in prior]}
    # merchants / entities over the last 12 months (all months, covered or not)
    lo12 = add_months_key(cur, -11)
    by_e: dict[str, dict] = {}
    for t in txs:
        if t.month < lo12:
            continue
        e = by_e.setdefault(t.entity, {"entity": t.entity, "total": 0, "one_off": 0, "n_tx": 0, "last_date": t.date,
                                       "accounts": set(), "categories": set()})
        e["total"] += sign * t.amount_c
        e["one_off"] += sign * t.amount_c if t.is_one_off else 0
        e["n_tx"] += 1
        e["last_date"] = max(e["last_date"], t.date)
        e["accounts"].add(ds.label(t.account))
        e["categories"].add(t.category)
    grand = sum(e["total"] for e in by_e.values()) or 1
    entities = sorted(({"entity": e["entity"], "total": money_str(e["total"]), "one_off": money_str(e["one_off"]),
                        "n_tx": e["n_tx"], "last_date": e["last_date"].isoformat(), "share": round(e["total"] / grand, 4),
                        "accounts": sorted(e["accounts"]), "categories": sorted(e["categories"])}
                       for e in by_e.values()), key=lambda e: -to_cents(e["total"]))[:40]
    one_offs = [{"tx_key": t.key, "date": t.date.isoformat(), "amount": money_str(t.amount_c), "entity": t.entity,
                 "category": t.category, "tags": sorted(t.tags), "event": t.event, "account": ds.label(t.account)}
                for t in sorted(txs, key=lambda t: t.date, reverse=True) if t.is_one_off][:50]
    notes = []                                         # common.note: the English (notes) and the web's message (notes_msg)
    if avg and avg.get("low_confidence"):
        notes.append(note("coverage.lowConfidence", f"low confidence: only {avg['n_months']} fully covered month(s) back this figure",
                          count=int(avg["n_months"])))
    if avg and avg.get("lumpy"):
        notes.append(note("coverage.lumpy", "seasonal or lumpy category: its average needs at least 12 covered months"))
    missing = [s["month"] for s in series if not s["covered"] and not s["partial"] and s["n_tx"]]
    if missing:
        notes.append(note("coverage.monthsNotCovered", f"{len(missing)} month(s) with transactions are not fully covered by the accounts "
                          "carrying this category (shown lighter, left out of the average)", count=len(missing)))
    if ds.foreign:
        notes.append(note("coverage.nonEurEveryFigure", f"{len(ds.foreign)} non-EUR transaction(s) are left out of every figure",
                          count=len(ds.foreign)))
    cov = ds.coverage.info(sorted(carriers), (avg or {}).get("months", []),
                           "months fully covered by every account that carries the category", notes)
    notes, notes_msg = split_notes(notes)
    return {"id": cat_id, "is_group": is_group, "kind": kind,
            "description": R.CATEGORIES.get(cat_id) if not is_group else None,
            "leaves": [{"id": l, "description": R.CATEGORIES[l]} for l in leaves] if is_group else [],
            "group": cat_id.split(".")[0], "as_of": ds.today.isoformat(), "series": series, "average": avg, "trend": trend,
            "entities": entities, "one_offs": one_offs, "notes": notes, "notes_msg": notes_msg, "coverage": cov.to_dict(),
            "totals": {"last_12_months": money_str(sum(e["total"] for e in by_e.values())),
                       "n_tx": sum(e["n_tx"] for e in by_e.values())}}


# ---------------------------------------------------------------- subscriptions

def _contract_names(ds: Dataset) -> dict:
    out = {}
    for rel, c in ds.memory.contracts:
        out[("contract", c.id)] = {"kind": "contract", "id": c.id, "name": c.provider or c.id, "detail": c.kind,
                                   "renewal": c.renewal.isoformat() if c.renewal else None,
                                   "keep": c.keep, "commitment_end": c.commitment_end.isoformat() if c.commitment_end else None}
    for rel, l in ds.memory.liabilities:
        out[("liability", l.id)] = {"kind": "liability", "id": l.id, "name": l.lender or l.id, "detail": l.kind,
                                    "renewal": None, "keep": None, "commitment_end": l.end_date.isoformat() if l.end_date else None}
    return out


def subscriptions(ds: Dataset, rec, pcs, scope: Optional[Scope] = None, status: str = "active", kind: str = "expense",
                  cadence: Optional[str] = None, contracts_only: bool = False) -> dict:
    names = _contract_names(ds)
    by_series: dict[str, list] = {}
    for c in pcs.changes:
        by_series.setdefault(c.series_id, []).append(c.to_dict())
    rows = []
    for x in rec.series:
        if kind != "all" and x.kind != kind:
            continue
        if status != "all" and x.status != status:
            continue
        if cadence and x.cadence != cadence:
            continue
        if contracts_only and not (x.contract_candidate or x.links):
            continue
        if scope is not None and not scope.is_all and not _series_in_scope(ds, x, scope):
            continue
        d = x.to_dict()
        d["occurrences"] = d["occurrences"][-12:]
        d["monthly_cost"] = money_str(div_cents(x.yearly_cost_c, 12))
        d["price_changes"] = by_series.get(x.id, [])
        d["linked"] = [dict(names[(l.kind, l.id)], share=l.share) for l in x.links if (l.kind, l.id) in names]
        rows.append(d)
    active = [r for r in rows if r["status"] == "active" and r["direction"] == "out"]
    return {"as_of": ds.today.isoformat(), "series": rows,
            "totals": {"active_monthly": money_str(sum(to_cents(r["monthly_cost"]) for r in active)),
                       "active_yearly": money_str(sum(to_cents(r["yearly_cost"]) for r in active)),
                       "n_active": len(active),
                       "n_missing_contract": sum(1 for r in active if r["missing_contract"])},
            "by_cadence": rec.by_cadence, "coverage": rec.coverage.to_dict()}


def _series_in_scope(ds, x, scope) -> bool:
    a = ds.accounts.get(x.account)
    return bool(a and scope.accepts(a))


# ---------------------------------------------------------------- calendar window

def calendar_for(ds: Dataset, rec, month: Optional[str] = None, days: Optional[int] = None) -> dict:
    from coach.analytics import upcoming
    if month:
        end = dt.date(month_start(month).year + (month_start(month).month // 12), month_start(month).month % 12 + 1, 1) \
            - dt.timedelta(days=1)
        n = max(1, (end - ds.today).days + 1)
        res = upcoming.calendar_items(ds, min(n, 400), recurring=rec)
        d = res.to_dict()
        d["items"] = [i for i in d["items"] if i["date"].startswith(month)]
        d["month"] = month
        d["counts"] = {}
        for i in d["items"]:
            d["counts"][i["source"]] = d["counts"].get(i["source"], 0) + 1
        return d
    return upcoming.calendar_items(ds, days or 14, recurring=rec).to_dict()


# ---------------------------------------------------------------- insights

def _iid(*parts) -> str:
    return "ins_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def build_insights(ds: Dataset, anomalies, pcs, forecast, budget_status, recurring=None) -> list[dict]:
    cards: list[dict] = []
    for a in anomalies.anomalies:
        cards.append({"id": a.id, "kind": "anomaly", "subtype": a.type, "severity": a.severity,
                      "title": {"category_spike": "Spending spike", "duplicate_charge": "Possible duplicate charge",
                                "new_merchant": "New merchant", "large_transaction": "Large payment"}.get(a.type, a.type),
                      "body": a.message, "amount": money_str(a.amount_c), "date": a.period, "subject": a.subject,
                      "evidence": a.evidence[:20], "persist": "anomaly"})
    for c in pcs.changes:
        up = c.direction == "increase"
        cards.append({"id": c.id, "kind": "price_change", "subtype": c.direction, "severity": "medium" if up else "low",
                      "title": f"{c.entity}: {'price up' if up else 'price down'} {abs(c.pct) * 100:.0f}%",
                      "body": f"{money_str(c.old_c)} -> {money_str(c.new_c)} EUR per {c.cadence} payment, about "
                              f"{money_str(abs(c.yearly_impact_c))} EUR a year{'' if c.confirmed else ' (not yet confirmed by a second payment)'}.",
                      "amount": money_str(c.yearly_impact_c), "date": c.date.isoformat(), "subject": c.entity,
                      "evidence": [c.tx_key], "persist": "price_change"})
    for acc in [forecast.household, *forecast.accounts]:
        if acc.first_negative or acc.first_at_risk or any(f for f in acc.flags):
            when = acc.first_negative or acc.first_at_risk
            sev = "high" if acc.first_negative else "medium"
            cards.append({"id": _iid("forecast", acc.account or "household", when, ",".join(acc.flags)), "kind": "forecast",
                          "subtype": "negative" if acc.first_negative else "at_risk", "severity": sev,
                          "title": f"{acc.label}: balance " + ("projected negative" if acc.first_negative else "at risk"),
                          "body": (f"Projected below zero from {acc.first_negative}. " if acc.first_negative else
                                   f"Could run short around {acc.first_at_risk} (low end of the band). ")
                                  + (f"Lowest expected {money_str(acc.min_balance_c)} EUR on {acc.min_date}." if acc.min_balance_c is not None else ""),
                          "amount": money_str(acc.min_balance_c) if acc.min_balance_c is not None else None,
                          "date": when.isoformat() if when else None, "subject": acc.label, "evidence": [], "persist": "ui",
                          "flags": acc.flags})
    for p in budget_status.budgets:
        if p.status in ("over", "at_risk"):
            cards.append({"id": _iid("budget", p.id, p.month, p.status), "kind": "budget", "subtype": p.status,
                          "severity": "high" if p.status == "over" else "medium",
                          "title": f"Budget {p.target}: " + ("over" if p.status == "over" else "on track to overrun"),
                          "body": f"{money_str(p.spent_c)} EUR spent of {money_str(p.available_c)} EUR in {p.month}; projected "
                                  f"{money_str(p.projected_c)} EUR by month end.",
                          "amount": money_str(p.remaining_c), "date": p.month, "subject": p.target,
                          "evidence": p.evidence[:20], "persist": "ui"})
    if recurring is not None:
        from coach.subs import reminders
        cards += reminders.subscription_cards(ds, recurring)          # E8: unused / notice deadline / decision to check
        from coach.loans import service as loans_service
        cards += loans_service.loan_cards(ds)                          # E9: missed / changed / extra payment, LOA end of contract
        from coach.rental import service as rental_service
        cards += rental_service.cards(ds)                              # E15: scheme commitment end, missing rent, rent cap / tenant income
    order = {"high": 0, "medium": 1, "low": 2}
    cards.sort(key=lambda c: c["date"] or "", reverse=True)            # newest first inside a severity (stable sort)
    cards.sort(key=lambda c: order.get(c["severity"], 3))
    return cards
