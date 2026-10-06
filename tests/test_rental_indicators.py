"""E15-5: the renegotiate-or-sell indicators: the loan rate against the market rate the OWNER entered (nothing is looked up), the end of the commitment,
the net equity (declared valuation - capital still due from the schedule), the renegotiation priced on the real schedule. Facts and a neutral
reading, never a lender or a product."""
from __future__ import annotations


import pytest

from anhelpers import D
from coach.analytics.common import to_cents
from coach.loans import service as LS
from coach.rental import indicators as IND, model as M
from coach.rental.render import plain
from test_rental_tax import LOAN, asset, world


def ind(ds, **kw):
    return IND.indicators(ds, M.properties(ds)[0], **kw)


def test_a_loan_above_the_market_rate_is_flagged_and_priced_on_the_real_schedule():
    ds = world()
    r = ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-02-01"))
    lr = r["loan_rate"]
    assert lr["status"] == "above_market" and lr["loan_rate_pct"] == 3.0 and lr["market_rate_pct"] == 2.4 and lr["gap_pts"] == 0.6
    assert "a quote may be worth asking for" in lr["reading"] and "not financial advice" in lr["reading"]
    rn = lr["renegotiation"]
    assert rn["status"] == "computed" and rn["current_rate_pct"] == 3.0 and rn["new_rate_pct"] == 2.4
    sch = LS.schedule_of(ds, LOAN)
    assert rn["remaining_months"] == sch.remaining_instalments and rn["basis"]["from"] == "the amortization schedule"     # the capital and the months of the schedule, not of the contract
    assert lr["renegotiation_note"].startswith("no fee was given")
    assert {s["id"] for s in r["signals"]} >= {"rate_above_market"}


def test_fees_given_by_the_owner_raise_the_costs_of_the_scenario():
    ds = world()
    base = ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-02-01"))["loan_rate"]["renegotiation"]
    with_fees = ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-02-01"), fees={"bank_fees": 1000, "guarantee_fees": 500})["loan_rate"]["renegotiation"]
    assert to_cents(with_fees["total_costs"]) - to_cents(base["total_costs"]) == 150000
    assert "renegotiation_note" not in ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-02-01"), fees={"bank_fees": 1000})["loan_rate"]


@pytest.mark.parametrize("market,status", [(2.4, "above_market"), (2.7, "close_to_market"), (3.4, "close_to_market"), (3.6, "below_market")])
def test_the_gap_is_compared_with_the_threshold_of_the_settings(market, status):
    ds = world()
    lr = ind(ds, market_rate_pct=market, market_rate_date=D("2026-02-01"))["loan_rate"]
    assert lr["status"] == status and ds.settings.rental_rate_gap_pts == 0.5
    assert ("renegotiation" in lr) == (status == "above_market")


def test_no_market_rate_is_never_invented():
    ds = world()
    r = ind(ds)
    assert r["market_rate"]["status"] == "missing" and "never looks it up" in r["market_rate"]["note"] or "coach never looks it up" in r["market_rate"]["note"]
    assert r["loan_rate"]["status"] == "unknown" and r["loan_rate"]["missing"] == ["a market rate you entered"] and "renegotiation" not in r["loan_rate"]


def test_a_market_rate_recorded_on_the_property_is_used_and_an_argument_wins_over_it():
    rec = asset(market_rate={"rate_pct": 2.5, "as_of": "2026-02-01", "source": "a quote"})
    ds = world(assets=[rec])
    r = ind(ds)
    assert r["market_rate"]["basis"] == "recorded on the property" and r["market_rate"]["rate_pct"] == 2.5 and r["loan_rate"]["gap_pts"] == 0.5
    over = ind(ds, market_rate_pct=2.9, market_rate_date=D("2026-02-05"))
    assert over["market_rate"]["basis"] == "given for this call" and over["loan_rate"]["gap_pts"] == 0.1


def test_an_old_or_undated_market_rate_is_not_presented_as_current():
    ds = world()
    old = ind(ds, market_rate_pct=2.4, market_rate_date=D("2025-12-01"))["market_rate"]
    assert old["age_days"] == 71 and "more than 30" in old["warning"]
    assert "date of the market rate was not given" in ind(ds, market_rate_pct=2.4)["market_rate"]["warning"]
    assert "in the future" in ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-03-01"))["market_rate"]["warning"]
    assert "warning" not in ind(ds, market_rate_pct=2.4, market_rate_date=D("2026-02-01"))["market_rate"]
    import coach.mcp.tools  # noqa: F401  (the tools modules import each other: this one first)
    from coach.skills.tools import MARKET_MAX_AGE_DAYS
    assert IND.MARKET_MAX_AGE_DAYS == MARKET_MAX_AGE_DAYS                                  # the same 30 days as the mortgage check


def test_net_equity_is_the_declared_value_less_the_capital_due_from_the_schedule():
    ds = world()
    eq = ind(ds)["equity"]
    cap = LS.schedule_of(ds, LOAN).remaining_capital_c
    assert eq["status"] == "computed" and eq["outstanding_c"] == cap and eq["value_c"] == 18000000 and eq["net_equity_c"] == 18000000 - cap
    assert eq["loan_to_value_pct"] == round(cap / 18000000 * 100, 1) and eq["equity_share_pct"] == round((18000000 - cap) / 18000000 * 100, 1)
    assert "selling costs" in eq["notes"][0] and "value_warning" not in eq
    assert {"id": "equity_positive"} == {"id": [s for s in ind(ds)["signals"] if s["id"].startswith("equity")][0]["id"]}


def test_a_valuation_below_the_capital_gives_a_negative_equity_signal():
    ds = world(assets=[asset(value=50000)])
    r = ind(ds)
    assert r["equity"]["net_equity_c"] < 0 and any(s["id"] == "equity_negative" and "would not repay the loan" in s["reading"] for s in r["signals"])


def test_a_stale_or_undated_valuation_is_flagged_and_a_missing_one_is_listed():
    assert "dates from 2025-06-01" in ind(world(assets=[asset(as_of="2025-06-01")]))["equity"]["value_warning"]
    assert ind(world(assets=[asset(as_of=None)]))["equity"]["value_warning"] == "the valuation has no as_of date"
    none = ind(world(assets=[asset(value=None)]))["equity"]
    assert none["status"] == "unknown" and none["missing"][0].startswith("value") and "net_equity_c" not in none


def test_an_unknown_loan_makes_the_equity_unknown_and_is_never_estimated():
    ds = world(loans=())
    eq = ind(ds)["equity"]
    assert eq["status"] == "unknown" and "net_equity_c" not in eq and any("loan" in m for m in eq["missing"])
    assert ind(ds)["loan_rate"]["missing"] == ["the loan and its nominal rate"]


def test_a_declared_outstanding_stands_in_when_no_schedule_can_be_computed():
    bare = LOAN.model_copy(update={"principal": None, "start_date": None, "end_date": None, "term_months": None, "outstanding": 90000, "outstanding_as_of": D("2026-01-15")})
    ds = world(loans=(("liabilities/loan-1.yaml", bare),))
    eq = ind(ds)["equity"]
    assert eq["status"] == "computed" and eq["outstanding_c"] == 9000000 and eq["approximate"] is True and eq["loans"][0]["source"].startswith("declared outstanding")


def test_the_commitment_signals_follow_the_state():
    ds = world()
    assert [s["id"] for s in ind(ds)["signals"] if s["id"].startswith("commitment")] == ["commitment_running"]
    near = world(assets=[asset(commitment={"start_date": "2017-03-05", "years": 9, "reduction_rate_pct": 18})])
    sig = [s for s in ind(near)["signals"] if s["id"].startswith("commitment")]
    assert sig[0]["id"] == "commitment_ending" and "extend (if the scheme allows it)" in sig[0]["reading"] and "not financial advice" in sig[0]["reading"]
    over = world(assets=[asset(commitment={"start_date": "2010-01-01", "years": 6})])
    assert [s["id"] for s in ind(over)["signals"] if s["id"].startswith("commitment")] == ["commitment_over"]


def test_nothing_in_the_indicators_names_a_lender_a_product_or_recommends_one():
    r = plain(ind(world(), market_rate_pct=2.4, market_rate_date=D("2026-02-01")))
    text = str(r).lower()
    for word in ("recommend you", "you should sell", "you should keep", "best offer", "switch to"):
        assert word not in text
    assert "no product is recommended" in r["disclaimer"] and "pret" not in text
