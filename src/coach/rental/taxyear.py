"""Tax-year figures of a rental property for the rental-income return, France (E15-4): CANDIDATES, never a return, never advice.

For an income year the module computes, from the property's closed months and the loan schedule, the two ways the rents can be taxed as the
return knows them:

    micro-foncier   gross rents of the household's rental properties (the regime applies when their total stays under a ceiling) less a flat
                    abatement: the taxable figure is what is left, no cost is deducted
    reel            gross rents less the costs the return lets you deduct: management fees, insurance (PNO, GLI), property tax, the
                    NON-recoverable part of the co-ownership charges, repairs / maintenance / improvement works, the loan INTEREST and the
                    borrower insurance (never the principal repaid)

and, for a property under a scheme, the scheme's reduction as a candidate from the price and the rate the owner declared (``purchase_price``,
``commitment.reduction_rate_pct``, ``commitment.years``): the total reduction spread evenly over the commitment years.

Honest limits, stated in every result: the amounts come from the bank flows classified by category (a mis-labelled payment moves them), the
co-ownership charges and the works are UPPER BOUNDS (only part may be deductible: the statement of charges and the invoices decide), the interest
comes from the theoretical amortization (the lender's annual statement is the figure to report) and is listed as unknown, not estimated, when the
loan is not known. The ceiling and the abatement are those known when this module was written (``LAST_REVIEWED``): verify them.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach import disclaimers as D
from coach.analytics.common import money_str, mul_cents
from coach.i18n_msg import server_msg
from coach.rental import cashflow as CF, model as M, reduction as RED, scheme as SC

MICRO_CEILING_C = 1_500_000            # EUR 15,000 of gross rents (all the household's rental properties together)
MICRO_ABATEMENT_PCT = 30
LAST_REVIEWED = "2026-10"
FLAT_MANAGEMENT_C = 2000              # EUR 20 per property: the flat management allowance of the reel regime (a candidate line)
DEFICIT_CEILING_C = 1_070_000         # EUR 10,700 a year of land deficit imputable on the global income (the interest part and the rest are carried forward)
DISCLAIMER = D.TAX_BY_COUNTRY["FR"]

CHECKLIST = [
    ("manager-statement", "Annual statement of the property manager (rents collected, fees withheld, charges paid)", "your property manager"),
    ("leases", "The lease(s) of the year, rent revisions and the tenant's rent receipts", "your files"),
    ("property-tax-notice", "Property tax notice (taxe fonciere), to separate the household-waste part the tenant reimburses", "impots.gouv.fr, your tax space"),
    ("charges-statement", "Co-ownership statement of charges with the recoverable / non-recoverable split", "your syndic"),
    ("insurance", "Insurance certificates and premium receipts (PNO, unpaid-rent cover)", "your insurers"),
    ("works-invoices", "Invoices of repairs, maintenance and improvement works (not building or enlargement)", "your contractors"),
    ("loan-statement", "The lender's annual statement of interest and borrower insurance paid", "your lender"),
    ("bank-statements", "Bank statements of the property account for the year", "your bank, or this app (coverage)"),
]
SCHEME_CHECKLIST = [
    ("deed", "Deed of purchase and the scheme's commitment form", "your notary"),
    ("scheme-declaration", "The scheme's own annual declaration or certificate, if the scheme asks for one", "the scheme's rules"),
    ("tenant-evidence", "The tenant's income evidence at the signature of each lease (against the income limit)", "the tenant / your manager"),
]


def _line(item: dict, amount_c: int, source: dict, bound: dict) -> dict:
    """A deductible line from the web's messages: the English ``item``, ``source`` and ``bound`` (what the CLI and the tool read) are
    their ``text``."""
    return {"item": item["text"], "amount_c": amount_c, "source": source["text"], "bound": bound["text"],
            "item_msg": item, "source_msg": source, "bound_msg": bound}


def _flows(categories: str) -> dict:
    return server_msg("rental.tax.source.bankFlows", f"bank flows ({categories})", categories=categories)


AS_PAID = server_msg("rental.tax.bound.asPaid", "as paid")
SCHEDULE = server_msg("rental.tax.source.schedule", "amortization schedule of the loan file")


def _bank_amount(row_totals: dict, key: str) -> int:
    return row_totals.get(key, 0)


def scheme_reduction(prop, year: int, today: Optional[dt.date] = None) -> dict:
    """The scheme reduction of `year`: the one shared function of :mod:`coach.rental.reduction` (also used by ``tax_candidates``)."""
    return RED.compute(prop.asset, year)


def tax_year(ds, prop, year: int, country: str = "FR") -> dict:
    if country != "FR":
        note = "only the French rental-income return is modelled (micro-foncier / reel); the Italian one is not"
        return {"year": year, "country": country, "status": "not_modelled", "note": note, "disclaimer": DISCLAIMER,
                "note_msg": server_msg("rental.tax.notModelled", note)}
    pnl = CF.year_pnl(ds, prop, year)
    t = pnl["totals"]
    gross = t["rent_c"]
    household_gross = sum(CF.year_pnl(ds, p, year)["totals"]["rent_c"] for p in M.properties(ds))
    abate = mul_cents(gross, MICRO_ABATEMENT_PCT / 100)
    micro = {"gross_rents_c": gross, "household_gross_rents_c": household_gross, "ceiling_c": MICRO_CEILING_C,
             "within_ceiling": household_gross <= MICRO_CEILING_C, "abatement_pct": MICRO_ABATEMENT_PCT, "abatement_c": abate,
             "taxable_c": gross - abate}
    split = pnl["loan_split"]
    items = [
        _line(server_msg("rental.tax.item.fees", "management fees"), t["fees_c"], _flows("housing.property_management"), AS_PAID),
        _line(server_msg("rental.tax.item.insurance", "insurance (PNO, unpaid-rent cover)"), t["insurance_c"], _flows("housing.property_insurance"), AS_PAID),
        _line(server_msg("rental.tax.item.propertyTax", "property tax"), t["taxes_c"], _flows("housing.property_tax"),
              server_msg("rental.tax.bound.propertyTax", "upper bound: the household-waste part reimbursed by the tenant is not deductible")),
        _line(server_msg("rental.tax.item.charges", "co-ownership charges"), t["charges_c"], _flows("housing.property_charges"),
              server_msg("rental.tax.bound.charges", "upper bound: only the non-recoverable part counts (the syndic's statement splits it)")),
        _line(server_msg("rental.tax.item.works", "repairs, maintenance and improvement works"), t["works_c"], _flows("housing.renovation, housing.maintenance_diy"),
              server_msg("rental.tax.bound.works", "upper bound: building or enlargement works are not deductible, check each invoice")),
    ]
    items.append(_line(server_msg("rental.tax.item.flatAllowance", "flat management allowance (per property)"), FLAT_MANAGEMENT_C,
                       server_msg("rental.tax.source.flatAmount", f"flat amount known in {LAST_REVIEWED}", reviewed_month=LAST_REVIEWED),
                       server_msg("rental.tax.bound.flatAllowance", "a candidate line: a flat EUR 20 per property in the reel regime, verify that it applies to you",
                             flat_amount=money_str(FLAT_MANAGEMENT_C))))
    unknown, unknown_msg = [], []
    if split:
        interest_bound = (server_msg("rental.tax.bound.interestPartial", "theoretical table: report the lender's annual statement (partial year: the table starts mid-year)")
                          if split["partial"] else server_msg("rental.tax.bound.interest", "theoretical table: report the lender's annual statement"))
        items.append(_line(server_msg("rental.tax.item.interest", "loan interest"), split["interest_c"], SCHEDULE, interest_bound))
        items.append(_line(server_msg("rental.tax.item.borrowerInsurance", "borrower insurance"), split["insurance_c"], SCHEDULE,
                           server_msg("rental.tax.bound.borrowerInsurance", "theoretical table: the lender's statement decides")))
    else:
        u = ("loan interest and borrower insurance: the loan is not known or its schedule cannot be computed for this year "
             "(link the loan, see `coach rental scheme`); the lender's annual statement gives them")
        unknown.append(u)
        unknown_msg.append(server_msg("rental.tax.unknownInterest", u))
    total_ded = sum(i["amount_c"] for i in items)
    reel = {"gross_rents_c": gross, "deductible": items, "total_deductible_c": total_ded, "net_c": gross - total_ded, "unknown": unknown,
            "not_deductible": ["the loan principal repaid", "the owner's own transfers into the account"],
            "unknown_msg": unknown_msg, "not_deductible_msg": [server_msg("rental.tax.notDeductible.principal", "the loan principal repaid"),
                                                               server_msg("rental.tax.notDeductible.ownTransfers", "the owner's own transfers into the account")]}
    if reel["unknown"]:
        reel["net_is_upper_bound"] = True
    if reel["net_c"] < 0:                       # a land deficit: the part due to the interest only reduces later rental income
        interest_c = (split["interest_c"] + split["insurance_c"]) if split else 0
        other_c = max(0, (total_ded - interest_c) - gross)
        imputable = min(other_c, DEFICIT_CEILING_C)
        dn = (f"a candidate computed with the rules known in {LAST_REVIEWED} (EUR 10,700 a year on the global income, the part due to the "
              "loan interest and the excess carried forward on later rental income, for ten years): verify the conditions "
              "(a ceiling and a holding period apply) with the tax office or an adviser")
        reel["deficit_foncier"] = {"deficit_c": -reel["net_c"], "interest_part_c": min(-reel["net_c"], interest_c), "ceiling_c": DEFICIT_CEILING_C,
                                   "imputable_on_global_income_c": imputable, "carried_forward_c": -reel["net_c"] - imputable, "note": dn,
                                   "note_msg": server_msg("rental.tax.deficit", dn, reviewed_month=LAST_REVIEWED, ceiling_amount=money_str(DEFICIT_CEILING_C))}
    iy = f"{year} is the income year: declared in the spring of {year + 1}"
    n_flows = "candidates computed from the bank flows of the property account: a payment in the wrong category moves them"
    n_net = "if the manager pays the rent net of its fees, the gross rent is higher than the rent received: the manager's annual statement gives it"
    n_rules = ("the ceiling and the abatement are those known at the date of this module (" + LAST_REVIEWED + "): verify them, and the "
               "consequences of choosing the reel regime (it binds you for several years), with the tax office or an adviser")
    out = {"year": year, "country": "FR", "status": "computed", "property_kind": "rental property",
           "income_year_note": iy, "income_year_note_msg": server_msg("rental.tax.incomeYear", iy, year=year, filing_year=year + 1),
           "gross_rents_c": gross, "months_counted": pnl["n_months"], "months_incomplete": pnl["months_incomplete"],
           "months_missing_data": pnl["months_missing_data"], "complete": pnl["complete"], "micro_foncier": micro, "reel": reel,
           "difference_c": reel["net_c"] - micro["taxable_c"],
           "lower_taxable_candidate": "reel" if reel["net_c"] < micro["taxable_c"] else "micro_foncier",
           "scheme_reduction": scheme_reduction(prop, year, ds.today) if SC.declared(prop) else {"status": "no_scheme"},
           "documents": [{"id": i, "item": t_, "from": w} for i, t_, w in CHECKLIST + (SCHEME_CHECKLIST if SC.declared(prop) else [])],
           "notes": [n_flows, n_net, n_rules],
           "disclaimer": DISCLAIMER,
           "notes_msg": [server_msg("rental.tax.note.flows", n_flows), server_msg("rental.tax.note.netRent", n_net),
                         server_msg("rental.tax.note.rules", n_rules, reviewed_month=LAST_REVIEWED)]}
    if pnl["year_in_progress"]:
        n = f"{year} is not over: only its closed months are counted"
        out["notes"].insert(0, n)
        out["notes_msg"].insert(0, server_msg("rental.tax.note.yearInProgress", n, year=year))
    if not micro["within_ceiling"]:
        n = "the household's gross rents are above the micro-foncier ceiling: that regime would not apply"
        out["notes"].append(n)
        out["notes_msg"].append(server_msg("rental.tax.note.overCeiling", n))
    return out
