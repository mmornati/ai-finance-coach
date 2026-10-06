"""The finance MCP tools of the loans epic (E9): ``net_worth`` and ``loans_overview`` (both read-only).

They sit behind the same choke point as every tool (``ToolSession._publish``): untrusted text wrapped, memory ids pseudonymised, the
final privacy assertion. What is generalised before it gets there: bank accounts are the account pseudonyms (``account-main-1``), assets and
liabilities are named by their KIND ("employee savings plan", "house", "mortgage", "car lease (LOA)"), never by their description, provider,
lender or the financed asset; owners are the owner pseudonyms (``joint``, ``adult-1``). Amounts and dates are allowed. Lender account numbers,
contract numbers, payment labels (``payment_match``) and the postal / contact details never appear: nothing here reads them.
"""
from __future__ import annotations

from coach.analytics.common import _plain, money_str
from coach.loans import history as H, networth as NW, service as LS
from coach.mcp.tools import DATA_NOTE, ToolError, ToolSession, ToolSpec, _obj
from coach.skills.tools import _recurring, _today

MAX_MONTHS = 60


def _owner(s: ToolSession, o: str) -> str:
    return o if o == NW.UNASSIGNED else s.reg.red.owner_of(o)


def _net_worth(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    today = _today(s)
    idmap = s.data()[5]
    cfg = s.cfg
    nw = NW.build(ds, today, asset_stale_months=cfg.memory_asset_stale_months, liability_stale_months=cfg.memory_stale_months,
                  schedules=LS.schedules(ds))
    comps = []
    for c in nw.components:
        if c.type == "account":
            acc = ds.accounts.get(c.id)
            ref, name = reg.red.account.get(c.id, "account"), f"{(acc.purpose if acc else None) or 'bank'} account"
        elif c.type == "asset":
            ref, name = idmap.fwd.get(c.id, "asset"), NW.GENERIC_NAME.get(c.kind or "", "asset")
        else:
            ref, name = idmap.fwd.get(c.id, "liability"), NW.GENERIC_NAME.get(c.kind or "", "loan")
        row = {"type": c.type, "ref": ref, "label": name, "category": c.category, "owner": _owner(s, c.owner), "status": c.status,
               "amount": money_str(c.amount_c), "as_of": c.as_of, "stale": c.stale, "source": c.source,
               "balance_type": c.balance_type}
        if c.status != "known":
            row["reason"] = (c.note or "unknown").split(":")[0]            # "capital unknown", "no value recorded", "no balance synced yet"
        comps.append({k: v for k, v in row.items() if v is not None})
    unknown = [{"type": u["type"], "ref": next((x["ref"] for x, c in zip(comps, nw.components) if c.id == u["id"] and c.type == u["type"]), "?"),
                "reason": u["reason"].split(":")[0]} for u in nw.unknown]
    raw = {"as_of": nw.as_of, "net_worth": money_str(nw.net_worth_c), "assets": money_str(nw.assets_c),
           "liabilities": money_str(nw.liabilities_c), "complete": nw.complete, "n_unknown": nw.n_unknown,
           "by_category": {k: money_str(v) for k, v in nw.by_category_c.items()},
           "by_owner": {_owner(s, o): {"assets": money_str(v["assets_c"]), "liabilities": money_str(v["liabilities_c"]),
                                       "net_worth": money_str(v["net_worth_c"]), "n_unknown": v["n_unknown"]}
                        for o, v in nw.by_owner.items()},
           "components": comps, "unknown": unknown,
           "stale": [{"type": x["type"], "ref": next((c["ref"] for c, k in zip(comps, nw.components) if k.id == x["id"] and k.type == x["type"]), "?")}
                     for x in nw.stale],
           "notes": ["net_worth = known assets - known liabilities; an item listed in `unknown` is NOT counted, so the real figure differs",
                     "liabilities are computed from the amortization schedule when it is computable, else the declared capital, else unknown"]}
    if a.get("history"):
        n = min(int(a.get("months") or 24), MAX_MONTHS)
        raw["history"] = [{k: p[k] for k in ("month", "source", "net_worth", "assets", "liabilities", "by_category", "n_unknown", "complete")}
                          for p in H.series(s.con, n)]
        for h, p in zip(raw["history"], H.series(s.con, n)):
            h["n_newly_counted"] = len(p.get("newly_counted", []))
            if p.get("caveat"):
                h["caveat"] = p["caveat"]
        raw["history_note"] = ("'snapshot' = recorded on that day; 'backfill' = rebuilt from bank balances, transactions, amortization "
                               "schedules and manual assets (from their own as_of date): a month with n_unknown > 0 is the known part only, "
                               "and an item counted from a month on (n_newly_counted) makes the total jump without any change in wealth")
    n_known = sum(1 for c in nw.components if c.status == "known")
    n_unknown = nw.n_unknown
    raw["partial"] = not nw.complete
    raw["n_items_counted"], raw["n_items_unknown"] = n_known, n_unknown
    raw["known_share"] = round(n_known / (n_known + n_unknown), 2) if (n_known + n_unknown) else None
    raw["caveat"] = (None if nw.complete else
                     f"PARTIAL: {n_unknown} item(s) have no known value and are NOT in the totals (known share {raw['known_share']}). "
                     "`net_worth` is the known part only: never quote it as the household's net worth, say it is partial and name what is unknown.")
    out = _plain(raw)
    out["note"] = "Numbers are computed. Quote them exactly; never add or estimate an unknown item."
    return out


def _loans_overview(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    idmap = s.data()[5]
    rec = _recurring(s)
    rows = []
    for _rel, lb in ds.memory.liabilities:
        o = LS.overview_of(ds, rec, lb)
        sch = o["schedule"]
        row = {"ref": idmap.fwd.get(lb.id, "liability"), "kind": lb.kind,
               "schedule": {k: sch.get(k) for k in ("status", "mode", "missing", "alternative", "approximate", "payment", "remaining_capital",
                                                      "payments_made", "remaining_instalments", "next_due", "last_due", "total_interest",
                                                      "total_insurance", "total_cost", "remaining_interest", "payment_check", "outstanding_check", "source")
                            if sch.get(k) is not None},
               "payments": {k: o["payments"].get(k) for k in ("count", "first", "last", "last_amount", "median_amount") if o["payments"].get(k) is not None},
               "alerts": [{"type": x["type"], "severity": x["severity"], "date": x["date"], "amount": x.get("amount"),
                           "expected_date": x.get("expected_date"), "expected_amount": x.get("expected_amount"),
                           "evidence": [reg.red.tx(e) for e in x.get("evidence", [])[:2]]} for x in o["alerts"]]}
        if sch.get("by_year") and a.get("years", True):
            row["interest_by_year"] = [{"year": y["year"], "interest": y["interest"], "insurance": y["insurance"], "principal": y["principal"],
                                        "partial": y["partial"]} for y in sch["by_year"]]
        inf = o["inference"]
        if inf.get("fields"):
            row["inferred_suggestions"] = [{"field": f["field"], "value": f["value"], "confidence": f["confidence"], "inferred": True,
                                            "method": f["method"]} for f in inf["fields"]]
            row["inference_note"] = "SUGGESTIONS inferred from bank payments, never stored: the user confirms them (coach loans infer --propose)"
        if o["lease"]:
            ls = o["lease"]
            row["lease"] = {"end": ls["end"], "decision": {k: v for k, v in ls["decision"].items() if k in ("residual_value", "needs")},
                            "mileage": {k: v for k, v in ls["mileage"].items() if k not in ("readings",)}, "missing": ls["missing"]}
        rows.append(row)
    out = _plain({"as_of": ds.today, "loans": rows})
    out["note"] = ("Computed from the loan files and the bank payments; a missing field is listed, never guessed. Anything under "
                   "`inferred_suggestions` is an inference to confirm with the user, not a fact.")
    return out


def specs() -> list[ToolSpec]:
    return [
        ToolSpec("net_worth", "Net worth today (and optionally its monthly history): bank balances + manual assets (savings, investments, real "
                 "estate, vehicles) - what is owed, by category and by owner. Liabilities come from the amortization schedule when computable, "
                 "else the declared capital, else they are UNKNOWN: unknown items are listed, never counted and never estimated. Assets and "
                 "liabilities are named by kind only (e.g. 'employee savings plan', 'house', 'mortgage')." + DATA_NOTE,
                 _obj({"history": {"type": "boolean", "description": "include the monthly history (snapshots + back-filled months)"},
                       "months": {"type": "integer", "minimum": 1, "maximum": MAX_MONTHS, "description": "history length (default 24)"}}),
                 _net_worth),
        ToolSpec("loans_overview", "Every loan / lease: amortization schedule status (or the exact missing fields), capital still due, interest and "
                 "insurance by calendar year, the bank payments seen, payment alerts (missed, changed, extra, wrong account), suggestions inferred "
                 "from the payments (marked inferred, with a confidence), and for a car lease the end-of-contract view (residual value, mileage "
                 "projection). Loans are named by pseudonym and kind only." + DATA_NOTE,
                 _obj({"years": {"type": "boolean", "description": "include interest by year (default true)"}}), _loans_overview),
    ]
