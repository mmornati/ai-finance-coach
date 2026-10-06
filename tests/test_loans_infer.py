"""E9-1: inferring the missing terms of a loan from the observed instalments. Reference annuities (checked against the usual tables):
200,000 EUR at 3 % over 240 months = 1,109.20 a month; 150,000 at 4.8 % over 120 months = 1,576.36; 10,000 at 12 % over 12 months = 888.49."""
from __future__ import annotations

import datetime as dt

import pytest

from coach.loans import infer as I
from coach.loans.payments import Payment
from coach.memory import schemas

TODAY = dt.date(2026, 10, 5)


def D(s):
    return dt.date.fromisoformat(s)


def obs(amounts, first="2026-03-05"):
    out, d = [], D(first)
    from coach.analytics.common import add_months
    for i, a in enumerate(amounts):
        out.append(Payment(add_months(d, i), int(round(a * 100)), "acc", "Main", f"k{i}"))
    return out


def loan(**kw):
    base = dict(id="m", kind="mortgage")
    base.update(kw)
    return schemas.Liability(**base)


STEADY = obs([1109.20] * 8)


def fields(inf):
    return {f.field: f for f in inf.fields}


def test_principal_from_the_instalment_the_rate_and_the_term():
    inf = I.infer_loan(loan(rate={"nominal": 3.0}, start_date=D("2021-01-05"), end_date=D("2041-01-05")), STEADY, TODAY)
    f = fields(inf)
    # P (1 - 1.0025^-240) / 0.0025 = 1,109.20 x 180.3 = 200,000.87: the instalment is rounded to the cent, so the capital is right to
    # about 1 EUR (rounded to the euro: 200,001) and the suggestion says to check it against the contract
    assert inf.status == "inferred" and abs(f["principal"].value - 200000) <= 1 and f["principal"].inferred is True
    assert "check against the contract" in f["principal"].note
    assert f["principal"].confidence == "medium"                  # 8 steady payments (high) but the insurance is not recorded: capped at medium
    assert "monthly_payment" in f and f["monthly_payment"].value == 1109.2


def test_rate_by_newtons_method_from_principal_term_and_instalment():
    inf = I.infer_loan(loan(principal=200000, start_date=D("2021-01-05"), end_date=D("2041-01-05")), STEADY, TODAY)
    assert fields(inf)["rate.nominal"].value == 3.0 and "Newton" in fields(inf)["rate.nominal"].method
    assert I.rate_pct_of(I.solve_rate_newton(200000, 1109.20, 240)) == 3.0
    # the same loan with a 0 % rate: 12,000 over 12 months at 1,000
    assert I.solve_rate_newton(12000, 1000, 12) == 0.0
    # 150,000 over 120 months at 1,576.36 is 4.8 %
    assert I.rate_pct_of(I.solve_rate_newton(150000, 1576.36, 120)) == 4.8
    # instalments that total less than the capital: no rate exists
    assert I.solve_rate_newton(200000, 100, 240) is None


def test_term_and_end_date_from_principal_rate_and_instalment():
    inf = I.infer_loan(loan(principal=200000, rate={"nominal": 3.0}, start_date=D("2021-01-05")), STEADY, TODAY)
    f = fields(inf)
    # n = -ln(1 - 200,000 x 0.0025 / 1,109.20) / ln(1.0025) = 240
    assert f["term_months"].value == 240 and f["end_date"].value == "2041-01-05"
    assert I.solve_term(200000, 1109.20, 0.0025) == pytest.approx(240, abs=0.05)
    assert I.solve_term(200000, 400, 0.0025) is None                   # 400 does not even cover the interest (500)


def test_the_insurance_is_subtracted_when_known_and_caps_the_confidence_when_not():
    steady_ins = obs([1129.20] * 8)                                    # 1,109.20 + 20.00 of insurance debited together
    inf = I.infer_loan(loan(rate={"nominal": 3.0}, start_date=D("2021-01-05"), end_date=D("2041-01-05"), insurance={"monthly": 20}), steady_ins, TODAY)
    f = fields(inf)
    assert abs(f["principal"].value - 200000) <= 1 and f["principal"].confidence == "high"
    assert any("subtracted" in n for n in inf.notes)
    unknown = I.infer_loan(loan(rate={"nominal": 3.0}, start_date=D("2021-01-05"), end_date=D("2041-01-05")), steady_ins, TODAY)
    assert fields(unknown)["principal"].value > 200000 and fields(unknown)["principal"].confidence == "medium"     # overstated: said so
    assert any("insurance" in n for n in unknown.notes)


def test_confidence_follows_the_number_and_the_stability_of_the_observations():
    known = dict(kind="consumer_loan", insurance={"monthly": 0.0}, rate={"nominal": 3.0}, start_date=D("2021-01-05"), end_date=D("2041-01-05"))
    assert fields(I.infer_loan(loan(**known), obs([1109.20] * 6), TODAY))["principal"].confidence == "high"
    assert fields(I.infer_loan(loan(**known), obs([1109.20] * 4), TODAY))["principal"].confidence == "medium"
    assert fields(I.infer_loan(loan(**known), obs([1109.20] * 2), TODAY))["principal"].confidence == "low"
    wobbly = I.infer_loan(loan(**known), obs([1109.20, 1109.20, 1109.20, 1109.20, 1109.20, 1170.0]), TODAY)
    assert fields(wobbly)["principal"].confidence == "low" and any("varies" in n for n in wobbly.notes)
    var = I.infer_loan(loan(**{**known, "rate": {"type": "variable", "nominal": 3.0}}), obs([1109.20] * 8), TODAY)
    assert fields(var)["principal"].confidence == "medium"


def test_the_remaining_capital_route_gives_the_rate_or_the_end_date():
    # 150,000 due on 2026-01-10, 120 months to 2036-01-10, instalment 1,576.36 -> 4.8 %
    inf = I.infer_loan(loan(outstanding=150000, outstanding_as_of=D("2026-01-10"), end_date=D("2036-01-10"), insurance={"monthly": 0}),
                       obs([1576.36] * 8), TODAY)
    f = fields(inf)
    assert f["rate.nominal"].value == 4.8 and f["rate.nominal"].confidence == "medium" and "capital still due" in f["rate.nominal"].method
    # with the rate known instead of the end date: 150,000 at 4.8 % with 1,576.36 a month -> 120 months after the date of the capital
    inf2 = I.infer_loan(loan(outstanding=150000, outstanding_as_of=D("2026-01-10"), rate={"nominal": 4.8}, insurance={"monthly": 0}),
                        obs([1576.36] * 8), TODAY)
    assert fields(inf2)["end_date"].value == "2036-01-10"


def test_the_start_date_is_never_taken_from_the_first_payment_in_the_bank_data():
    inf = I.infer_loan(loan(), STEADY, TODAY)               # nothing known but the instalment
    assert inf.status == "insufficient" and "start_date" not in fields(inf) and "monthly_payment" in fields(inf)
    # a start date follows from an end date and a term, both recorded or inferred
    inf2 = I.infer_loan(loan(principal=200000, rate={"nominal": 3.0}, end_date=D("2041-01-05")), STEADY, TODAY)
    f2 = fields(inf2)
    assert f2["term_months"].value == 240 and f2["start_date"].value == "2021-01-05"


def test_edge_cases_nothing_to_infer_a_lease_and_no_payments():
    full = I.infer_loan(loan(principal=200000, rate={"nominal": 3.0}, start_date=D("2021-01-05"), end_date=D("2041-01-05"), monthly_payment=1109.2),
                        STEADY, TODAY)
    assert full.status == "nothing_to_infer" and full.fields == []
    lease = I.infer_loan(schemas.Liability(id="c", kind="loa", monthly_payment=300), obs([300] * 6), TODAY)
    assert lease.status == "not_applicable"
    none = I.infer_loan(loan(), [], TODAY)
    assert none.status == "insufficient" and none.missing and none.fields == []


def test_the_suggestions_become_set_operations_with_dates_as_dates_and_a_confidence_floor():
    inf = I.infer_loan(loan(principal=200000, rate={"nominal": 3.0}, start_date=D("2021-01-05")), STEADY, TODAY)
    ops = I.to_ops(inf, "low")
    assert {"op": "set", "path": "end_date", "value": D("2041-01-05")} in ops and {"op": "set", "path": "term_months", "value": 240} in ops
    assert I.to_ops(inf, "high") == [o for o in ops if o["path"] == "monthly_payment"]       # only the payment is high confidence here
