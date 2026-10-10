"""Security model of the web app: the one-time login link, the session cookie, CSRF, Host / Origin checks, headers on every
response, rate limiting, the read-only database, and "a rejected request changes nothing" across ALL mutating endpoints."""
from __future__ import annotations

import hashlib
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from apihelpers import CALLS, HOST, PORT, TODAY, Ctx, api, ctx, login, make_client, static_dir, world  # noqa: F401
from coach.api import security as sec
from coach.api.app import PUBLIC_PATHS, create_app
from coach.api.server import check_bind, login_url
from coach.cli import main
from coach.config import ConfigError, load_config


def build(world, tmp_path, **kw):
    kw.setdefault("inline_jobs", True)
    app = create_app(world, insecure=True, port=kw.pop("port", PORT), static_dir=static_dir(tmp_path), **kw)
    app.state.coach.clock = lambda: TODAY
    return app


# ====================================================================== the one-time login link

def test_plain_page_loads_never_issue_a_cookie(world, tmp_path):
    c = TestClient(build(world, tmp_path), base_url=HOST)
    for path in ("/", "/login", "/transactions", "/categories/food.groceries"):
        for h in ({}, {"Sec-Fetch-Site": "none"}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Mode": "navigate"}):
            r = c.get(path, headers=h)
            assert r.status_code == 200 and "set-cookie" not in r.headers, (path, h)
    r = c.get(api("/session"))
    assert r.status_code == 401 and "coach ui --login-link" in r.json()["error"]["message"]


def test_exchange_gives_the_cookie_once(world, tmp_path):
    app = build(world, tmp_path)
    c = TestClient(app, base_url=HOST)
    token = app.state.security.tokens.issue()
    assert token not in (world.data_dir / sec.LOGIN_FILE).read_text()                       # only a hash is stored
    r = c.post(api("/session/exchange"), json={"token": token})
    assert r.status_code == 200 and r.json() == {"ok": True}
    ck = r.headers["set-cookie"].lower()
    assert f"coach_session_{PORT}=" in ck and "httponly" in ck and "samesite=strict" in ck and "max-age=43200" in ck
    assert "secure" not in ck.replace("samesite", "")
    assert c.get(api("/session")).status_code == 200
    again = TestClient(app, base_url=HOST).post(api("/session/exchange"), json={"token": token})
    assert again.status_code == 401 and again.json()["error"]["code"] == "invalid_login_token"     # single use


def test_exchange_refuses_wrong_expired_and_malformed_tokens(world, tmp_path):
    app = build(world, tmp_path)
    c = TestClient(app, base_url=HOST)
    for body in ({"token": "nope"}, {"token": ""}, {"token": "x" * 500}, {}, {"token": 5}):
        assert c.post(api("/session/exchange"), json=body).status_code in (401, 422), body
    expired = app.state.security.tokens.issue(ttl=-1)
    assert c.post(api("/session/exchange"), json={"token": expired}).status_code == 401
    assert "set-cookie" not in c.post(api("/session/exchange"), json={"token": "nope"}).headers


def test_exchange_is_rate_limited(world, tmp_path):
    app = build(world, tmp_path)
    c = TestClient(app, base_url=HOST)
    codes = [c.post(api("/session/exchange"), json={"token": f"guess{i}"}).status_code for i in range(12)]
    assert codes[:8] == [401] * 8 and 429 in codes[8:]
    good = app.state.security.tokens.issue()
    assert c.post(api("/session/exchange"), json={"token": good}).status_code == 429          # even a good one waits


def test_exchange_origin_site_and_content_type_checks(world, tmp_path):
    app = build(world, tmp_path)
    c = TestClient(app, base_url=HOST)
    t = app.state.security.tokens.issue()
    assert c.post(api("/session/exchange"), json={"token": t}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(api("/session/exchange"), json={"token": t}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post(api("/session/exchange"), content=f"token={t}", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    assert c.post(api("/session/exchange"), json={"token": t}, headers={"Origin": HOST}).status_code == 200   # the token survived the refusals


def test_login_link_command_prints_a_working_one_time_link(world, tmp_path, capsys):
    app = build(world, tmp_path)
    main(["--config", str(world.config_path), "ui", "--login-link", "--port", str(PORT)])
    out = capsys.readouterr().out.strip()
    assert out.startswith(f"http://127.0.0.1:{PORT}/login#t=") and "?" not in out
    token = out.split("#t=")[1]
    c = TestClient(app, base_url=HOST)
    assert c.post(api("/session/exchange"), json={"token": token}).status_code == 200          # the running server accepts it
    assert c.post(api("/session/exchange"), json={"token": token}).status_code == 401


def test_dev_mode_has_no_direct_minting(world, tmp_path):
    app = build(world, tmp_path, dev=True)
    c = TestClient(app, base_url="http://localhost:5173")
    r = c.get(api("/session"))
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert c.get("/", headers={"Sec-Fetch-Site": "none"}).headers.get("set-cookie") is None
    t = app.state.security.tokens.issue()
    assert c.post(api("/session/exchange"), json={"token": t}, headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert c.get(api("/session")).status_code == 200
    assert login_url(world, PORT, "127.0.0.1", True, "T") == "http://localhost:5173/login#t=T"
    prod = TestClient(build(world, tmp_path), base_url="http://localhost:5173")
    assert prod.get(api("/session")).status_code == 421


# ====================================================================== the cookie

def test_cookie_expires_and_cannot_be_forged(world, tmp_path):
    s = sec.Security(b"k" * 64, PORT, session_hours=1)
    ck = s.new_cookie(now=1000)
    assert s.cookie_valid(ck, now=1000 + 3599) and not s.cookie_valid(ck, now=1000 + 3601)
    sid, iat, sig = ck.split(".")
    for bad in (f"{sid}.{int(iat) + 5000}.{sig}", f"x{sid}.{iat}.{sig}", f"{sid}.{iat}.{sig[:-1]}{'1' if sig[-1] == '0' else '0'}", f"{sid}.{iat}", "", None, "a.b.c"):
        assert not s.cookie_valid(bad, now=1000), bad
    assert not s.cookie_valid(s.new_cookie(now=10 ** 10), now=1000)                          # issued "in the future"
    assert not sec.Security(b"other" * 20, PORT).cookie_valid(ck, now=1000)                  # another key


def test_session_hours_is_configurable(world, tmp_path):
    world.ui_session_hours = 2
    app = build(world, tmp_path)
    assert app.state.security.max_age == 7200
    c = make_client(app)
    c_ck = [x for x in c.cookies.jar][0]
    assert c_ck.name == f"coach_session_{PORT}"


def test_cookie_name_is_per_port_and_other_ports_cookies_are_ignored(world, tmp_path):
    app = build(world, tmp_path)
    c = make_client(app)
    value = [x.value for x in c.cookies.jar][0]
    other = TestClient(app, base_url=HOST)
    other.cookies.set("coach_session_9999", value, domain="127.0.0.1")
    other.cookies.set("coach_session", value, domain="127.0.0.1")
    assert other.get(api("/session")).status_code == 401


def test_logout_revokes_the_session_server_side(world, tmp_path):
    app = build(world, tmp_path)
    c = make_client(app)
    value = [x.value for x in c.cookies.jar][0]
    csrf = c.get(api("/session")).json()["csrf_token"]
    assert c.post(api("/session/logout"), json={}).status_code == 403                           # needs CSRF too
    assert c.post(api("/session/logout"), json={}, headers={"X-CSRF-Token": csrf}).json() == {"ok": True}
    replay = TestClient(app, base_url=HOST)
    replay.cookies.set(f"coach_session_{PORT}", value, domain="127.0.0.1")
    assert replay.get(api("/session")).status_code == 401                                       # the copied cookie is dead
    assert (world.data_dir / sec.REVOKED_FILE).exists()
    restarted = build(world, tmp_path)                                                           # and stays dead after a restart
    again = TestClient(restarted, base_url=HOST)
    again.cookies.set(f"coach_session_{PORT}", value, domain="127.0.0.1")
    assert again.get(api("/session")).status_code == 401


def test_rotating_the_key_signs_everyone_out(world, tmp_path, capsys):
    app = build(world, tmp_path)
    c = make_client(app)
    assert c.get(api("/session")).status_code == 200
    main(["--config", str(world.config_path), "ui", "--rotate-session-key"])
    assert "rotated" in capsys.readouterr().out
    fresh = build(world, tmp_path)                                                               # the server restarts with the new key
    assert TestClient(fresh, base_url=HOST).get(api("/session")).status_code == 401
    old = TestClient(fresh, base_url=HOST)
    old.cookies.set(f"coach_session_{PORT}", [x.value for x in c.cookies.jar][0], domain="127.0.0.1")
    assert old.get(api("/session")).status_code == 401
    key = world.data_dir / sec.KEY_FILE
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_the_key_rotates_by_itself_when_old(world, tmp_path):
    first = sec.load_secret(world.data_dir, 30)
    assert sec.load_secret(world.data_dir, 30) == first
    key = world.data_dir / sec.KEY_FILE
    old = time.time() - 31 * 86400
    os.utime(key, (old, old))
    second = sec.load_secret(world.data_dir, 30)
    assert second != first and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert sec.load_secret(world.data_dir, 30) == second
    assert sec.load_secret(world.data_dir, None) == second


def test_secret_files_are_private(world, tmp_path):
    app = build(world, tmp_path)
    app.state.security.tokens.issue()
    for name in (sec.KEY_FILE, sec.LOGIN_FILE):
        assert stat.S_IMODE((world.data_dir / name).stat().st_mode) == 0o600, name
    assert len((world.data_dir / sec.KEY_FILE).read_bytes().strip()) >= 64


# ====================================================================== remote use

def test_remote_binds_need_the_tls_acknowledgement_and_host_names(world):
    for ok in ("127.0.0.1", "localhost", "::1"):
        check_bind(world, ok)
    with pytest.raises(ConfigError, match="allow_remote"):
        check_bind(world, "0.0.0.0")
    world.ui_allow_remote = True
    for host in ("0.0.0.0", "127.0.0.1"):                       # also behind a local proxy: allow_remote is the switch
        with pytest.raises(ConfigError, match="remote_tls_ack"):
            check_bind(world, host)
    world.ui_remote_tls_ack = True
    with pytest.raises(ConfigError, match="allowed_hosts"):
        check_bind(world, "0.0.0.0")
    world.ui_allowed_hosts = ("mac.tailnet.ts.net",)
    check_bind(world, "0.0.0.0")
    check_bind(world, "127.0.0.1")
    assert login_url(world, PORT, "0.0.0.0", False, "T") == "https://mac.tailnet.ts.net/login#t=T"
    assert login_url(world, PORT, "127.0.0.1", False, "T") == "https://mac.tailnet.ts.net/login#t=T"       # behind the proxy


def test_remote_sessions_are_secure_cookies_behind_https_and_still_need_the_token(world, tmp_path):
    world.ui_allow_remote, world.ui_remote_tls_ack, world.ui_allowed_hosts = True, True, ("mac.tailnet.ts.net",)
    app = build(world, tmp_path)
    c = TestClient(app, base_url="https://mac.tailnet.ts.net")
    assert c.get("/").status_code == 200 and c.get(api("/session")).status_code == 401
    t = app.state.security.tokens.issue()
    r = c.post(api("/session/exchange"), json={"token": t}, headers={"Origin": "https://mac.tailnet.ts.net"})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()
    assert c.get(api("/session")).status_code == 200
    assert TestClient(app, base_url="https://other.example").get("/").status_code == 421


# ====================================================================== Host / CSRF / Origin / content type

def test_host_header_is_checked_against_dns_rebinding(ctx):
    for bad in ("evil.example", "127.0.0.1:9999", "localhost.evil.example:8123", "127.0.0.1"):
        r = ctx.client.get(api("/session"), headers={"Host": bad})
        assert r.status_code == 421 and r.json()["error"]["code"] == "bad_host", bad
    assert ctx.client.get(api("/session"), headers={"Host": f"localhost:{PORT}"}).status_code == 200
    assert ctx.client.get("/", headers={"Host": "evil.example"}).status_code == 421


def test_mutations_need_the_csrf_token(ctx):
    body = {"tx_key": "x"}
    r = ctx.client.post(api("/transactions/override/clear"), json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    assert ctx.client.post(api("/transactions/override/clear"), json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert ctx.post("/transactions/override/clear", body).status_code == 200


def test_csrf_token_is_bound_to_the_session(world, tmp_path):
    app = build(world, tmp_path)
    a, b = make_client(app), make_client(app)
    ta = a.get(api("/session")).json()["csrf_token"]
    r = b.post(api("/transactions/override/clear"), json={"tx_key": "x"}, headers={"X-CSRF-Token": ta})
    assert r.status_code == 403


def test_cross_origin_cross_site_and_non_json_requests_are_refused(ctx):
    h = {"X-CSRF-Token": ctx.csrf}
    p = api("/transactions/override/clear")
    assert ctx.client.post(p, json={"tx_key": "x"}, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert ctx.client.post(p, json={"tx_key": "x"}, headers={**h, "Origin": "null"}).status_code == 403
    assert ctx.client.post(p, json={"tx_key": "x"}, headers={**h, "Origin": HOST}).status_code == 200
    assert ctx.client.get(api("/session"), headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert ctx.client.get(api("/session"), headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert ctx.client.get(api("/session"), headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"):
        r = ctx.client.post(p, content='{"tx_key": "x"}', headers={**h, "Content-Type": ctype})
        assert r.status_code == 415, ctype
    r = ctx.client.post(p, content='{"tx_key": "x"}', headers=h)                                 # a body without a type
    assert r.status_code in (415, 422)


def world_fingerprint(ctx) -> str:
    """Everything a write could touch: every table of the database, every memory file, the UI state."""
    h = hashlib.sha256()
    with ctx.state.read() as con:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
            h.update(repr(con.execute(f'SELECT * FROM "{t}"').fetchall()).encode())
    for root, _, files in os.walk(ctx.cfg.memory_dir):
        if ".history.git" in root:
            continue
        for f in sorted(files):
            h.update((root + f).encode() + open(os.path.join(root, f), "rb").read())
    st = ctx.cfg.data_dir / "ui-state.json"
    h.update(st.read_bytes() if st.exists() else b"")
    return h.hexdigest()


def mutating_routes(app):
    out = []
    for path, ops in app.openapi()["paths"].items():
        for m in ops:
            if m.upper() in sec.MUTATING and path != "/api/v1/session/exchange":
                out.append((m.upper(), path))
    return sorted(out)


BODIES = {  # plausible valid bodies, so that a refused request would have written if it had got through
    "/api/v1/transactions/category": {"tx_key": "fm0", "category": "food.restaurants", "scope": "transaction"},
    "/api/v1/transactions/override/clear": {"tx_key": "fm0"},
    "/api/v1/annotations": {"tx_keys": ["fm0"], "tags": ["one_off"]},
    "/api/v1/budgets": {"target": "food.groceries", "monthly": "300"},
    "/api/v1/goals": {"id": "g1", "target": "100", "tag": "savings"},
    "/api/v1/review/correct": {"key": "FRESH MARKET", "category": "food.restaurants"},
    "/api/v1/review/confirm": {"key": "FRESH MARKET"},
    "/api/v1/questions/{qid}/answer": {"text": "x"},
    "/api/v1/transfers/link": {"out_tx": "tr_out", "in_tx": "tr_in"},
    "/api/v1/transfers/unlink": {"ref": "tr_out"},
    "/api/v1/transactions/split": {"tx_key": "fm0", "parts": [{"category": "food.groceries", "amount": "10"}, {"category": "food.restaurants", "amount": "rest"}]},
    "/api/v1/memory/{kind}/{item_id}": {"fields": {"name": "Zed", "role": "child"}},
    "/api/v1/accounts/{uid}": {"label": "Hacked"},
}


def test_a_rejected_mutation_changes_nothing_on_every_endpoint(ctx):
    """CSRF missing, wrong Origin, cross-site, wrong content type, and no session: on ALL mutating endpoints the answer is a
    refusal and the database, the memory and the UI state are byte-identical afterwards."""
    routes = mutating_routes(ctx.app)
    assert len(routes) >= 30
    before = world_fingerprint(ctx)
    anon = TestClient(ctx.app, base_url=HOST)
    for method, path in routes:
        if (method, path) in PUBLIC_PATHS:            # E16: the calls that CREATE a session have their own refusal tests (test_api_passkeys)
            continue
        url = path.replace("{kind}", "members").replace("{item_id}", "zed").replace("{qid}", "q-001").replace("{uid}", "fo") \
            .replace("{pid}", "p-20260101-abcdef").replace("{budget_id}", "x").replace("{annotation_id}", "x") \
            .replace("{anomaly_id}", "x").replace("{change_id}", "x").replace("{insight_id}", "x") \
            .replace("{alert_id}", "alr_x").replace("{name}", "ntfy")
        body = BODIES.get(path, {})
        good = {"X-CSRF-Token": ctx.csrf}
        cases = {
            "no csrf": dict(headers={}, code=403),
            "wrong csrf": dict(headers={"X-CSRF-Token": "0" * 64}, code=403),
            "evil origin": dict(headers={**good, "Origin": "https://evil.example"}, code=403),
            "cross site": dict(headers={**good, "Sec-Fetch-Site": "cross-site"}, code=403),
            "form type": dict(headers={**good, "Content-Type": "application/x-www-form-urlencoded"}, code=415, raw=True),
            "text type": dict(headers={**good, "Content-Type": "text/plain"}, code=415, raw=True),
        }
        for name, c in cases.items():
            kw = {"content": "a=b"} if c.get("raw") else {"json": body}
            r = ctx.client.request(method, url, headers=c["headers"], **kw)
            assert r.status_code == c["code"], (method, path, name, r.status_code, r.text[:120])
        r = anon.request(method, url, json=body)
        assert r.status_code == 401, (method, path, "no session")
    assert world_fingerprint(ctx) == before, "a refused request changed something"


def test_mutations_are_rate_limited_per_session(ctx):
    codes = [ctx.post("/transactions/override/clear", {"tx_key": "x"}).status_code for _ in range(45)]
    assert codes[:30] == [200] * 30 and 429 in codes[30:]
    r = ctx.post("/transactions/override/clear", {"tx_key": "x"})
    assert r.status_code == 429 and r.headers["retry-after"] and r.json()["error"]["code"] == "rate_limited"
    assert ctx.get("/accounts/balances").status_code == 200                                      # reads are not limited
    other = make_client(ctx.app)                                                                 # another session has its own budget
    csrf = other.get(api("/session")).json()["csrf_token"]
    assert other.post(api("/transactions/override/clear"), json={"tx_key": "x"}, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_previews_have_their_own_larger_budget(ctx):
    body = {"target": "food.groceries", "monthly": "300"}
    codes = [ctx.post("/budgets", body, dry_run=True).status_code for _ in range(35)]
    assert codes == [200] * 35


# ====================================================================== headers on every response

def assert_hardened(r, api_path=True):
    h = r.headers
    csp = h["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0] and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer" and h["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in h
    if api_path:
        assert h["cache-control"] == "no-store"


def test_security_headers_are_on_every_response_including_errors(ctx, monkeypatch):
    anon = TestClient(ctx.app, base_url=HOST)
    cases = {
        "ok": ctx.client.get(api("/accounts/balances")),
        "401": anon.get(api("/session")),
        "421": ctx.client.get(api("/session"), headers={"Host": "evil.example"}),
        "403 csrf": ctx.client.post(api("/budgets"), json={}),
        "403 site": ctx.client.get(api("/session"), headers={"Sec-Fetch-Site": "cross-site"}),
        "415": ctx.client.post(api("/budgets"), content="x", headers={"X-CSRF-Token": ctx.csrf, "Content-Type": "text/plain"}),
        "404": ctx.client.get(api("/nope")),
        "405": ctx.client.delete(api("/accounts/balances"), headers={"X-CSRF-Token": ctx.csrf}),
        "422": ctx.client.get(api("/transactions"), params={"limit": 0}),
        "429 login": TestClient(ctx.app, base_url=HOST).post(api("/session/exchange"), json={"token": "z"}),
    }
    for name, r in cases.items():
        assert_hardened(r)
    assert cases["421"].status_code == 421 and cases["415"].status_code == 415 and cases["405"].status_code in (404, 405)
    # an unexpected exception: the 500 handler runs outside the middleware and must still carry the headers
    quiet = TestClient(ctx.app, base_url=HOST, raise_server_exceptions=False)
    quiet.cookies.update(ctx.client.cookies)
    monkeypatch.setattr(type(ctx.state), "snapshot", lambda self: (_ for _ in ()).throw(RuntimeError("boom with /secret/path")))
    r = quiet.get(api("/accounts/balances"))
    assert r.status_code == 500 and "boom" not in r.text and "/secret/path" not in r.text
    assert_hardened(r)


def test_no_cors(ctx):
    r = ctx.client.options(api("/session"), headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers and r.status_code in (400, 403, 405)


def test_api_reference_is_local_and_cookie_protected(ctx):
    r = ctx.client.get("/api/docs")
    assert r.status_code == 200 and "/api/v1/transactions" in r.text and "Agents must not call this API" in r.text
    assert "http://" not in r.text.replace("http://127.0.0.1", "") and "cdn" not in r.text.lower()
    assert "<script" not in r.text.lower() and "javascript:" not in r.text.lower()
    assert "'unsafe-inline'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    spec = ctx.client.get("/api/openapi.json").json()
    assert "/api/v1/transactions" in spec["paths"]
    assert TestClient(ctx.app, base_url=HOST).get("/api/docs").status_code == 401
    assert TestClient(ctx.app, base_url=HOST).get("/api/openapi.json").status_code == 401


def test_static_serving_spa_fallback_traversal_and_service_worker_headers(ctx):
    assert "coach" in ctx.client.get("/categories/food.groceries").text
    r = ctx.client.get("/assets/app.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert ctx.client.get("/assets/missing.js").status_code == 404
    sw = ctx.client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["content-type"].startswith("text/javascript")
    assert sw.headers["service-worker-allowed"] == "/" and sw.headers["cache-control"] == "no-cache"
    assert "root:" not in ctx.client.get("/..%2f..%2f..%2fetc/passwd").text
    assert ctx.client.get("/%2e%2e/%2e%2e/etc/passwd").status_code in (200, 404)
    assert ctx.get("/nope").json()["error"]["code"] == "not_found"
    assert_hardened(ctx.client.get("/"), api_path=False)


# ====================================================================== the database cannot be written from the read path

def test_database_read_path_is_denied_writes_pragmas_and_attach(ctx):
    import sqlcipher3
    with ctx.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 0
        for sql in ("DELETE FROM tx_overrides", "INSERT INTO tx_overrides VALUES ('a','b',NULL)", "UPDATE accounts SET label='x'",
                    "CREATE TABLE evil(x)", "DROP TABLE tx_overrides", "ALTER TABLE accounts ADD COLUMN z", "PRAGMA query_only=OFF",
                    "PRAGMA journal_mode=DELETE", "PRAGMA writable_schema=1", "ATTACH DATABASE ':memory:' AS m", "VACUUM"):
            with pytest.raises(sqlcipher3.Error):
                con.execute(sql)
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
    with ctx.state.write() as con:
        con.execute("INSERT INTO tx_overrides VALUES ('zz','food.groceries',NULL)")
        con.commit()
    with ctx.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM tx_overrides").fetchone()[0] == 1
        with pytest.raises(sqlcipher3.Error):
            con.execute("DELETE FROM tx_overrides")


def test_a_failed_write_rolls_back_and_the_door_closes(ctx):
    import sqlcipher3
    with pytest.raises(RuntimeError):
        with ctx.state.write() as con:
            con.execute("INSERT INTO tx_overrides VALUES ('zz','food.groceries',NULL)")
            raise RuntimeError("boom")
    with ctx.state.read() as con:
        assert con.execute("SELECT COUNT(*) FROM tx_overrides").fetchone()[0] == 0
        with pytest.raises(sqlcipher3.Error):
            con.execute("DELETE FROM tx_overrides")


def test_error_model(ctx):
    r = ctx.get("/transactions", limit=0)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]
    r = ctx.get("/categories/not.a.category")
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message", "details"}
    assert ctx.get("/transactions/detail", tx_key="nope").status_code == 404


def test_ui_config_section(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[ui]\nport = 9000\nallow_remote = true\nallowed_hosts = ["Mac.TS.net"]\nopen_browser = false\n'
                 'session_hours = 6\nkey_rotation_days = 7\nremote_tls_ack = true\n')
    c = load_config(p, env={})
    assert (c.ui_port, c.ui_allow_remote, c.ui_allowed_hosts, c.ui_open_browser, c.ui_host) == (9000, True, ("mac.ts.net",), False, "127.0.0.1")
    assert (c.ui_session_hours, c.ui_key_rotation_days, c.ui_remote_tls_ack) == (6, 7, True)
    for bad in ('allow_remote = "yes"', "port = 0", "session_hours = 0", "session_hours = 5000", "key_rotation_days = 0", 'remote_tls_ack = "x"'):
        p.write_text(f"[ui]\n{bad}\n")
        with pytest.raises(ConfigError):
            load_config(p, env={})
    p.write_text("")
    d = load_config(p, env={})
    assert (d.ui_session_hours, d.ui_key_rotation_days, d.ui_remote_tls_ack, d.ui_allow_remote) == (12, 30, False, False)


def test_ui_command_is_registered():
    from coach.cli import build_parser
    a = build_parser().parse_args(["ui", "--port", "9001", "--no-browser", "--dev", "--login-link"])
    assert (a.port, a.no_browser, a.dev, a.host, a.login_link, a.rotate_session_key) == (9001, True, True, None, True, False)
