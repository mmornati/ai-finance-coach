"""E15-1 / E15-2: the property account, its categories (from the shipped rules), the monthly cash flow, the yearly P&L, the effort d'epargne and
the vacancy. Synthetic property (``rentalhelpers``); the figures are hand-computed from the amounts listed there."""
from __future__ import annotations


import pytest

from coach.analytics import api as analytics_api
from coach.rental import cashflow as CF, model as M
from coach.rental.render import plain
from rentalhelpers import rental_world
from memhelpers import TODAY


@pytest.fixture
def ds(cfg):
    con = rental_world(cfg)
    d = analytics_api.build_dataset(con, cfg, TODAY)
    yield d
    con.close()


def test_the_shipped_rules_label_every_flow_of_the_property_account(ds):
    cats = {t.key: t.category for t in ds.txs if t.account == "rn"}
    assert cats["rent0"] == "income.rental"                          # "LOYER" coming IN
    assert cats["fee0"] == "housing.property_management"             # gestion locative
    assert cats["loan1"] == "housing.rental_property_loan"          # the loan instalment of an account whose purpose is rental
    assert cats["copro1"] == "housing.property_charges"             # syndic
    assert cats["pno1"] == "housing.property_insurance"             # PNO
    assert cats["tf1"] == "housing.property_tax"                    # taxe fonciere wins over the public-finance rule
    assert cats["works1"] == "housing.renovation"
    assert cats["topup1"] == "transfer.internal"                    # the owner's own money is not rent


def test_the_property_is_linked_to_its_account_and_loan(ds):
    (p,) = M.properties(ds)
    assert p.id == "rental-flat-1" and p.accounts == ["rn"] and p.account_link == "declared"
    assert [lb.id for lb in p.loans] == ["rental-loan"] and p.loan_link == "declared"
    assert M.unlinked_rental_accounts(ds) == []


def test_monthly_cash_flow_figures_and_statuses(ds):
    (p,) = M.properties(ds)
    rows = {r.month: r for r in CF.all_rows(ds, p)[0]}
    assert sorted(rows) == [f"2026-{m:02d}" for m in range(1, 10)]
    jan, mar, jul = rows["2026-01"], rows["2026-03"], rows["2026-07"]
    assert (jan.rent_c, jan.loan_c, jan.charges_c, jan.insurance_c, jan.fees_c, jan.other_c) == (62000, 90000, 3500, 2500, 3100, 225)
    assert jan.net_c == -37325 and jan.effort_c == 37325 and jan.owner_in_c == 40000 and jan.complete
    assert mar.taxes_c == 80000 and mar.net_c == -114825 and rows["2026-04"].works_c == 45000 and rows["2026-04"].net_c == -82325
    assert jul.rent_c == 124000 and jul.fees_c == 6200 and jul.net_c == 21575 and jul.effort_c == 0            # two rents: the one of June came late
    assert {m: r.rent_status for m, r in rows.items()} == {
        "2026-01": "received", "2026-02": "declared_vacancy", "2026-03": "received", "2026-04": "received", "2026-05": "missing",
        "2026-06": "late_paid", "2026-07": "received", "2026-08": "received", "2026-09": "received"}


def test_yearly_pnl_effort_and_vacancy(ds):
    (p,) = M.properties(ds)
    y = CF.year_pnl(ds, p, 2026)
    t = y["totals"]
    assert (t["rent_c"], t["loan_c"], t["charges_c"], t["fees_c"], t["taxes_c"], t["insurance_c"], t["works_c"], t["other_c"]) == (
        434000, 810000, 31500, 21700, 80000, 7500, 45000, 2025)
    assert t["costs_c"] == 997725 and t["net_c"] == -563725 and t["effort_c"] == 585300 and t["owner_in_c"] == 360000
    assert y["n_months"] == 9 and y["year_in_progress"] and not y["complete"] and y["monthly_average_effort_c"] == 65033
    v = y["vacancy"]
    assert v["missing_months"] == ["2026-05"] and v["declared_months"] == ["2026-02"] and v["late_paid_months"] == ["2026-06"] and v["n_missing"] == 1
    # the loan of the year from the amortization schedule: the principal is not a cost of the economic result
    split = y["loan_split"]
    assert split["interest_c"] > 0 and split["principal_c"] > 0 and split["source"] == "amortization schedule"
    assert y["economic"]["result_c"] == t["rent_c"] - (t["costs_c"] - t["loan_c"]) - split["interest_c"] - split["insurance_c"]
    assert "gross_yield_pct" not in y                                       # only a complete year has a yield
    out = plain(y)
    assert out["totals"]["effort"] == "5853.00" and out["totals"]["net"] == "-5637.25" and out["monthly_average_effort"] == "650.33"


def test_the_average_uses_complete_months_only_and_the_summary_is_json_safe(ds):
    (p,) = M.properties(ds)
    cm = CF.monthly(ds, p, 12)
    assert cm["n_months"] == 9 and cm["n_complete"] == 9 and cm["rent"]["expected_c"] == 62000 and cm["rent"]["source"] == "declared"
    assert cm["average_c"]["rent_c"] == div(434000, 9) and cm["vacancy"]["occupancy_rate"] == round(1 - 2 / 9, 4)
    last3 = CF.monthly(ds, p, 3)
    assert [r.month for r in last3["months"]] == ["2026-07", "2026-08", "2026-09"]
    import json
    json.dumps(plain(cm))


def div(a, n):
    from coach.analytics.common import div_cents
    return div_cents(a, n)


def test_the_flows_to_label_skip_the_bank_fees_and_list_what_is_in_no_category(cfg):
    con = rental_world(cfg)
    from helpers import add_tx
    add_tx(con, "rn", "odd1", "2026-09-12", -77.0, "MYSTERE SAS", "card")
    d = analytics_api.build_dataset(con, cfg, TODAY)
    (p,) = M.properties(d)
    assert [t.key for t in CF.flows_to_review(d, p)] == ["odd1"]               # the monthly bank fee stays in "other flows" but is not asked about
    assert CF.year_pnl(d, p, 2026)["totals"]["other_c"] == 2025 + 7700
    con.close()


def test_a_tag_puts_a_cost_paid_from_another_account_on_the_property(cfg):
    con = rental_world(cfg)
    from helpers import add_tx
    add_tx(con, "ce", "tf-main", "2026-05-20", -150.0, "PRLV DGFIP TAXE FONCIERE GARAGE", "direct_debit")
    (cfg.memory_dir / "categorization.yaml").write_text((cfg.memory_dir / "categorization.yaml").read_text() +
        "\n  - id: garage-tax\n    match:\n      tx_keys: [tf-main]\n    tags: [property-rental-flat-1]\n")
    d = analytics_api.build_dataset(con, cfg, TODAY)
    (p,) = M.properties(d)
    assert CF.year_pnl(d, p, 2026)["totals"]["taxes_c"] == 80000 + 15000
    con.close()


def test_the_loan_payments_of_another_account_count_as_the_property_loan(cfg):
    con = rental_world(cfg)
    from helpers import add_tx
    con.execute("DELETE FROM transactions WHERE tx_key LIKE 'loan%'")
    con.execute("DELETE FROM tx_enriched WHERE tx_key LIKE 'loan%'")
    for mo in range(1, 10):
        add_tx(con, "ce", f"lm{mo}", f"2026-{mo:02d}-01", -900.0, "ECH PRET LENDERCO", "loan_payment")
    con.commit()
    d = analytics_api.build_dataset(con, cfg, TODAY)
    (p,) = M.properties(d)
    assert CF.year_pnl(d, p, 2026)["totals"]["loan_c"] == 810000                                  # from the loan's payment_match, on the main account
    con.close()


def test_two_properties_and_an_unlinked_rental_account(cfg):
    second = """
  - id: rental-flat-2
    kind: real_estate_rental
    account: fo
    rent_monthly: 500
"""
    con = rental_world(cfg, asset=RENTAL_ASSET_PLUS(second))
    from helpers import add_bank
    add_bank(con, "s10", "Otherbank", "FR", [("r3", "FR7600000000000000000098", "Other rental")])
    con.execute("UPDATE accounts SET owner='joint', purpose='rental' WHERE uid='r3'")
    con.commit()
    d = analytics_api.build_dataset(con, cfg, TODAY)
    ps = {p.id: p for p in M.properties(d)}
    assert set(ps) == {"rental-flat-1", "rental-flat-2"} and ps["rental-flat-2"].accounts == ["fo"]
    assert M.unlinked_rental_accounts(d) == ["r3"]                                                 # a rental account that no property names
    assert not [t for t in M.attributed(d)["rental-flat-2"] if t.account == "rn"]               # nothing leaks between properties
    with pytest.raises(LookupError):
        M.find(d, None)                                                                            # several properties: name one
    con.close()


def RENTAL_ASSET_PLUS(extra: str) -> str:
    from rentalhelpers import RENTAL_ASSET
    return RENTAL_ASSET + extra


def test_a_single_rental_account_and_property_are_linked_and_flagged_only_one(cfg):
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    rent_monthly: 620
"""
    con = rental_world(cfg, asset=asset, loan=False)
    d = analytics_api.build_dataset(con, cfg, TODAY)
    (p,) = M.properties(d)
    assert p.accounts == ["rn"] and p.account_link == "only_one" and p.loan_link == "none" and p.loans == []
    con.close()
