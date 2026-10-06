"""E10 web API: the alerts center. Local state only (ack / snooze / mute), a read-only channel view, dry-run tests of a channel, the
digest preview. Session cookie + CSRF on every call; nothing here can send anything outside the machine."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import apihelpers
from alerthelpers import SECRETS, build_alert_world
from apihelpers import TODAY, api, ctx  # noqa: F401
from coach import secrets
from coach.api.app import create_app
from coach.config import load_config


@pytest.fixture
def world(cfg):
    build_alert_world(cfg).close()
    return cfg


def enable_all(cfg):
    cfg.config_path.write_text(cfg.config_path.read_text() + '\n[alerts]\nexternal_detail = "summary"\n'
                               '[alerts.ntfy]\nenabled = true\nurl = "https://ntfy.example.net/coach-9f3a"\n'
                               '[alerts.email]\nenabled = true\nhost = "smtp.example.net"\nusername = "mailer"\n'
                               'from = "coach@example.net"\nto = "owner@example.net"\n'
                               '[alerts.telegram]\nenabled = true\nchat_id = "424242"\n[alerts.macos]\nenabled = true\n')
    for k, v in SECRETS.items():
        secrets.set_secret(k, v)
    return load_config(cfg.config_path, env={})


def ctx_for(cfg, tmp_path):
    app = create_app(cfg, insecure=True, port=apihelpers.PORT, static_dir=apihelpers.static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    return apihelpers.Ctx(app, apihelpers.make_client(app), cfg)


@pytest.fixture
def ctx_world(world, tmp_path):
    return ctx_for(world, tmp_path)


def checked(c):
    r = c.post("/alerts/check")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- list, check, badge

def test_before_any_check_the_list_is_empty_and_the_kinds_are_known(ctx):
    d = ctx.get("/alerts").json()
    assert d["ready"] is True and d["items"] == [] and d["counts"]["open"] == 0
    assert {k["kind"] for k in d["kinds"]} >= {"consent", "sync_failing", "unusual_charge", "price_increase", "low_balance", "budget",
                                              "loan_alert", "loa_end", "unused_subscription", "contract_notice", "decision_contradicted"}
    assert d["settings"] == {"min_severity": "low", "external_detail": "minimal", "quiet_hours": "", "max_per_week": 7}
    assert ctx.get("/alerts/summary").json() == {"ready": True, "open": 0, "new": 0, "high": 0}


def test_check_keeps_events_without_sending_anything(world, tmp_path):
    cfg = enable_all(world)                      # every channel enabled: the real transports fail loudly in tests, so a send would error
    c = ctx_for(cfg, tmp_path)
    r = checked(c)
    assert r["new"] > 5 and r["sent"] is False and r["escalated"] == 0
    with c.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM alert_events WHERE status='sent'").fetchone()[0] == 0
    again = checked(c)
    assert again["new"] == 0 and again["unchanged"] == r["candidates"]


def test_list_filters_counts_and_ordering(ctx_world):
    c = ctx_world
    checked(c)
    d = c.get("/alerts").json()
    items = d["items"]
    assert items[0]["severity"] == "high" and d["counts"]["open"] == len(items) and d["counts"]["high"] >= 3
    assert all(i["status"] in ("new", "sent", "snoozed") for i in items)
    assert {i["kind"] for i in c.get("/alerts", kind="consent").json()["items"]} == {"consent"}
    assert {i["severity"] for i in c.get("/alerts", severity="high").json()["items"]} == {"high"}
    assert c.get("/alerts", status="acked").json()["items"] == []
    assert set(items[0]) >= {"id", "kind", "severity", "title", "body", "payload", "status", "created", "channels_sent", "escalations", "resolved"}
    for bad in ({"status": "x"}, {"kind": "x"}, {"severity": "x"}):
        assert c.get("/alerts", **bad).status_code == 422
    s = c.get("/alerts/summary").json()
    assert s["open"] == d["counts"]["open"] and s["high"] == d["counts"]["high"] and s["new"] == s["open"]


def test_ack_snooze_restore(ctx_world):
    c = ctx_world
    checked(c)
    a = c.get("/alerts", kind="consent", severity="high").json()["items"][0]
    assert c.post(f"/alerts/{a['id']}/ack").json() == {"id": a["id"], "status": "acked"}
    assert a["id"] not in [i["id"] for i in c.get("/alerts").json()["items"] if i["status"] in ("new", "sent")]
    assert c.get("/alerts", status="acked").json()["items"][0]["id"] == a["id"]
    assert c.post(f"/alerts/{a['id']}/restore").json()["status"] == "new"
    sn = c.post(f"/alerts/{a['id']}/snooze", {"days": 5}).json()
    assert sn["status"] == "snoozed" and sn["snoozed_until"] == "2026-10-09"
    assert c.get("/alerts", status="snoozed").json()["items"][0]["id"] == a["id"]
    assert c.post(f"/alerts/{a['id']}/snooze", {"days": 0}).status_code == 422
    assert c.post(f"/alerts/{a['id']}/snooze", {"days": 181}).status_code == 422
    for path in ("ack", "restore"):
        assert c.post(f"/alerts/alr_nope/{path}").status_code == 404
    assert c.post("/alerts/alr_nope/snooze", {"days": 2}).status_code == 404


def test_mute_and_snooze_a_kind(ctx_world):
    c = ctx_world
    checked(c)
    n = len(c.get("/alerts", kind="unusual_charge").json()["items"])
    assert n > 0
    assert c.post("/alerts/kinds/unusual_charge/mute").json() == {"kind": "unusual_charge", "muted": True}
    d = c.get("/alerts").json()
    assert not [i for i in d["items"] if i["kind"] == "unusual_charge" and i["status"] != "suppressed"]
    assert next(k for k in d["kinds"] if k["kind"] == "unusual_charge")["muted"] is True
    assert len(c.get("/alerts", status="suppressed").json()["items"]) == n
    checked(c)                                                       # a new check keeps them suppressed
    assert len(c.get("/alerts", status="suppressed").json()["items"]) == n
    assert c.post("/alerts/kinds/unusual_charge/unmute").json()["muted"] is False
    assert c.get("/alerts", status="suppressed").json()["items"] == []
    s = c.post("/alerts/kinds/consent/snooze", {"days": 3}).json()
    assert s["snoozed_until"] == "2026-10-07" and s["events"] == 2
    assert next(k for k in c.get("/alerts").json()["kinds"] if k["kind"] == "consent")["snoozed_until"] == "2026-10-07"
    assert c.post("/alerts/kinds/consent/wake").json()["events"] == 2
    for path in ("mute", "unmute", "wake"):
        assert c.post(f"/alerts/kinds/nonsense/{path}").status_code == 404
    assert c.post("/alerts/kinds/nonsense/snooze", {"days": 2}).status_code == 404


# ---------------------------------------------------------------- channels (read only) and dry-run tests

def test_channels_default_all_off_and_nothing_to_enable_here(ctx):
    d = ctx.get("/alerts/channels").json()
    assert d["in_app"]["enabled"] is True and [c["enabled"] for c in d["channels"]] == [False] * 4
    assert d["external_detail"] == "minimal" and "Nothing is enabled from this page" in d["how_to_enable"]
    assert {c["channel"] for c in d["channels"]} == {"macos", "ntfy", "email", "telegram"} and d["recent_deliveries"] == []


def test_channels_view_shows_readiness_and_never_a_secret(world, tmp_path):
    cfg = enable_all(world)
    c = ctx_for(cfg, tmp_path)
    d = c.get("/alerts/channels").json()
    assert [x["ready"] for x in d["channels"]] == [True] * 4 and d["external_detail"] == "summary"
    blob = json.dumps(d)
    for v in SECRETS.values():
        assert v not in blob
    assert "owner@example.net" not in blob and "424242" not in blob and "coach-9f3a" not in blob       # targets are masked
    secrets.delete_secret("smtp_password")
    d = ctx_for(cfg, tmp_path / "again").get("/alerts/channels").json()
    mail = next(x for x in d["channels"] if x["channel"] == "email")
    assert mail["ready"] is False and "smtp_password" in mail["problems"][0]


@pytest.mark.parametrize("name", ["ntfy", "email", "telegram", "macos"])
def test_test_channel_is_a_dry_run_that_returns_the_exact_message(world, tmp_path, name):
    cfg = enable_all(world)
    c = ctx_for(cfg, tmp_path)
    d = c.post(f"/alerts/channels/{name}/test").json()
    assert d["dry_run"] is True and d["sent"] is False and d["ready"] is True and d["channel"] == name
    assert "invented sample events" in d["sample"]
    blob = json.dumps(d)
    for v in SECRETS.values():
        assert v not in blob
    for target in ("owner@example.net", "424242", "coach-9f3a", "mailer"):          # the web app never shows the topic, the address or the chat id
        assert target not in blob, target
    if name != "macos":
        assert d["message"]["title"] == "Coach (test)"
        assert "- Unusual charge (high), about 150 EUR" in d["message"]["body"]          # summary mode of this config
    with c.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 0


def test_test_channel_on_a_disabled_channel_still_previews(ctx):
    d = ctx.post("/alerts/channels/ntfy/test").json()
    assert d["enabled"] is False and d["ready"] is False and d["sent"] is False
    assert d["message"]["body"] == "Coach: 2 new alerts (1 high). Open the app."
    assert ctx.post("/alerts/channels/carrier-pigeon/test").status_code == 404


# ---------------------------------------------------------------- digest, feed, migration

def test_digest_preview_is_computed_not_stored(ctx_world):
    c = ctx_world
    d = c.get("/alerts/digest").json()
    assert d["markdown"].startswith("# Weekly summary, ") and {"spent_c", "usual_c", "alerts", "budgets", "savings"} <= set(d)
    assert c.sql("SELECT COUNT(*) FROM insights WHERE skill='weekly-local'") == [(0,)]


def test_the_insights_feed_points_at_the_alerts_center(ctx_world):
    c = ctx_world
    assert c.get("/insights").json()["alerts"] == {"open": 0, "high": 0}
    checked(c)
    a = c.get("/insights").json()["alerts"]
    assert a["open"] == c.get("/alerts/summary").json()["open"] > 0 and a["high"] >= 3


def test_without_the_migration_the_center_degrades_cleanly(ctx_world):
    c = ctx_world
    with c.state.write() as con:
        con.execute("DROP TABLE alert_events")
    d = c.get("/alerts").json()
    assert d["ready"] is False and "coach db migrate" in d["message"]
    assert c.get("/alerts/summary").json()["ready"] is False and c.get("/insights").json()["alerts"] == {"open": 0, "high": 0}
    assert c.post("/alerts/check").status_code == 409 and c.post("/alerts/kinds/consent/mute").status_code == 409


def test_alerts_disabled_in_config(world, tmp_path):
    world.config_path.write_text(world.config_path.read_text() + "\n[alerts]\nenabled = false\n")
    c = ctx_for(load_config(world.config_path, env={}), tmp_path)
    r = c.post("/alerts/check")
    assert r.status_code == 409 and r.json()["error"]["code"] == "alerts_disabled"
    assert c.get("/alerts").json()["enabled"] is False


# ---------------------------------------------------------------- auth and CSRF

def test_every_alerts_endpoint_needs_a_session_and_mutations_need_csrf(ctx_world):
    c = ctx_world
    anon = TestClient(c.app, base_url=apihelpers.HOST)
    gets = ["/alerts", "/alerts/summary", "/alerts/channels", "/alerts/digest"]
    posts = ["/alerts/check", "/alerts/alr_x/ack", "/alerts/alr_x/snooze", "/alerts/alr_x/restore", "/alerts/kinds/consent/mute",
             "/alerts/kinds/consent/unmute", "/alerts/kinds/consent/snooze", "/alerts/kinds/consent/wake", "/alerts/channels/ntfy/test"]
    for p in gets:
        assert anon.get(api(p)).status_code == 401, p
    for p in posts:
        assert anon.post(api(p), json={}).status_code == 401, p
        assert c.client.post(api(p), json={}).status_code == 403, p                    # a session but no CSRF token
        evil = {"X-CSRF-Token": c.csrf, "Origin": "https://evil.example"}
        assert c.client.post(api(p), json={}, headers=evil).status_code == 403, p
    with c.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 0        # nothing changed


def test_the_alerts_api_has_no_route_that_enables_a_channel_or_sends(ctx):
    paths = ctx.app.openapi()["paths"]
    mine = {p: ops for p, ops in paths.items() if "/alerts" in p}
    assert mine and all(set(ops) <= {"get", "post"} for ops in mine.values())
    assert not [p for p in mine if any(w in p for w in ("send", "enable", "settings", "config", "secret"))]
