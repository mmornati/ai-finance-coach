"""E4 foundations: money / month helpers, result serialisation, account coverage, coverage-aware averages and the
monthly cash flow (income, spending, saved, savings rate). Synthetic data, hand-computed expectations."""
import datetime as dt
import json

import pytest

from anhelpers import D, account, make_ds, monthly, tx
from coach.analytics import averages, cashflow
from coach.analytics.common import (Scope, add_months, add_months_key, days_in_month, div_cents, last_closed_month,
                                    median_c, money_str, month_end, months_between, parse_money, round_to, to_cents)
from coach.analytics.settings import AnalyticsSettings


# ---------------------------------------------------------------- money / dates

def test_cents_roundtrip_and_float_noise():
    assert to_cents(0.1 + 0.2) == 30
    assert to_cents(-12.345) == -1234 or to_cents(-12.345) == -1235      # half-even on the decimal repr
    assert to_cents("19.99") == 1999 and to_cents(5) == 500
    assert money_str(-1234) == "-12.34" and money_str(5) == "0.05" and money_str(0) == "0.00" and money_str(None) is None
    assert parse_money("-7.5") == -750


def test_division_is_half_even_and_exact():
    assert div_cents(10, 4) == 2 and div_cents(30, 4) == 8       # 2.5 -> 2, 7.5 -> 8 (half-even)
    assert div_cents(-10, 4) == -2
    assert median_c([1, 5, 3]) == 3 and median_c([1, 2]) == 2 and median_c([10, 20]) == 15
    assert round_to(1049, 500) == 1000 and round_to(1250, 500) == 1500 and round_to(-1250, 500) == -1500


def test_month_arithmetic_month_ends_and_leap_years():
    assert add_months(D("2026-01-31"), 1) == D("2026-02-28")
    assert add_months(D("2024-01-31"), 1) == D("2024-02-29")         # leap year
    assert add_months(D("2024-02-29"), 12) == D("2025-02-28")
    assert add_months(D("2026-12-15"), 1) == D("2027-01-15") and add_months(D("2026-01-15"), -2) == D("2025-11-15")
    assert month_end("2024-02") == D("2024-02-29") and days_in_month("2025-02") == 28 and days_in_month("2026-12") == 31
    assert add_months_key("2026-11", 3) == "2027-02" and add_months_key("2026-01", -1) == "2025-12"
    assert months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert last_closed_month(D("2026-01-01")) == "2025-12" and last_closed_month(D("2026-10-31")) == "2026-09"


def test_dst_changes_cannot_shift_dates():
    # calendar arithmetic only: the day of a spring-forward / fall-back weekend is an ordinary date
    for d0 in ("2026-03-28", "2026-10-24", "2026-03-29", "2026-10-25"):
        assert (add_months(D(d0), 0) + dt.timedelta(days=1)) == D(d0) + dt.timedelta(days=1)
    assert (D("2026-03-29") - D("2026-03-28")).days == 1


def test_result_to_dict_money_strings_and_stable_keys():
    ds = make_ds(monthly("2026-01-05", 8, -100.5))
    r = averages.category_averages(ds)
    d = r.to_dict()
    json.dumps(d)                                                   # JSON-safe
    assert d["categories"][0]["monthly_avg"] == "100.50" and "monthly_avg_c" not in d["categories"][0]
    assert isinstance(d["household_months"], list) and "coverage" in d and "evidence" in d
    assert list(d["categories"][0])[:2] == ["category", "monthly_avg"]       # field order = key order
    assert r.to_dict() == d                                         # same input, same output


# ---------------------------------------------------------------- coverage

def test_account_coverage_first_and_last_months():
    txs = [tx("2025-01-10", -5, account="a"), tx("2026-09-29", -5, account="a"),
           tx("2026-03-01", -5, account="b"), tx("2026-09-30", -5, account="b")]
    ds = make_ds(txs, accounts=[account("a", "A"), account("b", "B")])
    ca, cb = ds.coverage.of("a"), ds.coverage.of("b")
    assert ca.first == D("2025-01-10") and "2025-01" not in ca.months and ca.months[0] == "2025-02"
    assert ca.months[-1] == "2026-08" and "2026-09" in ca.partial_months and "2025-01" in ca.partial_months
    assert (cb.months[0], cb.months[-1], cb.n_months) == ("2026-03", "2026-09", 7)      # starts exactly on the 1st
    assert ds.coverage.common_months(["a", "b"]) == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert ds.coverage.common_months([]) == []


def test_a_quiet_but_synced_account_stays_covered_and_the_current_month_never_is():
    ds = make_ds([tx("2026-01-15", -5), tx("2026-05-03", -5)], last_sync={"a": "2026-10-04"})
    c = ds.coverage.of("a")
    assert c.last == D("2026-10-04") and c.months == ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
                                                      "2026-08", "2026-09"]
    assert "2026-10" not in c.months                                # the current month is partial
    ds2 = make_ds([tx("2026-01-15", -5), tx("2026-05-03", -5)])    # no sync info: ends with the last booking
    assert ds2.coverage.of("a").months == ["2026-02", "2026-03", "2026-04"]


def test_account_without_transactions_has_no_covered_month():
    ds = make_ds([tx("2026-01-15", -5)], accounts=[account("a", "A"), account("z", "Empty")])
    assert ds.coverage.of("z").first is None and ds.coverage.covered("z") == frozenset()


# ---------------------------------------------------------------- averages (coverage-aware)

def test_mortgage_averages_over_the_months_its_account_covers_not_over_twelve():
    # CE-like account: history from 2026-02-16; mortgage 2,000 on the 5th from March. Fortuneo-like: 24 months
    ce = [tx("2026-02-16", -3, "food.groceries", "ce")] + monthly("2026-03-05", 7, -2000, "housing.mortgage", account="ce")
    fo = monthly("2024-10-04", 24, -50, "food.groceries", account="fo")
    ds = make_ds(ce + fo, accounts=[account("ce", "CE"), account("fo", "FO", purpose="cards")],
                 last_sync={"ce": "2026-10-04", "fo": "2026-10-04"})
    r = averages.category_averages(ds)
    m = next(c for c in r.categories if c.category == "housing.mortgage")
    assert m.monthly_avg_c == 200000 and m.n_months == 7 and m.months[0] == "2026-03" and m.months[-1] == "2026-09"
    assert m.accounts == ["CE"] and m.total_c == 1400000
    legacy = 1400000 / 12                                           # what dividing by 12 would have shown
    assert round(legacy) == 116667 and m.monthly_avg_c != round(legacy)
    g = next(c for c in r.categories if c.category == "food.groceries")
    # CE carries 3 EUR of the groceries, FO 1,200: CE is a minor carrier and does not shorten the window to 7 months
    assert g.n_months == 12 and g.accounts == ["FO"] and g.ignored_accounts == ["CE"]


def test_category_on_a_long_history_account_uses_the_long_window_capped_at_twelve():
    ds = make_ds(monthly("2024-10-04", 24, -50, "food.groceries"), last_sync={"a": "2026-10-04"})
    c = averages.category_averages(ds).categories[0]
    assert c.n_months == 12 and c.months[-1] == "2026-09" and c.monthly_avg_c == 5000


def test_one_offs_and_capital_are_excluded_from_the_run_rate_but_reported():
    txs = (monthly("2026-02-10", 8, -100, "food.groceries")
           + [tx("2026-03-20", -1000, "housing.renovation", tags=["one_off", "capital"], event="works"),
              tx("2026-04-20", -500, "shopping.electronics", tags=["capital"]),
              tx("2026-05-20", -300, "leisure.hobbies", tags=["exclude_from_averages"])])
    ds = make_ds(txs, last_sync={"a": "2026-10-04"})
    r = averages.category_averages(ds)
    cats = {c.category: c for c in r.categories}
    assert cats["food.groceries"].monthly_avg_c == 10000
    assert cats["housing.renovation"].monthly_avg_c == 0 and cats["housing.renovation"].monthly_avg_with_one_offs_c > 0
    assert [e.category for e in r.excluded] == ["housing.renovation", "leisure.hobbies"]
    assert [e.category for e in r.capital] == ["shopping.electronics"]          # capital only, not also one_off
    assert cats["shopping.electronics"].monthly_avg_c == 0
    # the account starts on 2026-02-10, so February is partial: 7 covered months (Mar-Sep). Without one-offs only the
    # groceries (700 / 7 = 100); with them (700 + 1000 + 500 + 300) / 7 = 357.14
    assert len(r.household_months) == 7 and r.household_monthly_avg_c == 10000
    assert r.household_monthly_avg_with_one_offs_c == 35714
    assert cats["housing.renovation"].monthly_avg_with_one_offs_c == 14286 and cats["housing.renovation"].excluded_total_c == 100000
    assert set(r.evidence) == {e.tx_key for e in r.excluded + r.capital}


def test_refunds_net_against_the_category_and_savings_tags_are_not_spending():
    txs = (monthly("2026-02-10", 6, -100, "shopping.clothing") + [tx("2026-03-12", 40, "shopping.clothing")]
           + [tx("2026-02-01", -500, "transfer.internal", tags=["savings"])])
    r = averages.category_averages(make_ds(txs, last_sync={"a": "2026-10-04"}))
    c = next(c for c in r.categories if c.category == "shopping.clothing")
    assert c.n_months == 8 and c.total_c == 56000 and c.monthly_avg_c == 7000          # account starts Feb 1: 8 covered months; (600 - 40 refund) / 8
    assert all(x.category != "transfer.internal" for x in r.categories)


def test_few_months_flagged_low_confidence_and_uncovered_categories_listed():
    ds = make_ds([tx("2026-08-10", -20, "food.cafes_bars"), tx("2026-06-02", -5, "food.groceries"),
                  tx("2026-09-29", -5, "food.groceries")])
    r = averages.category_averages(ds)
    assert all(c.low_confidence for c in r.categories)               # fewer than 3 covered months
    # a category whose carrying account has no closed covered month at all is reported, not silently dropped
    ds2 = make_ds([tx("2026-09-20", -20, "food.cafes_bars")])
    r2 = averages.category_averages(ds2)
    assert r2.categories == [] and r2.unavailable[0]["category"] == "food.cafes_bars"


def test_scope_filters_by_owner_and_purpose():
    kid = account("k", "Kid", owner="alex", purpose="kids")
    txs = monthly("2026-02-10", 8, -100, "food.groceries") + monthly("2026-02-12", 8, -10, "leisure.hobbies", account="k")
    ds = make_ds(txs, accounts=[account(), kid], last_sync={"a": "2026-10-04", "k": "2026-10-04"})
    only_kid = averages.category_averages(ds, Scope.make(purposes=["kids"]))
    assert [c.category for c in only_kid.categories] == ["leisure.hobbies"]
    everyone = averages.category_averages(ds)
    assert {c.category for c in everyone.categories} == {"food.groceries", "leisure.hobbies"}
    assert averages.category_averages(ds, Scope.make(owners=["alex"])).household_monthly_avg_c == 1000


def test_split_transactions_count_each_part_in_its_category():
    parts = [tx("2026-02-10", -60, "food.groceries", key="big", split=0), tx("2026-02-10", -40, "housing.maintenance_diy", key="big", split=1)]
    ds = make_ds(parts + monthly("2026-01-03", 8, -1, "fees.bank_fees"), last_sync={"a": "2026-10-04"})
    cats = {c.category: c for c in averages.category_averages(ds).categories}
    assert cats["food.groceries"].total_c == 6000 and cats["housing.maintenance_diy"].total_c == 4000


# ---------------------------------------------------------------- cash flow

def _month_ds(extra=()):
    base = [tx("2026-08-25", 3000, "income.salary", entity="ACME"),
            tx("2026-08-26", 500, "income.rental", entity="TENANT"),
            tx("2026-08-03", -400, "food.groceries"),
            tx("2026-08-17", 50, "food.groceries", desc="card refund"),          # refund inside the category
            tx("2026-08-20", 20, "income.refund"),                                # a refund: negative spending
            tx("2026-08-04", -500, "transfer.internal"),                          # own accounts: ignored
            tx("2026-08-05", 500, "transfer.internal"),
            tx("2026-08-06", -30, "transfer.to_people"),
            tx("2026-08-07", -200, "transfer.internal", tags=["savings", "investment"]),   # saved
            tx("2026-08-08", -1000, "housing.renovation", tags=["one_off"]),
            tx("2026-08-09", -100, "other.uncategorized"),
            tx("2026-07-02", -10, "food.groceries"), tx("2026-09-30", -10, "food.groceries")]
    return make_ds(base + list(extra), last_sync={"a": "2026-10-04"})


def test_month_income_spending_saved_and_savings_rate_hand_computed():
    ds = _month_ds()
    r = cashflow.cashflow(ds, months=2, end="2026-08")
    m = next(x for x in r.household.months if x.month == "2026-08")
    assert m.income_c == 350000                                      # salary + rental; income.refund is NOT income
    assert m.refunds_c == 5000 + 2000                                # card refund 50 + income.refund 20
    assert m.spending_gross_c == 40000 + 100000 + 10000              # groceries + renovation + uncategorized
    assert m.spending_c == 150000 - 7000 == 143000
    assert m.one_off_spending_c == 100000 and m.spending_ex_one_offs_c == 43000
    assert m.saved_c == 20000                                        # tagged transfer: saved, not spent
    assert m.net_c == 350000 - 143000 == 207000
    assert m.savings_rate == round(207000 / 350000, 4) and m.saved_rate == round(20000 / 350000, 4)
    assert m.uncategorized_c == 10000
    assert (m.transfers.internal_out_c, m.transfers.internal_in_c, m.transfers.people_out_c) == (50000, 50000, 3000)
    assert m.complete is True and m.missing_accounts == []


def test_transfers_never_count_as_spending_or_income_and_zero_income_gives_no_rate():
    ds = make_ds([tx("2026-08-04", -500, "transfer.internal"), tx("2026-08-05", 800, "transfer.from_people"),
                  tx("2026-08-06", -100, "food.groceries"), tx("2026-07-01", -1, "food.groceries"),
                  tx("2026-09-30", -1, "food.groceries")], last_sync={"a": "2026-10-04"})
    m = next(x for x in cashflow.cashflow(ds, months=2, end="2026-08").household.months if x.month == "2026-08")
    assert (m.income_c, m.spending_c, m.saved_c) == (0, 10000, 0)
    assert m.savings_rate is None and m.saved_rate is None
    assert m.transfers.people_in_c == 80000 and m.transfers.internal_out_c == 50000


def test_partial_months_are_flagged_and_excluded_from_the_complete_totals():
    # a second account only exists from 2026-08-16: July is incomplete (and so is August: it starts mid-month)
    a = monthly("2026-05-10", 5, -100, "food.groceries") + [tx("2026-06-25", 2000, "income.salary")]
    b = [tx("2026-08-16", -20, "food.groceries", "b"), tx("2026-09-10", -30, "food.groceries", "b")]
    ds = make_ds(a + b, accounts=[account(), account("b", "B", purpose="cards")], last_sync={"a": "2026-10-04", "b": "2026-10-04"})
    r = cashflow.cashflow(ds, months=5)
    rows = {m.month: m for m in r.household.months}
    assert rows["2026-07"].complete is False and rows["2026-07"].missing_accounts == ["B"]
    assert rows["2026-08"].complete is False                         # B starts on the 16th
    assert rows["2026-09"].complete is True
    assert r.household.totals_complete.n_months == 1 and r.household.totals_all.n_months == 5
    assert "incomplete" in " ".join(r.coverage.notes)


def test_breakdown_by_purpose_and_owner_with_a_children_account():
    kid = account("k", "Kid card", owner="alex", purpose="kids")
    txs = [tx("2026-08-25", 3000, "income.salary"), tx("2026-08-03", -400, "food.groceries"),
           tx("2026-08-05", -25, "leisure.hobbies", "k"), tx("2026-08-06", 10, "transfer.internal", "k"),
           tx("2026-07-01", -1, "food.groceries"), tx("2026-09-30", -1, "food.groceries"),
           tx("2026-07-01", -1, "food.groceries", "k"), tx("2026-09-30", -1, "food.groceries", "k")]
    ds = make_ds(txs, accounts=[account(), kid], last_sync={"a": "2026-10-04", "k": "2026-10-04"})
    r = cashflow.cashflow(ds, months=2, end="2026-08")
    aug = lambda blk: next(m for m in blk.months if m.month == "2026-08")          # noqa: E731
    assert aug(r.household).spending_c == 42500 and aug(r.household).income_c == 300000     # kids spending is household spending
    assert aug(r.by_purpose["kids"]).spending_c == 2500 and aug(r.by_purpose["main"]).spending_c == 40000
    assert set(r.by_owner) == {"alex", "joint"} and aug(r.by_owner["alex"]).income_c == 0
    only = cashflow.cashflow(ds, Scope.make(owners=["alex"]), months=2, end="2026-08")
    assert aug(only.household).spending_c == 2500 and only.household.accounts == ["Kid card"]


def test_cashflow_lists_only_months_with_data_and_names_its_evidence():
    ds = _month_ds()
    r = cashflow.cashflow(ds, months=12, end="2026-09")
    assert r.household.months[0].month == "2026-07"                  # nothing before the first booking
    assert set(r.evidence) >= {t.key for t in ds.txs if t.category == "income.salary"}
    assert cashflow.cashflow(make_ds([]), months=3).household.months == []


def test_foreign_currency_transactions_are_left_out_and_reported(cfg):
    from coach.analytics.dataset import load_dataset
    from coach.db import connect
    from helpers import add_bank, add_tx
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Bank", "FR", [("acc", "FR7600000000000000000000099", "Main")])
    add_tx(con, "acc", "k1", "2026-08-03", -40.0, "CARTE SHOP ONE", "card")
    add_tx(con, "acc", "k2", "2026-08-04", -99.0, "CARTE SHOP TWO", "card")
    con.execute("UPDATE transactions SET currency='USD' WHERE tx_key='k2'")
    con.commit()
    ds = load_dataset(con, cfg.memory_dir, today=D("2026-10-04"))
    assert [t.key for t in ds.txs] == ["k1"] and ds.foreign == ["k2"]
    r = cashflow.cashflow(ds, months=2, end="2026-08", breakdown=False)
    assert "non-EUR" in " ".join(r.coverage.notes)


def test_settings_validation():
    assert AnalyticsSettings.from_dict({"recurring_amount_tolerance": 0.2}).recurring_amount_tolerance == 0.2
    for bad in ({"nope": 1}, {"recurring_amount_tolerance": 2}, {"average_window_months": -1},
                {"recurring_variable_categories": "x"}, {"anomaly_z": "x"}):
        with pytest.raises(ValueError):
            AnalyticsSettings.from_dict(bad)


def test_leap_february_month_end_decides_coverage():
    # 2024 is a leap year: a history that ends on 28 February does not cover February, one that ends on the 29th does
    txs = [tx("2023-11-05", -1), tx("2024-02-28", -1)]
    cut = make_ds(txs, today=D("2024-03-10"))
    assert "2024-02" not in cut.coverage.of("a").months and "2024-01" in cut.coverage.of("a").months
    full = make_ds(txs + [tx("2024-02-29", -1)], today=D("2024-03-10"))
    assert full.coverage.of("a").months[-1] == "2024-02"
    assert month_end("2023-02") == D("2023-02-28")


def test_cashflow_on_a_leap_february_counts_the_29th():
    txs = [tx("2023-12-05", -1), tx("2024-02-29", -10.0, "food.groceries"), tx("2024-03-01", -1), tx("2024-01-05", 100.0, "income.salary")]
    ds = make_ds(txs, today=D("2024-03-10"))
    feb = next(m for m in cashflow.cashflow(ds, months=3).household.months if m.month == "2024-02")
    assert feb.spending_c == 1000 and feb.complete is True
