"""E9-3: the amortization schedule of a liability. Every expected figure is worked out by hand in the comments (10,000 EUR at 12 % nominal =
1 % a month over 12 months: instalment 888.49, month 1 interest 100.00, month 2 interest 9,211.51 x 1 % = 92.12 ...)."""
from __future__ import annotations

import datetime as dt

import pytest

from coach.analytics.common import money_str
from coach.loans import schedule as S
from coach.memory import schemas

TODAY = dt.date(2026, 1, 20)


def D(s):
    return dt.date.fromisoformat(s)


def lb(**kw) -> schemas.Liability:
    base = dict(id="loan", kind="car_loan", principal=10000, start_date=D("2025-10-15"), term_months=12, rate={"nominal": 12.0})
    base.update(kw)
    return schemas.Liability(**base)


def cents(rows, attr):
    return [money_str(getattr(r, attr + "_c")) for r in rows]


def test_a_plain_annuity_schedule_hand_computed():
    s = S.compute(lb(), TODAY)
    assert s.status == "computed" and s.mode == "from_principal" and not s.approximate
    assert money_str(s.payment_c) == "888.49" and s.term_instalments == 12
    # interest = balance x 1 %, rounded half up; principal = 888.49 - interest; the last instalment clears 879.67 + 8.80
    assert cents(s.rows, "interest")[:3] == ["100.00", "92.12", "84.15"]
    assert cents(s.rows, "principal")[:3] == ["788.49", "796.37", "804.34"]
    assert cents(s.rows, "balance")[:3] == ["9211.51", "8415.14", "7610.80"]
    assert money_str(s.rows[-1].payment_c) == "888.47" and s.rows[-1].balance_c == 0
    # 661.86 = 11 x 888.49 + 888.47 - 10,000
    assert money_str(s.total_interest_c) == "661.86" and money_str(s.total_cost_c) == "661.86"
    # due dates: one month after the start, on the 15th: 2025-11-15 ... 2026-10-15
    assert s.rows[0].due == D("2025-11-15") and s.rows[-1].due == D("2026-10-15") and s.next_due == D("2026-02-15")


def test_remaining_capital_and_payments_made_today():
    s = S.compute(lb(), TODAY)                      # 2025-11-15, 12-15 and 2026-01-15 are due: three instalments
    assert s.payments_made == 3 and s.remaining_instalments == 9
    assert money_str(s.remaining_capital_c) == "7610.80"
    assert money_str(s.interest_paid_c) == "276.27"                       # 100.00 + 92.12 + 84.15
    assert money_str(s.remaining_interest_c) == "385.59"                  # 661.86 - 276.27
    assert S.balance_on(s, D("2025-11-01")) == 1000000                    # before the first instalment: the whole capital
    assert money_str(S.balance_on(s, D("2025-12-31"))) == "8415.14"


def test_interest_per_calendar_year_is_the_sum_of_the_instalments_due_that_year():
    s = S.compute(lb(), TODAY)
    y25, y26 = s.by_year
    # 2025: instalments 1 and 2 (11-15, 12-15); 2026: the other ten
    assert (y25.year, y25.instalments, money_str(y25.interest_c), money_str(y25.principal_c)) == (2025, 2, "192.12", "1584.86")
    assert (y26.year, y26.instalments, money_str(y26.interest_c)) == (2026, 10, "469.74")
    assert y25.interest_c + y26.interest_c == s.total_interest_c
    assert S.interest_in_year(s, 2026)["interest"] == "469.74" and S.interest_in_year(s, 2030) is None


def test_a_partial_deferral_pays_interest_only_then_amortizes_the_same_capital():
    s = S.compute(lb(deferral={"months": 2, "kind": "partial"}), TODAY)
    r1, r2, r3 = s.rows[:3]
    assert (r1.kind, money_str(r1.interest_c), money_str(r1.principal_c), money_str(r1.payment_c), money_str(r1.balance_c)) == \
        ("deferral", "100.00", "0.00", "100.00", "10000.00")
    assert r2.balance_c == 1000000
    # then 10 instalments of the annuity of 10,000 at 1 %: 1,055.82; month 3: interest 100.00, principal 955.82
    assert (r3.kind, money_str(r3.payment_c), money_str(r3.principal_c), money_str(r3.balance_c)) == ("regular", "1055.82", "955.82", "9044.18")
    assert money_str(s.payment_c) == "1055.82" and len(s.rows) == 12 and s.rows[-1].balance_c == 0
    assert any("interest-only" in a for a in s.assumptions)


def test_a_total_deferral_adds_the_interest_to_the_capital():
    s = S.compute(lb(deferral={"months": 2, "kind": "total"}), TODAY)
    r1, r2, r3 = s.rows[:3]
    # nothing is paid; 100.00 then 101.00 (1 % of 10,100.00) are added: 10,201.00; the annuity of that over 10 months is 1,077.04
    assert (money_str(r1.payment_c), money_str(r1.interest_c), money_str(r1.balance_c)) == ("0.00", "100.00", "10100.00")
    assert (money_str(r2.interest_c), money_str(r2.balance_c)) == ("101.00", "10201.00")
    assert money_str(r3.payment_c) == "1077.04" and money_str(r3.interest_c) == "102.01"
    assert s.rows[-1].balance_c == 0


def test_insurance_flat_or_as_a_rate_on_the_initial_or_the_outstanding_capital():
    flat = S.compute(lb(insurance={"monthly": 20}), TODAY)
    assert money_str(flat.rows[0].total_c) == "908.49" and money_str(flat.total_insurance_c) == "240.00"
    assert money_str(flat.total_cost_c) == "901.86"                              # 661.86 interest + 240.00 insurance
    init = S.compute(lb(insurance={"rate_pct": 0.36, "basis": "initial"}), TODAY)
    assert {money_str(r.insurance_c) for r in init.rows} == {"3.00"}              # 10,000 x 0.36 % / 12
    out = S.compute(lb(insurance={"rate_pct": 0.36, "basis": "outstanding"}), TODAY)
    # 10,000 x 0.03 % = 3.00; 9,211.51 x 0.03 % = 2.76; 8,415.14 x 0.03 % = 2.52 (before each instalment)
    assert cents(out.rows, "insurance")[:3] == ["3.00", "2.76", "2.52"]
    assert out.by_year[0].insurance_c == 300 + 276


def test_first_payment_date_and_payment_day_move_the_due_dates():
    s = S.compute(lb(first_payment_date=D("2025-12-05"), payment_day=5), TODAY)
    assert s.rows[0].due == D("2025-12-05") and s.rows[1].due == D("2026-01-05")          # the first instalment on the given date, then monthly
    s1 = S.compute(lb(payment_day=28), TODAY)
    assert [r.due for r in s1.rows[:2]] == [D("2025-11-28"), D("2025-12-28")]             # start + k months, on the debit day
    s2 = S.compute(lb(start_date=D("2026-01-31"), term_months=3), TODAY)
    assert [r.due for r in s2.rows] == [D("2026-02-28"), D("2026-03-31"), D("2026-04-30")]   # start + k months, clamped to the month end


def test_the_schedule_agrees_with_the_skills_module_and_flags_a_variable_rate():
    from coach.skills import loans as L
    s = S.compute(lb(rate={"type": "variable", "nominal": 12.0, "index": "Euribor 12M", "margin": 1.2, "cap": 14}), TODAY)
    assert s.approximate and any("variable" in a for a in s.assumptions)
    ref = L.schedule(1000000, 12, 12)
    assert [r.interest_c for r in s.rows] == [r.interest_c for r in ref] and [r.balance_c for r in s.rows] == [r.balance_c for r in ref]


def test_a_declared_payment_and_capital_are_checked_against_the_table():
    ok = S.compute(lb(monthly_payment=908.49, insurance={"monthly": 20}), TODAY)
    assert ok.payment_check["status"] == "matches"
    no_ins = S.compute(lb(monthly_payment=888.49, insurance={"monthly": 20}), TODAY)
    assert no_ins.payment_check["status"] == "matches_without_insurance"
    off = S.compute(lb(monthly_payment=1200), TODAY)
    assert off.payment_check["status"] == "differs"
    cap = S.compute(lb(outstanding=9000, outstanding_as_of=D("2026-01-16")), TODAY)    # the table says 7,610.80
    assert cap.outstanding_check["status"] == "differs" and cap.outstanding_check["computed"] == "7610.80"
    near = S.compute(lb(outstanding=7650, outstanding_as_of=D("2026-01-16")), TODAY)
    assert near.outstanding_check is None


def test_what_is_missing_is_listed_and_nothing_is_guessed():
    s = S.compute(schemas.Liability(id="x", kind="mortgage", monthly_payment=1000), TODAY)
    assert s.status == "not_computable" and s.missing == ["principal", "rate.nominal", "start_date", "end_date or term_months"] and s.rows == []
    assert S.compute(lb(principal=None), TODAY).missing == ["principal"]
    assert S.compute(lb(term_months=None), TODAY).missing == ["end_date or term_months"]
    lease = S.compute(schemas.Liability(id="l", kind="loa", monthly_payment=300), TODAY)
    assert lease.status == "not_applicable" and "lease" in lease.alternative


def test_from_the_declared_capital_only_the_future_table_is_built():
    # 6000.00 due on 2026-01-10 at 1 % a month, ends 2026-07-10: the 10th of Feb .. Jul = 6 instalments: annuity 1,035.29
    x = lb(principal=None, start_date=None, term_months=None, end_date=D("2026-07-10"), outstanding=6000, outstanding_as_of=D("2026-01-10"))
    s = S.compute(x, TODAY)
    assert s.status == "computed" and s.mode == "from_outstanding" and s.missing == ["principal", "start_date"]
    assert money_str(s.payment_c) == "1035.29" and len(s.rows) == 6 and s.rows[0].due == D("2026-02-10") and s.rows[-1].balance_c == 0
    assert s.total_interest_c is None and s.interest_paid_c is None and s.remaining_interest_c is not None
    assert s.rows[0].interest_c == 6000 and money_str(s.remaining_capital_c) == "6000.00"      # nothing due yet after the date of the capital


def test_it_works_on_a_loan_declared_without_the_newer_fields():
    from types import SimpleNamespace
    old = SimpleNamespace(id="old", kind="car_loan", principal=10000, start_date=D("2025-10-15"), end_date=D("2026-10-15"),
                          rate=SimpleNamespace(nominal=12.0, type="fixed"), insurance=None, deferral=None, monthly_payment=888.49)
    s = S.compute(old, TODAY)
    assert s.status == "computed" and money_str(s.payment_c) == "888.49"


@pytest.mark.parametrize("kw,err", [({"first_payment_date": D("2025-01-01")}, "before start_date"),
                                    ({"deferral": {"months": 12}}, "not shorter"),
                                    ({"odometer": [{"date": D("2026-01-01"), "km": 10}, {"date": D("2026-02-01"), "km": 5}]}, "must not decrease")])
def test_the_schema_refuses_inconsistent_loans(kw, err):
    with pytest.raises(Exception, match=err):
        lb(**kw)
