"""The local web API (E5-1): every endpoint. Security (login link, cookie, CSRF, Host, headers, rate limits) is in
test_api_security.py. Synthetic data only; Enable Banking is always mocked."""
from __future__ import annotations

import datetime as dt
import stat

import pytest

from apihelpers import CALLS, HOST, TODAY, Ctx, api, ctx, make_client, world  # noqa: F401
from coach.memory import proposals as prop_mod
from helpers import FakeClient, add_tx, eb_tx


# ====================================================================== core / meta

def test_session_info(ctx):
    d = ctx.get("/session").json()
    assert d["csrf_token"] and d["today"] == "2026-10-04" and d["coach"]["backend"] == "claude-code"
    assert d["allow_remote"] is False and d["sync_daily_limit"] == 4 and d["enable_banking_configured"] is False


def test_taxonomy_and_filters(ctx):
    t = ctx.get("/meta/taxonomy").json()
    cats = {c["id"] for g in t["groups"] for c in g["categories"]}
    assert "food.groceries" in cats and "transfer.internal" in cats
    f = ctx.get("/meta/filters").json()
    assert {a["uid"] for a in f["accounts"]} == {"fo", "ce", "rl"}
    assert {"joint", "mia", "anna"} <= set(f["owners"]) and "kids" in f["purposes"]
    assert "one_off" in f["tags"] and any(e["id"] == "kitchen-2026" for e in f["events"]) and "food" in f["groups"]


def test_health_coverage_balances(ctx):
    h = ctx.get("/health").json()
    assert h["banks"] and "memory" in h and h["level"] in ("green", "amber", "red")
    c = ctx.get("/analytics/coverage").json()
    assert len(c["accounts"]) == 3 and c["accounts"][0]["n_months"] >= 0
    b = ctx.get("/accounts/balances").json()
    assert b["household_total"] == "5700.00" and {a["uid"] for a in b["accounts"]} == {"fo", "ce", "rl"}
    by = {a["uid"]: a for a in b["accounts"]}
    assert by["fo"]["balance"] == "1500.00" and by["rl"]["balance"] is None and b["n_without_balance"] == 1
    kids = ctx.get("/accounts/balances", owner="mia").json()
    assert [a["uid"] for a in kids["accounts"]] == ["rl"]
    assert b["note_msg"]["code"] == "balances.oneBalance" and b["note_msg"]["text"] == b["note"]   # i18n step 4: code + English text


def test_meta_disclaimers_in_the_asked_language(ctx):
    fr = ctx.get("/meta/disclaimers", lang="fr").json()
    assert fr["lang"] == "fr" and fr["texts"]["ai_label_short"] == "Généré par IA" and "IA" in fr["texts"]["ai_label"]
    en = ctx.get("/meta/disclaimers", lang="xx").json()                     # an unknown language: English
    assert en["lang"] == "en" and en["texts"]["ai_label_short"] == "AI-generated" and set(en["texts"]) == {"ai_label", "ai_label_short"}


# ====================================================================== analytics

def test_cashflow_averages_forecast(ctx):
    cf = ctx.get("/analytics/cashflow", months=6).json()
    assert cf["household"]["months"] and cf["by_owner"] == {} and "coverage" in cf
    assert all(isinstance(m["income"], str) for m in cf["household"]["months"])             # money never a float
    assert ctx.get("/analytics/cashflow", months=3, by="owner").json()["by_owner"]
    av = ctx.get("/analytics/averages").json()
    assert av["window_months"] == 12 and any(c["category"] == "food.groceries" for c in av["categories"])
    mc = ctx.get("/analytics/month-categories", month="2026-09").json()
    assert mc["month"] == "2026-09" and mc["partial"] is False and mc["categories"]
    assert ctx.get("/analytics/month-categories").json()["partial"] is True
    fc = ctx.get("/analytics/forecast", days=60).json()
    assert fc["horizon_days"] == 60 and len(fc["household"]["points"]) >= 60 and fc["household"]["start_balance"] == "5700.00"
    assert fc["household"]["account"] is None and "accounts_without_balance:1" in fc["household"]["flags"]   # a code flag, the count after ':'
    assert ctx.get("/analytics/forecast", days=3).status_code == 422


def test_scope_filters_narrow_the_analytics(ctx):
    all_ = ctx.get("/analytics/averages").json()["household_monthly_avg"]
    kids = ctx.get("/analytics/averages", owner="mia").json()
    assert kids["scope"]["owners"] == ["mia"] and kids["household_monthly_avg"] != all_ or kids["household_monthly_avg"] is None
    acc = ctx.get("/analytics/cashflow", account="Main").json()
    assert acc["scope"]["accounts"]


def test_categories_overview_and_drilldown(ctx):
    ov = ctx.get("/categories").json()
    row = next(c for c in ov["categories"] if c["category"] == "food.groceries")
    assert row["monthly_avg"] and row["group"] == "food"
    d = ctx.get("/categories/food.groceries").json()
    assert d["kind"] == "spending" and not d["is_group"] and d["series"] and d["entities"] and d["average"]["monthly"]
    assert all({"covered", "partial", "run_rate", "one_off"} <= set(s) for s in d["series"])
    assert d["series"][-1]["partial"] is True and d["coverage"]["rule"]
    g = ctx.get("/categories/food").json()
    assert g["is_group"] and {l["id"] for l in g["leaves"]} >= {"food.groceries", "food.restaurants"}
    reno = ctx.get("/categories/housing.renovation").json()
    assert reno["one_offs"] and reno["one_offs"][0]["tags"]
    inc = ctx.get("/categories/income.salary").json()
    assert inc["kind"] == "income" and inc["average"] is None


def test_calendar_and_ics(ctx):
    c = ctx.get("/calendar", days=45).json()
    assert c["items"] and c["days"] == 45
    m = ctx.get("/calendar", month="2026-11").json()
    assert m["month"] == "2026-11" and all(i["date"].startswith("2026-11") for i in m["items"])
    assert ctx.get("/calendar", month="2020-01").json()["items"] == []
    r = ctx.get("/calendar.ics", days=60)
    assert r.status_code == 200 and r.text.startswith("BEGIN:VCALENDAR") and "text/calendar" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]


def test_subscriptions_and_price_changes(ctx):
    s = ctx.get("/subscriptions", contracts_only=False).json()
    names = {x["entity"]: x for x in s["series"]}
    assert "Streambox" in names and names["Streambox"]["monthly_cost"] == "12.99" and names["Streambox"]["yearly_cost"] == "155.88"
    assert s["totals"]["n_active"] >= 3 and "active_monthly" in s["totals"]
    up = names["Priceup"]
    assert up["price_changes"] and up["price_changes"][0]["direction"] == "increase"
    assert ctx.get("/subscriptions", status="ended").status_code == 200
    assert all(x["cadence"] == "monthly" for x in ctx.get("/subscriptions", cadence="monthly", contracts_only=False).json()["series"])
    pc = ctx.get("/price-changes").json()
    cid = next(c["id"] for c in pc["changes"] if c["entity"] == "Priceup")
    assert ctx.post(f"/price-changes/{cid}/dismiss", {"note": "known"}).json() == {"id": cid, "dismissed": True}
    assert all(c["id"] != cid for c in ctx.get("/price-changes").json()["changes"])
    assert any(c["id"] == cid and c["dismissed"] for c in ctx.get("/price-changes", include_dismissed=True).json()["changes"])
    assert ctx.sql("SELECT note FROM price_change_dismissals WHERE id=?", cid) == [("known",)]
    assert ctx.post("/price-changes/chg_nope/dismiss").status_code == 404


def test_anomalies_dismiss_and_undismiss(ctx):
    a = ctx.get("/anomalies").json()
    assert a["anomalies"], "the large new purchase must be reported"
    aid = a["anomalies"][0]["id"]
    assert ctx.post(f"/anomalies/{aid}/dismiss", {"note": "ok"}).status_code == 200
    assert all(x["id"] != aid for x in ctx.get("/anomalies").json()["anomalies"])
    assert any(x["id"] == aid and x["dismissed"] for x in ctx.get("/anomalies", include_dismissed=True).json()["anomalies"])
    assert ctx.post(f"/anomalies/{aid}/undismiss").status_code == 200
    assert any(x["id"] == aid for x in ctx.get("/anomalies").json()["anomalies"])
    assert ctx.post("/anomalies/anm_nope/dismiss").status_code == 404
    assert ctx.post("/anomalies/anm_nope/undismiss").status_code == 404


def test_insights_feed_dismiss_snooze_restore(ctx):
    d = ctx.get("/insights").json()
    kinds = {c["kind"] for c in d["cards"]}
    assert {"anomaly", "price_change"} <= kinds and d["coach"]["items"] == [] and d["coach"]["hidden"] == 0
    anomaly = next(c for c in d["cards"] if c["kind"] == "anomaly")
    pc = next(c for c in d["cards"] if c["kind"] == "price_change")
    # dismiss: an anomaly and a price change go through their own persisted functions
    assert ctx.post(f"/insights/{anomaly['id']}/dismiss").status_code == 200
    assert ctx.sql("SELECT dismissed_at FROM anomalies WHERE id=?", anomaly["id"])[0][0]
    assert ctx.post(f"/insights/{pc['id']}/dismiss").status_code == 200
    assert ctx.sql("SELECT 1 FROM price_change_dismissals WHERE id=?", pc["id"])
    ids = {c["id"] for c in ctx.get("/insights").json()["cards"]}
    assert anomaly["id"] not in ids and pc["id"] not in ids
    # snooze: persisted in the UI-state file until the date, shown again with include_snoozed
    other = next(c for c in ctx.get("/insights").json()["cards"] if c["kind"] == "anomaly")
    r = ctx.post(f"/insights/{other['id']}/snooze", {"days": 7})
    assert r.json()["snoozed_until"] == "2026-10-11"
    assert other["id"] not in {c["id"] for c in ctx.get("/insights").json()["cards"]}
    back = ctx.get("/insights", include_snoozed=True).json()["cards"]
    assert next(c for c in back if c["id"] == other["id"])["snoozed_until"] == "2026-10-11"
    state_file = ctx.cfg.data_dir / "ui-state.json"
    assert state_file.exists() and stat.S_IMODE(state_file.stat().st_mode) == 0o600
    ctx.state.clock = lambda: dt.date(2026, 10, 12)                       # a week later the snooze is over
    ctx.state.touch()
    assert other["id"] in {c["id"] for c in ctx.get("/insights").json()["cards"]}
    ctx.state.clock = lambda: TODAY
    ctx.state.touch()
    assert ctx.post(f"/insights/{other['id']}/restore").status_code == 200
    assert ctx.post(f"/insights/{anomaly['id']}/restore").status_code == 200
    assert anomaly["id"] in {c["id"] for c in ctx.get("/insights").json()["cards"]}
    assert ctx.post("/insights/ins_nope/dismiss").status_code == 404
    assert ctx.post("/insights/ins_nope/snooze", {"days": 3}).status_code == 404
    assert ctx.post(f"/insights/{other['id']}/snooze", {"days": 0}).status_code == 422


# ====================================================================== budgets / goals

def test_budget_preview_write_edit_delete(ctx):
    assert ctx.get("/budgets").json()["budgets"] == []
    body = {"target": "food.groceries", "monthly": "600", "rollover": True}
    pv = ctx.post("/budgets", body, dry_run=True).json()
    assert pv["dry_run"] and pv["changed"] and "+budgets:" in pv["diff"] and not (ctx.cfg.memory_dir / "budgets.yaml").exists()
    w = ctx.post("/budgets", body).json()
    assert w["changed"] and w["change_id"] and w["id"] == "food-groceries"
    text = (ctx.cfg.memory_dir / "budgets.yaml").read_text()
    assert "monthly: 600" in text and "rollover: true" in text and "start: 2026-10-01" in text
    assert ctx.state.store.history(limit=1)[0].source == "ui"                       # recorded as the UI's change
    b = ctx.get("/budgets").json()
    row = b["budgets"][0]
    assert row["id"] == "food-groceries" and row["monthly"] == "600.00" and row["category"] == "food.groceries" and row["status"] in ("ok", "at_risk", "over")
    # update the amount of the same budget; an unchanged one is a no-op
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "650"}).json()["changed"]
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "650"}).json()["changed"] is False
    g = ctx.post("/budgets", {"target": "food", "monthly": "900", "owner": "joint"}).json()
    assert g["id"] == "group-food"
    sg = ctx.get("/budgets/suggestions", limit=5).json()
    assert sg["suggestions"] and sg["suggestions"][0]["suggested"]
    assert any(x["existing"] for x in ctx.get("/budgets/suggestions", limit=50).json()["suggestions"] if x["category"] == "food.groceries")
    pv = ctx.post("/budgets/group-food/delete", dry_run=True).json()
    assert "-" in pv["diff"] and "group-food" in (ctx.cfg.memory_dir / "budgets.yaml").read_text()
    assert ctx.post("/budgets/group-food/delete").json()["changed"]
    assert "group-food" not in (ctx.cfg.memory_dir / "budgets.yaml").read_text()
    assert ctx.post("/budgets/nope/delete").status_code == 404


def test_budget_validation(ctx):
    assert ctx.post("/budgets", {"target": "food.nonsense", "monthly": "5"}).json()["error"]["code"] == "unknown_category"
    assert ctx.post("/budgets", {"target": "nogroup", "monthly": "5"}).json()["error"]["code"] == "unknown_category"
    assert ctx.post("/budgets", {"target": "group:nogroup", "monthly": "5"}).json()["error"]["code"] == "unknown_group"
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "-5"}).json()["error"]["code"] == "bad_amount"
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "abc"}).json()["error"]["code"] == "bad_amount"
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "5", "owner": "ghost"}).json()["error"]["code"] == "unknown_owner"
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "5", "account": "nope"}).json()["error"]["code"] == "unknown_account"


def test_goals(ctx):
    assert ctx.get("/goals").json()["goals"] == []
    body = {"id": "holiday", "title": "Holiday", "target": "2000", "tag": "savings", "date": "2027-06-01", "monthly": "150"}
    pv = ctx.post("/goals", body, dry_run=True).json()
    assert pv["changed"] and "holiday" in pv["diff"]
    assert ctx.post("/goals", body).json()["changed"]
    g = ctx.get("/goals").json()["goals"][0]
    assert g["id"] == "holiday" and g["target"] == "2000.00" and g["source"] == "tag"
    assert ctx.post("/goals", {"id": "new-one", "tag": "savings"}).json()["error"]["code"] == "missing_target"


# ====================================================================== wealth

def test_liabilities_assets_contracts_net_worth(ctx):
    L = ctx.get("/liabilities").json()
    loan = L["liabilities"][0]
    assert loan["id"] == "home-loan" and loan["monthly_payment"] == "1500.00" and loan["outstanding"] == "180000.00"
    assert "debited account" in loan["missing"] and loan["payments"] and loan["payments"]["amount"] == "1500.00"
    # E9-3 / MJ-2: the declared capital (180,000 on 2026-01-15, a statement) differs from the theoretical table (250,000 at 2.1 % over 240 months:
    # about 186,600 then) beyond the tolerance, so it WINS and is rolled forward: 168 instalments of 1,237.57 from 2026-02-01, 9 due by 2026-10-04,
    # 171,638.52 left (closed form 171,638.55; the cents come from the instalment rounded to 1,237.57)
    assert loan["remaining_capital"] == "171638.52" and loan["remaining_capital_source"] == "declared_rolled"
    assert loan["schedule"]["outstanding_check"]["status"] == "differs"
    assert L["totals"]["outstanding_known"] == "171638.52"
    A = ctx.get("/assets").json()["assets"]
    by = {a["id"]: a for a in A}
    assert by["savings-book"]["value"] == "5000.00" and by["savings-book"]["stale"] is True and by["savings-book"]["unknown_value"] is False
    assert by["family-house"]["value"] is None and by["family-house"]["unknown_value"] is True
    assert ctx.get("/contracts").json() == {"contracts": []}
    nw = ctx.get("/net-worth").json()
    # 5700 bank + 5000 savings book - 171638.52 loan (the declared capital rolled forward); the house has no value and "rl" has no balance
    assert nw["net_worth"] == "-160938.52" and nw["complete"] is False
    assert {(u["kind"], u["id"]) for u in nw["unknown"]} == {("asset", "family-house"), ("account", "rl")}
    assert nw["bank"]["total"] == "5700.00" and nw["assets"]["total"] == "5000.00" and nw["liabilities"]["total"] == "171638.52"
    assert nw["stale"] and "NOT included" in nw["note"]


def test_net_worth_complete_when_everything_is_known(ctx):
    r = ctx.put("/memory/assets/family-house", {"fields": {"value": 300000, "as_of": "2026-09-30"}})
    assert r.status_code == 200 and r.json()["changed"]
    with ctx.state.write() as con:
        con.execute("INSERT INTO balances VALUES ('rl','2026-10-04T08:00:00+00:00','CLBD',100.0,'EUR','2026-10-04')")
        con.commit()
    nw = ctx.get("/net-worth").json()
    assert nw["complete"] is True and nw["unknown"] == [] and nw["net_worth"] == "139161.48"


# ====================================================================== transactions

def test_transaction_list_filters_totals_pagination(ctx):
    d = ctx.get("/transactions", limit=5).json()
    assert d["limit"] == 5 and len(d["items"]) == 5 and d["next_offset"] == 5 and d["total"] > 100
    assert d["items"][0]["date"] >= d["items"][-1]["date"]
    t = d["totals"]
    assert t["count"] == d["total"] and t["sum"] == f"{float(t['income']) + float(t['outflow']):.2f}"
    p2 = ctx.get("/transactions", limit=5, offset=5).json()
    assert p2["items"][0]["tx_key"] != d["items"][0]["tx_key"]
    last = ctx.get("/transactions", limit=500, offset=0).json()
    assert len(last["items"]) <= 500
    g = ctx.get("/transactions", category="food.groceries", date_from="2026-01-01", date_to="2026-03-31").json()
    assert g["items"] and all(i["category"] == "food.groceries" and "2026-01-01" <= i["date"] <= "2026-03-31" for i in g["items"])
    assert g["totals"]["income"] == "0.00"
    assert all(i["category"].startswith("food.") for i in ctx.get("/transactions", group="food", limit=50).json()["items"])
    assert {i["account"] for i in ctx.get("/transactions", account="ce", limit=50).json()["items"]} == {"ce"}
    assert all(i["account"] == "rl" for i in ctx.get("/transactions", owner="mia", limit=50).json()["items"])
    assert all(i["purpose"] == "kids" for i in ctx.get("/transactions", purpose="kids", limit=50).json()["items"])
    big = ctx.get("/transactions", amount_min="1000", limit=50).json()["items"]
    assert big and all(abs(float(i["amount"])) >= 1000 for i in big)
    small = ctx.get("/transactions", amount_max="20", direction="out", limit=50).json()["items"]
    assert small and all(-20 <= float(i["amount"]) < 0 for i in small)
    assert all(float(i["amount"]) > 0 for i in ctx.get("/transactions", direction="in", limit=50).json()["items"])
    tagged = ctx.get("/transactions", tag="one_off", limit=50).json()["items"]
    assert tagged and all("one_off" in i["tags"] for i in tagged)
    assert all(i["event"] == "kitchen-2026" for i in ctx.get("/transactions", event="kitchen-2026").json()["items"])
    assert {i["source"] for i in ctx.get("/transactions", source="user", limit=50).json()["items"]} == {"user"}
    assert all("STREAMBOX" in (i["entity"] + i["description"]).upper() for i in ctx.get("/transactions", q="streambox").json()["items"])
    assert ctx.get("/transactions", q="streambox 12.99 nope").json()["total"] == 0
    assert all("ACME" in i["entity"].upper() for i in ctx.get("/transactions", merchant="acme gro", limit=50).json()["items"])
    assert all(i["entity"] == "Streambox" for i in ctx.get("/transactions", entity="Streambox").json()["items"])
    asc = ctx.get("/transactions", sort="amount_desc", limit=3).json()["items"]
    assert abs(float(asc[0]["amount"])) >= abs(float(asc[1]["amount"]))
    assert not any(i["category"].startswith("transfer.") for i in ctx.get("/transactions", exclude_transfers=True, limit=500).json()["items"])
    assert ctx.get("/transactions", amount_min="abc").json()["error"]["code"] == "bad_amount"
    assert ctx.get("/transactions", sort="whatever").status_code == 422


def test_transaction_export_csv_matches_filter_and_neutralises_formulas(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "evil1", "2026-10-02", -5.0, "=HYPERLINK(\"http://x\")", "card")
    r = ctx.get("/transactions/export.csv", q="hyperlink")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"] and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("date,account") and len(lines) == 2
    assert "'=HYPERLINK" in lines[1] and ',=HYPERLINK' not in lines[1]
    full = ctx.get("/transactions/export.csv", category="food.groceries").text.strip().splitlines()
    assert len(full) - 1 == ctx.get("/transactions", category="food.groceries", limit=1).json()["total"]


def test_transaction_detail_explains_the_category(ctx):
    d = ctx.get("/transactions/detail", tx_key="reno1").json()
    assert d["transaction"]["amount"] == "-6500.00" and d["final"]["category"] == "housing.renovation" and d["final"]["source"] == "memory"
    assert d["memory"]["winner"] == "kitchen-works" and "one_off" in d["final"]["tags"]
    assert d["steps"] and all("edit" not in s for s in d["steps"]) and d["item"]["tx_key"] == "reno1" and d["consistent"]
    link = ctx.get("/transactions/detail", tx_key="tr_out").json()
    assert link["transfer_link"]["method"] == "manual" and link["final"]["category"] == "transfer.internal"
    assert ctx.get("/transactions/detail", tx_key="fm0").json()["same_merchant_count"] == 5


def test_category_fix_this_transaction_only(ctx):
    body = {"tx_key": "fm0", "category": "food.restaurants", "scope": "transaction", "note": "lunch"}
    pv = ctx.post("/transactions/category", body, dry_run=True).json()
    assert pv["dry_run"] and pv["affected"]["count"] == 1 and ctx.sql("SELECT COUNT(*) FROM tx_overrides") == [(0,)]
    assert ctx.post("/transactions/category", body).status_code == 200
    assert ctx.sql("SELECT category, note FROM tx_overrides WHERE tx_key='fm0'") == [("food.restaurants", "lunch")]
    row = next(i for i in ctx.get("/transactions", q="fresh market", limit=50).json()["items"] if i["tx_key"] == "fm0")
    assert row["category"] == "food.restaurants" and row["source"] == "override" and row["overridden"] is True
    other = next(i for i in ctx.get("/transactions", q="fresh market", limit=50).json()["items"] if i["tx_key"] == "fm1")
    assert other["category"] == "food.groceries"
    assert ctx.post("/transactions/override/clear", {"tx_key": "fm0"}).json()["cleared"] is True
    assert ctx.post("/transactions/override/clear", {"tx_key": "fm0"}).json()["cleared"] is False
    assert ctx.sql("SELECT COUNT(*) FROM tx_overrides") == [(0,)]


def test_category_fix_this_merchant(ctx):
    body = {"tx_key": "fm0", "category": "food.restaurants", "scope": "merchant"}
    pv = ctx.post("/transactions/category", body, dry_run=True).json()
    assert pv["affected"]["count"] == 5 and pv["affected"]["merchant_key"] == "FRESH MARKET"
    assert pv["affected"]["from_categories"] == [{"category": "food.groceries", "n": 5}]
    assert ctx.sql("SELECT source FROM merchants WHERE merchant_key='FRESH MARKET'") == [("llm",)]
    ctx.post("/transactions/category", body)
    assert ctx.sql("SELECT category, source, confidence FROM merchants WHERE merchant_key='FRESH MARKET'") == [("food.restaurants", "user", 1.0)]
    items = ctx.get("/transactions", entity="Fresh Market", limit=50).json()["items"]
    assert items and all(i["category"] == "food.restaurants" and i["source"] == "user" for i in items)
    again = ctx.post("/transactions/category", body, dry_run=True).json()
    assert again["affected"]["count"] == 0 and again["affected"]["already"] == 5 and not again["changed"]


def test_merchant_fix_reports_what_it_cannot_change(ctx):
    ctx.post("/transactions/category", {"tx_key": "fm0", "category": "food.fast_food", "scope": "transaction"})
    pv = ctx.post("/transactions/category", {"tx_key": "fm1", "category": "food.restaurants", "scope": "merchant"}, dry_run=True).json()
    assert pv["affected"]["count"] == 4
    assert pv["affected"]["blocked"] == [{"reason": "a per-transaction override", "n": 1}]
    assert any("per-transaction override" in w for w in pv["warnings"])


def test_category_fix_memory_annotation_preview_and_write(ctx):
    body = {"tx_key": "fm0", "category": "food.restaurants", "scope": "memory", "match": "merchant", "note": "it is a deli"}
    pv = ctx.post("/transactions/category", body, dry_run=True).json()
    assert pv["dry_run"] and pv["affected"]["count"] == 5 and "FRESH MARKET" in pv["diff"] and pv["id"] == "fresh-market-restaurants"
    assert "fresh-market" not in (ctx.cfg.memory_dir / "categorization.yaml").read_text()
    w = ctx.post("/transactions/category", body).json()
    assert w["changed"] and w["change_id"]
    txt = (ctx.cfg.memory_dir / "categorization.yaml").read_text()
    assert "id: fresh-market-restaurants" in txt and "merchant_key: ^FRESH MARKET$" in txt and "# --- streaming" in txt   # comments kept
    h = ctx.state.store.history(limit=1)[0]
    assert h.source == "ui" and "categorization.yaml" in h.files
    row = next(i for i in ctx.get("/transactions", q="fresh market", limit=50).json()["items"] if i["tx_key"] == "fm2")
    assert row["category"] == "food.restaurants" and row["source"] == "memory"
    only = ctx.post("/transactions/category", {"tx_key": "fm0", "category": "food.fast_food", "scope": "memory", "match": "transaction"}, dry_run=True).json()
    # the merchant annotation (written above) comes first in the file and wins: the preview says the new one would never apply
    assert only["affected"]["matched"] == 1 and only["affected"]["count"] == 0 and only["affected"]["applies_to"] == 0
    assert any("would never apply" in w for w in only["warnings"])


def test_category_fix_validation(ctx):
    assert ctx.post("/transactions/category", {"tx_key": "fm0", "category": "nope.nope", "scope": "transaction"}).json()["error"]["code"] == "unknown_category"
    assert ctx.post("/transactions/category", {"tx_key": "ghost", "category": "food.groceries", "scope": "transaction"}).status_code == 404
    assert ctx.post("/transactions/category", {"tx_key": "fm0", "category": "food.groceries", "scope": "bogus"}).status_code == 422


def test_annotations_tags_notes_events(ctx):
    pv = ctx.post("/annotations", {"tx_keys": ["fm0"], "tags": ["one_off", "reimbursable"], "event": "kitchen-2026", "note": "gift"}, dry_run=True).json()
    assert pv["affected"]["matched"] == 1 and pv["affected"]["tag_changes"] == 1 and pv["affected"]["count"] == 0 and "reimbursable" in pv["diff"] and pv["id"]
    w = ctx.post("/annotations", {"tx_keys": ["fm0"], "tags": ["one_off", "reimbursable"], "event": "kitchen-2026", "note": "gift", "id": "gift-fm0"})
    assert w.json()["changed"]
    items = ctx.get("/transactions", tag="reimbursable").json()["items"]
    assert [i["tx_key"] for i in items] == ["fm0"] and items[0]["event"] == "kitchen-2026"
    ann = {a["id"]: a for a in ctx.get("/annotations").json()["annotations"]}
    assert ann["gift-fm0"]["matched"] == 1 and ann["gift-fm0"]["applies_to"] == 1 and "kitchen-works" in ann
    assert ctx.post("/annotations", {"tx_keys": ["fm1"], "tags": ["x"], "id": "gift-fm0"}).json()["error"]["code"] == "annotation_exists"
    assert ctx.post("/annotations", {"tx_keys": ["fm1"], "event": "no-such-event"}).json()["error"]["code"] == "memory_invalid"
    assert ctx.post("/annotations", {"tx_keys": ["fm1"]}).json()["error"]["code"] == "no_effect"
    assert ctx.post("/annotations", {"tags": ["x"]}).json()["error"]["code"] == "no_match"
    assert ctx.post("/annotations", {"tx_keys": ["fm1"], "tags": ["Bad Tag"]}).json()["error"]["code"] == "invalid_annotation"
    d = ctx.post("/annotations/gift-fm0/delete", dry_run=True).json()
    assert "gift-fm0" in d["diff"] and ctx.post("/annotations/gift-fm0/delete").json()["changed"]
    assert ctx.get("/transactions", tag="reimbursable").json()["items"] == []
    assert ctx.post("/annotations/gift-fm0/delete").status_code == 404


def test_split_and_clear(ctx):
    ok = ctx.post("/transactions/split", {"tx_key": "fm0", "parts": [{"category": "food.groceries", "amount": "15"}, {"category": "household.nope", "amount": "rest"}]})
    assert ok.status_code == 400
    ok = ctx.post("/transactions/split", {"tx_key": "fm0", "parts": [{"category": "food.groceries", "amount": "15"}, {"category": "shopping.clothing", "amount": "rest", "note": "shirt"}]})
    assert ok.status_code == 200
    assert [(p["amount"], p["category"]) for p in ok.json()["parts"]] == [("-15.00", "food.groceries"), ("-25.00", "shopping.clothing")]
    row = next(i for i in ctx.get("/transactions", q="fresh market", limit=50).json()["items"] if i["tx_key"] == "fm0")
    assert row["split"] is True and row["amount"] == "-40.00"
    assert ctx.get("/transactions/detail", tx_key="fm0").json()["split"][1]["note"] == "shirt"
    assert ctx.post("/transactions/split", {"tx_key": "fm0", "parts": [{"category": "food.groceries", "amount": "50"}, {"category": "shopping.clothing", "amount": "5"}]}).status_code == 400
    assert ctx.post("/transactions/split/clear", {"tx_key": "fm0"}).json()["removed"] == 2


def test_transfers_link_unlink(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "x_out", "2026-09-10", -250.0, "VIR TO SAVINGS 2", "transfer_out")
        add_tx(con, "ce", "x_in", "2026-09-10", 250.0, "VIR FROM CARDS 2", "transfer_in")
    t = ctx.get("/transfers").json()
    assert len(t["links"]) == 1 and t["links"][0]["amount"] == "300.00"
    assert any(p["debit"]["tx_key"] == "x_out" for p in t["proposals"])
    assert ctx.post("/transfers/link", {"out_tx": "x_out", "in_tx": "x_in"}).status_code == 200
    assert ctx.post("/transfers/link", {"out_tx": "x_in", "in_tx": "x_out"}).status_code == 400
    row = next(i for i in ctx.get("/transactions", q="savings 2").json()["items"] if i["tx_key"] == "x_out")
    assert row["transfer_linked"] is True and row["category"] == "transfer.internal"
    assert ctx.post("/transfers/unlink", {"ref": "x_out"}).json()["out"] == "x_out"
    assert ctx.post("/transfers/unlink", {"ref": "x_out"}).status_code == 400


# ====================================================================== review queue

def test_review_queue_confirm_and_correct(ctx):
    d = ctx.get("/review").json()
    keys = {i["key"]: i for i in d["items"]}
    assert "FRESH MARKET" in keys and d["total"] >= 1
    fm = keys["FRESH MARKET"]
    assert fm["category"] == "food.groceries" and fm["reason"] == "low_confidence" and fm["n"] == 5 and fm["at_stake"] == "200.00"
    assert ctx.client.post(api("/review/confirm"), json={"key": "FRESH MARKET"}).status_code == 403
    ok = ctx.post("/review/confirm", {"key": "FRESH MARKET"})
    assert ok.json() == {"key": "FRESH MARKET", "category": "food.groceries"}
    assert ctx.sql("SELECT source, confidence FROM merchants WHERE merchant_key='FRESH MARKET'") == [("user", 1.0)]
    assert "FRESH MARKET" not in {i["key"] for i in ctx.get("/review").json()["items"]}
    again = ctx.post("/review/confirm", {"key": "FRESH MARKET"})
    assert again.status_code == 404 and again.json()["error"]["code"] == "not_in_queue"
    assert ctx.get("/review", limit=1).json()["items"].__len__() <= 1


def test_review_correct_and_knn_labels_cannot_be_confirmed(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "kn1", "2026-09-20", -60.0, "WEIRD SHOP", "card")
        con.execute("INSERT OR REPLACE INTO merchants VALUES ('WEIRD SHOP','Weird shop','other.uncategorized',0.5,0,'knn','knn:OTHER','t')")
    queue = {i["key"] for i in ctx.get("/review").json()["items"]}
    assert "WEIRD SHOP" in queue
    r = ctx.post("/review/confirm", {"key": "WEIRD SHOP"})
    assert r.status_code == 400 and "similarity" in r.json()["error"]["message"]
    assert ctx.post("/review/correct", {"key": "WEIRD SHOP", "category": "nope.nope"}).status_code == 400
    assert ctx.post("/review/correct", {"key": "NO SUCH KEY", "category": "shopping.clothing"}).status_code == 400
    ok = ctx.post("/review/correct", {"key": "WEIRD SHOP", "category": "shopping.electronics"})
    assert ok.json()["merchants"] == ["WEIRD SHOP"]
    assert ctx.sql("SELECT category, source FROM merchants WHERE merchant_key='WEIRD SHOP'") == [("shopping.electronics", "user")]
    assert "WEIRD SHOP" not in {i["key"] for i in ctx.get("/review").json()["items"]}


# ====================================================================== memory

def test_memory_overview_check_and_history(ctx):
    o = ctx.get("/memory/overview").json()
    assert "categorization.yaml" in o["files"] and o["counts"]["members"] == 3 and o["counts"]["liabilities"] == 1
    c = ctx.get("/memory/check").json()
    assert set(c["summary"]) >= {"errors", "warnings", "info"} and isinstance(c["issues"], list)
    assert ctx.get("/memory/history").json()["enabled"] is True
    ctx.post("/budgets", {"target": "food.groceries", "monthly": "300"})
    ch = ctx.get("/memory/history", limit=5).json()["changes"][0]
    assert ch["source"] == "ui" and ch["files"] == ["budgets.yaml"]
    assert "+budgets:" in ctx.get(f"/memory/history/{ch['id']}/diff").json()["diff"]
    assert ctx.get("/memory/history/zzzzzzz/diff").status_code == 404


def test_history_off(ctx):
    ctx.cfg.memory_history = False
    assert ctx.get("/memory/history").json() == {"enabled": False, "changes": []}


def test_questions_inbox(ctx):
    from coach.memory import questions as q_mod
    q_mod.add(ctx.state.store, "Who is the payee of FOO?", topic="Merchants", stake=500, evidence={"total": 500, "n": 3}, target="categorization.yaml")
    q_mod.add(ctx.state.store, "Second one?", qid="q-second")
    d = ctx.get("/questions").json()
    assert d["counts"]["open"] == 2 and d["questions"][0]["stake"] == "500.00" and d["questions"][0]["suggested_target"]["file"] == "categorization.yaml"
    a = ctx.post("/questions/q-001/answer", {"text": "  my brother  "}).json()
    assert a["status"] == "answered" and a["answer"] == "my brother"
    assert ctx.post("/questions/q-001/answer", {"text": "again"}).status_code == 409
    assert ctx.post("/questions/q-001/answer", {"text": ""}).status_code == 422
    assert ctx.post("/questions/q-second/dismiss", {"reason": "not useful"}).json()["status"] == "dismissed"
    assert ctx.get("/questions", status="open").json()["questions"] == []
    assert {q["id"] for q in ctx.get("/questions", status="all").json()["questions"]} == {"q-001", "q-second"}
    assert ctx.post("/questions/q-001/reopen").json()["status"] == "open"
    assert ctx.post("/questions/ghost/answer", {"text": "x"}).status_code == 409
    assert ctx.get("/questions", status="bogus").status_code == 422
    assert ctx.state.store.history(limit=1)[0].source == "ui"


def test_household_events_and_item_forms(ctx):
    assert [m["id"] for m in ctx.get("/household").json()["members"]] == ["anna", "luca", "mia"]
    ev = ctx.get("/events").json()["events"]
    assert any(e["id"] == "kitchen-2026" for e in ev)
    # a member: preview, then write
    body = {"fields": {"name": "Zoe Rossi", "role": "child", "birth_year": 2015, "aliases": ["ZOE R"]}}
    pv = ctx.put("/memory/members/zoe", body, dry_run=True).json()
    assert pv["changed"] and "zoe" in pv["diff"] and "Zoe" not in (ctx.cfg.memory_dir / "household.yaml").read_text()
    assert ctx.put("/memory/members/zoe", body).json()["changed"]
    assert next(m for m in ctx.get("/household").json()["members"] if m["id"] == "zoe")["birth_year"] == 2015
    assert ctx.put("/memory/members/zoe", {"fields": {"role": "robot"}}).json()["error"]["code"] == "memory_invalid"
    # an event (events.yaml is created) and an annotation can then point at it
    assert ctx.put("/memory/events/summer-trip", {"fields": {"title": "Summer trip", "start": "2026-07-01", "end": "2026-07-15", "budget": 2500, "status": "done"}}).json()["changed"]
    assert any(e["id"] == "summer-trip" and e["budget"] == "2500.00" and e["start"] == "2026-07-01" for e in ctx.get("/events").json()["events"])
    assert "start: 2026-07-01" in (ctx.cfg.memory_dir / "events.yaml").read_text()          # a real YAML date, not a string
    assert ctx.post("/annotations", {"tx_keys": ["fm0"], "event": "summer-trip", "tags": ["one_off"]}).json()["changed"]
    # an existing liability: fill the missing fields; comments of other lines survive
    r = ctx.put("/memory/liabilities/home-loan", {"fields": {"debited_account": "ce", "rate": {"nominal": 2.3}, "outstanding": 175000, "outstanding_as_of": "2026-10-01"}})
    assert r.status_code == 200 and r.json()["changed"]
    txt = (ctx.cfg.memory_dir / "liabilities" / "home-loan.yaml").read_text()
    assert "debited_account: ce" in txt and "nominal: 2.3" in txt and "outstanding_as_of: 2026-10-01" in txt and "# synthetic loan" in txt
    loan = ctx.get("/liabilities").json()["liabilities"][0]
    assert loan["outstanding"] == "175000.00" and "debited account" not in loan["missing"] and loan["debited_account_label"] == "CPT COURANT TEST"
    # a new liability and contract are created from the template
    assert ctx.put("/memory/liabilities/car-loan", {"fields": {"monthly_payment": 250}}).json()["error"]["code"] == "missing_kind"
    assert ctx.put("/memory/liabilities/car-loan", {"fields": {"kind": "car_loan", "lender": "CarBank", "monthly_payment": 250}}).json()["changed"]
    assert (ctx.cfg.memory_dir / "liabilities" / "car-loan.yaml").exists()
    assert ctx.put("/memory/contracts/power", {"fields": {"provider": "SunPower", "kind": "energy", "billing": {"amount": 85, "period": "monthly"}, "renewal": "2027-01-01"}}).json()["changed"]
    c = ctx.get("/contracts").json()["contracts"][0]
    assert c["provider"] == "SunPower" and c["billing"] == {"amount": "85.00", "period": "monthly"} and c["renewal"] == "2027-01-01"
    # assets: the value goes to the field the asset already uses
    assert ctx.put("/memory/assets/savings-book", {"fields": {"balance": 5200, "as_of": "2026-10-01"}}).json()["changed"]
    assert next(a for a in ctx.get("/assets").json()["assets"] if a["id"] == "savings-book")["value"] == "5200.00"
    # guards
    assert ctx.put("/memory/members/Bad Id", {"fields": {"name": "x", "role": "adult"}}).status_code in (409, 422)
    assert ctx.put("/memory/members/anna", {"fields": {"id": "other"}}).json()["error"]["code"] == "id_mismatch"
    assert ctx.put("/memory/members/anna", {"fields": {"nonsense_field": 1}}).status_code == 409
    assert ctx.put("/memory/members/anna", {"fields": {}}).json()["error"]["code"] == "nothing_to_change"
    assert ctx.put("/memory/robots/anna", {"fields": {"a": 1}}).status_code == 422


# ====================================================================== proposals: the typed-code flow

def make_proposal(ctx, amount=420):
    ops = [{"op": "create", "value": {"budgets": []}}, {"op": "append", "path": "budgets", "value": {"id": "prop-b", "category": "food.restaurants", "monthly": amount}}]
    return prop_mod.create(ctx.state.store, "budgets.yaml", ops, "the coach suggests a restaurant budget", "coach-llm")


def test_proposals_list_shows_diff_and_the_terminal_command(ctx):
    p = make_proposal(ctx)
    d = ctx.get("/proposals").json()["proposals"]
    assert len(d) == 1 and d[0]["id"] == p.id and d[0]["accept_command"] == f"uv run coach memory accept {p.id}"
    assert "code" not in d[0] and p.id.rsplit("-", 1)[1] not in [v for k, v in d[0].items() if k == "code"]
    assert d[0]["status"] == "pending" and d[0]["sealed"] and d[0]["applicable"] and "prop-b" in d[0]["diff"] and d[0]["source"] == "coach-llm"
    assert d[0]["changes"] and d[0]["suspicious_paths"] == []
    assert not (ctx.cfg.memory_dir / "budgets.yaml").exists()                      # listing never writes


def test_reject_closes_without_writing(ctx):
    p = make_proposal(ctx)
    assert ctx.client.post(api(f"/proposals/{p.id}/reject"), json={}).status_code == 403
    r = ctx.post(f"/proposals/{p.id}/reject", {"note": "no thanks"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert not (ctx.cfg.memory_dir / "budgets.yaml").exists() and ctx.get("/proposals").json()["proposals"] == []
    assert ctx.get("/proposals", status="all").json()["proposals"][0]["status"] == "rejected"
    assert ctx.post(f"/proposals/{p.id}/reject").status_code == 409
    assert ctx.post("/proposals/p-20260101-abcdef/reject").status_code == 409


def test_accepting_and_reverting_are_not_web_endpoints_at_all(ctx):
    """Accepting a proposal and reverting a history change are decisions for a terminal (`coach memory accept|revert`): the
    web API has no endpoint for them, whatever is sent, so no typed code can be read from or replayed to it."""
    p = make_proposal(ctx)
    code = p.id.rsplit("-", 1)[1]
    for path in (f"/proposals/{p.id}/accept", f"/proposals/{p.id}/accept?force=true", "/memory/history/abc1234/revert",
                 "/proposals/accept", "/memory/revert"):
        for body in ({}, {"code": code}, {"code": code, "force": True, "confirm_fields": []}, {"confirm": "abc1234"}):
            r = ctx.post(path, body)
            assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (path, body)
    assert not (ctx.cfg.memory_dir / "budgets.yaml").exists()
    assert ctx.get("/proposals").json()["proposals"][0]["status"] == "pending"
    paths = ctx.client.get("/api/openapi.json").json()["paths"]
    assert not [x for x in paths if x.endswith("/accept") or x.endswith("/revert")]
    text = ctx.client.get(api("/proposals")).text + ctx.client.get(api("/memory/history")).text
    assert code not in text.replace(p.id, "")                          # the short code is not served anywhere
    ctx.post("/budgets", {"target": "food.groceries", "monthly": "300"})
    cid = ctx.get("/memory/history", limit=1).json()["changes"][0]["id"]
    d = ctx.get(f"/memory/history/{cid}/diff").json()
    assert d["revert_command"] == f"uv run coach memory revert {cid}" and "+budgets:" in d["diff"]


# ====================================================================== connections

def test_connections_listing_masks_ibans(ctx):
    d = ctx.get("/connections").json()
    assert {a["uid"] for a in d["accounts"]} == {"fo", "ce", "rl"}
    fo = next(a for a in d["accounts"] if a["uid"] == "fo")
    assert fo["iban_last4"] == "…0001" and "iban" not in fo and fo["syncs_left_today"] == 4
    raw = ctx.client.get(api("/connections")).text
    assert "FR7600000000000000000001" not in raw and "LT0000000000000000001" not in raw
    assert d["purposes"] == ["main", "cards", "rental", "kids", "savings"] and d["sync"]["state"] == "idle" and d["sync"]["daily_limit"] == 4
    assert {c["bank"] for c in d["consents"]} == {"Fortuneo", "Caisse d'Epargne X", "Revolut"} and all("days_left" in c for c in d["consents"])
    for forbidden in ("private_key", "app_id", "token", "secret", "password"):
        assert forbidden not in raw.lower(), forbidden


def test_account_edit(ctx):
    r = ctx.patch("/accounts/fo", {"label": "Cards", "owner": "anna", "purpose": "cards", "exclude": True})
    assert r.status_code == 200 and (r.json()["label"], r.json()["owner"], r.json()["excluded"]) == ("Cards", "anna", True)
    assert ctx.sql("SELECT label, owner, exclude FROM accounts WHERE uid='fo'") == [("Cards", "anna", 1)]
    ctx.patch("/accounts/fo", {"exclude": False})
    assert ctx.patch("/accounts/fo", {"purpose": "bogus"}).status_code == 400
    assert ctx.patch("/accounts/fo", {"label": "Cards"}).status_code == 200
    assert ctx.patch("/accounts/ce", {"label": "cards"}).status_code == 400                   # label already taken
    assert ctx.patch("/accounts/nope", {"label": "x"}).status_code == 400
    assert ctx.patch("/accounts/fo", {}).status_code == 400                                    # nothing to set
    assert {a["uid"] for a in ctx.get("/meta/filters").json()["accounts"]} == {"fo", "ce", "rl"}
    assert next(a for a in ctx.get("/accounts/balances").json()["accounts"] if a["uid"] == "fo")["label"] == "Cards"


def configure_eb(ctx, monkeypatch, client):
    ctx.cfg.eb_app_id, ctx.cfg.eb_private_key_path = "app", "/dev/null"
    ctx.cfg.eb_redirect_url = "https://localhost:8443/callback"
    monkeypatch.setattr("coach.api.jobs.EnableBankingClient.from_config", classmethod(lambda cls, cfg: client))
    monkeypatch.setattr("coach.api.routes.connections.EnableBankingClient.from_config", classmethod(lambda cls, cfg: client))


def test_sync_needs_configuration(ctx):
    r = ctx.post("/sync")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"


def test_sync_now_respects_the_daily_limit(ctx, monkeypatch):
    fake = FakeClient(pages={"fo": [{"transactions": [eb_tx("n1", "2026-10-03", -12.5, "CARTE NEW SHOP"), eb_tx("n2", "2026-10-03", -7, "CARTE OTHER")]}] * 3},
                      balances={"balances": [{"balance_type": "CLBD", "balance_amount": {"amount": "1400.00", "currency": "EUR"}, "reference_date": "2026-10-04"}]})
    configure_eb(ctx, monkeypatch, fake)
    ctx.cfg.sync_daily_limit = 1
    r = ctx.post("/sync", {"account": "fo"})
    assert r.status_code == 200 and r.json()["state"] == "done" and r.json()["results"][0]["new"] == 2
    assert "2 new transaction(s)" in r.json()["message"]
    assert ctx.get("/sync/status").json()["state"] == "done"
    assert ctx.sql("SELECT COUNT(*) FROM transactions WHERE tx_key LIKE '%n1'") == [(1,)]
    assert ctx.sql("SELECT tx_type FROM tx_enriched WHERE tx_key LIKE '%n1'")                 # parsed by the normalize step (no LLM)
    top = ctx.get("/transactions", q="new shop").json()["items"]
    assert top and top[0]["amount"] == "-12.50"                                                # fresh data visible at once (cache invalidated)
    n_calls = len([c for c in fake.calls if c[1].startswith("/accounts/")])
    again = ctx.post("/sync", {"account": "fo"}).json()                                        # limit 1 reached: skipped, no bank call
    assert again["results"][0]["status"] == "skipped" and len([c for c in fake.calls if c[1].startswith("/accounts/")]) == n_calls
    assert "skipped" in again["message"]
    assert next(a for a in ctx.get("/connections").json()["accounts"] if a["uid"] == "fo")["syncs_left_today"] == 0


def test_sync_failure_is_reported_without_details_leaking(ctx, monkeypatch):
    from coach.ingest.client import ApiError
    fake = FakeClient(pages={"fo": [ApiError(500, "secret-token-xyz oops")]})
    configure_eb(ctx, monkeypatch, fake)
    r = ctx.post("/sync", {"account": "fo"}).json()
    assert r["state"] == "failed" and r["results"][0]["status"] == "failed"
    assert "failed" in r["message"] and ctx.get("/connections").json()["health"]["level"] == "red"


def test_connect_and_reconnect_start_the_existing_flow(ctx, monkeypatch):
    seen = {}

    def fake_flow(con, client, cfg, bank, country, days, replaces=None, **kw):
        seen.update(bank=bank, country=country, days=days, replaces=replaces, no_server=kw["no_server"])
        kw["open_browser"]("https://bank.test/login?state=abc")
        kw["out"]("Open this URL")
        return 0
    monkeypatch.setattr("coach.api.jobs.auth.connect_flow", fake_flow)
    configure_eb(ctx, monkeypatch, FakeClient())
    r = ctx.post("/connections/connect", {"bank": "Fortuneo", "country": "fr", "days": 90})
    assert r.status_code == 409 and "already has an active consent" in r.json()["error"]["message"]      # one owner per bank
    r = ctx.post("/connections/connect", {"bank": "Boursorama", "country": "fr", "days": 90})
    assert r.status_code == 200 and r.json()["state"] == "done" and r.json()["url"] == "https://bank.test/login?state=abc"
    assert seen == {"bank": "Boursorama", "country": "FR", "days": 90, "replaces": None, "no_server": False}
    assert ctx.get("/connections/auth/status").json()["url"] == "https://bank.test/login?state=abc"
    rc = ctx.post("/connections/reconnect", {"session_id": "s1", "days": 120})
    assert rc.status_code == 200 and seen["bank"] == "Fortuneo" and seen["replaces"] == "s1" and seen["days"] == 120
    assert ctx.post("/connections/reconnect", {"session_id": "nope"}).status_code == 409
    assert ctx.post("/connections/connect", {"bank": "X", "country": "FRA"}).status_code == 422


def test_connect_failure_reports_without_a_url(ctx, monkeypatch):
    monkeypatch.setattr("coach.api.jobs.auth.connect_flow", lambda *a, **k: 1)
    configure_eb(ctx, monkeypatch, FakeClient())
    r = ctx.post("/connections/connect", {"bank": "Boursorama", "country": "FR"}).json()
    assert r["state"] == "failed" and r["url"] is None


def test_connect_without_configuration(ctx):
    r = ctx.post("/connections/connect", {"bank": "Boursorama", "country": "FR"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"
    assert ctx.get("/banks", country="FR").status_code == 409


def test_banks_listing_is_mocked(ctx, monkeypatch):
    class C:
        def call(self, method, path, **kw):
            assert (method, path) == ("GET", "/aspsps") and kw["params"]["country"] == "FR"
            return {"aspsps": [{"name": "Fortuneo", "country": "FR", "maximum_consent_validity": 180 * 86400},
                               {"name": "Revolut", "country": "FR", "maximum_consent_validity": 90 * 86400, "beta": True}]}
    configure_eb(ctx, monkeypatch, C())
    d = ctx.get("/banks", country="fr", q="revo").json()
    assert d["banks"] == [{"name": "Revolut", "country": "FR", "max_consent_days": 90, "beta": True}]
    assert len(ctx.get("/banks", country="FR").json()["banks"]) == 2
    assert ctx.get("/banks", country="FRA").status_code == 422


# (the coach endpoints are tested in test_api_coach.py)


# ====================================================================== misc



# ====================================================================== preview == effect (M3), groups (M4), and the smaller rules

def categories_of(ctx, **params):
    items = ctx.get("/transactions", limit=500, **params).json()["items"]
    return {i["tx_key"]: i["category"] for i in items}


def test_preview_equals_effect_for_override_merchant_and_annotation(ctx):
    cases = [
        ({"tx_key": "fm0", "category": "food.restaurants", "scope": "transaction"}, {"q": "fresh market"}),
        ({"tx_key": "fm0", "category": "food.restaurants", "scope": "merchant"}, {"q": "fresh market"}),
        ({"tx_key": "fm0", "category": "food.restaurants", "scope": "memory", "match": "merchant"}, {"q": "fresh market"}),
        ({"tx_key": "fm0", "category": "food.restaurants", "scope": "memory", "match": "transaction"}, {"q": "fresh market"}),
    ]
    for body, filt in cases:
        before = categories_of(ctx, **filt)
        pv = ctx.post("/transactions/category", body, dry_run=True).json()
        assert categories_of(ctx, **filt) == before, "a preview must not change anything"
        assert ctx.sql("SELECT COUNT(*) FROM tx_overrides") == [(0,)] or body["scope"] == "transaction"
        ctx.post("/transactions/category", body)
        after = categories_of(ctx, **filt)
        changed = [k for k in before if before[k] != after[k]]
        assert pv["affected"]["count"] == len(changed), (body, pv["affected"], changed)
        assert all(after[k] == "food.restaurants" for k in changed)
        for k in changed:
            assert before[k] == "food.groceries"
        assert {c["category"]: c["n"] for c in pv["affected"]["from_categories"]} == ({"food.groceries": len(changed)} if changed else {})
        # undo for the next case
        with ctx.state.write() as con:
            con.execute("DELETE FROM tx_overrides")
            con.execute("UPDATE merchants SET category='food.groceries', source='llm', confidence=0.5 WHERE merchant_key='FRESH MARKET'")
            con.commit()
        for f in ("categorization.yaml",):
            from coach.memory.store import MemoryStore
            st = MemoryStore(ctx.cfg.memory_dir, history=False)
            for a in [a.id for a in st.annotations() if a.id.startswith("fresh-market") or a.id.startswith("fm")]:
                st.edit(f, [{"op": "remove", "path": f"annotations[{a}]"}], action="test-cleanup")
        ctx.state.touch()


def test_merchant_preview_sees_an_annotation_that_stops_applying(ctx):
    """The reported bug: an annotation written for the OLD pre-memory category (category_in) no longer matches once the merchant
    is relabelled, so the relabel really changes those transactions. The preview must say so (it used to say 0)."""
    with ctx.state.write() as con:
        for i in range(3):
            add_tx(con, "fo", f"odd{i}", f"2026-08-0{i + 1}", -20.0, "ODD SHOP FAMILLE", "card")
    from coach.memory.store import MemoryStore
    st = MemoryStore(ctx.cfg.memory_dir, history=False)
    st.edit("categorization.yaml", [{"op": "append", "path": "annotations", "value": {
        "id": "odd-hack", "match": {"merchant_key": "^ODD SHOP FAMILLE$", "category_in": ["other.uncategorized"]}, "category": "food.restaurants"}}], action="t")
    ctx.state.touch()
    before = categories_of(ctx, q="odd shop")
    assert set(before.values()) == {"food.restaurants"}
    body = {"tx_key": "odd0", "category": "pets.pets", "scope": "merchant"}
    pv = ctx.post("/transactions/category", body, dry_run=True).json()
    assert pv["affected"]["count"] == 3 and pv["affected"]["blocked"] == [] and pv["changed"] is True
    assert pv["affected"]["from_categories"] == [{"category": "food.restaurants", "n": 3}]
    ctx.post("/transactions/category", body)
    after = categories_of(ctx, q="odd shop")
    assert set(after.values()) == {"pets.pets"} and len([k for k in before if before[k] != after[k]]) == 3


def test_merchant_preview_names_what_still_wins(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "pin1", "2026-08-01", -20.0, "PINNED SHOP", "card")
        add_tx(con, "fo", "pin2", "2026-08-02", -20.0, "PINNED SHOP", "card")
    from coach.memory.store import MemoryStore
    MemoryStore(ctx.cfg.memory_dir, history=False).edit("categorization.yaml", [{"op": "append", "path": "annotations", "value": {
        "id": "pin-it", "match": {"tx_keys": ["pin1"]}, "category": "food.fast_food"}}], action="t")
    ctx.state.touch()
    pv = ctx.post("/transactions/category", {"tx_key": "pin2", "category": "pets.pets", "scope": "merchant"}, dry_run=True).json()["affected"]
    assert pv["count"] == 1 and pv["blocked"] == [{"reason": "the memory annotation 'pin-it'", "n": 1}]
    ov = ctx.post("/transactions/category", {"tx_key": "pin1", "category": "pets.pets", "scope": "transaction"}, dry_run=True).json()
    assert ov["affected"]["count"] == 0 and ov["changed"] is False and ov["affected"]["blocked"][0]["reason"] == "the memory annotation 'pin-it'"


def test_a_preview_leaves_the_caches_and_the_database_alone(ctx):
    snap = ctx.state.snapshot()
    h = ctx.state.version
    ctx.post("/transactions/category", {"tx_key": "fm0", "category": "food.restaurants", "scope": "merchant"}, dry_run=True)
    assert ctx.state.version == h and ctx.state.snapshot() is snap
    assert ctx.sql("SELECT category, source FROM merchants WHERE merchant_key='FRESH MARKET'") == [("food.groceries", "llm")]


def test_group_averages_come_from_the_analytics_and_match_the_drilldown(ctx):
    ov = ctx.get("/categories").json()
    assert ov["groups"] and {g["group"] for g in ov["groups"]} == {c["group"] for c in ov["categories"]}
    food = next(g for g in ov["groups"] if g["group"] == "food")
    detail = ctx.get("/categories/food").json()
    assert food["monthly_avg"] == detail["average"]["monthly"] and food["n_months"] == detail["average"]["n_months"]
    for g in ov["groups"]:
        d = ctx.get(f"/categories/{g['group']}").json()
        assert (d["average"] or {}).get("monthly") == g["monthly_avg"], g["group"]
    assert ov["household_monthly_avg"] == ctx.get("/analytics/averages").json()["household_monthly_avg"]
    assert food["this_month"] and food["last_month"] is not None


def test_balances_show_their_type_and_flag_a_mixed_total(ctx):
    b = ctx.get("/accounts/balances").json()
    assert b["mixed_types"] is False and all(a["booked"] for a in b["accounts"] if a["balance"])
    with ctx.state.write() as con:
        con.execute("INSERT INTO balances VALUES ('rl','2026-10-04T08:00:00+00:00','ITAV',100.0,'EUR','2026-10-04')")
        con.execute("INSERT INTO balances VALUES ('fo','2026-10-04T09:00:00+00:00','ITAV',999.0,'EUR','2026-10-04')")   # the booked CLBD is preferred
        con.commit()
    b = ctx.get("/accounts/balances").json()
    by = {a["uid"]: a for a in b["accounts"]}
    assert by["fo"]["balance_type"] == "CLBD" and by["fo"]["balance"] == "1500.00" and by["fo"]["balance_type_label"] == "booked"
    assert by["rl"]["balance_type"] == "ITAV" and by["rl"]["booked"] is False and by["rl"]["balance_type_label"] == "available (interim)"
    assert b["mixed_types"] is True and b["non_booked"] == [by["rl"]["label"]] and "mixes types" in b["note"] and b["household_total"] == "5800.00"


def test_csv_locale_fr_has_bom_semicolons_and_decimal_commas(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "csv1", "2026-10-02", -12.5, "=cmd|' /C calc'!A0", "card")
    en = ctx.get("/transactions/export.csv", q="cmd").content
    assert not en.startswith(b"\xef\xbb\xbf") and b"amount_eur" in en.split(b"\n")[0] and b",-12.50," in en
    fr = ctx.get("/transactions/export.csv", q="cmd", locale="fr-FR").content
    assert fr.startswith(b"\xef\xbb\xbf")                                                    # UTF-8 BOM for Excel
    head, row = fr.decode("utf-8-sig").strip().splitlines()
    assert head.startswith("date;account;owner;purpose;amount_eur") and ";-12,50;" in row and "'=cmd" in row   # formula neutralised
    it = ctx.get("/transactions/export.csv", q="cmd", locale="it-IT").content                # Italian: same Excel conventions
    assert it.startswith(b"\xef\xbb\xbf") and ";-12,50;" in it.decode("utf-8-sig")
    assert ctx.get("/transactions/export.csv", locale="de-DE").status_code == 422


def test_search_ignores_accents_and_matches_amounts_either_way(ctx):
    with ctx.state.write() as con:
        add_tx(con, "fo", "cafe1", "2026-10-02", -12.5, "CARTE CAFÉ DU COIN", "card")
    for q in ("cafe", "CAFÉ", "café du", "12,5", "12.50", "12,50", "cafe 12.5"):
        keys = [i["tx_key"] for i in ctx.get("/transactions", q=q, limit=500).json()["items"]]
        assert "cafe1" in keys, q
    assert "cafe1" not in [i["tx_key"] for i in ctx.get("/transactions", q="cafe 13,5").json()["items"]]
    assert "cafe1" in [i["tx_key"] for i in ctx.get("/transactions", merchant="Cafe").json()["items"]]


def test_dates_are_validated_everywhere(ctx):
    for bad in ("2026-13-45", "not a date", "2026-02-30"):
        r = ctx.put("/memory/events/trip", {"fields": {"title": "T", "start": bad}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_date", bad
    assert ctx.put("/memory/events/trip", {"fields": {"title": "T", "start": 20260101}}).status_code == 422
    assert ctx.put("/memory/liabilities/home-loan", {"fields": {"end_date": "31/12/2040"}}).json()["error"]["code"] == "bad_date"
    assert ctx.get("/calendar", month="2026-13").status_code == 422
    assert ctx.get("/analytics/month-categories", month="2026-00").status_code == 422
    assert ctx.post("/budgets", {"target": "food.groceries", "monthly": "5", "start": "2026-99-01"}).status_code == 422
    assert ctx.get("/transactions", date_from="2026-13-01").status_code == 422
    assert not (ctx.cfg.memory_dir / "events.yaml").exists()


def test_annotations_reject_unknown_transaction_keys(ctx):
    r = ctx.post("/annotations", {"tx_keys": ["fm0", "ghost-1", "ghost-2"], "tags": ["one_off"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_transaction" and "2 transaction key" in r.json()["error"]["message"]
    assert ctx.post("/annotations", {"tx_keys": ["ghost"], "tags": ["x"]}, dry_run=True).status_code == 422
    assert "ghost" not in (ctx.cfg.memory_dir / "categorization.yaml").read_text()
    assert ctx.post("/annotations", {"tx_keys": ["fm0"], "tags": ["one_off"]}).status_code == 200


def test_review_confirm_uses_the_queue_being_viewed(ctx):
    strict = ctx.get("/review", max_conf=0.4).json()["items"]
    assert "FRESH MARKET" not in {i["key"] for i in strict}                      # confidence 0.5 is above 0.4
    r = ctx.post("/review/confirm", {"key": "FRESH MARKET", "max_conf": 0.4})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_in_queue"
    assert ctx.sql("SELECT source FROM merchants WHERE merchant_key='FRESH MARKET'") == [("llm",)]
    loose = ctx.get("/review", max_conf=0.7).json()["items"]
    assert "FRESH MARKET" in {i["key"] for i in loose}
    assert ctx.post("/review/confirm", {"key": "FRESH MARKET", "max_conf": 0.7}).status_code == 200
    assert ctx.post("/review/confirm", {"key": "FRESH MARKET", "max_conf": 7}).status_code == 422
