"""E9-5: early repayment / renegotiation / insurance scenarios on the real schedule (hand-computed). Reference loan: 10,000 EUR at 12 % over
12 months from 2025-10-15 (1 % a month, instalment 888.49); on 2026-01-20 three instalments are paid and 7,610.80 is due."""
from __future__ import annotations

import datetime as dt

import pytest

from coach.loans import scenario as SC, schedule as S
from coach.memory import schemas

TODAY = dt.date(2026, 1, 20)


def D(s):
    return dt.date.fromisoformat(s)


def loan(**kw):
    base = dict(id="car", kind="car_loan", principal=10000, start_date=D("2025-10-15"), term_months=12, rate={"nominal": 12.0})
    base.update(kw)
    return schemas.Liability(**base)


def run(lb, **kw):
    return SC.prepay(lb, S.compute(lb, TODAY), TODAY, kw.pop("amount", 2000), D(kw.pop("on", "2026-02-15")), **kw)


def opt(res, mode):
    return next(o for o in res["options"] if o["mode"] == mode)


def test_prepayment_keeping_the_instalment_shortens_the_loan():
    res = run(loan())
    assert res["status"] == "computed" and res["capital_before"] == "7610.80" and res["capital_after"] == "5610.80"
    assert res["remaining_instalments"] == 9 and res["instalment"] == "888.49" and res["penalty"] == "0.00"
    kp = opt(res, "keep_payment")
    # 5,610.80 at 888.49: 7 instalments instead of 9 (the last one is 494.88); interest 215.02 instead of 385.59 (= 661.86 - 276.27) -> 170.57 saved
    assert kp["months_saved"] == 2 and kp["interest_saved"] == "170.57" and kp["new_instalment"] == "888.49" and kp["monthly_change"] == "0.00"
    assert kp["new_last_instalment"] == "2026-08-15" and kp["net_saving"] == "170.57" and kp["break_even_months"] == 0


def test_prepayment_keeping_the_end_date_lowers_the_instalment():
    kt = opt(run(loan()), "keep_term")
    # annuity of 5,610.80 over 9 months at 1 % = 655.01 (233.48 less); interest 284.27 instead of 385.59 -> 101.32 saved
    assert kt["new_instalment"] == "655.01" and kt["monthly_change"] == "-233.48" and kt["interest_saved"] == "101.32" and kt["months_saved"] == 0
    assert kt["new_last_instalment"] == "2026-10-15"


def test_penalty_net_saving_and_break_even_from_the_cumulative_interest_saved():
    res = run(loan(), penalty=60)
    assert res["penalty"] == "60.00" and "your contract" in res["penalty_basis"]
    kp, kt = opt(res, "keep_payment"), opt(res, "keep_term")
    # cumulative interest saved by month (keep instalment): 20.00, 40.20, 60.60 -> covers 60.00 in month 3; net 170.57 - 60.00 = 110.57
    assert kp["break_even_months"] == 3 and kp["net_saving"] == "110.57"
    # keep the end date: 20.00, 37.86, 53.57, 67.10 -> month 4; net 101.32 - 60.00 = 41.32
    assert kt["break_even_months"] == 4 and kt["net_saving"] == "41.32"
    big = run(loan(), penalty=500)
    assert opt(big, "keep_payment")["break_even_months"] is None and opt(big, "keep_payment")["net_saving"] == "-329.43"
    assert "exceeds" in opt(big, "keep_payment")["verdict"]


def test_the_french_ira_is_the_lower_of_six_months_of_interest_and_three_percent():
    mortgage = loan(kind="mortgage")
    # 6 months of interest on the amount prepaid: 2,000 x 12 % / 2 = 120.00 ; 3 % of the capital due (7,610.80) = 228.32 -> 120.00
    pen, basis = SC._penalty(mortgage, "FR", 761080, 200000, 12, None)
    assert pen == 12000 and "six months of interest" in basis
    # a small capital: 4,000 of 5,000: 6 months = 240.00 but 3 % of 5,000 = 150.00 -> the cap
    pen2, _ = SC._penalty(mortgage, "FR", 500000, 400000, 12, None)
    assert pen2 == 15000
    it, basis_it = SC._penalty(mortgage, "IT", 761080, 200000, 12, None)
    assert it == 0 and "art. 120-ter TUB" in basis_it and "purchase or renovation of residential property" in basis_it
    car, note = SC._penalty(loan(), "FR", 761080, 200000, 12, None, 9)
    assert car == 0 and "L312-34" in note and "10,000 EUR" in note                       # 2,000 repaid: under the 10,000 EUR threshold
    other, note2 = SC._penalty(loan(kind="bnpl"), "FR", 761080, 200000, 12, None, 9)
    assert other == 0 and "no legal penalty is modelled" in note2
    assert run(mortgage)["penalty"] == "120.00"


def test_insurance_savings_follow_the_months_saved_or_the_outstanding_capital():
    flat = run(loan(insurance={"monthly": 20}))
    assert opt(flat, "keep_payment")["insurance_saved"] == "40.00"           # two fewer months at 20.00; a flat premium is not recalculated
    assert opt(flat, "keep_term")["insurance_saved"] == "0.00" and any("flat insurance" in n for n in flat["notes"])
    out = run(loan(insurance={"rate_pct": 0.36, "basis": "outstanding"}))
    assert float(opt(out, "keep_term")["insurance_saved"]) > 0


def test_the_prepayment_is_applied_before_an_instalment_due_the_same_day_and_refuses_the_impossible():
    on_due = run(loan(), on="2026-02-15")
    assert on_due["remaining_instalments"] == 9 and on_due["capital_before"] == "7610.80"       # the 2026-02-15 instalment is still ahead
    assert run(loan(), amount=7610.80)["status"] == "payoff"
    assert SC.prepay(loan(), S.compute(loan(), TODAY), TODAY, 100, D("2027-01-01"))["status"] == "nothing_left"
    with pytest.raises(ValueError):
        run(loan(), amount=-5)
    deferred = loan(deferral={"months": 6, "kind": "partial"})
    assert run(deferred, on="2025-12-20", amount=1000)["status"] == "needs_fields"                # still in the deferral


def test_a_loan_without_a_schedule_lists_the_missing_fields():
    x = schemas.Liability(id="m", kind="mortgage", monthly_payment=1000)
    r = SC.prepay(x, S.compute(x, TODAY), TODAY, 1000)
    assert r["status"] == "needs_fields" and "principal" in r["missing"]
    assert SC.renegotiate(x, S.compute(x, TODAY), TODAY, 2.0)["status"] == "needs_fields"
    lease = schemas.Liability(id="l", kind="loa", monthly_payment=300)
    assert SC.prepay(lease, S.compute(lease, TODAY), TODAY, 1000)["status"] == "needs_fields"


def test_renegotiation_uses_the_remaining_capital_and_instalments_of_the_schedule():
    lb = loan(kind="mortgage")
    r = SC.renegotiate(lb, S.compute(lb, TODAY), TODAY, 6.0, country="FR", variant="rachat", bank_fees=100)
    assert r["status"] == "computed" and r["basis"]["capital"] == "7610.80" and r["basis"]["remaining_instalments"] == 9
    # the current instalment on 7,610.80 over 9 months at 1 % = 888.49; at 6 % a year (0.5 % a month) it is 7,610.80 x 0.005 / (1 - 1.005^-9)
    # = 38.054 / 0.043895 = 866.93
    assert r["current_payment"] == "888.49" and r["new_payment"] == "866.93"
    # IRA on the whole capital: 6 months of interest at 12 % = 456.65 vs 3 % of 7,610.80 = 228.32 -> 228.32
    assert r["penalty"] == "228.32" and r["total_costs"] == "328.32" and r["variant"] == "rachat"
    it = SC.renegotiate(lb, S.compute(lb, TODAY), TODAY, 6.0, country="IT")
    assert it["variant"] == "surroga" and it["penalty"] == "0.00"


def test_insurance_scenario_takes_the_current_premium_from_the_schedule():
    lb = loan(insurance={"monthly": 30})
    r = SC.insurance(lb, S.compute(lb, TODAY), TODAY, 20, fees=0)
    # 10 EUR a month over the 9 remaining instalments
    assert r["status"] == "computed" and r["monthly_saving"] == "10.00" and r["total_saving"] == "90.00" and r["net_saving"] == "90.00"
    assert SC.insurance(loan(), S.compute(loan(), TODAY), TODAY, 20)["status"] == "needs_fields"


def test_the_result_can_be_stored_as_an_insight_without_names_or_ids():
    res = run(loan(lender="Secret Bank"))
    from coach.analytics.common import _plain
    title, body = SC.insight_text("prepay", "car_loan", _plain(res))
    assert "car loan" in title and "Secret" not in body and "Secret" not in title and "car-" not in body
    assert "not financial advice" in body and "170.57" in body


def test_french_consumer_and_car_loans_follow_the_l312_34_cap():
    # 12,000 repaid (above 10,000): at most 1 % = 120.00 with more than a year left (24 months), 0.5 % = 60.00 with a year or less (12 months)
    long_, basis = SC._penalty(loan(), "FR", 3000000, 1200000, 12, None, 24)
    short, _ = SC._penalty(loan(kind="consumer_loan"), "FR", 3000000, 1200000, 12, None, 12)
    assert long_ == 12000 and "1 %" in basis and "more than a year" in basis and short == 6000
    assert SC._penalty(loan(), "FR", 3000000, 1000000, 12, None, 24)[0] == 0                 # exactly 10,000: still free
    assert SC._penalty(loan(), "FR", 3000000, 1200000, 12, 45, 24)[0] == 4500                 # the contract's own amount wins
    assert SC._penalty(loan(), "IT", 3000000, 1200000, 12, None, 24)[0] == 0                  # not an Italian rule
    # end to end: 10,000 at 12 % over 36 months, 12,000 is more than the loan: use a bigger one (40,000, 36 months, 30 left)
    big = loan(principal=40000, term_months=36)
    s = S.compute(big, TODAY)
    res = SC.prepay(big, s, TODAY, 12000, D("2026-02-15"))
    assert res["status"] == "computed" and res["penalty"] == "120.00" and "L312-34" in res["penalty_basis"]
