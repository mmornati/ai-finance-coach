"""E4-3 recurring detection and E4-4 price changes: cadences, tolerances, status, links, stability, persistence."""
import datetime as dt
import random
from types import SimpleNamespace

import pytest

from anhelpers import D, account, make_ds
from anhelpers import monthly as _monthly, tx as _tx
from coach.analytics import pricechanges, recurring
from coach.analytics.common import add_months
from coach.analytics.dataset import MemorySnapshot
from coach.analytics.settings import AnalyticsSettings
from coach.db import connect


def tx(date, amount, category="subscriptions.memberships", *a, **k):
    return _tx(date, amount, category, *a, **k)


def monthly(start, n, amount, category="subscriptions.memberships", **k):
    return _monthly(start, n, amount, category, **k)


def series_of(txs, **kw):
    return recurring.detect_recurring(make_ds(txs, **kw)).series


def every(days, n, amount, start="2026-01-02", category="subscriptions.memberships", **kw):
    s = D(start)
    return [tx(s + dt.timedelta(days=days * i), amount, category, **kw) for i in range(n)]


@pytest.mark.parametrize("name,txs", [
    ("weekly", every(7, 8, -10.0)),
    ("biweekly", every(14, 6, -20.0)),
    ("monthly", monthly("2026-01-05", 6, -12.99)),
    ("quarterly", monthly("2025-01-10", 6, -90.0)[:0] + [tx(add_months(D("2025-01-10"), 3 * i), -90.0, "insurance.other") for i in range(5)]),
    ("semiannual", [tx(add_months(D("2024-02-12"), 6 * i), -55.0, "insurance.other") for i in range(4)]),
    ("yearly", [tx(add_months(D("2023-11-20"), 12 * i), -120.0, "insurance.other") for i in range(3)]),
])
def test_cadences_are_detected(name, txs):
    (s,) = series_of(txs)
    assert s.cadence == name and s.amount_mode == "fixed" and s.n_occurrences == len(txs)


def test_bimonthly_utility_bills_with_a_variable_amount():
    amounts = [-60.0, -95.0, -120.0, -75.0, -140.0]
    txs = [tx(add_months(D("2026-01-12"), 2 * i), a, "housing.water", entity="WATERCO") for i, a in enumerate(amounts)]
    (s,) = series_of(txs)
    assert s.cadence == "bimonthly" and s.amount_mode == "variable" and s.kind == "expense"
    assert (s.amount_low_c, s.amount_high_c) == (-6000, -14000)               # smallest / largest of the last six
    assert s.expected_amount_c == -9500                                       # their median
    assert s.yearly_cost_c == 57000                                           # 95 x 6 bills a year
    # five bills within +-15 % of each other would have been 'fixed' (see the next test for the tolerance)
    steady = [tx(add_months(D("2026-01-12"), 2 * i), a, "housing.water", entity="WATERCO2") for i, a in enumerate([-84.0, -91.5, -77.2, -88.4, -90.0])]
    assert series_of(steady)[0].amount_mode == "fixed"


def test_a_variable_amount_outside_a_utility_category_is_not_recurring():
    amounts = [-84.0, -191.5, -37.2, -302.9, -58.4]
    txs = [tx(add_months(D("2026-01-12"), i), a, "food.restaurants", entity="BISTRO") for i, a in enumerate(amounts)]
    assert series_of(txs) == []


def test_monthly_tolerance_is_five_days_and_amount_tolerance_fifteen_percent():
    ok = [tx("2026-01-05", -50), tx("2026-02-09", -50), tx("2026-03-06", -50), tx("2026-04-10", -50)]   # gaps 35 / 25 / 35
    assert series_of(ok)[0].cadence == "monthly"
    off = [tx("2026-01-05", -50), tx("2026-02-17", -50), tx("2026-03-31", -50), tx("2026-05-13", -50)]  # gaps 43
    assert series_of(off) == []
    near = monthly("2026-01-05", 5, -100.0)
    near[1] = tx("2026-02-05", -114.0)                                                                  # +14 %: inside
    assert series_of(near)[0].amount_mode == "fixed"
    far = monthly("2026-01-05", 6, -100.0, "food.restaurants")
    far[1], far[3], far[4] = tx("2026-02-05", -130.0, "food.restaurants"), tx("2026-04-05", -140.0, "food.restaurants"), \
        tx("2026-05-05", -60.0, "food.restaurants")
    assert all(s.n_occurrences <= 3 for s in series_of(far))                                            # not one fixed series


def test_amount_tolerance_is_configurable():
    txs = monthly("2026-01-05", 5, -100.0)
    txs[1], txs[2] = tx("2026-02-05", -112.0), tx("2026-03-05", -88.0)
    tight = AnalyticsSettings(recurring_amount_tolerance=0.05)
    assert len(recurring.detect_recurring(make_ds(txs, settings=tight)).series) == 0 \
        or recurring.detect_recurring(make_ds(txs, settings=tight)).series[0].n_occurrences < 5
    assert recurring.detect_recurring(make_ds(txs)).series[0].n_occurrences == 5


def test_two_occurrences_are_not_enough_except_yearly_which_is_low_confidence():
    assert series_of(monthly("2026-01-05", 2, -10.0)) == []
    two_years = [tx("2025-03-10", -240.0, "insurance.other"), tx("2026-03-12", -240.0, "insurance.other")]
    (s,) = series_of(two_years)
    assert s.cadence == "yearly" and s.confidence <= 0.5 and s.confidence_label == "low" and s.n_occurrences == 2
    # a pair of yearly purchases in a discretionary category is a coincidence, not a series
    assert series_of([tx("2025-03-10", -40.0, "food.restaurants"), tx("2026-03-12", -40.0, "food.restaurants")]) == []


def test_next_expected_expected_amount_range_and_yearly_cost():
    (s,) = series_of(monthly("2026-05-31", 5, -9.99, "subscriptions.video_streaming", entity="STREAMBOX"))
    # paid on the last day of the month (31 May ... 30 Sep): 31 October is a Saturday, booked on Monday 2 November
    assert s.last_date == D("2026-09-30") and s.end_of_month is True and s.next_expected == D("2026-11-02")
    assert s.expected_amount_c == -999 and (s.amount_low_c, s.amount_high_c) == (-999, -999)
    assert s.yearly_cost_c == 11988 and s.status == "active" and s.overdue_days == 0 and s.kind == "expense"
    (m,) = series_of([tx("2025-11-30", -5), tx("2025-12-31", -5), tx("2026-01-31", -5), tx("2026-02-28", -5)], today=D("2026-03-10"))
    assert m.next_expected == D("2026-03-31") and m.end_of_month                       # end-of-month payer: 31 March
    (n,) = series_of([tx("2025-11-30", -5), tx("2025-12-30", -5), tx("2026-01-30", -5), tx("2026-02-28", -5)], today=D("2026-03-10"))
    assert n.day_of_month == 30 and not n.end_of_month and n.next_expected == D("2026-03-30")   # the short February does not stick


def test_status_ended_after_one_and_a_half_cadences_and_overdue_days():
    old = monthly("2025-10-05", 4, -10.0, entity="OLDSVC")   # last 2026-01-05: more than 45 days before 2026-10-04
    (s,) = series_of(old)
    assert s.status == "ended" and s.next_expected is None
    late = monthly("2026-05-01", 5, -10.0, entity="LATESVC")  # last 2026-09-01, next expected 2026-10-01: 3 days overdue
    (t,) = series_of(late)
    assert t.status == "active" and t.next_expected == D("2026-10-01") and t.overdue_days == 3
    res = recurring.detect_recurring(make_ds(old + late))
    assert [x.status for x in res.series] == ["active", "ended"] and res.counts["active"] == 1 and res.counts["ended"] == 1
    assert recurring.detect_recurring(make_ds(old + late), include_ended=False).counts["ended"] == 0


def test_income_transfer_and_saving_kinds():
    txs = (monthly("2026-04-25", 6, 2500.0, "income.salary", entity="ACME")
           + monthly("2026-04-02", 6, -300.0, "transfer.internal", entity="TOPUP")
           + monthly("2026-04-03", 6, -100.0, "transfer.internal", entity="PLAN", tags=["savings"]))
    kinds = {s.entity: s.kind for s in series_of(txs)}
    assert kinds == {"ACME": "income", "TOPUP": "transfer", "PLAN": "saving"}


def test_two_subscriptions_of_one_merchant_are_two_series_and_ids_are_stable():
    txs = [tx(add_months(D("2026-01-05"), i), -9.99, "subscriptions.software_cloud", entity="APPSTORE") for i in range(6)] \
        + [tx(add_months(D("2026-01-20"), i), -29.99, "subscriptions.software_cloud", entity="APPSTORE") for i in range(6)]
    res = series_of(txs)
    assert sorted(s.expected_amount_c for s in res) == [-2999, -999] and len({s.id for s in res}) == 2
    shuffled = list(txs)
    random.Random(1).shuffle(shuffled)
    assert [s.id for s in series_of(shuffled)] == [s.id for s in res]                       # order of input is irrelevant
    assert [s.to_dict() for s in series_of(txs)] == [s.to_dict() for s in res]              # reproducible


def test_variants_of_one_descriptor_that_alternate_are_one_series():
    # the shape of a real case: the bank alternates between two descriptors of one service
    paris = set(range(12)) | {18, 22}
    txs = [tx(add_months(D("2024-10-21"), i), -14.99, "subscriptions.video_streaming",
              entity="VIDEOBOX PARIS" if i in paris else "VIDEOBOX AMSTERDAM") for i in range(24)]
    (s,) = series_of(txs)
    assert s.n_occurrences == 24 and s.cadence == "monthly" and s.entity == "VIDEOBOX AMSTERDAM"
    # two different services at the same price on different days stay apart
    two = [tx(add_months(D("2026-01-05"), i), -9.99, "subscriptions.video_streaming", entity="ONE") for i in range(6)] \
        + [tx(add_months(D("2026-01-20"), i), -9.99, "subscriptions.video_streaming", entity="TWO") for i in range(6)]
    assert sorted(x.entity for x in series_of(two)) == ["ONE", "TWO"]


def test_series_are_per_account_and_direction():
    a = monthly("2026-01-05", 5, -10.0, entity="X", account="a")
    b = monthly("2026-01-05", 5, -10.0, entity="X", account="b")
    ds = make_ds(a + b, accounts=[account("a", "A"), account("b", "B")])
    assert sorted(s.account for s in recurring.detect_recurring(ds).series) == ["a", "b"]


def test_sepa_creditor_id_groups_differently_named_payments():
    txs = [tx(add_months(D("2026-01-05"), i), -30.0, "housing.energy", entity=f"POWER {i}", key=f"k{i}") for i in range(5)]
    meta = {f"k{i}": ("FR12ZZZ123456", "MANDATE-1") for i in range(5)}
    assert series_of(txs) == []                                    # five different names
    (s,) = series_of(txs, meta=meta)
    assert s.n_occurrences == 5 and s.key.startswith("sepa:FR12ZZZ123456")


def test_links_to_contracts_and_liabilities_and_the_missing_contract_feed():
    contract = SimpleNamespace(id="stream-plan", merchant_match="^STREAMBOX", payment_match=None)
    loan = SimpleNamespace(id="home-loan", payment_match="^HOMEBANK", merchant_match=None)
    mem = MemorySnapshot(contracts=[("contracts/stream-plan.yaml", contract)], liabilities=[("liabilities/home-loan.yaml", loan)])
    txs = (monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
           + monthly("2026-04-06", 6, -1500.0, "housing.mortgage", entity="HOMEBANK")
           + monthly("2026-04-07", 6, -25.0, "subscriptions.music_streaming", entity="TUNEBOX"))
    res = recurring.detect_recurring(make_ds(txs, memory=mem))
    by = {s.entity: s for s in res.series}
    assert [(l.kind, l.id) for l in by["STREAMBOX"].links] == [("contract", "stream-plan")] and not by["STREAMBOX"].missing_contract
    assert [(l.kind, l.id) for l in by["HOMEBANK"].links] == [("liability", "home-loan")]
    assert by["TUNEBOX"].links == [] and by["TUNEBOX"].missing_contract is True
    assert [s.entity for s in recurring.missing_contracts(res)] == ["TUNEBOX"]
    assert res.counts["missing_contract"] == 1


def test_price_steps_keep_one_series():
    amounts = [-12.99] * 4 + [-14.99] * 4
    txs = [tx(add_months(D("2026-01-05"), i), a, "subscriptions.video_streaming", entity="STREAMBOX") for i, a in enumerate(amounts)]
    (s,) = series_of(txs)
    assert s.n_occurrences == 8 and s.price_steps == 1 and s.expected_amount_c == -1499


def test_detection_is_fast_on_five_thousand_transactions():
    import time
    rnd = random.Random(7)
    txs = []
    for i in range(40):                                              # 40 monthly series
        txs += monthly("2024-10-03", 24, -(5 + i), "subscriptions.memberships", entity=f"SVC{i}")
    for i in range(4000):                                            # noise
        txs.append(tx(D("2024-10-01") + dt.timedelta(days=rnd.randrange(700)), -rnd.uniform(2, 80), "food.groceries",
                      entity=f"SHOP{rnd.randrange(60)}"))
    ds = make_ds(txs)
    t0 = time.perf_counter()
    res = recurring.detect_recurring(ds)
    assert time.perf_counter() - t0 < 2.0
    assert sum(1 for s in res.series if s.entity.startswith("SVC")) == 40


# ---------------------------------------------------------------- price changes

def test_price_increase_decrease_and_blip():
    inc = [-10.0, -10.0, -10.0, -10.99, -10.99, -10.99]                    # +9.9 %
    (c,) = pricechanges.price_changes(make_pc_ds(_series(inc))).changes
    assert (c.old_c, c.new_c, c.delta_c, c.direction, c.effect, c.confirmed) == (1000, 1099, 99, "increase", "costs_more", True)
    assert c.pct == 0.099 and c.date == D("2026-04-05") and c.yearly_impact_c == 1188
    dec = pricechanges.price_changes(make_pc_ds(_series([-20.0] * 3 + [-15.0] * 3))).changes
    assert [(x.direction, x.delta_c) for x in dec] == [("decrease", -500)] and dec[0].effect == "costs_less"
    blip = pricechanges.price_changes(make_pc_ds(_series([-10.0, -10.0, -25.0, -10.0, -10.0, -10.0]))).changes
    assert blip == []                                                      # one extra charge, then back to normal


def test_unconfirmed_change_on_the_latest_occurrence_and_thresholds():
    res = pricechanges.price_changes(make_pc_ds(_series([-10.0] * 5 + [-12.0])))
    assert [(c.confirmed, c.new_c) for c in res.changes] == [(False, 1200)] and res.counts["unconfirmed"] == 1
    assert pricechanges.price_changes(make_pc_ds(_series([-10.0] * 5 + [-12.0])), only_confirmed=True).changes == []
    small = _series([-10.0] * 4 + [-10.2] * 2)                              # +2 %: below 3 %
    assert pricechanges.price_changes(make_pc_ds(small)).changes == []
    loose = AnalyticsSettings(price_change_threshold_pct=1.0, price_change_min_abs=0.1)
    assert len(pricechanges.price_changes(make_pc_ds(small, settings=loose)).changes) == 1
    cents_only = AnalyticsSettings(price_change_min_abs=5.0)
    assert pricechanges.price_changes(make_pc_ds(_series([-10.0] * 3 + [-11.0] * 3), settings=cents_only)).changes == []


def test_income_change_effect_and_since_filter():
    sal = [tx(add_months(D("2026-01-25"), i), a, "income.salary", entity="ACME") for i, a in enumerate([2500.0] * 3 + [2700.0] * 3)]
    (c,) = pricechanges.price_changes(make_pc_ds(sal)).changes
    assert c.effect == "pays_more" and c.delta_c == 20000
    small = [tx(add_months(D("2026-01-25"), i), a, "income.salary", entity="ACME2") for i, a in enumerate([2500.0] * 3 + [2600.0] * 3)]
    assert pricechanges.price_changes(make_pc_ds(small)).changes == []              # +4 %: pay noise, not a raise
    assert pricechanges.price_changes(make_pc_ds(sal), since=D("2026-12-01")).changes == []


def test_variable_series_use_a_wide_threshold_and_are_never_confirmed():
    vals = [-80.0, -120.0, -95.0, -70.0, -150.0, -110.0, -200.0, -210.0, -190.0]
    txs = [tx(add_months(D("2025-01-15"), 2 * i), a, "housing.energy", entity="POWERCO") for i, a in enumerate(vals)]
    ch = pricechanges.price_changes(make_pc_ds(txs, today=D("2026-06-15"))).changes
    assert len(ch) == 1 and ch[0].variable and not ch[0].confirmed and ch[0].direction == "increase"
    assert (ch[0].old_c, ch[0].new_c) == (11000, 20000)                       # median of the last 3 vs the 3 before
    # the same step in a smaller move (+10 %) is below the 20 % threshold of variable series
    mild = [-100.0, -90.0, -110.0, -105.0, -95.0, -100.0, -112.0, -108.0, -118.0]
    assert pricechanges.price_changes(make_pc_ds([tx(add_months(D("2025-01-15"), 2 * i), a, "housing.energy", entity="POWERCO") for i, a in enumerate(mild)], today=D("2026-06-15"))).changes == []


PC_TODAY = D("2026-07-01")


def make_pc_ds(txs, **kw):
    return make_ds(txs, today=kw.pop("today", PC_TODAY), **kw)


def _series(amounts, entity="STREAMBOX"):
    return [tx(add_months(D("2026-01-05"), i), a, "subscriptions.video_streaming", entity=entity) for i, a in enumerate(amounts)]


# ---------------------------------------------------------------- persistence

def test_refresh_is_idempotent_and_keeps_first_detected(cfg):
    con = connect(cfg, insecure=True, create=True)
    base = monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
    ds = make_ds(base)
    s1 = recurring.refresh_recurring(con, ds, now_iso="2026-10-04T10:00:00+00:00")
    assert (s1.series, s1.created, s1.updated, s1.removed, s1.unchanged) == (1, 1, 0, 0, 0)
    rows1 = con.execute("SELECT * FROM recurring_series").fetchall()
    s2 = recurring.refresh_recurring(con, ds, now_iso="2026-10-05T10:00:00+00:00")
    assert (s2.created, s2.updated, s2.unchanged) == (0, 0, 1)
    assert con.execute("SELECT * FROM recurring_series").fetchall() == rows1            # not even updated_at moved
    assert con.execute("SELECT COUNT(*) FROM recurring_members").fetchone()[0] == 6
    # new data: the row is updated, first_detected_at is kept; a vanished series is removed
    ds2 = make_ds(base + [tx("2026-10-05", -9.99, "subscriptions.video_streaming", entity="STREAMBOX")])
    s3 = recurring.refresh_recurring(con, ds2, now_iso="2026-10-06T10:00:00+00:00")
    assert (s3.updated, s3.created) == (1, 0)
    row = con.execute("SELECT first_detected_at, updated_at, n_occurrences FROM recurring_series").fetchone()
    assert row == ("2026-10-04T10:00:00+00:00", "2026-10-06T10:00:00+00:00", 7)
    s4 = recurring.refresh_recurring(con, make_ds([tx("2026-01-01", -1)]))
    assert s4.removed == 1 and con.execute("SELECT COUNT(*) FROM recurring_series").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM recurring_members").fetchone()[0] == 0


def test_stored_series_roundtrip(cfg):
    con = connect(cfg, insecure=True, create=True)
    ds = make_ds(monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
                 + monthly("2025-01-05", 4, -3.0, "subscriptions.memberships", entity="OLD"))
    recurring.refresh_recurring(con, ds)
    rows = recurring.stored_series(con)
    assert [r["entity"] for r in rows] == ["STREAMBOX", "OLD"] and rows[0]["expected_amount"] == "-9.99"
    assert [r["entity"] for r in recurring.stored_series(con, include_ended=False)] == ["STREAMBOX"]


def test_yearly_series_on_a_leap_day_moves_to_the_28th():
    txs = [tx("2020-02-29", -100.0, "insurance.other", entity="LEAPCO"), tx("2021-03-01", -100.0, "insurance.other", entity="LEAPCO"),
           tx("2022-03-01", -100.0, "insurance.other", entity="LEAPCO"), tx("2023-03-01", -100.0, "insurance.other", entity="LEAPCO"),
           tx("2024-02-29", -100.0, "insurance.other", entity="LEAPCO")]
    (s,) = series_of(txs, today=D("2024-06-01"))
    assert s.cadence == "yearly" and s.next_expected == D("2025-02-28")
