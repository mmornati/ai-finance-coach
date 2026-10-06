"""E15 web API: the properties list, one property (cash flow, P&L, scheme), the tax-year figures, the indicators, the flows to label, and the three
small writes (extension decision, vacancy period, market rate). Every mutation needs the session and the CSRF token; dry_run=true previews
and writes nothing; a child login is denied (deny by default)."""
from __future__ import annotations

import pytest

from apihelpers import HOST, TODAY, api, Ctx, make_client, static_dir, PORT  # noqa: F401
from coach.api.app import create_app
from coach.memory.store import MemoryStore
from rentalhelpers import rental_world


@pytest.fixture
def rctx(cfg, tmp_path):
    con = rental_world(cfg)
    con.close()
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    return Ctx(app, make_client(app), cfg)


def store(c) -> MemoryStore:
    return MemoryStore(c.cfg.memory_dir, history=True)


def asset(c):
    return next(a for a in store(c).assets() if a.id == "rental-flat-1")


def test_the_list_of_properties(rctx):
    d = rctx.get("/rental/properties").json()
    (r,) = d["properties"]
    assert r["id"] == "rental-flat-1" and r["account_link"] == "declared" and r["expected_rent"] == "620.00" and r["vacancy"]["n_missing"] == 1
    assert r["scheme"]["state"] == "active" and r["scheme"]["months_left"] == 41 and d["unlinked_rental_accounts"] == []
    assert "housing.property_tax" in d["property_categories"] and "income.rental" in d["property_categories"]


def test_one_property_in_full(rctx):
    d = rctx.get("/rental/rental-flat-1", months=6).json()
    assert d["id"] == "rental-flat-1" and d["links"]["account"] == "declared" and len(d["cashflow"]["months"]) == 6 and d["years"] == [2026]
    assert d["pnl"]["year"] == 2026 and d["pnl"]["totals"]["net"] == "-5637.25" and d["flows_to_label"] == 0
    assert d["scheme"]["end_date"] == "2030-03-04" and d["asset"]["commitment"]["years"] == 9 and d["tag"] == "property-rental-flat-1"
    assert d["asset"]["vacancies"] == [{"start": "2026-02-01", "end": "2026-02-28", "note": "works between two tenants"}]
    y = rctx.get("/rental/rental-flat-1", year=2025).json()
    assert y["pnl"]["year"] == 2025 and y["pnl"]["n_months"] == 0
    r = rctx.get("/rental/ghost")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_tax_year_and_indicators_and_flows(rctx):
    t = rctx.get("/rental/rental-flat-1/tax", year=2026).json()
    assert t["status"] == "computed" and t["gross_rents"] == "4340.00" and t["micro_foncier"]["taxable"] == "3038.00" and "Nothing is filed" in t["disclaimer"]
    assert rctx.get("/rental/rental-flat-1/tax", year=1990).status_code == 422
    assert rctx.get("/rental/rental-flat-1/tax").json()["year"] == 2026                 # the default: the income year to declare next (October onwards)
    i = rctx.get("/rental/rental-flat-1/indicators", market_rate=2.4, market_date="2026-09-20", bank_fees=500).json()
    assert i["loan_rate"]["status"] == "above_market" and i["loan_rate"]["renegotiation"]["status"] == "computed" and i["market_rate"]["age_days"] == 14
    assert rctx.get("/rental/rental-flat-1/indicators").json()["market_rate"]["status"] == "missing"
    assert rctx.get("/rental/rental-flat-1/indicators", market_rate=60).status_code == 422
    f = rctx.get("/rental/rental-flat-1/flows").json()
    assert f["n"] == 0 and f["flows"] == [] and "housing.property_tax" in f["property_categories"]


def test_the_extension_decision_is_previewed_then_written_with_the_csrf_token(rctx):
    body = {"decision": "extend", "years": 3, "additional_rate_pct": 6, "note": "kept"}
    pv = rctx.post("/rental/rental-flat-1/extension", body, dry_run="true").json()
    assert pv["dry_run"] is True and pv["changed"] and "extension" in pv["diff"]
    assert asset(rctx).commitment.extension is None                                                  # nothing written by the preview
    res = rctx.post("/rental/rental-flat-1/extension", body)
    assert res.status_code == 200 and res.json()["dry_run"] is False
    e = asset(rctx).commitment.extension
    assert e.decision == "extend" and e.years == 3 and e.additional_rate_pct == 6 and e.decided_on == TODAY
    assert store(rctx).history(limit=1)[0].source == "ui"
    assert rctx.get("/rental/rental-flat-1").json()["scheme"]["effective_end_date"] == "2033-03-04"
    assert rctx.post("/rental/rental-flat-1/extension", {"decision": "extend"}).status_code == 422          # an extension needs its length
    assert rctx.post("/rental/rental-flat-1/extension", {"decision": "nope"}).status_code == 422


def test_a_vacancy_and_a_market_rate_are_recorded(rctx):
    assert rctx.post("/rental/rental-flat-1/vacancy", {"start": "2026-05-01", "end": "2026-05-31"}).status_code == 200
    assert rctx.post("/rental/rental-flat-1/vacancy", {"start": "2026-06-10", "end": "2026-06-01"}).status_code == 422
    assert [str(v.start) for v in asset(rctx).vacancies] == ["2026-02-01", "2026-05-01"]
    assert rctx.get("/rental/rental-flat-1").json()["cashflow"]["vacancy"]["missing_months"] == []
    assert rctx.post("/rental/rental-flat-1/market-rate", {"rate_pct": 2.7, "as_of": "2026-09-30", "source": "a quote"}).status_code == 200
    assert asset(rctx).market_rate.rate_pct == 2.7
    assert rctx.post("/rental/rental-flat-1/market-rate", {"rate_pct": 70}).status_code == 422
    assert rctx.post("/rental/ghost/market-rate", {"rate_pct": 2.7}).status_code == 404


def test_the_facts_of_a_property_are_written_with_the_generic_asset_endpoint(rctx):
    r = rctx.put("/memory/assets/rental-flat-1", {"fields": {"commitment": {"tenant_income_limit": 33000, "start_date": "2021-04-01"}, "value": 205000}})
    assert r.status_code == 200
    a = asset(rctx)
    assert a.commitment.tenant_income_limit == 33000 and str(a.commitment.start_date) == "2021-04-01" and a.value == 205000
    new = rctx.put("/memory/assets/rental-flat-9", {"fields": {"kind": "real_estate_rental", "account": "rn"}})
    assert new.status_code == 200 and any(x.id == "rental-flat-9" for x in store(rctx).assets())


def test_writes_without_the_csrf_token_or_the_session_are_refused(rctx, cfg, tmp_path):
    r = rctx.client.post(api("/rental/rental-flat-1/vacancy"), json={"start": "2026-05-01"})
    assert r.status_code == 403
    anon = make_client(rctx.app, logged_in=False)
    assert anon.get(api("/rental/properties")).status_code == 401


def test_the_rental_cards_reach_the_insights_feed_with_their_own_kind(rctx):
    feed = rctx.get("/insights").json()
    cards = [c for c in feed["cards"] if c["kind"] == "rental"]
    assert [c["subtype"] for c in cards] == ["rent_cap"] and feed["counts"]["rental"] == 1
    assert cards[0]["evidence"] == ["rental-flat-1"] and cards[0]["subject"] == "rental-flat-1"


def test_a_child_login_cannot_reach_any_rental_endpoint(rctx):
    from coach.api.app import child_allowed
    for path in ("/api/v1/rental/properties", "/api/v1/rental/rental-flat-1", "/api/v1/rental/rental-flat-1/tax"):
        assert not child_allowed("GET", path)
    for path in ("/api/v1/rental/rental-flat-1/extension", "/api/v1/rental/rental-flat-1/vacancy", "/api/v1/rental/rental-flat-1/market-rate"):
        assert not child_allowed("POST", path)
