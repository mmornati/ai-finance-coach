"""E7-3 / E7-4 / E7-5 / E7-9 / E7-10: monthly_review, explain_spike, subscription_audit, what_if and tax_candidates on synthetic
datasets with hand-computed expected values (no database; invented merchants and amounts only)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from anhelpers import D, TODAY, make_ds, monthly, tx
from coach.analytics.dataset import MemorySnapshot
from coach.memory import schemas as S
from coach.skills import review as RV, subaudit as SA, tax as TX, whatif as WI

HIST = {"a": ("2026-01-01", "2026-10-03")}
SEPT_DAYS = [(3, 12, 25), (5, 14, 27), (2, 11, 22), (6, 16, 28), (4, 13, 24), (7, 17, 26), (3, 15, 29), (8, 18, 27)]


def groceries(extra_sept=True):
    """Jan-Aug: 100 + 120 + 80 = 300.00 a month on irregular days; September 200 + 180 + 170 = 550.00."""
    out = []
    for m, days in enumerate(SEPT_DAYS, start=1):
        for d_, amt in zip(days, (100, 120, 80)):
            out.append(tx(f"2026-{m:02d}-{d_:02d}", -amt, "food.groceries", entity="MARKET"))
    if extra_sept:
        for d_, amt in zip((4, 13, 26), (200, 180, 170)):
            out.append(tx(f"2026-09-{d_:02d}", -amt, "food.groceries", entity="MARKET"))
    return out


def sept_world(**kw):
    txs = groceries() + monthly("2026-01-25", 9, 2500, "income.salary", entity="ACME")
    txs += [tx("2026-09-20", -90, "food.restaurants", entity="NEWRESTO"),
            tx("2026-09-21", -500, "housing.renovation", entity="BRICO", tags=("one_off", "capital"))]
    return make_ds(txs, history=HIST, **kw)


# ---------------------------------------------------------------- monthly_review

def test_monthly_review_cash_flow_movers_one_offs_and_budget_hand_computed():
    snap = MemorySnapshot(budgets=[S.Budget(id="groceries", category="food.groceries", monthly=500)])
    r = RV.monthly_review(sept_world(memory=snap), "2026-09")
    cf = r["cash_flow"]
    # income 2,500 ; spending 550 + 90 + 500 = 1,140 of which 500 one-off ; net 1,360
    assert (cf["income"], cf["spending"], cf["one_off_spending"], cf["spending_ex_one_offs"], cf["net"]) == \
        ("2500.00", "1140.00", "500.00", "640.00", "1360.00")
    assert cf["complete"] and r["partial_month"] is False and cf["drawn_from_savings_accounts"] == "0.00"
    g, rest = r["movers"][0], r["movers"][1]
    assert (g["category"], g["spent"], g["usual"], g["delta"], g["usual_months"], g["direction"]) == \
        ("food.groceries", "550.00", "300.00", "250.00", 8, "up")
    assert g["pct"] == pytest.approx(0.8333, abs=1e-4) and len(g["evidence"]) == 3
    assert (rest["category"], rest["spent"], rest["usual"], rest["delta"], rest["pct"]) == ("food.restaurants", "90.00", "0.00", "90.00", None)
    # 550 + 90 = 640 against 300 + 0 usual: +340 ; the 500 one-off is listed apart and is not a mover
    au = r["against_usual"]
    assert (au["comparable_month_spending"], au["usual_for_those_categories"], au["delta"]) == ("640.00", "300.00", "340.00")
    assert r["one_offs"]["total"] == "500.00" and r["one_offs"]["items"][0]["category"] == "housing.renovation"
    assert all(m["category"] != "housing.renovation" for m in r["movers"])
    b = r["budgets"]["items"][0]
    assert (b["id"], b["monthly"], b["spent"], b["remaining"], b["status"]) == ("groceries", "500.00", "550.00", "-50.00", "over")
    assert r["budgets"]["counts"] == {"over": 1, "at_risk": 0, "ok": 0}


def test_monthly_review_defaults_to_the_last_closed_month_flags_partial_data_and_refuses_bad_months():
    ds = sept_world()
    assert RV.monthly_review(ds)["month"] == "2026-09"
    cur = RV.monthly_review(ds, "2026-10")
    assert cur["partial_month"] is True and any("still running" in n for n in cur["notes"])
    with pytest.raises(ValueError):
        RV.monthly_review(ds, "2026-11")
    with pytest.raises(ValueError):
        RV.monthly_review(ds, "September")
    gap = make_ds(groceries(), history={"a": ("2026-03-01", "2026-10-03")})            # the account only starts in March
    r = RV.monthly_review(gap, "2026-09")
    assert next(m for m in r["movers"] if m["category"] == "food.groceries")["usual_months"] == 6        # Mar-Aug, not 8


def test_a_category_without_covered_history_is_listed_apart_never_compared_with_a_made_up_figure():
    ds = make_ds([tx("2026-09-10", -80, "leisure.hobbies", entity="CLUB")], history={"a": ("2026-09-01", "2026-10-03")})
    r = RV.monthly_review(ds, "2026-09")
    assert r["movers"] == [] and r["categories_without_baseline"][0]["category"] == "leisure.hobbies"
    assert r["against_usual"]["categories_without_baseline"] == 1


# ---------------------------------------------------------------- explain_spike

def test_explain_spike_splits_the_excess_by_class_and_merchant():
    ds = sept_world()
    e = RV.explain_spike(ds, category="food", month="2026-09")
    t = e["totals"]
    assert (t["spent_ex_one_offs"], t["usual_ex_one_offs"], t["excess_ex_one_offs"], t["baseline_months"]) == ("640.00", "300.00", "340.00", 8)
    assert e["by_class"]["new_merchant"] == "90.00"
    assert Decimal(e["by_class"]["recurring"]) + Decimal(e["by_class"]["habitual"]) == Decimal("550.00")
    ms = {m["entity"]: m for m in e["merchants"]}
    assert ms["MARKET"]["delta"] == "250.00" and ms["MARKET"]["usual"] == "300.00" and ms["NEWRESTO"]["class"] == "new_merchant"
    assert ms["NEWRESTO"]["delta"] == "90.00" and e["merchants"][0]["entity"] == "MARKET"
    assert sum(Decimal(v) for v in e["excess_by_class"].values()) == Decimal("340.00")            # the classes add up to the excess
    top = e["top_transactions"][0]
    assert top["amount"] == "-200.00" and top["entity"] == "MARKET" and len(e["top_transactions"]) == 4
    assert e["same_month_last_year"]["available"] is False and e["month_complete"]


def test_explain_spike_for_an_account_includes_the_one_offs_apart():
    e = RV.explain_spike(sept_world(), account="a", month="2026-09")
    assert e["by_class"]["one_off"] == "500.00" and e["totals"]["spent"] == "1140.00" and e["totals"]["spent_ex_one_offs"] == "640.00"
    assert {c["category"] for c in e["categories"]} == {"food.groceries", "food.restaurants"} or "housing.renovation" in {c["category"] for c in e["categories"]}


def test_explain_spike_compares_with_the_same_month_last_year_only_when_covered():
    ds = make_ds([tx("2025-09-10", -400, "food.groceries", entity="MARKET"), tx("2026-09-10", -550, "food.groceries", entity="MARKET")],
                 history={"a": ("2025-01-01", "2026-10-03")})
    ly = RV.explain_spike(ds, category="food.groceries", month="2026-09")["same_month_last_year"]
    assert ly == {"month": "2025-09", "available": True, "spent": "400.00", "spent_ex_one_offs": "400.00",
                  "delta_ex_one_offs": "150.00", "pct": 0.375}
    short = make_ds([tx("2026-09-10", -550, "food.groceries", entity="MARKET")], history={"a": ("2026-06-01", "2026-10-03")})
    assert RV.explain_spike(short, category="food.groceries", month="2026-09")["same_month_last_year"]["available"] is False


def test_explain_spike_arguments_are_validated():
    ds = sept_world()
    for kw in ({}, {"category": "food", "account": "a"}, {"category": "nonsense.nothing"}, {"account": "zzz"}):
        with pytest.raises(ValueError):
            RV.explain_spike(ds, month="2026-09", **kw)


# ---------------------------------------------------------------- subscription_audit

def sub_world(asked=frozenset()):
    txs = []
    txs += monthly("2026-01-05", 9, -9.99, "subscriptions.video_streaming", entity="STREAMA")
    txs += monthly("2026-01-05", 9, -9.99, "subscriptions.video_streaming", entity="STREAMB")
    txs += monthly("2026-01-12", 9, -10.99, "subscriptions.music_streaming", entity="MUSICO")
    txs += monthly("2026-01-20", 9, -12.99, "subscriptions.music_streaming", entity="TUNEZ")
    txs += monthly("2026-01-03", 5, -4.99, "subscriptions.software_cloud", entity="CLOUDBOX")
    txs += monthly("2026-06-03", 4, -5.99, "subscriptions.software_cloud", entity="CLOUDBOX")
    txs += monthly("2026-01-09", 9, -19.99, "subscriptions.telecom", entity="TELONE")
    txs += monthly("2026-01-22", 9, -24.99, "subscriptions.telecom", entity="TELTWO")
    txs += monthly("2026-01-15", 9, -52.30, "transport.car_insurance", entity="CARCOVER")
    c = S.Contract(id="music-contract", provider="Musico", kind="streaming", merchant_match="^MUSICO$", keep=False)
    return make_ds(txs, history=HIST, memory=MemorySnapshot(contracts=[("contracts/music-contract.yaml", c)])), asked


def test_subscription_audit_totals_groups_and_ranking_hand_computed():
    ds, asked = sub_world()
    a = SA.subscription_audit(ds)
    # yearly: 119.88 x 2 + 131.88 + 155.88 + 71.88 (5.99 level) + 239.88 + 299.88 + 627.60 = 1,766.76 ; monthly 147.23
    assert a["totals"] == {"active_series": 8, "monthly": "147.23", "yearly": "1766.76"}
    g = a["groups"]
    assert (g["streaming_media"]["count"], g["streaming_media"]["yearly"]) == (4, "527.52")
    assert (g["telecom"]["yearly"], g["software_cloud"]["yearly"], g["insurance"]["yearly"]) == ("539.76", "71.88", "627.60")
    c = {x["entity"]: x for x in a["candidates"]}
    order = [x["entity"] for x in a["candidates"]]
    assert order[:4] == ["CARCOVER", "TUNEZ", "MUSICO", "TELTWO"] and set(order[4:6]) == {"STREAMA", "STREAMB"}      # equal bounds: ordered by series id
    assert order[6:] == ["TELONE", "CLOUDBOX"]
    assert c["CARCOVER"]["expected_yearly_savings"] == {"low": "31.38", "high": "156.90"}          # 5 % .. 25 % of 627.60
    assert c["TELONE"]["expected_yearly_savings"] == {"low": "23.99", "high": "95.95"}             # 10 % .. 40 % of 239.88
    assert c["TUNEZ"]["expected_yearly_savings"] == {"low": "46.76", "high": "155.88"}             # 30 % .. 100 % of 155.88
    assert c["MUSICO"]["expected_yearly_savings"] == {"low": "131.88", "high": "131.88"} and "marked_to_cancel" in c["MUSICO"]["reasons"]
    assert c["MUSICO"]["next_step"] == "stop_payment_marked_in_contract" and "usage_unknown" not in c["MUSICO"]["reasons"]
    assert c["STREAMA"]["expected_yearly_savings"] == {"low": "0.00", "high": "119.88"}            # a possible duplicate: 0 .. the whole cost
    assert c["STREAMA"]["reasons"][:3] == ["usage_unknown", "duplicate_amount", "overlap"]
    assert a["candidates_total"] == 8
    assert a["candidates_savings_range_sum"]["high"] == "972.20" and a["candidates_savings_range_sum"]["low"] == "285.56"


def test_subscription_audit_duplicates_overlaps_price_rise_and_contracts():
    ds, _ = sub_world()
    a = SA.subscription_audit(ds)
    assert len(a["duplicates"]) == 1 and a["duplicates"][0]["kind"] == "same_amount_same_day" and a["duplicates"][0]["amount"] == "9.99"
    ov = {o["category"]: o for o in a["overlaps"]}
    assert set(ov) == {"subscriptions.video_streaming", "subscriptions.music_streaming", "subscriptions.telecom"}
    assert ov["subscriptions.video_streaming"]["combined_yearly"] == "239.76" and ov["subscriptions.video_streaming"]["dropping_the_cheapest_saves_yearly"] == "119.88"
    assert ov["subscriptions.music_streaming"]["combined_yearly"] == "287.76" and ov["subscriptions.telecom"]["informational"] is True
    pi = a["price_increases"][0]
    assert pi["yearly_impact"] == "12.00" and pi["confirmed"]                                   # 4.99 -> 5.99, x 12
    item = next(i for i in a["items"] if i["entity"] == "CLOUDBOX")
    assert item["expected"] == "5.99" and item["price_change"]["old"] == "4.99" and item["price_change"]["new"] == "5.99"
    musico = next(i for i in a["items"] if i["entity"] == "MUSICO")
    assert musico["contract"]["on_file"] and musico["contract"]["keep"] is False and "MUSICO" not in a["no_contract_on_file"]
    assert len(a["no_contract_on_file"]) == 7
    assert "not market quotes" in " ".join(a["notes"]) and "nothing is cancelled" in " ".join(a["notes"])


def test_unknown_usage_becomes_questions_not_guesses_and_asked_ones_are_not_repeated():
    ds, _ = sub_world()
    a = SA.subscription_audit(ds)
    needs = {n["series"]: n for n in a["usage_questions_needed"]}
    ents = {i["series"]: i["entity"] for i in a["items"]}
    assert {ents[s] for s in needs} == {"STREAMA", "STREAMB", "TUNEZ", "CLOUDBOX"}                # not MUSICO (marked), not telecom / insurance
    a_id = next(s for s, e in ents.items() if e == "STREAMA")
    b = SA.subscription_audit(ds, asked=frozenset({f"usage:{a_id}"}))
    assert a_id not in {n["series"] for n in b["usage_questions_needed"]}
    keep = S.Contract(id="stream-a", kind="streaming", merchant_match="^STREAMA$", keep=True, usage="every evening")
    ds2 = make_ds(monthly("2026-01-05", 9, -9.99, "subscriptions.video_streaming", entity="STREAMA"), history=HIST,
                  memory=MemorySnapshot(contracts=[("contracts/stream-a.yaml", keep)]))
    k = SA.subscription_audit(ds2)
    assert k["candidates"] == [] and k["usage_questions_needed"] == [] and k["items"][0]["usage"] == "recorded"


def test_loans_taxes_and_rent_are_not_subscriptions_and_ended_series_are_listed_apart():
    txs = monthly("2026-01-03", 9, -1500, "housing.mortgage", entity="HOMEBANK") + monthly("2026-01-10", 3, -9.99, "subscriptions.news_media", entity="DAILYNEWS")
    a = SA.subscription_audit(make_ds(txs, history=HIST))
    assert a["totals"]["active_series"] == 0 and a["recently_ended"][0]["entity"] == "DAILYNEWS" and a["recently_ended"][0]["yearly"] == "119.88"


# ---------------------------------------------------------------- what_if

def wi_world(extra=(), memory=None):
    txs = groceries(extra_sept=False) + monthly("2026-01-25", 9, 2500, "income.salary", entity="ACME")
    txs += monthly("2026-01-05", 9, -12.99, "subscriptions.video_streaming", entity="STREAMA")
    txs += [tx("2026-09-12", -300, "food.groceries", entity="MARKET")] + list(extra)
    return make_ds(txs, history=HIST, balances={"a": 5000.0}, memory=memory)


def rec_id(ds, entity):
    from coach.analytics.recurring import detect_recurring
    return next(x.id for x in detect_recurring(ds).series if x.entity == entity)


def run(ds, *changes, days=90):
    return WI.what_if(ds, {"days": days, "changes": list(changes)})


def test_cancel_a_recurring_series_adds_back_exactly_its_occurrences_in_the_horizon():
    ds = wi_world()
    r = run(ds, {"type": "cancel_recurring", "series": rec_id(ds, "STREAMA")})
    # payments on the 5th: 10-05, 11-05, 12-05 are inside 90 days (until 2027-01-02): 3 x 12.99 = 38.97
    assert r["delta"]["end_balance"] == "38.97" and r["delta"]["monthly_savings"] == "12.99" and r["delta"]["yearly_impact"] == "155.88"
    assert r["changes"][0]["monthly_effect"] == "12.99" and r["changes"][0]["yearly_effect"] == "155.88"
    assert Decimal(r["delta"]["min_balance"]) >= 0 and r["baseline"]["available"] and r["scenario"]["available"]
    assert r["baseline"]["start_balance"] == "5000.00" and r["horizon_days"] == 90
    assert Decimal(r["scenario"]["monthly_savings"]) - Decimal(r["baseline"]["monthly_savings"]) == Decimal("12.99")


def test_category_percent_and_level_changes_use_the_coverage_aware_average():
    ds = wi_world()
    up = run(ds, {"type": "adjust_category", "category": "food.groceries", "percent": 10})
    # usual 300.00 a month: +10 % = 30.00 more spending ; spread evenly: 30.00 / 30.4375 per day x 90 days = 88.71
    assert up["changes"][0]["evidence"]["usual_monthly"] == "300.00"
    assert (up["delta"]["monthly_savings"], up["delta"]["yearly_impact"], up["delta"]["end_balance"]) == ("-30.00", "-360.00", "-88.71")
    lvl = run(ds, {"type": "set_category_level", "category": "food", "monthly_target": 250})
    assert lvl["delta"]["monthly_savings"] == "50.00" and lvl["delta"]["yearly_impact"] == "600.00"
    with pytest.raises(ValueError):
        run(ds, {"type": "adjust_category", "category": "travel.flights", "percent": 5})         # no spending: no made-up average


def test_monthly_amounts_one_offs_and_income_changes():
    ds = wi_world()
    add = run(ds, {"type": "add_monthly", "amount": 50, "start_date": "2026-11-01"})
    # 11-01, 12-01 and 2027-01-01 are inside the horizon: 150.00 ; 12 payments in the next 12 months: 600.00 a year
    assert add["delta"]["end_balance"] == "-150.00" and add["delta"]["monthly_savings"] == "-50.00" and add["delta"]["yearly_impact"] == "-600.00"
    rem = run(ds, {"type": "remove_monthly", "amount": 20})
    assert rem["delta"]["monthly_savings"] == "20.00" and rem["delta"]["yearly_impact"] == "240.00"
    one = run(ds, {"type": "one_off", "amount": 1200, "date": "2026-11-01"})
    assert one["delta"]["end_balance"] == "-1200.00" and one["delta"]["one_off_next_12_months"] == "-1200.00"
    assert one["delta"]["yearly_impact"] == "-1200.00" and one["delta"]["monthly_savings"] == "0.00"
    gift = run(ds, {"type": "one_off", "amount": 300, "date": "2026-11-10", "direction": "in"})
    assert gift["delta"]["end_balance"] == "300.00"
    inc = run(ds, {"type": "change_income", "percent": 10})
    # salary 2,500 a month: +250.00 on the 25th: 10-25, 11-25, 12-25 inside the horizon = 750.00
    assert inc["delta"]["monthly_savings"] == "250.00" and inc["delta"]["yearly_impact"] == "3000.00" and inc["delta"]["end_balance"] == "750.00"
    fixed = run(ds, {"type": "change_income", "monthly_delta": -100, "from_date": "2026-11-01"})
    assert fixed["delta"]["end_balance"] == "-200.00" and fixed["delta"]["monthly_savings"] == "-100.00"
    with pytest.raises(ValueError):
        run(ds, {"type": "change_income", "percent": 5, "monthly_delta": 5})


def test_several_changes_add_up_and_a_scenario_can_push_the_balance_negative():
    ds = wi_world()
    r = run(ds, {"type": "one_off", "amount": 9000, "date": "2026-10-20"}, {"type": "cancel_recurring", "series": rec_id(ds, "STREAMA")})
    assert Decimal(r["delta"]["end_balance"]) == Decimal("38.97") - Decimal("9000.00")
    assert r["scenario"]["first_negative"] == dt.date(2026, 10, 20) and r["baseline"]["first_negative"] is None
    assert len(r["changes"]) == 2


def liab(**kw):
    base = dict(id="car-loan", kind="car_loan", lender="Lender", start_date=dt.date(2020, 10, 1), end_date=dt.date(2030, 10, 1),
                principal=120000, rate=S.Rate(nominal=0.0), monthly_payment=1000)
    base.update(kw)
    return ("liabilities/car-loan.yaml", S.Liability(**base))


def test_prepaying_a_loan_exact_when_the_schedule_is_known():
    ds = wi_world(memory=MemorySnapshot(liabilities=[liab()]))
    # 72 instalments paid (2020-11-01 .. 2026-10-01): 48,000.00 left over 48 months; prepay 12,000.00
    keep = run(ds, {"type": "prepay_loan", "liability": "car-loan", "amount": 12000, "date": "2026-11-01"})
    c = keep["changes"][0]
    assert (c["capital_before"], c["capital_after"], c["months_saved"], c["method"], c["approximate"]) == ("48000.00", "36000.00", 12, "amortization", False)
    assert c["one_off"] == "-12000.00" and c["monthly_effect"] == "0.00" and keep["delta"]["yearly_impact"] == "-12000.00"
    term = run(ds, {"type": "prepay_loan", "liability": "car-loan", "amount": 12000, "date": "2026-11-01", "keep": "term"})
    # the instalment goes from 1,000.00 to 750.00 (36,000 over 48 months): +250.00 a month from December
    assert term["changes"][0]["instalment_after"] == "750.00" and term["delta"]["monthly_savings"] == "250.00"
    assert term["delta"]["yearly_impact"] == "-9000.00"                                          # -12,000 now + 12 x 250


def test_prepaying_without_the_rate_is_flagged_approximate_and_without_the_capital_only_the_cash_moves():
    part = liab(principal=None, rate=None, start_date=None, outstanding=48000, outstanding_as_of=TODAY - dt.timedelta(days=10))
    ds = wi_world(memory=MemorySnapshot(liabilities=[part]))
    c = run(ds, {"type": "prepay_loan", "liability": "car-loan", "amount": 12000, "date": "2026-11-01"})["changes"][0]
    assert c["approximate"] is True and any("back-solved" in n for n in c["notes"])
    none = liab(principal=None, rate=None, start_date=None, end_date=None)
    ds2 = wi_world(memory=MemorySnapshot(liabilities=[none]))
    r = run(ds2, {"type": "prepay_loan", "liability": "car-loan", "amount": 5000, "date": "2026-11-01"})
    assert r["changes"][0]["approximate"] is True and r["changes"][0]["needs"] and r["delta"]["end_balance"] == "-5000.00"
    with pytest.raises(ValueError):
        run(ds, {"type": "prepay_loan", "liability": "nope", "amount": 1, "date": "2026-11-01"})


def test_unknown_series_and_the_schema_is_strict():
    import jsonschema
    ds = wi_world()
    with pytest.raises(ValueError):
        run(ds, {"type": "cancel_recurring", "series": "rec_deadbeef01"})
    v = jsonschema.Draft202012Validator({"type": "object", "properties": {"scenario": WI.SCENARIO_SCHEMA}, "required": ["scenario"]})
    ok = {"scenario": {"changes": [{"type": "one_off", "amount": 10, "date": "2026-11-01"}]}}
    assert not list(v.iter_errors(ok))
    bad = [{"scenario": {"changes": []}}, {"scenario": {}}, {"scenario": {"changes": [{"type": "teleport"}]}},
           {"scenario": {"changes": [{"type": "one_off", "amount": 10, "date": "tomorrow"}]}},
           {"scenario": {"changes": [{"type": "one_off", "amount": 10, "date": "2026-11-01", "colour": "red"}]}},
           {"scenario": {"changes": [{"type": "adjust_category", "category": "food", "percent": 900}]}},
           {"scenario": {"changes": [{"type": "cancel_recurring", "series": "../etc/passwd"}]}},
           {"scenario": {"days": 5, "changes": [{"type": "one_off", "amount": 10, "date": "2026-11-01"}]}},
           {"scenario": {"changes": [{"type": "one_off", "amount": 10, "date": "2026-11-01"}] * 7}}]
    for b in bad:
        assert list(v.iter_errors(b)), b


# ---------------------------------------------------------------- tax_candidates

def members(*specs):
    return [S.Member(id=i, name=i.title(), role=r, birth_year=y) for i, r, y in specs]


def tax_ds(txs, memory=None):
    return make_ds(txs, history={"a": ("2025-01-01", "2026-10-03")}, memory=memory or MemorySnapshot())


def cand(res, cid):
    return next((c for c in res["candidates"] if c["id"] == cid), None)


def test_fr_donations_range_and_other_years_are_ignored():
    ds = tax_ds([tx("2026-03-10", -500, "charity.donations", entity="HELPCHARITY"), tx("2026-06-10", -1000, "charity.donations", entity="HELPCHARITY"),
                 tx("2025-06-10", -300, "charity.donations", entity="HELPCHARITY")])
    res = TX.tax_candidates(ds, 2026, "FR")
    c = cand(res, "fr-dons")
    # 1,500 given: all at 66 % = 990.00 ; at best 75 % of the first 1,000 (750.00) + 66 % of 500 (330.00) = 1,080.00
    assert (c["spent"], c["n_tx"], c["estimated_benefit"]) == ("1500.00", 2, {"low": "990.00", "high": "1080.00"})
    assert c["organisations"][0]["entity"] == "HELPCHARITY" and c["organisations"][0]["total"] == "1500.00"
    assert "recu" in " ".join(c["documents_to_keep"]) and "impots.gouv.fr" in res["disclaimer"]
    assert TX.tax_candidates(ds, 2025, "FR")["candidates"][0]["spent"] == "300.00"
    assert res["income_year_note"].endswith("spring of 2027")


def test_fr_home_employment_childcare_and_school_fees():
    kids = members(("mia", "child", 2012), ("leo", "child", 2009), ("eva", "child", 2007), ("tom", "child", None), ("ann", "adult", 1985))
    txs = [tx(f"2026-{m:02d}-15", -200, "housing.maintenance_diy", entity="HOMEHELP", tags=("cesu",)) for m in range(1, 13) if m <= 9]
    txs += [tx("2026-02-10", -1800, "housing.maintenance_diy", entity="HOMEHELP", tags=("cesu",))]
    ds = tax_ds(txs, MemorySnapshot(members=kids))
    res = TX.tax_candidates(ds, 2026, "FR")
    e = cand(res, "fr-emploi-domicile")
    # 9 x 200 + 1,800 = 3,600 ; dependents: mia 14, leo 17, tom (unknown age counted) = 3 -> 12,000 + 3 x 1,500 = 16,500 capped at 15,000
    assert e["spent"] == "3600.00" and "15000.00" in e["ceilings"][0] and e["estimated_benefit"] == {"low": "1800.00", "high": "1800.00"}
    sc = cand(res, "fr-scolarite")
    assert [(c["id"], c["stage"], c["amount"]) for c in sc["children"]] == [("mia", "college", "61.00"), ("leo", "lycee", "153.00"), ("eva", "superieur", "183.00")]
    assert sc["estimated_benefit"] == {"low": "397.00", "high": "397.00"} and sc["missing_info"] == ["birth year of: tom"]
    young = members(("zoe", "child", 2022), ("max", "child", 2018))
    care = tax_ds([tx("2026-04-01", -4000, "kids.childcare", entity="CRECHE")], MemorySnapshot(members=young))
    g = cand(TX.tax_candidates(care, 2026, "FR"), "fr-garde-enfants")
    # one child under 6 (zoe, 2022): ceiling 3,500 -> credit 50 % = 1,750.00 ; max (2018) is 8: not eligible
    assert g["estimated_benefit"] == {"low": "1750.00", "high": "1750.00"} and "1 child" in g["ceilings"][0]
    none = tax_ds([tx("2026-04-01", -4000, "kids.childcare", entity="CRECHE")], MemorySnapshot(members=members(("max", "child", 2015))))
    n = cand(TX.tax_candidates(none, 2026, "FR"), "fr-garde-enfants")
    assert n["estimated_benefit"] == {"low": "0.00", "high": "0.00"} and "no child under 6" in n["missing_info"][0]


def test_fr_pinel_reminder_alimony_and_per_are_listed_without_a_made_up_saving():
    def pinel(py, yrs=9):
        return S.Asset(id="flat", kind="real_estate_rental", scheme="pinel", pinel_commitment_years=yrs, purchase_price=250000,
                       purchase_date=dt.date(py, 6, 1))
    ds = tax_ds([], MemorySnapshot(assets=[pinel(2022)]))
    p = cand(TX.tax_candidates(ds, 2026, "FR"), "fr-pinel")
    assert p["estimated_benefit"] == {"low": "5000.00", "high": "5000.00"}                       # 250,000 x 18 % / 9 years = 2 % a year
    p23 = cand(TX.tax_candidates(tax_ds([], MemorySnapshot(assets=[pinel(2023)])), 2026, "FR"), "fr-pinel")
    assert p23["estimated_benefit"] == {"low": "4166.67", "high": "5000.00"}                      # reduced 2023 rate 15 % / 9 .. classic 18 % / 9
    over = cand(TX.tax_candidates(tax_ds([], MemorySnapshot(assets=[pinel(2015)])), 2026, "FR"), "fr-pinel")
    assert over["estimated_benefit"] == {"low": None, "high": None} and "does not include 2026" in " ".join(over["notes"])
    miss = S.Asset(id="flat", kind="real_estate_rental", pinel_commitment_years=6)
    assert set(cand(TX.tax_candidates(tax_ds([], MemorySnapshot(assets=[miss])), 2026, "FR"), "fr-pinel")["missing_info"]) == {"purchase_price", "purchase_date"}
    ded = tax_ds([tx("2026-05-01", -600, "transfer.to_people", entity="EXPARTNER", tags=("pension-alimentaire",)),
                  tx("2026-05-02", -2000, "transfer.internal", entity="PERPROVIDER", tags=("per", "savings"))])
    r = TX.tax_candidates(ded, 2026, "FR")
    assert cand(r, "fr-pension-alimentaire")["spent"] == "600.00" and cand(r, "fr-per")["spent"] == "2000.00"
    assert cand(r, "fr-per")["estimated_benefit"] == {"low": None, "high": None} and any("not a recommendation" in n for n in cand(r, "fr-per")["notes"])


def test_it_730_candidates_hand_computed():
    mortgage = S.Liability(id="mutuo", kind="mortgage", start_date=dt.date(2020, 1, 1), end_date=dt.date(2050, 1, 1), principal=300000,
                           rate=S.Rate(nominal=3.5))
    kids = members(("ale", "child", 2015), ("ann", "adult", 1985))
    txs = [tx("2026-02-01", -629.11, "health.pharmacy", entity="FARMACIA"), tx("2026-03-01", -500, "health.doctors", entity="DOTTORE"),
           tx("2026-04-01", -10000, "housing.renovation", entity="IMPRESA"), tx("2026-05-01", -1000, "kids.school", entity="SCUOLA"),
           tx("2026-06-01", -600, "insurance.life", entity="ASSICURA"), tx("2026-07-01", -1000, "charity.donations", entity="ONLUS")]
    r = TX.tax_candidates(tax_ds(txs, MemorySnapshot(members=kids, liabilities=[("liabilities/mutuo.yaml", mortgage)])), 2026, "IT")
    assert r["country"] == "IT" and "Agenzia" in r["disclaimer"]
    # medical: 629.11 + 500 = 1,129.11 ; above the 129.11 franchise = 1,000.00 ; 19 % = 190.00
    assert cand(r, "it-spese-mediche")["estimated_benefit"] == {"low": "190.00", "high": "190.00"}
    # renovation 2026 is NOT settled: 30 % (older schedule, other property) .. 50 % (main home if the budget law extended it) of 10,000
    ren = cand(r, "it-ristrutturazioni")
    assert ren["estimated_benefit"] == {"low": "3000.00", "high": "5000.00"} and ren["per_year_over_10_years"] == {"low": "300.00", "high": "500.00"}
    assert ren["rates_info"]["confidence"] == "low" and "CHECK CURRENT LAW" in ren["rates_info"]["note"] and ren["rates_info"]["last_reviewed"] == "2026-10"
    assert any("120,000" in n and "16-ter" in n for n in cand(r, "it-spese-mediche")["notes"])
    assert cand(r, "it-istruzione")["estimated_benefit"] == {"low": "152.00", "high": "152.00"}      # 19 % of min(1,000, 800 x 1 student)
    assert cand(r, "it-assicurazioni")["estimated_benefit"] == {"low": "0.00", "high": "100.70"}     # 19 % of min(600, 530)
    assert cand(r, "it-erogazioni-liberali")["estimated_benefit"] == {"low": "260.00", "high": "350.00"}
    mu = cand(r, "it-interessi-mutuo")
    # a 300,000 EUR 30-year loan at 3.5 % pays far more than 4,000 EUR of interest in 2026: the 4,000 ceiling -> 19 % = 760.00
    assert float(mu["interest_year_computed"]) > 4000 and mu["estimated_benefit"] == {"low": "760.00", "high": "760.00"}
    # independent float reference: instalment k is due `k` months after 2020-01-01, so 2026 holds k = 72 .. 83
    rate = 3.5 / 1200
    pay = 300000 * rate / (1 - (1 + rate) ** -360)
    bal, interest = 300000.0, 0.0
    for k in range(1, 84):
        i = bal * rate
        bal -= pay - i
        if k >= 72:
            interest += i
    assert float(mu["interest_year_computed"]) == pytest.approx(interest, abs=0.5)


def test_it_mortgage_without_its_terms_says_what_is_missing_and_defaults_come_from_the_household():
    bare = S.Liability(id="mutuo", kind="mortgage")
    r = TX.tax_candidates(tax_ds([], MemorySnapshot(liabilities=[("liabilities/mutuo.yaml", bare)])), 2026, "IT")
    mu = cand(r, "it-interessi-mutuo")
    assert mu["estimated_benefit"] == {"low": None, "high": None} and "principal" in mu["missing_info"][0]
    assert TX.tax_candidates(tax_ds([]), 2026, None, household_country="IT")["country"] == "IT"
    assert TX.tax_candidates(tax_ds([]), 2026)["country"] == "FR"
    assert TX.tax_candidates(tax_ds([]))["year"] == 2026                            # October: the year in progress
    ids = {n["id"] for n in TX.tax_candidates(tax_ds([]), 2026, "FR")["no_data_for"]}
    assert {"fr-dons", "fr-emploi-domicile", "fr-garde-enfants", "fr-per"} <= ids
    for bad in ((1999, "FR"), (2026, "DE"), (2030, "FR")):
        with pytest.raises(ValueError):
            TX.tax_candidates(tax_ds([]), *bad)


def test_it_renovation_rates_are_a_dated_table_with_a_confidence():
    for year, lo, hi, conf, ceil in ((2024, 50, 50, "high", 96000), (2025, 36, 50, "medium", 96000), (2026, 30, 50, "low", 96000),
                                     (2027, 30, 50, "low", 96000), (2029, 30, 30, "low", 48000)):
        t = TX.it_renovation_rates(year)
        assert (t["low_rate"], t["high_rate"], t["confidence"], t["ceiling_eur"], t["last_reviewed"]) == (lo, hi, conf, ceil, "2026-10"), year
    ds = tax_ds([tx("2029-01-10", -60000, "housing.renovation", entity="IMPRESA")])
    r = TX.tax_candidates(make_ds([tx("2029-01-10", -60000, "housing.renovation", entity="IMPRESA")], today=dt.date(2029, 6, 1),
                                  history={"a": ("2028-01-01", "2029-05-30")}), 2029, "IT")
    # 2029: fallback 30 %, ceiling 48,000: 48,000 x 30 % = 14,400.00
    assert cand(r, "it-ristrutturazioni")["estimated_benefit"] == {"low": "14400.00", "high": "14400.00"}
    assert ds


def test_fr_home_employment_counts_members_over_65_and_states_the_other_ceilings():
    mem = members(("gran", "adult", 1955), ("pat", "adult", 1985))
    ds = tax_ds([tx("2026-03-01", -3000, "housing.maintenance_diy", entity="HELP", tags=("cesu",))], MemorySnapshot(members=mem))
    e = cand(TX.tax_candidates(ds, 2026, "FR"), "fr-emploi-domicile")
    # one member over 65 (born 1955): 12,000 + 1,500 = 13,500 ; first year 15,000 + 1,500 = 16,500 ; disability 20,000
    assert "13500.00" in e["ceilings"][0] and "1 adult(s) over 65" in e["ceilings"][0]
    assert "16500.00" in e["ceilings"][1] and "max 18,000" in e["ceilings"][1] and "20,000" in e["ceilings"][2]
    assert e["last_reviewed"] == "2026-10" and e["source"]


def test_every_tax_candidate_carries_its_source_and_a_review_date():
    r = TX.tax_candidates(tax_ds([tx("2026-03-10", -500, "charity.donations", entity="HELPCHARITY"), tx("2026-04-10", -100, "health.pharmacy", entity="PHARMA")]),
                          2026, "IT")
    assert r["candidates"] and all(c["source"] and c["last_reviewed"] == "2026-10" for c in r["candidates"])
    assert any("75,000" in n for c in r["candidates"] for n in c["notes"])
