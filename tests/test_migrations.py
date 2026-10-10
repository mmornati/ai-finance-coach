import sqlite3
from argparse import Namespace

import pytest

from coach import db as dbm
from coach.ingest import commands as ic
from helpers import make_prototype_db

PROTO_TABLES = ["sessions", "accounts", "transactions", "pending_transactions", "balances", "sync_log",
                "pending_auth", "tx_enriched", "merchants", "tx_overrides", "merchant_eval"]


PROTO_COLS = {"sessions": 6, "accounts": 7, "pending_auth": 4}   # later migrations only ADD columns (0003)


def dump(path):
    """Prototype-column view of every prototype table (columns added by later migrations are ignored)."""
    con = sqlite3.connect(path)
    out = {t: sorted(r[:PROTO_COLS[t]] if t in PROTO_COLS else r
                     for r in con.execute(f"SELECT * FROM {t}").fetchall()) for t in PROTO_TABLES}
    con.close()
    return out


def test_migrations_on_empty_db_create_everything(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    assert set(PROTO_TABLES) <= set(dbm.user_tables(con))
    st = dbm.status(con)
    assert [v for v, _, _ in st["applied"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24] and st["pending"] == []


def test_migrations_idempotent_and_lossless_on_prototype_db(cfg):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    before = dump(cfg.db_path)
    con = dbm.connect(cfg, insecure=True)
    assert dbm.table_counts(con, PROTO_TABLES) == {t: len(rows) for t, rows in before.items()}
    first_stamp = con.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone()
    con.close()
    # run again, several times: nothing changes
    for _ in range(2):
        con = dbm.connect(cfg, insecure=True)
        assert dbm.apply_migrations(con) == []
        assert con.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone() == first_stamp
        assert con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 24
        con.close()
    assert dump(cfg.db_path) == before


def test_0002_creates_indexes(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_transactions_account_date" in idx and "idx_tx_enriched_merchant_key" in idx
    cols = [r[2] for r in con.execute("PRAGMA index_info(idx_transactions_account_date)")]
    assert cols == ["account_uid", "booking_date"]


def test_failed_migration_rolls_back(cfg, tmp_path):
    d = tmp_path / "mig"
    d.mkdir()
    (d / "0001_ok.sql").write_text("CREATE TABLE t1(x);")
    (d / "0002_bad.sql").write_text("CREATE TABLE t2(x); CREATE TABLE t2(x);")
    cfg.db_path.parent.mkdir(parents=True)
    con = dbm.connect(cfg, insecure=True, migrate=False, create=True)
    with pytest.raises(dbm.Error):
        dbm.apply_migrations(con, d)
    assert dbm.applied_versions(con).keys() == {1}
    assert "t2" not in dbm.user_tables(con)


def test_bad_migration_filename_rejected(tmp_path):
    (tmp_path / "oops.sql").write_text("")
    with pytest.raises(ValueError):
        dbm.available_migrations(tmp_path)


def test_import_prototype_copies_migrates_and_verifies_counts(cfg, tmp_path):
    src = make_prototype_db(tmp_path / "proto.db")
    before = dump(src)
    src_bytes = src.read_bytes()
    dest, result = dbm.import_prototype(cfg, src)
    assert dest == cfg.db_path.resolve()
    assert all(s == d for s, d in result.values()) and result["pending_auth"] == (2, 2)
    assert src.read_bytes() == src_bytes                      # source untouched
    assert dump(dest) == before
    assert "schema_migrations" in dbm.user_tables(dbm._open(dest, None))
    with pytest.raises(dbm.EncryptionError):                    # no silent overwrite
        dbm.import_prototype(cfg, src)
    dbm.import_prototype(cfg, src, force=True)


def test_finish_accepts_pending_auth_states_after_migration(cfg, monkeypatch):
    """Bank logins started with the prototype must still complete after migration."""
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)

    calls = []

    def fake_call(self, method, path, **kw):
        calls.append((method, path, kw))
        return {"session_id": "s-new", "aspsp": {"name": "Test Bank", "country": "FR"},
                "access": {"valid_until": "2027-01-01T00:00:00Z"},
                "accounts": [{"uid": "acc-new", "name": "NEW ACC", "currency": "EUR",
                              "account_id": {"iban": "FR7600000000000000009999"}}]}

    monkeypatch.setattr(ic.EnableBankingClient, "call", fake_call)
    monkeypatch.setattr(ic.EnableBankingClient, "from_config", classmethod(lambda cls, c: cls("a", "/nope")))
    a = Namespace(insecure=True, code_or_url="https://localhost:8443/callback?state=state-aaa&code=CODE123")
    ic.cmd_finish(a, cfg)
    assert calls == [("POST", "/sessions", {"json": {"code": "CODE123"}})]
    con = dbm.connect(cfg, insecure=True)
    assert con.execute("SELECT uid FROM accounts WHERE uid='acc-new'").fetchone()
    # unknown state is still rejected
    bad = Namespace(insecure=True, code_or_url="https://localhost:8443/callback?state=zzz&code=C")
    with pytest.raises(SystemExit, match="Unknown state"):
        ic.cmd_finish(bad, cfg)


def test_0016_creates_the_net_worth_history_table(cfg):
    """E9-4: one row per (day, source); a source outside snapshot / backfill is refused by the table itself; additive (nothing else changes)."""
    con = dbm.connect(cfg, insecure=True, create=True)
    cols = {r[1] for r in con.execute("PRAGMA table_info(net_worth_history)")}
    assert {"as_of", "month", "source", "net_worth_c", "assets_c", "liabilities_c", "cash_c", "savings_c", "investments_c", "real_estate_c",
            "vehicles_c", "other_c", "n_unknown", "complete", "detail", "created_at"} <= cols
    row = ("2026-10-04", "2026-10", "snapshot", 100, 200, 100, 0, 0, 0, 0, 0, 0, 1, 0, "{}", "t")
    con.execute("INSERT INTO net_worth_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    import pytest
    with pytest.raises(Exception):
        con.execute("INSERT INTO net_worth_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)                  # same day and source
    with pytest.raises(Exception):
        con.execute("INSERT INTO net_worth_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("2026-10-05", "2026-10", "guess", *row[3:]))
    assert "idx_nwh_month" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_0022_household_tables_are_additive_and_constrained(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    assert {"tx_person", "tx_person_log", "ui_users", "audit_log"} <= set(dbm.user_tables(con))
    cols = {t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")] for t in ("tx_person", "tx_person_log", "ui_users", "audit_log")}
    assert cols["tx_person"] == ["tx_key", "member", "set_at", "set_by", "note"]
    assert cols["ui_users"] == ["id", "member_id", "role", "created_at", "disabled_at", "prefs"]
    assert cols["audit_log"] == ["id", "at", "actor", "method", "path", "status"]
    with pytest.raises(Exception):                                     # a login has a role: adult or child, nothing else
        con.execute("INSERT INTO ui_users(id, role, created_at) VALUES ('x', 'root', 't')")
    con.execute("INSERT INTO ui_users(id, role, created_at) VALUES ('x', 'child', 't')")
    assert con.execute("SELECT prefs FROM ui_users").fetchone()[0] == "{}"


def test_0023_moves_dateless_rows_to_pending_and_dates_the_others(cfg, tmp_path):
    import json
    import shutil
    d = tmp_path / "mig"
    d.mkdir()
    for f in dbm.MIGRATIONS_DIR.iterdir():
        if f.is_file() and not f.name.startswith("0023"):
            shutil.copy(f, d / f.name)
    con = dbm.connect(cfg, insecure=True, create=True, migrate=False)
    dbm.apply_migrations(con, d, safety_copy=False)
    ins = "INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description, raw) VALUES (?,?,?,?,?,?,?)"
    con.execute(ins, ("a:ref:ok", "a", "2026-10-01", -1.0, "EUR", "KEEP", "{}"))
    con.execute(ins, ("a:ref:none", "a", None, -2.0, "EUR", "OTHER", json.dumps({"status": "OTHR"})))
    con.execute(ins, ("a:ref:vd", "a", None, -3.0, "EUR", "VALUE", json.dumps({"status": "BOOK", "value_date": "2026-10-02"})))
    con.execute("INSERT INTO tx_enriched(tx_key) VALUES ('a:ref:none')")
    con.commit()
    dbm.apply_migrations(con, safety_copy=False)
    rows = dict(con.execute("SELECT tx_key, booking_date FROM transactions").fetchall())
    assert rows == {"a:ref:ok": "2026-10-01", "a:ref:vd": "2026-10-02"}
    assert con.execute("SELECT description FROM pending_transactions").fetchall() == [("OTHER",)]
    assert con.execute("SELECT COUNT(*) FROM tx_enriched WHERE tx_key='a:ref:none'").fetchone()[0] == 0
