"""CLI of the rental epic (E15): ``coach rental ...``.

    coach rental list                          every rental property: account and loan links, rent, last month, scheme end, missing facts
    coach rental show [ID]                     one property in full
    coach rental cashflow [ID] [--months N]    the monthly cash flow: rent, loan, charges, fees, taxes, insurance, works, the effort d'epargne
    coach rental pnl [ID] [--year Y]           the yearly P&L (and the loan interest / principal split of the schedule)
    coach rental scheme [ID] [--propose-questions]   the scheme commitment: dates, reminders, rent cap and tenant income checks, missing facts
    coach rental tax [ID] [--year Y]           the figures of the rental-income return (micro-foncier / reel), the scheme reduction, the documents
    coach rental indicators [ID] [--market-rate R --market-date D]   loan rate vs market, end of the commitment, net equity
    coach rental flows [ID]                    the flows of the property account that are in no property category (to label)
    coach rental add ID / edit ID --set path=value / extension ID / vacancy ID / market-rate ID    record the facts (previewed, typed yes)

Every write goes through the memory store: validated, PREVIEWED (the diff) and written only after a typed yes (``--yes`` is for the person at
the keyboard; the coach skills never pass it), source ``cli``. Nothing is looked up on the web and nothing is guessed: what is missing is listed.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Optional

from coach.analytics.common import money_str
from coach.i18n_msg import strip_msgs
from coach.loans.commands import _confirm, _ctx, _parse_sets, _store
from coach.rental import cashflow as CF, indicators as IND, model as M, scheme as SC, service as RS, taxyear as TX
from coach.rental.render import plain


def _j(obj) -> str:
    return json.dumps(strip_msgs(plain(obj)), ensure_ascii=False, indent=2, default=str)       # *_msg: the web's only


def _m(c) -> str:
    return "?" if c is None else money_str(c)


def _find(ds, ident: Optional[str]) -> M.Property:
    try:
        return M.find(ds, ident)
    except LookupError as e:
        sys.exit(f"error: {e}")


def _year(ds, a) -> int:
    return a.year if getattr(a, "year", None) else int(CF.last_closed_month(ds.today)[:4])


def _tax_year(ds, a) -> int:
    return a.year or (ds.today.year if ds.today.month >= 10 else ds.today.year - 1)


# ---------------------------------------------------------------- read commands

def cmd_list(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        props = M.properties(ds)
        rows = [RS.summary_row(ds, p) for p in props]
        if a.json:
            print(_j({"properties": rows, "unlinked_rental_accounts": len(M.unlinked_rental_accounts(ds))}))
            return
        if not rows:
            print("no rental property on file: an asset of kind real_estate_rental in assets.yaml (`coach rental add ID`)")
        for r in rows:
            sc = r["scheme"]
            lm = r["last_month"]
            print(f"{r['id']:<22}account {r['account_link']:<9}loan {r['loan_link']:<9}rent expected {_m(r['expected_rent_c']):>9} ({r['rent_source']})")
            if lm:
                print(f"{'':<22}{lm['month']}: rent {_m(lm['rent_c'])}, net {_m(lm['net_c'])}, effort {_m(lm['effort_c'])}, rent {lm['rent_status']}"
                      + ("" if lm["complete"] else "  (data incomplete)"))
            print(f"{'':<22}vacancy: {r['vacancy']['n_missing']} month(s) without rent, {r['vacancy']['n_declared']} declared"
                  + (f"; scheme {sc['scheme'] or '?'} {sc['state']}, ends {sc['end_date'] or '?'}" if sc["declared"] else "; no scheme recorded")
                  + (f"; net equity {_m(r['net_equity_c'])}" if r["net_equity_c"] is not None else ""))
            if r["missing"]:
                print(f"{'':<22}missing: {', '.join(r['missing'])} (`coach rental scheme {r['id']} --propose-questions`)")
        n = len(M.unlinked_rental_accounts(ds))
        if n:
            print(f"{n} account(s) flagged 'rental' belong to no property (`coach rental edit ID --set account=...`)")
    finally:
        con.close()


def _print_cashflow(cm: dict) -> None:
    print(f"{'month':<8}{'rent':>9}{'loan':>9}{'charges':>9}{'fees':>8}{'taxes':>9}{'insur.':>8}{'works':>9}{'other':>8}{'net':>10}{'effort':>9}  rent")
    for r in cm["months"]:
        print(f"{r.month:<8}{_m(r.rent_c):>9}{_m(r.loan_c):>9}{_m(r.charges_c):>9}{_m(r.fees_c):>8}{_m(r.taxes_c):>9}{_m(r.insurance_c):>8}"
              f"{_m(r.works_c):>9}{_m(r.other_c):>8}{_m(r.net_c):>10}{_m(r.effort_c):>9}  {r.rent_status.replace('_', ' ')}" + ("" if r.complete else "  *"))
    print("(* = the account data do not cover the whole month; figures in EUR, a cash flow: the loan principal is included)")
    av = cm.get("average_c")
    if av:
        print(f"average of the {cm['n_complete']} complete month(s): rent {_m(av['rent_c'])}, costs {_m(av['costs_c'])}, net {_m(av['net_c'])}, "
              f"effort d'epargne {_m(av['effort_c'])} a month")
    rent = cm["rent"]
    print(f"expected rent {_m(rent['expected_c'])} ({rent['source']}); first month let {rent['first_month'] or '?'}")
    v = cm["vacancy"]
    print(f"vacancy: {v['n_missing']} month(s) without rent and no explanation" + (f" ({', '.join(v['missing_months'][-6:])})" if v["missing_months"] else "")
          + f", {v['n_declared']} declared" + (f", occupancy {v['occupancy_rate'] * 100:.0f} %" if v["occupancy_rate"] is not None else ""))


def cmd_cashflow(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        cm = CF.monthly(ds, p, a.months)
        cur = CF.current_month(ds, p, ds.settings.rental_rent_grace_days)
        if a.json:
            print(_j({"id": p.id, "cashflow": cm, "current_month": cur}))
            return
        if not cm["months"]:
            print("no closed month of data for this property yet (link its account: `coach rental edit ID --set account=...`)")
            return
        _print_cashflow(cm)
        print(f"this month ({cur['month']}): {cur['status']}" + (f", rent seen {_m(cur['rent_c'])}" if cur["rent_c"] else ""))
    finally:
        con.close()


def _print_pnl(p: dict) -> None:
    t = p["totals"]
    print(f"P&L {p['year']}: {p['n_months']} closed month(s)" + ("" if p["complete"] else "  (not a complete year: see below)"))
    for label, k in (("rent received", "rent_c"), ("- loan instalments", "loan_c"), ("- co-ownership charges", "charges_c"), ("- management fees", "fees_c"),
                     ("- property tax", "taxes_c"), ("- insurance (PNO, GLI)", "insurance_c"), ("- works and repairs", "works_c"), ("- other flows", "other_c")):
        print(f"  {label:<26}{_m(t[k]):>12}")
    print(f"  {'= net cash result':<26}{_m(t['net_c']):>12}   effort d'epargne {_m(t['effort_c'])} (about {_m(p['monthly_average_effort_c'])} a month)")
    print(f"  the owner's own transfers into the account {_m(t['owner_in_c'])} (not rent)")
    if p["loan_split"]:
        sp = p["loan_split"]
        print(f"  loan of the year (schedule): interest {_m(sp['interest_c'])}, borrower insurance {_m(sp['insurance_c'])}, principal {_m(sp['principal_c'])}")
        print(f"  economic result (principal not a cost): {_m(p['economic']['result_c'])}")
    if p.get("gross_yield_pct") is not None:
        print(f"  gross yield on the declared value: {p['gross_yield_pct']} %")
    for k, lab in (("months_incomplete", "months not fully covered by the account data"), ("months_missing_data", "closed months without any data")):
        if p[k]:
            print(f"  {lab}: {', '.join(p[k])}")


def cmd_pnl(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        res = CF.year_pnl(ds, p, _year(ds, a))
        print(_j(res)) if a.json else _print_pnl(res)
    finally:
        con.close()


def _print_scheme(s: dict) -> None:
    if not s["declared"]:
        print("no scheme recorded for this property (`coach rental edit ID --set scheme=NAME --set commitment.start_date=... --set commitment.years=9`)")
    else:
        print(f"scheme: {s['scheme'] or '(name not recorded)'}; commitment {s['start_date'] or '?'} for {s['years'] or '?'} year(s), ends "
              f"{s['end_date'] or '?'}" + (f" ({s['end_source']})" if s["end_date"] else ""))
        if s.get("state") != "unknown":
            print(f"  {s['state']}: {s.get('progress_pct', 0)} % elapsed, {s.get('days_left', 0)} day(s) / about {s.get('months_left', 0)} month(s) left")
        e = s["extension"]
        print(f"  extension: {e['decision']}" + (f" for {e['years']} year(s)" if e.get("years") else "") + (f", decided on {e['decided_on']}" if e.get("decided_on") else ""))
        if s.get("reminders"):
            print("  reminders: " + ", ".join(f"{r['months_before']} months before ({r['date']})" for r in s["reminders"])
                  + ("; a decision is needed now" if s.get("decision_needed") else ""))
        for w in s.get("warnings", []):
            print(f"  warning: {w}")
        rc = s["rent_cap"]
        print(f"  rent cap: {rc['status'].replace('_', ' ')}" + (f" (rent {_m(rc['rent_c'])} vs cap {_m(rc['cap_c'])}, {rc['rent_basis']})" if rc["status"] != "unknown" else ""))
        ti = s["tenant_income"]
        print(f"  tenant income: {ti['status'].replace('_', ' ')}" + (f" ({_m(ti['tenant_income_c'])} vs limit {_m(ti['limit_c'])})" if ti["status"] != "unknown" else ""))
    if s["missing"]:
        print("missing facts (never guessed):")
        for m in s["missing"]:
            print(f"  - {m['field']}: needed for {m['needed_for']}")


def cmd_scheme(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        rows, _r = CF.all_rows(ds, p)
        last = next((r for r in reversed(rows) if r.rent_c > 0), None)
        s = SC.status(ds, p, ds.settings.rental_reminder_months, last.rent_c if last else None)
        print(_j(s)) if a.json else _print_scheme(s)
        if a.propose_questions:
            _propose_questions(cfg, p, s)
    finally:
        con.close()


def _propose_questions(cfg, prop: M.Property, s: dict) -> None:
    from coach.memory import questions as Q, schemas
    from coach.memory.qgen import qid
    store = _store(cfg)
    key = f"fill:rental:{prop.id}"
    if any(q.key == key for q in store.questions()):
        print("the question about these facts is already on the open-questions list")
        return
    if not s["missing"]:
        print("nothing is missing")
        return
    miss = [m["field"] for m in s["missing"]]
    q = schemas.Question(id=qid("fill", key), topic="Rental property", key=key, origin="manual", created=dt.date.today(),
                         question=(f"Rental property {prop.id}: I still need {', '.join(miss)} to follow its cash flow, its scheme commitment and "
                                   "the tax figures. The deed of purchase, the lease and the loan offer have them."),
                         evidence={"missing": miss}, suggested_target={"file": "assets.yaml", "field": ",".join(miss)})
    Q.add_many(store, [q], source="cli")
    print(f"added 1 open question about {', '.join(miss)} (`coach questions list --open`)")


def _print_tax(t: dict) -> None:
    if t.get("status") != "computed":
        print(t.get("note") or t.get("status"))
        return
    print(f"rental-income return, income year {t['year']} (declared in {t['year'] + 1}): CANDIDATES, not a return")
    print(f"  gross rents of the property {_m(t['gross_rents_c'])} over {t['months_counted']} closed month(s)"
          + (f"; not fully covered: {', '.join(t['months_incomplete'])}" if t["months_incomplete"] else "")
          + (f"; no data: {', '.join(t['months_missing_data'])}" if t["months_missing_data"] else ""))
    mi = t["micro_foncier"]
    print(f"  micro-foncier: abatement {mi['abatement_pct']} % = {_m(mi['abatement_c'])}, taxable {_m(mi['taxable_c'])}"
          + ("" if mi["within_ceiling"] else f"  (the household's gross rents {_m(mi['household_gross_rents_c'])} exceed the ceiling {_m(mi['ceiling_c'])})"))
    rl = t["reel"]
    print("  reel: deductible costs")
    for i in rl["deductible"]:
        print(f"    {i['item']:<46}{_m(i['amount_c']):>12}   {i['bound']}")
    print(f"    {'total deductible':<46}{_m(rl['total_deductible_c']):>12}   net {_m(rl['net_c'])}" + ("  (upper bound: see unknown)" if rl.get("net_is_upper_bound") else ""))
    for u in rl["unknown"]:
        print(f"    unknown: {u}")
    print(f"  the lower taxable figure is the {t['lower_taxable_candidate'].replace('_', '-')} candidate (reel - micro = {_m(t['difference_c'])})")
    sr = t["scheme_reduction"]
    if sr["status"] == "computed":
        print(f"  scheme reduction candidate for {t['year']}: {_m(sr['candidate_c'])} (total {_m(sr['total_c'])} = {sr['rate_pct']} % of {_m(sr['base_c'])}, "
              f"over {sr['years']} year(s) from {sr['first_year']})")
    elif sr["status"] == "needs_fields":
        print(f"  scheme reduction: missing {', '.join(sr['missing'])}")
    print("  documents to gather:")
    for d in t["documents"]:
        print(f"    [ ] {d['item']} ({d['from']})")
    for n in t["notes"]:
        print(f"  note: {n}")
    print(t["disclaimer"])


def cmd_tax(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        t = TX.tax_year(ds, p, _tax_year(ds, a))
        print(_j(t)) if a.json else _print_tax(t)
    finally:
        con.close()


def cmd_indicators(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        day = dt.date.fromisoformat(a.market_date) if a.market_date else None
        fees = {k: getattr(a, k) for k in ("bank_fees", "guarantee_fees", "other_fees", "penalty") if getattr(a, k) is not None}
        res = IND.indicators(ds, p, market_rate_pct=a.market_rate, market_rate_date=day, fees=fees)
        if a.json:
            print(_j(res))
            return
        mk = res["market_rate"]
        print("market rate: " + (f"{mk['rate_pct']} % ({mk['basis']}, {mk.get('date') or 'undated'})" if mk["status"] == "given" else mk["note"]))
        if mk.get("warning"):
            print(f"  warning: {mk['warning']}")
        lr = res["loan_rate"]
        if lr.get("loan_rate_pct") is not None:
            print(f"loan rate {lr['loan_rate_pct']} %" + (f", gap {lr['gap_pts']:+.2f} point(s): {lr['status'].replace('_', ' ')}" if "gap_pts" in lr else ""))
            if lr.get("reading"):
                print(f"  {lr['reading']}")
            rn = lr.get("renegotiation")
            if rn and rn.get("status") == "computed":
                print(f"  renegotiation on the real schedule: instalment {rn['current_payment']} -> {rn['new_payment']}, net saving {rn['net_saving']} EUR "
                      f"after costs {rn['total_costs']} (penalty {rn['penalty']}), break-even {rn['break_even_months'] if rn['break_even_months'] is not None else 'never'} month(s)")
            if lr.get("renegotiation_note"):
                print(f"  {lr['renegotiation_note']}")
        if lr.get("missing"):
            print(f"  missing: {', '.join(lr['missing'])}")
        c = res["commitment"]
        print(f"commitment: {c['state']}" + (f", ends {c['end_date']}, about {c['months_left']} month(s) left" if c.get("end_date") else ""))
        e = res["equity"]
        if e["status"] == "computed":
            print(f"net equity: value {_m(e['value_c'])} - capital due {_m(e['outstanding_c'])} = {_m(e['net_equity_c'])} ({e['equity_share_pct']} % of the value)")
        else:
            print("net equity: unknown, missing " + ", ".join(e.get("missing", ["?"])))
        if e.get("value_warning"):
            print(f"  warning: {e['value_warning']}")
        for s in res["signals"]:
            print(f"- {s['reading']}")
        print(res["disclaimer"])
    finally:
        con.close()


def cmd_flows(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        txs = CF.flows_to_review(ds, p, a.months)
        rows = [{"date": t.date.isoformat(), "amount": money_str(t.amount_c), "category": t.category, "entity": t.entity, "tx": t.key} for t in txs]
        if a.json:
            print(_j({"id": p.id, "flows": rows}))
            return
        if not rows:
            print("every flow of the property account is in a property category")
            return
        print(f"{len(rows)} flow(s) of the property account in no property category (they are counted in 'other flows'):")
        for r in rows[:60]:
            print(f"  {r['date']}  {r['amount']:>10}  {r['category']:<28}{r['entity'][:40]}")
        print("label them with a memory annotation, e.g. `coach memory annotate --merchant-key '^NAME' --category housing.property_charges` "
              "(property categories: " + ", ".join(M.PROPERTY_CATEGORIES) + "); a cost paid from another account counts for the property "
              f"with the tag `{M.tag_of(p.id)}`")
    finally:
        con.close()


def cmd_show(a, cfg):
    con, ds, _rec = _ctx(a, cfg)
    try:
        p = _find(ds, a.id)
        o = RS.overview(ds, p, months=a.months)
        if a.json:
            print(_j({**o, "tax": TX.tax_year(ds, p, _tax_year(ds, a)), "indicators": IND.indicators(ds, p)}))
            return
        lk = o["links"]
        print(f"property {p.id}: account link {lk['account']}, loan link {lk['loan']}" + "".join(f"\n  note: {n}" for n in lk["notes"]))
        if o["cashflow"]["months"]:
            _print_cashflow(o["cashflow"])
            print()
            _print_pnl(o["pnl"])
        print()
        _print_scheme(o["scheme"])
    finally:
        con.close()


# ---------------------------------------------------------------- guided writes

def _asset_rel(store, ident: str) -> None:
    if not any(x.id == ident for x in store.assets()):
        ids = ", ".join(x.id for x in store.assets() if x.kind == M.RENTAL_KIND) or "none"
        sys.exit(f"error: no asset with id {ident!r} in assets.yaml (rental properties: {ids}; create one with `coach rental add {ident}`)")


def cmd_add(a, cfg):
    store = _store(cfg)
    if any(x.id == a.id for x in store.assets()):
        sys.exit(f"error: asset {a.id!r} already exists (`coach rental edit {a.id}`)")
    value = {"id": a.id, "kind": M.RENTAL_KIND}
    ops = [{"op": "append", "path": "assets", "value": value}] if store.exists("assets.yaml") else [{"op": "create", "value": {"assets": [value]}}]
    ops += [{"op": "set", "path": f"assets[{a.id}].{k}", "value": v} for k, v in _parse_sets(a.set)]
    _confirm(a, store, "assets.yaml", ops, "rental-add", a.reason or "coach rental add")
    print(f"next: `coach rental edit {a.id} --set account=<uid or label> --set loan=<loan id>`, `coach accounts set` (purpose rental), `coach rental scheme {a.id}`")


def cmd_edit(a, cfg):
    store = _store(cfg)
    _asset_rel(store, a.id)
    ops = [{"op": "set", "path": f"assets[{a.id}].{k}", "value": v} for k, v in _parse_sets(a.set)]
    ops += [{"op": "unset", "path": f"assets[{a.id}].{p}"} for p in (a.unset or [])]
    if not ops:
        sys.exit("error: give --set path=value (e.g. commitment.years=9) and / or --unset path")
    _confirm(a, store, "assets.yaml", ops, "rental-edit", a.reason or "coach rental edit")


def cmd_extension(a, cfg):
    store = _store(cfg)
    _asset_rel(store, a.id)
    if a.decision == "extend" and not a.years:
        sys.exit("error: --years N is needed to record an extension")
    ext = {"decision": a.decision, "decided_on": dt.date.fromisoformat(a.on) if a.on else dt.date.today()}
    if a.years:
        ext["years"] = a.years
    if a.rate is not None:
        ext["additional_rate_pct"] = a.rate
    if a.note:
        ext["note"] = a.note
    _confirm(a, store, "assets.yaml", [{"op": "set", "path": f"assets[{a.id}].commitment.extension", "value": ext}], "rental-extension",
             a.reason or f"extension decision: {a.decision}")


def cmd_vacancy(a, cfg):
    store = _store(cfg)
    _asset_rel(store, a.id)
    asset = next(x for x in store.assets() if x.id == a.id)
    cur = [{"start": v.start, **({"end": v.end} if v.end else {}), **({"note": v.note} if v.note else {})} for v in asset.vacancies]
    new = {"start": dt.date.fromisoformat(a.start), **({"end": dt.date.fromisoformat(a.end)} if a.end else {}), **({"note": a.note} if a.note else {})}
    _confirm(a, store, "assets.yaml", [{"op": "set", "path": f"assets[{a.id}].vacancies", "value": cur + [new]}], "rental-vacancy",
             a.reason or "a vacancy period")


def cmd_market_rate(a, cfg):
    store = _store(cfg)
    _asset_rel(store, a.id)
    v = {"rate_pct": a.rate, "as_of": dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today(), **({"source": a.source} if a.source else {})}
    _confirm(a, store, "assets.yaml", [{"op": "set", "path": f"assets[{a.id}].market_rate", "value": v}], "rental-market-rate",
             a.reason or "market rate entered by the owner")


# ---------------------------------------------------------------- registration

def register(sub, add) -> None:
    rp = sub.add_parser("rental", help="rental property under a tax-incentive scheme: cash flow, P&L, commitment, tax-year figures, indicators (E15)",
                        description="rental properties of the household; memory writes are previewed and need a typed yes")
    rsub = rp.add_subparsers(dest="rental_cmd", required=True, metavar="SUBCOMMAND")

    def common(s, id_=True):
        if id_:
            s.add_argument("id", nargs="?", help="the property id (optional when there is only one)")
        s.add_argument("--as-of", metavar="YYYY-MM-DD", help="compute as of that day")
        s.add_argument("--json", action="store_true")
        return s
    s = add(rsub, "list", cmd_list, "every rental property"); common(s, False)
    s = add(rsub, "show", cmd_show, "one property in full"); common(s); s.add_argument("--months", type=int, default=12)
    s.add_argument("--year", type=int, help="the income year of the tax part of --json")
    s = add(rsub, "cashflow", cmd_cashflow, "the monthly cash flow, effort d'epargne and vacancy"); common(s)
    s.add_argument("--months", type=int, default=12)
    s = add(rsub, "pnl", cmd_pnl, "the yearly P&L"); common(s); s.add_argument("--year", type=int)
    s = add(rsub, "scheme", cmd_scheme, "the scheme commitment, reminders and missing facts"); common(s)
    s.add_argument("--propose-questions", action="store_true", help="add an open question for the missing facts")
    s = add(rsub, "tax", cmd_tax, "the figures of the rental-income return and the documents checklist (candidates, no filing)"); common(s)
    s.add_argument("--year", type=int, help="the income year (default: the one to declare next)")
    s = add(rsub, "indicators", cmd_indicators, "renegotiate-or-sell indicators: loan rate vs market, end of commitment, net equity"); common(s)
    s.add_argument("--market-rate", type=float, help="a market rate YOU looked up (%%); it is never looked up here")
    s.add_argument("--market-date", metavar="YYYY-MM-DD", help="the date of that rate")
    s.add_argument("--bank-fees", type=float); s.add_argument("--guarantee-fees", type=float); s.add_argument("--other-fees", type=float)
    s.add_argument("--penalty", type=float, help="the early-repayment amount of your contract (EUR)")
    s = add(rsub, "flows", cmd_flows, "flows of the property account that are in no property category"); common(s)
    s.add_argument("--months", type=int, default=24)

    def writer(name, fn, hlp):
        s = add(rsub, name, fn, hlp)
        s.add_argument("id")
        s.add_argument("--reason", help="why (recorded in the history)")
        s.add_argument("--yes", action="store_true", help="write without asking (for the person at the keyboard)")
        return s
    s = writer("add", cmd_add, "declare a rental property (an asset of kind real_estate_rental); previewed, written after a typed yes")
    s.add_argument("--set", action="append", metavar="PATH=VALUE", help="e.g. account=LABEL loan=ID scheme=pinel (repeat)")
    s = writer("edit", cmd_edit, "change the facts of a property: --set / --unset; previewed, written after a typed yes")
    s.add_argument("--set", action="append", metavar="PATH=VALUE", help="e.g. commitment.start_date=2021-03-05 commitment.years=9 (repeat)")
    s.add_argument("--unset", action="append", metavar="PATH")
    s = writer("extension", cmd_extension, "record the decision at the end of the commitment")
    s.add_argument("--decision", required=True, choices=["extend", "not_extend", "undecided"])
    s.add_argument("--years", type=int, help="length of the extension")
    s.add_argument("--rate", type=float, help="the extra reduction rate of the extension (%%), as the scheme states it")
    s.add_argument("--on", metavar="YYYY-MM-DD"); s.add_argument("--note")
    s = writer("vacancy", cmd_vacancy, "declare a period the property was not let (it is then not reported as a missing rent)")
    s.add_argument("--start", required=True, metavar="YYYY-MM-DD"); s.add_argument("--end", metavar="YYYY-MM-DD"); s.add_argument("--note")
    s = writer("market-rate", cmd_market_rate, "record a market rate you looked up (a quote, a comparator), with its date")
    s.add_argument("--rate", type=float, required=True); s.add_argument("--as-of", metavar="YYYY-MM-DD"); s.add_argument("--source")
