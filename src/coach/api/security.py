"""Security of the local web app (see docs/ui.md).

* a per-install random secret (a 0600 file in the data dir), rotated on demand and when older than
  ``[ui] key_rotation_days``;
* **no cookie is ever issued by a plain page load.** ``coach ui`` (or ``coach ui --login-link``) prints a one-time login
  link whose token travels in the URL *fragment* (never sent to a server, a log or a Referer). The page POSTs it once to
  ``/api/v1/session/exchange``; the token is single-use and lives 2 minutes. Only then does the browser get an HttpOnly,
  SameSite=Strict cookie, named per port, signed with the secret, valid for ``[ui] session_hours`` and revocable;
* every /api call needs the cookie; mutating calls also need the CSRF token (HMAC of the cookie) and are rate limited;
* the Host header must be an allowed value (DNS rebinding) and a present Origin must match it;
* no CORS; strict response headers (CSP without inline scripts, frame denial, no referrer) on EVERY response.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

CSRF_HEADER = "x-csrf-token"
KEY_FILE = "ui-session.key"
LOGIN_FILE = "ui-login.json"
REVOKED_FILE = "ui-revoked.json"
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
LOGIN_TTL = 120                      # seconds a one-time login token lives

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
       "manifest-src 'self'; worker-src 'self'")
# the server-rendered API reference needs a stylesheet inline; it has no script
DOCS_CSP = CSP.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": CSP,
}


def apply_headers(resp, *, no_store: bool = True, csp: str | None = None) -> None:
    """The security headers, on any response (errors included)."""
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    if csp:
        resp.headers["Content-Security-Policy"] = csp
    if no_store:
        resp.headers["Cache-Control"] = "no-store"


def is_loopback(host: str) -> bool:
    return host.strip().lower() in LOOPBACK


# ---------------------------------------------------------------- the session secret

def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def rotate_secret(data_dir: Path) -> bytes:
    """A new secret: every cookie signed with the old one stops working at once; revocations are forgotten."""
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_hex(32).encode()
    _write_private(data_dir / KEY_FILE, raw)
    (data_dir / REVOKED_FILE).unlink(missing_ok=True)
    return raw


def load_secret(data_dir: Path, rotate_days: int | None = None) -> bytes:
    """The per-install secret: created on first use (0600, directory tightened to 0700) and replaced when it is older
    than `rotate_days`."""
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    path = data_dir / KEY_FILE
    if path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        raw = path.read_bytes().strip()
        old = rotate_days is not None and time.time() - path.stat().st_mtime > rotate_days * 86400
        if len(raw) >= 32 and not old:
            return raw
    return rotate_secret(data_dir)


@contextmanager
def _locked(path: Path):
    """Cross-process exclusive lock (the server and `coach ui --login-link` share the token file)."""
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class LoginTokens:
    """One-time login tokens, stored only as SHA-256 hashes with their expiry, in a 0600 file next to the secret."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / LOGIN_FILE
        self.data_dir = data_dir

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _exp(v) -> float:
        """A stored value is the expiry itself (the owner's token) or {"exp": ..., "user": ...} (a login of E14-8)."""
        if isinstance(v, dict):
            try:
                return float(v.get("exp", 0))
            except (TypeError, ValueError):
                return 0.0
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    def issue(self, ttl: int = LOGIN_TTL, now: float | None = None, user: str | None = None) -> str:
        """A one-time token. With `user`, the session it opens belongs to that login (role and scope are read from the database on
        every request); without, it is the owner's, as it always was."""
        now = time.time() if now is None else now
        self.data_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        with _locked(self.path):
            d = {h: e for h, e in _read_json(self.path).items() if self._exp(e) > now}
            d[self._h(token)] = ({"exp": now + ttl, "user": user} if user else now + ttl)
            _write_private(self.path, json.dumps(d).encode())
        return token

    def consume_user(self, token: str, now: float | None = None) -> tuple[bool, str | None]:
        """(valid, login id or None). True exactly once for a valid, unexpired token; the token is gone afterwards (a wrong one
        changes nothing)."""
        now = time.time() if now is None else now
        if not token or len(token) > 200:
            return False, None
        h = self._h(token)
        with _locked(self.path):
            d = _read_json(self.path)
            v = d.pop(h, None)
            ok = v is not None and self._exp(v) > now
            user = v.get("user") if ok and isinstance(v, dict) else None
            d = {k: e for k, e in d.items() if self._exp(e) > now}
            _write_private(self.path, json.dumps(d).encode())
        return ok, (user if isinstance(user, str) else None)

    def consume(self, token: str, now: float | None = None) -> bool:
        return self.consume_user(token, now)[0]


# ---------------------------------------------------------------- rate limiting

class Bucket:
    """Token bucket per key: `capacity` actions at once, refilled at `rate` per second."""

    def __init__(self, capacity: float, rate: float):
        self.capacity, self.rate = capacity, rate
        self.state: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def take(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            tokens, last = self.state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1:
                self.state[key] = (tokens, now)
                return False
            self.state[key] = (tokens - 1, now)
            if len(self.state) > 1000:
                self.state = dict(list(self.state.items())[-500:])
            return True


# ---------------------------------------------------------------- the security object

class Security:
    def __init__(self, secret: bytes, port: int, extra_hosts=(), dev_ports=(), *, session_hours: int = 12,
                 secure_cookie: bool = False, data_dir: Path | None = None):
        self.secret = secret
        self.port = port
        self.max_age = int(session_hours * 3600)
        self.secure_cookie = secure_cookie
        self.cookie_name = f"coach_session_{port}"
        self.data_dir = data_dir
        self.tokens = LoginTokens(data_dir) if data_dir else None
        hosts = set()
        for p in (port, *dev_ports):
            hosts |= {f"localhost:{p}", f"127.0.0.1:{p}", f"[::1]:{p}"}
        for h in extra_hosts:                                   # [ui] allowed_hosts, only with allow_remote
            hosts |= {h, f"{h}:{port}"}
        self.hosts = {h.lower() for h in hosts}
        self.revoked: dict[str, float] = {}
        self._rev_lock = threading.Lock()
        if data_dir:
            self.revoked = {k: float(v) for k, v in _read_json(data_dir / REVOKED_FILE).items()}
        self.write_bucket = Bucket(capacity=30, rate=2.0)       # writes per session
        self.preview_bucket = Bucket(capacity=40, rate=8.0)     # dry-run previews (debounced typing) per session
        self.exchange_bucket = Bucket(capacity=8, rate=8 / 300)  # login attempts, whoever sends them
        self.passkey_bucket = Bucket(capacity=10, rate=10 / 300)  # passkey assertions (E16), whoever sends them

    def _mac(self, label: str, value: str) -> str:
        return hmac.new(self.secret, f"{label}:{value}".encode(), hashlib.sha256).hexdigest()

    # -- cookie: sid.issued_at.signature  (the owner)  or  sid.issued_at.login.signature  (a login of E14-8)
    def new_cookie(self, now: float | None = None, user: str | None = None) -> str:
        sid = secrets.token_urlsafe(18)
        iat = str(int(time.time() if now is None else now))
        body = f"{sid}.{iat}" + (f".{user}" if user else "")
        return f"{body}.{self._mac('sid', body)}"

    def parse_session(self, value: str | None, now: float | None = None) -> tuple[str, str | None] | None:
        """(session id, login id or None) of a valid, unexpired, unrevoked cookie, else None. Whether the login still exists and
        is enabled is checked by the caller against the database, on every request."""
        if not value or value.count(".") not in (2, 3):
            return None
        parts = value.split(".")
        sig = parts[-1]
        body = ".".join(parts[:-1])
        sid, iat = parts[0], parts[1]
        user = parts[2] if len(parts) == 4 else None
        if not hmac.compare_digest(self._mac("sid", body), sig):
            return None
        now = time.time() if now is None else now
        if not iat.isdigit() or now - int(iat) > self.max_age or int(iat) > now + 60:
            return None
        with self._rev_lock:
            if sid in self.revoked:
                return None
        return sid, user

    def parse_cookie(self, value: str | None, now: float | None = None) -> str | None:
        """The session id of a valid, unexpired, unrevoked cookie, else None."""
        got = self.parse_session(value, now)
        return got[0] if got else None

    def cookie_valid(self, value: str | None, now: float | None = None) -> bool:
        return self.parse_cookie(value, now) is not None

    def revoke(self, value: str | None) -> None:
        sid = self.parse_cookie(value)
        if not sid:
            return
        with self._rev_lock:
            now = time.time()
            self.revoked = {k: e for k, e in self.revoked.items() if e > now}
            self.revoked[sid] = now + self.max_age + 60
            if self.data_dir:
                _write_private(self.data_dir / REVOKED_FILE, json.dumps(self.revoked).encode())

    def csrf_token(self, cookie: str) -> str:
        return self._mac("csrf", cookie)

    def csrf_valid(self, cookie: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(self.csrf_token(cookie), token)

    def host_allowed(self, host: str | None) -> bool:
        return bool(host) and host.strip().lower() in self.hosts

    def origin_allowed(self, origin: str | None) -> bool:
        """No Origin header (same-origin GET, curl) is fine; a present one must be http(s)://<allowed host>."""
        if origin is None:
            return True
        for scheme in ("http://", "https://"):
            if origin.lower().startswith(scheme):
                return origin[len(scheme):].lower() in self.hosts
        return False
