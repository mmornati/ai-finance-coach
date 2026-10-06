"""E4-6 cash-flow forecast: recurring items, variable spend, bands, negative balances, household, liabilities."""
import math
from types import SimpleNamespace

from anhelpers import D, account, make_ds, monthly, tx
from coach.analytics import forecast as fc
from coach.analytics.dataset import MemorySnapshot

HIST = {"a": ("2026-03-01", None)}                 # history begins on 1 March: April-September are covered months
SYNC = {"a": "2026-10-04", "b": "2026-10-04"}


def scenario(groceries=(300,) * 6, extra=(), balance=1000.0, **kw):
    """rent -500 on the 5th and salary +2000 on the 25th (April-September) + irregular shopping (one tx per merchant)."""
    txs = (monthly("2026-04-05", 6, -500.0, "housing.rent", entity="LANDLORD")
           + monthly("2026-04-25", 6, 2000.0, "income.salary", entity="ACME")
           + [tx(f"2026-{4 + i:02d}-{3 + 5 * j:02d}", -g / 5, "food.groceries", entity=f"SHOP{i}{j}")     # 5 small shops a month
              for i, g in enumerate(groceries) for j in range(5)]
           + list(extra))
    return make_ds(txs, balances={"a": balance}, history=HIST, last_sync=SYNC, **kw)


def test_milestones_hand_computed():
    ds0 = scenario()
    r = fc.forecast(ds0, 90)
    f = r.accounts[0]
    per_day = 30000 / 30.4375                                           # 300 EUR a month spread evenly
    expect = lambda events, days: round(100000 + events - days * per_day)   # noqa: E731
    got = {m.days: m.balance_c for m in f.milestones}
    # day 30 = 2026-11-03: rent 10-05, salary 10-25.  day 60 = 12-03: + rent 11-05, salary 11-25.  day 90 = 01-02: + 12-05, 12-25
    assert abs(got[30] - expect(-50000 + 200000, 30)) <= 1
    assert abs(got[60] - expect(-100000 + 400000, 60)) <= 1
    assert abs(got[90] - expect(-150000 + 3 * 200000, 90)) <= 1
    assert f.variable_monthly_c == 30000 and f.variable_sigma_c == 0 and f.start_balance_c == 100000
    assert [m.date for m in f.milestones] == [D("2026-11-03"), D("2026-12-03"), D("2027-01-02")]
    # no variable-amount series and sigma 0: the band is the expectation itself
    assert all(m.low_c == m.balance_c == m.high_c for m in f.milestones)
    assert f.first_negative is None and f.first_at_risk is None and f.flags == []
    assert {e.ref for e in f.events} == {x.id for x in fc.detect_recurring(ds0).series}      # nothing else was added
    assert len(f.events) == 6 and len(f.points) == 90 and f.points[0].date == D("2026-10-05")


def test_band_follows_the_spread_of_monthly_variable_spend():
    r = fc.forecast(scenario(groceries=(200, 400, 300, 250, 350, 300)), 30)
    f = r.accounts[0]
    assert f.variable_monthly_c == 30000
    sigma = round(math.sqrt(5000) * 100)                               # sample standard deviation of the six months
    assert f.variable_sigma_c == sigma
    m = f.milestones[0]
    band = 1.28 * sigma * math.sqrt(30 / 30.4375)
    assert abs((m.high_c - m.balance_c) - band) <= 1 and abs((m.balance_c - m.low_c) - band) <= 1


def test_negative_balance_is_flagged_with_its_first_day():
    r = fc.forecast(scenario(balance=100.0), 30)
    f = r.accounts[0]
    assert f.first_negative == D("2026-10-05") and "projected_negative" in f.flags
    assert f.min_balance_c < 0 and f.min_date == D("2026-10-25")                      # lowest just before the salary (Sunday 25th: booked Monday 26th)
    assert "projected_negative" in r.household.flags and r.household.first_negative == D("2026-10-05")


def test_at_risk_when_only_the_low_band_goes_negative():
    r = fc.forecast(scenario(groceries=(100, 600, 150, 700, 100, 650), balance=900.0), 30)
    f = r.accounts[0]
    assert f.first_negative is None and f.first_at_risk is not None and "at_risk" in f.flags


def test_household_is_the_sum_of_accounts_and_internal_transfers_cancel():
    b_hist = {"b": ("2026-03-01", None)}
    transfers_a = monthly("2026-04-02", 6, -300.0, "transfer.internal", entity="TOCARD")
    transfers_b = monthly("2026-04-02", 6, 300.0, "transfer.internal", entity="FROMMAIN", account="b")
    cards = monthly("2026-04-09", 6, -4.0, "food.cafes_bars", account="b", entity="X")
    for i, t in enumerate(cards):
        cards[i] = tx(t.date, -4.0 - i, "food.cafes_bars", "b", entity=f"CAFE{i}")
    base = scenario(extra=[tx("2026-03-01", -1.0, "fees.bank_fees", "b", entity="FEE")], )
    accs = [account(), account("b", "Card", purpose="cards")]
    with_tr = make_ds(base.txs + transfers_a + transfers_b + cards, accounts=accs, balances={"a": 1000.0, "b": 200.0},
                      history={**HIST, **b_hist}, last_sync=SYNC)
    without = make_ds(base.txs + cards, accounts=accs, balances={"a": 1000.0, "b": 200.0},
                      history={**HIST, **b_hist}, last_sync=SYNC)
    r1, r2 = fc.forecast(with_tr, 90), fc.forecast(without, 90)
    a1, b1 = (next(x for x in r1.accounts if x.label == n) for n in ("Main", "Card"))
    assert a1.milestones[0].balance_c < next(x for x in r2.accounts if x.label == "Main").milestones[0].balance_c    # A pays
    assert b1.milestones[0].balance_c > next(x for x in r2.accounts if x.label == "Card").milestones[0].balance_c    # B receives
    for m1, m2 in zip(r1.household.milestones, r2.household.milestones):
        assert m1.balance_c == m2.balance_c                                    # at household level they net to zero
    assert r1.household.start_balance_c == 120000
    assert sum(x.milestones[0].balance_c for x in r1.accounts) == r1.household.milestones[0].balance_c


def test_stale_balance_overdue_item_and_account_without_balance():
    ds = scenario(balance=(1000.0, "2026-09-28"))
    f = fc.forecast(ds, 30).accounts[0]
    assert "balance_stale" in f.flags
    overdue = monthly("2026-05-01", 5, -40.0, "subscriptions.memberships", entity="GYM")       # last 2026-09-01: next 10-01
    ds2 = make_ds(scenario().txs + overdue, balances={"a": 1000.0}, history=HIST, last_sync=SYNC)
    ev = [e for e in fc.forecast(ds2, 30).accounts[0].events if e.label == "GYM"]
    assert ev and ev[0].overdue is True and ev[0].date == D("2026-10-05")                      # placed on the first projected day
    nobal = make_ds(scenario().txs, history=HIST, last_sync=SYNC)
    r = fc.forecast(nobal, 30)
    assert "no_balance" in r.accounts[0].flags and r.household.start_balance_c is None and r.household.milestones == []


def test_liability_without_a_recurring_series_is_assumed_on_its_start_day():
    loan = SimpleNamespace(id="car-loan", monthly_payment=300.0, start_date=D("2025-01-10"), end_date=D("2026-12-10"),
                           debited_from="Main", payment_match="^NOTHING", kind="car_loan")
    mem = MemorySnapshot(liabilities=[("liabilities/car-loan.yaml", loan)])
    ds = scenario(memory=mem)
    ev = [e for e in fc.forecast(ds, 90).accounts[0].events if e.source == "liability"]
    assert [(e.date, e.amount_c, e.certainty) for e in ev] == [(D("2026-10-10"), -30000, "assumed"), (D("2026-11-10"), -30000, "assumed"),
                                                              (D("2026-12-10"), -30000, "assumed")]       # stops at end_date
    # when a recurring series already explains the payments, nothing is added
    seen = monthly("2026-04-10", 6, -300.0, "debt.loan_repayment", entity="LOANCO")
    mem2 = MemorySnapshot(liabilities=[("l", SimpleNamespace(id="car-loan", monthly_payment=300.0, start_date=D("2025-01-10"),
                                                              end_date=None, debited_from="Main", payment_match="^LOANCO", kind="car_loan"))])
    r = fc.forecast(make_ds(scenario().txs + seen, memory=mem2, balances={"a": 1000.0}, history=HIST, last_sync=SYNC), 90)
    assert [e for e in r.accounts[0].events if e.source == "liability"] == []
    # unresolved account: household only
    stray = SimpleNamespace(id="stray", monthly_payment=100.0, start_date=D("2025-01-10"), end_date=None, debited_from="somewhere else",
                            payment_match=None, kind="consumer_loan")
    r3 = fc.forecast(scenario(memory=MemorySnapshot(liabilities=[("l", stray)])), 30)
    assert any(e.source == "liability" and e.account is None for e in r3.household.events)
    base = fc.forecast(scenario(), 30).household.milestones[0].balance_c
    assert r3.household.milestones[0].balance_c == base - 10000                    # one instalment on 2026-10-10


def test_savings_accounts_get_no_variable_flows_and_short_history_none():
    sav = account("s", "Pocket", purpose="savings")
    txs = scenario().txs + [tx(f"2026-0{m}-03", -50.0, "transfer.internal", "s", entity=f"M{m}") for m in range(4, 9)]
    ds = make_ds(txs, accounts=[account(), sav], balances={"a": 1000.0, "s": 500.0}, history={**HIST, "s": ("2026-03-01", None)},
                 last_sync={**SYNC, "s": "2026-10-04"})
    s = next(x for x in fc.forecast(ds, 30).accounts if x.label == "Pocket")
    assert s.variable_monthly_c == 0 and s.milestones[0].balance_c == 50000
    newbie = make_ds([tx("2026-09-10", -30.0, "food.groceries", entity="Z")], balances={"a": 100.0}, last_sync=SYNC)
    f = fc.forecast(newbie, 30).accounts[0]
    assert f.variable_monthly_c == 0 and "no_variable_history" in f.flags              # one covered month is not a history


def test_forecast_is_reproducible_and_json_safe():
    import json
    ds = scenario()
    r1, r2 = fc.forecast(ds, 90), fc.forecast(ds, 90)
    assert r1.to_dict() == r2.to_dict()
    d = json.loads(json.dumps(r1.to_dict()))
    assert d["household"]["milestones"][0]["balance"].count(".") == 1 and d["horizon_days"] == 90
    assert fc.forecast(scenario(), 30, points=False).accounts[0].points == []


def test_a_liability_with_a_computable_schedule_is_forecast_from_it_exactly():
    """E9-3: 10,000 at 12 % over 12 months from 2026-08-10 (first due 2026-09-10): instalment 888.49 + 20.00 insurance = 908.49 debited."""
    from coach.memory import schemas
    loan = schemas.Liability(id="car-loan", kind="car_loan", principal=10000, start_date=D("2026-08-10"), term_months=12,
                             rate={"nominal": 12.0}, insurance={"monthly": 20}, debited_account="Main", payment_match="^NOTHING")
    ds = scenario(memory=MemorySnapshot(liabilities=[("liabilities/car-loan.yaml", loan)]))
    ev = [e for e in fc.forecast(ds, 90).accounts[0].events if e.source == "liability"]
    assert [(e.date, e.amount_c, e.certainty) for e in ev] == [(D("2026-10-10"), -90849, "scheduled"), (D("2026-11-10"), -90849, "scheduled"),
                                                              (D("2026-12-10"), -90849, "scheduled")]
    assert any("amortization schedule" in a for a in fc.forecast(ds, 90).assumptions)
    # the last instalment is smaller (the balance is cleared): 2027-08-10 is k=12 of the 12-month loan; a 400-day horizon reaches it
    last = [e for e in fc.forecast(ds, 400, points=False).accounts[0].events if e.source == "liability"][-1]
    assert last.date == D("2027-08-10") and last.amount_c == -(88847 + 2000)          # 879.67 + 8.80 interest, and the insurance
    # without the schedule fields the flat declared payment is used, flagged as assumed
    flat = schemas.Liability(id="car-loan", kind="car_loan", monthly_payment=300, start_date=D("2025-01-10"), end_date=D("2026-12-10"),
                             debited_account="Main")
    ev2 = [e for e in fc.forecast(scenario(memory=MemorySnapshot(liabilities=[("l", flat)])), 90).accounts[0].events if e.source == "liability"]
    assert {e.certainty for e in ev2} == {"assumed"} and [e.amount_c for e in ev2] == [-30000] * 3
