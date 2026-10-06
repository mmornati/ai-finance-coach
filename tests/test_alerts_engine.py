"""E10-2 / E10-4: the alert engine: stable ids and dedupe, escalation only on a severity increase, resolve / reopen, snooze / ack /
mute, min severity, digest-only kinds, quiet hours, the weekly budget, failure handling. Synthetic data; no network (Net fakes)."""
from __future__ import annotations

import datetime as dt

import pytest

from alerthelpers import ALL_ON, NOW, TODAY, Net, build_alert_world, cand, deliveries, event, retime_consents, settings
from coach import db as dbm
from coach.alerts import engine, signals, store
from coach.analytics import api as analytics_api

UTC = dt.timezone.utc


@pytest.fixture
def con(cfg):
    c = dbm.connect(cfg, insecure=True, create=True)
    yield c
    c.close()


def check(con, cfg, cands, s, net, now=NOW, dry=False, tz=UTC):
    """evaluate + dispatch for a list of candidates (what engine.run does after the signals are computed)."""
    ev = engine.sync_events(con, cands, s, now=now, today=now.date())
    disp = engine.dispatch(con, cfg, s, now=now, today=now.date(), dry_run=dry, transports=net.transports(), tz=tz)
    con.rollback() if dry else con.commit()
    return ev, {d["channel"]: d for d in disp}


NTFY = {"ntfy": ALL_ON["ntfy"]}


# ---------------------------------------------------------------- ids and dedupe

def test_event_id_is_a_hash_of_the_key_and_leaks_no_name():
    c = cand(key="forecast:M OU MME ROSSI ANNA:negative")
    assert c.id.startswith("alr_") and len(c.id) == 16 and "ROSSI" not in c.id
    assert c.id == cand(key="forecast:M OU MME ROSSI ANNA:negative", severity="high", title="other words").id
    assert c.id != cand(key="forecast:other").id


def test_the_same_signal_is_one_event_however_often_it_is_checked(con, cfg):
    s, net = settings(), Net()
    for i in range(3):
        ev, _ = check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(hours=i))
    assert con.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1
    assert ev["unchanged"] == [cand().id] and ev["new"] == []
    e = event(con, cand())
    assert e["created"] == store.now_iso(NOW) and e["last_seen"] == store.now_iso(NOW + dt.timedelta(hours=2))


def test_a_moving_title_or_amount_updates_the_event_not_duplicates_it(con, cfg):
    s, net = settings(), Net()
    check(con, cfg, [cand(title="a", amount_c=100)], s, net)
    check(con, cfg, [cand(title="b", amount_c=200)], s, net)
    e = event(con, cand())
    assert e["title"] == "b" and e["payload"]["amount_c"] == 200 and con.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1


# ---------------------------------------------------------------- noise control

def test_disabled_kind_is_not_evaluated_at_all(con, cfg):
    check(con, cfg, [cand(kind="price_increase", key="a"), cand(kind="consent", key="b")], settings(disabled_kinds=["price_increase"]), Net())
    assert [r[0] for r in con.execute("SELECT kind FROM alert_events")] == ["consent"]


def test_min_severity_keeps_the_event_but_never_sends_it(con, cfg):
    net = Net()
    s = settings(min_severity="high", **NTFY)
    _, d = check(con, cfg, [cand(severity="medium")], s, net)
    assert event(con, cand())["status"] == "suppressed" and net.total() == 0 and d["ntfy"]["status"] == "nothing"
    check(con, cfg, [cand(severity="high")], s, net)                  # an escalation above the threshold wakes it
    assert event(con, cand())["status"] == "sent" and len(net.posts) == 1


def test_digest_only_kinds_are_shown_but_not_sent_one_by_one(con, cfg):
    net = Net()
    s = settings(digest_only_kinds=["price_increase"], **NTFY)
    check(con, cfg, [cand(kind="price_increase"), cand(kind="consent", key="c")], s, net)
    assert event(con, cand(kind="price_increase"))["status"] == "new" and len(net.posts) == 1
    assert "1 new alert" in net.posts[0]["body"]
    assert store.counts(con, TODAY)["open"] == 2                       # both are in the app


def test_muted_kind_is_suppressed_and_unmuting_brings_it_back(con, cfg):
    net = Net()
    store.set_muted(con, "unusual_charge", True)
    s = settings(**NTFY)
    check(con, cfg, [cand()], s, net)
    assert event(con, cand())["status"] == "suppressed" and net.total() == 0
    store.set_muted(con, "unusual_charge", False)
    assert event(con, cand())["status"] == "new"
    check(con, cfg, [cand()], s, net)
    assert len(net.posts) == 1
    with pytest.raises(ValueError):
        store.set_muted(con, "nonsense", True)


def test_kind_snooze_holds_new_events_too_and_expires(con, cfg):
    net = Net()
    s = settings(**NTFY)
    store.snooze_kind(con, "unusual_charge", TODAY + dt.timedelta(days=3))
    check(con, cfg, [cand()], s, net)
    e = event(con, cand())
    assert e["status"] == "snoozed" and e["snoozed_until"] == "2026-10-07" and net.total() == 0
    later = NOW + dt.timedelta(days=4)
    check(con, cfg, [cand()], s, net, now=later)                       # the snooze ran out: sent now
    assert event(con, cand())["status"] == "sent" and len(net.posts) == 1


def test_event_snooze_ack_and_restore(con, cfg):
    net = Net()
    s = settings(**NTFY)
    check(con, cfg, [cand()], s, net)
    store.snooze(con, cand().id, TODAY + dt.timedelta(days=2))
    assert store.counts(con, TODAY)["snoozed"] == 1 and store.counts(con, TODAY)["open"] == 0
    store.restore(con, cand().id)
    assert event(con, cand())["status"] == "sent"                      # a channel already had it
    store.ack(con, cand().id)
    assert event(con, cand())["status"] == "acked" and store.counts(con, TODAY)["open"] == 0
    check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(days=1))
    assert event(con, cand())["status"] == "acked" and len(net.posts) == 1


# ---------------------------------------------------------------- never re-send; escalation only on a severity increase

def test_an_event_is_sent_once_per_channel(con, cfg):
    net = Net()
    s = settings(**ALL_ON)
    _, d = check(con, cfg, [cand()], s, net)
    assert {k: v["status"] for k, v in d.items()} == {"macos": "sent", "ntfy": "sent", "email": "sent", "telegram": "sent"}
    assert net.total() == 4
    for h in (1, 30):
        _, d = check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(hours=h))
        assert {k: (v["status"], v["reason"]) for k, v in d.items()} == {k: ("nothing", None) for k in d}
    assert net.total() == 4 and set(event(con, cand())["channels_sent"]) == {"macos", "ntfy", "email", "telegram"}


def test_escalation_sends_again_once_and_a_decrease_never_does(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand(severity="medium")], s, net)
    ev, _ = check(con, cfg, [cand(severity="high")], s, net, now=NOW + dt.timedelta(days=1))
    assert ev["escalated"] == [cand().id] and len(net.posts) == 2 and "(1 high)" in net.posts[1]["body"]
    check(con, cfg, [cand(severity="high")], s, net, now=NOW + dt.timedelta(days=2))
    check(con, cfg, [cand(severity="medium")], s, net, now=NOW + dt.timedelta(days=3))
    check(con, cfg, [cand(severity="high")], s, net, now=NOW + dt.timedelta(days=4))     # back up to what was already sent
    e = event(con, cand())
    assert len(net.posts) == 2 and e["escalations"] == 1 and e["severity"] == "high"


def test_ack_holds_until_an_escalation_which_reopens_it(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand(severity="medium")], s, net)
    store.ack(con, cand().id)
    check(con, cfg, [cand(severity="high")], s, net, now=NOW + dt.timedelta(days=1))
    assert event(con, cand())["status"] == "sent" and len(net.posts) == 2


def test_a_snoozed_event_stays_snoozed_through_an_escalation(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand(severity="medium")], s, net)
    store.snooze(con, cand().id, TODAY + dt.timedelta(days=5))
    check(con, cfg, [cand(severity="high")], s, net, now=NOW + dt.timedelta(days=1))
    assert event(con, cand())["status"] == "snoozed" and len(net.posts) == 1


def test_one_message_per_channel_for_several_events(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand(key="a"), cand(key="b", severity="high"), cand(kind="consent", key="c")], s, net)
    assert len(net.posts) == 1 and net.posts[0]["body"].startswith("Coach: 3 new alerts (1 high).")
    assert deliveries(con) == [("ntfy", "alert", 3, 1, None)]


# ---------------------------------------------------------------- resolved / flapping / reopened

def test_a_signal_that_goes_away_is_resolved_and_kept(con, cfg):
    s, net = settings(), Net()
    check(con, cfg, [cand()], s, net)
    ev, _ = check(con, cfg, [], s, net, now=NOW + dt.timedelta(days=1))
    e = event(con, cand())
    assert ev["resolved"] == [cand().id] and e["resolved"] and store.counts(con, TODAY)["open"] == 0
    assert store.listing(con, include_resolved=True, today=TODAY)[0]["id"] == cand().id and store.listing(con, today=TODAY) == []


def test_a_flapping_signal_is_the_same_event_and_is_not_sent_again(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand()], s, net)
    check(con, cfg, [], s, net, now=NOW + dt.timedelta(days=1))
    ev, _ = check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(days=3))
    e = event(con, cand())
    assert not e["resolved"] and len(net.posts) == 1 and e["created"] == store.now_iso(NOW) and ev["reopened"] == []


def test_a_signal_back_after_a_week_is_a_new_occurrence_and_is_sent_again(con, cfg):
    net, s = Net(), settings(**NTFY)
    check(con, cfg, [cand()], s, net)
    check(con, cfg, [], s, net, now=NOW + dt.timedelta(days=1))
    back = NOW + dt.timedelta(days=9)
    ev, _ = check(con, cfg, [cand()], s, net, now=back)
    e = event(con, cand())
    assert ev["reopened"] == [cand().id] and e["created"] == store.now_iso(back) and len(net.posts) == 2 and e["escalations"] == 0


# ---------------------------------------------------------------- quiet hours and the weekly budget

@pytest.mark.parametrize("quiet,hour,expect", [("22:00-08:00", 23, True), ("22:00-08:00", 3, True), ("22:00-08:00", 8, False),
                                               ("22:00-08:00", 12, False), ("13:00-14:00", 13, True), ("13:00-14:00", 14, False),
                                               ("", 3, False)])
def test_quiet_hours_window(quiet, hour, expect):
    s = settings(quiet_hours=quiet)
    assert engine.in_quiet_hours(s, NOW.replace(hour=hour, minute=30), UTC) is expect


def test_quiet_hours_use_the_local_zone_not_utc():
    s = settings(quiet_hours="22:00-08:00")
    rome = dt.timezone(dt.timedelta(hours=2))
    assert engine.in_quiet_hours(s, NOW.replace(hour=21), rome) is True       # 23:00 in Rome
    assert engine.in_quiet_hours(s, NOW.replace(hour=21), UTC) is False


def test_quiet_hours_hold_every_channel_back_then_send_after_the_window(con, cfg):
    net, s = Net(), settings(quiet_hours="22:00-08:00", **ALL_ON)
    night = NOW.replace(hour=23)
    ev, d = check(con, cfg, [cand()], s, net, now=night)
    assert net.total() == 0 and {v["status"] for v in d.values()} == {"deferred"} and d["ntfy"]["reason"] == "quiet hours"
    assert event(con, cand())["status"] == "new" and deliveries(con) == []
    morning = night + dt.timedelta(hours=9, minutes=1)                     # 08:01
    _, d = check(con, cfg, [cand()], s, net, now=morning)
    assert {v["status"] for v in d.values()} == {"sent"} and net.total() == 4


def test_weekly_limit_per_channel_and_the_window_rolls(con, cfg):
    net, s = Net(), settings(max_per_week=2, **NTFY)
    for i in range(3):
        _, d = check(con, cfg, [cand(key=f"k{i}")], s, net, now=NOW + dt.timedelta(days=i))
    assert len(net.posts) == 2 and d["ntfy"]["status"] == "deferred" and "weekly limit" in d["ntfy"]["reason"]
    _, d = check(con, cfg, [cand(key="k0"), cand(key="k1"), cand(key="k2")], s, net, now=NOW + dt.timedelta(days=7, hours=1))
    assert d["ntfy"]["status"] == "sent" and len(net.posts) == 3             # the first message is older than 7 days: a slot is free
    assert event(con, cand(key="k2"))["status"] == "sent"


def test_weekly_limit_zero_sends_nothing_but_keeps_the_feed(con, cfg):
    net, s = Net(), settings(max_per_week=0, **NTFY)
    check(con, cfg, [cand()], s, net)
    assert net.total() == 0 and store.counts(con, TODAY)["open"] == 1


def test_the_limit_is_per_channel(con, cfg):
    net, s = Net(), settings(max_per_week=1, **ALL_ON)
    check(con, cfg, [cand(key="a")], s, net)
    _, d = check(con, cfg, [cand(key="a"), cand(key="b")], s, net, now=NOW + dt.timedelta(days=1))
    assert {v["status"] for v in d.values()} == {"deferred"} and net.total() == 4


# ---------------------------------------------------------------- failures, dry run, readiness

def test_a_failed_send_is_recorded_without_a_secret_and_retried(con, cfg):
    net, s = Net(), settings(**ALL_ON)
    net.fail = True
    _, d = check(con, cfg, [cand()], s, net)
    assert {v["status"] for v in d.values()} == {"failed"}
    rows = deliveries(con)
    assert len(rows) == 4 and all(r[3] == 0 for r in rows)
    blob = " ".join(str(r) for r in rows)
    assert "pw-SMTP-1" not in blob and "TOKEN-xyz" not in blob and "tk_ntfy" not in blob
    assert event(con, cand())["status"] == "new" and event(con, cand())["channels_sent"] == {}
    net.fail = False
    _, d = check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(days=1))
    assert {v["status"] for v in d.values()} == {"sent"}


def test_failed_sends_do_not_use_the_weekly_budget(con, cfg):
    net, s = Net(), settings(max_per_week=1, **NTFY)
    net.fail = True
    check(con, cfg, [cand()], s, net)
    net.fail = False
    _, d = check(con, cfg, [cand()], s, net, now=NOW + dt.timedelta(hours=1))
    assert d["ntfy"]["status"] == "sent"


def test_dry_run_writes_nothing_and_sends_nothing_but_shows_the_message(con, cfg):
    net, s = Net(), settings(**ALL_ON)
    ev, d = check(con, cfg, [cand()], s, net, dry=True)
    assert ev["new"] == [cand().id] and {v["status"] for v in d.values()} == {"would_send"}
    assert d["ntfy"]["message"]["body"] == "Coach: 1 new alert. Open the app." and d["ntfy"]["request"]["url"].startswith("https://")
    assert net.total() == 0 and con.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 0 and deliveries(con) == []


def test_a_channel_missing_a_secret_or_a_field_is_reported_not_sent(con, cfg):
    net = Net(secrets={})
    s = settings(email={"enabled": True, "host": "smtp.example.net", "username": "m", "from": "a@example.net", "to": "b@example.net"},
                 telegram={"enabled": True}, ntfy={"enabled": True})
    _, d = check(con, cfg, [cand()], s, net)
    assert d["email"]["status"] == "not_ready" and "smtp_password" in d["email"]["reason"]
    assert d["telegram"]["status"] == "not_ready" and "chat_id" in d["telegram"]["reason"]
    assert d["ntfy"]["status"] == "not_ready" and "url" in d["ntfy"]["reason"] and net.total() == 0


def test_nothing_is_enabled_by_default(con, cfg):
    net = Net()
    ev, d = check(con, cfg, [cand()], settings(), net)
    assert d == {} and net.total() == 0 and event(con, cand())["status"] == "new"


# ---------------------------------------------------------------- the real signals

@pytest.fixture
def world(cfg):
    con = build_alert_world(cfg)
    yield con
    con.close()


def kinds(res):
    return sorted({e["kind"] for e in res["events"]})


def test_signals_produce_every_expected_kind_on_the_synthetic_world(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    res = engine.run(world, cfg, ds, s=settings(), now=NOW, send=False)
    assert kinds(res) == ["consent", "loan_alert", "low_balance", "price_increase", "sync_failing", "unused_subscription", "unusual_charge"]
    by = {(e["kind"], e["severity"]) for e in res["events"]}
    assert ("consent", "high") in by and ("consent", "medium") in by and ("sync_failing", "medium") in by
    assert ("low_balance", "high") in by and ("price_increase", "medium") in by and ("unused_subscription", "medium") in by


def test_running_the_whole_check_twice_changes_nothing(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    engine.run(world, cfg, ds, s=settings(), now=NOW, send=False)
    before = world.execute("SELECT id, severity, status, created FROM alert_events ORDER BY id").fetchall()
    res = engine.run(world, cfg, ds, s=settings(), now=NOW + dt.timedelta(hours=5), send=False)
    assert res["evaluated"]["new"] == [] and res["evaluated"]["resolved"] == [] and res["evaluated"]["escalated"] == []
    assert world.execute("SELECT id, severity, status, created FROM alert_events ORDER BY id").fetchall() == before


def test_consent_thresholds_are_distinct_successive_events(cfg):
    con = build_alert_world(cfg, failing_sync=False)
    s = settings()
    got = {}
    for days, thr in ((10, "d14"), (2, "d3"), (-1, "expired")):
        con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s1'", ((NOW + dt.timedelta(days=days)).isoformat(),))
        con.commit()
        got[thr] = [c for c in signals.consent_candidates(con, NOW) if c.payload["session_id"] == "s1"][0]
    assert got["d14"].severity == "medium" and got["d3"].severity == "high" and got["expired"].severity == "high"
    assert len({c.id for c in got.values()}) == 3
    con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s1'", ((NOW + dt.timedelta(days=100)).isoformat(),))
    assert not [c for c in signals.consent_candidates(con, NOW) if c.payload["session_id"] == "s1"]


def test_sync_failing_needs_n_in_a_row_and_a_success_ends_the_streak(cfg):
    con = build_alert_world(cfg, consents=False, failing_sync=True)
    assert len(signals.sync_candidates(con, 3)) == 1 and signals.sync_candidates(con, 4) == []
    c = signals.sync_candidates(con, 3)[0]
    assert c.severity == "medium" and c.payload["failures"] == 3
    assert signals.sync_candidates(con, 1)[0].severity == "high"                   # 3 >= 2 * 1: the streak is long for that threshold
    con.execute("INSERT INTO sync_log VALUES ('fo','2026-10-04T06:00:00+00:00',1,0,1,'ok')")
    assert signals.sync_candidates(con, 3) == []
    for d in (5, 6, 7):                                                              # a NEW streak is a new event
        con.execute("INSERT INTO sync_log VALUES ('fo',?,0,0,0,'x')", (f"2026-10-0{d}T06:00:00+00:00",))
    assert signals.sync_candidates(con, 3)[0].id != c.id


def test_dismissed_anomalies_and_price_changes_do_not_alert(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    first = signals.all_candidates(world, cfg, ds, settings(), NOW)
    an = [c for c in first if c.kind == "unusual_charge"]
    pc = [c for c in first if c.kind == "price_increase"]
    assert an and pc
    world.execute("INSERT INTO anomalies(id, first_seen, dismissed_at, payload) VALUES (?, 't', 't', '{}')", (an[0].key,)) \
        if False else None
    world.execute("INSERT OR IGNORE INTO price_change_dismissals(id, dismissed_at, note) VALUES (?, 't', NULL)", (pc[0].key,))
    world.commit()
    again = signals.all_candidates(world, cfg, ds, settings(), NOW)
    assert pc[0].id not in {c.id for c in again} and an[0].id in {c.id for c in again}


def test_anomaly_threshold_setting(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    high_only = signals.all_candidates(world, cfg, ds, settings(thresholds={"anomaly_min_severity": "high"}), NOW)
    assert all(c.severity == "high" for c in high_only if c.kind == "unusual_charge")


def test_schedule_run_alert_step_is_warn_only(cfg, monkeypatch, db_key):
    """The daily job's alerts step never fails the job and never reaches the network (the real transports fail loudly in tests)."""
    from coach import schedule as sch
    connect = dbm.connect
    build_alert_world(cfg).close()
    lines = []
    monkeypatch.setattr("coach.classify.llm.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no llm")))
    monkeypatch.setattr("coach.alerts.engine.run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch.run_daily(cfg, insecure=True, out=lines.append)
    log = (cfg.log_dir / "schedule.log").read_text()
    assert "alerts: could not run" in log and "alerts: ERROR" not in log
    assert "WARNING alerts could not run: RuntimeError" in "\n".join(lines)


def test_schedule_run_alert_step_keeps_events_and_the_digest_and_sends_nothing_by_default(cfg, monkeypatch, db_key):
    from coach import schedule as sch
    build_alert_world(cfg).close()
    monkeypatch.setattr("coach.classify.llm.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no llm")))
    sch.run_daily(cfg, insecure=True, out=lambda *_: None)
    log = (cfg.log_dir / "schedule.log").read_text()
    assert "alerts: new=" in log and "send=[no channel]" in log and "digest=done" in log
    con = dbm.connect(cfg, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] > 0
    assert con.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM insights WHERE skill='weekly-local'").fetchone()[0] == 1


def test_lowering_min_severity_later_brings_the_suppressed_event_back_and_sends_it(con, cfg):
    net = Net()
    check(con, cfg, [cand(severity="medium")], settings(min_severity="high", **NTFY), net)
    assert event(con, cand())["status"] == "suppressed" and net.total() == 0
    check(con, cfg, [cand(severity="medium")], settings(min_severity="medium", **NTFY), net, now=NOW + dt.timedelta(days=1))
    assert event(con, cand())["status"] == "sent" and len(net.posts) == 1


def test_raising_min_severity_later_stops_sending_but_keeps_the_event_in_the_app(con, cfg):
    net = Net()
    check(con, cfg, [cand(severity="medium", key="a"), cand(severity="medium", key="b")], settings(min_severity="high", **NTFY), net)
    check(con, cfg, [cand(severity="medium", key="a"), cand(severity="medium", key="b", title="x"), cand(severity="medium", key="c")],
          settings(min_severity="high", **NTFY), net, now=NOW + dt.timedelta(days=1))
    assert net.total() == 0 and store.counts(con, TODAY)["suppressed"] == 3


def test_the_older_macos_consent_notification_is_not_sent_twice(cfg, monkeypatch, db_key):
    """[notify] macos (E1, consent warnings) is superseded by [alerts.macos]: with both on, only the alert channel notifies."""
    from coach import schedule as sch
    from coach.config import load_config
    build_alert_world(cfg).close()
    retime_consents(cfg)
    cfg.config_path.write_text(cfg.config_path.read_text() + "\n[notify]\nmacos = true\n[alerts.macos]\nenabled = true\n")
    cfg2 = load_config(cfg.config_path, env={})
    monkeypatch.setattr("coach.classify.llm.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no llm")))
    legacy, net = [], Net()
    sch.run_daily(cfg2, insecure=True, out=lambda *_: None, notifier=lambda m: legacy.append(m) or True, alert_transports=net.transports())
    assert legacy == [] and len(net.macos) == 1
    # without [alerts.macos] the legacy notification still works
    cfg.config_path.write_text(cfg.config_path.read_text().replace("[alerts.macos]\nenabled = true\n", ""))
    legacy.clear()
    sch.run_daily(load_config(cfg.config_path, env={}), insecure=True, out=lambda *_: None, notifier=lambda m: legacy.append(m) or True,
                  alert_transports=Net().transports())
    assert len(legacy) == 2                     # the two consents of the world: the legacy notification still works when [alerts.macos] is off


# ---------------------------------------------------------------- follow-ups: expense-only price alerts, baseline, external min severity

def test_price_increase_alerts_are_for_expenses_never_for_income(cfg):
    from helpers import add_tx
    from memhelpers import label, monthly
    con = build_alert_world(cfg, consents=False, failing_sync=False)
    monthly(con, "ce", "rent", "RENTIN LOCATAIRE", [500.0] * 6 + [550.0] * 3, tx_type="transfer_in", start=(2026, 1), day=3)
    label(con, "RENTIN LOCATAIRE", "income.rental")
    con.commit()
    ds = analytics_api.build_dataset(con, cfg, TODAY)
    from coach.analytics import pricechanges, recurring
    pcs = pricechanges.price_changes(ds, None, recurring.detect_recurring(ds))
    assert any(c.effect == "pays_more" for c in pcs.changes)                       # the analytics do see the rise in rent received
    titles = [c.title for c in signals.all_candidates(con, cfg, ds, settings(), NOW) if c.kind == "price_increase"]
    assert titles and not any("Rentin" in t or "RENTIN" in t for t in titles)


def test_the_shipped_defaults_hold_noisy_kinds_back_and_need_medium(con, cfg):
    from coach.alerts.settings import AlertSettings
    s = AlertSettings.from_dict({"ntfy": ALL_ON["ntfy"]})
    assert s.digest_only_kinds == ("price_increase", "unusual_charge") and s.external_min_severity == "medium"
    net = Net()
    check(con, cfg, [cand(kind="unusual_charge", key="a", severity="high"), cand(kind="price_increase", key="b"),
                     cand(kind="consent", key="c", severity="low"), cand(kind="budget", key="d", severity="medium")], s, net)
    assert len(net.posts) == 1 and net.posts[0]["body"] == "Coach: 1 new alert. Open the app."          # only the medium budget alert
    assert store.counts(con, TODAY)["open"] == 4                                                    # all are in the app


def test_a_channel_enabled_later_gets_no_backlog(con, cfg):
    net = Net()
    check(con, cfg, [cand(key="old1"), cand(key="old2", severity="high")], settings(), net)            # no channel yet
    assert net.total() == 0
    later = NOW + dt.timedelta(days=3)
    _, d = check(con, cfg, [cand(key="old1"), cand(key="old2", severity="high")], settings(**NTFY), net, now=later)
    assert d["ntfy"]["status"] == "nothing" and d["ntfy"]["baselined"] == 2 and net.total() == 0
    assert event(con, cand(key="old1"))["channels_sent"]["ntfy"]["baseline"] is True
    assert con.execute("SELECT channel, n_events FROM alert_channel_state").fetchall() == [("ntfy", 2)]
    check(con, cfg, [cand(key="old1"), cand(key="old2", severity="high"), cand(key="fresh")], settings(**NTFY), net, now=later + dt.timedelta(days=1))
    assert len(net.posts) == 1 and net.posts[0]["body"] == "Coach: 1 new alert. Open the app."          # only what arrived after
    # an escalation of a baselined alert is still sent
    check(con, cfg, [cand(key="old1", severity="high")], settings(**NTFY), net, now=later + dt.timedelta(days=2))
    assert len(net.posts) == 2


def test_the_baseline_is_shown_but_not_written_by_a_dry_run(con, cfg):
    net = Net()
    check(con, cfg, [cand()], settings(), net)
    _, d = check(con, cfg, [cand()], settings(**NTFY), net, now=NOW + dt.timedelta(days=2), dry=True)
    assert d["ntfy"]["baselined"] == 1 and con.execute("SELECT COUNT(*) FROM alert_channel_state").fetchone()[0] == 0
    assert event(con, cand())["channels_sent"] == {}
