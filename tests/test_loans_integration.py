"""E9-3 feeding the rest of the analytics: the principal part of the instalments (savings rate incl. principal), the what-if prepayment on the
exact schedule, the mortgage check, the insights feed and the calendar. Hand-computed on 10,000 EUR at 12 % over 12 months (instalment 888.49)."""
from __future__ import annotations

import datetime as dt

from anhelpers import D, TODAY, account, make_ds, monthly, tx
from coach.analytics import cashflow as cf
from coach.analytics.dataset import MemorySnapshot
from coach.analytics.upcoming import calendar_items
from coach.loans import service as LS
from coach.memory import schemas
from coach.skills import whatif as WI


def loan(**kw):
    base = dict(id="car-loan", kind="car_loan", lender="Lender", principal=10000, start_date=D("2026-07-15"), term_months=12,
                rate={"nominal": 12.0}, monthly_payment=888.49, payment_match="^LOANCO")
    base.update(kw)
    return schemas.Liability(**base)


def world(lb, extra=()):
    txs = monthly("2026-01-25", 9, 3000, "income.salary", entity="ACME")
    txs += [tx("2026-08-16", -888.49, "debt.loan_repayment", entity="LOANCO"), tx("2026-09-16", -888.49, "debt.loan_repayment", entity="LOANCO")]
    txs += list(extra)
    return make_ds(txs, today=TODAY, history={"a": ("2026-01-01", None)}, last_sync={"a": "2026-10-04"}, balances={"a": 5000.0},
                   memory=MemorySnapshot(liabilities=[("liabilities/car-loan.yaml", lb)]))


def test_the_principal_part_of_an_instalment_comes_from_the_schedule():
    ds = world(loan())
    res = cf.cashflow(ds, months=2)
    sept = next(m for m in res.household.months if m.month == "2026-09")
    # the 2026-09-16 debit is instalment 2 (due 2026-09-15): interest 8,000-ish... 9,211.51 x 1 % = 92.12, principal 888.49 - 92.12 = 796.37
    assert sept.debt_service_c == 88849 and sept.loan_principal_c == 79637
    # savings rate including the principal: net = 3,000 income - 888.49 spent = 2,111.51; + 796.37 principal = 2,907.88 over 3,000 income
    assert sept.income_c == 300000 and sept.savings_rate_incl_principal == round((300000 - 88849 + 79637) / 300000, 4) == 0.9693
    aug = next(m for m in res.household.months if m.month == "2026-08")
    assert aug.loan_principal_c == 78849                                                    # instalment 1: 888.49 - 100.00 = 788.49


def test_without_a_schedule_the_rough_estimate_is_still_used():
    rough = loan(principal=None, start_date=None, term_months=None, outstanding=9000, outstanding_as_of=D("2026-09-01"))
    ds = world(rough)
    sept = next(m for m in cf.cashflow(ds, months=2).household.months if m.month == "2026-09")
    assert sept.loan_principal_c == 88849 - 9000                                           # 9,000 x 12 % / 12 = 90.00 of interest


def test_the_what_if_prepayment_uses_the_loans_own_schedule():
    ds = world(loan())
    r = WI.what_if(ds, {"days": 90, "changes": [{"type": "prepay_loan", "liability": "car-loan", "amount": 2000, "date": "2026-11-15"}]})
    c = r["changes"][0]
    # applied before the 2026-11-15 instalment: 7,610.80 due after instalment 3 (2026-10-15), 9 instalments left; 5,610.80 at 888.49 ends two
    # months earlier and saves 385.59 - 215.02 = 170.57 of interest
    assert (c["capital_before"], c["capital_after"], c["months_saved"], c["lifetime_interest_saved"], c["approximate"]) == \
        ("7610.80", "5610.80", 2, "170.57", False)
    assert c["method"] == "amortization" and any("amortization schedule" in n for n in c["notes"])
    term = WI.what_if(ds, {"days": 90, "changes": [{"type": "prepay_loan", "liability": "car-loan", "amount": 2000, "date": "2026-11-15",
                                                    "keep": "term"}]})["changes"][0]
    assert term["instalment_before"] == "888.49" and term["instalment_after"] == "655.01"


def test_a_deferral_changes_the_what_if_numbers():
    ds = world(loan(deferral={"months": 2, "kind": "partial"}))
    # 2 interest-only months (08-15, 09-15), then 10 instalments of 1,055.82 from 2026-10-15: on 2026-10-10 the capital is still 10,000.00
    # (the old flat approach would have said 8,415.14 after two annuity instalments) and 10 instalments remain
    r = WI.what_if(ds, {"days": 90, "changes": [{"type": "prepay_loan", "liability": "car-loan", "amount": 1000, "date": "2026-10-10"}]})
    c = r["changes"][0]
    assert c["capital_before"] == "10000.00" and c["instalment_before"] == "1055.82"
    after = WI.what_if(ds, {"days": 90, "changes": [{"type": "prepay_loan", "liability": "car-loan", "amount": 1000, "date": "2026-10-20"}]})
    assert after["changes"][0]["capital_before"] == "9044.18"                # after the first regular instalment of 2026-10-15 (955.82 of principal)


def test_the_insights_feed_and_the_calendar_carry_the_loan_alerts_and_the_schedule():
    from coach.api import views
    from types import SimpleNamespace
    from coach.analytics import anomalies, budgets, forecast as fc, pricechanges
    lb = loan()
    ds = world(lb, extra=[])                                    # the 2026-10-15 instalment is due after today: no alert yet
    rec_ = LS.schedules(ds)
    assert rec_["car-loan"].status == "computed"
    from coach.analytics.recurring import detect_recurring
    rec = detect_recurring(ds)
    cards = views.build_insights(ds, SimpleNamespace(anomalies=[]), SimpleNamespace(changes=[]), fc.forecast(ds, 30), budgets.budget_status(ds, recurring=rec), rec)
    assert [c for c in cards if c["kind"] == "loan"] == []
    items = calendar_items(ds, 60).items
    loan_items = [(i.date, i.amount_c, i.certainty) for i in items if i.source == "liability" and i.kind == "loan_payment"]
    assert loan_items[:2] == [(D("2026-10-15"), -88849, "scheduled"), (D("2026-11-15"), -88849, "scheduled")]
    ended = calendar_items(world(loan(end_date=D("2026-10-31"))), 60).items
    assert [i.kind for i in ended if i.source == "liability"] == ["loan_payment", "loan_end"]
    # a missed instalment shows up as a card (due 2026-09-15 paid on the 16th in the world above; remove it)
    ds2 = make_ds(monthly("2026-01-25", 9, 3000, "income.salary", entity="ACME") + [tx("2026-08-16", -888.49, "debt.loan_repayment", entity="LOANCO")],
                  today=TODAY, history={"a": ("2026-01-01", None)}, last_sync={"a": "2026-10-04"}, balances={"a": 5000.0},
                  memory=MemorySnapshot(liabilities=[("liabilities/car-loan.yaml", lb)]))
    rec2 = detect_recurring(ds2)
    cards2 = views.build_insights(ds2, SimpleNamespace(anomalies=[]), SimpleNamespace(changes=[]), fc.forecast(ds2, 30),
                                  budgets.budget_status(ds2, recurring=rec2), rec2)
    assert [c["subtype"] for c in cards2 if c["kind"] == "loan"] == ["missed_payment"]
