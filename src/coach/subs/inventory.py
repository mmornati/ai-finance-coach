"""The canonical "subscriptions & contracts" inventory (E8-1): ONE list of every recurring cost the household pays, merging

    * the recurring series detected in the bank data (E4-3): cost, cadence, next charge, price history;
    * the contract files of the memory (``contracts/*.yaml``): provider, dates, notice, the user-recorded usage;
    * the cancellation rules (E8-3: :mod:`coach.skills.cancel`), the alternatives (E8-4) and the decisions (E8-6).

One row per service. A series without a contract file is a row with contract status ``missing`` (and ``draftable``: see
:mod:`coach.subs.draft`); a contract file whose payments are not seen in the bank data is a row with ``cost_source = contract``
(its billing amount). Contract status: ``on_file`` | ``missing`` | ``expired`` (every date on file is in the past: it may have been
renewed or ended; refresh it). Groups are those of the subscription audit; loans, mortgages, rent and taxes are not subscriptions.

Everything here is local and unredacted (real provider names); the coach sees the redacted view built by the MCP tool.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from coach.analytics.common import div_cents, money_str
from coach.i18n_msg import server_msg
from coach.skills import cancel as C
from coach.skills.subaudit import group_of
from coach.subs import alternatives as A, decisions as DEC, usage as U

GROUPS = ("streaming_media", "software_cloud", "telecom", "memberships", "other_subscriptions", "insurance", "energy_utilities")
# the web shows labels.subsGroup.<group> (web/src/locales/<lang>/server.json); this English label is the fallback
GROUP_LABEL = {"streaming_media": "Streaming & media", "software_cloud": "Software & cloud", "telecom": "Telecom",
               "memberships": "Memberships", "other_subscriptions": "Other subscriptions", "insurance": "Insurance",
               "energy_utilities": "Energy & utilities"}
KIND_GROUP = {"energy": "energy_utilities", "telecom": "telecom", "insurance_home": "insurance", "insurance_car": "insurance",
              "insurance_health": "insurance", "health": "insurance", "streaming": "streaming_media", "software": "software_cloud",
              "membership": "memberships", "insurance_other": "insurance", "water": "energy_utilities", "other": "other_subscriptions"}
DRAFTED_RE = re.compile(r"^drafted from (rec_[0-9a-f]{6,})")
PERIODS_PER_YEAR = {"monthly": 12, "bimonthly": 6, "quarterly": 4, "yearly": 1}
# series category -> contract kind (the draft-contracts inference); the category GROUP decides when a leaf is not listed
CATEGORY_KIND = {"housing.energy": "energy", "housing.water": "water", "subscriptions.telecom": "telecom",
                 "subscriptions.video_streaming": "streaming", "subscriptions.music_streaming": "streaming",
                 "subscriptions.news_media": "streaming", "subscriptions.software_cloud": "software",
                 "subscriptions.memberships": "membership", "leisure.sports_activities": "membership",
                 "housing.home_insurance": "insurance_home", "transport.car_insurance": "insurance_car",
                 "health.health_insurance": "insurance_health"}


def series_contracts(ds, rec) -> dict:
    """series id -> contract id: the memory link (``merchant_match``), or the contract drafted from that series when its bank label could
    not be matched (no ``merchant_match``: its notes say ``drafted from rec_...``)."""
    out: dict[str, str] = {}
    for _rel, c in ds.memory.contracts:
        m = DRAFTED_RE.search(c.notes or "")
        if m and not c.merchant_match:
            out[m.group(1)] = c.id
    for x in rec.series:
        link = next((l.id for l in x.links if l.kind == "contract"), None)
        if link:
            out[x.id] = link
    return out


def kind_of_category(category: str) -> str:
    """The contract kind inferred from the category group (insurance_*, energy, telecom, streaming, software, membership)."""
    if category in CATEGORY_KIND:
        return CATEGORY_KIND[category]
    g = group_of(category)
    # an insurance that is not home / car / health (life, pet, legal protection...) is "insurance_other": the Code des assurances anniversary
    # rule applies, the home / car free cancellation (Hamon) does not
    return {"streaming_media": "streaming", "software_cloud": "software", "telecom": "telecom", "memberships": "membership",
            "energy_utilities": "energy", "insurance": "insurance_other"}.get(g or "", "other")


def _contract_status(c, today: dt.date) -> dict:
    if c is None:
        return {"status": "missing", "id": None}
    dates = [d for d in (c.renewal, c.commitment_end) if d is not None]
    st = "expired" if dates and max(dates) < today else "on_file"
    return {"status": st, "id": c.id, "provider": c.provider, "kind": c.kind, "contract_number_on_file": bool(c.contract_number),
            "start_date": c.start_date, "renewal": c.renewal, "commitment_end": c.commitment_end,
            "notice_period_days": c.notice_period_days, "keep": c.keep,
            "expired_on": max(dates) if st == "expired" else None,
            "documents": len(c.documents or [])}


def price_history(x) -> list[dict]:
    """The distinct price levels of a series, oldest first: ``{from, amount}`` (absolute payment amount)."""
    out: list[dict] = []
    for o in x.occurrences:
        a = abs(o.amount_c)
        if not out or out[-1]["_c"] != a:
            out.append({"from": o.date, "amount": money_str(a), "_c": a})
    for h in out:
        h.pop("_c")
    return out


def _pc_row(c) -> dict:
    return {"id": c.id, "date": c.date, "old": money_str(c.old_c), "new": money_str(c.new_c), "direction": c.direction,
            "pct": c.pct, "yearly_impact": money_str(c.yearly_impact_c), "confirmed": c.confirmed}


def _contract_cost_c(c) -> Optional[tuple]:
    b = c.billing
    if b is None or b.amount is None or not b.period:
        return None
    yearly = round(b.amount * 100 * PERIODS_PER_YEAR[b.period])
    return div_cents(yearly, 12), yearly


def build(ds, rec, pcs=None, *, country: Optional[str] = None, alternatives=(), decisions=(), asked: frozenset = frozenset(),
          include_ended: bool = False, series_filter=None) -> dict:
    """The inventory at ``ds.today``. ``alternatives`` / ``decisions``: loaded rows (any state; only confirmed decisions count)."""
    today = ds.today
    country = (country or ds.memory.country or "FR").upper()
    contracts = {c.id: (rel, c) for rel, c in ds.memory.contracts}
    # a contract drafted from a series whose bank label could not be matched (no merchant_match) still belongs to that series
    smap = series_contracts(ds, rec)

    def contract_of(x) -> Optional[str]:
        return smap.get(x.id)

    def series_of(cid: str) -> list:
        return [s for s in rec.series if contract_of(s) == cid]
    changes: dict[str, list] = {}
    for c in (pcs.changes if pcs is not None else []):
        if c.effect == "costs_more" or c.effect == "costs_less":
            changes.setdefault(c.series_id, []).append(c)
    alts_by: dict[str, list] = {}
    for a in alternatives:
        for k in (a.contract_id, a.series_id):
            if k:
                alts_by.setdefault(k, []).append(a)
    decs_by: dict[str, list] = {}
    for d in decisions:
        for k in (d.contract_id, d.series_id):
            if k:
                decs_by.setdefault(k, []).append(d)
    rows: list[dict] = []
    seen_contracts: set = set()

    def finish(row, terms_c, x, c, monthly_c, paying):
        res = C.cancellability(terms_c, today, country, messages=True)          # the web reads the *_msg siblings (MCP: subs/tools.py picks fields)
        row["cancellation"] = C.cancellation_info(res)
        u = U.usage_of(c)
        sig = U.signals(u, paying=paying, today=today, last_payment=x.last_date if x else None)
        row["usage"] = {"frequency": u["frequency"], "last_used": u["last_used"], "note": u["note"], "recorded": u["recorded"],
                        "signals": sig, "measurable": bool(sig) or u["recorded"],
                        "question_asked": bool(x and f"usage:{x.id}" in asked),
                        "note_not_measurable": None if u["recorded"] else U.NOT_MEASURABLE,
                        "note_not_measurable_msg": None if u["recorded"] else U.NOT_MEASURABLE_MSG}
        ks = [k for k in (row["contract_id"], row["series_id"]) if k]
        alts = list({a.id: a for k in ks for a in alts_by.get(k, [])}.values())
        alts.sort(key=lambda a: (a.retrieved_at, a.id), reverse=True)
        row["alternatives"] = A.summarize(alts, monthly_c, today)
        dec = list({d.id: d for k in ks for d in decs_by.get(k, [])}.values())
        confirmed = [d for d in dec if d.state == "confirmed"]
        latest = max(confirmed, key=lambda d: (d.decided_on, d.id), default=None)
        row["decision"] = None if latest is None else DEC.status_row(latest, rec, today)
        if row["decision"]:
            row["decision"].pop("_saving_c"), row["decision"].pop("_since_c")
        row["proposed_decisions"] = [d.id for d in dec if d.state == "proposed"]
        return row

    for x in rec.series:
        if x.kind != "expense":
            continue
        g = group_of(x.category)
        if g is None or (x.status != "active" and not include_ended):
            continue
        if series_filter is not None and not series_filter(x):
            continue
        cid = contract_of(x)
        rel_c = contracts.get(cid) if cid else None
        c = rel_c[1] if rel_c else None
        if c is not None:
            seen_contracts.add(c.id)
        monthly = div_cents(x.yearly_cost_c, 12)
        pcs_x = sorted(changes.get(x.id, []), key=lambda p: p.date)
        row = {"ref": x.id, "name": (c.provider if c and c.provider else x.entity), "entity": x.entity, "group": g,
               "group_label": GROUP_LABEL[g], "category": x.category, "kind": c.kind if c and c.kind else kind_of_category(x.category),
               "cost_source": "series", "cadence": x.cadence, "expected_amount": money_str(abs(x.expected_amount_c)),
               "monthly": money_str(monthly), "yearly": money_str(x.yearly_cost_c), "status": x.status,
               "first_seen": x.first_date, "last_payment": x.last_date, "next_charge": x.next_expected if x.status == "active" else None,
               "overdue_days": x.overdue_days, "account": x.account_label,
               "price_history": price_history(x), "price_changes": [_pc_row(p) for p in pcs_x],
               "contract": _contract_status(c, today), "series_id": x.id, "series": [x.id], "contract_id": c.id if c else None,
               "linked_series": [s.id for s in series_of(c.id)] if c else [],
               "draftable": c is None, "_monthly_c": monthly, "_yearly_c": x.yearly_cost_c}
        terms = C.terms_for_series(x, c, infer_fee=True)
        rows.append(finish(row, terms, x, c, monthly, x.status == "active"))
    # contract files no listed series explains
    for rel, c in ds.memory.contracts:
        if c.id in seen_contracts:
            continue
        linked = series_of(c.id)
        if linked and not include_ended:
            ended_only = all(s.status != "active" for s in linked)
            if not ended_only:
                continue                                  # its (filtered-out) active series belongs to another scope
        elif linked:
            continue
        cost = _contract_cost_c(c)
        g = KIND_GROUP.get(c.kind or "other", "other_subscriptions")
        paying = None if not linked else False
        row = {"ref": f"contract:{c.id}", "name": c.provider or c.id, "entity": c.provider or c.id, "group": g, "group_label": GROUP_LABEL[g],
               "category": None, "kind": c.kind or "other", "cost_source": "contract" if cost else "unknown", "cadence": c.billing.period if c.billing else None,
               "expected_amount": money_str(round(c.billing.amount * 100)) if c.billing and c.billing.amount is not None else None,
               "monthly": money_str(cost[0]) if cost else None, "yearly": money_str(cost[1]) if cost else None,
               "status": "ended" if linked else "contract_only", "first_seen": None,
               "last_payment": max(s.last_date for s in linked) if linked else None, "next_charge": None, "overdue_days": 0,
               "account": None, "price_history": [], "price_changes": [], "contract": _contract_status(c, today),
               "series_id": linked[0].id if linked else None, "series": [s.id for s in linked], "contract_id": c.id, "linked_series": [s.id for s in linked],
               "draftable": False, "_monthly_c": cost[0] if cost else None, "_yearly_c": cost[1] if cost else 0}
        rows.append(finish(row, C.terms_of(c), linked[0] if linked else None, c, cost[0] if cost else None, paying))
    order = {g: i for i, g in enumerate(GROUPS)}
    rows.sort(key=lambda r: (order.get(r["group"], 99), -(r["_yearly_c"] or 0), r["ref"]))
    return _wrap(ds, rows, country, rec, alternatives, decisions)


def _wrap(ds, rows, country, rec, alternatives, decisions) -> dict:
    today = ds.today
    active = [r for r in rows if r["status"] in ("active", "contract_only")]
    groups: dict[str, dict] = {}
    for r in active:
        g = groups.setdefault(r["group"], {"label": r["group_label"], "count": 0, "monthly_c": 0, "yearly_c": 0, "without_contract": 0,
                                           "unknown_cost": 0})
        g["count"] += 1
        if r["_yearly_c"]:
            g["monthly_c"] += r["_monthly_c"]
            g["yearly_c"] += r["_yearly_c"]
        else:
            g["unknown_cost"] += 1
        g["without_contract"] += r["contract"]["status"] == "missing"
    tot_y = sum(r["_yearly_c"] or 0 for r in active)
    tot_m = sum(r["_monthly_c"] or 0 for r in active)
    for r in rows:
        r.pop("_monthly_c"), r.pop("_yearly_c")
    cov = sum(1 for r in active if r["cancellation"]["legal_basis"])
    decidable = sum(1 for r in active if r["cancellation"]["can_cancel_now"] is not None)
    sav = DEC.savings(list(decisions), rec, today)
    return {"as_of": today, "country": country, "rows": rows,
            "groups": {g: {"label": v["label"], "count": v["count"], "monthly": money_str(v["monthly_c"]), "yearly": money_str(v["yearly_c"]),
                           "without_contract": v["without_contract"], "unknown_cost": v["unknown_cost"]}
                       for g, v in sorted(groups.items(), key=lambda kv: (-kv[1]["yearly_c"], kv[0]))},
            "totals": {"services": len(active), "monthly": money_str(tot_m), "yearly": money_str(tot_y),
                       "without_contract": sum(1 for r in active if r["contract"]["status"] == "missing"),
                       "expired_contracts": sum(1 for r in active if r["contract"]["status"] == "expired"),
                       "with_cancellation_rule": cov, "cancellation_decidable": decidable,
                       "usage_unknown": sum(1 for r in active if not r["usage"]["recorded"] and r["group"] in
                                            ("streaming_media", "software_cloud", "memberships", "other_subscriptions")),
                       "outdated_alternatives": sum(r["alternatives"]["outdated"] for r in active),
                       "reminders": sum(len(r["usage"]["signals"]) for r in active) + len(sav["reminders"])},
            "savings": {k: sav[k] for k in ("realised_monthly", "realised_since_decisions", "realised_yearly_run_rate", "verified",
                                            "pending", "contradicted", "claimed_monthly_unverified")},
            "notes": [m["text"] for m in NOTES], "notes_msg": list(NOTES)}


NOTES = (server_msg("subs.inventory.notSubscriptions", "loans, mortgages, rent and taxes are not subscriptions and are not listed"),
         server_msg("subs.inventory.costSource",
                    "the cost comes from the detected payments; a contract file without payments in the bank data uses its billing amount"),
         server_msg("subs.inventory.cancellationSummary", "cancellation info is a general summary of consumer-law rules: verify with your contract"))
