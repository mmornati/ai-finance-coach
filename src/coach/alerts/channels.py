"""The delivery channels (E10-1): macOS notification, ntfy, e-mail (SMTP), Telegram. The in-app feed needs no channel: it is the
``alert_events`` table.

* All four are OFF unless the user enables them in ``[alerts.<name>]``; a real send happens only for an enabled and complete channel.
* Secrets (ntfy token, SMTP password, Telegram bot token) come from the secrets store (Keychain / environment), never from the config
  file, and are never printed: :func:`render` masks them.
* Only https: ntfy URL must be https; SMTP needs STARTTLS or SSL; Telegram goes to the fixed https://api.telegram.org. No redirect is
  followed (a token must not follow a redirect to another host).
* Nothing here touches the network at import time or by itself: the real transports are the module-level ``_default_*`` functions, which
  tests replace (tests/conftest.py makes them fail loudly) or bypass with a :class:`Transports` of fakes.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Callable, Optional

from coach import egress, secrets
from coach.alerts.messages import Message
from coach.alerts.settings import CHANNELS, SECRET_OF, AlertSettings

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 10


class ChannelError(Exception):
    """A send failed. The text never contains a secret (it is stored in alert_deliveries)."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):                                        # noqa: D401
        return None


def _default_http_post(url: str, data: bytes, headers: dict, timeout: float = TIMEOUT) -> int:
    """POST and return the HTTP status. https only, no redirect."""
    if not url.lower().startswith("https://"):
        raise ChannelError("refusing to send over a non-https URL")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _default_smtp(host: str, port: int, tls: str, timeout: float = TIMEOUT):
    ctx = ssl.create_default_context()
    if tls == "ssl":
        return smtplib.SMTP_SSL(host, port, local_hostname="localhost", timeout=timeout, context=ctx)
    c = smtplib.SMTP(host, port, local_hostname="localhost", timeout=timeout)
    c.ehlo()
    c.starttls(context=ctx)
    c.ehlo()
    return c


def _default_run(cmd, **kw):
    return subprocess.run(cmd, **kw)


@dataclass
class Transports:
    """What really talks to the outside. Every field defaults to the module's real function, looked up when used, so a test can
    replace either a field or the module attribute."""
    http_post: Optional[Callable] = None
    smtp: Optional[Callable] = None
    macos_runner: Optional[Callable] = None
    secret: Optional[Callable] = None            # name -> value or None

    def post(self, *a, **k):
        return (self.http_post or _default_http_post)(*a, **k)

    def smtp_connect(self, *a, **k):
        return (self.smtp or _default_smtp)(*a, **k)

    def get_secret(self, name: str) -> Optional[str]:
        if self.secret:
            return self.secret(name)
        try:
            return secrets.get_secret(name, required=False)
        except secrets.SecretBackendError:
            return None


def problems(s: AlertSettings, name: str, t: Optional[Transports] = None, cfg=None) -> list[str]:
    """Why an enabled channel cannot send (empty = ready). Never contains a secret value."""
    t = t or Transports()
    ok, _, why = egress.evaluate(f"alerts.{name}", cfg=cfg)          # E11-4: refused by the privacy mode = not ready (not an error)
    out = [why] if not ok else []
    out += [f"missing setting: {m}" for m in s.missing(name)]
    need = SECRET_OF.get(name)
    if need and need != "ntfy_token" and not t.get_secret(need):
        out.append(f"secret '{need}' not set: `coach config set-secret {need}`")
    return out


def status(s: AlertSettings, t: Optional[Transports] = None, cfg=None) -> list[dict]:
    """One row per channel for `coach alerts channels` / the web app: enabled, ready, what is missing, a masked target."""
    rows = []
    for name in CHANNELS:
        c = s.channel(name)
        probs = problems(s, name, t, cfg) if c.enabled else []
        rows.append({"channel": name, "enabled": c.enabled, "ready": c.enabled and not probs, "problems": probs,
                     "external": name != "macos", "target": target_of(s, name)})
    return rows


def target_of(s: AlertSettings, name: str) -> str:
    """Where the channel sends, masked enough to show on screen."""
    from urllib.parse import urlparse
    c = s.channel(name)
    if name == "macos":
        return "this Mac (notification centre)"
    if name == "ntfy":
        u = urlparse(c.url) if c.url else None
        return f"{u.hostname}/{'*' * 3}" if u and u.hostname else "(no topic URL set)"
    if name == "email":
        if not c.to:
            return "(no address set)"
        user, _, dom = c.to.partition("@")
        return f"{user[:1]}***@{dom} ({c.tls})"
    return f"chat {'*' * max(0, len(c.chat_id) - 3)}{c.chat_id[-3:]}" if c.chat_id else "(no chat id set)"


# ---------------------------------------------------------------- rendering = exactly what is sent (secrets masked)

def _ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode()


def build(s: AlertSettings, name: str, msg: Message, t: Optional[Transports] = None) -> dict:
    """The request a send would make, secrets included under the key ``_secret`` (never printed: use :func:`render`)."""
    t = t or Transports()
    c = s.channel(name)
    if name == "macos":
        from coach.notify import SCRIPT
        argv = ["osascript"]
        for line in SCRIPT:
            argv += ["-e", line]
        return {"channel": name, "argv": argv + [msg.body or msg.title, msg.title], "title": msg.title, "body": msg.body}
    if name == "ntfy":
        headers = {"Title": _ascii(msg.title), "Priority": "high" if msg.priority == "high" else "default",
                   "Tags": ",".join(msg.tags or ["bell"]), "Content-Type": "text/plain; charset=utf-8"}
        token = t.get_secret("ntfy_token")
        return {"channel": name, "method": "POST", "url": c.url, "headers": headers, "body": msg.body,
                "_secret": {"Authorization": f"Bearer {token}"} if token else {}}
    if name == "email":
        return {"channel": name, "smtp": {"host": c.host, "port": c.port, "tls": c.tls, "username": c.username},
                "from": c.from_addr, "to": c.to, "subject": msg.title, "body": msg.body, "_secret": {"password": t.get_secret("smtp_password")}}
    if name == "telegram":
        token = t.get_secret("telegram_bot_token")
        return {"channel": name, "method": "POST", "url": f"{TELEGRAM_API}/bot{token or '<token>'}/sendMessage",
                "headers": {"Content-Type": "application/json"},
                "json": {"chat_id": c.chat_id, "text": f"{msg.title}\n{msg.body}", "disable_web_page_preview": True},
                "_secret": {"token": token}}
    raise ValueError(f"unknown channel {name!r}")


def render(s: AlertSettings, name: str, msg: Message, t: Optional[Transports] = None, mask_target: bool = False) -> dict:
    """What a send would do, safe to print: tokens and passwords are replaced by ********. `mask_target` (the web app, which may be
    reached from another device) also hides the ntfy topic, the e-mail address and the Telegram chat id: on a public ntfy server the
    topic name is the only secret."""
    req = build(s, name, msg, t)
    sec = req.pop("_secret", {}) or {}
    out = json.loads(json.dumps(req))
    if name == "ntfy" and sec:
        out["headers"]["Authorization"] = "Bearer ********"
    if name == "email":
        out["smtp"]["password"] = "********" if sec.get("password") else "(not set)"
    if name == "telegram":
        out["url"] = f"{TELEGRAM_API}/bot********/sendMessage"
    if mask_target:
        if name == "ntfy":
            out["url"] = _mask_topic(out["url"])
        elif name == "email":
            out["to"] = target_of(s, "email").split(" (")[0]
            out["from"] = out["from"][:1] + "***@" + out["from"].partition("@")[2]
            out["smtp"]["host"] = "***"
            out["smtp"]["username"] = out["smtp"]["username"][:1] + "***"
        elif name == "telegram":
            out["json"]["chat_id"] = "***" + str(out["json"]["chat_id"])[-3:]
    return out


def _mask_topic(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}/***" if u.hostname else "***"


def format_render(r: dict) -> str:
    lines = [f"channel: {r['channel']}"]
    if r["channel"] == "macos":
        lines += [f"title: {r['title']}", f"body: {r['body']}", "run: " + " ".join(json.dumps(a) for a in r["argv"][:2]) + " ... (message and title as arguments, never inside the script)"]
    elif r["channel"] == "email":
        sm = r["smtp"]
        lines += [f"smtp: {sm['host']}:{sm['port']} ({sm['tls']}), user {sm['username']}, password {sm['password']}",
                  f"from: {r['from']}", f"to: {r['to']}", f"subject: {r['subject']}", "body:", *("  " + ln for ln in r["body"].splitlines())]
    else:
        lines += [f"{r['method']} {r['url']}"] + [f"  {k}: {v}" for k, v in r["headers"].items()]
        lines += ["body:"] + ["  " + ln for ln in (r["body"] if "body" in r else json.dumps(r["json"], ensure_ascii=False)).splitlines()]
    return "\n".join(lines)


# ---------------------------------------------------------------- sending

def send(s: AlertSettings, name: str, msg: Message, t: Optional[Transports] = None, cfg=None) -> None:
    """Hand the message to the channel. Raises ChannelError (never with a secret in the text)."""
    t = t or Transports()
    c = s.channel(name)
    if not c.enabled:
        raise ChannelError(f"channel {name} is not enabled in config.toml ([alerts.{name}] enabled = true)")
    probs = problems(s, name, t, cfg)
    if probs:
        raise ChannelError(f"channel {name} is not ready: " + "; ".join(probs))
    # E11-1: the egress gate + journal row (host and size only). A macOS notification stays on this machine (journaled as local).
    from urllib.parse import urlparse as _up
    host = {"ntfy": lambda: _up(c.url or "").hostname or "", "email": lambda: c.host or "", "telegram": lambda: "api.telegram.org",
            "macos": lambda: "localhost"}[name]()
    try:
        egress.allow(f"alerts.{name}", {"host": host, "bytes": len((msg.title or "") + (msg.body or "")), "purpose": "alert"}, cfg=cfg)
    except egress.EgressDenied as e:
        raise ChannelError(str(e)) from None
    req = build(s, name, msg, t)
    sec = req.get("_secret") or {}
    try:
        if name == "macos":
            from coach.notify import notify_macos
            if not notify_macos(msg.body or msg.title, msg.title, runner=t.macos_runner):
                raise ChannelError("the notification command failed")
        elif name == "ntfy":
            h = {**req["headers"], **sec}
            code = t.post(c.url, req["body"].encode("utf-8"), h, TIMEOUT)
            if not 200 <= int(code) < 300:
                raise ChannelError(f"HTTP {code}")
        elif name == "telegram":
            code = t.post(f"{TELEGRAM_API}/bot{sec['token']}/sendMessage", json.dumps(req["json"]).encode("utf-8"), req["headers"], TIMEOUT)
            if not 200 <= int(code) < 300:
                raise ChannelError(f"HTTP {code}")
        elif name == "email":
            em = EmailMessage()
            em["From"], em["To"], em["Subject"] = c.from_addr, c.to, msg.title
            em["Date"], em["Message-ID"] = formatdate(localtime=True), make_msgid(domain="coach.invalid")
            em.set_content(msg.body)
            conn = t.smtp_connect(c.host, c.port, c.tls)
            try:
                conn.login(c.username, sec["password"])
                conn.send_message(em)
            finally:
                try:
                    conn.quit()
                except Exception:                                               # noqa: BLE001
                    pass
    except ChannelError:
        raise
    except Exception as e:                                                      # noqa: BLE001 - the class name only: a message may echo a secret
        raise ChannelError(type(e).__name__) from None


# the real transports under a stable name, for the tests that exercise them with a fake socket layer (the conftest replaces the
# `_default_*` names above by functions that fail)
REAL_HTTP_POST = _default_http_post
REAL_SMTP = _default_smtp
