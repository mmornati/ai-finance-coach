"""E9-6: LOA / LLD end of contract: the reminder window, the mileage projection and the excess-km estimate (hand computed), the buy-or-return
figures and the restitution checklist."""
from __future__ import annotations

import datetime as dt

from anhelpers import D, account, make_ds
from coach.analytics.dataset import MemorySnapshot
from coach.loans import loa as LOA, service as LS
from coach.memory import schemas


def lease(**kw) -> schemas.Liability:
    base = dict(id="car", kind="loa", lender="CarFin", monthly_payment=300, start_date=D("2025-01-01"), end_date=D("2027-01-01"),
                residual_value=15000, mileage_limit_km=25000, excess_km_fee=0.10, initial_km=0,
                odometer=[{"date": D("2026-01-01"), "km": 15000}])
    base.update(kw)
    return schemas.Liability(**base)


TODAY = D("2026-10-05")


def test_the_mileage_projection_and_the_excess_cost():
    m = LOA.mileage(lease(), TODAY)
    # 15,000 km in the 365 days from the start (2025-01-01) to the reading (2026-01-01) = 41.0959 km a day; 365 days to the end (2027-01-01):
    # 15,000 + 41.0959 x 365 = 30,000 km at the end, 5,000 above the 25,000 limit, 5,000 x 0.10 = 500.00
    assert m["pace"]["km_per_year"] == 15010 and m["pace"]["km_per_month"] == 1251        # 41.0959 x 365.25 and x 30.4375
    assert m["projected_odometer_at_end"] == 30000 and m["projected_contract_km"] == 30000
    assert (m["status"], m["excess_km"], m["excess_cost"]) == ("over_limit", 5000, "500.00")
    assert m["allowed_km_per_year"] == 12509                                              # 25,000 over 730 days x 365.25
    assert m["latest"] == {"date": "2026-01-01", "km": 15000, "age_days": 277}


def test_within_the_limit_and_without_a_fee():
    assert LOA.mileage(lease(mileage_limit_km=31000), TODAY)["status"] == "within_limit"
    m = LOA.mileage(lease(excess_km_fee=None), TODAY)
    assert m["excess_km"] == 5000 and "excess_cost" not in m and any("excess_km_fee" in n for n in m["needs"])


def test_without_the_initial_odometer_two_readings_give_the_pace_only():
    x = lease(initial_km=None, odometer=[{"date": D("2025-07-01"), "km": 6000}, {"date": D("2026-01-01"), "km": 15000}])
    m = LOA.mileage(x, TODAY)
    # 9,000 km in the 184 days between the readings = 48.913 km a day; 365 days to the end: 15,000 + 17,853 = 32,853 on the odometer
    assert m["status"] == "pace_only" and m["projected_odometer_at_end"] == 32853 and "excess_km" not in m
    assert any("initial_km" in n for n in m["needs"]) and "first and the latest reading" in m["basis"]


def test_what_is_missing_is_listed_not_guessed():
    one = LOA.mileage(lease(initial_km=None), TODAY)
    assert one["status"] == "needs_readings" and any("second reading" in n for n in one["needs"]) and "pace" not in one
    none = LOA.mileage(lease(odometer=[]), TODAY)
    assert none["status"] == "needs_readings" and any("odometer reading" in n for n in none["needs"])
    short = LOA.mileage(lease(odometer=[{"date": D("2025-01-10"), "km": 500}]), TODAY)         # 9 days after the start: too short to project
    assert "pace" not in short and any("30 days" in n for n in short["needs"])
    noend = LOA.mileage(lease(end_date=None), TODAY)
    assert "end_date" in noend["needs"] and noend["pace"]["km_per_year"] == 15010 and "projected_odometer_at_end" not in noend


def test_the_reminder_starts_six_months_before_the_end():
    e = LOA.end_status(lease(), TODAY)
    assert e["reminder_date"] == "2026-07-01" and e["reminder_active"] and e["days_left"] == 88 and not e["ended"]
    assert not LOA.end_status(lease(), D("2026-06-30"))["reminder_active"]                      # one day before the window
    assert LOA.end_status(lease(), D("2026-07-01"))["reminder_active"]
    assert LOA.end_status(lease(), D("2027-01-02"))["ended"]
    assert LOA.end_status(lease(end_date=None), TODAY) == {"end_date": None, "known": False}


def test_cards_inside_the_window_high_severity_in_the_last_three_months():
    cards = LOA.cards(lease(), TODAY)
    kinds = {c["subtype"]: c for c in cards}
    assert set(kinds) == {"loa_end", "loa_mileage"} and kinds["loa_end"]["severity"] == "high"          # 88 days <= 90
    assert "88 days" in kinds["loa_end"]["title"] and kinds["loa_end"]["amount"] == "15000.00" and kinds["loa_end"]["date"] == "2026-07-01"
    assert kinds["loa_mileage"]["amount"] == "500.00" and "5,000 km over" in kinds["loa_mileage"]["title"]
    assert LOA.cards(lease(), D("2026-06-30")) == [] and LOA.cards(lease(), D("2027-01-02")) == []     # outside the window / over
    mid = LOA.cards(lease(), D("2026-08-01"))
    assert [c for c in mid if c["subtype"] == "loa_end"][0]["severity"] == "medium"
    assert LOA.cards(lease(end_date=None), TODAY) == []


def test_a_missing_reading_inside_the_window_asks_for_one():
    cards = LOA.cards(lease(odometer=[]), TODAY)
    assert {c["subtype"] for c in cards} == {"loa_end", "loa_mileage_missing"}
    stale = LOA.cards(lease(odometer=[{"date": D("2026-01-01"), "km": 100}], initial_km=None), TODAY)
    assert "loa_mileage_missing" in {c["subtype"] for c in stale}


def test_buy_or_return_uses_the_users_own_market_value_never_a_looked_up_one():
    d = LOA.decision(lease(), 18000)
    assert d["market_minus_option_price"] == "3000.00" and "above the option price" in d["reading"]
    assert "note" in LOA.decision(lease(), None) and "market_minus_option_price" not in LOA.decision(lease(), None)
    assert LOA.decision(lease(residual_value=None))["needs"]
    st = LOA.status(lease(), TODAY)
    assert st["missing"] == [] and len(st["checklist"]) >= 6 and any("odometer" in x for x in st["checklist"])
    assert LOA.status(schemas.Liability(id="m", kind="mortgage"), TODAY) is None


def test_the_calendar_has_the_reminder_date_and_the_service_lists_it():
    ds = make_ds([], [account()], today=TODAY, memory=MemorySnapshot(liabilities=[("liabilities/car.yaml", lease())]))
    assert [r["date"] for r in LS.calendar_reminders(ds, D("2026-06-01"), D("2026-08-01"))] == [D("2026-07-01")]
    assert LS.calendar_reminders(ds, D("2026-10-01"), D("2026-12-01")) == []
    from coach.analytics.upcoming import calendar_items
    items = calendar_items(make_ds([], [account()], today=D("2026-06-20"), memory=MemorySnapshot(liabilities=[("liabilities/car.yaml", lease())])), 30)
    assert [(i.date, i.kind) for i in items.items if i.kind == "loa_decision"] == [(D("2026-07-01"), "loa_decision")]
