"""Passkeys (WebAuthn) of the web app (E16).

A passkey is a key pair made by the person's device (Face ID, Touch ID, Windows Hello, a security key, a password manager): the
private key never leaves it, the app keeps the PUBLIC key in ``ui_passkeys`` with the login it belongs to. A passkey is enrolled from
a session that was opened the usual way (the one-time link): from then on the login page offers "Sign in with a passkey", and the link
remains the recovery path. A credential is bound to the host name it was made on (the relying-party id): one made on
``server.tailnet.ts.net`` does not work on ``localhost`` and the other way round, which is what makes it phishing-resistant.

Challenges live in this process only (the server is one process) for two minutes; they are single-use.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Optional

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                      UserVerificationRequirement)

from coach.api.errors import ApiError
from coach.db import now_iso

RP_NAME = "AI finance coach"
OWNER = "owner"
CHALLENGE_TTL = 120
MAX_PASSKEYS = 10                   # per login
LABEL_MAX = 40
LOOPBACK_NAMES = ("127.0.0.1", "::1", "[::1]")


class Challenges:
    """Pending challenges: id -> (challenge, kind, login, expiry). In memory, single-use, two minutes, capped."""

    def __init__(self, ttl: int = CHALLENGE_TTL, cap: int = 200):
        self.ttl, self.cap = ttl, cap
        self._items: dict[str, tuple[bytes, str, Optional[str], float]] = {}
        self._lock = threading.Lock()

    def put(self, challenge: bytes, kind: str, login: Optional[str], now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        cid = secrets.token_urlsafe(18)
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[3] > now}
            if len(self._items) >= self.cap:                                   # the oldest go first
                for k in sorted(self._items, key=lambda k: self._items[k][3])[: len(self._items) - self.cap + 1]:
                    del self._items[k]
            self._items[cid] = (challenge, kind, login, now + self.ttl)
        return cid

    def take(self, cid: str, kind: str, now: Optional[float] = None) -> Optional[tuple[bytes, Optional[str]]]:
        now = time.time() if now is None else now
        with self._lock:
            item = self._items.pop(cid or "", None)
        if not item or item[1] != kind or item[3] <= now:
            return None
        return item[0], item[2]


def relying_party(request, security) -> tuple[str, str]:
    """``(rp id, origin)`` of this request. The host was already checked against the allowed names; a loopback ADDRESS cannot be a
    relying party (browsers refuse it): open the app as ``localhost``."""
    host = (request.headers.get("host") or "").strip().lower()
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") and "]:" in host else host
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name in LOOPBACK_NAMES:
        raise ApiError(400, "passkey_host", "a passkey needs a host NAME: open the app as http://localhost:PORT on this machine")
    origin = request.headers.get("origin")
    if not origin:
        scheme = "https" if security.secure_cookie and name != "localhost" else "http"
        origin = f"{scheme}://{host}"
    return name, origin


def _rows(con, login: str) -> list[dict]:
    try:
        rows = con.execute("SELECT id, login, public_key, sign_count, transports, rp_id, label, created_at, last_used_at, backed_up "
                           "FROM ui_passkeys WHERE login=? ORDER BY created_at", (login,)).fetchall()
    except Exception:                                                      # noqa: BLE001 - migration 0024 pending
        return []
    return [dict(zip(("id", "login", "public_key", "sign_count", "transports", "rp_id", "label", "created_at", "last_used_at", "backed_up"), r))
            for r in rows]


def listing(con, login: str) -> list[dict]:
    """What the person sees: never the public key."""
    return [{"id": r["id"], "label": r["label"], "rp_id": r["rp_id"], "created_at": r["created_at"], "last_used_at": r["last_used_at"],
             "backed_up": bool(r["backed_up"])} for r in _rows(con, login)]


def _descriptors(rows: list[dict]) -> list[PublicKeyCredentialDescriptor]:
    from webauthn.helpers import base64url_to_bytes
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"])) for r in rows]


def registration_options(con, challenges: Challenges, request, security, login: str) -> dict:
    rp_id, _ = relying_party(request, security)
    rows = _rows(con, login)
    if len(rows) >= MAX_PASSKEYS:
        raise ApiError(409, "passkey_limit", f"this login already has {MAX_PASSKEYS} passkeys: remove one first")
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=login.encode(), user_name=login, user_display_name=login, timeout=CHALLENGE_TTL * 1000,
        exclude_credentials=_descriptors(rows),
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED))
    cid = challenges.put(opts.challenge, "register", login)
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def register(con, challenges: Challenges, request, security, login: str, challenge_id: str, credential: dict, label: str) -> dict:
    rp_id, origin = relying_party(request, security)
    label = (label or "").strip()[:LABEL_MAX] or "passkey"
    pending = challenges.take(challenge_id, "register")
    if not pending or pending[1] != login:
        raise ApiError(400, "passkey_challenge", "this passkey request expired: start again")
    try:
        v = verify_registration_response(credential=json.dumps(credential), expected_challenge=pending[0], expected_rp_id=rp_id,
                                         expected_origin=origin)
    except (InvalidRegistrationResponse, ValueError, TypeError, KeyError) as e:
        raise ApiError(400, "passkey_invalid", f"the browser's answer could not be verified ({type(e).__name__})")
    cred_id = bytes_to_base64url(v.credential_id)
    transports = credential.get("response", {}).get("transports") if isinstance(credential.get("response"), dict) else None
    transports = [t for t in transports if isinstance(t, str)][:6] if isinstance(transports, list) else []
    if len(_rows(con, login)) >= MAX_PASSKEYS:
        raise ApiError(409, "passkey_limit", f"this login already has {MAX_PASSKEYS} passkeys: remove one first")
    con.execute("INSERT OR REPLACE INTO ui_passkeys(id, login, public_key, sign_count, transports, rp_id, label, created_at, backed_up) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cred_id, login, v.credential_public_key, v.sign_count, json.dumps(transports), rp_id, label, now_iso(), int(bool(v.credential_backed_up))))
    con.commit()
    return {"id": cred_id, "label": label, "rp_id": rp_id}


def remove(con, login: str, cred_id: str) -> bool:
    cur = con.execute("DELETE FROM ui_passkeys WHERE id=? AND login=?", (cred_id, login))
    con.commit()
    return cur.rowcount > 0


def authentication_options(challenges: Challenges, request, security) -> dict:
    rp_id, _ = relying_party(request, security)
    opts = generate_authentication_options(rp_id=rp_id, timeout=CHALLENGE_TTL * 1000, user_verification=UserVerificationRequirement.PREFERRED)
    cid = challenges.put(opts.challenge, "login", None)
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def authenticate(con, challenges: Challenges, request, security, challenge_id: str, credential: dict) -> Optional[str]:
    """The login the passkey belongs to, or None. The signature counter is updated on success."""
    rp_id, origin = relying_party(request, security)
    pending = challenges.take(challenge_id, "login")
    if not pending:
        return None
    cred_id = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(cred_id, str) or not cred_id:
        return None
    try:
        row = con.execute("SELECT login, public_key, sign_count FROM ui_passkeys WHERE id=? AND rp_id=?", (cred_id, rp_id)).fetchone()
    except Exception:                                                      # noqa: BLE001
        return None
    if not row:
        return None
    login, public_key, count = row
    try:
        v = verify_authentication_response(credential=json.dumps(credential), expected_challenge=pending[0], expected_rp_id=rp_id,
                                           expected_origin=origin, credential_public_key=public_key, credential_current_sign_count=count)
    except (InvalidAuthenticationResponse, ValueError, TypeError, KeyError):
        return None
    con.execute("UPDATE ui_passkeys SET sign_count=?, last_used_at=? WHERE id=?", (v.new_sign_count, now_iso(), cred_id))
    con.commit()
    return login
