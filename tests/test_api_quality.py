"""E12 web API: the gold set and its labelling (CSRF, writes the gold set only), the evaluation runs, the usage and the logs."""
from __future__ import annotations

import datetime as dt


from apihelpers import api, ctx, world  # noqa: F401
from coach.classify import corrections
from coach.quality import gold as G
from memhelpers import make_world
from test_quality_logs import make_runs

TABLES = ("merchants", "tx_overrides", "tx_splits", "merchant_eval", "merchant_aliases")


def tables(c):
    with c.state.read() as con:
        return [con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in TABLES]


def test_every_write_needs_the_csrf_token_and_a_session(ctx):
    for path, body in (("/gold/label", {"tx_key": "fm0", "category": "food.groceries"}), ("/gold/remove", {"tx_key": "fm0"}),
                       ("/gold/bootstrap", {}), ("/eval/classify", {})):
        r = ctx.client.post(api(path), json=body)                          # no X-CSRF-Token
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf", path
    with ctx.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM gold_labels").fetchone()[0] == 0


def test_the_gold_page_data(ctx):
    d = ctx.get("/gold", n=5).json()
    assert d["counts"] == {"total": 0, "by_origin": {}, "by_labeled_by": {}, "categories": 0} and d["latest"] is None and d["runs"] == []
    assert len(d["sample"]) == 5 and d["strategy"] == "money"
    first = d["sample"][0]
    assert {"tx_key", "date", "amount", "description", "account", "category", "source", "confidence", "merchant_key"} <= set(first)
    assert first["source"] in G.SAMPLE_SOURCES and isinstance(first["amount"], float)
    assert ctx.get("/gold", n=5, strategy="stratified").json()["strategy"] == "stratified"
    assert ctx.get("/gold", strategy="nonsense").status_code == 422


def test_sample_endpoint(ctx):
    items = ctx.get("/gold/sample", n=3, seed=3).json()["items"]
    assert len(items) == 3 and [i["tx_key"] for i in items] == [i["tx_key"] for i in ctx.get("/gold/sample", n=3, seed=3).json()["items"]]


def test_labelling_writes_the_gold_set_and_nothing_else(ctx):
    before = tables(ctx)
    memory_before = {p.name: p.read_bytes() for p in ctx.cfg.memory_dir.rglob("*") if p.is_file() and ".history.git" not in p.parts}
    r = ctx.post("/gold/label", {"tx_key": "fm0", "category": "food.restaurants", "note": "lunch"})
    assert r.status_code == 200 and r.json()["counts"]["total"] == 1 and r.json()["counts"]["by_origin"] == {"manual": 1}
    with ctx.state.read() as con:
        row = con.execute("SELECT category, labeled_by, origin, note FROM gold_labels WHERE tx_key='fm0'").fetchone()
    assert row == ("food.restaurants", "user", "manual", "lunch")
    assert tables(ctx) == before                                               # no merchant label, override, split or entity changed
    assert {p.name: p.read_bytes() for p in ctx.cfg.memory_dir.rglob("*") if p.is_file() and ".history.git" not in p.parts} == memory_before
    assert "fm0" not in {i["tx_key"] for i in ctx.get("/gold/sample", n=100).json()["items"]}       # it is not proposed again
    with ctx.state.read() as con:                                                # and the category the app shows did not move
        from coach.classify.rules import resolve
        assert resolve(con, "fm0", "card", "FRESH MARKET") == ("food.groceries", "llm")


def test_labelling_validates(ctx):
    assert ctx.post("/gold/label", {"tx_key": "fm0", "category": "no.such"}).status_code == 422
    assert ctx.post("/gold/label", {"tx_key": "fm0", "category": "other.uncategorized"}).status_code == 422
    r = ctx.post("/gold/label", {"tx_key": "nope", "category": "food.groceries"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rejected"
    assert ctx.post("/gold/label", {"tx_key": "fm0"}).status_code == 422


def test_remove_from_the_gold_set(ctx):
    ctx.post("/gold/label", {"tx_key": "fm1", "category": "food.groceries"})
    assert ctx.post("/gold/remove", {"tx_key": "fm1"}).json() == {"removed": True, "counts": {"total": 0, "by_origin": {}, "by_labeled_by": {}, "categories": 0}}
    assert ctx.post("/gold/remove", {"tx_key": "fm1"}).status_code == 404


def test_bootstrap_previews_then_writes(ctx):
    pre = ctx.post("/gold/bootstrap", dry_run="true").json()
    assert pre["dry_run"] is True and pre["added"]["merchant_label"] == 70 and pre["gold"] is None
    assert ctx.get("/gold").json()["counts"]["total"] == 0                         # the preview wrote nothing
    done = ctx.post("/gold/bootstrap").json()
    assert done["gold"]["by_origin"] == {"annotation": 13, "merchant_label": 70}


def test_scoring_from_the_page_stores_a_run_that_the_page_then_shows(ctx):
    with ctx.state.write() as con:
        corrections.set_override(con, "fm0", "food.restaurants")
    ctx.post("/gold/bootstrap")
    res = ctx.post("/eval/classify").json()
    assert res["run_id"] and res["headline"]["n"] > 0 and res["gold"]["total"] == 84
    page = ctx.get("/gold").json()
    assert page["latest"]["id"] == res["run_id"] and page["latest"]["label"] == "web"
    assert page["latest"]["summary"]["accuracy_tx"] == res["summary"]["accuracy_tx"] and len(page["runs"]) == 1
    runs = ctx.get("/eval/runs", kind="classify").json()["items"]
    assert [r["id"] for r in runs] == [res["run_id"]] and "result" not in runs[0]
    one = ctx.get(f"/eval/runs/{res['run_id']}").json()
    assert one["kind"] == "classify" and one["result"]["headline"]["n"] == res["headline"]["n"]
    assert ctx.get("/eval/runs/99999").status_code == 404
    assert ctx.get("/eval/runs", kind="bad").status_code == 422


def test_usage_endpoint(ctx):
    with ctx.state.write() as con:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        con.execute("""INSERT INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd,
                       cost_is_estimate, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (now, "claude-code", "sonnet", "coach:ask", 3, 100, 50, 0, 0, 0.2, 1, 5.0))
    d = ctx.get("/usage", days=7).json()
    assert d["totals"]["calls"] == 1 and d["lines"][0]["job"] == "coach ask" and d["lines"][0]["notional"] is True
    assert len(d["by_day"]) == 7 and d["month"]["threshold_usd"] is None and "NOTIONAL" in d["note"]
    assert ctx.get("/usage", days=0).status_code == 422
    assert ctx.get("/usage", days=400).status_code == 422


def test_the_usage_threshold_comes_from_the_configuration(ctx):
    ctx.cfg.usage_monthly_warn_usd = 0.1
    with ctx.state.write() as con:
        con.execute("""INSERT INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd,
                       cost_is_estimate, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "claude-code", "sonnet", "label", 3, 100, 50, 0, 0, 0.5, 1, 5.0))
    m = ctx.get("/usage").json()["month"]
    assert m["threshold_usd"] == 0.1 and m["level"] == "high" and m["ratio"] == 5.0


def test_the_tables_must_exist(ctx):
    with ctx.state.write() as con:
        con.execute("DROP TABLE gold_labels")
    assert ctx.get("/gold").status_code == 409 and ctx.get("/gold").json()["error"]["code"] == "migration_pending"


# ---------------------------------------------------------------- the health card

def test_the_health_endpoints_show_the_last_run_summary(cfg, tmp_path):
    from apihelpers import PORT, Ctx, make_client, static_dir
    from coach.api.app import create_app
    make_world(cfg).close()
    make_runs(cfg.log_dir)
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    c = Ctx(app, make_client(app), cfg)
    for path in ("/health", "/connections"):
        r = c.get(path).json()
        rep = r if path == "/health" else r["health"]
        assert rep["last_run"]["run"] == "r2" and rep["last_run"]["outcome"] == "failed" and rep["last_run"]["failed"] == ["sync"]
        assert [s["step"] for s in rep["last_run"]["steps"]] == ["sync", "alerts", "eval"]
    runs = c.get("/logs/runs").json()
    assert [r["run"] for r in runs["items"]] == ["r1", "r2"]


def test_the_health_endpoint_without_any_run(cfg, tmp_path):
    from apihelpers import PORT, Ctx, make_client, static_dir
    from coach.api.app import create_app
    make_world(cfg).close()
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    c = Ctx(app, make_client(app), cfg)
    assert c.get("/health").json()["last_run"] is None and c.get("/logs/runs").json()["items"] == []
