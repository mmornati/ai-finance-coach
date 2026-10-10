"""E16: passkeys (WebAuthn). A software authenticator plays the browser's part: a real key pair, real attestation and assertion bytes,
verified by the server library, so a wrong origin, a replayed challenge, a stale counter or another login's credential are refused."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from apihelpers import PORT, TODAY, RecClient, api, static_dir, world  # noqa: F401
from coach.api import passkeys as pk
from coach.api.app import create_app
from coach.config import load_config

HOST = f"http://localhost:{PORT}"                     # a passkey needs a host NAME: 127.0.0.1 cannot be a relying party


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class SoftKey:
    """A platform authenticator in software (ES256), bound to one relying party and origin."""

    def __init__(self, rp_id: str = "localhost", origin: str = HOST):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.count = 0
        self.rp_id, self.origin = rp_id, origin

    @property
    def id(self) -> str:
        return b64u(self.cred_id)

    def _cose(self) -> bytes:
        n = self.priv.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _client_data(self, typ: str, challenge: str) -> bytes:
        return json.dumps({"type": typ, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        cd = self._client_data("webauthn.create", options["challenge"])
        auth = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x01 | 0x04 | 0x40]) + struct.pack(">I", 0) + bytes(16)
                + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose())
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": self.id, "rawId": self.id, "type": "public-key", "clientExtensionResults": {}, "authenticatorAttachment": "platform",
                "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(att), "transports": ["internal"]}}

    def get(self, options: dict, count=None) -> dict:
        self.count = self.count + 1 if count is None else count
        cd = self._client_data("webauthn.get", options["challenge"])
        auth = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x01 | 0x04]) + struct.pack(">I", self.count)
        sig = self.priv.sign(auth + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": self.id, "rawId": self.id, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(auth), "signature": b64u(sig)}}


def pk_cfg(world, enabled: bool = True):
    base = world.config_path.read_text().split("\n[ui]\n")[0]
    world.config_path.write_text(base + f"\n[ui]\npasskeys = {'true' if enabled else 'false'}\n")
    return load_config(world.config_path, env={})


@pytest.fixture
def app(world, tmp_path):
    a = create_app(pk_cfg(world), insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    a.state.coach.clock = lambda: TODAY
    return a


def session(app, user=None) -> tuple[TestClient, str]:
    """A browser signed in with the one-time link (the enrolment path), and its CSRF token."""
    c = RecClient(app, base_url=HOST)
    assert c.post(api("/session/exchange"), json={"token": app.state.security.tokens.issue(user=user)}).status_code == 200
    return c, c.get(api("/session")).json()["csrf_token"]


def enrol(c: TestClient, csrf: str, key: SoftKey, label="my phone") -> dict:
    r = c.post(api("/session/passkeys/options"), json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["options"]["rp"]["id"] == key.rp_id and o["options"]["authenticatorSelection"]["residentKey"] == "preferred"
    r = c.post(api("/session/passkeys"), json={"challenge_id": o["challenge_id"], "credential": key.create(o["options"]), "label": label},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    return r.json()


def sign_in(app, key: SoftKey, count=None) -> TestClient:
    """A fresh browser: 'Sign in with a passkey'."""
    c = RecClient(app, base_url=HOST)
    r = c.post(api("/session/passkey/options"), json={})
    assert r.status_code == 200, r.text
    o = r.json()
    assert "set-cookie" not in r.headers and o["options"]["rpId"] == "localhost"
    c.post(api("/session/passkey/verify"), json={"challenge_id": o["challenge_id"], "credential": key.get(o["options"], count)})
    return c


def add_login(app, login: str, role: str = "adult", member=None, disabled=None):
    with app.state.coach.write() as con:
        con.execute("INSERT INTO ui_users(id, member_id, role, created_at, disabled_at, prefs) VALUES (?,?,?,?,?,'{}')",
                    (login, member, role, "2026-01-01T00:00:00+00:00", disabled))
        con.commit()


# ====================================================================== enrol, then sign in

def test_methods_tell_the_login_page_what_is_possible(app, world, tmp_path):
    c = TestClient(app, base_url=HOST)
    assert c.get(api("/session/methods")).json() == {"passkeys": True, "sso": False, "sso_sign_out": None}
    assert "set-cookie" not in c.get(api("/session/methods")).headers
    off = create_app(pk_cfg(world, enabled=False), insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    assert TestClient(off, base_url=HOST).get(api("/session/methods")).json()["passkeys"] is False


def test_a_passkey_enrolled_in_a_session_opens_the_owner_session_later(app):
    c, csrf = session(app)
    key = SoftKey()
    made = enrol(c, csrf, key)
    assert made == {"id": key.id, "label": "my phone", "rp_id": "localhost"}
    mine = c.get(api("/session/passkeys")).json()
    assert mine["enabled"] and [p["label"] for p in mine["passkeys"]] == ["my phone"] and mine["passkeys"][0]["last_used_at"] is None
    assert "public_key" not in json.dumps(mine)
    fresh = sign_in(app, key)
    r = fresh.get(api("/session"))
    assert r.status_code == 200 and r.json()["user"]["id"] == "owner"                      # the owner login, as the link gives
    assert fresh.get(api("/session/passkeys")).json()["passkeys"][0]["last_used_at"]        # the use is recorded
    assert fresh.cookies.get(f"coach_session_{PORT}")


def test_a_passkey_belongs_to_one_login_and_follows_its_state(app):
    add_login(app, "papa", "adult")
    c, csrf = session(app, user="papa")
    key = SoftKey()
    enrol(c, csrf, key, label="laptop")
    assert sign_in(app, key).get(api("/session")).json()["user"]["id"] == "papa"
    owner, ocsrf = session(app)
    assert owner.get(api("/session/passkeys")).json()["passkeys"] == []                     # not the owner's
    assert owner.post(api("/session/passkeys/delete"), json={"id": key.id}, headers={"X-CSRF-Token": ocsrf}).status_code == 404
    with app.state.coach.write() as con:
        con.execute("UPDATE ui_users SET disabled_at='2026-03-01T00:00:00+00:00' WHERE id='papa'")
        con.commit()
    gone = sign_in(app, key)
    assert gone.get(api("/session")).status_code == 401                                     # a disabled login cannot sign in


def test_a_child_login_enrols_its_own_passkey_and_keeps_its_scope(app):
    add_login(app, "kid", "child", member="mia")
    c, csrf = session(app, user="kid")
    key = SoftKey()
    enrol(c, csrf, key, label="tablet")
    k = sign_in(app, key)
    assert k.get(api("/session")).json()["user"]["role"] == "child" and k.get(api("/health")).status_code == 403


def test_removing_a_passkey_closes_that_way_in(app):
    c, csrf = session(app)
    key, other = SoftKey(), SoftKey()
    enrol(c, csrf, key, "phone")
    enrol(c, csrf, other, "tablet")
    assert len(c.get(api("/session/passkeys")).json()["passkeys"]) == 2
    assert c.post(api("/session/passkeys/delete"), json={"id": key.id}, headers={"X-CSRF-Token": csrf}).json() == {"ok": True}
    assert [p["label"] for p in c.get(api("/session/passkeys")).json()["passkeys"]] == ["tablet"]
    assert sign_in(app, key).get(api("/session")).status_code == 401
    assert sign_in(app, other).get(api("/session")).status_code == 200


# ====================================================================== what is refused

def test_wrong_origin_replay_stale_counter_and_unknown_keys_are_refused(app):
    c, csrf = session(app)
    key = SoftKey()
    enrol(c, csrf, key)
    # a credential made for another site (phishing): the origin and rp id in the signed data do not match
    evil = SoftKey(rp_id="evil.example", origin="https://evil.example")
    evil.priv, evil.cred_id = key.priv, key.cred_id
    assert sign_in(app, evil).get(api("/session")).status_code == 401
    # a never-enrolled key
    assert sign_in(app, SoftKey()).get(api("/session")).status_code == 401
    # a replayed assertion: the challenge is single-use
    fresh = RecClient(app, base_url=HOST)
    o = fresh.post(api("/session/passkey/options"), json={}).json()
    cred = key.get(o["options"])
    assert fresh.post(api("/session/passkey/verify"), json={"challenge_id": o["challenge_id"], "credential": cred}).status_code == 200
    again = RecClient(app, base_url=HOST)
    r = again.post(api("/session/passkey/verify"), json={"challenge_id": o["challenge_id"], "credential": cred})
    assert r.status_code == 401 and r.json()["error"]["code"] == "passkey_rejected" and "set-cookie" not in r.headers
    # a cloned authenticator: the signature counter went backwards
    assert sign_in(app, key, count=1).get(api("/session")).status_code == 401
    assert sign_in(app, key).get(api("/session")).status_code == 200                       # the real one still works
    # a registration challenge cannot be used to sign in, and the other way round
    o = c.post(api("/session/passkeys/options"), json={}, headers={"X-CSRF-Token": csrf}).json()
    r = RecClient(app, base_url=HOST).post(api("/session/passkey/verify"), json={"challenge_id": o["challenge_id"], "credential": key.get(o["options"])})
    assert r.status_code == 401
    o = RecClient(app, base_url=HOST).post(api("/session/passkey/options"), json={}).json()
    r = c.post(api("/session/passkeys"), json={"challenge_id": o["challenge_id"], "credential": key.create(o["options"]), "label": "x"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_challenge"


def test_sign_in_attempts_are_rate_limited(app):
    codes = [sign_in(app, SoftKey()).get(api("/session")).status_code for _ in range(10)]
    assert codes == [401] * 10
    c = RecClient(app, base_url=HOST)
    o = c.post(api("/session/passkey/options"), json={}).json()
    r = c.post(api("/session/passkey/verify"), json={"challenge_id": o["challenge_id"], "credential": SoftKey().get(o["options"])})
    assert r.status_code == 429


def test_passkeys_off_refuses_everything_but_the_listing(world, tmp_path):
    app = create_app(pk_cfg(world, enabled=False), insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    c, csrf = session(app)
    for path in ("/session/passkey/options", "/session/passkeys/options"):
        r = c.post(api(path), json={}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403 and r.json()["error"]["code"] == "passkeys_disabled", path
    assert c.get(api("/session/passkeys")).json() == {"enabled": False, "passkeys": []}


def test_a_loopback_address_cannot_be_a_relying_party(app):
    c = RecClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert c.post(api("/session/exchange"), json={"token": app.state.security.tokens.issue()}).status_code == 200
    csrf = c.get(api("/session")).json()["csrf_token"]
    r = c.post(api("/session/passkeys/options"), json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_host"
    assert RecClient(app, base_url=f"http://127.0.0.1:{PORT}").post(api("/session/passkey/options"), json={}).status_code == 400


def test_the_public_calls_keep_the_origin_site_and_type_checks(app):
    c = TestClient(app, base_url=HOST)
    assert c.post(api("/session/passkey/options"), json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(api("/session/passkey/options"), json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post(api("/session/passkey/options"), content="a=b", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    assert c.post(api("/session/passkey/verify"), json={"challenge_id": "x", "credential": {}}).status_code == 401
    assert c.post(api("/session/passkey/verify"), json={"challenge_id": "x"}).status_code == 422
    for path in ("/", "/login"):
        assert "set-cookie" not in c.get(path).headers


def test_a_login_holds_at_most_ten_passkeys(app):
    c, csrf = session(app)
    for i in range(pk.MAX_PASSKEYS):
        enrol(c, csrf, SoftKey(), f"k{i}")
    r = c.post(api("/session/passkeys/options"), json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409 and r.json()["error"]["code"] == "passkey_limit"


def test_challenges_expire_and_are_single_use():
    ch = pk.Challenges(ttl=10, cap=3)
    a = ch.put(b"a", "login", None, now=100)
    assert ch.take(a, "register", now=101) is None                 # the wrong kind
    assert ch.take(a, "login", now=111) is None                    # too late
    b = ch.put(b"b", "register", "papa", now=100)
    assert ch.take(b, "register", now=105) == (b"b", "papa") and ch.take(b, "register", now=105) is None
    for i in range(5):
        ch.put(bytes([i]), "login", None, now=200 + i)
    assert len(ch._items) <= 3                                      # capped: the oldest go first
