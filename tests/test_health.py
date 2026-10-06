import json
from datetime import datetime, timedelta, timezone

import pytest

from coach.cli import main
from coach.db import connect
from coach.ingest import health as hm
from helpers import add_bank, add_tx

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def log(con, uid, when, ok=1, note="x"):
    con.execute("INSERT INTO sync_log VALUES (?,?,?,?,?,?)", (uid, iso(when), ok, 0, 1, note))
    con.commit()


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Bank", "FR", [("a1", "FR7600000000000000000000001", "ONE")], (NOW + timedelta(days=90)).isoformat())
    return c


def acct(rep, uid="a1"):
    return next(a for b in rep.banks for a in b.accounts if a.uid == uid)


def test_green_account_reports_everything(con):
    add_tx(con, "a1", "t1", "2026-08-01", -5, "X")
    add_tx(con, "a1", "t2", "2026-10-02", -6, "Y")
    con.execute("INSERT INTO pending_transactions VALUES ('a1','2026-10-03',-1,'EUR','','P','{}')")
    log(con, "a1", NOW - timedelta(hours=3))
    log(con, "a1", NOW - timedelta(hours=1))
    rep = hm.health(con, daily_limit=4, stale_days=2, now=NOW)
    a = acct(rep)
    assert rep.ok and rep.level == "green" and a.level == "green" and a.problems == []
    assert (a.tx_count, a.first_tx_date, a.last_tx_date, a.pending_count) == (2, "2026-08-01", "2026-10-02", 1)
    assert a.last_ok_sync == iso(NOW - timedelta(hours=1))
    assert (a.syncs_today, a.daily_limit, a.syncs_left_today) == (2, 4, 2)
    assert a.consent_status == "ok" and a.consent_days_left == 90
    assert rep.banks[0].consent_days_left == 90 and rep.banks[0].bank == "Bank"
    d = json.loads(rep.to_json())
    assert d["ok"] is True and d["banks"][0]["accounts"][0]["syncs_today"] == 2


def test_syncs_today_counts_failures_and_limit_is_shown(con):
    for i in range(5):
        log(con, "a1", NOW - timedelta(minutes=10 * i), ok=1 if i else 0, note="429")
    log(con, "a1", NOW - timedelta(days=1, hours=13))                    # yesterday: not counted
    a = acct(hm.health(con, 4, 2, NOW))
    assert a.syncs_today == 5 and a.syncs_left_today == 0


def test_never_synced_and_stale_are_amber_not_red(con):
    rep = hm.health(con, 4, 2, NOW)
    a = acct(rep)
    assert a.level == "amber" and "never synced" in a.problems[0] and rep.ok
    log(con, "a1", NOW - timedelta(days=2, hours=1))
    a = acct(hm.health(con, 4, 2, NOW))
    assert a.stale and a.level == "amber" and any("stale" in p for p in a.problems)
    log(con, "a1", NOW - timedelta(days=1, hours=23))
    assert not acct(hm.health(con, 4, 2, NOW)).stale


def test_latest_sync_failure_is_red_and_old_error_is_shown_but_resolved(con):
    log(con, "a1", NOW - timedelta(hours=5), ok=1)
    log(con, "a1", NOW - timedelta(hours=1), ok=0, note="HTTP 500: boom")
    rep = hm.health(con, 4, 2, NOW)
    a = acct(rep)
    assert not rep.ok and a.level == "red" and a.last_error == "HTTP 500: boom" and a.last_attempt_ok is False
    assert any("last sync failed" in p for p in a.problems)
    log(con, "a1", NOW - timedelta(minutes=5), ok=1)
    rep = hm.health(con, 4, 2, NOW)
    assert rep.ok and acct(rep).last_error == "HTTP 500: boom" and acct(rep).level == "green"
    assert "resolved" in hm.format_report(rep)


@pytest.mark.parametrize("days,level", [(40, "green"), (14, "amber"), (10, "amber"), (3, "red"), (1, "red"),
                                         (-1, "red")])
def test_consent_levels(con, days, level):
    con.execute("UPDATE sessions SET valid_until=?", ((NOW + timedelta(days=days, minutes=1)).isoformat(),))
    log(con, "a1", NOW - timedelta(hours=1))
    rep = hm.health(con, 4, 2, NOW)
    assert acct(rep).level == level and rep.banks[0].level == level and rep.ok == (level != "red")


def test_revoked_consent_is_red(con):
    con.execute("UPDATE sessions SET status='revoked'")
    log(con, "a1", NOW - timedelta(hours=1))
    a = acct(hm.health(con, 4, 2, NOW))
    assert a.level == "red" and a.consent_status == "revoked" and "reconnect" in a.problems[0]


def test_manual_accounts_and_replaced_sessions(con):
    con.execute("INSERT INTO accounts(uid, source, label, bank) VALUES ('imp-x','import','Old card','Old card')")
    con.execute("INSERT INTO imports(account_uid, imported_at, rows_total, rows_new) VALUES ('imp-x','2026-10-01T00:00:00+00:00',3,3)")
    add_bank(con, "s-old", "Bank", "FR", [("orphan", "FR7600000000000000000000009", "ORPH")])
    con.execute("UPDATE sessions SET status='replaced' WHERE session_id='s-old'")
    con.commit()
    log(con, "a1", NOW - timedelta(hours=1))
    rep = hm.health(con, 4, 2, NOW)
    m = acct(rep, "imp-x")
    assert m.source == "import" and m.level == "green" and m.last_import == "2026-10-01T00:00:00+00:00"
    assert any(b.bank == "Manual imports" for b in rep.banks)
    o = acct(rep, "orphan")
    assert o.level == "amber" and o.consent_status == "replaced"


def test_cli_health_exit_codes_and_no_network(cfg, con, capsys, monkeypatch):
    monkeypatch.setattr("coach.ingest.client.requests.request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    base = ["--config", str(cfg.config_path), "--insecure"]
    log(con, "a1", datetime.now(timezone.utc) - timedelta(minutes=5))
    con.close()
    main(base + ["health"])                                    # green: returns normally (exit 0)
    assert "overall: GREEN" in capsys.readouterr().out
    c = connect(cfg, insecure=True)
    c.execute("INSERT INTO sync_log VALUES ('a1', ?, 0, 0, 0, 'HTTP 401')", (iso(datetime.now(timezone.utc)),))
    c.commit()
    c.close()
    with pytest.raises(SystemExit) as e:
        main(base + ["health", "--json"])
    assert e.value.code == 1
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is False and d["level"] == "red"
