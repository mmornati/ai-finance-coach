"""CLI of the loans epic (E9): ``coach loans ...`` and ``coach networth``.

    coach loans list                         every loan: instalment, capital still due and where it comes from, end, alerts
    coach loans show ID                      one loan in full (schedule summary, payments, alerts, suggestions, lease view)
    coach loans schedule ID [--rows]         the amortization schedule, or exactly what is missing (+ --propose-questions)
    coach loans payments ID                  the bank payments linked to the loan
    coach loans alerts                       missed / changed / extra payments, wrong account, lease reminders
    coach loans infer ID [--propose]         suggest missing terms from the observed payments (never written; --propose queues a proposal)
    coach loans add ID --kind K              guided (or --set path=value) creation, previewed, written after a typed yes (source cli)
    coach loans edit ID                      the same for an existing loan (--set / --unset, or guided)
    coach loans odometer ID --km N           record a mileage reading of a leased vehicle
    coach loans lease [ID]                   end-of-contract view: decision, mileage projection, return checklist
    coach loans scenario prepay|renegotiate|insurance ID ...    early repayment / rachat / insurance scenario on the real schedule
    coach networth [--history]               net worth now and its monthly history (--record stores today's snapshot)

Every memory write is validated, PREVIEWED (the diff) and written only after a typed yes (``--yes`` is for the person at the keyboard; the
coach skills never pass it); it is recorded in the memory history with the source ``cli``. Nothing inferred is written silently.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Optional

from coach import db as db_mod
from coach.analytics import api as analytics_api
from coach.analytics.common import _plain, money_str
from coach.i18n_msg import strip_msgs
from coach.loans import history as H, infer as I, loa as LOA, networth as NW, scenario as SC, schedule as S, service as LS
from coach.memory import proposals as prop_mod, yamlio
from coach.memory.store import MemoryStore, MemoryStoreError

LOAN_GUIDE = [
    ("lender", "lender (bank / financing company)"), ("asset", "financed asset (house, car...)"), ("holder", "borrower: member id or joint"),
    ("start_date", "start date (YYYY-MM-DD)"), ("first_payment_date", "date of the first instalment (blank = a month after the start)"),
    ("end_date", "end date (YYYY-MM-DD)"), ("term_months", "number of instalments / months (blank = start to end)"),
    ("payment_day", "day of the month the instalment is debited (1-31)"), ("principal", "amount borrowed (EUR)"),
    ("rate.type", "rate type (fixed / variable / mixed)"), ("rate.nominal", "nominal annual rate (%)"), ("rate.taeg", "TAEG (%)"),
    ("rate.index", "variable rate: index (text, stored only)"), ("rate.margin", "variable rate: margin (points, stored only)"),
    ("rate.cap", "variable rate: cap (%, stored only)"), ("monthly_payment", "total debited per month (EUR, insurance included)"),
    ("insurance.provider", "borrower insurance provider"), ("insurance.monthly", "borrower insurance, flat EUR per month"),
    ("insurance.rate_pct", "borrower insurance as a yearly % (instead of a flat amount)"),
    ("insurance.basis", "that % applies to the initial capital or the capital still due (initial / outstanding)"),
    ("insurance.delegated", "insurance delegated to another insurer (true / false)"),
    ("deferral.months", "deferral at the start: number of months"), ("deferral.kind", "deferral kind: partial (interest only) / total"),
    ("outstanding", "capital still due (EUR, from your latest statement)"), ("outstanding_as_of", "that capital is as of (YYYY-MM-DD)"),
    ("debited_account", "account the instalment leaves (uid or label)"), ("payment_match", "regex of the payment label in the bank data"),
    ("early_repayment_penalty", "early-repayment clause (text or EUR)"),
]
LEASE_GUIDE = [
    ("lender", "lender (financing company)"), ("asset", "the vehicle"), ("holder", "who signed it: member id or joint"),
    ("start_date", "start date (YYYY-MM-DD)"), ("end_date", "end date (YYYY-MM-DD)"), ("term_months", "contract length in months"),
    ("payment_day", "day of the month the rent is debited (1-31)"), ("monthly_payment", "monthly rent (EUR)"),
    ("first_payment", "first, increased rent / down payment (EUR)"), ("residual_value", "residual value = purchase-option price (EUR)"),
    ("mileage_limit_km", "mileage limit over the whole contract (km)"), ("excess_km_fee", "fee per excess km (EUR)"),
    ("initial_km", "odometer at the start (km; 0 for a new car)"), ("insurance.monthly", "insurance included / separate, EUR per month"),
    ("debited_account", "account the rent leaves (uid or label)"), ("payment_match", "regex of the payment label in the bank data"),
]
KINDS = ("mortgage", "car_loan", "loa", "lld", "consumer_loan", "bnpl")


def _store(cfg) -> MemoryStore:
    return MemoryStore(cfg.memory_dir, history=cfg.memory_history, source="cli")


def _ctx(a, cfg):
    con = db_mod.connect(cfg, insecure=a.insecure)
    today = dt.date.fromisoformat(a.as_of) if getattr(a, "as_of", None) else None
    ds = analytics_api.build_dataset(con, cfg, today)
    from coach.analytics.recurring import detect_recurring
    return con, ds, detect_recurring(ds)


def _find(ds, ident: str):
    hits = [(rel, lb) for rel, lb in ds.memory.liabilities if lb.id == ident]
    if not hits:
        ids = ", ".join(lb.id for _r, lb in ds.memory.liabilities) or "none on file"
        sys.exit(f"error: no loan with id {ident!r} (known: {ids}; create one with `coach loans add`)")
    return hits[0]


def _j(obj) -> str:
    return json.dumps(strip_msgs(_plain(obj)), ensure_ascii=False, indent=2)          # *_msg: the web's only


def _m(x) -> str:
    return "?" if x is None else f"{x} EUR"


# ---------------------------------------------------------------- read commands

def cmd_list(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rows = []
        for rel, lb in ds.memory.liabilities:
            o = LS.overview_of(ds, rec, lb)
            sch = o["schedule"]
            rows.append({"id": lb.id, "kind": lb.kind, "lender": lb.lender, "instalment": lb.monthly_payment,
                         "schedule": sch["status"], "mode": sch.get("mode"), "remaining_capital": sch.get("remaining_capital"),
                         "capital_source": (sch.get("source") or "schedule") if sch["status"] == "computed" else ("declared" if lb.outstanding is not None else "unknown"),
                         "declared_outstanding": lb.outstanding, "end_date": lb.end_date.isoformat() if lb.end_date else None,
                         "payments_seen": o["payments"]["count"], "alerts": len(o["alerts"]), "missing_for_schedule": sch.get("missing") or []})
        if a.json:
            print(_j(rows))
            return
        if not rows:
            print("no loan on file (`coach loans add ID --kind mortgage`)")
            return
        for r in rows:
            cap = "n/a (lease)" if r["schedule"] == "not_applicable" else f"{r['remaining_capital']} EUR ({r['capital_source'].replace('_', ' ')})" if r["capital_source"] in ("schedule", "declared_rolled", "declared") and r["remaining_capital"] else (
                f"{r['declared_outstanding']:,.2f} EUR (declared)" if r["capital_source"] == "declared" else "unknown")
            print(f"{r['id']:<22}{r['kind']:<14}{(r['lender'] or '?')[:17]:<18}instalment {r['instalment'] if r['instalment'] is not None else '?':<9}"
                  f"capital due {cap:<28}ends {r['end_date'] or '?'}   payments seen {r['payments_seen']}   alerts {r['alerts']}")
            if r["schedule"] == "not_applicable":
                print(f"{'':<22}a lease: no capital owed; `coach loans lease {r['id']}` for the end-of-contract view")
            elif r["missing_for_schedule"]:
                print(f"{'':<22}no schedule yet, missing: {', '.join(r['missing_for_schedule'])}")
    finally:
        con.close()


def cmd_show(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rel, lb = _find(ds, a.id)
        o = LS.overview_of(ds, rec, lb, rel, with_rows=False)
        if a.json:
            print(_j(o))
            return
        _print_schedule(o["schedule"], rows=None)
        print("\npayments seen: " + (f"{o['payments']['count']} (last {o['payments']['last']}: {_m(o['payments']['last_amount'])})"
                                     if o["payments"]["count"] else "none (set payment_match)"))
        _print_alerts(o["alerts"])
        _print_inference(o["inference"])
        if o["lease"]:
            _print_lease(o["lease"])
    finally:
        con.close()


def _print_schedule(sch: dict, rows: Optional[list]) -> None:
    if sch["status"] == "not_applicable":
        print(f"schedule: not applicable. {sch.get('alternative') or ''}")
        return
    if sch["status"] != "computed":
        print(f"schedule: not computable ({sch['status']}). Missing: {', '.join(sch.get('missing') or ['?'])}")
        if sch.get("alternative"):
            print(f"  {sch['alternative']}")
        print("  `coach loans schedule ID --propose-questions` asks you for them; `coach loans infer ID` suggests values from the payments")
        return
    print(f"schedule ({sch['mode']}{', approximate: variable rate' if sch['approximate'] else ''}): {sch['term_instalments']} instalments of "
          f"{_m(sch['payment'])} (+ insurance), {sch['first_due']} -> {sch['last_due']}")
    print(f"  capital due today {_m(sch['remaining_capital'])}; {sch['remaining_instalments']} instalment(s) left; next due {sch['next_due'] or '-'}")
    if sch.get("total_interest") is not None:
        print(f"  interest paid to date {_m(sch['interest_paid'])}, remaining {_m(sch['remaining_interest'])}, total {_m(sch['total_interest'])}; "
              f"insurance total {_m(sch['total_insurance'])}; total cost {_m(sch['total_cost'])}")
    else:
        print(f"  remaining interest {_m(sch['remaining_interest'])} (the table starts at the declared capital)")
    print("  by calendar year (interest = the figure for a rental-income or mortgage-interest declaration; check the lender's statement):")
    for y in sch["by_year"]:
        print(f"    {y['year']}: {y['instalments']:>2} instalments  interest {y['interest']:>10}  insurance {y['insurance']:>8}  principal {y['principal']:>10}"
              + ("  (part of the year)" if y["partial"] else ""))
    for k in ("payment_check", "outstanding_check"):
        if sch.get(k):
            print(f"  {k.replace('_', ' ')}: {sch[k]['status']}" + (f" - {sch[k]['hint']}" if sch[k].get("hint") else ""))
    for x in sch["assumptions"]:
        print(f"  assumption: {x}")
    if rows:
        print("\n   k  due         kind      interest  principal  insurance      total     balance")
        for r in rows:
            print(f"{r['k']:>4}  {r['due']}  {r['kind']:<8}{r['interest']:>10}{r['principal']:>11}{r['insurance']:>11}{r['total']:>11}{r['balance']:>12}"
                  + ("  *" if r["made"] else ""))
        print("(* = already due)")


def cmd_schedule(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rel, lb = _find(ds, a.id)
        sch = LS.schedule_of(ds, lb)
        d = sch.to_dict()
        if a.year and sch.status == "computed":
            d["rows"] = [r for r in d["rows"] if r["due"].startswith(str(a.year))]
        if a.json:
            if not a.rows:
                d.pop("rows", None)
            print(_j(d))
        else:
            _print_schedule(d, d["rows"] if a.rows or a.year else None)
        if a.propose_questions and sch.status != "computed":
            _propose_questions(cfg, lb, rel, sch)
    finally:
        con.close()


def _propose_questions(cfg, lb, rel, sch: S.LoanSchedule) -> None:
    from coach.memory import questions as Q, schemas
    from coach.memory.qgen import TOPIC_CODE, qid, qmsg
    store = _store(cfg)
    key = f"fill:loan-schedule:{lb.id}"
    if any(q.key == key for q in store.questions()):
        print("the question about these fields is already on the open-questions list")
        return
    miss = sch.missing
    fields = ", ".join(miss)
    text = (f"Liability {lb.id} ({lb.kind}): to show its amortization schedule I need {fields}. The loan offer or "
            "the latest annual statement has them.")
    q = schemas.Question(id=qid("fill", key), topic="Liabilities", topic_code=TOPIC_CODE["Liabilities"], key=key, origin="manual",
                         created=dt.date.today(), stake=round((lb.monthly_payment or 0) * 12, 2) or None, question=text,
                         question_msg=qmsg("question.loanSchedule", text, id=lb.id, loan_kind=lb.kind, fields=fields),
                         evidence={"missing": miss}, suggested_target={"file": rel, "field": ",".join(miss)})
    Q.add_many(store, [q], source="cli")
    print(f"added 1 open question about {', '.join(miss)} (`coach questions list --open`)")


def cmd_payments(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rel, lb = _find(ds, a.id)
        obs = LS.observed_of(ds, lb)
        rows = [{"date": p.date.isoformat(), "amount": money_str(p.amount_c), "account": p.account_label, "tx": p.tx_key} for p in obs]
        if a.json:
            print(_j({"id": lb.id, "summary": P_summary(obs), "payments": rows, "alerts": [x.to_dict() for x in LS.alerts_of(ds, lb)]}))
            return
        if not lb.payment_match:
            print("no payment_match on this loan: set it (`coach loans edit ID --set payment_match='^LABEL'`) to link its bank payments")
            return
        print(f"{len(rows)} payment(s) match /{lb.payment_match}/" + (f" with an amount near {lb.amount_match.amount}" if lb.amount_match else ""))
        for r in rows[-(a.limit or 24):]:
            print(f"  {r['date']}  {r['amount']:>10} EUR  {r['account']}")
        _print_alerts([x.to_dict() for x in LS.alerts_of(ds, lb)])
    finally:
        con.close()


def P_summary(obs):
    from coach.loans import payments as P
    return P.summary(obs)


def _print_alerts(alerts: list) -> None:
    if not alerts:
        print("alerts: none")
        return
    print(f"alerts ({len(alerts)}):")
    for x in alerts:
        print(f"  [{x['severity']}] {x['title']} - {x['body']}")


def cmd_alerts(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        cards = LS.loan_cards(ds)
        if a.json:
            print(_j(cards))
            return
        if not cards:
            print("no loan alert or lease reminder")
        for c in cards:
            print(f"[{c['severity']}] {c['title']}\n      {c['body']}")
    finally:
        con.close()


def _print_inference(inf: dict) -> None:
    if inf.get("status") in ("not_applicable", "nothing_to_infer"):
        for n in inf.get("notes") or []:
            print(f"inference: {n}")
        return
    fields = inf.get("fields") or []
    if not fields:
        print("inference: nothing can be suggested yet" + (f" (need: {'; '.join(inf['missing'])})" if inf.get("missing") else ""))
        return
    print("suggestions inferred from the bank payments (INFERRED, not stored; confirm with the contract):")
    for f in fields:
        print(f"  {f['field']} = {f['value']}   [{f['confidence']} confidence] {f['method']}" + (f" - {f['note']}" if f.get("note") else ""))
    for n in inf.get("notes") or []:
        print(f"  note: {n}")


def cmd_infer(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rel, lb = _find(ds, a.id)
        inf = I.infer_loan(lb, LS.observed_of(ds, lb), ds.today)
        if a.json:
            print(_j(inf))
        else:
            o = inf.to_dict()
            print(f"observed: {o['observed'].get('count', 0)} payment(s)" + (f", typical {o['observed'].get('median')} EUR" if o["observed"].get("median") else ""))
            _print_inference(o)
        if a.propose:
            ops = I.to_ops(inf, a.min_confidence)
            if not ops:
                sys.exit("nothing to propose (no suggestion at that confidence)")
            reason = ("inferred from " + str(inf.observed.get("count")) + " observed bank payments by `coach loans infer` (annuity maths); "
                      "INFERRED values, to be checked against the loan contract: " + ", ".join(f"{f.field} ({f.confidence})" for f in inf.fields
                                                                                              if I.CONF.index(f.confidence) >= I.CONF.index(a.min_confidence)))
            try:
                p = prop_mod.create(_store(cfg), rel, ops, reason, source="loans-inference")
            except MemoryStoreError as e:
                sys.exit(f"error: {e}")
            print(f"\nproposal {p.id} created (nothing is written): review it in the web app (Memory > Proposals) or with "
                  f"`uv run coach memory proposals`, and accept it yourself with `uv run coach memory accept {p.id}`")
    finally:
        con.close()


# ---------------------------------------------------------------- guided writes

def _ask(prompt: str) -> str:
    return input(prompt)


def _confirm(a, store: MemoryStore, rel: str, ops: list, action: str, reason: Optional[str]) -> None:
    try:
        res = store.edit(rel, ops, action=action, reason=reason, source="cli", dry_run=True, replace_inline_comments=True)
    except MemoryStoreError as e:
        sys.exit(f"error: {e}")
    if not res.changed:
        print("no change (the values are already there)")
        return
    print(res.diff.rstrip("\n"))
    for i in res.issues:
        if i.level != "error":
            print(f"{i.level}: {i.message}")
    from coach.memory.commands import _is_tty
    if not _is_tty():                       # --yes only skips the typed prompt for a human at a terminal
        sys.exit("(preview only) run this yourself in a terminal, or use --propose; nothing was written")
    if not a.yes:
        if _ask("write this change? [y/N] ").strip().lower() not in ("y", "yes"):
            print("skipped")
            return
    done = store.edit(rel, ops, action=action, reason=reason, source="cli", replace_inline_comments=True)
    print("written" + (f" (change {done.change_id}; `coach memory revert {done.change_id}` undoes it)" if done.change_id else ""))


def _parse_sets(sets: list) -> list[tuple[str, object]]:
    out = []
    for item in sets or []:
        if "=" not in item:
            sys.exit(f"error: --set expects path=value (got {item!r})")
        k, v = item.split("=", 1)
        out.append((k.strip(), yamlio.to_plain(yamlio.parse_scalar(v))))
    return out


def _guided(guide: list, current: dict) -> list[tuple[str, object]]:
    print("Answer each question; Enter keeps the unknown / current value, `x` stops. Dates YYYY-MM-DD, amounts in EUR, rates in %.")
    out = []
    for path, prompt in guide:
        cur = current.get(path)
        raw = _ask(f"  {prompt}" + (f" [{cur}]" if cur not in (None, "") else "") + ": ").strip()
        if raw.lower() == "x":
            break
        if raw:
            out.append((path, yamlio.to_plain(yamlio.parse_scalar(raw))))
    return out


def _flat(lb) -> dict:
    d = lb.model_dump(mode="json") if hasattr(lb, "model_dump") else {}
    out = {}

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{prefix}{k}.", x)
        elif v not in (None, "", []):
            out[prefix[:-1]] = v
    walk("", d)
    return out


def _template(kind: str, ident: str) -> dict:
    base = {"id": ident, "kind": kind, "lender": None, "asset": None, "start_date": None, "end_date": None, "principal": None,
            "outstanding": None, "outstanding_as_of": None, "rate": {"type": None, "nominal": None, "taeg": None},
            "monthly_payment": None, "payment_match": None, "documents": [], "notes": ""}
    if kind in ("loa", "lld"):
        base.update({"first_payment": None, "residual_value": None, "mileage_limit_km": None, "excess_km_fee": None, "initial_km": None})
    return base


def _set_ops(pairs: list) -> list[dict]:
    return [{"op": "set", "path": k, "value": v} for k, v in pairs]


def cmd_add(a, cfg):
    store = _store(cfg)
    rel = f"liabilities/{a.id}.yaml"
    if store.exists(rel):
        sys.exit(f"error: {rel} already exists (`coach loans edit {a.id}`)")
    pairs = _parse_sets(a.set)
    if not pairs:
        from coach.memory.commands import _is_tty
        if not _is_tty():
            sys.exit("error: give the values with --set path=value (several allowed), or run this in a terminal for the guided questions")
        pairs = _guided(LEASE_GUIDE if a.kind in ("loa", "lld") else LOAN_GUIDE, {})
    ops = [{"op": "create", "value": _template(a.kind, a.id)}] + _set_ops(pairs)
    _confirm(a, store, rel, ops, "loans-add", a.reason or "coach loans add")
    print(f"next: `coach loans show {a.id}` (schedule, missing fields), `coach loans infer {a.id}` (suggestions from the payments)")


def cmd_edit(a, cfg):
    store = _store(cfg)
    rel = f"liabilities/{a.id}.yaml"
    if not store.exists(rel):
        sys.exit(f"error: {rel} does not exist (`coach loans add {a.id} --kind ...`)")
    lb = next(m for r, m in store.liabilities() if m.id == a.id)
    pairs = _parse_sets(a.set)
    ops = _set_ops(pairs) + [{"op": "unset", "path": p} for p in (a.unset or [])]
    if not ops:
        from coach.memory.commands import _is_tty
        if not _is_tty():
            sys.exit("error: give --set path=value / --unset path, or run this in a terminal for the guided questions")
        ops = _set_ops(_guided(LEASE_GUIDE if lb.kind in ("loa", "lld") else LOAN_GUIDE, _flat(lb)))
        if not ops:
            print("nothing to change")
            return
    _confirm(a, store, rel, ops, "loans-edit", a.reason or "coach loans edit")


def cmd_odometer(a, cfg):
    store = _store(cfg)
    rel = f"liabilities/{a.id}.yaml"
    if not store.exists(rel):
        sys.exit(f"error: {rel} does not exist")
    lb = next(m for r, m in store.liabilities() if m.id == a.id)
    if lb.kind not in ("loa", "lld"):
        sys.exit("error: odometer readings are recorded for leases (loa / lld)")
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    readings = {o.date: o.km for o in lb.odometer}
    readings[day] = a.km
    value = [{"date": d, "km": k} for d, k in sorted(readings.items())]
    _confirm(a, store, rel, [{"op": "set", "path": "odometer", "value": value}], "loans-odometer", a.reason or f"odometer {a.km} km on {day}")


# ---------------------------------------------------------------- lease and scenarios

def _print_lease(ls: dict) -> None:
    e = ls["end"]
    print("\nend of the lease:")
    if not e["known"]:
        print("  end date unknown: record end_date (`coach loans edit ID --set end_date=YYYY-MM-DD`)")
    else:
        print(f"  ends {e['end_date']} ({e['days_left']} days); reminder from {e['reminder_date']}" + ("  <- reminder active" if e["reminder_active"] else ""))
    d = ls["decision"]
    print(f"  buy or return: option price {d.get('residual_value') or 'unknown'}" + (f" - needs {', '.join(d['needs'])}" if d.get("needs") else ""))
    m = ls["mileage"]
    print(f"  mileage: limit {m['limit_km'] if m['limit_km'] is not None else '?'} km, readings {m['readings']}, status {m['status']}")
    if m.get("pace"):
        print(f"    pace {m['pace']['km_per_year']:,} km/year since {m['pace']['since']}")
    if m.get("projected_contract_km") is not None:
        print(f"    projected at the end: {m['projected_contract_km']:,} km" + (f", {m['excess_km']:,} km over" if m.get("excess_km") else ", within the limit")
              + (f", about {m['excess_cost']} EUR" if m.get("excess_cost") else ""))
    if m["needs"]:
        print(f"    needs: {'; '.join(m['needs'])}")
    print("  return checklist:")
    for x in ls["checklist"]:
        print(f"    - {x}")


def cmd_lease(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        months = getattr(ds.settings, "loan_reminder_months", 6)
        rows = [(lb.id, LOA.status(lb, ds.today, months, a.market_value)) for _r, lb in ds.memory.liabilities
                if LOA.is_lease(lb) and (not a.id or lb.id == a.id)]
        if not rows:
            sys.exit("error: no lease (loa / lld) on file" + (f" with id {a.id}" if a.id else ""))
        if a.json:
            print(_j({i: s for i, s in rows}))
            return
        for ident, st in rows:
            print(f"== {ident} ({st['kind']}) ==")
            _print_lease(st)
            if st["missing"]:
                print(f"  missing: {', '.join(st['missing'])}")
    finally:
        con.close()


def cmd_scenario(a, cfg):
    con, ds, rec = _ctx(a, cfg)
    try:
        rel, lb = _find(ds, a.id)
        sch = LS.schedule_of(ds, lb)
        country = (a.country or ds.memory.country or "FR").upper()
        if a.what == "prepay":
            if a.amount is None:
                sys.exit("error: --amount is required")
            res = SC.prepay(lb, sch, ds.today, a.amount, dt.date.fromisoformat(a.on) if a.on else None, country, a.penalty)
        elif a.what == "renegotiate":
            if a.new_rate is None:
                sys.exit("error: --new-rate (the offered nominal rate, %) is required")
            res = SC.renegotiate(lb, sch, ds.today, a.new_rate, country, a.variant, a.bank_fees or 0, a.guarantee_fees or 0, a.other_fees or 0, a.penalty)
        else:
            if a.alternative is None:
                sys.exit("error: --alternative (monthly premium of the other policy, EUR) is required")
            res = SC.insurance(lb, sch, ds.today, a.alternative, country, a.fees or 0)
        res = _plain(res)
        if a.json:
            print(_j(res))
        else:
            _print_scenario(a.what, res)
        if a.save:
            if res.get("status") != "computed":
                sys.exit("nothing to save: the scenario was not computed")
            from coach.agent import insights as ins
            title, body = SC.insight_text(a.what, lb.kind, res)
            iid = ins.add(con, kind="finding", title=title, body=body, findings=[res], skill="loan-scenario", backend="local", model="none")
            print(f"stored as insight {iid} (Insights page)")
    finally:
        con.close()


def _print_scenario(what: str, res: dict) -> None:
    if res.get("status") != "computed":
        print(f"{res.get('status')}: " + (f"missing {', '.join(res.get('missing') or [])}" if res.get("missing") else res.get("note", "")))
        if res.get("alternative"):
            print(res["alternative"])
        return
    if what == "prepay":
        print(f"prepay {res['amount']} EUR on {res['date']}: capital due {res['capital_before']} -> {res['capital_after']} EUR, "
              f"{res['remaining_instalments']} instalments of {res['instalment']} EUR left")
        print(f"penalty {res['penalty']} EUR: {res['penalty_basis']}")
        for o in res["options"]:
            print(f"  {o['label']}: instalment {o['new_instalment']} EUR ({o['monthly_change']:>8}/month), {o['months_saved']} month(s) saved, "
                  f"interest saved {o['interest_saved']}, insurance saved {o['insurance_saved']}, net {o['net_saving']} EUR, "
                  f"break-even {o['break_even_months'] if o['break_even_months'] is not None else 'never'} month(s) -> {o['verdict']}")
    elif what == "renegotiate":
        print(f"{res['variant']} ({res['country']}) at {res['new_rate_pct']} % instead of {res['current_rate_pct']} %: instalment {res['current_payment']} -> "
              f"{res['new_payment']} EUR ({res['monthly_saving']} saved a month)")
        print(f"  interest {res['interest_current']} -> {res['interest_new']} (gross saving {res['gross_interest_saving']}); costs {res['total_costs']} "
              f"(penalty {res['penalty']}: {res['penalty_basis']}); net saving {res['net_saving']} EUR; break-even "
              f"{res['break_even_months'] if res['break_even_months'] is not None else 'never'} month(s); verdict: {res['verdict']}")
        for n in res["notes"]:
            print(f"  note: {n}")
    else:
        print(f"insurance {res['current_monthly']} -> {res['alternative_monthly']} EUR a month for {res['remaining_months']} months: "
              f"net saving {res['net_saving']} EUR ({res['verdict']})")
        for n in res["notes"]:
            print(f"  note: {n}")
    for n in res.get("notes", []) if what == "prepay" else []:
        print(f"  note: {n}")
    print("estimate, not an offer; general information, not financial advice")


# ---------------------------------------------------------------- net worth

def cmd_networth(a, cfg):
    if a.record and a.as_of:
        sys.exit("error: --as-of refuses every write: record a snapshot without it (it stores today's figures)")
    con, ds, rec = _ctx(a, cfg)
    try:
        nw = NW.build(ds, ds.today, asset_stale_months=cfg.memory_asset_stale_months, liability_stale_months=cfg.memory_stale_months,
                      schedules=LS.schedules(ds))
        if a.record:
            H.record_snapshot(con, nw)
            n = H.backfill(con, ds, ds.today, LS.schedules(ds))
            print(f"recorded the snapshot of {nw.as_of}; {n} past month(s) back-filled")
        hist = H.series(con, a.months) if a.history else None
        if a.json:
            out = nw.to_dict()
            if hist is not None:
                out["history"] = hist
            print(_j(out))
            return
        print(f"net worth on {nw.as_of}: {money_str(nw.net_worth_c)} EUR" + ("" if nw.complete else f"  (known part only: {nw.n_unknown} item(s) unknown and NOT counted)"))
        print(f"  assets {money_str(nw.assets_c)}   owed {money_str(nw.liabilities_c)}")
        for k in NW.CATEGORIES:
            print(f"    {k:<12}{money_str(nw.by_category_c[k]):>14}")
        print(f"    {'liabilities':<12}{money_str(nw.by_category_c['liabilities']):>14}")
        print("  by owner: " + "; ".join(f"{o}: {money_str(v['net_worth_c'])}" + (f" ({v['n_unknown']} unknown)" if v["n_unknown"] else "")
                                        for o, v in nw.by_owner.items()))
        for c in nw.components:
            amt = "not counted" if c.status == "excluded" else "unknown" if c.status != "known" else (("-" if c.type == "liability" else "") + money_str(c.amount_c))
            flags = ("  [stale]" if c.stale else "") + (f"  ({c.note})" if c.note else "")
            print(f"  {c.type:<10}{c.label[:28]:<30}{c.category:<12}{amt:>14}{flags}")
        if hist is not None:
            print("\nhistory (month, net worth, unknown items):")
            for p in hist:
                print(f"  {p['month']}  {p['net_worth']:>14}  {p['source']:<8}" + ("" if p["complete"] else f"  known part only, {p['n_unknown']} unknown"))
            if not hist:
                print("  none yet: `coach networth --record` stores today's snapshot and back-fills the past months")
    finally:
        con.close()


# ---------------------------------------------------------------- registration

def register(sub, add) -> None:
    lp = sub.add_parser("loans", help="loans, mortgage and leases: schedule, payments, alerts, inference, scenarios (E9)",
                        description="loans and leases of the household; memory writes are previewed and need a typed yes")
    lsub = lp.add_subparsers(dest="loans_cmd", required=True, metavar="SUBCOMMAND")

    def common(s, id_=True):
        if id_:
            s.add_argument("id")
        s.add_argument("--as-of", metavar="YYYY-MM-DD", help="compute as of that day")
        return s
    s = add(lsub, "list", cmd_list, "every loan with its capital due and alerts")
    s.add_argument("--json", action="store_true"); common(s, False)
    s = add(lsub, "show", cmd_show, "one loan in full")
    s.add_argument("--json", action="store_true"); common(s)
    s = add(lsub, "schedule", cmd_schedule, "the amortization schedule, or what is missing")
    s.add_argument("--json", action="store_true"); s.add_argument("--rows", action="store_true", help="list every instalment")
    s.add_argument("--year", type=int, help="only the instalments of that calendar year")
    s.add_argument("--propose-questions", action="store_true", help="when not computable, add an open question for the missing fields")
    common(s)
    s = add(lsub, "payments", cmd_payments, "the bank payments linked to the loan, with its alerts")
    s.add_argument("--json", action="store_true"); s.add_argument("--limit", type=int); common(s)
    s = add(lsub, "alerts", cmd_alerts, "payment alerts and lease reminders of every loan")
    s.add_argument("--json", action="store_true"); common(s, False)
    s = add(lsub, "infer", cmd_infer, "suggest missing terms from the observed payments (never written)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--propose", action="store_true", help="queue the suggestions as a memory PROPOSAL (you accept it yourself)")
    s.add_argument("--min-confidence", choices=I.CONF, default="medium", help="with --propose: the lowest confidence kept (default medium)")
    common(s)
    for name, fn, hlp in (("add", cmd_add, "create a loan: guided questions or --set path=value; previewed, written after a typed yes"),
                          ("edit", cmd_edit, "change a loan: --set / --unset or guided; previewed, written after a typed yes")):
        s = add(lsub, name, fn, hlp)
        s.add_argument("id")
        if name == "add":
            s.add_argument("--kind", required=True, choices=KINDS)
        s.add_argument("--set", action="append", metavar="PATH=VALUE", help="e.g. rate.nominal=3.1 start_date=2021-03-05 (repeat)")
        if name == "edit":
            s.add_argument("--unset", action="append", metavar="PATH")
        s.add_argument("--reason", help="why (recorded in the history)")
        s.add_argument("--yes", action="store_true", help="write without asking (for the person at the keyboard)")
    s = add(lsub, "odometer", cmd_odometer, "record a mileage reading of a leased vehicle")
    s.add_argument("id"); s.add_argument("--km", type=int, required=True); s.add_argument("--date", help="default today")
    s.add_argument("--reason"); s.add_argument("--yes", action="store_true")
    s = add(lsub, "lease", cmd_lease, "lease end-of-contract view: decision, mileage projection, return checklist")
    s.add_argument("id", nargs="?"); s.add_argument("--market-value", type=float, help="market value of the same car (EUR, a quote you looked up)")
    s.add_argument("--json", action="store_true"); s.add_argument("--as-of")
    s = add(lsub, "scenario", cmd_scenario, "early repayment / renegotiation / insurance scenario on the real schedule")
    s.add_argument("what", choices=["prepay", "renegotiate", "insurance"]); s.add_argument("id")
    s.add_argument("--amount", type=float, help="prepay: amount (EUR)"); s.add_argument("--on", metavar="YYYY-MM-DD", help="prepay: date (default today)")
    s.add_argument("--new-rate", type=float, help="renegotiate: offered nominal rate (%%)")
    s.add_argument("--variant", choices=["renegotiation", "rachat", "surroga"])
    s.add_argument("--bank-fees", type=float); s.add_argument("--guarantee-fees", type=float); s.add_argument("--other-fees", type=float)
    s.add_argument("--penalty", type=float, help="the early-repayment amount of your contract (EUR), instead of the legal cap")
    s.add_argument("--alternative", type=float, help="insurance: monthly premium of the other policy (EUR)")
    s.add_argument("--fees", type=float, help="insurance: switching fees (EUR)")
    s.add_argument("--country", choices=["FR", "IT"]); s.add_argument("--json", action="store_true")
    s.add_argument("--save", action="store_true", help="store the result as an insight (Insights page)"); s.add_argument("--as-of")
    s = add(sub, "networth", cmd_networth, "net worth now and its monthly history (E9-4)")
    s.add_argument("--history", action="store_true", help="the monthly history"); s.add_argument("--months", type=int, default=24)
    s.add_argument("--json", action="store_true")
    s.add_argument("--record", action="store_true", help="store today's snapshot and back-fill the past months (database only)")
    s.add_argument("--as-of", metavar="YYYY-MM-DD")
