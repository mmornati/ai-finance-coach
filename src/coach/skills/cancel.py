"""cancellability(contract) (E7-6): can this contract be cancelled now, from which date, with what notice, by which method.

A RULES TABLE (law names and what they say, no URLs) and a small deterministic function over the contract's own dates. It never
reads a contract text (that is ``coach memory doc extract``, which produces a proposal the user accepts) and never cancels
anything. Every answer ends with "verify with your contract".

Meaning of ``can_cancel_now``
    True   you can end it now (``earliest_effective_date`` is when it ends if the request is sent today); when leaving costs
           something (telecom inside its commitment) ``early_termination_cost`` says what and when exit is free;
    False  not before ``earliest_effective_date`` (or only with a penalty / at the next anniversary);
    None   not decidable from the dates on file (``unknown`` lists what is missing).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from coach import disclaimers as D
from coach.analytics.common import add_months

DISCLAIMER = D.get("contract")      # the wording lives in coach.disclaimers (E11-5)

# ---------------------------------------------------------------- the rules table

RULES: dict[str, dict] = {
    "fr-hamon": {
        "country": "FR", "families": ("insurance_home", "insurance_car"), "name": "Loi Hamon (insurance after the first year)",
        "law": "loi n° 2014-344 du 17 mars 2014 (Code des assurances art. L113-15-2)",
        "summary": "Home and car insurance can be cancelled at any time after the first year, free of charge; the cancellation takes "
                   "effect one month after the insurer receives the request (the new insurer can send it for you)."},
    "fr-chatel-insurance": {
        "country": "FR", "families": ("insurance_home", "insurance_car", "insurance_health", "insurance_other"),
        "name": "Tacit renewal of insurance (Loi Chatel)",
        "law": "loi n° 2005-67 du 28 janvier 2005 (Code des assurances art. L113-12, L113-15-1)",
        "summary": "The contract ends at its anniversary with the notice of the contract (L113-12; commonly two months). The insurer must "
                   "send the renewal notice (avis d'echeance) in time: if it is sent less than 15 days before the deadline for refusing "
                   "the renewal, or after it, the insured has 20 days from its sending to cancel (L113-15-1)."},
    "fr-ria-sante": {
        "country": "FR", "families": ("insurance_health",),
        "name": "Complementary health (mutuelle): resiliation infra-annuelle",
        "law": "loi n° 2019-733 du 14 juillet 2019 (decret 2020-1438)",
        "summary": "A complementary health contract can be cancelled at any time after one year, free of charge, effective one month "
                   "after the request. Employer-mandated group contracts are not covered."},
    "fr-lemoine": {
        "country": "FR", "families": ("loan_insurance",), "name": "Borrower insurance: Loi Lemoine",
        "law": "loi n° 2022-270 du 28 fevrier 2022 (Code de la consommation art. L313-30)",
        "summary": "The insurance of a loan can be replaced at any time, free of charge, by a policy with equivalent guarantees; "
                   "the lender must answer within 10 working days."},
    "fr-telecom": {
        "country": "FR", "families": ("telecom",), "name": "Telecom contracts (Loi Chatel)",
        "law": "loi n° 2008-3 du 3 janvier 2008 (Code de la consommation / CPCE)",
        "summary": "A commitment is limited to 24 months. After the first 12 months an early exit costs at most 25 % of the "
                   "subscription still due; without a commitment, or after it, the contract can be ended at any time, normally "
                   "within about ten days of the request."},
    "fr-energy": {
        "country": "FR", "families": ("energy",), "name": "Electricity and gas for households",
        "law": "Code de l'energie (free choice of supplier)",
        "summary": "No termination fee for household electricity or gas; the new supplier handles the switch (typically within "
                   "three weeks). Fixed-term market offers may state conditions: read them."},
    "fr-3-clics": {
        "country": "FR", "families": ("subscription", "telecom", "insurance_home", "insurance_car", "insurance_health", "insurance_other", "other"),
        "name": "Termination online (\"resiliation en 3 clics\")",
        "law": "loi n° 2022-1158 du 16 aout 2022 (pouvoir d'achat), art. L215-1-1 Code de la consommation, in force since 1 June 2023",
        "summary": "A contract that can be signed online must be terminable online through a clearly visible button; the provider "
                   "must confirm the date the contract ends."},
    "fr-chatel-renewal": {
        "country": "FR", "families": ("subscription", "other"), "name": "Tacit renewal of service contracts (Loi Chatel)",
        "law": "loi n° 2005-67 du 28 janvier 2005 (Code de la consommation art. L215-1)",
        "summary": "For a tacitly renewed contract the provider must remind you, between three and one month before the end of the "
                   "period in which you can refuse the renewal. If it did not, you can terminate at any time after the renewal and "
                   "get the unused part refunded."},
    "it-bersani-telecom": {
        "country": "IT", "families": ("telecom",), "name": "Telecom and pay-TV: Legge Bersani",
        "law": "D.L. 7/2007 conv. L. 40/2007 art. 1 (Bersani), AGCOM rules",
        "summary": "The customer can withdraw at any time without penalties; only costs justified by the real cost of closing, or "
                   "proportionate to a discount received, can be charged. Notice is at most 30 days."},
    "it-bersani-energy": {
        "country": "IT", "families": ("energy",), "name": "Electricity and gas: recesso (ARERA)",
        "law": "ARERA rules for the retail market; D.L. 7/2007 (Bersani)",
        "summary": "A household can withdraw at any time without penalties; notice is at most one month, and a switch to a new "
                   "supplier is handled by the new supplier."},
    "it-rcauto": {
        "country": "IT", "families": ("insurance_car",), "name": "RC auto: no tacit renewal",
        "law": "D.L. 179/2012 art. 22, conv. L. 221/2012",
        "summary": "The motor third-party policy ends at its expiry: there is no tacit renewal, so no cancellation notice is "
                   "needed; shop for a new policy before the expiry. The cover continues for 15 days after the expiry (grace period)."},
    "it-insurance": {
        "country": "IT", "families": ("insurance_home", "insurance_health", "insurance_other", "other"), "name": "Other insurance: disdetta",
        "law": "Codice civile art. 1899 and the policy conditions",
        "summary": "Annual non-life policies renew tacitly unless a notice (disdetta) is sent within the period in the policy. A policy "
                   "longer than five years (with a discount for its duration) can be cancelled by the insured after the fifth year with "
                   "60 days' notice, effective at the end of the policy year (art. 1899 c.c.)."},
    "it-loan-insurance": {
        "country": "IT", "families": ("loan_insurance",), "name": "Insurance linked to a mortgage",
        "law": "D.L. 1/2012 art. 28; IVASS regulations 40/2018 and 41/2018",
        "summary": "The borrower may bring a policy of their choice (the bank must accept an equivalent one); the unused part of a "
                   "single premium is refundable on early closure of the loan."},
    "it-subscription": {
        "country": "IT", "families": ("subscription", "other"), "name": "Subscriptions: Codice del consumo",
        "law": "D.Lgs. 206/2005 (Codice del consumo)",
        "summary": "Terms of renewal and withdrawal are those of the contract; the 14-day withdrawal right applies only to distance "
                   "contracts at signing."},
}

for _r in RULES.values():                        # every rule carries its source (law / article) and when the table was last reviewed
    _r.setdefault("source", _r["law"])
    _r["last_reviewed"] = "2026-10"

FAMILY = {
    "insurance_home": "insurance_home", "insurance_car": "insurance_car", "insurance_health": "insurance_health",
    "insurance_other": "insurance_other", "water": "other",
    "health": "insurance_health", "mutuelle": "insurance_health", "loan_insurance": "loan_insurance",
    "borrower_insurance": "loan_insurance", "telecom": "telecom", "energy": "energy", "streaming": "subscription",
    "software": "subscription", "membership": "subscription", "subscription": "subscription", "other": "other"}
CATEGORY_FAMILY = {
    "subscriptions.telecom": "telecom", "housing.energy": "energy", "housing.home_insurance": "insurance_home",
    "transport.car_insurance": "insurance_car", "health.health_insurance": "insurance_health", "insurance.borrower": "loan_insurance",
    "leisure.sports_activities": "subscription"}
INSURANCE_FAMILIES = ("insurance_home", "insurance_car", "insurance_health", "insurance_other")


def family_of(kind: Optional[str], category: Optional[str] = None) -> str:
    if kind and kind in FAMILY:
        return FAMILY[kind]
    if category:
        if category in CATEGORY_FAMILY:
            return CATEGORY_FAMILY[category]
        if category.startswith("subscriptions."):
            return "subscription"
    return "other"


@dataclass
class Terms:
    kind: str = "other"
    start_date: Optional[dt.date] = None
    renewal: Optional[dt.date] = None
    commitment_end: Optional[dt.date] = None
    notice_period_days: Optional[int] = None
    tacit_renewal: Optional[bool] = None
    group_contract: Optional[bool] = None
    billing_period: Optional[str] = None
    start_is_lower_bound: bool = False         # the 'start' is only the first payment seen: the contract may be older
    monthly_fee_c: Optional[int] = None        # the monthly subscription (cents), to price an early exit


def terms_of(contract, **override) -> Terms:
    """The dates and terms on file of a memory ``Contract`` (a ``contracts/*.yaml`` model), optionally overridden."""
    t = Terms(kind=contract.kind or "other", start_date=contract.start_date, renewal=contract.renewal,
              commitment_end=contract.commitment_end, notice_period_days=contract.notice_period_days,
              billing_period=contract.billing.period if contract.billing else None)
    b = contract.billing
    if b and b.amount is not None and b.period:
        t.monthly_fee_c = round(b.amount * 100 / {"monthly": 1, "bimonthly": 2, "quarterly": 3, "yearly": 12}[b.period])
    for k, v in override.items():
        setattr(t, k, v)
    return t


def cancellability_of(contract, today: dt.date, country: str = "FR", **override) -> dict:
    """:func:`cancellability` for a memory ``Contract`` (pass ``group_contract=True`` for an employer-mandated mutuelle)."""
    return cancellability(terms_of(contract, **override), today, country)


def rules_table(country: Optional[str] = None) -> list[dict]:
    return [{"id": k, "country": v["country"], "name": v["name"], "law": v["law"], "source": v["source"], "last_reviewed": v["last_reviewed"],
             "summary": v["summary"], "applies_to": list(v["families"])} for k, v in RULES.items() if country is None or v["country"] == country.upper()]


def _rule(rid: str) -> dict:
    r = RULES[rid]
    return {"id": rid, "name": r["name"], "law": r["law"], "source": r["source"], "last_reviewed": r["last_reviewed"], "summary": r["summary"]}


def _next_renewal(renewal: dt.date, today: dt.date) -> dt.date:
    d = renewal
    for _ in range(60):
        if d >= today:
            return d
        d = add_months(d, 12)
    return d


def cancellability(terms: Terms, today: dt.date, country: str = "FR") -> dict:
    """Apply the rules of `country` (FR / IT) to the dates on file. Returns a JSON-safe dict."""
    country = country.upper()
    if country not in ("FR", "IT"):
        raise ValueError("country must be FR or IT")
    fam = family_of(terms.kind)
    rules: list[str] = []
    unknown: list[str] = []
    conditions: list[str] = []
    can: Optional[bool] = None
    effective: Optional[dt.date] = None
    notice: Optional[int] = terms.notice_period_days
    method = None
    extra: dict = {}
    start = terms.start_date
    anniv = add_months(start, 12) if start else None
    renewal = terms.renewal

    def anniversary_route(label: str = "anniversary", months_notice: int = 2):
        if renewal is None:
            return
        nxt = _next_renewal(renewal, today)
        send_by = add_months(nxt, -months_notice)
        extra["anniversary_route"] = {"effective": nxt, "send_notice_by": send_by, "months_notice": months_notice,
                                      "notice_assumption": "two months unless the contract says otherwise (L113-12)",
                                      "notice_still_possible": send_by >= today,
                                      "assumes": "annual renewal on this date" if nxt != renewal else None}

    if country == "FR" and fam in ("insurance_home", "insurance_car", "insurance_health"):
        rules += ["fr-ria-sante"] if fam == "insurance_health" else ["fr-hamon"]
        rules.append("fr-chatel-insurance")
        if fam == "insurance_health" and terms.group_contract:
            can = False
            conditions.append("an employer-mandated group contract cannot be cancelled this way (only on a qualifying event)")
        elif start is None:
            unknown.append("start_date")
        elif today >= anniv:
            can, effective, notice = True, add_months(today, 1), 30
        elif terms.start_is_lower_bound:
            unknown.append("contract start date (only the first payment seen is known, and it is less than a year old)")
        else:
            can, effective, notice = False, add_months(anniv, 1), 30
            extra["first_request_date"] = anniv
        if can is False and "first_request_date" in extra:
            conditions.append("free cancellation opens after the first year; before that, the contract ends at its anniversary "
                              "with the notice of the contract (commonly two months)")
        anniversary_route()
        method = ("written request (registered letter, e-mail or the insurer's online form), or let the new insurer send it for "
                  "you; a contract signed online can be terminated online (3 clicks)")
        rules.append("fr-3-clics")
    elif country == "FR" and fam == "insurance_other":
        # pet, legal protection, accident...: the Code des assurances anniversary rule (L113-12, L113-15-1); the free mid-term cancellation
        # (Hamon / mutuelle) is for car, home and health policies only, so it is NOT applied here
        rules += ["fr-chatel-insurance", "fr-3-clics"]
        anniversary_route()
        if renewal is None:
            unknown.append("renewal (anniversary date)")
        else:
            can, effective = False, _next_renewal(renewal, today)
        conditions.append("free cancellation at any time (Hamon) applies to eligible car and home policies: check whether yours is one; "
                          "otherwise the contract ends at its anniversary with the notice of the contract (commonly two months)")
        method = "written request (registered letter, e-mail or the insurer's online form) before the notice deadline"
    elif country == "FR" and fam == "loan_insurance":
        rules.append("fr-lemoine")
        can, notice = True, None
        extra["lender_answer_working_days"] = 10
        conditions += ["the replacement policy must offer equivalent guarantees", "the old policy ends the day the new one starts: "
                       "there must be no gap in cover"]
        method = "send the new policy's offer to the lender (the new insurer usually does the paperwork)"
    elif country == "FR" and fam == "telecom":
        rules += ["fr-telecom", "fr-3-clics"]
        if terms.commitment_end and terms.commitment_end > today:
            # you CAN leave now (can_cancel_now true) but it costs: the remaining fees in the first 12 months, at most 25 % of them after
            can, notice = True, terms.notice_period_days or 10
            effective = today + dt.timedelta(days=notice)
            left = max(1, -(-((terms.commitment_end - today).days * 10) // 305))           # whole months left, rounded up (30.5 days)
            after12 = bool(start) and today >= add_months(start, 12)
            fee = terms.monthly_fee_c
            if start is None or (terms.start_is_lower_bound and not after12):
                basis, share = "unknown: the full remainder in the first 12 months, at most 25 % of it afterwards (Loi Chatel)", None
            elif after12:
                basis, share = "25 % of the subscription still due (after the first 12 months, Loi Chatel)", 25
            else:
                basis, share = "the subscription still due (first 12 months)", 100
            cost = {"basis": basis, "share_pct": share, "remaining_months": left, "free_exit_date": terms.commitment_end,
                    "amount": None if (fee is None or share is None) else round(fee * left * share / 100) / 100}
            if fee is None:
                cost["needs"] = "billing.amount of the contract to price it"
            extra["early_termination_cost"] = cost
            conditions.append("leaving before the end of the commitment costs the amount in early_termination_cost; waiting until "
                              f"{terms.commitment_end.isoformat()} is free")
        else:
            can, notice = True, terms.notice_period_days or 10
            effective = today + dt.timedelta(days=notice)
        method = "online (termination button), by letter or e-mail, or through the new operator (number portability ends the old line)"
    elif country == "FR" and fam == "energy":
        rules.append("fr-energy")
        can, notice = True, terms.notice_period_days
        if terms.commitment_end and terms.commitment_end > today:
            conditions.append("this looks like a fixed-term offer: read its conditions for any exit clause")
        method = "subscribe to the new supplier, who cancels the old contract for you; do not cancel first (no gap in supply)"
    elif country == "FR":                                         # subscriptions and other contracts
        rules += ["fr-chatel-renewal", "fr-3-clics"] if fam == "subscription" else ["fr-chatel-renewal"]
        can, effective, notice = _generic(terms, today, conditions, extra, unknown)
        method = "online termination button if you signed online, else the method in the contract (letter or e-mail)"
    elif country == "IT" and fam == "telecom":
        rules.append("it-bersani-telecom")
        if terms.commitment_end and terms.commitment_end > today:
            can, effective = False, terms.commitment_end
            conditions.append("an early exit can only cost what is justified or proportionate to the discount received")
        else:
            can, notice = True, min(30, terms.notice_period_days or 30)
            effective = today + dt.timedelta(days=notice)
        method = "written request to the operator (e-mail or registered letter) or through the new operator"
    elif country == "IT" and fam == "energy":
        rules.append("it-bersani-energy")
        can, notice = True, min(30, terms.notice_period_days or 30)
        effective = today + dt.timedelta(days=notice)
        method = "sign with the new supplier, who sends the recesso; a direct request goes to the current supplier"
    elif country == "IT" and fam == "insurance_car":
        rules.append("it-rcauto")
        if renewal:
            can, effective = False, _next_renewal(renewal, today)
            conditions.append("no notice is needed: the policy simply ends at expiry (the cover lasts 15 more days); mid-term only for "
                              "sale, destruction or theft of the vehicle")
        else:
            unknown.append("renewal (expiry date)")
        notice = None
        method = "do nothing at expiry and buy the new policy beforehand"
    elif country == "IT" and fam in ("insurance_home", "insurance_health", "insurance_other"):
        rules.append("it-insurance")
        can, effective, notice = _notice_by_renewal(terms, today, unknown, extra, default_notice=None)
        conditions.append("a policy longer than five years (with a duration discount) can be ended by the insured after the fifth year with "
                          "60 days' notice, effective at the end of the policy year (art. 1899 c.c.); annual policies follow the contract")
        method = "disdetta by registered letter or certified e-mail within the period in the policy"
    elif country == "IT" and fam == "loan_insurance":
        rules.append("it-loan-insurance")
        can = True
        conditions += ["the replacement policy must be equivalent to what the bank requires",
                       "a single premium: ask for the refund of the unused part on early closure"]
        method = "present the new policy to the bank"
    else:                                                          # IT subscriptions and anything else
        rules.append("it-subscription")
        can, effective, notice = _generic(terms, today, conditions, extra, unknown)
        method = "the method in the contract (online area, letter or certified e-mail)"
    if terms.notice_period_days and renewal and "contract_notice_deadline" not in extra:
        nxt = _next_renewal(renewal, today)
        send_by = nxt - dt.timedelta(days=terms.notice_period_days)
        extra["contract_notice_deadline"] = {"renewal": nxt, "send_notice_by": send_by, "notice_period_days": terms.notice_period_days,
                                             "days_left": (send_by - today).days,
                                             "assumes": "annual renewal on this date" if nxt != renewal else None}
    out = {"country": country, "family": fam, "kind": terms.kind, "can_cancel_now": can, "earliest_effective_date": effective,
           "notice_period_days": notice, "method": method, "conditions": conditions, "unknown": unknown,
           "rules": [_rule(r) for r in dict.fromkeys(rules)], "disclaimer": DISCLAIMER}
    out.update(extra)
    return out


def _notice_by_renewal(terms: Terms, today: dt.date, unknown: list, extra: dict, default_notice: Optional[int]):
    renewal = terms.renewal
    notice = terms.notice_period_days or default_notice
    if renewal is None:
        unknown.append("renewal (expiry date)")
        return None, None, notice
    nxt = _next_renewal(renewal, today)
    if notice is None:
        unknown.append("notice_period_days")
        return False, nxt, None
    send_by = nxt - dt.timedelta(days=notice)
    extra["send_notice_by"] = send_by
    return False, nxt, notice


def _generic(terms: Terms, today: dt.date, conditions: list, extra: dict, unknown: list):
    """Subscriptions and other contracts: a commitment, a tacit renewal with notice, or a rolling period."""
    if terms.commitment_end and terms.commitment_end > today:
        conditions.append("the minimum term runs until this date: leaving earlier is governed by the contract")
        return False, terms.commitment_end, terms.notice_period_days
    renewal = terms.renewal
    if renewal is not None:
        nxt = _next_renewal(renewal, today)
        notice = terms.notice_period_days or 0
        send_by = nxt - dt.timedelta(days=notice)
        extra["send_notice_by"] = send_by
        if renewal < today:
            conditions.append("it renewed on " + renewal.isoformat() + ": if the provider did not remind you before that "
                              "date, you may be able to terminate at any time (see the rules)")
        return (send_by >= today and renewal >= today), nxt, notice or None
    if terms.billing_period == "monthly" or terms.tacit_renewal is False:
        conditions.append("a rolling monthly subscription normally ends at the end of the period already paid")
        return True, None, terms.notice_period_days
    unknown.append("renewal or commitment_end")
    return None, None, terms.notice_period_days


# ---------------------------------------------------------------- E8-3: the rules engine behind the inventory, the calendar and the letters

VERIFY = D.get("contract_verify")


def terms_for_series(x, contract=None, *, infer_fee: bool = False, **override) -> Terms:
    """Terms of a recurring series: the linked contract's dates when there is one, else only what the bank data gives (the
    first payment seen is a LOWER BOUND of the contract start). ``infer_fee``: price an early exit from the series' own
    monthly cost when the contract has no billing amount."""
    if contract is not None:
        t = terms_of(contract)
        t.kind = contract.kind if contract.kind and contract.kind != "other" else family_of(None, x.category)
    else:
        t = Terms(kind=family_of(None, x.category), start_date=x.first_date, start_is_lower_bound=True,
                  renewal=x.next_expected if x.cadence in ("yearly", "semiannual", "quarterly") else None)
    if infer_fee and t.monthly_fee_c is None:
        t.monthly_fee_c = round(x.yearly_cost_c / 12)
    for k, v in override.items():
        setattr(t, k, v)
    return t


def legal_basis(res: dict) -> list[dict]:
    """The rules that apply, each with its law, its source and when the table was last reviewed."""
    return [{"id": r["id"], "name": r["name"], "law": r["law"], "source": r["source"], "last_reviewed": r["last_reviewed"]}
            for r in res.get("rules", [])]


def cancellation_info(res: dict) -> dict:
    """The part of a :func:`cancellability` result that the subscriptions view shows: can cancel now, earliest date, notice,
    method, early-termination cost, legal basis + source + review date, the 'verify' disclaimer."""
    ec = res.get("early_termination_cost")
    return {"country": res["country"], "family": res["family"], "can_cancel_now": res["can_cancel_now"],
            "earliest_effective_date": res["earliest_effective_date"], "notice_period_days": res["notice_period_days"],
            "method": res["method"], "conditions": res["conditions"], "unknown": res["unknown"],
            "early_termination_cost": ec, "legal_basis": legal_basis(res),
            "anniversary_route": res.get("anniversary_route"), "contract_notice_deadline": res.get("contract_notice_deadline"),
            "first_request_date": res.get("first_request_date"), "send_notice_by": res.get("send_notice_by"),
            "last_reviewed": max((r["last_reviewed"] for r in res.get("rules", [])), default=None),
            "verify": VERIFY, "disclaimer": res["disclaimer"]}


def notice_deadlines(contract, res: dict, today: dt.date, horizon: Optional[dt.date] = None) -> list[dict]:
    """Dates to act by for a contract on file, derived from the rules engine's answer (``res`` = cancellability of the contract):

    * ``contract_notice``  renewal / commitment end minus the contract's own notice period (as before E8);
    * ``legal_notice``     the legal anniversary deadline when the contract states no notice (FR insurance: two months before the
                           anniversary, art. L113-12), tagged with the rule;
    * ``window_opens``     the first day a free cancellation becomes possible (Hamon / mutuelle: after the first year).
    Only dates from today on (and up to ``horizon``) are returned."""
    out: list[dict] = []

    def keep(d):
        return d is not None and d >= today and (horizon is None or d <= horizon)
    n = contract.notice_period_days
    for date_, what in ((contract.renewal, "renews"), (contract.commitment_end, "commitment ends")):
        if date_ is None or date_ < today or not n:
            continue
        nd = date_ - dt.timedelta(days=n)
        if keep(nd):
            out.append({"date": nd, "kind": "contract_notice", "reference_date": date_, "reference": what, "days": n,
                        "rules": [r["name"] for r in res.get("rules", [])]})
    ar = res.get("anniversary_route")
    if not n and ar and keep(ar["send_notice_by"]) and ar["effective"] >= today:
        out.append({"date": ar["send_notice_by"], "kind": "legal_notice", "reference_date": ar["effective"], "reference": "anniversary",
                    "days": None, "months": ar["months_notice"], "rules": [r["name"] for r in res.get("rules", [])
                                                                          if r["id"] == "fr-chatel-insurance"] or [r["name"] for r in res.get("rules", [])]})
    fr = res.get("first_request_date")
    if fr and keep(fr):
        out.append({"date": fr, "kind": "window_opens", "reference_date": fr, "reference": "free cancellation opens", "days": None,
                    "rules": [r["name"] for r in res.get("rules", [])][:1]})
    return out
