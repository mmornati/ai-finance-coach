import sqlite3
from argparse import Namespace

import pytest

from coach import db as dbm
from coach.analytics.report import build_report
from coach.classify import commands as cc
from coach.classify.rules import categorised
from coach.cli import main
from coach.db import connect
from coach.ingest import accounts as acc
from coach.ingest.client import ApiError
from coach.ingest.sync import sync_all
from helpers import FakeClient, add_bank, add_tx, eb_tx, make_prototype_db

IB = ["FR7600000000000000000000001", "IT60X0542811101000000123456", "FR7600000000000000000000003"]


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s-fr", "Bank FR", "FR", [("fr1", IB[0], "FR ONE"), ("fr2", IB[2], "FR TWO")])
    add_bank(c, "s-it", "Bank IT", "IT", [("it1", IB[1], "IT ONE")])
    return c


def page(ref, amount=-5.0):
    return [{"transactions": [eb_tx(ref, "2026-10-01", amount, "CARTE 01/10 SHOP")]}]


def test_sync_iterates_all_banks_and_accounts(con):
    fc = FakeClient({"fr1": page("a"), "fr2": page("b"), "it1": page("c")})
    res = sync_all(con, fc, out=lambda *_: None)
    assert [(r["uid"], r["status"], r["bank"]) for r in res] == [
        ("fr1", "ok", "Bank FR"), ("fr2", "ok", "Bank FR"), ("it1", "ok", "Bank IT")]
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3


@pytest.mark.parametrize("failure", [
    ApiError(401, "session closed"), ApiError(429, "rate"), RuntimeError("parser exploded"),
    ConnectionError("network down"), KeyError("unexpected payload")])
def test_one_bank_failing_never_stops_the_others(con, failure):
    fc = FakeClient({"fr1": page("a"), "it1": page("c")}, fail_accounts={"fr2": failure})
    res = {r["uid"]: r for r in sync_all(con, fc, out=lambda *_: None)}
    assert res["fr2"]["status"] == "failed"
    assert res["fr1"]["status"] == "ok" and res["it1"]["status"] == "ok"
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    ok, note = con.execute("SELECT ok, note FROM sync_log WHERE account_uid='fr2'").fetchone()
    assert ok == 0 and note
    assert con.execute("SELECT COUNT(*) FROM sync_log WHERE ok=1").fetchone()[0] == 2


def test_whole_first_bank_down_other_bank_still_syncs(con):
    fc = FakeClient({"it1": page("c")}, fail_accounts={"fr1": ApiError(500, "x"), "fr2": RuntimeError("y")})
    res = sync_all(con, fc, out=lambda *_: None)
    assert [r["status"] for r in res] == ["failed", "failed", "ok"]


def test_sync_account_selector_accepts_label_and_bank_uid_and_refuses_manual(con):
    con.execute("UPDATE accounts SET api_uid='newuid' WHERE uid='it1'")
    acc.set_account(con, "fr2", label="Cards")
    fc = FakeClient({"fr2": page("b"), "it1": page("c")})
    assert [r["uid"] for r in sync_all(con, fc, "cards", out=lambda *_: None)] == ["fr2"]
    fc = FakeClient({"it1": page("c")})
    assert [r["uid"] for r in sync_all(con, fc, "newuid", out=lambda *_: None)] == ["it1"]
    con.execute("INSERT INTO accounts(uid, source, label) VALUES ('imp-x','import','X')")
    with pytest.raises(acc.AccountError, match="manual"):
        sync_all(con, fc, "imp-x", out=lambda *_: None)


def test_manual_accounts_are_not_synced(con):
    con.execute("INSERT INTO accounts(uid, source, label) VALUES ('imp-x','import','X')")
    fc = FakeClient({"fr1": page("a"), "fr2": page("b"), "it1": page("c")})
    assert {r["uid"] for r in sync_all(con, fc, out=lambda *_: None)} == {"fr1", "fr2", "it1"}


# ---- accounts: label / owner / purpose / exclude

def test_accounts_set_and_list(cfg, con, capsys):
    con.close()
    base = ["--config", str(cfg.config_path), "--insecure"]
    main(base + ["accounts", "set", "fr1", "--label", "Joint main", "--owner", "joint", "--purpose", "main"])
    main(base + ["accounts", "set", "it1", "--label", "Kids card", "--owner", "Lea", "--purpose", "kids"])
    capsys.readouterr()
    main(base + ["accounts"])
    out = capsys.readouterr().out
    assert "Joint main" in out and "Kids card" in out and "Lea" in out and "kids" in out
    assert "Bank FR" in out and "Bank IT" in out and f"…{IB[0][-4:]}" in out and IB[0] not in out   # IBAN masked
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT label, owner, purpose FROM accounts WHERE uid='it1'").fetchone() == ("Kids card", "Lea", "kids")


def test_accounts_set_validation(con):
    with pytest.raises(acc.AccountError, match="purpose"):
        acc.set_account(con, "fr1", purpose="bogus")
    with pytest.raises(acc.AccountError, match="nothing to set"):
        acc.set_account(con, "fr1")
    acc.set_account(con, "fr1", label="Main")
    with pytest.raises(acc.AccountError, match="already used"):
        acc.set_account(con, "fr2", label="main")
    with pytest.raises(acc.AccountError, match="No account matches"):
        acc.set_account(con, "zzz", label="x")
    assert acc.set_account(con, "main", owner="JOINT") == "fr1"            # by label, case-insensitive
    assert con.execute("SELECT owner FROM accounts WHERE uid='fr1'").fetchone()[0] == "joint"
    for p in acc.PURPOSES:
        acc.set_account(con, "fr1", purpose=p)


def test_cli_accounts_set_rejects_unknown_purpose(cfg, con):
    con.close()
    with pytest.raises(SystemExit):
        main(["--config", str(cfg.config_path), "--insecure", "accounts", "set", "fr1", "--purpose", "bogus"])


# ---- exclude from analytics

def test_excluded_account_leaves_analytics_but_not_the_database(cfg, con):
    for i in range(5):
        add_tx(con, "fr1", f"fr1:{i}", f"2026-0{i + 1}-10", -100.0, "CARTE 10/01 SHOP ALPHA", "card")
        add_tx(con, "it1", f"it1:{i}", f"2026-0{i + 1}-11", -40.0, "CARTE 11/01 SHOP BETA", "card")
    con.execute("INSERT INTO merchants VALUES ('SHOP ALPHA','Alpha','food.groceries',0.9,0,'user',NULL,'t')")
    con.execute("INSERT INTO merchants VALUES ('SHOP BETA','Beta','shopping.clothing',0.9,0,'user',NULL,'t')")
    con.commit()
    rules = {"type_rules": {}, "merchant_rules": []}
    both = build_report(con, rules=rules, annotations=[])
    acc.set_account(con, "it1", exclude=True)
    only = build_report(con, rules=rules, annotations=[])
    assert both["averages"]["avg_monthly"] == pytest.approx(-140.0) and only["averages"]["avg_monthly"] == pytest.approx(-100.0)
    assert only["coverage"]["total"] == 5 and both["coverage"]["total"] == 10
    assert len(list(categorised(con, rules=rules, annotations=[], include_excluded=True))) == 10
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 10            # data kept
    acc.set_account(con, "it1", exclude=False)
    assert build_report(con, rules=rules, annotations=[])["coverage"]["total"] == 10


# ---- migration 0003 applies cleanly to an existing (prototype-shaped) database

def test_migration_0003_on_existing_db_keeps_rows_and_backfills_bank(cfg):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    c = connect(cfg, insecure=True)
    assert dbm.status(c)["pending"] == []
    assert c.execute("SELECT bank, source, exclude, status FROM accounts a JOIN sessions s USING(session_id)"
                     ).fetchone() == ("Fortuneo", "api", 0, "active")
    assert c.execute("SELECT COUNT(*) FROM pending_auth WHERE completed_at IS NULL").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
    assert list(cfg.db_path.parent.glob("*.pre-migrate-*.bak"))                         # safety copy kept
