"""Shared costs split by rule: the "who pays what" view (E14-9).

``allocations`` in household.yaml: each rule says WHICH costs it covers (a category, a group, a tag, a merchant pattern, an account), HOW they
are shared (``equal``, ``income``: in proportion to each member's income over the window, or ``custom``: percentages) and AMONG WHOM
(default: every adult). A transaction belongs to the FIRST rule that matches it.

For each rule and member the view gives the member's fair share (``owed``), what they actually paid out of their own money (``paid``: the
costs attributed to them, e.g. paid from their personal account or with their card), and the settlement ``net``:

* costs paid from the JOINT account are paid by the common pot: nobody owes anything for them (``joint_paid``), they only show how the pot's
  cost splits ("their share of the joint costs");
* ``net = paid - share x (what the sharing members paid personally)``: positive = the others owe this member, negative = this member owes.
  The nets add up to zero (cents are shared by largest remainder, so sums are exact);
* a cost attributed to someone outside the rule (a child, a person that is not among the members) or to nobody is reported apart
  (``unattributed``) and settles nothing.

It reports a split by rule: it never moves money, never decides who is right, and gives no legal or tax view of a couple's finances.
"""
from __future__ import annotations

import re
from typing import Optional

from coach.analytics.common import add_months_key, last_closed_month, money_str, months_between
from coach.analytics.dataset import Dataset, is_income, is_spending
from coach.household.people import JOINT, People, fold
from coach.i18n_msg import server_msg

WINDOW_MONTHS = 12


def split_cents(total: int, weights: dict) -> dict:
    """`total` cents shared in proportion to `weights` by largest remainder: the parts add up to `total` exactly."""
    keys = sorted(weights)
    w = sum(weights.values())
    if total == 0 or w <= 0:
        return {k: 0 for k in keys}
    sign = -1 if total < 0 else 1
    t = abs(total)
    raw = {k: t * weights[k] / w for k in keys}
    out = {k: int(raw[k]) for k in keys}
    rest = t - sum(out.values())
    for k in sorted(keys, key=lambda k: (-(raw[k] - out[k]), k))[:rest]:
        out[k] += 1
    return {k: sign * v for k, v in out.items()}


def _matches(rule, t, accounts: dict) -> bool:
    m = rule.match
    if not (is_spending(t.category) or t.category == "income.refund"):
        return False
    if m.category and t.category != m.category:
        return False
    if m.group and t.category.split(".")[0] != m.group:
        return False
    if m.tag and m.tag not in t.tags:
        return False
    if m.merchant_key:
        try:
            if not re.search(m.merchant_key, t.mkey or "", re.I):
                return False
        except re.error:
            return False
    if m.account:
        acc = accounts.get(t.account)
        names = {fold(x) for x in (t.account, acc.label if acc else "") if x}
        if fold(m.account) not in names:
            return False
    return True


def who_pays(ds: Dataset, months: int = WINDOW_MONTHS, rule_id: Optional[str] = None) -> dict:
    people: People = ds.memory.people or People()
    closed = last_closed_month(ds.today)
    win = months_between(add_months_key(closed, -(max(1, months) - 1)), closed)
    txs = [t for t in ds.txs if t.month in win]
    income_by: dict = {}
    for t in txs:
        if t.person and is_income(t.category):
            income_by[t.person] = income_by.get(t.person, 0) + t.amount_c
    claimed: set = set()
    rules_out = []
    totals: dict = {}
    for rule in people.allocations:
        if rule_id and rule.id != rule_id:
            continue
        mine = [t for t in txs if (t.key, t.split_index) not in claimed and _matches(rule, t, ds.accounts)]
        for t in mine:
            claimed.add((t.key, t.split_index))
        among = list(rule.among) if rule.among else people.adults()
        notes_msg = []
        if rule.method == "custom":
            weights = {m: float(p) for m, p in rule.shares.items()}
            among = [m for m in weights]
        elif rule.method == "income":
            weights = {m: float(max(income_by.get(m, 0), 0)) for m in among}
            if sum(weights.values()) <= 0:
                notes_msg.append(server_msg("household.allocationNoIncome",
                                            "no income is attributed to these members over the window: shared equally instead"))
                weights = {m: 1.0 for m in among}
        else:
            weights = {m: 1.0 for m in among}
        total_w = sum(weights.values()) or 1.0
        share_pct = {m: round(weights[m] / total_w * 100, 2) for m in among}
        cost = -sum(t.amount_c for t in mine)
        joint = -sum(t.amount_c for t in mine if t.person == JOINT)
        paid = {m: -sum(t.amount_c for t in mine if t.person == m) for m in among}
        personal = sum(paid.values())
        unattributed = cost - joint - personal
        owed = split_cents(cost, weights)
        fair_personal = split_cents(personal, weights)
        net = {m: paid[m] - fair_personal[m] for m in among}
        joint_share = split_cents(joint, weights)
        if not mine:
            notes_msg.append(server_msg("household.allocationNoMatch", "no transaction matched this rule in the window"))
        rows = []
        for m in among:
            rows.append({"member": m, "share_pct": share_pct[m], "owed_c": owed[m], "paid_c": paid[m],
                         "joint_share_c": joint_share[m], "net_c": net[m]})
            tot = totals.setdefault(m, {"member": m, "owed_c": 0, "paid_c": 0, "net_c": 0})
            tot["owed_c"] += owed[m]
            tot["paid_c"] += paid[m]
            tot["net_c"] += net[m]
        rules_out.append({"id": rule.id, "title": rule.title, "method": rule.method, "among": among, "members": rows, "total_c": cost,
                          "joint_paid_c": joint, "personal_paid_c": personal, "unattributed_c": unattributed, "n": len(mine),
                          "income_basis": {m: income_by.get(m, 0) for m in among} if rule.method == "income" else None,
                          "evidence": sorted({t.key for t in mine})[:20], "notes": [m["text"] for m in notes_msg],
                          "notes_msg": notes_msg})
    top = [] if people.allocations else [server_msg("household.allocationNoRule", "no allocation rule: add one with `coach household allocate` "
                                                    "(household.yaml: allocations)", command="coach household allocate")]
    return {"as_of": ds.today, "window": {"months": win}, "rules": rules_out, "by_member": sorted(totals.values(), key=lambda r: r["member"]),
            "notes": [m["text"] for m in top], "notes_msg": top}


def to_json(report: dict) -> dict:
    from coach.household.kids import to_json as conv
    out = conv(report)
    for r in out.get("rules", []):
        ib = r.get("income_basis")
        if ib:
            r["income_basis"] = {k: money_str(v) if isinstance(v, int) else v for k, v in ib.items()}
    return out
