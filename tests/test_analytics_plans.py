"""E4-7 budgets, E4-8 calendar, E4-9 goals, E4-10 year in review (pure functions over hand-built datasets)."""
from types import SimpleNamespace

from anhelpers import D, TODAY, account, consent, make_ds, monthly, tx
from coach.analytics import budgets, goals, review, upcoming
from coach.analytics.common import add_months
from coach.analytics.dataset import MemorySnapshot
from coach.memory import schemas

SYNC = {"a": "2026-10-04", "k": "2026-10-04"}


def months_of(category, amounts, start="2026-03-10", **kw):
    return [tx(add_months(D(start), i), -a, category, entity=f"{category}{i}", **kw) for i, a in enumerate(amounts)]


# ---------------------------------------------------------------- budgets

def test_suggestions_are_the_rounded_median_of_covered_months():
    txs = (months_of("food.groceries", [212, 198, 240, 205, 230, 260, 220])
           + months_of("leisure.hobbies", [87, 90, 80, 85, 95, 87, 70])
           + months_of("housing.energy", [1234, 1250, 1200, 1230, 1240, 1236, 1280])
           + months_of("fees.bank_fees", [4, 5, 6, 7, 8, 9, 5])                              # median 6 EUR: below the 10 EUR floor
           + months_of("other.uncategorized", [300] * 7)
           + months_of("shopping.electronics", [50, 50, 40, 60, 55, 45, 50]))
    txs.append(tx("2026-05-02", -900.0, "shopping.electronics", entity="TV", tags=["one_off"]))   # a one-off never shapes a budget
    ds = make_ds(txs, last_sync=SYNC)
    r = budgets.suggest_budgets(ds, months=6)
    sug = {s.category: s for s in r.suggestions}
    # the account starts on 10 March, so the six covered months are April-September (the March payment is outside)
    assert sug["food.groceries"].median_c == 22500 and sug["food.groceries"].suggested_c == 23000     # [198,205,220,230,240,260] -> 225 -> 230
    assert sug["leisure.hobbies"].median_c == 8600 and sug["leisure.hobbies"].suggested_c == 8500     # nearest 5 EUR
    assert sug["housing.energy"].median_c == 123800 and sug["housing.energy"].suggested_c == 125000   # nearest 50 EUR
    assert sug["shopping.electronics"].median_c == 5000
    assert "fees.bank_fees" not in sug and "other.uncategorized" not in sug
    reasons = {s["category"]: s["reason"] for s in r.skipped}
    assert "below 10 EUR" in reasons["fees.bank_fees"] and "classify" in reasons["other.uncategorized"]
    assert all(s.n_months == 6 for s in r.suggestions) and list(sug)[0] == "housing.energy"        # biggest first
    assert [s.category for s in budgets.suggest_budgets(ds, months=6, limit=2).suggestions] == ["housing.energy", "food.groceries"]


def test_suggestion_shows_the_existing_budget_and_low_confidence():
    b = schemas.Budget(id="groceries", category="food.groceries", monthly=250)
    ds = make_ds(months_of("food.groceries", [200, 210], start="2026-07-10"), last_sync=SYNC, memory=MemorySnapshot(budgets=[b]))
    (s,) = budgets.suggest_budgets(ds).suggestions
    assert s.existing_c == 25000 and s.low_confidence is True and s.n_months == 2


def _status_ds(budget_list, txs, today=D("2026-10-10"), **kw):
    return make_ds(txs, today=today, last_sync={"a": today.isoformat()}, memory=MemorySnapshot(budgets=budget_list),
                   history={"a": ("2026-01-01", None)}, **kw)


def test_status_spent_remaining_percent_and_linear_projection():
    b = schemas.Budget(id="groceries", category="food.groceries", monthly=300)
    txs = [tx("2026-10-02", -50.0, "food.groceries", entity="S1"), tx("2026-10-07", -70.0, "food.groceries", entity="S2"),
           tx("2026-09-20", -999.0, "food.groceries", entity="S3"),                      # last month: not this month
           tx("2026-10-08", -400.0, "housing.renovation", entity="B", tags=["one_off"]),
           tx("2026-10-09", -20.0, "leisure.hobbies", entity="H")]
    r = budgets.budget_status(_status_ds([b], txs), recurring=None)
    (p,) = r.budgets
    assert (p.spent_c, p.available_c, p.remaining_c, p.percent_used) == (12000, 30000, 18000, 0.4)
    assert p.method == "pace+recurring" and p.projected_c == 37200                                # no recurring payment found: 120 x 31 / 10
    assert p.status == "at_risk" and r.counts == {"over": 0, "at_risk": 1, "ok": 0}
    assert p.evidence == [txs[1].key, txs[0].key]
    assert [u["category"] for u in r.unbudgeted][0] == "leisure.hobbies"                  # the one-off renovation is not listed
    assert r.month == "2026-10"


def test_first_days_of_the_month_use_the_plain_linear_pace():
    b = schemas.Budget(id="groceries", category="food.groceries", monthly=300)
    (p,) = budgets.budget_status(_status_ds([b], [tx("2026-10-01", -40.0, "food.groceries", entity="S1")], today=D("2026-10-02"))).budgets
    assert p.method == "pace" and p.projected_c == 4000 * 31 // 2                                  # 40 EUR in 2 days -> 620 over 31 days


def test_status_over_and_ok_and_one_offs_reported_apart():
    over = schemas.Budget(id="fun", category="leisure.hobbies", monthly=50)
    ok = schemas.Budget(id="food", category="food.groceries", monthly=600)
    txs = [tx("2026-10-02", -80.0, "leisure.hobbies", entity="H1"), tx("2026-10-03", -60.0, "food.groceries", entity="F1"),
           tx("2026-10-04", -300.0, "leisure.hobbies", entity="H2", tags=["one_off"])]
    r = budgets.budget_status(_status_ds([over, ok], txs))
    by = {p.id: p for p in r.budgets}
    assert by["fun"].status == "over" and by["fun"].spent_c == 8000 and by["fun"].excluded_c == 30000 and by["fun"].percent_used == 1.6
    assert by["food"].status == "ok" and [p.id for p in r.budgets] == ["fun", "food"]               # over first


def test_projection_adds_the_recurring_payment_still_to_come():
    from coach.analytics.recurring import detect_recurring
    b = schemas.Budget(id="sport", group="leisure", monthly=200)
    gym = monthly("2026-05-20", 5, -40.0, "leisure.sports_activities", entity="GYM")           # May-Sep, next 20 October
    noise = [tx("2026-10-05", -60.0, "leisure.cinema_events", entity="CINEMA")]
    ds = _status_ds([b], gym + noise)
    r = budgets.budget_status(ds, recurring=detect_recurring(ds))
    (p,) = r.budgets
    assert p.method == "pace+recurring" and p.spent_c == 6000
    # spent 60 (all non-recurring) + 40 still to come on the 20th + 60 over 10 elapsed days -> 21 remaining days: 60 + 40 + 126
    assert p.projected_c == 6000 + 4000 + 12600 and p.status == "at_risk"


def test_rollover_carries_unspent_money_only_since_start():
    b = schemas.Budget(id="food", category="food.groceries", monthly=300, rollover=True, start=D("2026-07-01"))
    txs = [tx("2026-07-10", -200.0, "food.groceries", entity="A"), tx("2026-08-10", -350.0, "food.groceries", entity="B"),
           tx("2026-09-10", -100.0, "food.groceries", entity="C"), tx("2026-06-10", -10.0, "food.groceries", entity="D"),
           tx("2026-10-03", -50.0, "food.groceries", entity="E")]
    (p,) = budgets.budget_status(_status_ds([b], txs)).budgets
    assert p.carry_c == 10000 + 0 + 20000                         # July +100, August overspent 0, September +200
    assert (p.available_c, p.remaining_c) == (60000, 55000) and "includes_carry_over" in p.flags
    nostart = schemas.Budget(id="food", category="food.groceries", monthly=300, rollover=True)
    assert budgets.budget_status(_status_ds([nostart], txs)).budgets[0].carry_c == 0


def test_owner_and_account_filters_and_future_start():
    kid = account("k", "Kid", owner="alex", purpose="kids")
    txs = [tx("2026-10-02", -30.0, "leisure.hobbies", entity="H1"), tx("2026-10-03", -12.0, "leisure.hobbies", "k", entity="H2")]
    mine = schemas.Budget(id="kid-fun", category="leisure.hobbies", monthly=50, owner="alex")
    acct = schemas.Budget(id="main-fun", category="leisure.hobbies", monthly=50, account="Main")
    later = schemas.Budget(id="later", category="leisure.hobbies", monthly=50, start=D("2026-12-01"))
    ds = make_ds(txs, accounts=[account(), kid], today=D("2026-10-10"), last_sync={"a": "2026-10-10", "k": "2026-10-10"},
                 memory=MemorySnapshot(budgets=[mine, acct, later]), history={"a": ("2026-01-01", None), "k": ("2026-01-01", None)})
    by = {p.id: p for p in budgets.budget_status(ds).budgets}
    assert by["kid-fun"].spent_c == 1200 and by["main-fun"].spent_c == 3000 and "later" not in by


def test_budget_ids_and_edit_operations():
    assert budgets.budget_id_for("food.groceries", set()) == "food-groceries"
    assert budgets.budget_id_for("food.groceries", {"food-groceries"}) == "food-groceries-2"
    assert budgets.budget_id_for("group:food", set()) == "group-food" and budgets.budget_id_for("food", set()) == "group-food"
    ops = budgets.budget_ops(set(), False, "b1", {"category": "food.groceries", "monthly": 300})
    assert [o["op"] for o in ops] == ["create", "append"] and ops[1]["value"] == {"id": "b1", "category": "food.groceries", "monthly": 300}
    assert [o["op"] for o in budgets.budget_ops(set(), True, "b1", {"monthly": 1})] == ["append"]
    assert budgets.budget_ops({"b1"}, True, "b1", {"monthly": 5, "rollover": True}) == [
        {"op": "set", "path": "budgets[b1].monthly", "value": 5}, {"op": "set", "path": "budgets[b1].rollover", "value": True}]


# ---------------------------------------------------------------- calendar

def _mem(**kw):
    return MemorySnapshot(**kw)


def test_calendar_contract_dates_notice_deadline_consents_assets_and_loans():
    contract = schemas.Contract(id="internet", provider="NetCo", renewal=D("2026-11-20"), notice_period_days=30,
                                billing=schemas.Billing(amount=29.99, period="monthly"), commitment_end=D("2027-03-01"))
    loa = schemas.Liability(id="car-loa", kind="loa", end_date=D("2026-12-15"))
    loan = schemas.Liability(id="plain-loan", kind="consumer_loan", end_date=D("2027-06-01"), monthly_payment=100.0,
                             start_date=D("2024-06-12"))
    stale = schemas.Asset(id="savings", kind="regulated_savings", balance=1000, as_of=D("2026-07-01"))
    fresh = schemas.Asset(id="fresh", kind="regulated_savings", balance=1000, as_of=D("2026-10-01"))
    novalue = schemas.Asset(id="house", kind="real_estate")
    mem = _mem(contracts=[("c", contract)], liabilities=[("l1", loa), ("l2", loan)], assets=[stale, fresh, novalue])
    cons = [consent("Bank One", "2026-12-30T10:00:00Z"), consent("Bank Two", "2027-06-01T00:00:00Z", sid="s2"),
            consent("Old Bank", "2026-01-01T00:00:00Z", status="expired", sid="s3")]
    ds = make_ds([tx("2026-09-01", -1.0)], memory=mem, consents=cons)
    r = upcoming.calendar_items(ds, 90)
    got = {(i.source, i.kind, i.ref): i for i in r.items}
    assert got[("contract", "renewal", "internet")].date == D("2026-11-20") and got[("contract", "renewal", "internet")].amount_c == -2999
    assert got[("contract", "notice_deadline", "internet")].date == D("2026-10-21")           # 30 days before the renewal
    assert ("contract", "commitment_end", "internet") not in got                              # beyond 90 days
    assert got[("liability", "loan_end", "car-loa")].date == D("2026-12-15") and "residual" in got[("liability", "loan_end", "car-loa")].note
    pays = [i for i in r.items if i.source == "liability" and i.kind == "loan_payment"]
    assert [i.date for i in pays] == [D("2026-10-12"), D("2026-11-12"), D("2026-12-12")] and pays[0].amount_c == -10000
    assert got[("consent", "consent_expiry", "s1")].date == D("2026-12-30") and ("consent", "consent_expiry", "s2") not in got
    assert got[("consent", "consent_expiry", "s3")].date == TODAY                             # an expired consent is due today
    assert got[("asset", "asset_stale", "savings")].date == TODAY                              # stale since 1 October
    assert got[("asset", "asset_stale", "fresh")].date == D("2027-01-01") and got[("asset", "asset_stale", "fresh")].days_until == 89
    assert got[("asset", "asset_no_value", "house")].date == TODAY
    assert [i.date for i in r.items] == sorted(i.date for i in r.items)
    assert r.counts["contract"] == 2 and r.counts["consent"] == 2 and r.days == 90


def test_calendar_recurring_items_and_transfers_option():
    txs = (monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
           + monthly("2026-04-02", 6, -300.0, "transfer.internal", entity="TOCARD"))
    ds = make_ds(txs, last_sync={"a": "2026-10-04"})
    r = upcoming.calendar_items(ds, 40)
    s = [i for i in r.items if i.source == "recurring"]
    assert [(i.date, i.title) for i in s] == [(D("2026-10-05"), "STREAMBOX"), (D("2026-11-05"), "STREAMBOX")]
    assert s[0].days_until == 1 and s[0].amount_c == -999 and s[0].kind == "payment"
    assert len(upcoming.calendar_items(ds, 40, include_transfers=True).items) > len(s)
    assert upcoming.calendar_items(ds, 40).to_dict() == r.to_dict()


def test_ics_export_is_valid_and_deterministic():
    contract = schemas.Contract(id="internet", provider="Net; Co, Ltd", renewal=D("2026-11-20"))
    ds = make_ds([tx("2026-09-01", -1.0)], memory=_mem(contracts=[("c", contract)]))
    r = upcoming.calendar_items(ds, 60)
    text = upcoming.to_ics(r)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and text.count("BEGIN:VEVENT") == 1
    assert "DTSTART;VALUE=DATE:20261120" in text and "DTEND;VALUE=DATE:20261121" in text
    assert "SUMMARY:Net\\; Co\\, Ltd renews" in text and "DTSTAMP:20261004T000000Z" in text
    assert text == upcoming.to_ics(upcoming.calendar_items(ds, 60))
    long_title = SimpleNamespace(as_of=TODAY, items=[upcoming.CalendarItem(D("2026-11-01"), 28, "contract", "renewal", "X" * 200, None, "id", "scheduled")])
    assert all(len(line.encode()) <= 75 for line in upcoming.to_ics(long_title).split("\r\n"))


# ---------------------------------------------------------------- goals

def _goal(**kw):
    base = {"id": "g", "target_amount": 3000}
    return schemas.Goal(**{**base, **kw})


def test_goal_from_an_asset_with_planned_contribution():
    asset = schemas.Asset(id="holiday-pot", kind="regulated_savings", balance=1500, as_of=D("2026-09-30"))
    ds = make_ds([tx("2026-09-01", -1.0)], memory=_mem(assets=[asset]))
    g = _goal(asset="holiday-pot", target_date=D("2027-06-30"), monthly_contribution=250)
    (p,) = goals.goal_progress(ds, [g]).goals
    assert (p.current_c, p.remaining_c, p.percent) == (150000, 150000, 0.5)
    assert (p.pace_c, p.pace_basis, p.projected_date, p.status) == (25000, "planned", D("2027-04-04"), "on_track")
    assert p.months_left == 9 and p.required_monthly_c == 16667 and p.flags == []
    behind = goals.goal_progress(ds, [_goal(asset="holiday-pot", target_date=D("2027-01-31"), monthly_contribution=250)]).goals[0]
    assert behind.status == "behind" and behind.required_monthly_c == 37500          # 1,500 over 4 months
    nopace = goals.goal_progress(ds, [_goal(asset="holiday-pot")]).goals[0]
    assert nopace.status == "no_pace" and nopace.projected_date is None


def test_goal_status_achieved_overdue_nodata_and_stale_flags():
    asset = schemas.Asset(id="pot", kind="regulated_savings", balance=3200, as_of=D("2026-01-01"))
    ds = make_ds([tx("2026-09-01", -1.0)], memory=_mem(assets=[asset]))
    done = goals.goal_progress(ds, [_goal(asset="pot")]).goals[0]
    assert done.status == "achieved" and "stale_value" in done.flags and done.percent > 1
    late = goals.goal_progress(ds, [_goal(asset="pot", target_amount=5000, target_date=D("2026-06-30"), monthly_contribution=100)]).goals[0]
    assert late.status == "overdue"
    missing = goals.goal_progress(ds, [_goal(asset="ghost"), _goal(id="h", account="nope")])
    assert [p.status for p in missing.goals] == ["no_data", "no_data"] and "unknown_asset" in missing.goals[0].flags
    r = goals.goal_progress(ds, [_goal(asset="pot", target_amount=5000, target_date=D("2026-06-30"), monthly_contribution=100), _goal(id="z", asset="pot")])
    assert [p.status for p in r.goals] == ["overdue", "achieved"] and r.counts == {"overdue": 1, "achieved": 1}


def test_goal_from_tagged_savings_flows():
    txs = monthly("2026-03-09", 7, -500.0, "transfer.internal", entity="PLAN", tags=["savings"]) + [tx("2026-09-20", -1.0)]
    ds = make_ds(txs, last_sync={"a": "2026-10-04"})
    g = _goal(tag="savings", target_amount=6000, baseline=1000, target_date=D("2027-03-31"))
    (p,) = goals.goal_progress(ds, [g]).goals
    assert p.current_c == 100000 + 350000 and p.pace_c == 50000 and p.pace_basis == "actual"        # 500 a month, last 6 covered
    assert p.remaining_c == 150000 and p.projected_date == D("2027-01-04") and p.status == "on_track"
    assert len(p.evidence) == 7
    since = goals.goal_progress(ds, [_goal(tag="savings", target_amount=6000, start=D("2026-07-01"))]).goals[0]
    assert since.current_c == 150000                                                              # Jul, Aug, Sep contributions


def test_goal_from_an_account_balance_and_net_flow():
    flows = [tx(add_months(D("2026-03-05"), i), 300.0, "transfer.internal", "k", entity=f"IN{i}") for i in range(7)]
    ds = make_ds(flows, accounts=[account(), account("k", "Pocket", purpose="savings")], balances={"k": 2000.0},
                 last_sync={"k": "2026-10-04"}, history={"k": ("2026-03-01", None)})
    (p,) = goals.goal_progress(ds, [_goal(account="Pocket", target_amount=5000, target_date=D("2027-03-01"))]).goals
    assert (p.current_c, p.pace_c, p.pace_basis) == (200000, 30000, "actual")
    assert p.projected_date == D("2027-08-04") and p.status == "behind" and p.required_monthly_c is not None


# ---------------------------------------------------------------- year in review

def _two_years():
    txs = []
    for i in range(24):                                              # 2024-01 .. 2025-12
        d0 = add_months(D("2024-01-10"), i)
        txs.append(tx(d0, 3000.0, "income.salary", entity="ACME"))
        txs.append(tx(d0, -(100.0 if d0.year == 2024 else 150.0), "food.groceries", entity=f"G{i}"))
        txs.append(tx(d0, -1200.0, "housing.rent", entity="LANDLORD"))
        if d0.year == 2025:
            txs.append(tx(d0, -40.0, "leisure.hobbies", entity="CLUB"))
    txs.append(tx("2025-06-20", -2000.0, "leisure.cinema_events", entity="HALL", event="wedding", tags=["one_off"]))
    txs.append(tx("2025-06-21", -300.0, "shopping.clothing", entity="TAILOR", event="wedding"))
    txs.append(tx("2025-03-15", -500.0, "transfer.internal", tags=["savings"]))
    txs.append(tx("2026-01-05", -10.0, "food.groceries", entity="LAST"))
    return make_ds(txs, today=D("2026-02-10"), last_sync={"a": "2026-02-10"})


def test_year_review_totals_events_merchants_and_changes():
    r = review.year_review(_two_years(), 2025)
    t = r.flow.totals_all
    assert (t.income_c, t.spending_c, t.saved_c) == (12 * 300000, 12 * (15000 + 120000 + 4000) + 200000 + 30000, 50000)
    assert t.one_off_spending_c == 200000 and t.spending_ex_one_offs_c == t.spending_c - 200000
    assert r.months_listed == 12 and r.months_complete == 12 and r.partial_year is False
    assert [(e.event, e.spending_c, e.n_tx) for e in r.by_event] == [("wedding", 230000, 2)]
    assert r.top_entities[0].entity == "LANDLORD" and r.top_entities[0].spending_c == 12 * 120000
    assert any(e.entity == "HALL" and e.one_off_c == 200000 for e in r.top_entities)
    ch = {c.category: c for c in r.increases + r.decreases}
    assert (ch["food.groceries"].avg_prev_c, ch["food.groceries"].avg_year_c, ch["food.groceries"].delta_c, ch["food.groceries"].pct) == (10000, 15000, 5000, 0.5)
    assert ch["food.groceries"].months_year == 12 and ch["food.groceries"].partial is True        # 2024 only has 11 covered months
    assert ch["leisure.hobbies"].avg_prev_c == 0 and ch["leisure.hobbies"].delta_c == 4000 and ch["leisure.hobbies"].pct is None
    assert "housing.rent" not in ch                                                                 # unchanged: neither up nor down
    assert r.coverage.n_months == 12 and r.not_comparable == []


def test_categories_without_enough_covered_months_are_not_compared():
    kid = account("k", "Kid", purpose="kids")
    txs = [tx(add_months(D("2024-01-10"), i), -100.0, "food.groceries", entity=f"G{i}") for i in range(24)]
    txs += [tx("2025-11-15", -20.0, "kids.school", "k", entity="S1"), tx("2025-12-05", -20.0, "kids.school", "k", entity="S2")]
    ds = make_ds(txs, accounts=[account(), kid], today=D("2026-02-10"), last_sync={"a": "2026-02-10", "k": "2026-02-10"},
                 history={"k": ("2025-11-01", None)})
    r = review.year_review(ds, 2025)
    (n,) = r.not_comparable
    assert n["category"] == "kids.school" and n["months_year"] == 2 and n["months_prev"] == 0
    assert r.months_complete == 2 and r.partial_year is True                      # the kid account only covers Nov-Dec
    assert any("Kid" in note for note in r.coverage.notes)


def test_year_review_of_a_partial_year_says_so():
    r = review.year_review(_two_years(), 2026)
    assert r.partial_year is True and r.months_listed == 1 and r.flow.totals_all.n_months == 1
    default = review.year_review(_two_years())
    assert default.year == 2026                                         # the year of the last closed month (January 2026)
    assert any("partial" in n for n in default.coverage.notes)
    empty = review.year_review(_two_years(), 2030)
    assert empty.months_listed == 0 and empty.flow.totals_all is None
