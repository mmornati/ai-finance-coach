"""tax_candidates(year, country) (E7-10): payments of the year that may open a tax reduction or credit. A reminder list, NEVER a
tax return, never advice: every candidate carries its rule, the ceilings used, the documents to keep and "verify on
impots.gouv.fr / Agenzia delle Entrate".

``year`` is the INCOME year (the one the return in spring of the next year, or the 730 of the next year, is about). Amounts come
from the household's transactions of that calendar year, matched by CATEGORY (e.g. ``charity.donations``) or by TAG
(``emploi-domicile``, ``pension-alimentaire``, ``per``... a tag is added with a memory annotation; the list of tags of each rule is in
``docs/skills.md``). The country is the household's (``country`` in household.yaml, FR by default) or the one asked for.

France (FR)
    dons              reduction 66 % (75 % of the first 1,000 EUR to organisations helping people in difficulty); the 20 %-of-income
                      ceiling is not computed (income unknown): range = all at 66 % ... best case
    emploi a domicile credit 50 % of the expenses within 12,000 EUR (+1,500 per dependent child, max 15,000)
    garde d'enfants   credit 50 % within 3,500 EUR per child under 6 on 1 January (crèche, assistante maternelle, halte-garderie)
    scolarite         reduction per child: college 61, lycee 153, superieur 183 EUR (a flat amount, not related to the spending)
    pinel             annual reduction reminder from the Pinel commitment of ``assets.yaml``
    pension alimentaire / PER: amounts deducted from the taxable income: the saving depends on your marginal rate (not computed)
Italy (IT, 730)
    spese mediche     19 % of the part above the 129.11 EUR franchise
    interessi mutuo   19 % of the mortgage interest paid on the main home, up to 4,000 EUR (max 760 EUR); interest from the amortization
    ristrutturazioni  renovation deduction (50 % / 36 % / 30 % by year and use), ceiling 96,000 EUR per unit, in 10 yearly instalments
    istruzione        19 % of school expenses up to 800 EUR per student
    assicurazioni     19 % of life / accident premiums up to 530 EUR
    erogazioni liberali  26-35 % of donations to qualified non-profit bodies, up to 30,000 EUR
The ceilings and rates are those known when this module was written; every candidate states them so that a change shows up
against the official page. A candidate is a reason to look, not an entitlement.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach import disclaimers as D
from coach.analytics.common import money_str
from coach.analytics.dataset import Dataset
from coach.i18n_msg import strip_msgs
from coach.skills import loans as L
from coach.skills.money import cents, pct_of_c

DISCLAIMER = D.TAX_BY_COUNTRY      # the wording lives in coach.disclaimers (E11-5)
TAGS = {
    "fr-emploi-domicile": ("emploi-domicile", "cesu", "services-personne"),
    "fr-pension-alimentaire": ("pension-alimentaire",),
    "fr-per": ("per", "plan-epargne-retraite"),
    "it-assicurazioni": ("assicurazione-vita", "assicurazione-infortuni"),
    "it-ristrutturazioni": ("ristrutturazione", "bonus-casa"),
}
FRANCHISE_IT_C = 12911
LAST_REVIEWED = "2026-10"
# IT renovation deduction (TUIR art. 16-bis) by year: the rates changed with each budget law and the 2026+ ones are NOT settled here.
# low_rate / high_rate bracket what the candidate rate may be; confidence says how sure the table is.
IT_RENOVATION_RATES = {
    "<=2024": {"main": 50, "other": 50, "ceiling_eur": 96000, "confidence": "high", "note": "50 % for every property"},
    2025: {"main": 50, "other": 36, "ceiling_eur": 96000, "confidence": "medium", "note": "50 % main home, 36 % other properties"},
    "2026-2027": {"main": "36 or 50", "other": "30 or 36", "ceiling_eur": 96000, "confidence": "low",
                  "note": "CHECK CURRENT LAW: the budget law 2026 may have extended 50 % (main home) / 36 % (other); the older schedule is 36 % / 30 %"},
    ">=2028": {"main": 30, "other": 30, "ceiling_eur": 48000, "confidence": "low", "note": "fallback schedule (about 30 %, ceiling 48,000 EUR): uncertain"},
}
IT_19_CAVEAT = ("19 % deductions shrink for incomes above 120,000 EUR (to zero at 240,000) and, from 2025, total deductions are capped for incomes "
                "above 75,000 EUR (TUIR art. 15 and 16-ter): the estimate ignores your income")


def it_renovation_rates(year: int) -> dict:
    key = "<=2024" if year <= 2024 else 2025 if year == 2025 else "2026-2027" if year <= 2027 else ">=2028"
    t = dict(IT_RENOVATION_RATES[key], last_reviewed=LAST_REVIEWED)
    if key == "<=2024":
        t["low_rate"] = t["high_rate"] = 50
    elif key == 2025:
        t["low_rate"], t["high_rate"] = 36, 50
    elif key == "2026-2027":
        t["low_rate"], t["high_rate"] = 30, 50
    else:
        t["low_rate"] = t["high_rate"] = 30
    return t


def _tx_in_year(ds: Dataset, year: int) -> list:
    return [t for t in ds.txs if t.date.year == year]


def _by_cat(txs: list, cats: tuple) -> list:
    return [t for t in txs if t.category in cats]


def _by_tag(txs: list, tags: tuple) -> list:
    return [t for t in txs if t.tags & set(tags)]


def _spent(txs: list) -> int:
    return -sum(t.amount_c for t in txs)


def _covered_note(ds: Dataset, year: int, txs: list, cats: tuple) -> Optional[str]:
    from coach.analytics.common import last_closed_month
    carriers = sorted({a for c in cats for a in ds.carrying_accounts(c)} or {t.account for t in txs})
    if not carriers:
        return None
    closed = last_closed_month(ds.today)
    months = [f"{year:04d}-{m:02d}" for m in range(1, 13) if f"{year:04d}-{m:02d}" <= closed]
    covered = set(ds.coverage.common_months(carriers))
    miss = [m for m in months if m not in covered]
    return f"months of {year} not fully covered by the accounts carrying this spending: {', '.join(miss)}" if miss else None


def _top_entities(txs: list, n: int = 6) -> list[dict]:
    tot: dict[str, list] = {}
    for t in txs:
        e = tot.setdefault(t.entity, [0, 0])
        e[0] -= t.amount_c
        e[1] += 1
    return [{"entity": k, "total": money_str(v[0]), "n_tx": v[1]} for k, v in sorted(tot.items(), key=lambda kv: (-kv[1][0], kv[0]))[:n]]


def _children(members: list, year: int) -> list[dict]:
    return [{"id": m.id, "birth_year": m.birth_year} for m in members if m.role == "child"]


def _cand(rid: str, title: str, mech: str, base_c: int, txs: list, **kw) -> dict:
    ev = [t.key for t in sorted(txs, key=lambda t: (t.amount_c, t.key))[:5]]
    out = {"id": rid, "heading": title, "mechanism": mech, "spent": money_str(base_c), "n_tx": len(txs), "evidence": ev}
    out.update(kw)
    return out


def _range(low_c: Optional[int], high_c: Optional[int]) -> dict:
    return {"low": money_str(low_c), "high": money_str(high_c)}


def _rental_income(ds: Dataset, year: int) -> list:
    """One candidate per rental property: the gross rents and the micro-foncier / reel figures of the year (see coach.rental.taxyear)."""
    from coach.rental import cashflow as RCF, model as RM, taxyear as RTX
    out = []
    for p in RM.properties(ds):
        t = RTX.tax_year(ds, p, year)
        if t.get("status") != "computed":
            continue
        rows = [r for r in RCF.all_rows(ds, p)[0] if r.month.startswith(f"{year:04d}-")]
        ev = [k for r in rows for k in r.rent_evidence][:5]
        mi, rl = t["micro_foncier"], t["reel"]
        miss = [] if t["complete"] else [f"the year is not fully covered by the property account: {', '.join(t['months_incomplete'] + t['months_missing_data'])}"]
        miss += list(rl["unknown"])
        out.append({"id": "fr-revenus-fonciers", "heading": "Rental income of a rental property (declaration of rental income)",
                    "mechanism": "declaration", "spent": None, "n_tx": len(ev), "evidence": ev, "asset": p.id,
                    "rate": f"micro-foncier: {mi['abatement_pct']} % flat abatement; reel: the deductible costs of the year",
                    "gross_rents": money_str(t["gross_rents_c"]),
                    "micro_foncier": {"taxable": money_str(mi["taxable_c"]), "abatement": money_str(mi["abatement_c"]),
                                      "within_ceiling": mi["within_ceiling"], "ceiling": money_str(mi["ceiling_c"])},
                    "reel": {"total_deductible": money_str(rl["total_deductible_c"]), "net": money_str(rl["net_c"]),
                             "deductible": [{"item": i["item"], "amount": money_str(i["amount_c"]), "bound": i["bound"]} for i in rl["deductible"]]},
                    "lower_taxable_candidate": t["lower_taxable_candidate"],
                    "ceilings": [f"micro-foncier ceiling {money_str(mi['ceiling_c'])} EUR of gross rents for the household (verify)"],
                    "estimated_benefit": _range(None, None),
                    "documents_to_keep": [d["item"] for d in t["documents"]], "missing_info": miss, "notes": t["notes"],
                    "source": "Code general des impots: revenus fonciers (art. 14 and following), micro-foncier regime (art. 32)",
                    "_high": 0, "_low": 0})
    return out


def _fr(ds: Dataset, year: int, txs: list, members: list, assets: list) -> tuple[list, list]:
    out, nodata = [], []
    kids = _children(members, year)
    # -- dons
    cats = ("charity.donations",)
    don = _by_cat(txs, cats)
    base = _spent(don)
    if don and base > 0:
        top75 = min(base, 100000)
        low = pct_of_c(base, 66)
        high = pct_of_c(top75, 75) + pct_of_c(base - top75, 66)
        out.append(_cand("fr-dons", "Donations to charities (reduction d'impot)", "reduction", base, don,
                         rate="66 % (75 % for the first 1,000 EUR given to organisations helping people in difficulty)",
                         ceilings=["75 % rate: first 1,000 EUR", "total gifts: 20 % of the taxable income (not computed)"],
                         estimated_benefit=_range(low, high), organisations=_top_entities(don),
                         documents_to_keep=["receipt (recu fiscal) of each organisation", "bank statement lines"],
                         missing_info=["which organisations help people in difficulty (75 % rate)", "taxable income (20 % ceiling)"],
                         notes=["only gifts to eligible organisations count: ask for the recu fiscal", _covered_note(ds, year, don, cats)],
                         source="Code general des impots art. 200 and 238 bis", _high=high, _low=low))
    else:
        nodata.append({"id": "fr-dons", "how": "category charity.donations"})
    # -- emploi a domicile
    emp = _by_tag(txs, TAGS["fr-emploi-domicile"])
    base = _spent(emp)
    n_kids = sum(1 for k in kids if k["birth_year"] is None or year - k["birth_year"] < 18)
    n_old = sum(1 for m in members if m.role == "adult" and m.birth_year is not None and year - m.birth_year > 65)
    n_dep = n_kids + n_old
    ceiling = min(1200000 + 150000 * n_dep, 1500000)
    first_year = min(1500000 + 150000 * n_dep, 1800000)
    if emp and base > 0:
        eligible = min(base, ceiling)
        credit = pct_of_c(eligible, 50)
        out.append(_cand("fr-emploi-domicile", "Home employment and personal services (credit d'impot)", "credit", base, emp,
                         rate="50 % of the expenses actually paid, net of any aid received (CAF, employer)",
                         ceilings=[f"expenses ceiling used: {money_str(ceiling)} EUR (12,000 + 1,500 per dependent child or member over 65, max 15,000); "
                                   f"{n_kids} child(ren) and {n_old} adult(s) over 65 counted from household.yaml",
                                   f"first year of employment: {money_str(first_year)} EUR (15,000 + 1,500 per dependent / over-65 member, max 18,000)",
                                   "20,000 EUR if the household includes a person with a disability card (carte d'invalidite)"],
                         estimated_benefit=_range(credit if base <= ceiling else pct_of_c(ceiling, 50), credit),
                         documents_to_keep=["annual tax certificate (attestation fiscale) of CESU / the organisation", "invoices"],
                         missing_info=["aids received (to be deducted)", "whether it is the first year of employment (higher ceiling)"],
                         notes=[_covered_note(ds, year, emp, ())], source="CGI art. 199 sexdecies", _high=credit, _low=credit))
    else:
        nodata.append({"id": "fr-emploi-domicile", "how": "tag transactions with emploi-domicile (or cesu)"})
    # -- garde d'enfants
    cats = ("kids.childcare",)
    gar = _by_cat(txs, cats)
    base = _spent(gar)
    young = [k for k in kids if k["birth_year"] is not None and year - k["birth_year"] <= 6]
    unknown_birth = [k["id"] for k in kids if k["birth_year"] is None]
    if gar and base > 0:
        ceil_c = 350000 * len(young)
        eligible = min(base, ceil_c)
        credit = pct_of_c(eligible, 50)
        out.append(_cand("fr-garde-enfants", "Childcare outside the home for children under 6 (credit d'impot)", "credit", base, gar,
                         rate="50 % of the expenses, net of aids (CMG...)",
                         ceilings=[f"3,500 EUR of expenses per child under 6 on 1 January: {len(young)} child(ren) found in household.yaml "
                                   f"-> {money_str(ceil_c)} EUR"],
                         estimated_benefit=_range(credit, credit),
                         documents_to_keep=["certificate of the childcare provider with the amounts paid", "contract"],
                         missing_info=([f"no child under 6 on 1 January {year} is known: check the birth years in household.yaml"]
                                       if not young else []) + (["birth year of: " + ", ".join(unknown_birth)] if unknown_birth else []),
                         notes=["a child born in " + str(year - 6) + " is eligible unless born on 1 January", _covered_note(ds, year, gar, cats)],
                         source="CGI art. 200 quater B", _high=credit, _low=credit))
    else:
        nodata.append({"id": "fr-garde-enfants", "how": "category kids.childcare"})
    # -- scolarite (flat)
    if kids:
        age = lambda k: None if k["birth_year"] is None else year - k["birth_year"]       # noqa: E731 - age in the autumn of `year`
        stages, total, unknown = [], 0, []
        for k in kids:
            a = age(k)
            if a is None:
                unknown.append(k["id"])
            elif 11 <= a <= 14:
                stages.append({"id": k["id"], "stage": "college", "amount": "61.00"}); total += 6100
            elif 15 <= a <= 17:
                stages.append({"id": k["id"], "stage": "lycee", "amount": "153.00"}); total += 15300
            elif 18 <= a <= 25:
                stages.append({"id": k["id"], "stage": "superieur", "amount": "183.00"}); total += 18300
        if stages:
            out.append({"id": "fr-scolarite", "heading": "Secondary and higher education (reduction d'impot, flat amount)", "mechanism": "reduction",
                        "spent": None, "n_tx": 0, "evidence": [], "rate": "flat per child: college 61, lycee 153, superieur 183 EUR",
                        "children": stages, "estimated_benefit": _range(total, total),
                        "ceilings": ["one flat amount per child attached to your tax household, whatever was spent"],
                        "documents_to_keep": ["school enrolment certificate"], "missing_info": (["birth year of: " + ", ".join(unknown)] if unknown else []),
                        "notes": ["the stage is guessed from the age (standard schooling); the reduction depends on the real level in September"],
                        "source": "CGI art. 199 quater F", "_high": total, "_low": total})
        else:
            nodata.append({"id": "fr-scolarite", "how": "needs the birth year of a child aged 11 to 25 in household.yaml"})
    else:
        nodata.append({"id": "fr-scolarite", "how": "needs children in household.yaml"})
    # -- pinel
    pin = [a for a in assets if getattr(a, "scheme", None) == "pinel" or getattr(a, "pinel_commitment_years", None)]
    for a in pin:
        yrs = a.pinel_commitment_years
        miss = [n for n, v in (("purchase_price", a.purchase_price), ("purchase_date", a.purchase_date), ("pinel_commitment_years", yrs)) if v is None]
        item = {"id": "fr-pinel", "heading": "Pinel reduction (reminder)", "mechanism": "reduction", "spent": None, "n_tx": 0, "evidence": [],
                "asset": a.id, "missing_info": miss, "documents_to_keep": ["deed of purchase", "rental agreements", "annual rent declaration"],
                "source": "CGI art. 199 novovicies", "notes": ["Pinel applied to purchases up to 31 December 2024; the reduction is spread over the commitment"],
                "estimated_benefit": _range(None, None), "_high": 0, "_low": 0}
        from coach.rental import reduction as RED
        red = strip_msgs(RED.compute(a, year))          # E15-4: the ONE shared function (rental page and tool use it too); *_msg: the web's only
        if red["status"] == "computed":
            item["declared_reduction"] = red
            alt = None
            if red["basis"] == "pinel_table" and a.purchase_date and a.purchase_date.year in (2023, 2024) and not (a.commitment and a.commitment.reduction_rate_pct):
                import copy
                classic = copy.copy(a)
                classic.purchase_date = a.purchase_date.replace(year=2022)         # Pinel+ kept the classic rates
                alt = RED.compute(classic, year)["candidate_c"]
            vals = [red["candidate_c"]] + ([alt] if alt is not None else [])
            if red["in_window"]:
                item["estimated_benefit"] = _range(min(vals), max(vals))
                item["_high"], item["_low"] = max(vals), min(vals)
            else:
                item["notes"].append(f"the commitment ({red['first_year']}-{red['last_year']}) does not include {year}")
            item["ceilings"] = ["price capped by the ceilings of the scheme (300,000 EUR, 5,500 EUR/m2 when the surface is recorded) and by the one you declared"]
            item["notes"] = [n for n in item["notes"] if "spread over the commitment" not in n] + red["notes"]
            if alt is not None:
                item["notes"].append("the lower figure uses the reduced rates of purchases in 2023-2024; Pinel+ kept the classic rates")
            item["missing_info"] = []
        else:
            item["missing_info"] = [("purchase_date" if m.startswith("commitment.reduction_first_year") else m) for m in red["missing"]]
        out.append(item)
    # -- rental income of the rental properties (E15-4)
    out += _rental_income(ds, year)
    # -- deductions from the taxable income
    for rid, key, title, how in (("fr-pension-alimentaire", "fr-pension-alimentaire", "Alimony paid (deduction from taxable income)", "pension-alimentaire"),
                                 ("fr-per", "fr-per", "PER contributions already made (deduction from taxable income)", "per")):
        tt = _by_tag(txs, TAGS[key])
        base = _spent(tt)
        if tt and base > 0:
            out.append(_cand(rid, title, "deduction", base, tt, rate="deducted from the taxable income: the saving is your marginal rate x the amount",
                             estimated_benefit=_range(None, None),
                             ceilings=["alimony to an adult child: yearly ceiling per child set by the tax administration"] if rid == "fr-pension-alimentaire"
                             else ["10 % of last year's professional income within a floor and a cap tied to the social security ceiling"],
                             documents_to_keep=["court decision or agreement", "proof of payment"] if rid == "fr-pension-alimentaire"
                             else ["annual statement of the provider"], missing_info=["marginal tax rate", "income (for the ceiling)"],
                             notes=["this lists contributions that exist; it is not a recommendation to make any"] if rid == "fr-per" else [],
                             source="CGI art. 156", _high=0, _low=0))
        else:
            nodata.append({"id": rid, "how": f"tag transactions with {how}"})
    return out, nodata


def _it(ds: Dataset, year: int, txs: list, members: list, liabilities: list, today: dt.date) -> tuple[list, list]:
    out, nodata = [], []
    kids = [m for m in members if m.role == "child"]
    cats = ("health.pharmacy", "health.doctors", "health.optician")
    med = _by_cat(txs, cats)
    base = _spent(med)
    if med and base > 0:
        ded = pct_of_c(max(0, base - FRANCHISE_IT_C), 19)
        out.append(_cand("it-spese-mediche", "Medical expenses (detrazione 19 %)", "detrazione", base, med,
                         rate="19 % of the part above the 129.11 EUR franchise", ceilings=["no general ceiling; franchise 129.11 EUR"],
                         estimated_benefit=_range(ded, ded),
                         documents_to_keep=["fatture / scontrino parlante with the tax code", "receipts of the pharmacy", "prescriptions where required"],
                         missing_info=["payments in cash are not deductible (except pharmacy and some devices): check each"],
                         notes=[_covered_note(ds, year, med, cats), IT_19_CAVEAT], source="TUIR art. 15 comma 1 lett. c", _high=ded, _low=ded))
    else:
        nodata.append({"id": "it-spese-mediche", "how": "categories health.pharmacy, health.doctors, health.optician"})
    mort = [m for m in liabilities if m.kind == "mortgage"]
    for lb in mort:
        st = L.loan_state(L.facts_of(lb), today)
        st.pop("_remaining_capital_c", None)
        am = st.get("amortization") or {}
        row = next((r for r in am.get("by_year", []) if r["year"] == year), None) if am.get("status") == "computed" else None
        item = {"id": "it-interessi-mutuo", "heading": "Mortgage interest on the main home (detrazione 19 %)", "mechanism": "detrazione", "spent": None,
                "n_tx": 0, "evidence": [], "loan": lb.id, "rate": "19 % of the interest paid, up to 4,000 EUR of interest (max 760 EUR)",
                "ceilings": ["interest ceiling 4,000 EUR per year"],
                "documents_to_keep": ["bank certificate of the interest paid (certificazione interessi passivi)", "mortgage deed", "deed of purchase"],
                "missing_info": ["the loan must be for the purchase of the main home (abitazione principale), taken within a year of the purchase"],
                "source": "TUIR art. 15 comma 1 lett. b", "notes": [], "estimated_benefit": _range(None, None), "_high": 0, "_low": 0}
        if row:
            interest = cents(row["interest"])
            ded = pct_of_c(min(interest, 400000), 19)
            item.update({"interest_year_computed": row["interest"], "estimated_benefit": _range(ded, ded), "_high": ded, "_low": ded})
            item["notes"] += ["interest computed from the amortization (principal, rate, term): the bank certificate is the figure to use", IT_19_CAVEAT]
        else:
            item["missing_info"] = [f"to compute the interest: {', '.join(st['missing_for_amortization'])}"] + item["missing_info"]
        out.append(item)
    if not mort:
        nodata.append({"id": "it-interessi-mutuo", "how": "needs a mortgage liability in memory/liabilities"})
    cats = ("housing.renovation",)
    ren = _by_cat(txs, cats) + _by_tag([t for t in txs if t.category not in cats], TAGS["it-ristrutturazioni"])
    base = _spent(ren)
    if ren and base > 0:
        info = it_renovation_rates(year)
        eligible = min(base, info["ceiling_eur"] * 100)
        lo, hi = pct_of_c(eligible, info["low_rate"]), pct_of_c(eligible, info["high_rate"])
        out.append(_cand("it-ristrutturazioni", "Renovation works (detrazione recupero edilizio)", "detrazione", base, ren,
                         rate=f"{info['low_rate']} % to {info['high_rate']} % for {year} ({info['note']}), recovered in 10 yearly instalments",
                         ceilings=[f"{info['ceiling_eur']:,} EUR of expenses per property unit"], estimated_benefit=_range(lo, hi),
                         rates_info=info,
                         per_year_over_10_years=_range(lo // 10, hi // 10),
                         documents_to_keep=["bonifico parlante", "invoices", "building permit or CILA", "ENEA communication for energy works"],
                         missing_info=["main home or not", "which works qualify"], notes=[_covered_note(ds, year, ren, cats)],
                         source="TUIR art. 16-bis", _high=hi, _low=lo))
    else:
        nodata.append({"id": "it-ristrutturazioni", "how": "category housing.renovation or tag ristrutturazione"})
    cats = ("kids.school", "education.courses")
    sch = _by_cat(txs, cats)
    base = _spent(sch)
    if sch and base > 0 and kids:
        ceil_c = 80000 * len(kids)
        ded = pct_of_c(min(base, ceil_c), 19)
        out.append(_cand("it-istruzione", "School expenses (detrazione 19 %)", "detrazione", base, sch, rate="19 %",
                         ceilings=[f"800 EUR per student: {len(kids)} child(ren) in household.yaml -> {money_str(ceil_c)} EUR"],
                         estimated_benefit=_range(ded, ded), organisations=_top_entities(sch, 4),
                         documents_to_keep=["receipts or invoices with the student's tax code"],
                         missing_info=["canteen and school trips count; private tutoring does not: check each line"],
                         notes=[_covered_note(ds, year, sch, cats), IT_19_CAVEAT], source="TUIR art. 15 comma 1 lett. e", _high=ded, _low=ded))
    else:
        nodata.append({"id": "it-istruzione", "how": "categories kids.school, education.courses and children in household.yaml"})
    ins = _by_cat(txs, ("insurance.life",)) + _by_tag(txs, TAGS["it-assicurazioni"])
    base = _spent(list({t.key: t for t in ins}.values()))
    if ins and base > 0:
        high = pct_of_c(min(base, 53000), 19)
        out.append(_cand("it-assicurazioni", "Life and accident insurance premiums (detrazione 19 %)", "detrazione", base, ins,
                         rate="19 % of the premiums up to 530 EUR (1,291.14 EUR for non-self-sufficiency cover)",
                         ceilings=["530 EUR per year for life / accident; 1,291.14 EUR for non-self-sufficiency"],
                         estimated_benefit=_range(0, high),
                         documents_to_keep=["insurer's annual certificate of the deductible part"],
                         missing_info=["only the risk part of a policy qualifies: a savings policy may be excluded (low bound 0)"],
                         notes=[IT_19_CAVEAT], source="TUIR art. 15 comma 1 lett. f", _high=high, _low=0))
    else:
        nodata.append({"id": "it-assicurazioni", "how": "category insurance.life or tag assicurazione-vita"})
    cats = ("charity.donations",)
    don = _by_cat(txs, cats)
    base = _spent(don)
    if don and base > 0:
        e = min(base, 3000000)
        out.append(_cand("it-erogazioni-liberali", "Donations to qualified non-profit bodies (detrazione)", "detrazione", base, don,
                         rate="26 % to 35 % depending on the type of body (verify)", ceilings=["30,000 EUR of donations per year"],
                         estimated_benefit=_range(pct_of_c(e, 26), pct_of_c(e, 35)), organisations=_top_entities(don),
                         documents_to_keep=["receipt of the body", "traceable payment (bank, card)"],
                         missing_info=["type of body (ETS, ODV, APS...)"], notes=[_covered_note(ds, year, don, cats)],
                         source="D.Lgs. 117/2017 art. 83; TUIR art. 15", _high=pct_of_c(e, 35), _low=pct_of_c(e, 26)))
    else:
        nodata.append({"id": "it-erogazioni-liberali", "how": "category charity.donations"})
    return out, nodata


def tax_candidates(ds: Dataset, year: Optional[int] = None, country: Optional[str] = None, *, household_country: Optional[str] = None) -> dict:
    today = ds.today
    if year is None:
        year = today.year if today.month >= 10 else today.year - 1
    if not 2000 <= year <= today.year:
        raise ValueError("year must be between 2000 and the current year")
    country = (country or household_country or "FR").upper()
    if country not in ("FR", "IT"):
        raise ValueError("country must be FR or IT")
    txs = _tx_in_year(ds, year)
    members = ds.memory.members
    if country == "FR":
        cands, nodata = _fr(ds, year, txs, members, ds.memory.assets)
    else:
        cands, nodata = _it(ds, year, txs, members, [m for _r, m in ds.memory.liabilities], today)
    cands.sort(key=lambda c: (-c.get("_high", 0), c["id"]))
    for c in cands:
        c["last_reviewed"] = LAST_REVIEWED
        c["notes"] = [n for n in c.get("notes", []) if n]
        c.pop("_high", None), c.pop("_low", None)
    return {"year": year, "country": country, "income_year_note": f"{year} is the income year: it is declared in the spring of {year + 1}",
            "year_in_progress": year == today.year, "candidates": cands, "no_data_for": nodata,
            "tags_used": {k: list(v) for k, v in TAGS.items() if k.startswith(country.lower())},
            "disclaimer": DISCLAIMER[country],
            "household": {"children": len([m for m in members if m.role == "child"]), "adults": len([m for m in members if m.role == "adult"])}}
