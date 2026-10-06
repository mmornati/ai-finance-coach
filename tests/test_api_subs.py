"""E8 web API: the inventory, contract drafts, usage, alternatives, letters, contact, decisions and savings. Memory writes go through the
store with the source `ui` and can be previewed (dry_run=true); database writes through the one write door; every mutation needs the
session cookie and the CSRF header."""
from __future__ import annotations

import datetime as dt

import pytest

import apihelpers
from apihelpers import TODAY, api, ctx  # noqa: F401
from coach.memory.store import MemoryStore
from coach.subs import decisions as DEC
from subshelpers import build_subs_world


@pytest.fixture
def world(cfg):
    con = build_subs_world(cfg)
    con.execute("INSERT INTO balances VALUES ('fo','2026-10-04T08:00:00+00:00','CLBD',1500.0,'EUR','2026-10-04')")
    con.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',4200.0,'EUR','2026-10-04')")
    con.commit()
    con.close()
    return cfg


def rows(ctx, **params):
    return {r["name"]: r for r in ctx.get("/subs/inventory", **params).json()["rows"]}


def delete(ctx, path):
    return ctx.client.delete(api(path), headers={"X-CSRF-Token": ctx.csrf})


# ---------------------------------------------------------------- inventory

def test_inventory_endpoint(ctx):
    d = ctx.get("/subs/inventory").json()
    assert d["totals"]["services"] == 6 and d["totals"]["yearly"] == "2356.44" and d["totals"]["monthly"] == "196.37"
    assert [g["id"] for g in d["groups_meta"]][:2] == ["streaming_media", "software_cloud"]
    r = {x["name"]: x for x in d["rows"]}
    assert r["Fitclub"]["yearly"] == "478.80" and r["Fitclub"]["contract"]["status"] == "missing" and r["Fitclub"]["draftable"] is True
    t = r["TelcoCo"]["cancellation"]
    assert t["can_cancel_now"] is True and t["early_termination_cost"]["amount"] == 29.99 and t["legal_basis"][0]["id"] == "fr-telecom"
    assert "Verify" in t["verify"] and r["StreamBox"]["usage"]["frequency"] == "never"
    assert set(rows(ctx, group="telecom")) == {"TelcoCo"} and "Oldapp" in rows(ctx, include_ended="true")
    assert ctx.get("/subs/inventory", group="nonsense").status_code == 422
    assert set(rows(ctx, owner="joint")) >= {"StreamBox"} and rows(ctx, owner="mia") == {}                 # scoped by the account owner


def test_a_session_is_required(world, tmp_path):
    from fastapi.testclient import TestClient
    app = apihelpers.create_app(world, insecure=True, port=apihelpers.PORT, static_dir=apihelpers.static_dir(tmp_path), inline_jobs=True)
    c = TestClient(app, base_url=apihelpers.HOST)
    for method, path in (("get", "/subs/inventory"), ("get", "/subs/letter?contract=telco"), ("get", "/subs/contact"), ("get", "/subs/savings"),
                         ("get", "/subs/alternatives")):
        assert getattr(c, method)(api(path)).status_code == 401, path


def test_mutations_need_the_csrf_header(ctx):
    for method, path, body in (("post", "/subs/contracts/draft", {"series": "rec_243e021a61"}), ("put", "/subs/contracts/telco/usage", {"frequency": "never"}),
                               ("post", "/subs/alternatives", {"ref": "telco", "provider": "p", "offer_name": "o", "monthly_price": 1}),
                               ("put", "/subs/contact", {"email": "a@example.org"}), ("post", "/subs/decisions", {"ref": "telco", "decision": "kept"}),
                               ("post", "/subs/usage-questions", {}), ("delete", "/subs/alternatives/alt_1", None), ("post", "/subs/decisions/dec_1/confirm", {})):
        r = getattr(ctx.client, method)(api(path), **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf", (method, path)
    assert ctx.sql("SELECT COUNT(*) FROM alternatives")[0][0] == 0 and not (ctx.cfg.memory_dir / "contracts" / "fitclub.yaml").exists()


# ---------------------------------------------------------------- contract drafts

def test_draft_a_contract_from_a_series_with_preview_then_write(ctx):
    ref = rows(ctx)["Fitclub"]["ref"]
    pv = ctx.post("/subs/contracts/draft", {"series": ref}, dry_run="true").json()
    assert pv["dry_run"] is True and pv["changed"] and "provider: Fitclub" in pv["diff"] and pv["contract"]["kind"] == "membership"
    assert pv["contract"]["missing"] == ["renewal", "commitment_end", "notice_period_days"] and pv["file"] == "contracts/fitclub.yaml"
    assert not (ctx.cfg.memory_dir / "contracts" / "fitclub.yaml").exists()
    r = ctx.post("/subs/contracts/draft", {"series": ref}).json()
    assert r["dry_run"] is False and r["change_id"] and r["question_added"].startswith("q-fill-")
    store = MemoryStore(ctx.cfg.memory_dir, history=False)
    assert "fitclub" in {m.id for _r, m in store.contracts()} and "fill:contract:fitclub" in {q.key for q in store.questions()}
    assert any(h.source == "ui" and "drafted from" in (h.reason or "") for h in MemoryStore(ctx.cfg.memory_dir, history=True).history(None, 10))
    now = rows(ctx)["Fitclub"]
    assert now["contract"]["status"] == "on_file" and now["draftable"] is False and ctx.get("/subs/inventory").json()["totals"]["without_contract"] == 3
    again = ctx.post("/subs/contracts/draft", {"series": ref})
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_draftable"
    assert ctx.post("/subs/contracts/draft", {"series": "rec_ffffffffff"}).status_code == 404
    assert ctx.post("/subs/contracts/draft", {"series": "not-a-ref"}).status_code == 422
    assert ctx.post("/subs/contracts/draft", {"series": rows(ctx)["TelcoCo"]["ref"]}).status_code == 409           # already has a contract


# ---------------------------------------------------------------- usage

def test_usage_preview_write_and_validation(ctx):
    body = {"frequency": "rarely", "last_used": "2026-09-01", "note": "kids only"}
    pv = ctx.put("/subs/contracts/telco/usage", body, dry_run="true").json()
    assert pv["dry_run"] is True and "frequency: rarely" in pv["diff"] and "usage:" not in (ctx.cfg.memory_dir / "contracts" / "telco.yaml").read_text()
    r = ctx.put("/subs/contracts/telco/usage", body).json()
    assert r["changed"] and r["change_id"]
    u = rows(ctx)["TelcoCo"]["usage"]
    assert (u["frequency"], u["last_used"], u["recorded"]) == ("rarely", "2026-09-01", True)
    assert ctx.put("/subs/contracts/nope/usage", {"frequency": "never"}).status_code == 404
    assert ctx.put("/subs/contracts/telco/usage", {"frequency": "sometimes"}).status_code == 422
    f = ctx.put("/subs/contracts/telco/usage", {"frequency": "never", "last_used": "2026-12-01"})
    assert f.status_code == 422 and f.json()["error"]["code"] == "bad_date"
    # the 60-day reminder appears in the inventory, the calendar and the insights, from the USER's record
    ctx.put("/subs/contracts/telco/usage", {"frequency": "monthly", "last_used": "2026-07-01"})
    assert [s["kind"] for s in rows(ctx)["TelcoCo"]["usage"]["signals"]] == ["unused_60_days"]
    cal = ctx.get("/calendar", days=14).json()
    assert any(i["kind"] == "unused_reminder" and "TelcoCo: unused for 95 days" in i["title"] for i in cal["items"])
    ins = ctx.get("/insights").json()
    assert ins["counts"]["subscription"] >= 1 and any(c["kind"] == "subscription" and "TelcoCo: unused for 95 days" in c["title"] for c in ins["cards"])
    cid = next(c["id"] for c in ins["cards"] if "TelcoCo: unused" in c["title"])
    assert ctx.post(f"/insights/{cid}/dismiss").status_code == 200
    assert not any("TelcoCo: unused" in c["title"] for c in ctx.get("/insights").json()["cards"])


def test_usage_questions_endpoint_adds_each_question_once(ctx):
    pv = ctx.post("/subs/usage-questions", dry_run="true").json()
    assert pv["dry_run"] is True and len(pv["questions"]) == 2 and pv["skipped_already_asked"] == 0
    assert ctx.sql("SELECT 1") and not [q for q in MemoryStore(ctx.cfg.memory_dir, history=False).questions() if (q.key or "").startswith("usage:")]
    ctx.post("/subs/usage-questions")
    assert len([q for q in MemoryStore(ctx.cfg.memory_dir, history=False).questions() if (q.key or "").startswith("usage:")]) == 2
    again = ctx.post("/subs/usage-questions").json()
    assert again["questions"] == [] and again["skipped_already_asked"] == 2
    assert rows(ctx)["Cloudbox"]["usage"]["question_asked"] is True


# ---------------------------------------------------------------- alternatives

def test_alternatives_lifecycle_and_validation(ctx):
    ref = rows(ctx)["StreamBox"]["ref"]
    ok = {"ref": ref, "provider": "CheapStream", "offer_name": "Basic", "monthly_price": 8.99, "source_url": "https://example.org/cheap",
          "retrieved_at": "2026-09-20", "method": "find-cheaper", "features": "HD, 2 screens"}
    r = ctx.post("/subs/alternatives", ok)
    assert r.status_code == 200 and r.json()["stored"] and r.json()["id"].startswith("alt_")
    d = ctx.get("/subs/alternatives", ref=ref).json()
    s = d["subscriptions"][0]
    assert d["max_age_days"] == 30 and s["name"] == "StreamBox" and s["monthly"] == "12.99" and s["count"] == 1
    item = s["items"][0]
    assert item["savings"]["yearly"] == "48.00" and item["status"] == "current" and item["source_url"] == "https://example.org/cheap" and s["best"]["id"] == item["id"]
    assert ctx.sql("SELECT source, method FROM alternatives") == [("ui", "find-cheaper")]
    assert ctx.get("/subs/alternatives").json()["subscriptions"][0]["ref"] == ref
    assert rows(ctx)["StreamBox"]["alternatives"]["best"]["provider"] == "CheapStream"
    for bad, msg in (({"monthly_price": 0}, "greater than 0"), ({"source_url": "http://example.org/x"}, "https"), ({"retrieved_at": "2026-10-05"}, "future"),
                     ({"ref": "contract:nothing"}, None)):
        rr = ctx.post("/subs/alternatives", {**ok, **bad})
        assert rr.status_code in (404, 422), bad
        if msg:
            assert msg in rr.json()["error"]["message"]
    assert ctx.sql("SELECT COUNT(*) FROM alternatives")[0][0] == 1
    assert delete(ctx, f"/subs/alternatives/{item['id']}").json()["removed"] is True
    assert delete(ctx, f"/subs/alternatives/{item['id']}").status_code == 404
    assert ctx.get("/subs/alternatives", ref="nothing-like-this").status_code == 404


def test_an_outdated_alternative_is_labelled_and_not_the_best(ctx):
    ref = rows(ctx)["StreamBox"]["ref"]
    ctx.post("/subs/alternatives", {"ref": ref, "provider": "Old", "offer_name": "Promo", "monthly_price": 4.99, "retrieved_at": "2026-08-01"})
    s = ctx.get("/subs/alternatives", ref=ref).json()["subscriptions"][0]
    assert s["outdated"] == 1 and s["best"] is None and s["items"][0]["label"] == "outdated, re-check" and s["items"][0]["age_days"] == 64


# ---------------------------------------------------------------- letters and contact

def test_contact_and_letter(ctx):
    assert ctx.get("/subs/contact").json() == {"contact": {}, "set": False, "local_only": True,
                                               "members": [{"id": "anna", "name": "Anna Rossi"}, {"id": "luca", "name": "Luca Rossi"}]}
    assert ctx.put("/subs/contact", {}).status_code == 422
    pv = ctx.put("/subs/contact", {"address": "12 rue de l'Exemple\n59000 Montfort-Test", "email": "jeanne@example.org"}, dry_run="true").json()
    assert pv["dry_run"] is True and "contact" in pv["diff"] and "contact" not in (ctx.cfg.memory_dir / "household.yaml").read_text()
    assert ctx.put("/subs/contact", {"address": "12 rue de l'Exemple\n59000 Montfort-Test", "email": "jeanne@example.org"}).json()["changed"]
    c = ctx.get("/subs/contact").json()
    assert c["set"] and c["contact"]["address"] == "12 rue de l'Exemple\n59000 Montfort-Test"
    l = ctx.get("/subs/letter", contract="telco").json()
    assert l["lang"] == "fr" and l["channel"] == "lrar" and l["sent"] is False and "TC-778899" in l["text"] and "59000 Montfort-Test" in l["text"]
    assert "Anna Rossi" in l["text"] and l["filename"] == "cancellation-telco-lrar-fr.txt" and l["legal_basis"][0]["id"] == "fr-telecom"
    assert any("29.99 EUR" in n for n in l["notes"]) and l["pdf"] is None
    en = ctx.get("/subs/letter", contract="telco", lang="en", channel="email", holder="luca").json()
    assert en["text"].startswith("Subject: cancellation of contract no. TC-778899") and "Luca Rossi" in en["text"]
    assert ctx.get("/subs/letter", contract="telco", lang="de").status_code == 422
    assert ctx.get("/subs/letter", contract="telco", channel="fax").status_code == 422
    assert ctx.get("/subs/letter", contract="nope").status_code == 404
    r = ctx.get("/subs/letter", contract=rows(ctx)["Fitclub"]["ref"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_contract"
    assert ctx.get("/subs/letter", contract="telco", holder="nobody").status_code == 404
    assert "no-store" in ctx.client.get(api("/subs/letter?contract=telco")).headers["cache-control"]               # the address is never cached


# ---------------------------------------------------------------- decisions and savings

def test_decisions_and_savings(ctx):
    ref = rows(ctx, include_ended="true")["Oldapp"]["ref"]
    pv = ctx.post("/subs/decisions", {"ref": ref, "decision": "cancelled", "decided_on": "2026-01-10", "before_monthly": 3.99}, dry_run="true").json()
    assert pv == {"dry_run": True, "valid": True, "monthly_saving": 3.99} and ctx.sql("SELECT COUNT(*) FROM decisions")[0][0] == 0
    r = ctx.post("/subs/decisions", {"ref": ref, "decision": "cancelled", "decided_on": "2026-01-10", "before_monthly": 3.99, "note": "unused"})
    assert r.status_code == 200 and r.json()["id"].startswith("dec_") and r.json()["monthly_saving"] == 3.99
    assert ctx.sql("SELECT decision, source, state, before_monthly_c, after_monthly_c FROM decisions") == [("cancelled", "ui", "confirmed", 399, 0)]
    s = ctx.get("/subs/savings").json()
    assert (s["realised_monthly"], s["realised_since_decisions"], s["verified"]) == ("3.99", "31.92", 1) and s["decisions"][0]["status"] == "verified"
    assert ctx.get("/subs/inventory", include_ended="true").json()["savings"]["realised_monthly"] == "3.99"
    # validation
    for body, code in (({"ref": ref, "decision": "cancelled", "after_monthly": 2}, 422), ({"ref": ref, "decision": "kept", "after_monthly": 1}, 422),
                       ({"ref": ref, "decision": "cancelled", "decided_on": "2026-12-01"}, 422), ({"ref": ref, "decision": "bogus"}, 422),
                       ({"ref": "nothing", "decision": "cancelled"}, 404)):
        assert ctx.post("/subs/decisions", body).status_code == code, body
    # the coach's proposal waits for the user
    con = ctx.state.new_connection()
    p = DEC.add(con, decision="cancelled", today=TODAY, series_id="rec_x", name="X", before=10, source="coach-llm")
    q = DEC.add(con, decision="cancelled", today=TODAY, series_id="rec_y", name="Y", before=10, source="coach-llm")
    con.close()
    pr = ctx.get("/subs/savings").json()["proposed"]
    assert {x["id"] for x in pr} == {p.id, q.id} and ctx.get("/subs/savings").json()["verified"] == 1
    assert ctx.post(f"/subs/decisions/{p.id}/confirm").json() == {"id": p.id, "state": "confirmed"}
    assert ctx.post(f"/subs/decisions/{p.id}/confirm").status_code == 409
    assert ctx.post(f"/subs/decisions/{q.id}/reject").json()["state"] == "rejected"
    assert ctx.post("/subs/decisions/dec_nope/confirm").status_code == 404 and ctx.post("/subs/decisions/dec_nope/reject").status_code == 404
    assert delete(ctx, f"/subs/decisions/{p.id}").json()["removed"] is True and delete(ctx, "/subs/decisions/dec_nope").status_code == 404


def test_a_contradicted_decision_raises_an_insight_and_a_calendar_item(ctx):
    ref = rows(ctx)["Fitclub"]["ref"]
    ctx.post("/subs/decisions", {"ref": ref, "decision": "cancelled", "decided_on": "2026-08-20", "effective_on": "2026-09-01", "before_monthly": 39.90})
    s = ctx.get("/subs/savings").json()
    assert s["contradicted"] == 1 and s["realised_monthly"] == "0.00" and "latest 2026-09-12 (39.90)" in s["decisions"][0]["reason"]
    assert any("still charged after you cancelled" in c["title"] and c["severity"] == "high" for c in ctx.get("/insights").json()["cards"])
    assert any(i["kind"] == "decision_check" and "still being charged" in i["title"] for i in ctx.get("/calendar", days=14).json()["items"])


def test_the_existing_contract_endpoints_serialise_the_structured_usage(ctx):
    cs = {c["id"]: c for c in ctx.get("/contracts").json()["contracts"]}
    assert cs["streambox"]["usage"] == {"frequency": "never", "last_used": "2026-06-01", "note": "nobody watches it"}
    # the memory form writes the nested usage fields one by one (usage.frequency ...): a validated, unquoted date
    r = ctx.put("/memory/contracts/telco", {"fields": {"usage": {"frequency": "weekly", "last_used": "2026-09-30"}}})
    assert r.status_code == 200
    text = (ctx.cfg.memory_dir / "contracts" / "telco.yaml").read_text()
    assert "frequency: weekly" in text and "last_used: 2026-09-30" in text
