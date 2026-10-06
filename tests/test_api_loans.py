"""E9 web API: loan detail and payments, scenarios, odometer readings, inference proposals, net worth with history. Every mutation needs the
session and the CSRF token; a calculator call writes nothing unless save is true; inferred values are only ever queued as a proposal."""
from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from apihelpers import HOST, TODAY, api, ctx, world  # noqa: F401
from coach.memory.store import MemoryStore


def store(c) -> MemoryStore:
    return MemoryStore(c.cfg.memory_dir, history=True)


def test_liabilities_carry_the_schedule_the_alerts_and_the_suggestions(ctx):
    L = ctx.get("/liabilities").json()
    loan = L["liabilities"][0]
    s = loan["schedule"]
    # the declared 180,000 (2026-01-15) wins over the theoretical table: rolled forward, 168 instalments of 1,237.57, 9 due by 2026-10-04
    assert s["status"] == "computed" and s["mode"] == "from_outstanding" and s["payment"] == "1237.57" and s["remaining_instalments"] == 159
    assert s["payments_made"] == 9 and s["payments_made"] + s["remaining_instalments"] == s["term_instalments"] == 168
    assert loan["remaining_capital"] == "171638.52" and loan["remaining_capital_source"] == "declared_rolled" and loan["payments_seen"] == 12
    assert s["outstanding_check"]["declared"] == "180000.00" and s["source"] == "declared_rolled"
    assert loan["alerts"] == [] and loan["lease"] is None and isinstance(loan["inferred"], list)
    assert s["by_year"][0]["year"] == 2026 and s["by_year"][0]["instalments"] == 11 and s["by_year"][0]["partial"] is True      # the table starts in February
    assert loan["insurance"]["monthly"] is None and loan["deferral"] is None and loan["odometer"] == []
    assert L["totals"]["outstanding_known"] == "171638.52"


def test_one_loan_in_full_and_its_payments(ctx):
    d = ctx.get("/loans/home-loan").json()
    assert d["id"] == "home-loan" and d["schedule"]["rows_count"] == 168 and len(d["schedule"]["rows"]) == 168
    assert d["schedule"]["rows"][0]["due"] == "2026-02-01" and d["schedule"]["rows"][0]["interest"] == "315.00"      # 180,000 x 2.1 % / 12
    assert d["payments"]["count"] == 12 and d["payments"]["recent"][-1]["amount"] == "1500.00"
    p = ctx.get("/loans/home-loan/payments").json()
    assert len(p["payments"]) == 12 and p["payment_match"] == "^HOMEBANK" and p["alerts"] == []
    r = ctx.get("/loans/ghost")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert ctx.get("/loans/ghost/payments").status_code == 404


def test_a_missed_payment_reaches_the_insights_feed(ctx):
    ctx.state.clock = lambda: dt.date(2026, 10, 12)                      # the payment of the 3rd (+ 5 days of grace) did not come
    with ctx.state.write() as con:
        con.execute("INSERT INTO sync_log(account_uid, ran_at, ok, new_tx, pages, note) VALUES ('ce','2026-10-12T08:00:00+00:00',1,0,1,'')")
        con.commit()
    feed = ctx.get("/insights").json()
    cards = [c for c in feed["cards"] if c["kind"] == "loan"]
    assert [c["subtype"] for c in cards] == ["missed_payment", "capital_differs"] and cards[0]["severity"] == "high" and feed["counts"]["loan"] == 2
    assert "2026-10-03" in cards[0]["title"]
    d = ctx.get("/loans/home-loan").json()
    assert [a["type"] for a in d["alerts"]] == ["missed_payment"]            # the capital difference is a card, not a payment alert


def test_the_scenario_calculator_writes_nothing_unless_saved(ctx):
    before = ctx.sql("SELECT COUNT(*) FROM insights")[0][0]
    r = ctx.post("/loans/home-loan/scenario", {"type": "prepay", "amount": 20000, "on": "2026-11-01"})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["status"] == "computed" and res["capital_before"] == "171638.52" and res["penalty"] == "210.00"      # IRA: 20,000 x 2.1 % / 2 = 210.00, below 3 % of the capital (5,149.16)
    assert {o["mode"] for o in res["options"]} == {"keep_payment", "keep_term"} and r.json()["saved_insight"] is None
    assert ctx.sql("SELECT COUNT(*) FROM insights")[0][0] == before
    reneg = ctx.post("/loans/home-loan/scenario", {"type": "renegotiate", "new_rate": 1.5, "bank_fees": 800, "variant": "rachat"}).json()["result"]
    assert reneg["status"] == "computed" and reneg["variant"] == "rachat" and reneg["bank_fees"] == "800.00"
    ins = ctx.post("/loans/home-loan/scenario", {"type": "insurance", "alternative": 20})
    assert ins.status_code == 200 and ins.json()["result"]["status"] == "needs_fields"                  # no insurance recorded on the loan
    saved = ctx.post("/loans/home-loan/scenario", {"type": "prepay", "amount": 20000, "save": True}).json()
    rows = ctx.sql("SELECT id, kind, title, skill FROM insights WHERE skill='loan-scenario'")
    assert len(rows) == 1 and rows[0][0] == saved["saved_insight"] and "home-loan" not in rows[0][2] and "mortgage" in rows[0][2]


def test_scenario_input_is_validated(ctx):
    assert ctx.post("/loans/home-loan/scenario", {"type": "prepay"}).status_code == 422
    assert ctx.post("/loans/home-loan/scenario", {"type": "renegotiate"}).status_code == 422
    assert ctx.post("/loans/home-loan/scenario", {"type": "prepay", "amount": -5}).status_code == 422
    assert ctx.post("/loans/home-loan/scenario", {"type": "nonsense"}).status_code == 422
    assert ctx.post("/loans/home-loan/scenario", {"type": "prepay", "amount": 500000}).json()["result"]["status"] == "payoff"
    assert ctx.post("/loans/ghost/scenario", {"type": "prepay", "amount": 1}).status_code == 404
    bare = ctx.put("/memory/liabilities/bare", {"fields": {"kind": "consumer_loan", "monthly_payment": 100}})
    assert bare.status_code == 200
    nope = ctx.post("/loans/bare/scenario", {"type": "prepay", "amount": 10, "save": True})
    assert nope.status_code == 422 and nope.json()["error"]["code"] == "not_computed"


def test_the_loan_forms_write_the_new_fields_through_the_memory_form(ctx):
    r = ctx.put("/memory/liabilities/new-car", {"fields": {
        "kind": "car_loan", "lender": "CarFin", "principal": 12000, "start_date": "2026-03-10", "term_months": 36, "payment_day": 10,
        "rate": {"type": "variable", "nominal": 4.5, "index": "Euribor 12M", "margin": 1.2, "cap": 6},
        "insurance": {"monthly": 15}, "deferral": {"months": 2, "kind": "partial"}, "first_payment_date": "2026-04-10"}}, dry_run=True)
    assert r.status_code == 200 and r.json()["changed"] and not store(ctx).exists("liabilities/new-car.yaml")      # a preview writes nothing
    r = ctx.put("/memory/liabilities/new-car", {"fields": {
        "kind": "car_loan", "lender": "CarFin", "principal": 12000, "start_date": "2026-03-10", "term_months": 36, "payment_day": 10,
        "rate": {"type": "variable", "nominal": 4.5, "index": "Euribor 12M", "margin": 1.2, "cap": 6},
        "insurance": {"monthly": 15}, "deferral": {"months": 2, "kind": "partial"}, "first_payment_date": "2026-04-10"}})
    assert r.status_code == 200
    d = next(x for x in ctx.get("/liabilities").json()["liabilities"] if x["id"] == "new-car")
    assert d["rate"]["index"] == "Euribor 12M" and d["rate"]["cap"] == 6 and d["deferral"] == {"months": 2, "kind": "partial"}
    assert d["insurance"]["monthly"] == "15.00" and d["schedule"]["status"] == "computed" and d["schedule"]["approximate"] is True      # variable rate
    bad = ctx.put("/memory/liabilities/new-car", {"fields": {"payment_day": 40}})
    assert bad.status_code == 422


def test_odometer_readings_preview_then_write(ctx):
    ctx.put("/memory/liabilities/lease", {"fields": {"kind": "loa", "monthly_payment": 300, "start_date": "2025-01-01", "end_date": "2027-01-01",
                                                      "mileage_limit_km": 25000, "excess_km_fee": 0.1, "initial_km": 0}})
    pv = ctx.post("/loans/lease/odometer", {"km": 15000, "date": "2026-01-01"}, dry_run=True).json()
    assert pv["dry_run"] and pv["changed"] and "15000" in pv["diff"]
    assert next(x for x in ctx.get("/liabilities").json()["liabilities"] if x["id"] == "lease")["odometer"] == []
    w = ctx.post("/loans/lease/odometer", {"km": 15000, "date": "2026-01-01"}).json()
    assert w["changed"] and w["change_id"]
    lease = next(x for x in ctx.get("/liabilities").json()["liabilities"] if x["id"] == "lease")
    assert lease["odometer"] == [{"date": "2026-01-01", "km": 15000}] and lease["lease"]["mileage"]["excess_km"] == 5000
    assert lease["lease"]["end"]["reminder_active"] is True and lease["schedule"]["status"] == "not_applicable"
    assert ctx.post("/loans/lease/odometer", {"km": 100, "date": "2026-06-01"}).status_code == 422                 # a decreasing reading
    assert ctx.post("/loans/home-loan/odometer", {"km": 100}).json()["error"]["code"] == "not_a_lease"
    assert ctx.post("/loans/lease/odometer", {"km": -1}).status_code == 422
    last = store(ctx).history("liabilities/lease.yaml", 1)[0]
    assert last.source == "ui"
    feed = ctx.get("/insights").json()
    assert {c["subtype"] for c in feed["cards"] if c["kind"] == "loan"} >= {"loa_end", "loa_mileage"}


def test_inferred_values_are_only_proposed_never_written(ctx):
    ctx.put("/memory/liabilities/inferme", {"fields": {"kind": "consumer_loan", "payment_match": "^HOMEBANK", "principal": 180000,
                                                       "start_date": "2020-01-01", "end_date": "2040-01-01"}})
    before = store(ctx).read_text("liabilities/inferme.yaml")
    d = next(x for x in ctx.get("/liabilities").json()["liabilities"] if x["id"] == "inferme")
    assert any(f["field"] == "rate.nominal" and f["value"] == 7.95 for f in d["inferred"])           # shown as a suggestion
    r = ctx.post("/loans/inferme/infer/propose", {"min_confidence": "medium"})
    assert r.status_code == 200 and r.json()["id"].startswith("p-") and "uv run coach memory accept" in r.json()["accept_command"]
    assert store(ctx).read_text("liabilities/inferme.yaml") == before
    props = ctx.get("/proposals").json()["proposals"]
    assert any(p["id"] == r.json()["id"] and p["status"] == "pending" for p in props)
    nothing = ctx.post("/loans/home-loan/infer/propose", {})
    assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "nothing_to_propose"


def test_net_worth_has_categories_owners_history_and_a_snapshot_endpoint(ctx):
    nw = ctx.get("/net-worth").json()
    assert nw["by_category"]["cash"] == "5700.00" and nw["by_category"]["savings"] == "5000.00" and nw["by_category"]["liabilities"] == "171638.52"
    assert nw["by_owner"]["joint"]["n_unknown"] >= 0 and nw["n_unknown"] == 2 and "history" not in nw
    item = next(i for i in nw["assets"]["items"] if i["id"] == "savings-book")
    assert item["category"] == "savings" and item["counted"] is True
    assert ctx.get("/net-worth/history").json()["history"] == []
    snap = ctx.post("/net-worth/snapshot").json()
    assert snap["net_worth"] == nw["net_worth"] and snap["n_unknown"] == 2 and snap["backfilled_months"] >= 1
    h = ctx.get("/net-worth/history", months=24).json()["history"]
    assert h[-1]["month"] == "2026-10" and h[-1]["source"] == "snapshot" and h[-1]["net_worth"] == nw["net_worth"]
    assert all(p["n_unknown"] >= 1 for p in h)
    assert ctx.get("/net-worth", history=True).json()["history"] == h


def test_the_new_endpoints_need_a_session_and_the_csrf_token(ctx):
    anon = TestClient(ctx.app, base_url=HOST)
    for method, path, body in (("GET", "/loans/home-loan", None), ("GET", "/loans/home-loan/payments", None), ("GET", "/net-worth/history", None),
                               ("POST", "/loans/home-loan/scenario", {"type": "prepay", "amount": 1}), ("POST", "/loans/home-loan/odometer", {"km": 1}),
                               ("POST", "/loans/home-loan/infer/propose", {}), ("POST", "/net-worth/snapshot", {})):
        r = anon.request(method, api(path), json=body)
        assert r.status_code == 401, (method, path)
        if method == "POST":
            r2 = ctx.client.post(api(path), json=body)                                                    # a session but no CSRF token
            assert r2.status_code == 403 and r2.json()["error"]["code"] == "csrf", path
            r3 = ctx.client.post(api(path), json=body, headers={"X-CSRF-Token": ctx.csrf, "Origin": "https://evil.example"})
            assert r3.status_code == 403, path
    assert ctx.sql("SELECT COUNT(*) FROM net_worth_history")[0][0] == 0 and ctx.sql("SELECT COUNT(*) FROM insights")[0][0] == 0


def test_new_nested_fields_can_be_added_to_an_existing_loan_file(ctx):
    r = ctx.put("/memory/liabilities/home-loan", {"fields": {"deferral": {"months": 3, "kind": "total"}, "payment_day": 1,
                                                              "insurance": {"rate_pct": 0.3, "basis": "initial", "delegated": True},
                                                              "rate": {"index": "Euribor 12M"}}})
    assert r.status_code == 200 and r.json()["changed"], r.text
    d = next(x for x in ctx.get("/liabilities").json()["liabilities"] if x["id"] == "home-loan")
    assert d["deferral"] == {"months": 3, "kind": "total"} and d["payment_day"] == 1 and d["rate"]["index"] == "Euribor 12M"
    assert d["insurance"]["rate_pct"] == 0.3 and d["insurance"]["basis"] == "initial" and d["insurance"]["delegated"] is True
    # a loan with no declared capital gets the table from the principal, with the deferral and the insurance: the first three instalments
    # only add the interest to the capital (with a declared capital that differs, the declared figure is rolled forward instead)
    new = ctx.put("/memory/liabilities/defer", {"fields": {"kind": "car_loan", "principal": 250000, "start_date": "2026-01-01", "term_months": 240,
                                                           "rate": {"nominal": 2.1}, "deferral": {"months": 3, "kind": "total"},
                                                           "insurance": {"rate_pct": 0.3, "basis": "initial"}}})
    assert new.status_code == 200, new.text
    rows = ctx.get("/loans/defer").json()["schedule"]["rows"]
    assert [x["kind"] for x in rows[:4]] == ["deferral", "deferral", "deferral", "regular"] and rows[0]["payment"] == "0.00"
    assert rows[0]["insurance"] == "62.50"                                                      # 250,000 x 0.3 % / 12
