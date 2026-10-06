"""E15-4: the figures of the rental-income return (micro-foncier vs reel), the loan interest from the E9 schedule, the scheme reduction from the
owner's own price and rate, the documents checklist, and the integration with `tax_candidates`. Hand-built dataset (no database): 2025 is a
complete income year of 12 rents of 600 EUR."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from anhelpers import D, account, make_ds, monthly, tx
from coach import disclaimers
from coach.analytics.dataset import MemorySnapshot
from coach.memory import schemas as S
from coach.rental import model as M, taxyear as TX
from coach.rental.render import plain
from coach.skills import tax as SKT

TODAY = D("2026-02-10")


def year_2025(prop_id="flat-1", acct="rent", rent=600.0):
    t = monthly("2025-01-03", 12, rent, "income.rental", account=acct, entity="MANAGER")
    t += monthly("2025-01-05", 12, -30.0, "housing.property_management", account=acct, entity="MANAGER FEE")
    t += monthly("2025-01-10", 4, -30.0, "housing.property_insurance", account=acct, entity="PNO")      # 4 premiums of 30
    t += monthly("2025-01-12", 12, -40.0, "housing.property_charges", account=acct, entity="SYNDIC")
    t += monthly("2025-01-01", 12, -555.0, "housing.rental_property_loan", account=acct, entity="LENDER")
    t += [tx("2025-10-15", -700.0, "housing.property_tax", account=acct, entity="TF"), tx("2025-05-10", -300.0, "housing.renovation", account=acct, entity="WORKS"),
          tx("2025-04-02", 250.0, "transfer.internal", account=acct)]
    return t


def asset(**kw):
    base = dict(id="flat-1", kind="real_estate_rental", account="rent", scheme="pinel", rent_monthly=600, value=180000, as_of="2026-01-15",
                purchase_price=170000, purchase_date="2021-02-01", loan="loan-1",
                commitment={"start_date": "2021-03-05", "years": 9, "reduction_rate_pct": 18, "reduction_first_year": 2021, "reduction_base_cap": 300000})
    base.update(kw)
    return S.Asset(**base)


LOAN = S.Liability(id="loan-1", kind="mortgage", asset="flat-1", start_date="2024-01-01", end_date="2044-01-01", term_months=240, principal=100000,
                   rate={"type": "fixed", "nominal": 3.0}, debited_account="rent")


def world(assets=None, txs=None, loans=(("liabilities/loan-1.yaml", LOAN),), extra_accounts=()):
    accounts = [account("rent", "Rental", purpose="rental"), *extra_accounts]
    return make_ds(txs if txs is not None else year_2025(), accounts, today=TODAY,
                   memory=MemorySnapshot(assets=assets or [asset()], liabilities=list(loans), country="FR"),
                   history={"rent": ("2024-06-01", "2026-02-09")}, last_sync={"rent": "2026-02-09"})


def independent_interest_2025() -> int:
    """The interest of the instalments due in 2025 (k = 13 .. 24: the first instalment is one month after the start, so Feb 2024 is k=1 ... Jan 2025 is k=12;
    2025 holds k = 12 .. 23), computed here with a plain Decimal loop, not with the schedule code."""
    r = Decimal(3) / 1200
    n = 240
    pay = (Decimal(100000) * r / (1 - (1 + r) ** -n)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    bal, total = Decimal(100000), Decimal(0)
    for k in range(1, 25):
        interest = (bal * r).quantize(Decimal("0.01"), ROUND_HALF_UP)
        bal -= pay - interest
        if 12 <= k <= 23:
            total += interest
    return int(total * 100)


def test_gross_rents_micro_foncier_and_the_reel_candidates_for_a_complete_year():
    ds = world()
    (p,) = M.properties(ds)
    t = TX.tax_year(ds, p, 2025)
    assert t["status"] == "computed" and t["complete"] and t["months_counted"] == 12 and t["months_incomplete"] == [] and t["months_missing_data"] == []
    assert t["gross_rents_c"] == 720000
    mi = t["micro_foncier"]
    assert (mi["abatement_pct"], mi["abatement_c"], mi["taxable_c"], mi["within_ceiling"]) == (30, 216000, 504000, True)
    rl = t["reel"]
    items = {i["item"]: i["amount_c"] for i in rl["deductible"]}
    interest = independent_interest_2025()
    assert items["management fees"] == 36000 and items["insurance (PNO, unpaid-rent cover)"] == 12000 and items["property tax"] == 70000
    assert items["co-ownership charges"] == 48000 and items["repairs, maintenance and improvement works"] == 30000
    assert items["loan interest"] == interest and items["borrower insurance"] == 0
    assert items["flat management allowance (per property)"] == 2000                      # a dated candidate line of EUR 20
    assert rl["total_deductible_c"] == 198000 + interest and rl["net_c"] == 720000 - 198000 - interest and rl["unknown"] == []
    assert t["difference_c"] == rl["net_c"] - mi["taxable_c"] == 18000 - interest        # 5,220 - interest against 5,040
    assert "deficit_foncier" not in rl
    assert t["lower_taxable_candidate"] == "reel"                                        # the interest alone is above the 200 EUR that separates them
    assert "principal" in " ".join(rl["not_deductible"]) and "transfers" in " ".join(rl["not_deductible"])               # the owner's own money is no cost


def test_the_loan_principal_and_the_owners_transfers_are_never_deducted():
    ds = world()
    t = plain(TX.tax_year(ds, M.properties(ds)[0], 2025))
    total = sum(float(i["amount"]) for i in t["reel"]["deductible"])
    assert float(t["reel"]["total_deductible"]) == pytest.approx(total)
    assert float(t["reel"]["total_deductible"]) < 1960 + 3200                           # nowhere near the 6,660 EUR of instalments paid


def test_without_a_known_loan_the_interest_is_listed_as_unknown_not_estimated():
    ds = world(loans=())
    t = TX.tax_year(ds, M.properties(ds)[0], 2025)
    assert all(i["item"] not in ("loan interest", "borrower insurance") for i in t["reel"]["deductible"])
    assert t["reel"]["unknown"] and "loan interest" in t["reel"]["unknown"][0] and t["reel"]["net_is_upper_bound"]
    assert t["reel"]["total_deductible_c"] == 198000


def test_a_year_in_progress_or_with_gaps_says_so():
    ds = world()
    t = TX.tax_year(ds, M.properties(ds)[0], 2026)
    assert t["gross_rents_c"] == 0 and t["months_counted"] <= 1 and not t["complete"]
    assert "2026 is not over: only its closed months are counted" in t["notes"][0]
    partial = [x for x in year_2025() if x.date.month != 6]
    ds2 = world(txs=partial)
    t2 = TX.tax_year(ds2, M.properties(ds2)[0], 2025)
    assert t2["gross_rents_c"] == 660000 and t2["months_counted"] == 12             # the month exists in the data but holds no rent: it is not hidden


def test_the_household_ceiling_of_the_micro_foncier_counts_every_property():
    second = asset(id="flat-2", account="rent2", loan=None, rent_monthly=700)
    ds = world(assets=[asset(), second], txs=year_2025() + year_2025("flat-2", "rent2", 700.0), extra_accounts=[account("rent2", "Rental 2", purpose="rental")])
    t = TX.tax_year(ds, M.properties(ds)[0], 2025)
    assert t["micro_foncier"]["household_gross_rents_c"] == 720000 + 840000 and t["micro_foncier"]["within_ceiling"] is False
    assert any("above the micro-foncier ceiling" in n for n in t["notes"])


def test_the_scheme_reduction_is_the_declared_rate_on_the_declared_price_spread_over_the_commitment():
    ds = world()
    (p,) = M.properties(ds)
    r = TX.scheme_reduction(p, 2025)
    assert r["status"] == "computed" and r["base_c"] == 17000000 and r["total_c"] == 3060000 and r["annual_c"] == 340000 and r["candidate_c"] == 340000
    assert (r["first_year"], r["last_year"], r["in_window"], r["years_elapsed_including_this"]) == (2021, 2029, True, 5)
    out = TX.scheme_reduction(p, 2030)
    assert out["candidate_c"] == 0 and out["in_window"] is False
    assert TX.scheme_reduction(p, 2020)["in_window"] is False


def test_the_eligible_price_is_capped_by_the_ceiling_the_owner_declared():
    ds = world(assets=[asset(purchase_price=350000)])
    r = TX.scheme_reduction(M.properties(ds)[0], 2025)
    assert r["base_c"] == 30000000 and r["base_capped"] is True and r["total_c"] == 5400000 and r["annual_c"] == 600000


def test_an_extension_with_its_own_rate_adds_years_after_the_commitment():
    ext = {"start_date": "2021-03-05", "years": 9, "reduction_rate_pct": 18, "reduction_first_year": 2021,
           "extension": {"decision": "extend", "years": 3, "additional_rate_pct": 6}}
    ds = world(assets=[asset(commitment=ext)])
    p = M.properties(ds)[0]
    r = TX.scheme_reduction(p, 2031)
    assert r["candidate_c"] == 340000 and r.get("via_extension") is True               # 6 % of 170,000 = 10,200 over 3 years = 3,400 and r["extension"]["last_year"] == 2032
    assert TX.scheme_reduction(p, 2033)["candidate_c"] == 0


def test_missing_price_or_rate_is_listed_never_guessed():
    ds = world(assets=[asset(scheme="my-scheme", purchase_price=None, commitment={"start_date": "2021-03-05", "years": 9})])
    r = TX.scheme_reduction(M.properties(ds)[0], 2025)
    assert r["status"] == "needs_fields" and r["missing"] == ["purchase_price"]
    r2 = TX.scheme_reduction(M.properties(world(assets=[asset(scheme="my-scheme", commitment={"start_date": "2021-03-05", "years": 9})]))[0], 2025)
    assert r2["status"] == "needs_fields" and "reduction_rate_pct" in r2["missing"][0]                 # not a Pinel: no table, so the rate is asked for
    t = TX.tax_year(ds, M.properties(ds)[0], 2025)
    assert t["scheme_reduction"]["status"] == "needs_fields"


def test_no_scheme_means_no_reduction_and_no_scheme_documents():
    plain_asset = asset(scheme=None, commitment=None)
    ds = world(assets=[plain_asset])
    t = TX.tax_year(ds, M.properties(ds)[0], 2025)
    assert t["scheme_reduction"] == {"status": "no_scheme"}
    ids = {d["id"] for d in t["documents"]}
    assert {"manager-statement", "property-tax-notice", "loan-statement", "charges-statement"} <= ids and "deed" not in ids
    with_scheme = {d["id"] for d in TX.tax_year(world(), M.properties(world())[0], 2025)["documents"]}
    assert {"deed", "scheme-declaration", "tenant-evidence"} <= with_scheme


def test_the_disclaimer_is_the_shared_wording_and_the_italian_return_is_not_modelled():
    ds = world()
    p = M.properties(ds)[0]
    assert TX.tax_year(ds, p, 2025)["disclaimer"] == disclaimers.TAX_BY_COUNTRY["FR"] and "Nothing is filed" in TX.DISCLAIMER
    it = TX.tax_year(ds, p, 2025, "IT")
    assert it["status"] == "not_modelled" and "Italian" in it["note"]


def test_tax_candidates_lists_one_rental_income_candidate_per_property_and_uses_the_declared_reduction():
    ds = world()
    res = SKT.tax_candidates(ds, 2025, "FR")
    cands = {c["id"]: c for c in res["candidates"]}
    rf = cands["fr-revenus-fonciers"]
    assert rf["gross_rents"] == "7200.00" and rf["micro_foncier"]["taxable"] == "5040.00" and rf["lower_taxable_candidate"] == "reel"
    assert rf["asset"] == "flat-1" and rf["mechanism"] == "declaration" and len(rf["reel"]["deductible"]) == 8 and rf["estimated_benefit"] == {"low": None, "high": None}
    pin = cands["fr-pinel"]
    assert pin["estimated_benefit"] == {"low": "3400.00", "high": "3400.00"} and pin["declared_reduction"]["rate_pct"] == 18
    assert "nothing is looked up" in " ".join(pin["notes"]) and pin["missing_info"] == []
    assert res["disclaimer"] == disclaimers.TAX_BY_COUNTRY["FR"]


def test_tax_candidates_keeps_the_table_estimate_when_no_rate_is_declared():
    ds = world(assets=[asset(commitment={"start_date": "2021-03-05", "years": 9}, pinel_commitment_years=9)])
    pin = {c["id"]: c for c in SKT.tax_candidates(ds, 2025, "FR")["candidates"]}["fr-pinel"]
    assert pin["declared_reduction"]["basis"] == "pinel_table" and pin["estimated_benefit"]["low"] is not None            # the dated Pinel table answers


def test_a_land_deficit_is_a_candidate_with_the_dated_ceiling_and_the_interest_part_carried_forward():
    # 2,400 of rents; 1,960 of costs without the loan + 20 flat = 1,980 + interest (about 2,900): a deficit of about 2,480, all due to the interest
    ds = world(txs=[x for x in year_2025() if x.category != "income.rental"] + [tx(f"2025-{m:02d}-03", 200.0, "income.rental", account="rent") for m in range(1, 13)])
    rl = TX.tax_year(ds, M.properties(ds)[0], 2025)["reel"]
    d = rl["deficit_foncier"]
    interest = independent_interest_2025()
    assert rl["net_c"] == 240000 - 198000 - interest < 0 and d["deficit_c"] == -rl["net_c"] and d["ceiling_c"] == 1070000
    assert d["interest_part_c"] == min(d["deficit_c"], interest) and "verify" in d["note"] and "2026-10" in d["note"]
    non_interest = 198000                                   # the costs other than the interest: 1,980 against 2,400 of rents -> none of the deficit comes from them
    assert d["imputable_on_global_income_c"] == max(0, non_interest - 240000) == 0 and d["carried_forward_c"] == d["deficit_c"]


def test_a_deficit_from_charges_is_imputable_on_the_global_income_up_to_the_ceiling():
    ds = world(loans=(), txs=[x for x in year_2025() if x.category not in ("income.rental",)] + [tx("2025-02-03", 100.0, "income.rental", account="rent")] +
               [tx("2025-06-10", -15000.0, "housing.renovation", account="rent")])
    d = TX.tax_year(ds, M.properties(ds)[0], 2025)["reel"]["deficit_foncier"]
    # costs 1,960 + 20 + 15,000 (works counted once more than the 300 listed: 15,300) = 17,280 against 100 of rent
    assert d["imputable_on_global_income_c"] == 1070000 and d["carried_forward_c"] == d["deficit_c"] - 1070000 > 0
