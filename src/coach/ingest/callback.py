"""Local HTTPS redirect receiver (E1-8).

`coach connect` / `reconnect` bind this server on the host/port of the configured redirect URL
(default ``https://localhost:8443/callback``, which must be whitelisted in the Enable Banking app), the
bank redirects the browser to it with ``?state&code``, the code is exchanged through the same code path as
``coach finish``, a small page is shown and the server stops (or gives up after a timeout).

TLS: a self-signed certificate for localhost / 127.0.0.1 / ::1 is generated once with the `cryptography`
package into ``<data_dir>/tls/`` (private key 0600) and reused. The browser shows a one-time warning for it.
The server only ever binds to loopback and ignores every request except ``GET <redirect path>``.
"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import os
import socket
import ssl
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from coach.db import ensure_private_dir

LOOPBACK = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}
CERT_NAME, KEY_NAME = "localhost.crt", "localhost.key"
CERT_DAYS = 825
RENEW_BEFORE_DAYS = 30


class CallbackError(Exception):
    pass


# ---------------------------------------------------------------- certificate

def _cert_ok(cert_path: Path, key_path: Path) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except Exception:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    return cert.not_valid_after_utc - now > dt.timedelta(days=RENEW_BEFORE_DAYS)


def ensure_cert(tls_dir: Path) -> tuple[Path, Path]:
    """Return (cert, key) in `tls_dir`, generating a self-signed pair when missing or about to expire."""
    ensure_private_dir(tls_dir)
    cert_path, key_path = tls_dir / CERT_NAME, tls_dir / KEY_NAME
    if cert_path.exists() and key_path.exists() and _cert_ok(cert_path, key_path):
        return cert_path, key_path
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ai-finance-coach (local, self-signed)")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=CERT_DAYS))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    tmp_key = key_path.with_name(key_path.name + ".tmp")
    tmp_key.unlink(missing_ok=True)
    fd = os.open(tmp_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # never world-readable, even briefly
    with os.fdopen(fd, "wb") as f:
        f.write(key_bytes)
    os.replace(tmp_key, key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(key_path, 0o600)
    return cert_path, key_path


# ---------------------------------------------------------------- server

@dataclass
class Outcome:
    """What the callback handler decided. done=False keeps the server waiting (e.g. unknown state)."""
    done: bool
    success: bool
    message: str
    http_status: int = 200


@dataclass
class CallbackResult:
    status: str            # ok | error | timeout
    message: str = ""


def parse_redirect(url: str) -> tuple[str, str, int, str]:
    """(scheme, bind address, port, path) for a redirect URL we can serve; CallbackError otherwise."""
    u = urlparse(url)
    if u.scheme not in ("https", "http"):
        raise CallbackError(f"redirect URL {url!r} must be http(s)")
    host = (u.hostname or "").lower()
    if host not in LOOPBACK:
        raise CallbackError(f"redirect URL host {host!r} is not local (localhost / 127.0.0.1 / ::1)")
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.scheme, LOOPBACK[host], port, u.path or "/"


PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#1b1b1b}}
h1{{font-size:1.4rem}}.ok{{color:#176b2c}}.err{{color:#a1281f}}</style>
<h1 class="{cls}">{title}</h1><p>{message}</p>"""


CONN_SECONDS = 10      # hard cap for one connection (handshake + request), further bounded by the server deadline


class _DeadlineIO(socket.SocketIO):
    """Raw reader with a TOTAL deadline: every recv gets only the time that is left, so a client dribbling one
    byte at a time (slow-loris) cannot hold the single-threaded server past its timeout."""

    def __init__(self, sock, mode, deadline: float):
        super().__init__(sock, mode)
        self.deadline = deadline

    def readinto(self, b):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("connection deadline exceeded")
        self._sock.settimeout(left)
        return super().readinto(b)


class _Server(HTTPServer):
    # Needed to rebind right after a previous run (TIME_WAIT). On macOS it would also let us bind next to a
    # wildcard listener of another program, so bind() probes the port first and refuses in that case.
    allow_reuse_address = True
    ssl_ctx: ssl.SSLContext | None = None
    deadline: float = float("inf")          # monotonic time at which the server stops waiting

    def conn_deadline(self) -> float:
        return min(time.monotonic() + CONN_SECONDS, self.deadline)

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(max(0.05, min(5.0, self.deadline - time.monotonic())))
        if self.ssl_ctx:       # the handshake timeout is the TOTAL time allowed for the handshake
            sock = self.ssl_ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def handle_error(self, request, client_address):  # handshake failures (cert warning) are routine
        pass


def _port_answers(addr: str, port: int) -> bool:
    """True if something already accepts connections on addr:port."""
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((addr, port)) == 0
    except OSError:                  # e.g. no IPv6 on this machine
        return False


def container_bind_allowed(cfg, marker=None) -> bool:
    """The redirect server may listen on 0.0.0.0 ONLY inside a real container AND with `[callback] container_bind = true` (written by the image's
    `coach init`). The setting without a container is an error, not a silent loopback bind."""
    if not getattr(cfg, "callback_container_bind", False):
        return False
    if marker is None:
        from coach import home as home_mod
        marker = home_mod.on_container_filesystem
    if not marker():
        raise CallbackError("[callback] container_bind = true, but this process is not running in a container (no /.dockerenv, /run/.containerenv "
                            "or container cgroup): remove it from your configuration. The redirect server listens on loopback only.")
    return True


class CallbackServer:
    def __init__(self, redirect_url: str, handle, tls_dir: Path | None = None, timeout: float = 600,
                 poll: float = 0.2, bind_all: bool = False):
        self.bind_all = bind_all                   # container mode only (see container_bind_allowed): listen on 0.0.0.0
        self.redirect_url = redirect_url
        self.handle = handle                       # handle(params: dict[str, str]) -> Outcome
        self.tls_dir, self.timeout, self.poll = tls_dir, timeout, poll
        self.scheme, self.addr, self.port, self.path = parse_redirect(redirect_url)
        self.host = (urlparse(redirect_url).hostname or "").lower()
        self.httpd: _Server | None = None
        self._result: CallbackResult | None = None

    def bind(self) -> int:
        """Bind and listen (so the redirect can never arrive before we are ready). Returns the port."""
        server_cls = _Server
        if self.addr == "::1":
            class server_cls(_Server):  # noqa: N801
                address_family = socket.AF_INET6
        probe = [self.addr] + (["::1"] if self.host == "localhost" else [])      # 'localhost' may be either family
        if self.port and any(_port_answers(a, self.port) for a in probe):
            raise CallbackError(
                f"{self.host or self.addr}:{self.port} is already served by another program (for example another coach "
                f"process or a bank tool listening on the same port). Stop it, or use --no-server for the manual "
                f"copy-paste flow.")
        try:
            if self.bind_all:
                class server_cls(_Server):  # noqa: N801,F811
                    address_family = socket.AF_INET
            httpd = server_cls(("0.0.0.0" if self.bind_all else self.addr, self.port), self._handler_class())
        except OSError as e:
            raise CallbackError(
                f"cannot listen on {self.addr}:{self.port} ({e}). Is another `coach connect` or program using "
                f"it? Use --no-server for the manual copy-paste flow.") from e
        if self.scheme == "https":
            if not self.tls_dir:
                httpd.server_close()
                raise CallbackError("https redirect needs a TLS directory")
            cert, key = ensure_cert(self.tls_dir)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert, key)
            httpd.ssl_ctx = ctx
        httpd.timeout = self.poll
        self.httpd = httpd
        self.port = httpd.server_address[1]
        return self.port

    def _handler_class(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "coach-callback"
            sys_version = ""

            def setup(self):
                if hasattr(self.request, "do_handshake"):
                    self.request.do_handshake()
                super().setup()
                self.rfile.close()
                self.rfile = _DeadlineIO(self.connection, "rb", owner.httpd.conn_deadline())

            def log_message(self, *a):  # never log URLs: they carry the authorisation code
                pass

            def _send(self, status, title, message, ok):
                body = PAGE.format(title=html.escape(title), message=html.escape(message),
                                   cls="ok" if ok else "err").encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path != owner.path:
                    self._send(404, "Not found", "This address is not the bank redirect.", False)
                    return
                params = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    outcome = owner.handle(params)
                except Exception as e:
                    outcome = Outcome(True, False, f"{type(e).__name__}: {e}"[:300], 500)
                if outcome.success:
                    self._send(200, "Bank connected", outcome.message + " You can close this tab and go back "
                               "to the terminal.", True)
                else:
                    self._send(outcome.http_status if outcome.http_status != 200 else 400,
                               "Connection failed", outcome.message, False)
                if outcome.done:
                    owner._result = CallbackResult("ok" if outcome.success else "error", outcome.message)

        return Handler

    def serve(self) -> CallbackResult:
        if self.httpd is None:
            self.bind()
        deadline = time.monotonic() + self.timeout
        self.httpd.deadline = deadline
        while self._result is None and time.monotonic() < deadline:
            self.httpd.handle_request()
        return self._result or CallbackResult("timeout", f"no redirect received within {self.timeout:g}s")

    def close(self) -> None:
        if self.httpd:
            self.httpd.server_close()
            self.httpd = None
