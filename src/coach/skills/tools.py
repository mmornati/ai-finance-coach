"""The finance MCP tools of the E7 skills: ``monthly_review``, ``explain_spike``, ``subscription_audit``, ``cancellability``,
``savings_estimate``, ``mortgage_check``, ``what_if``, ``tax_candidates``, ``onboarding_status`` (read-only) and
``questions_propose`` (creates a memory PROPOSAL of questions; nothing is applied).

They sit behind the same choke point as the other tools (``ToolSession._publish``): untrusted text wrapped, memory ids
pseudonymised, the final privacy assertion. The computing modules work on the real dataset; the handlers here rewrite the
free-text / reference fields of their output with the household redactor (``skills.redact``) before it leaves.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Optional

from coach.analytics.common import _plain
from coach.mcp.tools import DATA_NOTE, DATE, MONTH, ToolError, ToolSession, ToolSpec, _obj
from coach.loans import service as LS
from coach.skills import cancel as C, loans as L, onboarding as OB, review as RV, subaudit as SA, tax as TX, whatif as WI
from coach.skills.redact import redact
from coach.skills.savings import savings_estimate

MARKET_MAX_AGE_DAYS = 30
MAX_QUESTIONS = 8


# ---------------------------------------------------------------- shared helpers

def _recurring(s: ToolSession):
    """The recurring series of the current data, computed once per data version (it hangs on the redacted registry)."""
    reg = s.reg
    rec = getattr(reg, "_skills_recurring", None)
    if rec is None:
        from coach.analytics.recurring import detect_recurring
        rec = reg._skills_recurring = detect_recurring(reg.ds)
    return rec


def _today(s: ToolSession) -> dt.date:
    return s.today or dt.date.today()


def _country(s: ToolSession, a: dict) -> str:
    if a.get("country"):
        return a["country"]
    store = s.data()[2]
    try:
        c = (store.load_plain("household.yaml") if store.exists("household.yaml") else {}).get("country")
    except Exception:                                                      # noqa: BLE001
        c = None
    return c.upper() if isinstance(c, str) and c.upper() in ("FR", "IT") else "FR"


def _emit(s: ToolSession, raw: dict) -> dict:
    """JSON-safe, then the entity text / refs / account uids are rewritten for a model; the coverage block goes through the
    household redactor like in the other tools."""
    red = s.reg.red
    data = _plain(raw)
    cov = data.pop("coverage", None)
    out = redact(data, red)
    if cov is not None:
        out["coverage"] = red.walk(cov)
    return out


def _real_id(s: ToolSession, value: str) -> str:
    """A memory item id as the model sees it (``liability-1``, ``contract-2``) -> the real id. A REAL id is refused with a neutral
    message: otherwise the tool would answer differently for real and invented ids (an oracle on the memory)."""
    rev = s.data()[5].rev
    if value not in rev:
        raise ToolError("unknown id: use an id exactly as shown by `memory_context` (for example liability-1)")
    return rev[value]


def _guard_value(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise ToolError(str(e)) from None


def _age_warning(label: str, day: Optional[str], today: dt.date) -> dict:
    """Staleness of a dated market figure (a price, a rate): older than 30 days is not presented as current."""
    if not day:
        return {"dated": False, "warning": f"the date of the {label} was not given: state its source and date, and treat it as possibly outdated"}
    age = (today - dt.date.fromisoformat(day)).days
    out = {"dated": True, "date": day, "age_days": age}
    if age > MARKET_MAX_AGE_DAYS:
        out["warning"] = f"the {label} is {age} days old (more than {MARKET_MAX_AGE_DAYS}): do not present it as current"
    elif age < 0:
        out["warning"] = f"the date of the {label} is in the future"
    return out


# ---------------------------------------------------------------- read-only handlers

def _monthly_review(s: ToolSession, a: dict):
    raw = _guard_value(RV.monthly_review, s.reg.ds, a.get("month"), _recurring(s), a.get("top") or 8)
    out = _emit(s, raw)
    out["note"] = ("Numbers are computed from the data. 'usual' is the average of the earlier covered months without one-offs; "
                   "movers carry evidence refs (transactions, series, anomalies, price changes).")
    return out


def _explain_spike(s: ToolSession, a: dict):
    reg = s.reg
    account = a.get("account")
    if account:
        back = {v: k for k, v in reg.red.account.items()}
        if account not in back:
            raise ToolError("unknown account: use an account pseudonym from a tool result (for example from `coverage`)")
        account = back[account]
    raw = _guard_value(RV.explain_spike, reg.ds, category=a.get("category"), account=account, month=a.get("month"),
                       recurring=_recurring(s))
    out = _emit(s, raw)
    if account:
        out["scope"]["target"] = reg.red.account.get(account, "account")
    out["note"] = ("Classes: one_off (tagged), recurring (a detected series), new_merchant (first payment ever that month), "
                   "habitual. delta = month - usual per merchant.")
    return out


def _subscription_audit(s: ToolSession, a: dict):
    from coach.analytics.pricechanges import dismissed_ids
    store = s.data()[2]
    asked = frozenset(q.key for q in store.questions() if q.key)
    raw = SA.subscription_audit(s.reg.ds, _recurring(s), dismissed_ids(s.con), asked, a.get("limit") or 10, a.get("items_limit") or 20)
    out = _emit(s, raw)
    out["next"] = ("Ask the user about usage with `questions_propose` (series refs from usage_questions_needed); suggest "
                   "find-cheaper for telecom / insurance / energy. Never tell the user to cancel: list review candidates.")
    return out


def _cancellability(s: ToolSession, a: dict):
    reg, today = s.reg, _today(s)
    country = _country(s, a)
    rec = _recurring(s)
    over = {k: a[k] for k in ("kind", "tacit_renewal", "group_contract", "notice_period_days", "billing_period") if a.get(k) is not None}
    for k in ("start_date", "renewal", "commitment_end"):
        if a.get(k):
            over[k] = dt.date.fromisoformat(a[k])

    def terms_with(t: C.Terms) -> C.Terms:
        for k, v in over.items():
            setattr(t, k, v)
        return t
    items = []
    if a.get("contract"):
        cid = _real_id(s, a["contract"])
        c = next((m for _r, m in reg.ds.memory.contracts if m.id == cid), None)
        if c is None:
            raise ToolError("unknown contract: use an id from `memory_context`")
        items.append(("contract", c.id, c.provider, terms_with(C.terms_of(c))))
    elif a.get("series"):
        x = next((v for v in rec.series if v.id == a["series"]), None)
        if x is None:
            raise ToolError("unknown series: use a `rec_` id from `recurring`")
        link = next((l.id for l in x.links if l.kind == "contract"), None)
        c = next((m for _r, m in reg.ds.memory.contracts if m.id == link), None) if link else None
        if c is not None:
            t = C.terms_of(c)
            t.kind = c.kind if c.kind and c.kind != "other" else C.family_of(None, x.category)
        else:
            t = C.Terms(kind=C.family_of(None, x.category), start_date=x.first_date, start_is_lower_bound=True,
                        renewal=x.next_expected if x.cadence in ("yearly", "semiannual", "quarterly") else None)
        items.append(("series", x.id, x.entity, terms_with(t)))
    elif any(k in over for k in ("kind", "start_date", "renewal", "commitment_end")):
        items.append(("hypothetical", None, None, terms_with(C.Terms())))
    else:
        for _r, c in reg.ds.memory.contracts[:30]:
            items.append(("contract", c.id, c.provider, terms_with(C.terms_of(c))))
    results = []
    for src, ident, provider, t in items:
        res = C.cancellability(t, today, country)
        row = {"source": src, "id": ident, "provider": reg.red.text(provider) if provider else None, **res}
        if t.start_is_lower_bound:
            row["start_date_note"] = "only the first payment seen is known: the contract may be older"
        results.append(row)
    out = {"today": today, "country": country, "results": results,
           "note": "Rules are general consumer-law summaries; the contract's own terms decide. Nothing is cancelled by the coach.",
           "disclaimer": C.DISCLAIMER}
    if not results:
        out["note"] = "No contract on file: add contracts (`onboarding_status`, `coach memory doc add`) or pass kind and dates."
    if a.get("include_rules"):
        out["rules_table"] = C.rules_table(country)
    return _plain(out)


def _savings_estimate(s: ToolSession, a: dict):
    est = _guard_value(savings_estimate, a["current_monthly"], a["alternative_monthly"], a.get("switching_costs") or 0, a.get("months") or 12)
    out = est.to_dict()
    out["quote"] = _age_warning("alternative's price", a.get("quote_date"), _today(s))
    if out["quote"].get("warning"):
        out["notes"] = out["notes"] + [out["quote"]["warning"]]
    return out


def _mortgage_check(s: ToolSession, a: dict):
    reg, today = s.reg, _today(s)
    country = _country(s, a)
    libs = reg.ds.memory.liabilities
    if a.get("liability"):
        lid = _real_id(s, a["liability"])
        sel = [m for _r, m in libs if m.id == lid]
        if not sel:
            raise ToolError("unknown liability: use an id from `memory_context`")
    else:
        sel = [m for _r, m in libs if m.kind == "mortgage"]
    out: dict = {"today": today, "country": country, "mortgages": [], "disclaimer": L.DISCLAIMER}
    if not sel:
        out["note"] = "No mortgage on file."
        out["what_i_need"] = ["a liability of kind mortgage in memory (`onboarding_status` lists the steps; a loan offer PDF can be "
                              "attached with `coach memory doc add --kind loan`)"]
        return _plain(out)
    market = a.get("market_rate_pct")
    if market is not None:
        out["market_rate"] = {"nominal_pct": market, **_age_warning("market rate", a.get("market_rate_date"), today)}
    need_all: list[str] = []
    for lb in sel:
        f = L.facts_of(lb)
        st = L.loan_state(f, today)
        cap = st.pop("_remaining_capital_c")
        sch = LS.schedule_of(reg.ds, lb)                              # E9-3: deferral / first due date / insurance aware
        if sch.status == "computed":
            cap = sch.remaining_capital_c
            st["remaining_capital"] = L.money_str(cap)
            st["remaining_capital_source"] = "computed from the amortization schedule" + (" (variable rate: approximate)" if sch.approximate else "")
            st["schedule"] = {"mode": sch.mode, "instalment": L.money_str(sch.payment_c), "next_due": sch.next_due,
                              "remaining_instalments": sch.remaining_instalments, "last_due": sch.last_due,
                              "total_interest": L.money_str(sch.total_interest_c), "total_insurance": L.money_str(sch.total_insurance_c),
                              "total_cost": L.money_str(sch.total_cost_c), "remaining_interest": L.money_str(sch.remaining_interest_c),
                              "approximate": sch.approximate, "assumptions": sch.assumptions,
                              "interest_by_year": [{"year": y.year, "interest": L.money_str(y.interest_c), "insurance": L.money_str(y.insurance_c),
                                                    "principal": L.money_str(y.principal_c), "partial": y.partial} for y in sch.by_year],
                              "outstanding_check": sch.outstanding_check, "payment_check": sch.payment_check}
        entry: dict = {"id": lb.id, "kind": lb.kind, "state": st}
        miss_am = st["missing_for_amortization"]
        if miss_am:
            need_all.append(f"{lb.id}: {', '.join(miss_am)} (for the amortization schedule)")
        cur_rate, rate_src = f.rate_pct, "memory"
        if cur_rate is None and st.get("implied_rate"):
            cur_rate, rate_src = Decimal(str(st["implied_rate"]["nominal_pct"])), "back-solved (approximate, confirm it)"
        months_left = st.get("remaining_months_to_end_date")
        if sch.status == "computed" and sch.remaining_instalments:
            months_left = sch.remaining_instalments
        # -- renegotiation / rachat / surroga
        if market is None:
            entry["renegotiation"] = {"status": "needs_market_rate",
                                      "note": "give market_rate_pct: a current, generic rate for a loan of similar duration, with its source "
                                              "and date (market_rate_date); the interactive skill can look it up, this tool never does"}
        else:
            miss = []
            if cur_rate is None:
                miss.append("rate.nominal")
            if cap is None:
                miss.append("outstanding + outstanding_as_of (or principal, rate, start_date, end_date)")
            if not months_left:
                miss.append("end_date (in the future)")
            if miss:
                entry["renegotiation"] = {"status": "missing_fields", "missing": miss}
                need_all.append(f"{lb.id}: {', '.join(miss)} (for the renegotiation estimate)")
            else:
                r = _guard_value(L.renegotiation_estimate, current_rate_pct=cur_rate, new_rate_pct=market, remaining_capital=Decimal(cap) / 100,
                                 remaining_months=months_left, country=country, variant=a.get("mode"), bank_fees=a.get("bank_fees") or 0,
                                 guarantee_fees=a.get("guarantee_fees") or 0, other_fees=a.get("other_fees") or 0,
                                 penalty_override=a.get("penalty"))
                entry["renegotiation"] = {"status": "estimated", "current_rate_source": rate_src, **r.to_dict()}
                if f.penalty_clause:
                    entry["renegotiation"]["notes"].append("your loan file states an early-repayment clause: give its amount as `penalty` "
                                                           "instead of the legal cap")
        # -- borrower insurance
        cur_ins = a.get("current_insurance_monthly")
        cur_ins_c = None if cur_ins is None else cur_ins
        if cur_ins_c is None and f.insurance_monthly_c is not None:
            cur_ins_c = Decimal(f.insurance_monthly_c) / 100
        alt = a.get("alternative_insurance_monthly")
        if alt is None:
            entry["insurance_delegation"] = {"status": "needs_alternative_quote",
                                             "note": "give alternative_insurance_monthly (a dated quote for equivalent guarantees)",
                                             "current_monthly": L.money_str(f.insurance_monthly_c), "delegated": f.insurance_delegated}
        elif cur_ins_c is None or not months_left:
            miss = (["insurance.monthly"] if cur_ins_c is None else []) + (["end_date"] if not months_left else [])
            entry["insurance_delegation"] = {"status": "missing_fields", "missing": miss}
            need_all.append(f"{lb.id}: {', '.join(miss)} (for the insurance estimate)")
        else:
            entry["insurance_delegation"] = {"status": "estimated", **_guard_value(
                L.insurance_delegation_estimate, current_monthly=cur_ins_c, alternative_monthly=alt, remaining_months=months_left,
                country=country, fees=a.get("insurance_fees") or 0).to_dict()}
        out["mortgages"].append(entry)
    if need_all:
        out["what_i_need"] = need_all
        out["next"] = "Create the questions with `questions_propose` (liabilities: the ids above); the user answers them or edits the loan file."
    return _plain(out)


def _what_if(s: ToolSession, a: dict):
    scenario = {**a["scenario"], "changes": [
        {**ch, "liability": _real_id(s, ch["liability"])} if ch.get("type") == "prepay_loan" else ch for ch in a["scenario"]["changes"]]}
    raw = _guard_value(WI.what_if, s.reg.ds, scenario, _recurring(s))
    out = _emit(s, raw)
    out["note"] = "Baseline = the forecast; the scenario shifts it by the listed changes only. Nothing is changed in your accounts or memory."
    return out


def _tax_candidates(s: ToolSession, a: dict):
    store = s.data()[2]
    hh = None
    try:
        hh = (store.load_plain("household.yaml") if store.exists("household.yaml") else {}).get("country")
    except Exception:                                                      # noqa: BLE001
        pass
    raw = _guard_value(TX.tax_candidates, s.reg.ds, a.get("year"), a.get("country"), household_country=hh if isinstance(hh, str) else None)
    out = _emit(s, raw)
    out["note"] = "Candidates to check, not entitlements. Never present an amount as the tax you will save."
    return out


def _onboarding_status(s: ToolSession, a: dict):
    store = s.data()[2]
    raw = OB.onboarding_status(s.reg.ds, store, s.con, s.cfg, _recurring(s))
    return _emit(s, raw)


# ---------------------------------------------------------------- questions_propose

def _usage_question(s: ToolSession, x, store, fam, first, known):
    from coach.subs.usage import usage_question
    return usage_question(s.cfg, x, fam, first, known, _today(s))


def _loan_question(s: ToolSession, lb, rel, today):
    from coach.memory import schemas
    from coach.memory.qgen import qid
    lease = lb.kind in ("loa", "lld")                              # E9: a lease owes no capital: its end-of-contract fields are asked instead
    miss = [f for f in (OB.LEASE_FIELDS if lease else OB.MORTGAGE_FIELDS) if OB._get(lb, f) is None]
    if not miss:
        return None
    key = f"fill:mortgage-check:{lb.id}"
    return schemas.Question(
        id=qid("fill", key), topic="Liabilities", key=key, origin="coach", created=today,
        stake=round((lb.monthly_payment or 0) * 12, 2) or None,
        question=((f"Liability {lb.id} ({lb.kind}): to prepare the end of the lease (buy or return, mileage) I need {', '.join(miss)}. "
                   "The lease contract has them.") if lease else
                  (f"Liability {lb.id} ({lb.kind}): to show its amortization schedule and estimate a renegotiation or an insurance change "
                   f"I need {', '.join(miss)}. The loan offer or the latest annual statement has them.")),
        evidence={"missing": miss, "monthly_payment": lb.monthly_payment}, suggested_target={"file": rel, "field": ",".join(miss)})


def _questions_propose(s: ToolSession, a: dict):
    from coach.classify.candidates import household_names, known_merchants
    from coach.memory import questions as Q
    from coach.mcp.tools import _propose
    reg, store, guard = s.reg, s.data()[2], s.data()[1]
    series, libs, custom = a.get("series") or [], a.get("liabilities") or [], a.get("custom") or []
    if not (series or libs or custom):
        raise ToolError("give at least one of series, liabilities or custom")
    if len(series) + len(libs) + len(custom) > MAX_QUESTIONS:
        raise ToolError(f"at most {MAX_QUESTIONS} questions per call")
    existing_keys = {q.key for q in store.questions() if q.key}
    existing_ids = {q.id for q in store.questions()}
    rec = _recurring(s)
    fam, first = household_names(s.con)
    known = known_merchants(s.con)
    qs, skipped = [], 0
    for ref in dict.fromkeys(series):
        x = next((v for v in rec.series if v.id == ref), None)
        if x is None:
            raise ToolError(f"unknown series {ref}: use a `rec_` id from `recurring` or `subscription_audit`")
        q = _usage_question(s, x, store, fam, first, known)
        if q.key in existing_keys or q.id in existing_ids:
            skipped += 1
        else:
            qs.append(q)
    for lid in dict.fromkeys(libs):
        real = _real_id(s, lid)
        hit = next(((rel, m) for rel, m in reg.ds.memory.liabilities if m.id == real), None)
        if hit is None:
            raise ToolError("unknown liability: use an id from `memory_context`")
        q = _loan_question(s, hit[1], hit[0], _today(s))
        if q is None or q.key in existing_keys or q.id in existing_ids:
            skipped += 1
        else:
            qs.append(q)
    from coach.memory import schemas
    from coach.memory.qgen import qid
    for c in custom:
        masked, _hit = guard.mask({"q": c["question"], "t": c.get("topic") or "General"})
        text = masked["q"].strip()
        key = "custom:" + hashlib.sha1(text.lower().encode()).hexdigest()[:10]
        if key in existing_keys:
            skipped += 1
            continue
        qs.append(schemas.Question(id=qid("custom", key), topic=masked["t"][:40], question=text, key=key, origin="coach",
                                  created=_today(s), stake=c.get("stake_eur")))
    if not qs:
        return {"status": "nothing_to_propose", "skipped_already_asked": skipped,
                "note": "every question was already asked (open, answered or dismissed)"}
    ops = [{"op": "append", "path": "questions", "value": d} for d in Q.questions_to_doc(qs)["questions"]]
    if not store.exists(Q.YAML_FILE):
        ops = [{"op": "create", "value": {"questions": []}}] + ops
    res = _propose(s, {"file": Q.YAML_FILE, "ops": ops, "reason": (a.get("reason") or "questions the coach needs answered").strip()[:480]})
    res["questions"] = len(qs)
    res["skipped_already_asked"] = skipped
    return res


# ---------------------------------------------------------------- specs

def specs() -> list[ToolSpec]:
    return [
        ToolSpec("monthly_review", "Monthly review of one closed month (default the last): cash flow (income, spending, saved, debt "
                 "service, drawn from savings), the biggest movers against usual with evidence refs (transactions, series, anomalies, "
                 "price changes), one-offs listed apart, budgets at month end and the forecast flags. All numbers are computed." + DATA_NOTE,
                 _obj({"month": MONTH, "top": {"type": "integer", "minimum": 1, "maximum": 15}}), _monthly_review),
        ToolSpec("explain_spike", "Why was a category, a group or an account high in a month? Splits the month into one-off, recurring, "
                 "new merchant and habitual spending, lists each merchant's change against its own usual, the top transactions (refs), "
                 "and the same month a year earlier when covered. Give `category` (id or group) OR `account`." + DATA_NOTE,
                 _obj({"category": {"type": "string", "maxLength": 80, "description": "category id or group, e.g. 'food.groceries' or 'food'"},
                       "account": {"type": "string", "maxLength": 60, "description": "account pseudonym, e.g. 'account-main-1'"},
                       "month": MONTH}), _explain_spike),
        ToolSpec("subscription_audit", "Every active recurring cost grouped (streaming, software, telecom, insurance, energy, memberships): "
                 "monthly and yearly cost, price rises, duplicates and overlaps, contracts on file, unknown usage, and ranked review "
                 "candidates with expected yearly savings ranges (fixed heuristic shares, not quotes). Never advises cancelling." + DATA_NOTE,
                 _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "review candidates listed (default 10)"},
                       "items_limit": {"type": "integer", "minimum": 1, "maximum": 60, "description": "services listed in detail, biggest first (default 20; totals and groups always cover all)"}}),
                 _subscription_audit),
        ToolSpec("cancellability", "Can a contract be cancelled now, from which date, with what notice and by which method? Applies the "
                 "FR (Loi Hamon, Loi Chatel, mutuelle infra-annual, Loi Lemoine, telecom, energy) and IT (Bersani, RC auto, ARERA) "
                 "rules to the dates of a contract on file (`contract`), a recurring series (`series`), or explicit fields. Without "
                 "arguments: every contract on file. Always 'verify with your contract'." + DATA_NOTE,
                 _obj({"contract": {"type": "string", "maxLength": 80}, "series": {"type": "string", "pattern": r"^rec_[0-9a-f]{6,}$"},
                       "country": {"type": "string", "enum": ["FR", "IT"]},
                       "kind": {"type": "string", "enum": ["insurance_home", "insurance_car", "insurance_health", "health", "loan_insurance",
                                                          "telecom", "energy", "streaming", "software", "subscription", "other"]},
                       "start_date": DATE, "renewal": DATE, "commitment_end": DATE,
                       "notice_period_days": {"type": "integer", "minimum": 0, "maximum": 365}, "tacit_renewal": {"type": "boolean"},
                       "group_contract": {"type": "boolean", "description": "employer-mandated group contract (mutuelle)"},
                       "billing_period": {"type": "string", "enum": ["monthly", "bimonthly", "quarterly", "yearly"]},
                       "include_rules": {"type": "boolean"}}), _cancellability),
        ToolSpec("savings_estimate", "Net saving of switching a recurring service to a cheaper alternative: monthly and yearly saving, "
                 "net over `months` after switching costs, break-even. Prices are the ones you give (dated and sourced); `quote_date` "
                 "older than 30 days is flagged. Pure calculator." + DATA_NOTE,
                 _obj({"current_monthly": {"type": "number", "minimum": 0, "maximum": 100000},
                       "alternative_monthly": {"type": "number", "minimum": 0, "maximum": 100000},
                       "switching_costs": {"type": "number", "minimum": 0, "maximum": 100000},
                       "months": {"type": "integer", "minimum": 1, "maximum": 120}, "quote_date": DATE},
                      ("current_monthly", "alternative_monthly")), _savings_estimate),
        ToolSpec("mortgage_check", "Mortgage review from the loan files: amortization (remaining capital, interest by year) when principal, "
                 "rate, start and end dates are known; renegotiation / rachat (FR, IRA = lower of 6 months of interest and 3 % of capital) or "
                 "surroga (IT, no penalty) estimate when you pass a current market rate (`market_rate_pct`, with `market_rate_date`); "
                 "borrower-insurance delegation (Loi Lemoine) estimate when you pass `alternative_insurance_monthly`. Missing fields are "
                 "listed in `what_i_need`, never guessed. Estimates only." + DATA_NOTE,
                 _obj({"liability": {"type": "string", "maxLength": 80}, "country": {"type": "string", "enum": ["FR", "IT"]},
                       "market_rate_pct": {"type": "number", "minimum": 0, "maximum": 25}, "market_rate_date": DATE,
                       "mode": {"type": "string", "enum": ["renegotiation", "rachat", "surroga"]},
                       "bank_fees": {"type": "number", "minimum": 0, "maximum": 100000},
                       "guarantee_fees": {"type": "number", "minimum": 0, "maximum": 100000},
                       "other_fees": {"type": "number", "minimum": 0, "maximum": 100000},
                       "penalty": {"type": "number", "minimum": 0, "maximum": 1000000, "description": "early-repayment amount from the contract"},
                       "alternative_insurance_monthly": {"type": "number", "minimum": 0, "maximum": 10000},
                       "current_insurance_monthly": {"type": "number", "minimum": 0, "maximum": 10000},
                       "insurance_fees": {"type": "number", "minimum": 0, "maximum": 10000}}), _mortgage_check),
        ToolSpec("what_if", "Scenario on the forecast: cancel a recurring series, change a category by a percent or to a target, add or "
                 "remove a monthly amount, a one-off on a date, prepay a loan, change the income. Returns baseline vs scenario (minimum "
                 "balance and date, balance at the horizon and at 90 days, monthly savings, yearly impact) with evidence and assumptions. "
                 "Nothing is changed anywhere." + DATA_NOTE, _obj({"scenario": WI.SCENARIO_SCHEMA}, ("scenario",)), _what_if),
        ToolSpec("tax_candidates", "Payments of an income year that may open a tax reduction or credit (FR: donations, home employment, "
                 "childcare under 6, school fees, Pinel reminder, alimony, PER; IT 730: medical expenses, mortgage interest, renovation, "
                 "school, insurance, donations) with the rule, ceilings, estimated range, documents to keep and a 'verify on the official "
                 "site' disclaimer. Reminders, not advice; nothing is filed." + DATA_NOTE,
                 _obj({"year": {"type": "integer", "minimum": 2000, "maximum": 2100}, "country": {"type": "string", "enum": ["FR", "IT"]}}),
                 _tax_candidates),
        ToolSpec("onboarding_status", "Checklist of what the coach still does not know: household and privacy declarations, account "
                 "owners, loans (fields mortgage-check needs), contracts, preferences, budgets, open questions, with the next actions." + DATA_NOTE,
                 _obj({}), _onboarding_status),
        ToolSpec("questions_propose", "PROPOSE questions for the user: usage questions about subscriptions (`series`: `rec_` ids), the "
                 "missing loan fields for mortgage-check (`liabilities`), or free questions (`custom`, no names). Creates a sealed memory "
                 "PROPOSAL on the open-questions file that the user reviews and accepts themselves; nothing is applied and nothing is "
                 "cancelled. Questions already asked are skipped." + DATA_NOTE,
                 _obj({"series": {"type": "array", "maxItems": MAX_QUESTIONS, "items": {"type": "string", "pattern": r"^rec_[0-9a-f]{6,}$"}},
                       "liabilities": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 80}},
                       "custom": {"type": "array", "maxItems": 5, "items": _obj({
                           "question": {"type": "string", "minLength": 10, "maxLength": 300}, "topic": {"type": "string", "maxLength": 40},
                           "stake_eur": {"type": "number", "minimum": 0, "maximum": 10000000}}, ("question",))},
                       "reason": {"type": "string", "minLength": 3, "maxLength": 480}}), _questions_propose, writes="proposal"),
    ]


# a valid example call of every skill tool (used by `coach coach tools --sizes` and the tests)
SAMPLE_ARGS = {
    "explain_spike": {"category": "food"},
    "savings_estimate": {"current_monthly": 30, "alternative_monthly": 20},
    "what_if": {"scenario": {"changes": [{"type": "adjust_category", "category": "food", "percent": -10}]}},
}
