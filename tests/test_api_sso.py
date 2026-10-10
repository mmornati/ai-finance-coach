"""E16: sign-in through an identity-aware proxy (authentik forward-auth). The proxy's SIGNED token opens a session; a plain header never
does; the token is verified against the provider's keys fetched from the app's OWN configuration, through the egress gate."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from apihelpers import PORT, TODAY, api, static_dir, world  # noqa: F401
from coach.api.app import create_app
from coach.config import ConfigError, load_config

HOST = "https://coach.example.test"
ISS = "https://sso.example.test/application/o/coach/"
AUD = "client-abc"
JWKS_URL = "http://authentik:9000/application/o/coach/jwks/"
SECTION = f'''
[ui]
allow_remote = true
remote_tls_ack = true
allowed_hosts = ["coach.example.test"]
sso = "authentik"
sso_jwks_url = "{JWKS_URL}"
sso_issuer = "{ISS}"
sso_audience = "{AUD}"
'''


@pytest.fixture(scope="module")
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048), rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_of(priv, kid="k1") -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def token(priv, kid="k1", alg="RS256", **over) -> str:
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "exp": now + 300, "iat": now, "sub": "uid-1", "preferred_username": "marco", "email": "marco@example.test"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, priv, algorithm=alg, headers={"kid": kid})


def sso_cfg(world, users: str = 'marco = "owner"', section: str = SECTION, extra: str = ""):
    world.config_path.write_text(world.config_path.read_text() + section + extra + "\n[ui.sso_users]\n" + users + "\n")
    return load_config(world.config_path, env={})


class Fetch:
    def __init__(self, priv, kid="k1"):
        self.priv, self.kid, self.calls = priv, kid, 0

    def __call__(self):
        self.calls += 1
        return jwks_of(self.priv, self.kid)


def build(cfg, tmp_path, fetch):
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    app.state.sso._fetch = fetch
    return app


def add_login(app, login: str, role: str = "adult", member=None, disabled=None):
    with app.state.coach.write() as con:
        con.execute("INSERT INTO ui_users(id, member_id, role, created_at, disabled_at, prefs) VALUES (?,?,?,?,?,'{}')",
                    (login, member, role, "2026-01-01T00:00:00+00:00", disabled))
        con.commit()


# ====================================================================== the happy path

def test_a_valid_signed_token_opens_the_owner_session_once(world, tmp_path, keys):
    priv, _ = keys
    fetch = Fetch(priv)
    app = build(sso_cfg(world), tmp_path, fetch)
    c = TestClient(app, base_url=HOST)
    r = c.get(api("/session"), headers={"X-authentik-jwt": token(priv)})
    assert r.status_code == 200, r.text
    ck = r.headers["set-cookie"].lower()
    assert f"coach_session_{PORT}=" in ck and "httponly" in ck and "secure" in ck.replace("samesite", "")
    body = r.json()
    assert body["user"]["id"] == "owner" and body["auth"] == {"passkeys": False, "sso": True, "sso_sign_out": "/outpost.goauthentik.io/sign_out"}
    assert body["csrf_token"] == app.state.security.csrf_token(c.cookies[f"coach_session_{PORT}"])   # the token matches the cookie just issued
    # the cookie now carries the session: no header needed, and no new cookie is issued
    r2 = c.get(api("/session"))
    assert r2.status_code == 200 and "set-cookie" not in r2.headers
    assert fetch.calls == 1
    # writes work with the CSRF token of that session
    r3 = c.post(api("/session/logout"), json={}, headers={"X-CSRF-Token": body["csrf_token"]})
    assert r3.status_code == 200


def test_the_token_can_map_to_a_person_login_and_its_role_applies(world, tmp_path, keys):
    priv, _ = keys
    app = build(sso_cfg(world, users='marco = "owner"\n"kid@example.test" = "mia"\nnoa = "papa"'), tmp_path, Fetch(priv))
    add_login(app, "papa", "adult")
    add_login(app, "mia", "child", member="mia")
    papa = TestClient(app, base_url=HOST)
    r = papa.get(api("/session"), headers={"X-authentik-jwt": token(priv, preferred_username="noa", email="noa@example.test")})
    assert r.status_code == 200 and r.json()["user"]["id"] == "papa"
    assert papa.get(api("/health")).status_code == 200
    kid = TestClient(app, base_url=HOST)
    r = kid.get(api("/session"), headers={"X-authentik-jwt": token(priv, preferred_username="kiddo", email="kid@example.test")})
    assert r.status_code == 200 and r.json()["user"]["role"] == "child"
    assert kid.get(api("/health")).status_code == 403                                   # the child scope, as for any child session
    assert kid.get(api("/health"), headers={"X-authentik-jwt": token(priv, preferred_username="marco")}).status_code == 403   # the cookie wins


# ====================================================================== what is refused

def test_a_plain_header_or_no_token_never_opens_a_session(world, tmp_path, keys):
    priv, _ = keys
    app = build(sso_cfg(world), tmp_path, Fetch(priv))
    c = TestClient(app, base_url=HOST)
    r = c.get(api("/session"))
    assert r.status_code == 401 and r.json()["error"]["code"] == "sso_missing" and "set-cookie" not in r.headers
    r = c.get(api("/session"), headers={"X-authentik-username": "marco", "X-authentik-email": "marco@example.test"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "sso_missing"
    # a page load never gets a cookie, token or not
    r = c.get("/", headers={"X-authentik-jwt": token(priv)})
    assert r.status_code == 200 and "set-cookie" not in r.headers


@pytest.mark.parametrize("case, reason", [
    ("other_key", "invalid"), ("expired", "expired"), ("issuer", "issuer"), ("audience", "audience"), ("alg", "algorithm"), ("garbage", "invalid"),
    ("unknown_kid", "invalid"),
])
def test_a_bad_token_is_refused_with_its_reason_and_no_cookie(world, tmp_path, keys, case, reason):
    priv, other = keys
    fetch = Fetch(priv)
    app = build(sso_cfg(world), tmp_path, fetch)
    c = TestClient(app, base_url=HOST)
    if case == "other_key":
        t = token(other)
    elif case == "expired":
        t = token(priv, exp=int(time.time()) - 100)
    elif case == "issuer":
        t = token(priv, iss="https://evil.example.test/application/o/coach/")
    elif case == "audience":
        t = token(priv, aud="other-client")
    elif case == "alg":
        t = jwt.encode({"preferred_username": "marco", "exp": int(time.time()) + 300}, "secret", algorithm="HS256", headers={"kid": "k1"})
    elif case == "unknown_kid":
        t = token(priv, kid="k9")
        app.state.sso.identity(token(priv))           # the keys were fetched a while ago
        app.state.sso._fetched -= 120
    else:
        t = "not.a.token"
    r = c.get(api("/session"), headers={"X-authentik-jwt": t})
    assert r.status_code == 401 and r.json()["error"]["code"] == "sso_rejected" and "set-cookie" not in r.headers, r.text
    assert r.json()["error"]["details"] == {"reason": reason}
    if case == "unknown_kid":
        assert fetch.calls == 2                       # a key id the keys do not hold: fetched again, once (rate limited after that)
        app.state.sso.identity(t)
        assert fetch.calls == 2


def test_a_valid_token_for_an_unmapped_person_says_which_name_to_map(world, tmp_path, keys):
    priv, _ = keys
    app = build(sso_cfg(world), tmp_path, Fetch(priv))
    c = TestClient(app, base_url=HOST)
    r = c.get(api("/session"), headers={"X-authentik-jwt": token(priv, preferred_username="zoe", email="zoe@example.test")})
    assert r.status_code == 401 and r.json()["error"]["code"] == "sso_unmapped" and r.json()["error"]["details"] == {"identity": "zoe"}
    assert "set-cookie" not in r.headers


def test_a_mapping_to_a_missing_or_disabled_login_is_refused(world, tmp_path, keys):
    priv, _ = keys
    app = build(sso_cfg(world, users='marco = "ghost"\nnoa = "papa"'), tmp_path, Fetch(priv))
    add_login(app, "papa", "adult", disabled="2026-02-01T00:00:00+00:00")
    c = TestClient(app, base_url=HOST)
    r = c.get(api("/session"), headers={"X-authentik-jwt": token(priv)})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "login"}
    r = c.get(api("/session"), headers={"X-authentik-jwt": token(priv, preferred_username="noa")})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "login"}


def test_the_keys_are_rotated_when_the_token_names_a_new_key_id(world, tmp_path, keys):
    priv, other = keys
    fetch = Fetch(priv)
    app = build(sso_cfg(world), tmp_path, fetch)
    c = TestClient(app, base_url=HOST)
    assert c.get(api("/session"), headers={"X-authentik-jwt": token(priv)}).status_code == 200
    fetch.priv, fetch.kid = other, "k2"                                 # the provider rotated its key
    app.state.sso._fetched -= 120                                       # and the minute between two fetches is over
    assert TestClient(app, base_url=HOST).get(api("/session"), headers={"X-authentik-jwt": token(other, kid="k2")}).status_code == 200
    assert fetch.calls == 2


def test_the_one_time_link_keeps_working_next_to_sso(world, tmp_path, keys):
    priv, _ = keys
    app = build(sso_cfg(world), tmp_path, Fetch(priv))
    c = TestClient(app, base_url=HOST)
    t = app.state.security.tokens.issue()
    assert c.post(api("/session/exchange"), json={"token": t}).status_code == 200
    assert c.get(api("/session")).status_code == 200
    assert c.get(api("/session/methods")).json() == {"passkeys": False, "sso": True, "sso_sign_out": "/outpost.goauthentik.io/sign_out"}


def test_the_jwks_fetch_goes_through_the_egress_gate(world, tmp_path, keys):
    """[privacy] offline refuses the fetch: the token is refused (reason jwks), nothing is fetched, the link still works."""
    priv, _ = keys
    cfg = sso_cfg(world, extra="\n[privacy]\noffline = true\n")
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise AssertionError("requests.get must not run under offline")
    import coach.api.sso as sso_mod
    orig = sso_mod.requests.get
    sso_mod.requests.get = boom
    try:
        r = TestClient(app, base_url=HOST).get(api("/session"), headers={"X-authentik-jwt": token(priv)})
    finally:
        sso_mod.requests.get = orig
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "jwks"} and not calls


def test_without_sso_the_header_is_ignored(world, tmp_path, keys):
    priv, _ = keys
    app = create_app(world, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    assert app.state.sso is None
    r = TestClient(app, base_url=f"http://127.0.0.1:{PORT}").get(api("/session"), headers={"X-authentik-jwt": token(priv)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized" and "set-cookie" not in r.headers


# ====================================================================== configuration

@pytest.mark.parametrize("section, users, message", [
    ('\n[ui]\nsso = "authentik"\nsso_jwks_url = "http://authentik:9000/application/o/coach/jwks/"\n', 'marco = "owner"', "allow_remote"),
    (SECTION.replace(f'sso_jwks_url = "{JWKS_URL}"', 'sso_jwks_url = "authentik:9000"'), 'marco = "owner"', "sso_jwks_url"),
    (SECTION, "", "sso_users"),
    (SECTION, 'marco = "Owner"', "login id"),
    (SECTION, 'marco = 42', "login id"),
    (SECTION.replace('sso = "authentik"', 'sso = "okta"'), 'marco = "owner"', "ui.sso must be"),
])
def test_sso_configuration_is_checked(world, section, users, message):
    with pytest.raises(ConfigError, match=message):
        sso_cfg(world, users=users, section=section)


def test_sso_settings_are_listed_without_the_identities(world):
    from coach.config import effective
    cfg = sso_cfg(world, users='marco = "owner"\nnoa = "papa"')
    rows = {k: v for k, v, _ in effective(cfg)}
    assert rows["ui.sso"] == "authentik" and rows["ui.sso_users"] == "2 mapped" and "marco" not in str(rows)
    assert rows["ui.sso_jwks_url"] == JWKS_URL and rows["ui.sso_issuer"] == ISS
