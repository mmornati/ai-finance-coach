"""E10 CLI: `coach alerts ...`. A dry run writes and sends nothing; ack / snooze / mute are local state; a real test send needs an
enabled channel, an interactive terminal and a typed yes; every transport is faked (no network, no osascript)."""
from __future__ import annotations

import json

import pytest

from alerthelpers import NOW, SECRETS, TODAY, Net, build_alert_world
from coach import db as dbm, secrets
from coach.alerts import channels as ch, engine
from coach.cli import main
from coach.config import load_config
from memhelpers import fake_tty


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr("coach.alerts.commands._today", lambda: TODAY)
    real = engine._utc
    monkeypatch.setattr(engine, "_utc", lambda now=None: NOW if now is None else real(now))


@pytest.fixture
def world(cfg):
    build_alert_world(cfg).close()
    return cfg


def run(cfg, capsys, *argv, expect_exit=False):
    args = ["--insecure", "--config", str(cfg.config_path), *argv]
    if expect_exit:
        with pytest.raises(SystemExit) as e:
            main(args)
        return capsys.readouterr().out + str(e.value)
    main(args)
    return capsys.readouterr().out


def js(cfg, capsys, *argv):
    return json.loads(run(cfg, capsys, *argv, "--json"))


def enable_all(cfg, detail="minimal"):
    toml = [f'\n[alerts]\nexternal_detail = "{detail}"\ndigest_only_kinds = []\n',
            '[alerts.ntfy]\nenabled = true\nurl = "https://ntfy.example.net/coach-9f3a"\n',
            '[alerts.email]\nenabled = true\nhost = "smtp.example.net"\nusername = "mailer"\nfrom = "coach@example.net"\nto = "owner@example.net"\n',
            '[alerts.telegram]\nenabled = true\nchat_id = "424242"\n[alerts.macos]\nenabled = true\n']
    cfg.config_path.write_text(cfg.config_path.read_text() + "".join(toml))
    for k, v in SECRETS.items():
        secrets.set_secret(k, v)
    return load_config(cfg.config_path, env={})


def count(cfg, table):
    con = dbm.connect(cfg, insecure=True)
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    con.close()
    return n


def fake_net(monkeypatch):
    """Every Transports() the CLI builds uses the fakes."""
    net = Net()
    real = ch.Transports
    monkeypatch.setattr(ch, "Transports", lambda *a, **k: net.transports() if not a and not k else real(*a, **k))
    return net


# ---------------------------------------------------------------- check

def test_check_dry_run_writes_nothing_and_sends_nothing(world, capsys):
    o = run(world, capsys, "alerts", "check", "--dry-run")
    assert o.startswith("DRY RUN: nothing was written or sent.") and "signal(s)" in o and "consent" in o and "low_balance" in o
    assert "channels: none enabled" in o
    assert count(world, "alert_events") == 0 and count(world, "alert_deliveries") == 0


def test_check_dry_run_shows_exactly_what_each_enabled_channel_would_get(world, capsys):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "check", "--dry-run")
    assert "channel ntfy: would_send" in o and "POST https://ntfy.example.net/coach-9f3a" in o and "Authorization: Bearer ********" in o
    assert "Coach: " in o and "new alerts (" in o and "Open the app." in o
    assert "pw-SMTP-1" not in o and "TOKEN-xyz" not in o and "tk_ntfy_abc" not in o
    assert "channel macos: would_send" in o and "channel telegram: would_send" in o
    assert count(cfg, "alert_events") == 0 and count(cfg, "alert_deliveries") == 0


def test_check_keeps_the_events_and_a_second_check_changes_nothing(world, capsys):
    o = run(world, capsys, "alerts", "check")
    assert "new," in o and "open alerts:" in o and count(world, "alert_events") > 0
    q = "SELECT id, status, created FROM alert_events ORDER BY id"
    before = dbm.connect(world, insecure=True).execute(q).fetchall()
    assert "0 new, 0 escalated" in run(world, capsys, "alerts", "check")
    assert dbm.connect(world, insecure=True).execute(q).fetchall() == before


def test_check_no_send_keeps_events_but_sends_nothing_even_when_channels_are_on(world, capsys):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "check", "--no-send")
    assert "channel " not in o and count(cfg, "alert_events") > 0 and count(cfg, "alert_deliveries") == 0


def test_check_with_channels_sends_through_the_configured_transports(world, capsys, monkeypatch):
    cfg = enable_all(world)
    net = fake_net(monkeypatch)
    fake_tty(monkeypatch, answers=())
    o = run(cfg, capsys, "alerts", "check")
    assert "channel ntfy: sent" in o and len(net.posts) == 2 and len(net.mails) == 1 and len(net.macos) == 1
    assert count(cfg, "alert_deliveries") == 4
    assert "channel ntfy: nothing" in run(cfg, capsys, "alerts", "check")          # never re-sent


def test_check_json(world, capsys):
    d = js(world, capsys, "alerts", "check", "--dry-run")
    assert d["evaluated"]["candidates"] == len(d["events"]) and {"consent", "low_balance"} <= {e["kind"] for e in d["events"]}


def test_alerts_disabled_in_config_evaluates_nothing(world, capsys):
    world.config_path.write_text(world.config_path.read_text() + "\n[alerts]\nenabled = false\n")
    cfg = load_config(world.config_path, env={})
    assert "alerts are disabled" in run(cfg, capsys, "alerts", "check") and count(cfg, "alert_events") == 0


# ---------------------------------------------------------------- list / show / ack / snooze / mute

def first(cfg, capsys, **filters):
    args = sum([[f"--{k}", v] for k, v in filters.items()], [])
    return js(cfg, capsys, "alerts", "list", *args)[0]


def test_list_show_ack_snooze_restore(world, capsys):
    run(world, capsys, "alerts", "check", "--no-send")
    rows = js(world, capsys, "alerts", "list")
    assert rows[0]["severity"] == "high" and all(r["status"] in ("new", "sent", "snoozed") for r in rows)
    assert run(world, capsys, "alerts", "list", "--kind", "consent").count("consent") >= 2
    a = first(world, capsys, kind="consent", severity="high")
    shown = run(world, capsys, "alerts", "show", a["id"][:10])
    assert a["id"] in shown and "payload (stays on this machine)" in shown
    assert f"acknowledged {a['id']}" in run(world, capsys, "alerts", "ack", a["id"])
    assert a["id"] not in run(world, capsys, "alerts", "list")
    assert a["id"] in run(world, capsys, "alerts", "list", "--status", "acked")
    run(world, capsys, "alerts", "restore", a["id"])
    assert a["id"] in run(world, capsys, "alerts", "list")
    out = run(world, capsys, "alerts", "snooze", a["id"], "--days", "3")
    assert "until 2026-10-07" in out and a["id"] in run(world, capsys, "alerts", "list", "--status", "snoozed")


def test_ack_all_and_kind_snooze_mute_and_restore(world, capsys):
    run(world, capsys, "alerts", "check", "--no-send")
    n = len(js(world, capsys, "alerts", "list", "--kind", "consent"))
    assert f"snoozed the kind consent until 2026-10-06 ({n} open event(s)" in run(world, capsys, "alerts", "snooze", "consent", "--days", "2")
    assert run(world, capsys, "alerts", "list", "--kind", "consent", "--status", "new").startswith("no alerts")
    assert f"woke the kind consent ({n} event(s))" in run(world, capsys, "alerts", "restore", "consent")
    assert "consent: muted" in run(world, capsys, "alerts", "mute-kind", "consent")
    assert len(js(world, capsys, "alerts", "list", "--status", "suppressed")) == n
    run(world, capsys, "alerts", "check", "--no-send")                         # still suppressed after the next check
    assert len(js(world, capsys, "alerts", "list", "--status", "suppressed")) == n
    assert "consent: unmuted" in run(world, capsys, "alerts", "unmute-kind", "consent")
    assert "acknowledged" in run(world, capsys, "alerts", "ack", "--all")
    assert run(world, capsys, "alerts", "list").startswith("no alerts")


def test_cli_errors(world, capsys):
    run(world, capsys, "alerts", "check", "--no-send")
    assert "no alert 'alr_nope'" in run(world, capsys, "alerts", "show", "alr_nope", expect_exit=True)
    assert "matches" in run(world, capsys, "alerts", "ack", "alr_", expect_exit=True)
    assert "give an alert id" in run(world, capsys, "alerts", "ack", expect_exit=True)
    assert "between 1 and 180" in run(world, capsys, "alerts", "snooze", "consent", "--days", "0", expect_exit=True)
    with pytest.raises(SystemExit):
        main(["--insecure", "--config", str(world.config_path), "alerts", "mute-kind", "nonsense"])


# ---------------------------------------------------------------- channels and test-channel

def test_channels_all_off_by_default(world, capsys):
    o = run(world, capsys, "alerts", "channels")
    assert "in-app feed: always on" in o and "all external channels are off (default)" in o
    assert [r["enabled"] for r in js(world, capsys, "alerts", "channels")] == [False] * 4


def test_channels_shows_readiness_and_never_a_secret(world, capsys):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "channels")
    assert o.count("READY") == 4
    for v in SECRETS.values():
        assert v not in o
    secrets.delete_secret("smtp_password")
    o = run(cfg, capsys, "alerts", "channels")
    assert "enabled but NOT READY" in o and "smtp_password" in o


@pytest.mark.parametrize("name,needle", [("ntfy", "POST https://ntfy.example.net/coach-9f3a"), ("email", "smtp: smtp.example.net:587 (starttls)"),
                                         ("telegram", "POST https://api.telegram.org/bot********/sendMessage"), ("macos", "title: Coach (test)")])
def test_test_channel_dry_run_prints_the_exact_message_and_sends_nothing(world, capsys, name, needle):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "test-channel", name, "--dry-run")
    assert needle in o and "nothing was sent" in o and "SAMPLE events" in o
    assert "Coach: 2 new alerts (1 high)." in o or name == "macos"
    for v in SECRETS.values():
        assert v not in o
    assert count(cfg, "alert_deliveries") == 0


def test_test_channel_dry_run_works_for_a_disabled_channel_and_says_so(world, capsys):
    o = run(world, capsys, "alerts", "test-channel", "ntfy", "--dry-run")
    assert "the channel is disabled in config.toml" in o and "channel: ntfy" in o and "Open the app." in o


def test_test_channel_summary_mode_shows_the_sample_lines(world, capsys):
    cfg = enable_all(world, "summary")
    o = run(cfg, capsys, "alerts", "test-channel", "ntfy", "--dry-run")
    assert "- Unusual charge (high), about 150 EUR" in o and "- Bank consent expiring or expired (medium)" in o


def test_test_channel_refuses_a_disabled_channel_without_dry_run(world, capsys):
    assert "disabled in config.toml, so nothing is sent" in run(world, capsys, "alerts", "test-channel", "telegram", expect_exit=True)


def test_test_channel_refuses_without_a_terminal(world, capsys):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "test-channel", "ntfy", expect_exit=True)
    assert "needs an interactive terminal" in o and "deliberately no" in o and count(cfg, "alert_deliveries") == 0


def test_test_channel_sends_after_a_typed_yes_and_only_then(world, capsys, monkeypatch):
    cfg = enable_all(world)
    net = Net()
    monkeypatch.setattr(ch, "_default_http_post", net.http_post)
    fake_tty(monkeypatch, answers=("n",))
    assert "not sent" in run(cfg, capsys, "alerts", "test-channel", "ntfy") and net.total() == 0
    fake_tty(monkeypatch, answers=("y",))
    o = run(cfg, capsys, "alerts", "test-channel", "ntfy")
    assert o.rstrip().endswith("sent") and len(net.posts) == 1 and net.posts[0]["body"].startswith("Coach: 2 new alerts (1 high).")
    rows = dbm.connect(cfg, insecure=True).execute("SELECT channel, what, ok FROM alert_deliveries").fetchall()
    assert rows == [("ntfy", "test", 1)]


def test_test_channel_reports_a_failed_send_without_the_secret(world, capsys, monkeypatch):
    cfg = enable_all(world)
    net = Net()
    net.fail = True
    monkeypatch.setattr(ch, "_default_http_post", net.http_post)
    fake_tty(monkeypatch, answers=("y",))
    o = run(cfg, capsys, "alerts", "test-channel", "telegram", expect_exit=True)
    assert "the send failed: OSError" in o and "TOKEN" not in o


def test_test_channel_with_a_missing_secret_is_not_ready(world, capsys):
    cfg = enable_all(world)
    secrets.delete_secret("telegram_bot_token")
    o = run(cfg, capsys, "alerts", "test-channel", "telegram", expect_exit=True)
    assert "channel not ready: secret 'telegram_bot_token' not set" in o


# ---------------------------------------------------------------- digest

def test_digest_prints_the_markdown_and_stores_nothing(world, capsys):
    o = run(world, capsys, "alerts", "digest")
    assert o.startswith("# Weekly summary, ") and "## Savings tracker" in o and count(world, "insights") == 0


def test_digest_save_once_a_week_and_force(world, capsys):
    assert "saved to the in-app feed as cin_" in run(world, capsys, "alerts", "digest", "--save")
    assert "not saved: this week's digest already exists" in run(world, capsys, "alerts", "digest", "--save")
    assert "saved to the in-app feed" in run(world, capsys, "alerts", "digest", "--save", "--force")
    assert count(world, "insights") == 2


def test_digest_save_send_dry_run_and_real(world, capsys, monkeypatch):
    cfg = enable_all(world)
    o = run(cfg, capsys, "alerts", "digest", "--save", "--send", "--dry-run")
    assert "not saved" in o and "channel ntfy: would_send" in o and "your weekly summary is ready" in o and count(cfg, "insights") == 0
    fake_tty(monkeypatch, answers=())
    net = fake_net(monkeypatch)
    o = run(cfg, capsys, "alerts", "digest", "--save", "--send")
    assert "channel telegram: sent" in o and net.total() == 4 and count(cfg, "insights") == 1


def test_digest_send_needs_save(world, capsys):
    assert "--send needs --save" in run(world, capsys, "alerts", "digest", "--send", expect_exit=True)


def test_digest_json(world, capsys):
    d = js(world, capsys, "alerts", "digest")
    assert {"spent_c", "usual_c", "top_categories", "upcoming", "alerts", "budgets", "savings"} <= set(d) and "markdown" not in d


def test_outside_a_terminal_a_real_check_stores_but_sends_nothing(world, capsys, monkeypatch):
    cfg = enable_all(world)
    net = fake_net(monkeypatch)                                       # no fake_tty: stdin / stdout are not a terminal
    o = run(cfg, capsys, "alerts", "check")
    assert "not interactive" in o and "channel " not in o and net.total() == 0
    assert count(cfg, "alert_events") > 0 and count(cfg, "alert_deliveries") == 0


def test_outside_a_terminal_digest_send_does_not_send(world, capsys, monkeypatch):
    cfg = enable_all(world)
    net = fake_net(monkeypatch)
    o = run(cfg, capsys, "alerts", "digest", "--save", "--send")
    assert "not interactive" in o and net.total() == 0 and count(cfg, "insights") == 1


def test_config_show_and_channels_warn_about_a_foreign_app_url(world, capsys):
    world.config_path.write_text(world.config_path.read_text() + '\n[alerts]\napp_url = "https://mac.example.org/"\n')
    cfg = load_config(world.config_path, env={})
    assert "WARNING" in run(cfg, capsys, "alerts", "channels") and "mac.example.org" in run(cfg, capsys, "config", "show")
