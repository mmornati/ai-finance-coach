"""E9-2: loan payments linked to the liability and the alerts built from them (missed / changed / extra payment, wrong account), with the
timing worked out by hand: a payment is due on the 5th, the grace period is 5 days and a debit up to 4 days early still counts, so the
payment due on 2026-10-05 is reported missing from 2026-10-11 (the 10th is still inside the grace period) and only when the account's data
are complete through 2026-10-10."""
from __future__ import annotations

import datetime as dt

from anhelpers import D, account, make_ds, tx
from coach.analytics.dataset import MemorySnapshot
from coach.loans import payments as P, schedule as S, service as LS
from coach.memory import schemas


def lb(**kw) -> schemas.Liability:
    base = dict(id="home", kind="mortgage", lender="Homebank", monthly_payment=1843.27, payment_match="^ECH PRET", payment_day=5)
    base.update(kw)
    return schemas.Liability(**base)


def pay(date, amount=1843.27, account_="ce", entity="ECH PRET"):
    return tx(date, -amount, "housing.mortgage", account_, entity=entity)


REGULAR = [pay(f"2026-{m:02d}-05") for m in range(3, 10)]                 # 2026-03-05 .. 2026-09-05: seven payments
ACCOUNTS = [account("ce", "CE main", purpose="main"), account("ob", "Other bank", purpose="main")]


def world(txs, loan, today, sync=None, **kw):
    mem = MemorySnapshot(liabilities=[("liabilities/home.yaml", loan)])
    return make_ds(txs, ACCOUNTS, today=D(today), memory=mem, last_sync=sync or {"ce": today}, **kw)


def alerts(ds, loan):
    return LS.alerts_of(ds, loan)


def test_the_payments_are_linked_by_the_label_and_listed_oldest_first():
    loan = lb()
    ds = world(REGULAR + [tx("2026-09-06", -12.0, "food.groceries", "ce", entity="BAKERY")], loan, "2026-09-20")
    obs = LS.observed_of(ds, loan)
    assert [p.date for p in obs] == [D(f"2026-{m:02d}-05") for m in range(3, 10)] and {p.amount_c for p in obs} == {184327}
    assert P.summary(obs)["count"] == 7 and P.summary(obs)["median_amount"] == "1843.27" and alerts(ds, loan) == []


def test_amount_match_tells_apart_two_loans_that_share_one_label():
    a = lb(id="car-a", payment_match="^CREDIT", amount_match={"amount": 172, "tolerance_pct": 3}, monthly_payment=172)
    b = lb(id="car-b", payment_match="^CREDIT", amount_match={"amount": 389, "tolerance_pct": 3}, monthly_payment=389)
    txs = [tx(f"2026-0{m}-15", -171.80, "debt.loan_repayment", "ce", entity="CREDIT") for m in range(3, 6)] + \
          [tx(f"2026-0{m}-05", -389.44, "debt.loan_repayment", "ce", entity="CREDIT") for m in range(3, 6)]
    ds = make_ds(txs, ACCOUNTS, today=D("2026-05-20"), memory=MemorySnapshot(liabilities=[("liabilities/a.yaml", a), ("liabilities/b.yaml", b)]))
    assert {p.amount_c for p in P.observed(ds, a)} == {17180} and {p.amount_c for p in P.observed(ds, b)} == {38944}


def test_a_missed_payment_is_reported_after_the_grace_period_and_not_before():
    loan = lb()
    before = alerts(world(REGULAR, loan, "2026-10-10"), loan)                 # due 2026-10-05 + 5 days grace = 10-10: still fine
    assert [a.type for a in before] == []
    ds = world(REGULAR, loan, "2026-10-11")
    (a,) = alerts(ds, loan)
    assert a.type == "missed_payment" and a.severity == "high" and a.expected_date == D("2026-10-05") and a.date == D("2026-10-05")
    assert "2026-10-05" in a.title
    # a debit seen inside the window [10-01, 10-10] means it was paid (four days early counts)
    early = world(REGULAR + [pay("2026-10-01")], loan, "2026-10-11")
    assert [x.type for x in alerts(early, loan)] == []
    late = world(REGULAR + [pay("2026-10-12")], loan, "2026-10-14")           # paid on the 12th: outside the grace window
    (m,) = [x for x in alerts(late, loan) if x.type == "missed_payment"]
    assert "later matching debit was seen on 2026-10-12" in m.body and m.evidence


def test_no_alert_when_the_account_data_are_not_known_to_be_complete():
    loan = lb()
    stale = make_ds(REGULAR, ACCOUNTS, today=D("2026-10-11"), memory=MemorySnapshot(liabilities=[("liabilities/home.yaml", loan)]))
    assert stale.coverage.of("ce").last == D("2026-09-05") and alerts(stale, loan) == []         # last data 09-05: October is not covered
    synced = world(REGULAR, loan, "2026-10-11", sync={"ce": "2026-10-09"})                        # synced the 9th: the 10th is not covered yet
    assert alerts(synced, loan) == []
    assert [a.type for a in alerts(world(REGULAR, loan, "2026-10-11", sync={"ce": "2026-10-10"}), loan)] == ["missed_payment"]


def test_an_old_missed_payment_and_an_ended_loan_are_not_reported():
    loan = lb()
    assert [a.type for a in alerts(world(REGULAR, loan, "2026-12-20"), loan) if a.date == D("2026-10-05")] == []   # older than the 75-day look-back
    ended = lb(end_date=D("2026-08-05"))
    assert alerts(world(REGULAR, ended, "2026-10-20"), ended) == []
    nothing = lb(payment_match=None)
    assert alerts(world(REGULAR, nothing, "2026-10-20"), nothing) == []


def test_missed_payments_follow_the_computed_schedule_when_there_is_one():
    # 10,000 at 12 % over 12 months from 2026-01-05, debited on the 5th: instalment 888.49; the 2026-05-05 instalment never leaves the account
    loan = lb(principal=10000, rate={"nominal": 12.0}, start_date=D("2026-01-05"), term_months=12, monthly_payment=888.49)
    txs = [pay(f"2026-{m:02d}-05", 888.49) for m in (2, 3, 4, 6)]
    ds = world(txs, loan, "2026-06-20")
    sch = LS.schedule_of(ds, loan)
    assert sch.status == "computed" and sch.rows[3].due == D("2026-05-05")
    (a,) = [x for x in alerts(ds, loan) if x.type == "missed_payment"]
    assert a.expected_date == D("2026-05-05") and a.expected_amount_c == 88849 and "schedule" in a.body


def test_a_changed_amount_is_reported_against_the_previous_payment():
    loan = lb()
    ds = world(REGULAR + [pay("2026-10-05", 1925.00)], loan, "2026-10-06")
    (a,) = alerts(ds, loan)
    # 1,925.00 vs 1843.27: 81.73 apart, more than 2 % (36.87) and more than 1 EUR
    assert a.type == "amount_changed" and a.amount_c == 192500 and a.expected_amount_c == 184327
    small = world(REGULAR + [pay("2026-10-05", 1855.00)], loan, "2026-10-06")                # 11.73 apart: inside the 2 % tolerance
    assert alerts(small, loan) == []


def test_an_amount_that_differs_from_the_schedule_is_a_low_alert():
    loan = lb(principal=10000, rate={"nominal": 12.0}, start_date=D("2026-01-05"), term_months=12, monthly_payment=888.49)
    txs = [pay(f"2026-{m:02d}-05", 950.00) for m in (2, 3, 4)]                                # steady 950.00 but the schedule says 888.49
    ds = world(txs, loan, "2026-04-20")
    (a,) = alerts(ds, loan)
    assert a.type == "amount_changed" and a.severity == "low" and a.expected_amount_c == 88849


def test_a_big_or_second_debit_is_a_possible_prepayment():
    loan = lb()
    big = world(REGULAR + [pay("2026-10-05"), pay("2026-10-12", 5000.00)], loan, "2026-10-14")
    (a,) = [x for x in alerts(big, loan) if x.type == "extra_payment"]
    assert a.amount_c == 500000 and "much larger" in a.body                                   # 5,000 >= 1.5 x 1843.27 and 100 EUR above it
    twice = world(REGULAR + [pay("2026-10-05"), pay("2026-10-20")], loan, "2026-10-22")
    (b,) = [x for x in alerts(twice, loan) if x.type == "extra_payment"]
    assert b.date == D("2026-10-20") and "already has its instalment" in b.body
    shifted = world(REGULAR + [pay("2026-10-20")], loan, "2026-10-22")                        # the only debit of October, 15 days late: not an extra
    assert [x.type for x in alerts(shifted, loan)] == ["missed_payment"]


def test_a_payment_from_another_account_is_reported_when_the_loan_names_its_account():
    loan = lb(debited_account="CE main")
    ds = world(REGULAR + [pay("2026-10-05", account_="ob")], loan, "2026-10-06")
    (a,) = alerts(ds, loan)
    assert a.type == "wrong_account" and "Other bank" in a.body and "CE main" in a.body
    unnamed = lb()
    assert alerts(world(REGULAR + [pay("2026-10-05", account_="ob")], unnamed, "2026-10-06"), unnamed) == []    # no account recorded: nothing to compare


def test_the_alerts_become_insight_cards_and_the_calendar_uses_the_schedule():
    loan = lb(principal=10000, rate={"nominal": 12.0}, start_date=D("2026-01-05"), term_months=12, monthly_payment=888.49,
              insurance={"monthly": 20})
    ds = world([pay(f"2026-{m:02d}-05", 908.49) for m in (2, 3, 4)], loan, "2026-06-20")
    cards = LS.loan_cards(ds)
    assert {c["subtype"] for c in cards} == {"missed_payment"} and all(c["kind"] == "loan" and c["id"].startswith("ins_") for c in cards)
    ids = [c["id"] for c in cards]
    assert ids == [c["id"] for c in LS.loan_cards(world([pay(f"2026-{m:02d}-05", 908.49) for m in (2, 3, 4)], loan, "2026-06-20"))]   # stable ids
    # instalments due in July 2026: 07-05 only, exactly 888.49 + 20.00 insurance
    up = LS.upcoming_payments(ds, loan, D("2026-06-21"), D("2026-07-31"))
    assert [(d, a, c) for d, a, c, _n in up] == [(D("2026-07-05"), 90849, "scheduled")]
