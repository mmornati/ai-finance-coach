"""The finance MCP tool of the rental epic (E15): ``rental_overview`` (read-only).

Behind the same choke point as every tool (``ToolSession._publish``). What is generalised before it gets there: a property is its memory
pseudonym (``asset-1``) and the KIND ``rental property``; its bank account is the account pseudonym (``account-rental-1``); its loan is
``liability-1`` and its kind; a payment is its hashed ``ref``; the scheme name goes through the household scrubber. The postal address, the
property manager, the lender, the tenant and the notes are never read here. Amounts, dates and the figures the owner declared (rent cap, income
limit, rates, valuation) are allowed. Nothing is written and nothing is looked up: a missing figure is listed under ``missing``.
"""
from __future__ import annotations

import datetime as dt

from coach.mcp.tools import DATA_NOTE, DATE, ToolError, ToolSession, ToolSpec, _obj
from coach.rental import cashflow as CF, indicators as IND, model as M, scheme as SC, taxyear as TX
from coach.rental.render import plain

SECTIONS = ("cashflow", "pnl", "scheme", "tax", "indicators")


def _ref(s: ToolSession, real_id: str, kind: str) -> str:
    return s.data()[5].fwd.get(real_id, kind)


def _real_property(s: ToolSession, value: str) -> str:
    rev = s.data()[5].rev
    if value not in rev:
        raise ToolError("unknown property: use an id exactly as shown by `rental_overview` or `memory_context` (for example asset-1)")
    return rev[value]


def _tax_year(today: dt.date) -> int:
    return today.year if today.month >= 10 else today.year - 1


def _rental_overview(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    red = reg.red
    props = M.properties(ds)
    today = ds.today
    out: dict = {"as_of": today.isoformat(), "properties": []}
    if not props:
        out["note"] = ("no rental property is recorded: an asset of kind real_estate_rental in assets.yaml describes it (`coach rental edit` or "
                       "the Rental page); the user records it, a model only proposes")
        out["unlinked_rental_accounts"] = [red.account.get(u, "account") for u in M.unlinked_rental_accounts(ds)]
        return out
    if a.get("property"):
        want = _real_property(s, a["property"])
        props = [p for p in props if p.id == want]
        if not props:
            raise ToolError("that asset is not a rental property")
    sections = tuple(a.get("sections") or SECTIONS)
    months = int(a.get("months") or 12)
    market_date = dt.date.fromisoformat(a["market_rate_date"]) if a.get("market_rate_date") else None
    fees = {k: a[k] for k in ("bank_fees", "guarantee_fees", "other_fees", "penalty") if a.get(k) is not None}
    for p in props:
        row: dict = {"ref": _ref(s, p.id, "asset"), "kind": "rental property",
                     "links": {"account": [red.account.get(u, "account") for u in p.accounts], "account_link": p.account_link,
                               "loans": [{"ref": _ref(s, lb.id, "liability"), "kind": lb.kind} for lb in p.loans], "loan_link": p.loan_link}}
        if p.account_link == "only_one":
            row["links"]["note"] = "the account was linked because it is the only rental account and this the only property without one: confirm it"
        if "cashflow" in sections:
            cm = CF.monthly(ds, p, months)
            c = plain(cm)
            for r in c["months"]:
                r["rent_evidence"] = [red.tx(k) for k in r["rent_evidence"]]
            c["current_month"] = plain(CF.current_month(ds, p, ds.settings.rental_rent_grace_days))
            c["definition"] = ("net = rent - loan - charges - fees - taxes - insurance - works - other (a cash flow: the loan principal is "
                               "included); effort = the shortfall of the month the owner funds; the owner's own transfers are not rent")
            row["cashflow"] = c
            review = CF.flows_to_review(ds, p)
            if review:
                by: dict = {}
                for t in review:
                    e = by.setdefault(t.category, {"category": t.category, "n": 0, "total_c": 0, "evidence": []})
                    e["n"] += 1
                    e["total_c"] += t.amount_c
                    if len(e["evidence"]) < 2:
                        e["evidence"].append(red.tx(t.key))
                row["flows_to_label"] = plain({"n": len(review), "by_category": sorted(by.values(), key=lambda x: x["category"]),
                                               "note": "flows of the property account that are in no property category: the user labels them "
                                                       "(a memory annotation, proposed with `memory_propose`)"})
        if "pnl" in sections:
            yr = int(a["year"]) if a.get("year") else int(CF.last_closed_month(today)[:4])
            row["pnl"] = plain(CF.year_pnl(ds, p, yr))
        if "scheme" in sections:
            rows, _ = CF.all_rows(ds, p)
            last_rent = next((r for r in reversed(rows) if r.rent_c > 0), None)
            sc = plain(SC.status(ds, p, ds.settings.rental_reminder_months, last_rent.rent_c if last_rent else None))
            name = sc.pop("scheme", None)
            if name:
                sc["name"] = red.text(str(name))              # the scheme's name is the owner's free text: scrubbed, and wrapped as untrusted by `name`
            row["scheme"] = sc
        if "tax" in sections:
            yr = int(a["year"]) if a.get("year") else _tax_year(today)
            t = plain(TX.tax_year(ds, p, yr))
            row["tax"] = t
        if "indicators" in sections:
            row["indicators"] = plain(IND.indicators(ds, p, market_rate_pct=a.get("market_rate_pct"), market_rate_date=market_date, fees=fees))
        out["properties"].append(row)
    out["unlinked_rental_accounts"] = [red.account.get(u, "account") for u in M.unlinked_rental_accounts(ds)]
    out["note"] = ("Computed from the property account, the loan schedule and the facts the owner recorded. A fact listed under `missing` is NOT "
                   "known and must never be estimated; say it is missing and offer to propose it (`memory_propose`, `questions_propose`). The tax "
                   "section is a set of CANDIDATES for the rental-income return, not a return and not tax advice; the indicators are facts and "
                   "their neutral reading, never a recommendation to sell, keep or switch lender. End with the disclaimer of the section you used.")
    return out


def specs() -> list[ToolSpec]:
    return [
        ToolSpec("rental_overview", "Rental property under a tax-incentive scheme (E15): the monthly cash flow and the yearly P&L (rent - loan - "
                 "charges - fees - taxes - insurance - works), the monthly effort d'epargne, the vacancy months (expected rent missing), the scheme "
                 "commitment (start, length, end, extension decision, rent cap and tenant income checks from the user's own figures), the "
                 "tax-year candidates of the rental-income return (micro-foncier vs reel figures, loan interest from the amortization schedule, "
                 "the scheme reduction from the declared price and rate, a documents checklist) and the renegotiate-or-sell indicators (loan rate "
                 "vs the market rate the user entered, end of the commitment, net equity = declared valuation - capital due). Properties are "
                 "`asset-N`, accounts and loans pseudonyms; never an address, manager, lender or tenant. Missing facts are listed, never guessed."
                 + DATA_NOTE,
                 _obj({"property": {"type": "string", "maxLength": 80, "description": "a property id as shown by this tool (asset-1); default all"},
                       "sections": {"type": "array", "maxItems": 5, "items": {"type": "string", "enum": list(SECTIONS)},
                                    "description": "which parts to return (default all five)"},
                       "months": {"type": "integer", "minimum": 1, "maximum": 36, "description": "months of cash flow (default 12)"},
                       "year": {"type": "integer", "minimum": 2000, "maximum": 2100, "description": "the year of the P&L and of the tax summary"},
                       "market_rate_pct": {"type": "number", "minimum": 0, "maximum": 25, "description": "a market rate the user gave (never invent one)"},
                       "market_rate_date": DATE,
                       "bank_fees": {"type": "number", "minimum": 0, "maximum": 100000}, "guarantee_fees": {"type": "number", "minimum": 0, "maximum": 100000},
                       "other_fees": {"type": "number", "minimum": 0, "maximum": 100000},
                       "penalty": {"type": "number", "minimum": 0, "maximum": 1000000, "description": "the early-repayment amount of the user's contract"}}),
                 _rental_overview),
    ]
