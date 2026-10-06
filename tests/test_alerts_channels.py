"""E10-1: the channels with MOCKED transports (no network, no osascript), the config validation, secrets and the rendering that
`coach alerts test-channel` prints."""
from __future__ import annotations

import json
import ssl
import urllib.error
from types import SimpleNamespace

import pytest

from alerthelpers import ALL_ON, SECRETS, Net, settings
from coach import notify
from coach.alerts import channels as ch, messages as M
from coach.alerts.settings import AlertConfigError, AlertSettings
from coach.config import ConfigError, load_config

MSG = M.Message("Coach alerts", "Coach: 2 new alerts (1 high). Open the app.", "high", ["bell"])


# ---------------------------------------------------------------- config

def test_defaults_every_external_channel_off_and_minimal():
    s = AlertSettings()
    assert s.enabled_channels() == [] and s.external_detail == "minimal" and s.min_severity == "low" and s.max_per_week == 7
    assert s.weekly_digest is True and s.weekly_digest_to_channels is False and s.quiet_hours == ""


def test_the_example_config_loads_with_everything_off():
    from pathlib import Path
    import tomllib
    raw = tomllib.loads((Path(__file__).resolve().parents[1] / "config.example.toml").read_text())
    s = AlertSettings.from_dict(raw.get("alerts"))
    assert s.enabled_channels() == [] and s.external_detail == "minimal"
    assert raw["alerts"]["ntfy"]["enabled"] is False and raw["alerts"]["email"]["enabled"] is False and raw["alerts"]["telegram"]["enabled"] is False


@pytest.mark.parametrize("raw,msg", [
    ({"ntfy": {"enabled": True, "url": "http://ntfy.example.net/t"}}, "https"),
    ({"ntfy": {"url": "https://user:pw@ntfy.example.net/t"}}, "user name or password"),
    ({"ntfy": {"url": "ftp://x"}}, "https"),
    ({"email": {"tls": "none"}}, "starttls"),
    ({"email": {"tls": ""}}, "tls"),
    ({"email": {"to": "not-an-address"}}, "e-mail address"),
    ({"email": {"to": "a@example.net, b@example.net"}}, "e-mail address"),
    ({"telegram": {"chat_id": "not a chat"}}, "chat_id"),
    ({"external_detail": "full"}, "external_detail"),
    ({"min_severity": "critical"}, "min_severity"),
    ({"quiet_hours": "22-8"}, "quiet_hours"),
    ({"quiet_hours": "10:00-10:00"}, "same time"),
    ({"max_per_week": -1}, "max_per_week"),
    ({"max_per_week": True}, "max_per_week"),
    ({"disabled_kinds": ["nonsense"]}, "unknown kind"),
    ({"enabled": "yes"}, "true or false"),
    ({"external_detial": "summary"}, "unknown \\[alerts\\] setting"),
    ({"ntfy": {"enabld": True}}, "unknown \\[alerts\\.ntfy\\]"),
    ({"thresholds": {"sync_failures": 0}}, "sync_failures"),
    ({"weekly_digest_day": "someday"}, "weekly_digest_day"),
    ({"app_url": "ftp://x"}, "app_url"),
])
def test_bad_values_are_refused(raw, msg):
    with pytest.raises(AlertConfigError, match=msg):
        AlertSettings.from_dict(raw)


def test_a_bad_alerts_section_stops_the_config_from_loading(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[alerts.ntfy]\nenabled = true\nurl = "http://insecure.example.net/t"\n')
    with pytest.raises(ConfigError, match="https"):
        load_config(p, env={})
    p.write_text('[alerts]\nexternal_detail = "summary"\nquiet_hours = "23:00-07:00"\n[alerts.telegram]\nenabled = true\nchat_id = 42\n')
    cfg = load_config(p, env={})
    assert cfg.alert_settings.external_detail == "summary" and cfg.alert_settings.telegram.chat_id == "42"


def test_enabled_but_incomplete_channels_are_reported_not_fatal():
    s = settings(email={"enabled": True}, telegram={"enabled": True}, ntfy={"enabled": True})
    assert s.missing("email") == ["host", "username", "from", "to"] and s.missing("telegram") == ["chat_id"] and s.missing("ntfy") == ["url"]
    rows = {r["channel"]: r for r in ch.status(s, Net(secrets={}).transports())}
    assert rows["email"]["ready"] is False and any("smtp_password" in p for p in rows["email"]["problems"])
    assert rows["macos"]["ready"] is False and rows["macos"]["enabled"] is False


def test_config_show_lists_the_alert_settings_without_secrets(cfg):
    from coach.config import effective
    rows = {k: v for k, v, _ in effective(cfg)}
    assert rows["alerts.external_detail"] == "minimal" and rows["alerts.ntfy.enabled"] == "false" and rows["alerts.email.enabled"] == "false"
    assert not any("pw-" in str(v) or "token" in k for k, v in rows.items() if k.startswith("alerts."))


def test_the_new_secrets_are_known_to_the_secret_store(fake_keyring):
    from coach import secrets
    names = dict(secrets.describe())
    for n in ("ntfy_token", "smtp_password", "telegram_bot_token"):
        assert names[n] == "not set (optional)"
    secrets.set_secret("smtp_password", "hunter2")
    assert secrets.get_secret("smtp_password") == "hunter2" and "hunter2" not in " ".join(v for _, v in secrets.describe())


# ---------------------------------------------------------------- ntfy

def test_ntfy_posts_the_message_with_the_token_in_a_header():
    net, s = Net(), settings(ntfy=ALL_ON["ntfy"])
    ch.send(s, "ntfy", MSG, net.transports())
    p = net.posts[0]
    assert p["url"] == "https://ntfy.example.net/coach-9f3a" and p["body"] == MSG.body
    assert p["headers"] == {"Title": "Coach alerts", "Priority": "high", "Tags": "bell", "Content-Type": "text/plain; charset=utf-8",
                            "Authorization": "Bearer tk_ntfy_abc"}


def test_ntfy_without_a_token_sends_no_authorization_and_an_ascii_title():
    net, s = Net(secrets={}), settings(ntfy=ALL_ON["ntfy"])
    ch.send(s, "ntfy", M.Message("Coach: résumé hebdomadaire", "x"), net.transports())
    h = net.posts[0]["headers"]
    assert "Authorization" not in h and h["Title"].isascii() and h["Priority"] == "default"


def test_ntfy_non_2xx_is_an_error_without_the_token():
    net, s = Net(), settings(ntfy=ALL_ON["ntfy"])
    t = ch.Transports(http_post=lambda *a, **k: 403, secret=net.secrets.get)
    with pytest.raises(ch.ChannelError, match="HTTP 403") as e:
        ch.send(s, "ntfy", MSG, t)
    assert "tk_ntfy" not in str(e.value)


# ---------------------------------------------------------------- telegram

def test_telegram_posts_json_to_the_fixed_api_host():
    net, s = Net(), settings(telegram=ALL_ON["telegram"])
    ch.send(s, "telegram", MSG, net.transports())
    p = net.posts[0]
    assert p["url"] == "https://api.telegram.org/bot123456:TOKEN-xyz/sendMessage"
    assert json.loads(p["body"]) == {"chat_id": "424242", "text": "Coach alerts\nCoach: 2 new alerts (1 high). Open the app.",
                                     "disable_web_page_preview": True}
    assert p["headers"] == {"Content-Type": "application/json"}


def test_telegram_error_text_never_holds_the_token():
    net = Net()
    net.fail = True
    with pytest.raises(ch.ChannelError) as e:
        ch.send(settings(telegram=ALL_ON["telegram"]), "telegram", MSG, net.transports())
    assert str(e.value) == "OSError" and "TOKEN" not in str(e.value) and e.value.__cause__ is None


# ---------------------------------------------------------------- email

def test_email_sends_over_tls_with_the_password_from_the_secret_store():
    net, s = Net(), settings(email=ALL_ON["email"])
    ch.send(s, "email", MSG, net.transports())
    m = net.mails[0]
    assert m["smtp"] == ("smtp.example.net", 587, "starttls") and m["login"] == ("mailer", "pw-SMTP-1")
    assert (m["from"], m["to"], m["subject"]) == ("coach@example.net", "owner@example.net", "Coach alerts")
    assert m["body"].strip() == MSG.body


def test_email_failure_is_the_class_name_only():
    net = Net()
    net.fail = True
    with pytest.raises(ch.ChannelError) as e:
        ch.send(settings(email=ALL_ON["email"]), "email", MSG, net.transports())
    assert str(e.value) == "RuntimeError" and "pw-SMTP" not in str(e.value)


def test_the_real_smtp_helper_always_uses_tls(monkeypatch):
    calls = []

    class S:
        def __init__(self, *a, **k):
            calls.append(("init", type(self).__name__, a, k.get("context") is not None))

        def ehlo(self): calls.append("ehlo")

        def starttls(self, context=None): calls.append(("starttls", isinstance(context, ssl.SSLContext)))

    monkeypatch.setattr("smtplib.SMTP", type("SMTP", (S,), {}))
    monkeypatch.setattr("smtplib.SMTP_SSL", type("SMTP_SSL", (S,), {}))
    ch.REAL_SMTP("h", 587, "starttls")
    assert ("starttls", True) in calls
    calls.clear()
    ch.REAL_SMTP("h", 465, "ssl")
    assert calls == [("init", "SMTP_SSL", ("h", 465), True)]


# ---------------------------------------------------------------- macOS: argv, never script text

def test_macos_message_is_passed_as_argv_not_interpolated():
    net, s = Net(), settings(macos={"enabled": True})
    evil = M.Message('Coach "; do shell script "rm -rf ~" --', 'x" & (do shell script "id") & "', "default", [], "local")
    ch.send(s, "macos", evil, net.transports())
    cmd = net.macos[0]
    assert cmd[0] == "osascript" and cmd[-2:] == [evil.body, evil.title]
    assert cmd[1:-2] == [x for line in notify.SCRIPT for x in ("-e", line)]          # the script is the fixed one
    assert not any("rm -rf" in a for a in cmd[:-2])


def test_macos_failure_and_disabled_channel():
    net = Net()
    net.fail = True
    with pytest.raises(ch.ChannelError, match="notification command failed"):
        ch.send(settings(macos={"enabled": True}), "macos", MSG, net.transports())
    with pytest.raises(ch.ChannelError, match="not enabled"):
        ch.send(settings(), "macos", MSG, Net().transports())


# ---------------------------------------------------------------- gates and rendering

@pytest.mark.parametrize("name", ["ntfy", "email", "telegram", "macos"])
def test_a_disabled_channel_never_sends(name):
    net = Net()
    with pytest.raises(ch.ChannelError, match="not enabled"):
        ch.send(settings(), name, MSG, net.transports())
    assert net.total() == 0


def test_a_channel_without_its_secret_does_not_send():
    net = Net(secrets={})
    with pytest.raises(ch.ChannelError, match="smtp_password"):
        ch.send(settings(email=ALL_ON["email"]), "email", MSG, net.transports())
    with pytest.raises(ch.ChannelError, match="telegram_bot_token"):
        ch.send(settings(telegram=ALL_ON["telegram"]), "telegram", MSG, net.transports())
    assert net.total() == 0


def test_render_prints_what_is_sent_and_never_a_secret():
    net, s = Net(), settings(**ALL_ON)
    t = net.transports()
    out = {n: ch.render(s, n, MSG, t) for n in ("ntfy", "email", "telegram", "macos")}
    blob = json.dumps(out) + "\n".join(ch.format_render(r) for r in out.values())
    for secret in SECRETS.values():
        assert secret not in blob
    assert out["ntfy"]["headers"]["Authorization"] == "Bearer ********" and out["email"]["smtp"]["password"] == "********"
    assert out["telegram"]["url"] == "https://api.telegram.org/bot********/sendMessage"
    assert out["ntfy"]["body"] == MSG.body and out["email"]["subject"] == "Coach alerts"
    assert net.total() == 0                                                         # rendering sends nothing


def test_format_render_snapshots():
    net, s = Net(), settings(**ALL_ON)
    t = net.transports()
    assert ch.format_render(ch.render(s, "ntfy", MSG, t)) == (
        "channel: ntfy\nPOST https://ntfy.example.net/coach-9f3a\n  Title: Coach alerts\n  Priority: high\n  Tags: bell\n"
        "  Content-Type: text/plain; charset=utf-8\n  Authorization: Bearer ********\nbody:\n  Coach: 2 new alerts (1 high). Open the app.")
    assert ch.format_render(ch.render(s, "email", MSG, t)) == (
        "channel: email\nsmtp: smtp.example.net:587 (starttls), user mailer, password ********\nfrom: coach@example.net\n"
        "to: owner@example.net\nsubject: Coach alerts\nbody:\n  Coach: 2 new alerts (1 high). Open the app.")
    assert ch.format_render(ch.render(s, "telegram", MSG, t)) == (
        "channel: telegram\nPOST https://api.telegram.org/bot********/sendMessage\n  Content-Type: application/json\nbody:\n"
        '  {"chat_id": "424242", "text": "Coach alerts\\nCoach: 2 new alerts (1 high). Open the app.", "disable_web_page_preview": true}')


def test_target_is_masked_for_the_screen():
    s = settings(**ALL_ON)
    assert ch.target_of(s, "ntfy") == "ntfy.example.net/***" and "owner" not in ch.target_of(s, "email")
    assert ch.target_of(s, "email") == "o***@example.net (starttls)" and ch.target_of(s, "telegram") == "chat ***242"


# ---------------------------------------------------------------- the real HTTP helper (socket layer faked)

def test_real_http_post_is_https_only_and_does_not_follow_redirects(monkeypatch):
    with pytest.raises(ch.ChannelError, match="non-https"):
        ch.REAL_HTTP_POST("http://ntfy.example.net/t", b"x", {})
    seen = {}

    class Opener:
        def __init__(self, handlers): seen["handlers"] = handlers

        def open(self, req, timeout=None):
            seen.update(url=req.full_url, method=req.get_method(), data=req.data, timeout=timeout)
            return SimpleNamespace(status=200, __enter__=lambda s: s, __exit__=lambda *a: None)

    class Resp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    Opener.open = lambda self, req, timeout=None: (seen.update(url=req.full_url, method=req.get_method(), data=req.data, timeout=timeout) or Resp())
    monkeypatch.setattr("urllib.request.build_opener", lambda *h: Opener(h))
    assert ch.REAL_HTTP_POST("https://ntfy.example.net/t", b"hello", {"Title": "x"}) == 204
    assert seen["method"] == "POST" and seen["data"] == b"hello" and seen["timeout"] == 10
    assert any(h is ch.NoRedirect for h in seen["handlers"])
    assert ch.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example.net/") is None


def test_real_http_post_returns_the_error_status(monkeypatch):
    class Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    monkeypatch.setattr("urllib.request.build_opener", lambda *h: Opener())
    assert ch.REAL_HTTP_POST("https://ntfy.example.net/t", b"x", {}) == 401


def test_the_test_suite_cannot_reach_the_network_by_accident():
    with pytest.raises(AssertionError, match="must not be used in tests"):
        ch.Transports().post("https://x.example.net", b"", {})
    with pytest.raises(AssertionError, match="must not be used in tests"):
        ch.Transports().smtp_connect("h", 587, "starttls")


def test_the_web_rendering_also_hides_the_topic_the_address_and_the_chat_id():
    net, s = Net(), settings(**ALL_ON)
    t = net.transports()
    out = {n: json.dumps(ch.render(s, n, MSG, t, mask_target=True)) for n in ("ntfy", "email", "telegram")}
    assert "coach-9f3a" not in out["ntfy"] and "https://ntfy.example.net/***" in out["ntfy"]
    assert "smtp.example.net" not in out["email"] and "coach@example.net" not in out["email"] and "owner@example.net" not in out["email"] and "mailer" not in out["email"] and "o***@example.net" in out["email"]
    assert "424242" not in out["telegram"] and "***242" in out["telegram"]
    assert "coach-9f3a" in json.dumps(ch.render(s, "ntfy", MSG, t))                # the terminal shows exactly what is sent


def test_smtp_never_leaks_this_machine_s_host_name(monkeypatch):
    net, s = Net(), settings(email=ALL_ON["email"])
    ch.send(s, "email", MSG, net.transports())
    assert "coach.invalid" in net.mails[0]["message_id"]
    seen = {}

    class S:
        def __init__(self, *a, **k): seen.update(k)
        def ehlo(self): pass
        def starttls(self, context=None): pass

    monkeypatch.setattr("smtplib.SMTP", S)
    monkeypatch.setattr("smtplib.SMTP_SSL", S)
    for tls in ("starttls", "ssl"):
        seen.clear()
        ch.REAL_SMTP("h", 587, tls)
        assert seen["local_hostname"] == "localhost"


def test_app_url_warning():
    from coach.alerts.settings import warnings_of
    assert warnings_of(settings()) == [] and warnings_of(settings(app_url="http://127.0.0.1:8765/alerts")) == []
    assert warnings_of(settings(app_url="http://192.168.1.20:8765")) == []
    w = warnings_of(settings(app_url="https://mac.tailnet.ts.net/alerts"))
    assert len(w) == 1 and "mac.tailnet.ts.net" in w[0]
    assert warnings_of(settings(app_url="https://mac.tailnet.ts.net/alerts"), allowed_hosts=("mac.tailnet.ts.net",)) == []
