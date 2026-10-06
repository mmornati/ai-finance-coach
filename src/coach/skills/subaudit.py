"""subscription_audit (E7-5): every active recurring cost grouped, what it costs, and which ones deserve a second look.

Source: the recurring series (E4-3), the price changes (E4-4) and the contracts of the memory. Nothing is cancelled and nothing
is guessed: where the household's use of a service is unknown (it is, for almost every service) the audit says "usage
unknown" and names the series to ask about (the coach proposes the questions; see ``questions_propose``).

Groups (by the series' category)
    streaming_media   subscriptions.video_streaming / music_streaming / news_media
    software_cloud    subscriptions.software_cloud
    telecom           subscriptions.telecom
    memberships       subscriptions.memberships, leisure.sports_activities (a fixed recurring fee)
    other_subscriptions  any other subscriptions.* leaf
    insurance         insurance.* (not borrower insurance), housing.home_insurance, transport.car_insurance, health.health_insurance
    energy_utilities  housing.energy, housing.water
Loans, mortgages, rent, taxes and school fees are NOT audited here (they are not subscriptions).

Signals (``reasons`` of a review candidate)
    usage_unknown      a discretionary service (streaming, software, memberships) with no usage or keep decision on file
    overlap            several providers in the same discretionary category (video, music, news, software), or several lines
                       of one telecom category (informational)
    duplicate_amount   two series with the same amount on the same day of the month (+-1 day) - or one merchant paid from two
                       accounts: possibly one subscription paid twice
    price_increase     a confirmed or recent price rise (``chg_`` id) of the series
    marked_to_cancel   the contract on file says ``keep: false`` and the series is still being paid
    compare_offers     telecom, insurance or energy: worth comparing offers (find-cheaper)
    no_contract        no contract / liability file matches the series (information only)

Expected yearly savings range - HEURISTIC SHARES of the current yearly cost, the same constants every time, NOT market quotes:
    discretionary services  30 % to 100 % of the yearly cost (a cheaper tier up to dropping it)
    telecom 10-40 %, insurance 5-25 %, energy / utilities 5-15 % (what a comparison typically finds; ``find-cheaper`` replaces it
    with dated quotes)
    price_increase          at least the yearly effect of the increase is added to the upper bound
    duplicate_amount        0 to the whole yearly cost of one of the two
    marked_to_cancel        the whole yearly cost (low = high)
The candidates are ranked by upper bound, then lower bound. The sum of the ranges is an upper-bound picture, not a plan: the
actions are alternatives.
"""
from __future__ import annotations

from typing import Optional

from coach.analytics.common import div_cents, money_str
from coach.analytics.dataset import Dataset
from coach.analytics.pricechanges import price_changes
from coach.analytics.recurring import RecurringResult, RecurringSeries, detect_recurring
from coach.skills.money import pct_of_c
from coach.subs.usage import usage_of

GROUP_LEAVES = {
    "streaming_media": ("subscriptions.video_streaming", "subscriptions.music_streaming", "subscriptions.news_media"),
    "software_cloud": ("subscriptions.software_cloud",),
    "telecom": ("subscriptions.telecom",),
    "memberships": ("subscriptions.memberships", "leisure.sports_activities"),
    "insurance": ("housing.home_insurance", "transport.car_insurance", "health.health_insurance"),
    "energy_utilities": ("housing.energy", "housing.water"),
}
DISCRETIONARY = ("streaming_media", "software_cloud", "memberships", "other_subscriptions")
DISCRETIONARY_RANGE = (30, 100)
COMPARE_RANGE = {"telecom": (10, 40), "insurance": (5, 25), "energy_utilities": (5, 15)}
OVERLAP_CATEGORIES = ("subscriptions.video_streaming", "subscriptions.music_streaming", "subscriptions.news_media",
                      "subscriptions.software_cloud", "subscriptions.telecom")
INFORMATIONAL_OVERLAP = ("subscriptions.telecom",)
HEURISTIC_NOTE = ("savings ranges are fixed shares of the current yearly cost (discretionary 30-100 %, telecom 10-40 %, insurance "
                  "5-25 %, energy 5-15 %), not market quotes")


def group_of(category: str) -> Optional[str]:
    for g, leaves in GROUP_LEAVES.items():
        if category in leaves:
            return g
    if category.startswith("subscriptions."):
        return "other_subscriptions"
    if category.startswith("insurance.") and category != "insurance.borrower":
        return "insurance"
    return None


def _m(c): return money_str(c)


def _contract_info(ds: Dataset, x: RecurringSeries) -> dict:
    cid = next((l.id for l in x.links if l.kind == "contract"), None)
    c = next((m for _r, m in ds.memory.contracts if m.id == cid), None) if cid else None
    return {"on_file": c is not None, "id": cid, "keep": None if c is None else c.keep,
            "usage_recorded": bool(c and usage_of(c)["recorded"]), "renewal": c.renewal if c else None,
            "commitment_end": c.commitment_end if c else None}


def subscription_audit(ds: Dataset, recurring: Optional[RecurringResult] = None, dismissed_changes: frozenset = frozenset(),
                       asked: frozenset = frozenset(), limit: int = 10, items_limit: int = 20) -> dict:
    rec = recurring or detect_recurring(ds)
    chg = price_changes(ds, recurring=rec, dismissed=dismissed_changes)
    rises = {}
    for c in chg.changes:
        if c.effect == "costs_more":
            rises.setdefault(c.series_id, c)
    active, ended = [], []
    for x in rec.series:
        if x.kind != "expense":
            continue
        g = group_of(x.category)
        if g is None:
            continue
        (active if x.status == "active" else ended).append((x, g))
    items = []
    for x, g in sorted(active, key=lambda p: (-p[0].yearly_cost_c, p[0].id)):
        ctr = _contract_info(ds, x)
        it = {"series": x.id, "entity": x.entity, "group": g, "category": x.category, "cadence": x.cadence,
              "expected": _m(abs(x.expected_amount_c)), "monthly": _m(div_cents(x.yearly_cost_c, 12)), "yearly": _m(x.yearly_cost_c),
              "since": x.first_date, "last_payment": x.last_date, "account": x.account, "day_of_month": x.day_of_month,
              "contract": {k: v for k, v in {"on_file": ctr["on_file"], "keep": ctr["keep"], "renewal": ctr["renewal"],
                                              "commitment_end": ctr["commitment_end"]}.items() if v is not None or k == "on_file"},
              "usage": "recorded" if ctr["usage_recorded"] else "unknown", "_y": x.yearly_cost_c, "_x": x, "_ctr": ctr}
        c = rises.get(x.id)
        it["price_change"] = None if c is None else {"id": c.id, "date": c.date, "old": _m(c.old_c), "new": _m(c.new_c),
                                                    "yearly_impact": _m(c.yearly_impact_c), "confirmed": c.confirmed,
                                                    "_impact": c.yearly_impact_c}
        items.append(it)
    # -- duplicates and overlaps
    flags: dict[str, list[str]] = {it["series"]: [] for it in items}
    duplicates, overlaps = [], []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            xa, xb = a["_x"], b["_x"]
            same_amt = abs(abs(xa.expected_amount_c) - abs(xb.expected_amount_c)) <= 1 and xa.cadence == xb.cadence
            da, db = xa.day_of_month, xb.day_of_month
            same_day = da is not None and db is not None and abs(da - db) <= 1
            if same_amt and same_day and abs(xa.expected_amount_c) >= 100:
                duplicates.append({"series": [xa.id, xb.id], "kind": "same_amount_same_day", "amount": _m(abs(xa.expected_amount_c)),
                                   "cadence": xa.cadence, "yearly_each": _m(xa.yearly_cost_c), "entities": [xa.entity, xb.entity]})
                for it in (a, b):
                    flags[it["series"]].append("duplicate_amount")
            elif xa.key == xb.key and xa.account != xb.account:
                duplicates.append({"series": [xa.id, xb.id], "kind": "same_merchant_two_accounts", "amount": _m(abs(xa.expected_amount_c)),
                                   "cadence": xa.cadence, "yearly_each": _m(xa.yearly_cost_c), "entities": [xa.entity, xb.entity]})
                for it in (a, b):
                    flags[it["series"]].append("duplicate_amount")
    for cat in OVERLAP_CATEGORIES:
        members = [it for it in items if it["category"] == cat]
        ents = {it["_x"].entity for it in members}
        if len(members) >= 2 and len(ents) >= 2 or (cat in INFORMATIONAL_OVERLAP and len(members) >= 2):
            total = sum(it["_y"] for it in members)
            cheapest = min(it["_y"] for it in members)
            overlaps.append({"category": cat, "series": [it["series"] for it in members], "entities": sorted(ents),
                             "combined_yearly": _m(total), "dropping_the_cheapest_saves_yearly": _m(cheapest),
                             "informational": cat in INFORMATIONAL_OVERLAP,
                             "note": "several lines can be legitimate (family plan)" if cat in INFORMATIONAL_OVERLAP else None})
            if cat not in INFORMATIONAL_OVERLAP:
                for it in members:
                    flags[it["series"]].append("overlap")
    # -- candidates
    cands = []
    for it in items:
        x, g, y = it["_x"], it["group"], it["_y"]
        reasons = list(dict.fromkeys(flags[it["series"]]))
        keep = it["_ctr"]["keep"]
        low = high = 0
        if g in DISCRETIONARY and keep is not True:
            low, high = pct_of_c(y, DISCRETIONARY_RANGE[0]), pct_of_c(y, DISCRETIONARY_RANGE[1])
            if it["usage"] == "unknown" and keep is not False:
                reasons.insert(0, "usage_unknown")
        elif g in COMPARE_RANGE:
            low, high = pct_of_c(y, COMPARE_RANGE[g][0]), pct_of_c(y, COMPARE_RANGE[g][1])
            reasons.append("compare_offers")
        pc = it["price_change"]
        if pc is not None:
            reasons.append("price_increase")
            high = max(high, pc["_impact"])
        if "duplicate_amount" in reasons:
            low, high = 0, max(high, y)
        if keep is False:
            reasons.append("marked_to_cancel")
            low = high = y
        if not it["contract"]["on_file"]:
            reasons.append("no_contract")
        action = [r for r in reasons if r != "no_contract"]
        if not action:
            continue
        step = ("stop_payment_marked_in_contract" if "marked_to_cancel" in reasons else
                "check_possible_duplicate" if "duplicate_amount" in reasons else
                "ask_about_usage" if "usage_unknown" in reasons else
                "review_price_increase" if "price_increase" in reasons else "compare_offers")
        cands.append({"series": it["series"], "entity": it["entity"], "group": g, "category": it["category"], "yearly": it["yearly"],
                      "monthly": it["monthly"], "reasons": reasons, "next_step": step,
                      "expected_yearly_savings": {"low": _m(low), "high": _m(high)}, "_low": low, "_high": high})
    cands.sort(key=lambda c: (-c["_high"], -c["_low"], c["series"]))
    sum_low, sum_high = sum(c["_low"] for c in cands), sum(c["_high"] for c in cands)
    needs_usage = [{"series": c["series"], "group": c["group"], "monthly": c["monthly"]} for c in cands
                   if "usage_unknown" in c["reasons"] and f"usage:{c['series']}" not in asked]
    # -- totals
    groups = {}
    for it in items:
        gr = groups.setdefault(it["group"], {"count": 0, "monthly_c": 0, "yearly_c": 0})
        gr["count"] += 1
        gr["monthly_c"] += div_cents(it["_y"], 12)
        gr["yearly_c"] += it["_y"]
    total_y = sum(it["_y"] for it in items)
    out_items = []
    for it in items:
        it = dict(it)
        pc = it.get("price_change")
        if pc:
            pc = {k: v for k, v in pc.items() if not k.startswith("_")}
            it["price_change"] = pc
        it.pop("_y"), it.pop("_x"), it.pop("_ctr")
        out_items.append(it)
    recent_ended = sorted([(x, g) for x, g in ended], key=lambda p: p[0].last_date, reverse=True)[:5]
    return {"as_of": ds.today, "totals": {"active_series": len(items), "monthly": _m(div_cents(total_y, 12)), "yearly": _m(total_y)},
            "groups": {g: {"count": v["count"], "monthly": _m(v["monthly_c"]), "yearly": _m(v["yearly_c"])}
                       for g, v in sorted(groups.items(), key=lambda kv: -kv[1]["yearly_c"])},
            "items": out_items[:items_limit], "items_not_listed": max(0, len(out_items) - items_limit), "duplicates": duplicates, "overlaps": overlaps,
            "price_increases": [{"series": sid, "change": c.id, "yearly_impact": _m(c.yearly_impact_c), "confirmed": c.confirmed}
                                for sid, c in sorted(rises.items()) if any(it["series"] == sid for it in items)],
            "no_contract_on_file": [it["series"] for it in out_items if not it["contract"]["on_file"]],
            "candidates": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands[:limit]],
            "candidates_total": len(cands),
            "candidates_savings_range_sum": {"low": _m(sum_low), "high": _m(sum_high),
                                             "note": "upper-bound picture: the actions are alternatives, not a plan"},
            "usage_questions_needed": needs_usage[:limit],
            "recently_ended": [{"series": x.id, "entity": x.entity, "group": g, "yearly": _m(x.yearly_cost_c), "last_payment": x.last_date}
                               for x, g in recent_ended],
            "coverage": rec.coverage.to_dict(),
            "notes": [HEURISTIC_NOTE, "the household's use of a service is not in the bank data: unknown usage is asked, never guessed",
                      "nothing is cancelled by the coach: review candidates only"]}
