"""Horae's brief (coach.horae): the document (aggregates only, schema-valid, stable keys) and the `/mcp` endpoint (bearer token, only the
`brief` tool, bounded, off by default). Synthetic household of tests/alerthelpers.py: every name, merchant and amount is invented."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re

import anyio
import pytest

from alerthelpers import NOW, SENTINELS, TODAY, build_alert_world
from apihelpers import PORT, make_client, static_dir
from coach.horae import brief as B
from coach.horae import endpoint as E
from helpers import add_tx

# the schema is vendored byte for byte from horae-dev/plugin-mcp-brief at this commit (B.SCHEMA_SOURCE)
SCHEMA_SHA256 = "f5c18c6611dcfcd40a31542c97b0f200a420e82c7dd7586b27b05910904274ff"
TOKEN = "t" * 20 + "-synthetic-test-token-0123456789"
HOST = "coach.example.net"
BUDGETS = ("budgets:\n  - id: groceries\n    category: food.groceries\n    monthly: 60\n"
           "  - id: energy\n    category: housing.energy\n    monthly: 500\n"
           "  - id: mia-pocket\n    category: shopping.clothing\n    monthly: 10\n    owner: mia\n")


def world(cfg, *, expired: bool = False):
    """The alert world (a consent at D-2, another at D-10, the account 'fo' with 3 failed syncs) plus budgets, October spending, a
    child's purchase and an uncategorised merchant."""
    con = build_alert_world(cfg)
    (cfg.memory_dir / "budgets.yaml").write_text(BUDGETS)
    for i in range(3):
        add_tx(con, "fo", f"oct-gro{i}", f"2026-10-0{i + 1}", -30.0, "ACME GROCERS", "card")
    add_tx(con, "rl", "kid-buy", "2026-10-02", -999.0, "TOYLAND MEGASTORE", "card")
    add_tx(con, "ce", "mystery", "2026-10-03", -17.0, "ZQX UNKNOWN 4411", "card")
    if expired:
        con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s1'", ((NOW - dt.timedelta(days=1)).isoformat(),))
    con.commit()
    return con


def facts_of(cfg, con):
    from coach.analytics.api import build_dataset
    from coach.classify.commands import review_rows
    from coach.memory.store import MemoryStore
    store = MemoryStore(cfg.memory_dir, history=False)
    ds = build_dataset(con, cfg, TODAY)
    return B.collect(con, cfg, ds, store, review_rows=review_rows(con, cfg, 0.7, 10 ** 9), now=NOW), B.violations_for(store, con, cfg)


def produce(cfg, con, base=None):
    return B.produce(lambda: facts_of(cfg, con), now=NOW, base=base)


# ---------------------------------------------------------------- the schema

def test_the_vendored_schema_is_the_published_v1():
    assert hashlib.sha256(B.SCHEMA_FILE.read_bytes()).hexdigest() == SCHEMA_SHA256
    assert B.schema()["$id"].endswith("/plugin-mcp-brief/main/schemas/brief/v1.json")
    assert "81d2dc2011e1b694f5d38706574c2258627fb69a" in B.SCHEMA_SOURCE


def test_the_error_brief_is_valid_and_the_validator_has_the_plugins_extra_rules():
    assert B.validate(B.error_brief(NOW)) == []
    doc = B.error_brief(NOW)
    doc["valid_until"] = "2026-10-07T12:00:00Z"                      # 72 h: the plugin refuses more than 48
    assert B.validate(doc) == ["validity window"]
    doc = B.error_brief(NOW)
    doc["tasks"] = [{"id": "a", "title": "x"}, {"id": "a", "title": "y"}]
    assert B.validate(doc) == ["repeated task id"]
    doc = B.error_brief(NOW)
    doc["tasks"] = [{"id": "a", "title": "x", "due": "2026-02-31"}]
    assert B.validate(doc) == ["impossible date"]
    doc = B.error_brief(NOW)
    doc["headline"] = "two\nlines"
    assert B.validate(doc)
    doc = B.error_brief(NOW)
    doc["extra"] = 1
    assert B.validate(doc)


# ---------------------------------------------------------------- the document

def test_the_brief_of_a_household_is_valid_aggregated_and_names_nothing(cfg):
    con = world(cfg)
    doc = produce(cfg, con, base=f"https://{HOST}/")
    assert B.validate(doc) == []
    assert doc["status"] == {"state": "ok", "reason": None}
    assert doc["service"] == {"id": "finance-coach", "name": "Finance"}
    assert doc["generated_at"] == "2026-10-04T12:00:00Z" and doc["valid_until"] == "2026-10-05T12:00:00Z"
    # October everyday spending of the adults: 3 x 30 groceries + 17 uncategorised + 22.50 home insurance (the child's 999 is left out)
    assert doc["headline"].startswith("October so far: EUR 130 spent; budgets: 1 over")
    alerts = {a["key"]: a for a in doc["alerts"]}
    s1, s2 = B.ref("session", "s1"), B.ref("session", "s2")
    assert alerts[f"consent.{s1}.expiring"]["severity"] == "warn"
    assert alerts[f"consent.{s1}.expiring"]["title"] == "A bank consent expires in 2 days"
    assert alerts[f"sync.{s1}.failing"]["severity"] == "warn"          # 3 failures = the threshold: not yet "broken"
    over = alerts[f"budget.{B.ref('budget', 'groceries')}.2026-10.over"]
    assert over == {"key": over["key"], "severity": "warn", "title": "Groceries budget exceeded: 150% used",
                    "link": f"https://{HOST}/budgets"}
    assert not any(a["severity"] == "crit" for a in doc["alerts"])
    tasks = {t["id"]: t for t in doc["tasks"]}
    assert tasks[f"consent.{s1}.renew.2026-10-06"]["due"] == "2026-10-06"
    assert tasks[f"consent.{s2}.renew.2026-10-14"]["title"] == "Renew a bank consent before 2026-10-14"
    assert tasks["review.uncategorised.2026-W40"]["link"] == f"https://{HOST}/review"
    assert all(t["link"].startswith(f"https://{HOST}/") for t in doc["tasks"])
    assert [link["label"] for link in doc["links"]][0] == "Finance coach" and len(doc["links"]) <= 5
    text = json.dumps(doc).replace(HOST, "").lower()
    for s in SENTINELS + ["TOYLAND", "ZQX", "ACME", "999", "FR76"]:
        assert s.lower() not in text, s
    for word in ("mia", "fo", "ce", "rl", "s1", "s2"):                  # member, account and session ids
        assert not re.search(rf"\b{word}\b", text), word
    assert "pocket" not in text.lower() and "clothing" not in text.lower()   # the child's budget


def test_keys_and_ids_are_stable_across_runs_and_hours(cfg):
    con = world(cfg)
    a = produce(cfg, con)
    b = B.produce(lambda: facts_of(cfg, con), now=NOW + dt.timedelta(hours=5))
    assert [x["key"] for x in a["alerts"]] == [x["key"] for x in b["alerts"]]
    assert [x["id"] for x in a["tasks"]] == [x["id"] for x in b["tasks"]]
    assert a["links"] == [] and all("link" not in x for x in a["alerts"] + a["tasks"])     # no remote host: no link at all


def test_an_expired_consent_is_the_crit_alert_and_degrades_the_brief(cfg):
    con = world(cfg, expired=True)
    doc = produce(cfg, con)
    assert B.validate(doc) == []
    crit = [a for a in doc["alerts"] if a["severity"] == "crit"]
    assert [a["key"] for a in crit] == [f"consent.{B.ref('session', 's1')}.expired"]
    assert doc["alerts"][0] == crit[0]                                   # crit first
    assert doc["status"]["state"] == "degraded" and "expired" in doc["status"]["reason"]
    assert not any(a["key"].startswith("sync.") for a in doc["alerts"])   # the dead consent explains the failed syncs
    assert f"consent.{B.ref('session', 's1')}.reconnect" in {t["id"] for t in doc["tasks"]}


def test_a_long_failing_sync_is_crit(cfg):
    con = world(cfg)
    for d in (4, 5, 6):
        con.execute("INSERT INTO sync_log VALUES ('fo',?,0,0,0,'HTTP 500')", (f"2026-10-0{d}T05:00:00+00:00",))
    con.commit()
    doc = produce(cfg, con)
    sync = [a for a in doc["alerts"] if a["key"].startswith("sync.")]
    assert sync[0]["severity"] == "crit" and sync[0]["title"] == "A bank connection is broken: 6 syncs failed in a row"
    assert doc["status"]["state"] == "degraded"


def test_stale_data_degrades_and_an_empty_household_is_still_valid(cfg, tmp_path):
    f = B.Facts(TODAY, "2026-10", 0, True, TODAY - dt.timedelta(days=20))
    doc = B.finalize(B.compose(f, NOW, None), NOW, lambda _o: [])
    assert doc["status"] == {"state": "degraded", "reason": "The newest transaction is 20 days old: the figures may be incomplete."}
    doc = B.finalize(B.compose(B.Facts(TODAY, "2026-10", 0, False, None), NOW, None), NOW, lambda _o: [])
    assert doc["headline"] == "No bank data yet: connect a bank in the app." and B.validate(doc) == []


def test_pending_proposals_and_open_questions_become_weekly_tasks():
    f = B.Facts(TODAY, "2026-10", 12345, True, TODAY, pending_proposals=2, open_questions=1, unsure_merchants=3)
    doc = B.finalize(B.compose(f, NOW, "https://coach.example.net/"), NOW, lambda _o: [])
    assert {t["id"]: t["title"] for t in doc["tasks"]} == {
        "review.low-confidence.2026-W40": "Check 3 merchant labels the AI was unsure about",
        "memory.proposals.2026-W40": "Review 2 pending memory proposals",
        "memory.questions.2026-W40": "Answer 1 open question from the coach"}
    assert doc["headline"] == "October so far: EUR 123 spent (no budgets set)."


def test_the_privacy_guard_drops_an_item_naming_the_household(cfg):
    """A custom category can hold anything: an item whose words trip the finance tools' guard is dropped, the rest stays."""
    con = world(cfg)
    facts, violations = facts_of(cfg, con)
    facts.budgets.append(B.BudgetFact("abc", B.category_words("gifts.anna_rossi"), "over", 130, 140))
    doc = B.produce(lambda: (facts, violations), now=NOW)
    assert "Anna" not in json.dumps(doc) and B.validate(doc) == []
    assert any(a["key"].startswith("budget.") for a in doc["alerts"])    # the groceries alert is still there
    bad = B.compose(facts, NOW, None)
    bad["headline"] = "Luca Rossi spent a lot"
    out, dropped = B.guarded(bad, violations)
    assert out["headline"] == "Your finance brief is ready in the app." and dropped == 2


def test_any_failure_gives_the_fixed_error_brief(caplog):
    def boom():
        raise RuntimeError("secret detail FR7630006000011234567890189")
    with caplog.at_level(logging.INFO, logger="coach.horae"):
        doc = B.produce(boom, now=NOW)
    assert doc == B.error_brief(NOW) and B.validate(doc) == []
    assert "FR76" not in caplog.text and "RuntimeError" in caplog.text


def test_an_oversized_or_invalid_document_is_replaced(monkeypatch):
    f = B.Facts(TODAY, "2026-10", 0, True, TODAY)
    monkeypatch.setattr(B, "MAX_BYTES", 100)
    assert B.finalize(B.compose(f, NOW, None), NOW, lambda _o: [])["status"]["state"] == "error"


def test_link_base_only_for_a_remote_host(cfg):
    from dataclasses import replace
    assert B.link_base(cfg) is None
    assert B.link_base(replace(cfg, ui_allow_remote=True, ui_allowed_hosts=(HOST,))) == f"https://{HOST}/"
    assert B.link_base(replace(cfg, ui_allow_remote=True, ui_allowed_hosts=("bad host/x",))) is None


# ---------------------------------------------------------------- the endpoint

def make_app(cfg, tmp_path, monkeypatch, *, token=TOKEN, remote=True, **over):
    from dataclasses import replace
    from coach.api.app import create_app
    if token is not None:
        monkeypatch.setenv("COACH_HORAE_BRIEF_TOKEN", token)
    cfg = replace(cfg, ui_allow_remote=remote, ui_remote_tls_ack=remote, ui_allowed_hosts=(HOST,) if remote else (), **over)
    world(cfg).close()
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    app.state.brief.answer = E.web_answer(app.state.coach, lambda: NOW)
    return app


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}


def post(client, body, token=TOKEN, **headers):
    h = dict(MCP_HEADERS, **headers)
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    return client.post(E.PATH, content=json.dumps(body), headers=h)


@pytest.mark.parametrize("token, over", [(None, {}), ("short-token", {}), (TOKEN, {"privacy_local_only": True}),
                                         (TOKEN, {"privacy_offline": True})])
def test_the_endpoint_is_off_without_a_good_token_or_in_a_local_privacy_mode(cfg, tmp_path, monkeypatch, token, over):
    app = make_app(cfg, tmp_path, monkeypatch, token=token, **over)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    assert not app.state.brief.enabled
    assert post(c, INIT).status_code == 404
    r = c.get(E.PATH, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404 and "<title>" not in r.text              # not the page shell either


@pytest.mark.parametrize("auth", [None, f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"Basic {TOKEN}", f"bearer{TOKEN}", "Bearer "])
def test_a_missing_or_wrong_token_is_401_and_nothing_is_served(cfg, tmp_path, monkeypatch, auth):
    app = make_app(cfg, tmp_path, monkeypatch)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    h = dict(MCP_HEADERS, **({"Authorization": auth} if auth else {}))
    r = c.post(E.PATH, content=json.dumps(INIT), headers=h)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert r.json() == {"error": {"code": "unauthorized", "message": "a valid bearer token is required"}}


def test_the_token_opens_the_brief_only_never_the_api_or_another_host(cfg, tmp_path, monkeypatch):
    app = make_app(cfg, tmp_path, monkeypatch)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/api/v1/session", headers=auth).status_code == 401
    assert c.get("/api/v1/analytics/coverage", headers=auth).status_code == 401
    other = make_client(app, base_url="https://evil.example.org", logged_in=False)
    assert post(other, INIT).status_code == 421                          # the Host check of the web app still applies first


def test_only_post_is_served_a_get_would_hold_an_event_stream_open_forever(cfg, tmp_path, monkeypatch):
    app = make_app(cfg, tmp_path, monkeypatch)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    auth = dict(MCP_HEADERS, Authorization=f"Bearer {TOKEN}")
    for method in ("GET", "DELETE"):
        r = c.request(method, E.PATH, headers=auth)
        assert r.status_code == 405 and r.headers["allow"] == "POST", method
    assert c.get(E.PATH, headers=MCP_HEADERS).status_code == 401           # no token: still 401 first
    assert post(c, INIT).status_code == 200                              # and the endpoint is not left held by the refused calls


def test_an_oversized_request_is_refused(cfg, tmp_path, monkeypatch):
    app = make_app(cfg, tmp_path, monkeypatch)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    big = dict(INIT, params=dict(INIT["params"], clientInfo={"name": "x" * (E.MAX_REQUEST_BYTES + 10), "version": "1"}))
    assert post(c, big).status_code == 413


def test_raw_json_rpc_lists_only_brief_and_answers_without_compression(cfg, tmp_path, monkeypatch):
    app = make_app(cfg, tmp_path, monkeypatch)
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    r = post(c, INIT, **{"Accept-Encoding": "identity"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert "content-encoding" not in r.headers and r.headers["cache-control"] == "no-store"
    r = post(c, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert [t["name"] for t in r.json()["result"]["tools"]] == ["brief"]
    r = post(c, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "transactions_search", "arguments": {}}})
    assert r.json()["result"]["isError"] is True
    r = post(c, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "brief", "arguments": {"schema_version": 0}}})
    assert r.json()["result"]["isError"] is True
    r = post(c, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "brief", "arguments": {"schema_version": 1}}})
    res = r.json()["result"]
    assert res["isError"] is False and B.validate(res["structuredContent"]) == []
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
    assert len(r.content) < 64 * 1024
    for s in SENTINELS + ["TOYLAND", "ZQX", "ACME"]:
        assert s.lower() not in r.text.lower(), s


def test_the_mcp_client_horae_uses_gets_a_valid_brief_and_nothing_leaks_to_the_logs(cfg, tmp_path, monkeypatch, caplog):
    """The plugin's own transport: the `mcp` client over streamable HTTP, `Accept-Encoding: identity`, the bearer header, no redirect."""
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client
    app = make_app(cfg, tmp_path, monkeypatch)

    async def main(token):
        headers = {"Accept-Encoding": "identity", "Authorization": f"Bearer {token}"}
        async with (httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers, follow_redirects=False) as http,
                    Client(streamable_http_client(f"https://{HOST}/mcp", http_client=http), cache=None) as client):
            tools = await client.list_tools()
            result = await client.call_tool("brief", {"schema_version": 1})
            return [t.name for t in tools.tools], result

    with caplog.at_level(logging.DEBUG):
        names, result = anyio.run(main, TOKEN)
    assert names == ["brief"] and not result.is_error
    doc = result.structured_content
    assert B.validate(doc) == [] and doc["headline"].startswith("October so far: EUR 130 spent")
    assert all(t["link"].startswith(f"https://{HOST}/") for t in doc["tasks"])
    assert TOKEN not in caplog.text and "ACME" not in caplog.text and doc["headline"] not in caplog.text
    with pytest.raises(BaseException):
        anyio.run(main, "x" * 40)                                        # the client fails on the 401


def test_the_answer_is_journaled_by_size_only(cfg, tmp_path, monkeypatch):
    from coach import egress
    seen = []
    monkeypatch.setattr(egress, "allow", lambda kind, meta=None, cfg=None: seen.append((kind, sorted(meta))))
    app = make_app(cfg, tmp_path, monkeypatch)
    doc = app.state.brief.answer()
    assert B.validate(doc) == [] and seen == [("horae.brief", ["bytes", "host", "purpose"])]

    def deny(kind, meta=None, cfg=None):
        raise egress.EgressDenied(kind, "local_only", "refused")
    monkeypatch.setattr(egress, "allow", deny)
    assert app.state.brief.answer() is None
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    r = post(c, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "brief", "arguments": {}}})
    assert r.json()["result"]["isError"] is True and "privacy" in r.text


def test_a_failing_answer_is_a_tool_error_without_its_text(cfg, tmp_path, monkeypatch):
    app = make_app(cfg, tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("detail FR7630006000011234567890189")
    app.state.brief.answer = boom
    c = make_client(app, base_url=f"https://{HOST}", logged_in=False)
    r = post(c, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "brief", "arguments": {"schema_version": 1}}})
    assert r.json()["result"]["isError"] is True and "FR76" not in r.text and "RuntimeError" not in r.text


def test_the_security_audit_says_whether_the_endpoint_is_on(cfg, monkeypatch):
    from coach import security
    by_id = lambda: {c.id: c for c in security.audit(cfg, lsof=lambda: None, do_scan=False)}   # noqa: E731
    assert by_id()["horae_brief"].status == "ok" and "off (no token)" in by_id()["horae_brief"].title
    monkeypatch.setenv("COACH_HORAE_BRIEF_TOKEN", TOKEN)
    check = by_id()["horae_brief"]
    assert check.status == "info" and TOKEN not in json.dumps(check.as_dict())
