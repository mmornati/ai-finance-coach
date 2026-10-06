from datetime import datetime, timedelta, timezone

import pytest

from coach import notify, schedule as sch
from coach.cli import main
from coach.db import connect
from coach.ingest import consent as cm
from coach.ingest.client import ApiError
from helpers import FakeClient, add_bank, eb_tx

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def until(days, hours=0):
    return (NOW + timedelta(days=days, hours=hours)).isoformat()


@pytest.mark.parametrize("delta,status,left", [
    (timedelta(days=60), "ok", 60),
    (timedelta(days=15, minutes=1), "ok", 15),
    (timedelta(days=14, hours=23), "expiring", 14),     # floor(days) <= 14
    (timedelta(days=14), "expiring", 14),
    (timedelta(days=4, hours=1), "expiring", 4),
    (timedelta(days=3, hours=23), "urgent", 3),
    (timedelta(days=3), "urgent", 3),
    (timedelta(hours=5), "urgent", 0),
    (timedelta(seconds=-1), "expired", -1),
    (timedelta(days=-9), "expired", -9),
])
def test_status_thresholds(delta, status, left):
    assert cm.classify((NOW + delta).isoformat(), "active", None, NOW) == (status, left)


def test_status_revoked_expired_and_replaced_override_dates():
    far = until(100)
    assert cm.classify(far, "revoked", None, NOW)[0] == "revoked"
    assert cm.classify(far, "active", "REVOKED", NOW)[0] == "revoked"
    assert cm.classify(far, "active", "CLOSED", NOW)[0] == "revoked"
    assert cm.classify(far, "active", "EXPIRED", NOW)[0] == "expired"
    assert cm.classify(far, "replaced", None, NOW)[0] == "replaced"
    assert cm.classify("2027-04-02T09:22:10.525544Z", "active", "AUTHORIZED", NOW)[0] == "ok"   # 'Z' suffix
    assert cm.classify(None, "active", None, NOW) == ("ok", None)


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s-ok", "Bank Ok", "FR", [("a1", "FR7611111111111111111111111", "A1")], until(90))
    add_bank(c, "s-warn", "Bank Warn", "FR", [("a2", "FR7622222222222222222222222", "A2")], until(10))
    add_bank(c, "s-urg", "Bank Urgent", "IT", [("a3", "IT60X0542811101000000123456", "A3")], until(2))
    add_bank(c, "s-exp", "Bank Expired", "FR", [("a4", "FR7633333333333333333333333", "A4")], until(-1))
    c.execute("UPDATE sessions SET status='replaced' WHERE session_id='s-exp'")      # becomes history below
    c.execute("UPDATE sessions SET status='active' WHERE session_id='s-exp'")
    return c


def test_list_consents_levels_and_replaced_hidden(con):
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, status, replaced_by)"
                " VALUES ('s-old','Bank Ok','FR',?, 't','replaced','s-ok')", (until(5),))
    got = {c.session_id: (c.status, c.days_left) for c in cm.list_consents(con, NOW)}
    assert got == {"s-ok": ("ok", 90), "s-warn": ("expiring", 10), "s-urg": ("urgent", 2), "s-exp": ("expired", -1)}
    assert "s-old" in {c.session_id for c in cm.list_consents(con, NOW, include_replaced=True)}


def test_refresh_live_stores_status_and_valid_until_and_isolates_failures(con):
    fc = FakeClient(live={"s-ok": {"status": "REVOKED"},
                          "s-warn": ApiError(500, "boom"),
                          "s-urg": {"status": "AUTHORIZED", "access": {"valid_until": until(40)}}})
    res = cm.refresh_live(con, fc, out=lambda *_: None)
    assert {r["session_id"]: r["ok"] for r in res} == {"s-ok": True, "s-warn": False, "s-urg": True, "s-exp": True}
    got = {c.session_id: c.status for c in cm.list_consents(con, NOW)}
    assert got["s-ok"] == "revoked" and got["s-urg"] == "ok"
    assert got["s-warn"] == "unknown"                  # live check failed (error 500): not vouched for, date says 10d
    assert con.execute("SELECT live_status FROM sessions WHERE session_id='s-warn'").fetchone()[0] == "error 500"


def test_sync_refreshes_live_status_and_skips_revoked_bank(con):
    from coach.ingest.sync import sync_all
    fc = FakeClient({"a1": [{"transactions": [eb_tx("r1", "2026-10-01", -5, "X")]}]},
                    live={"s-warn": {"status": "REVOKED"}})
    res = {r["uid"]: r for r in sync_all(con, fc, out=lambda *_: None)}
    assert res["a1"]["status"] == "ok"
    assert res["a2"]["status"] == "failed" and "revoked" in res["a2"]["note"]
    assert res["a4"]["status"] == "failed" and "expired" in res["a4"]["note"]
    assert not any(c[1].startswith("/accounts/a2") for c in fc.calls)       # no bank call burned on a dead consent


class _FrozenDatetime(datetime):
    """`datetime` whose now() is the fixed NOW: the CLI reads the wall clock, the fixture dates are relative to NOW."""
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def test_consents_command_lists_and_exits_nonzero_on_expired(cfg, con, capsys, monkeypatch):
    monkeypatch.setattr(cm, "datetime", _FrozenDatetime)
    con.commit()
    con.close()
    with pytest.raises(SystemExit) as e:
        main(["--config", str(cfg.config_path), "--insecure", "consents"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    for s in ("Bank Ok", "ok", "expiring", "urgent", "expired", "reconnect"):
        assert s in out


def test_consents_command_ok_exit_zero_and_no_network(cfg, tmp_path, capsys, monkeypatch):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Bank Ok", "FR", [("a1", "FR76", "A1")], "2099-01-01T00:00:00+00:00")
    c.close()
    monkeypatch.setattr("coach.ingest.client.requests.request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    main(["--config", str(cfg.config_path), "--insecure", "consents"])           # returns normally = exit 0
    assert "Bank Ok" in capsys.readouterr().out


# ---- schedule: warnings, alerts once, non-zero on expired, notification via injected runner only

def test_notify_builds_argv_and_never_interpolates(monkeypatch):
    seen = []
    ok = notify.notify_macos('msg "with" quotes; do shell script "x"', "T",
                             runner=lambda cmd, **kw: seen.append(cmd) or type("R", (), {"returncode": 0})())
    assert ok and seen[0][0] == "osascript"
    assert seen[0][-2:] == ['msg "with" quotes; do shell script "x"', "T"]                # passed as argv
    assert all('do shell script' not in part for part in seen[0][:-2])


def test_default_runner_is_blocked_in_tests():
    with pytest.raises(AssertionError, match="must not run"):
        notify.notify_macos("x")


def sched_setup(cfg, valid_until):
    cfg.notify_macos = True
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Bank X", "FR", [("a1", "FR76", "A1")], valid_until)
    c.close()
    return FakeClient({"a1": [{"transactions": [eb_tx("r1", "2026-10-01", -5, "CARTE 01/10 X")]}] * 3})


def test_schedule_logs_warning_notifies_once_per_threshold(cfg, monkeypatch):
    client = sched_setup(cfg, (datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    monkeypatch.setattr("coach.classify.llm.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    sent = []
    out = []
    assert sch.run_daily(cfg, insecure=True, client=client, out=out.append, notifier=lambda m: sent.append(m) or True) == 0
    assert any("WARNING" in o and "EXPIRING" in o for o in out)
    assert len(sent) == 1 and "Bank X" in sent[0]
    sch.run_daily(cfg, insecure=True, client=client, out=lambda *_: None, notifier=lambda m: sent.append(m) or True)
    assert len(sent) == 1                                                  # same threshold: not notified again
    log = (cfg.log_dir / "schedule.log").read_text()
    assert "consents: warnings=1" in log and "EXPIRING" in log


def test_schedule_d3_is_a_new_threshold(cfg):
    client = sched_setup(cfg, (datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    sent = []
    sch.run_daily(cfg, insecure=True, client=client, out=lambda *_: None, notifier=lambda m: sent.append(m) or True)
    c = connect(cfg, insecure=True)
    c.execute("UPDATE sessions SET valid_until=?", ((datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),))
    c.commit()
    c.close()
    sch.run_daily(cfg, insecure=True, client=client, out=lambda *_: None, notifier=lambda m: sent.append(m) or True)
    assert len(sent) == 2 and "URGENT" in sent[1]


def test_schedule_exits_nonzero_when_consent_expired(cfg):
    client = sched_setup(cfg, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    cfg.notify_macos = False
    assert sch.run_daily(cfg, insecure=True, client=client, out=lambda *_: None) == 1
    assert "FAILED" in (cfg.log_dir / "schedule.log").read_text()


def test_notifications_disabled_by_default(cfg):
    assert cfg.notify_macos is False
    client = sched_setup(cfg, (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
    cfg.notify_macos = False
    sent = []
    sch.run_daily(cfg, insecure=True, client=client, out=lambda *_: None, notifier=lambda m: sent.append(m))
    assert sent == []


# ---- item 5: only AUTHORIZED is live-ok

@pytest.mark.parametrize("live,days,want", [
    ("AUTHORIZED", 90, "ok"), ("AUTHORIZED", 10, "expiring"), ("error 404", 90, "unknown"),
    ("error 401", 90, "unknown"), ("error 403", 90, "unknown"), ("PENDING_AUTHORIZATION", 90, "unknown"),
    ("SOMETHING_NEW", 90, "unknown"), ("error 404", 2, "urgent"), ("error 404", -1, "expired"),
    ("REVOKED", 90, "revoked"), ("EXPIRED", 90, "expired"), (None, 90, "ok")])
def test_only_authorized_is_live_ok(live, days, want):
    assert cm.classify((NOW + timedelta(days=days, hours=1)).isoformat(), "active", live, NOW)[0] == want


def test_unknown_consent_is_amber_in_health_and_warns_but_does_not_fail_the_job(cfg):
    from coach.ingest import health as hm
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Bank", "FR", [("a1", "FR76", "A1")], until(90))
    fc = FakeClient(live={"s1": ApiError(404, "no such session")})
    cm.refresh_live(c, fc, out=lambda *_: None)
    con_ = cm.list_consents(c, NOW)[0]
    assert con_.status == "unknown" and "UNKNOWN" in cm.describe(con_) and cm.warnings(c, NOW)
    c.execute("INSERT INTO sync_log VALUES ('a1', ?, 1, 0, 1, 'x')", (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    rep = hm.health(c, 4, 2)
    a = rep.banks[0].accounts[0]
    assert a.level == "amber" and rep.ok and any("unknown" in p for p in a.problems)
    fc = FakeClient(live={"s1": {"status": "AUTHORIZED"}})
    cm.refresh_live(c, fc, out=lambda *_: None)
    assert cm.list_consents(c, NOW)[0].status == "ok"
