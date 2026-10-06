"""MJ-2: a declared capital (a statement, dated) that differs from the theoretical table beyond the tolerance WINS: it is rolled forward with the
loan's rate and instalments (`declared_rolled`), the theoretical table stays only as the check. Reference: 10,000 EUR at 12 % over 12 months from
2025-10-15 (theory: 7,610.80 due after the 2026-01-15 instalment). Declared: 6000.00 on 2026-01-16 (an early repayment, say).
Rolled: 9 instalments left (02-15 .. 10-15), annuity of 6,000 over 9 months at 1 % = 700.44; interest 60.00, 53.60, 47.13 ...; balance after 3 = 4,059.41."""
from __future__ import annotations

import datetime as dt

from anhelpers import D, account, make_ds, tx
from coach.analytics import cashflow as cf, forecast as fc
from coach.analytics.dataset import MemorySnapshot
from coach.analytics.common import money_str
from coach.loans import networth as NW, schedule as S, service as LS
from coach.memory import schemas


def lb(**kw):
    base = dict(id="car", kind="car_loan", principal=10000, start_date=D("2025-10-15"), term_months=12, rate={"nominal": 12.0},
                outstanding=6000, outstanding_as_of=D("2026-01-16"))
    base.update(kw)
    return schemas.Liability(**base)


def test_the_declared_capital_is_rolled_forward_and_the_theory_is_only_the_check():
    s = S.compute(lb(), D("2026-04-20"))
    assert s.mode == "from_outstanding" and s.source == "declared_rolled" and s.status == "computed"
    assert money_str(s.payment_c) == "700.44" and len(s.rows) == 9 and s.rows[0].due == D("2026-02-15") and s.rows[-1].balance_c == 0
    assert [money_str(r.interest_c) for r in s.rows[:3]] == ["60.00", "53.60", "47.13"]
    # three instalments are due by 2026-04-20 (02-15, 03-15, 04-15): 4,059.41 left; made + left = the rows of the table
    assert money_str(s.remaining_capital_c) == "4059.41" and s.payments_made == 3 and s.remaining_instalments == 6
    assert s.payments_made + s.remaining_instalments == len(s.rows)
    c = s.outstanding_check
    assert c["status"] == "differs" and c["declared"] == "6000.00" and c["computed"] == "7610.80" and c["theoretical_capital_today"]
    assert "update the loan" in c["hint"]


def test_a_declared_capital_close_to_the_table_changes_nothing():
    s = S.compute(lb(outstanding=7650), D("2026-04-20"))                   # 39.20 from the table's 7,610.80: inside the tolerance
    assert s.mode == "from_principal" and s.source == "schedule" and s.outstanding_check is None


def test_when_it_cannot_be_rolled_forward_the_declared_figure_is_used_as_it_is():
    s = S.compute(lb(outstanding_as_of=D("2026-12-01")), D("2027-01-10"))   # declared after the end of the table: nothing to roll
    assert s.source == "declared" and s.mode == "from_principal" and money_str(s.remaining_capital_c) == "6000.00"
    assert s.outstanding_check["status"] == "differs" and any("cannot be rolled forward" in a for a in s.assumptions)


def test_net_worth_uses_the_declared_rolled_capital_and_says_so():
    mem = MemorySnapshot(liabilities=[("l", lb())])
    ds = make_ds([tx("2026-03-01", -1, "food.groceries")], [account()], today=D("2026-04-20"), memory=mem, balances={"a": 100.0})
    nw = NW.build(ds, ds.today, schedules=LS.schedules(ds))
    c = next(x for x in nw.components if x.type == "liability")
    assert c.amount_c == 405941 and c.source == "declared_rolled" and "rolled forward" in c.note
    assert "update the loan" in c.note and nw.liabilities_c == 405941


def test_the_forecast_and_the_cash_flow_follow_the_declared_capital():
    mem = MemorySnapshot(liabilities=[("l", lb(payment_match="^LOANCO"))])
    pay = [tx("2026-02-16", -700.44, "debt.loan_repayment", entity="LOANCO"), tx("2026-03-16", -700.44, "debt.loan_repayment", entity="LOANCO")]
    ds = make_ds(pay + [tx("2026-03-02", 3000, "income.salary", entity="ACME")], [account()], today=D("2026-04-01"), memory=mem, balances={"a": 5000.0},
                 history={"a": ("2026-01-01", None)}, last_sync={"a": "2026-04-01"})
    # cash flow: the 2026-03-16 debit is instalment 2 of the rolled table (due 03-15): 700.44 - 53.60 = 646.84 of principal
    m = next(x for x in cf.cashflow(ds, months=2).household.months if x.month == "2026-03")
    assert m.debt_service_c == 70044 and m.loan_principal_c == 64684
    # forecast of a loan whose payments the bank data do not link: the rolled instalments (04-15, 05-15 ...) of 700.44
    mem2 = MemorySnapshot(liabilities=[("l", lb(debited_account="Main"))])
    ds2 = make_ds([tx("2026-03-02", 3000, "income.salary", entity="ACME")], [account()], today=D("2026-04-01"), memory=mem2, balances={"a": 5000.0},
                  history={"a": ("2026-01-01", None)}, last_sync={"a": "2026-04-01"})
    ev = [e for e in fc.forecast(ds2, 60).accounts[0].events if e.source == "liability"]
    assert [(e.date, e.amount_c) for e in ev] == [(D("2026-04-15"), -70044), (D("2026-05-15"), -70044)]


def test_the_difference_is_surfaced_as_an_insight_card():
    mem = MemorySnapshot(liabilities=[("l", lb(lender="Bank"))])
    ds = make_ds([tx("2026-03-01", -1, "food.groceries")], [account()], today=D("2026-04-20"), memory=mem)
    (card,) = [c for c in LS.loan_cards(ds) if c["subtype"] == "capital_differs"]
    assert card["kind"] == "loan" and "differs from the declared capital" in card["title"] and "early repayment" in card["body"] and card["amount"] == "6000.00"
