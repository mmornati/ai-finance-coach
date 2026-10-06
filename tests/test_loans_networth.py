"""E9-4: net worth composition (accounts + manual assets - liabilities), categories, owners, unknowns and stale flags, and the monthly history
(snapshots + back-fill). All figures are small and hand-computed."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from anhelpers import D, account, make_ds, tx
from coach.analytics.common import money_str
from coach.analytics.dataset import MemorySnapshot
from coach.loans import history as H, networth as NW, schedule as S, service as LS
from coach.memory import schemas

TODAY = "2026-10-05"
ACCOUNTS = [account("ce", "CE main", owner="joint", purpose="main"), account("sv", "Savings", owner="paul", purpose="savings"),
            account("nb", "No balance", owner="iris", purpose="cards")]


def asset(**kw):
    return schemas.Asset(**kw)


def liab(**kw):
    return ("liabilities/x.yaml", schemas.Liability(**kw))


def build(assets=(), liabilities=(), balances=None, txs=None, **kw):
    mem = MemorySnapshot(assets=list(assets), liabilities=list(liabilities))
    ds = make_ds(txs or [tx("2026-09-01", -10, "food.groceries", "ce"), tx("2026-09-01", -10, "food.groceries", "sv"),
                         tx("2026-09-01", -10, "food.groceries", "nb")], ACCOUNTS, today=D(TODAY), memory=mem,
                 balances=balances if balances is not None else {"ce": 1000.00, "sv": 500.00}, **kw)
    return ds, NW.build(ds, ds.today, schedules=LS.schedules(ds))


def test_composition_by_category_and_owner_with_unknowns_listed_not_counted():
    ds, nw = build(
        assets=[asset(id="livret", kind="regulated_savings", balance=2000, as_of=D("2026-09-01"), holders=["paul", "iris"]),
                asset(id="pee", kind="employee_savings_plan", balance=430000, as_of=D("2026-01-01"), holder="paul"),
                asset(id="house", kind="real_estate"),                                          # no value: unknown
                asset(id="car", kind="vehicle", value=7000, as_of=D("2026-10-01")),
                asset(id="synced", kind="savings_account", balance=999, as_of=D(TODAY), connected=True)],       # counted through its bank account
        liabilities=[liab(id="mortgage", kind="mortgage", principal=100000, start_date=D("2024-01-15"), term_months=240, rate={"nominal": 3.0}),
                     liab(id="cons", kind="consumer_loan", outstanding=3000, outstanding_as_of=D("2026-09-01")),
                     liab(id="mystery", kind="consumer_loan"),
                     liab(id="lease", kind="loa", monthly_payment=300, end_date=D("2027-04-05"))])
    by = {c.id: c for c in nw.components}
    # the mortgage: 32 instalments are due by 2026-10-05 (2024-02-15 .. 2026-09-15): 89,865.49 left (closed form: 89,865.56 before the
    # instalment is rounded to 554.60, which shifts each balance by about 0.002 x k)
    assert money_str(by["mortgage"].amount_c) == "89865.49" and by["mortgage"].source == "schedule"
    assert abs(89865.56 - 89865.49) < 0.10
    assert by["cons"].amount_c == 300000 and by["cons"].source == "declared" and by["cons"].stale is False
    assert by["mystery"].status == "unknown" and by["lease"].status == "excluded" and "not counted" in by["lease"].note
    assert "synced" not in by                                                                      # not double counted
    assert by["house"].status == "unknown"
    # assets: cash 1000.00 (main) ; savings 500.00 (savings account) + 2,000.00 (livret) ; investments 430000 ; vehicles 7,000.00
    assert nw.by_category_c["cash"] == 100000 and nw.by_category_c["savings"] == 250000
    assert nw.by_category_c["investments"] == 43000000 and nw.by_category_c["vehicles"] == 700000 and nw.by_category_c["real_estate"] == 0
    assert nw.assets_c == 100000 + 250000 + 43000000 + 700000
    assert money_str(nw.liabilities_c) == "92865.49"                                               # 89,865.49 + 3,000.00
    assert money_str(nw.net_worth_c) == "347634.51"                                                # 440,500.00 - 92,865.49
    assert nw.complete is False
    assert {(u["type"], u["id"]) for u in nw.unknown} == {("account", "nb"), ("asset", "house"), ("liability", "mystery")} and nw.n_unknown == 3
    assert {(x["type"], x["id"]) for x in nw.stale} == {("asset", "pee")}
    # the PEE value is from January: stale after 3 months; the livret and the car are fresh
    assert by["pee"].stale and not by["livret"].stale and not by["car"].stale


def test_net_worth_by_owner_joint_holders_and_unassigned():
    ds, nw = build(assets=[asset(id="livret", kind="regulated_savings", balance=2000, as_of=D("2026-09-01"), holders=["paul", "iris"]),
                           asset(id="gift", kind="cash", value=100, as_of=D("2026-09-01"))],
                   liabilities=[liab(id="cons", kind="consumer_loan", holder="paul", outstanding=300, outstanding_as_of=D("2026-09-01"))])
    o = nw.by_owner
    assert o["joint"]["assets_c"] == 100000 + 200000                        # the joint account + the livret with two holders
    assert o["paul"]["assets_c"] == 50000 and o["paul"]["liabilities_c"] == 30000 and o["paul"]["net_worth_c"] == 20000
    assert o["unassigned"]["assets_c"] == 10000 and o["iris"]["n_unknown"] == 1          # the account without balance
    assert sum(v["net_worth_c"] for v in o.values()) == nw.net_worth_c
    d = nw.to_dict()
    assert d["by_category"]["savings"] == "2500.00" and d["by_owner"]["paul"]["net_worth"] == "200.00"


def test_a_balance_that_is_not_a_booked_one_is_flagged_and_a_stale_balance_too():
    ds, nw = build(balances={"ce": (1000.00, "2026-09-20"), "sv": 500.00})
    ce = next(c for c in nw.components if c.id == "ce")
    assert ce.stale and ce.balance_type == "CLBD" and ce.note is None                     # booked, but 15 days old
    assert any(x["id"] == "ce" for x in nw.stale)


def test_a_declared_capital_that_is_old_is_flagged_and_a_lease_never_counts():
    ds, nw = build(liabilities=[liab(id="old", kind="consumer_loan", outstanding=3000, outstanding_as_of=D("2026-01-01")),
                                liab(id="lease", kind="lld", monthly_payment=300, end_date=D("2027-04-05"))])
    by = {c.id: c for c in nw.components}
    assert by["old"].stale and by["old"].amount_c == 300000
    # 6 whole months of rent remain between 2026-10-05 and 2027-04-05: 1,800.00 shown as a commitment, never added up
    assert by["lease"].status == "excluded" and by["lease"].amount_c == 180000 and nw.liabilities_c == 300000


# ---------------------------------------------------------------- history


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.executescript((Path(__file__).resolve().parents[1] / "src/coach/migrations/0016_net_worth_history.sql").read_text())
    return c


def hist_world():
    # the account's first transaction is on 2026-07-15; the balance today is 1,000.00 (2026-10-05)
    txs = [tx("2026-07-15", 500, "income.salary", "ce"), tx("2026-08-20", 300, "income.salary", "ce"),
           tx("2026-09-10", -200, "food.groceries", "ce"), tx("2026-10-02", -50, "food.groceries", "ce")]
    mem = MemorySnapshot(assets=[asset(id="car", kind="vehicle", value=7000, as_of=D("2026-08-10")), asset(id="house", kind="real_estate")],
                         liabilities=[liab(id="loan", kind="car_loan", principal=1200, start_date=D("2026-07-01"), term_months=12, rate={"nominal": 0.0})])
    return make_ds(txs, [account("ce", "CE main", owner="joint", purpose="main")], today=D(TODAY), memory=mem, balances={"ce": 1000.00})


def test_back_fill_rebuilds_month_end_figures_only_where_they_can_be_known(con):
    ds = hist_world()
    n = H.backfill(con, ds, ds.today)
    pts = {p["month"]: p for p in H.series(con)}
    assert n == 3 and sorted(pts) == ["2026-07", "2026-08", "2026-09"]                   # July is the first month with data; October is live
    # balance on a month end = 1,000.00 minus the transactions dated after it (up to the balance date 2026-10-05)
    #   09-30: 1,000 - (-50)                    = 1,050.00
    #   08-31: 1,000 - (-200 - 50)              = 1,250.00
    #   07-31: 1,000 - (+300 - 200 - 50)        =   950.00
    # the car counts from its own date (2026-08-10): July is unknown, August and September have 7,000.00; the house is always unknown
    # the loan (1,200 at 0 % over 12 months from 2026-07-01, 100.00 a month, the first instalment due 2026-08-01):
    #   07-31: nothing paid yet 1,200.00 ; 08-31: one instalment 1,100.00 ; 09-30: two instalments 1,000.00
    assert pts["2026-09"]["assets"] == "8050.00" and pts["2026-09"]["liabilities"] == "1000.00" and pts["2026-09"]["net_worth"] == "7050.00"
    assert pts["2026-08"]["assets"] == "8250.00" and pts["2026-08"]["liabilities"] == "1100.00" and pts["2026-08"]["net_worth"] == "7150.00"
    assert pts["2026-07"]["assets"] == "950.00" and pts["2026-07"]["liabilities"] == "1200.00" and pts["2026-07"]["net_worth"] == "-250.00"
    assert [pts[m]["n_unknown"] for m in sorted(pts)] == [2, 1, 1]                       # July: house + car not yet valued; Aug / Sep: house
    assert pts["2026-07"]["complete"] is False and {u["id"] for u in pts["2026-07"]["unknown"]} == {"car", "house"}
    assert pts["2026-09"]["by_category"]["vehicles"] == "7000.00" and pts["2026-09"]["by_category"]["cash"] == "1050.00"


def test_a_month_before_the_accounts_data_start_is_unknown_not_zero(con):
    ds = hist_world()
    cats, liab_c, unknown, owners = H.month_figures(ds, D("2026-06-30"), LS.schedules(ds))
    assert cats["cash"] == 0 and {u["id"] for u in unknown} >= {"ce", "car", "house"}    # the account's first transaction is in July
    assert liab_c == 0                                                                    # the loan starts on 2026-07-01: nothing owed before it, not unknown


def test_snapshots_are_stored_once_per_day_and_win_over_the_back_fill(con):
    ds = hist_world()
    nw = NW.build(ds, ds.today, schedules=LS.schedules(ds))
    H.record_snapshot(con, nw)
    H.record_snapshot(con, nw)                                                            # the same day twice: one row
    assert con.execute("SELECT COUNT(*) FROM net_worth_history WHERE source='snapshot'").fetchone()[0] == 1
    n = H.backfill(con, ds, ds.today)
    pts = H.series(con)
    assert [p["month"] for p in pts] == ["2026-07", "2026-08", "2026-09", "2026-10"] and n == 3
    assert pts[-1]["source"] == "snapshot" and pts[-1]["net_worth"] == money_str(nw.net_worth_c) and pts[-1]["n_unknown"] == 1   # the house
    assert len(H.series(con, 2)) == 2


def test_a_back_fill_never_replaces_a_snapshot_of_a_past_month(con):
    ds = hist_world()
    sept = NW.build(ds, D("2026-09-30"), schedules=LS.schedules(ds))
    H.record_snapshot(con, sept)                                                          # a snapshot recorded on 2026-09-30
    assert H.backfill(con, ds, ds.today) == 2                                             # July and August only
    src = {p["month"]: p["source"] for p in H.series(con)}
    assert src == {"2026-07": "backfill", "2026-08": "backfill", "2026-09": "snapshot"}
    assert H.backfill(con, ds, ds.today) == 2                                             # recomputed each time, still two rows per source
    assert con.execute("SELECT COUNT(*) FROM net_worth_history").fetchone()[0] == 3


def test_a_value_known_from_a_month_on_is_marked_so_the_jump_is_not_read_as_wealth(con):
    ds = hist_world()
    H.backfill(con, ds, ds.today)
    # the car (7,000) is valued from 2026-08-10: August is the first month it counts; July listed it as unknown
    H.record_snapshot(con, NW.build(ds, ds.today, schedules=LS.schedules(ds)))
    pts = {p["month"]: p for p in H.series(con)}
    assert pts["2026-07"].get("newly_counted") == [] and [x["id"] for x in pts["2026-08"]["newly_counted"]] == ["car"]
    assert pts["2026-08"]["newly_counted"][0]["reason_before"].startswith("value only known from 2026-08-10")
    assert pts["2026-09"]["newly_counted"] == []


def test_non_booked_balances_leave_a_caveat_on_rebuilt_months(con):
    ds = hist_world()
    ds.balances["ce"] = type(ds.balances["ce"])("ce", 100000, "ITAV", "2026-10-04T08:00:00+00:00", ds.balances["ce"].as_of)
    H.backfill(con, ds, ds.today)
    p = H.series(con)[-1]
    assert p["non_booked_accounts"] == 1 and "not booked" in p["caveat"] and "pending" in p["caveat"]
    ds2 = hist_world()
    H.backfill(con, ds2, ds2.today)
    assert H.series(con)[-1]["caveat"] is None
