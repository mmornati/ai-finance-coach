"""Regression tests for the E0 review findings (numbering follows the review)."""
import os
import stat
import subprocess
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import keyring
import pytest
from keyring.errors import KeyringError

from coach import backup as bk
from coach import config as cm
from coach import db as dbm
from coach import schedule as sch
from coach import secrets as sec
from coach.cli import main
from coach.ingest import commands as ic
from coach.ingest.client import ApiError
from coach.ingest.sync import local_day_bounds_utc, sync_all, syncs_today
from helpers import FakeClient, make_prototype_db

KEY = "k-review"


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def plain(cfg):
    cfg.db_path.parent.mkdir(parents=True)
    return make_prototype_db(cfg.db_path)


# ---- 1. backup: symlinks / odd entries, verify before prune

def test_backup_handles_symlinks_and_special_files(cfg, plain, tmp_path):
    mem = cfg.memory_dir
    (mem / "real.yaml").write_text("a: 1\n")
    (tmp_path / "outside.md").write_text("outside\n")
    os.symlink(tmp_path / "outside.md", mem / "link.md")            # symlink leading OUTSIDE memory/ -> not followed
    os.symlink(tmp_path / "nowhere", mem / "dangling")              # dangling -> skipped
    (tmp_path / "otherdir").mkdir()
    os.symlink(tmp_path / "otherdir", mem / "dirlink")              # symlinked dir -> skipped
    os.mkfifo(mem / "pipe")                                         # non-regular -> skipped
    warnings = []
    path, _ = bk.create_backup(cfg, key=KEY, warn=warnings.append)
    out = tmp_path / "restored"
    bk.restore_backup(path, out, key=KEY)                           # must not be rejected
    assert not (out / "memory" / "link.md").exists()                # E3: never dereference a link out of the folder
    assert not (out / "memory" / "dangling").exists() and not (out / "memory" / "pipe").exists()
    assert len(warnings) == 4 and any("link.md" in w and "outside" in w for w in warnings)


def test_failed_verification_removes_new_backup_and_keeps_old_ones(cfg, plain, monkeypatch):
    made = [bk.create_backup(cfg, now=datetime(2026, 1, d, tzinfo=timezone.utc), key=KEY)[0] for d in (1, 2, 3)]

    def bad(*a, **k):
        raise bk.BackupError("corrupt")

    monkeypatch.setattr(bk, "verify_archive", bad)
    with pytest.raises(bk.BackupError):
        bk.create_backup(cfg, now=datetime(2026, 1, 4, tzinfo=timezone.utc), key=KEY)
    assert bk.list_backups(cfg.backup_dir) == made                  # nothing pruned, bad file gone


# ---- 2. missing DB / atomic encrypt / leftovers

def test_connect_never_creates_a_missing_database(cfg, db_key):
    for insecure in (False, True):
        with pytest.raises(dbm.DatabaseMissingError, match="No database at"):
            dbm.connect(cfg, insecure=insecure)
    assert not cfg.db_path.exists()


def test_interrupted_encrypt_leftover_blocks_startup(cfg, plain, db_key):
    cfg.db_path.with_name("finance.db.encrypting").write_bytes(b"junk")
    with pytest.raises(dbm.DatabaseMissingError, match="interrupted encryption"):
        dbm.connect(cfg, insecure=True)
    path, bak = dbm.encrypt_database(cfg)                           # encrypt itself recovers
    assert dbm.is_plaintext(path) is False


def test_encrypt_swap_failure_leaves_original_database_usable(cfg, plain, db_key, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        dbm.encrypt_database(cfg)
    assert dbm.is_plaintext(cfg.db_path) is True
    assert dbm.connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7


def test_insecure_create_prefers_encryption_when_key_available(cfg, db_key):
    dbm.connect(cfg, insecure=True, create=True).close()
    assert dbm.is_plaintext(cfg.db_path) is False


def test_insecure_create_without_key_is_plaintext(cfg):
    dbm.connect(cfg, insecure=True, create=True).close()
    assert dbm.is_plaintext(cfg.db_path) is True


# ---- 3. strict config types

@pytest.mark.parametrize("body,msg", [
    ('insecure_plaintext_db = "false"', "insecure_plaintext_db must be true or false"),
    ('insecure_plaintext_db = 1', "insecure_plaintext_db must be true or false"),
    ('[sync]\ndaily_limit = "4"', "sync.daily_limit must be an integer"),
    ('[sync]\ndaily_limit = true', "sync.daily_limit must be an integer"),
    ('[backup]\nretention = "3"', "backup.retention must be an integer"),
    ('[llm]\nmodel = 5', "llm.model must be a string"),
    ('data_dir = 3', "data_dir must be a string"),
    ('[enable_banking]\napp_id = 12', "enable_banking.app_id must be a string"),
])
def test_config_types_are_enforced(tmp_path, body, msg):
    p = tmp_path / "config.toml"
    p.write_text(body + "\n")
    with pytest.raises(cm.ConfigError, match=msg):
        cm.load_config(p, env={})


def test_real_bool_still_works(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("insecure_plaintext_db = true\n")
    assert cm.load_config(p, env={}).insecure_plaintext_db is True


# ---- 4. consistent snapshot, change detection during encrypt

def test_backup_of_encrypted_db_is_a_keyed_snapshot(cfg, plain, db_key, tmp_path):
    dbm.encrypt_database(cfg)
    path, _ = bk.create_backup(cfg, key=KEY)
    out = tmp_path / "r"
    bk.restore_backup(path, out, key=KEY)
    assert dbm.is_plaintext(out / "finance.db") is False
    con = dbm._open(out / "finance.db", "test-db-key")
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7


def test_concurrent_update_during_encrypt_is_blocked_not_lost(cfg, plain, db_key, monkeypatch):
    real = dbm._fsync_path
    outcome = {}

    code = ("import sqlite3,sys\n"
            "c=sqlite3.connect(sys.argv[1],timeout=0.1)\n"
            "try:\n"
            "    c.execute(\"UPDATE merchants SET category='x.hacked' WHERE merchant_key='RELAY BEAUVAIS'\")\n"
            "    c.commit(); print('succeeded')\n"
            "except sqlite3.OperationalError as e:\n"
            "    print(e)\n")

    def writer_tries(p):
        real(p)
        # a separate process (POSIX locks do not conflict inside one process across sqlite builds)
        r = subprocess.run([sys.executable, "-c", code, str(cfg.db_path)], capture_output=True, text=True)
        outcome["write"] = r.stdout.strip() or r.stderr

    monkeypatch.setattr(dbm, "_fsync_path", writer_tries)
    dbm.encrypt_database(cfg)
    assert "locked" in outcome["write"]                              # writer failed loudly, not silently lost
    cat = dbm.connect(cfg).execute("SELECT category FROM merchants WHERE merchant_key='RELAY BEAUVAIS'").fetchone()
    assert cat == ("shopping.books_media",)


def test_lock_is_released_after_encrypt_and_readers_work_during_it(cfg, plain, db_key, monkeypatch):
    seen = {}
    real = dbm._fsync_path

    def reader(p):
        real(p)
        c = sqlite3.connect(cfg.db_path, timeout=0.1)
        seen["n"] = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        c.close()

    monkeypatch.setattr(dbm, "_fsync_path", reader)
    path, _ = dbm.encrypt_database(cfg)
    assert seen["n"] == 7
    c = dbm.connect(cfg)
    c.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES ('after','B','FR','t')")   # writable again
    c.commit()


def test_encrypt_recovers_from_crash_between_link_and_replace(cfg, plain, db_key):
    bak = cfg.db_path.with_name("finance.db.plaintext.bak")
    os.link(cfg.db_path, bak)                                        # the state a crash would leave
    path, bak2 = dbm.encrypt_database(cfg)
    assert dbm.is_plaintext(path) is False and dbm.is_plaintext(bak2) is True
    # a real, different .bak is still protected
    other = cfg.db_path.with_name("o.db")
    make_prototype_db(other)
    other.with_name("o.db.plaintext.bak").write_text("precious")
    with pytest.raises(dbm.EncryptionError, match="already exists"):
        dbm.encrypt_database(cfg, path=other)
    assert other.with_name("o.db.plaintext.bak").read_text() == "precious"


def test_plaintext_leftovers_listed_and_warned(cfg, plain, db_key, capsys):
    dbm.connect(cfg, insecure=True).close()                          # creates a pre-migrate copy
    main(["--config", str(cfg.config_path), "db", "encrypt"])
    out = capsys.readouterr().out
    assert "plaintext.bak" in out and "pre-migrate" in out and "unencrypted copies" in out
    main(["--config", str(cfg.config_path), "db", "status"])
    assert "unencrypted copies" in capsys.readouterr().out
    for p in dbm.plaintext_leftovers(cfg.db_path):
        p.unlink()
    main(["--config", str(cfg.config_path), "db", "status"])
    assert "unencrypted copies" not in capsys.readouterr().out


def test_import_prototype_leaves_no_plaintext_siblings(cfg, tmp_path):
    src = make_prototype_db(tmp_path / "proto.db")
    dest, _ = dbm.import_prototype(cfg, src)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["finance.db"]


# ---- 5. migrations: unknown versions, safety copy

def test_newer_database_is_refused(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    con.execute("INSERT INTO schema_migrations VALUES (99, 'future', 't')")
    con.commit()
    con.close()
    with pytest.raises(dbm.MigrationError, match="newer version"):
        dbm.connect(cfg, insecure=True)


def test_safety_copy_before_migrating_non_empty_db_only(cfg, plain):
    dbm.connect(cfg, insecure=True).close()
    copies = list(cfg.db_path.parent.glob("finance.db.pre-migrate-*.bak"))
    assert len(copies) == 1 and mode(copies[0]) == 0o600
    assert sqlite3.connect(copies[0]).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
    assert "schema_migrations" not in {r[0] for r in sqlite3.connect(copies[0]).execute(
        "SELECT name FROM sqlite_master")}                         # copy is the pre-migration state
    dbm.connect(cfg, insecure=True).close()                        # up to date: no new copy
    assert len(list(cfg.db_path.parent.glob("*.pre-migrate-*.bak"))) == 1


def test_fresh_database_gets_no_safety_copy(cfg):
    dbm.connect(cfg, insecure=True, create=True).close()
    assert not list(cfg.db_path.parent.glob("*.pre-migrate-*"))


def test_migration_with_several_statements_on_one_line_and_triggers(tmp_path):
    assert len(dbm.split_sql("CREATE TABLE a(x); CREATE TABLE b(x);\n-- c\n")) == 2
    trig = "CREATE TRIGGER t AFTER INSERT ON a BEGIN INSERT INTO b VALUES (1); END;"
    assert dbm.split_sql(trig) == [trig]


# ---- 6. permissions

def test_permissions_db_dirs_backups_and_restore(cfg, plain, db_key, tmp_path):
    dbm.connect(cfg, insecure=True).close()
    assert mode(cfg.db_path) == 0o600 and mode(cfg.data_dir) == 0o700
    path, bak = dbm.encrypt_database(cfg)
    assert mode(path) == 0o600 and mode(bak) == 0o600
    (cfg.memory_dir / "x.yaml").write_text("a: 1")
    os.chmod(cfg.memory_dir / "x.yaml", 0o777)
    arc, _ = bk.create_backup(cfg, key=KEY)
    assert mode(arc) == 0o600 and mode(cfg.backup_dir) == 0o700
    out = tmp_path / "restored"
    files = bk.restore_backup(arc, out, key=KEY)
    assert files and all(mode(f) == 0o600 for f in files)           # archived 0o777 not re-applied
    assert mode(out) == 0o700 and mode(out / "memory") == 0o700


def test_schedule_creates_private_log_dir(cfg, tmp_path):
    sch.install(cfg, tmp_path / "LA", load=False, out=lambda *_: None, do_preflight=False)
    assert mode(cfg.log_dir) == 0o700


# ---- 7. import --force keeps the old DB

def test_import_force_moves_existing_encrypted_db_aside_with_warning(cfg, plain, db_key, tmp_path):
    dbm.encrypt_database(cfg)
    encrypted_bytes = cfg.db_path.read_bytes()
    src = make_prototype_db(tmp_path / "proto.db")
    msgs = []
    dest, _ = dbm.import_prototype(cfg, src, force=True, warn=msgs.append)
    aside = dest.with_name("finance.db.pre-import.bak")
    assert aside.read_bytes() == encrypted_bytes
    assert any("ENCRYPTED" in m for m in msgs) and dbm.is_plaintext(dest) is True


def test_import_uses_quoted_file_uri(cfg, tmp_path):
    weird = tmp_path / "dir with space & ?#%"
    weird.mkdir()
    src = make_prototype_db(weird / "pro?to#.db")
    dest, res = dbm.import_prototype(cfg, src)
    assert res["transactions"] == (7, 7)


# ---- 8. schedule preflight, failing sync => non-zero

def ready_cfg(cfg, tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("x")
    cfg.eb_app_id, cfg.eb_redirect_url, cfg.eb_private_key_path = "app", "https://x/cb", str(key)
    return cfg


def test_preflight_reports_every_problem_and_blocks_install(cfg, tmp_path):
    problems = sch.preflight(cfg)
    assert any("Enable Banking" in p for p in problems) and any("No database" in p for p in problems)
    with pytest.raises(cm.ConfigError, match="Not installing"):
        sch.install(cfg, tmp_path / "LA", out=lambda *_: None)
    assert not (tmp_path / "LA").exists()


def test_preflight_rejects_plaintext_db_and_passes_when_ready(cfg, tmp_path, db_key):
    ready_cfg(cfg, tmp_path)
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    assert any("Refusing to open plaintext" in p for p in sch.preflight(cfg))
    dbm.encrypt_database(cfg)
    problems = sch.preflight(cfg)                                    # key only in the shell env: launchd has none
    assert any("not in the macOS Keychain" in p for p in problems)
    sec.set_secret("db_key", "test-db-key")                          # fake keychain
    assert sch.preflight(cfg) == []
    sch.install(cfg, tmp_path / "LA", load=False, out=lambda *_: None)


def test_preflight_fails_without_db_key(cfg, tmp_path, db_key, monkeypatch):
    ready_cfg(cfg, tmp_path)
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    dbm.encrypt_database(cfg)
    monkeypatch.delenv("COACH_DB_KEY")
    assert any("db_key" in p for p in sch.preflight(cfg))


def test_run_daily_exits_nonzero_when_an_account_sync_fails(cfg, tmp_path, db_key, monkeypatch):
    con = dbm.connect(cfg, create=True)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','T','FR','2099-01-01','t','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','A','FR','EUR','CACC','{}')")
    con.commit()
    con.close()
    monkeypatch.setattr("coach.classify.llm.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"structured_output":{"results":[]}}', stderr=""))
    client = FakeClient({"acc1": [ApiError(429, "rate")]})
    assert sch.run_daily(cfg, client=client, out=lambda *_: None) == 1
    assert "schedule run FAILED" in (cfg.log_dir / "schedule.log").read_text()


# ---- 9. daily limit time base

def test_local_day_bounds_are_utc_strings_of_the_local_day():
    paris = timezone(timedelta(hours=2))
    now = datetime(2026, 10, 4, 0, 30, tzinfo=paris)
    assert local_day_bounds_utc(now) == ("2026-10-03T22:00:00+00:00", "2026-10-04T22:00:00+00:00")


def test_syncs_today_around_local_midnight(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    paris = timezone(timedelta(hours=2))
    for ts in ["2026-10-03T21:59:59+00:00",   # 23:59:59 local on Oct 3: yesterday
               "2026-10-03T22:00:00+00:00",   # 00:00:00 local Oct 4: today
               "2026-10-04T10:00:00+00:00",   # today
               "2026-10-04T21:59:59+00:00",   # 23:59:59 local Oct 4: today
               "2026-10-04T22:00:00+00:00"]:  # 00:00 local Oct 5: tomorrow
        con.execute("INSERT INTO sync_log VALUES ('a',?,1,0,1,'')", (ts,))
    assert syncs_today(con, "a", datetime(2026, 10, 4, 0, 30, tzinfo=paris)) == 3
    assert syncs_today(con, "a", datetime(2026, 10, 3, 12, 0, tzinfo=paris)) == 1
    # a run logged just now always counts as today, whatever the system timezone
    from coach.db import now_iso
    con.execute("INSERT INTO sync_log VALUES ('b',?,1,0,1,'')", (now_iso(),))
    assert syncs_today(con, "b") == 1


# ---- 10. connect opens the DB before calling the bank

def test_connect_does_not_call_bank_when_db_is_refused(cfg, tmp_path, monkeypatch):
    ready_cfg(cfg, tmp_path)
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)                                   # plaintext, no --insecure
    calls = []
    monkeypatch.setattr(ic.EnableBankingClient, "call", lambda self, *a, **k: calls.append(a))
    a = SimpleNamespace(insecure=False, bank="B", country="FR", days=90, no_browser=True)
    with pytest.raises(dbm.PlaintextDatabaseError):
        ic.cmd_connect(a, cfg)
    assert calls == []


def test_connect_stores_pending_auth_after_auth_call(cfg, tmp_path, monkeypatch):
    ready_cfg(cfg, tmp_path)
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    monkeypatch.setattr(ic.EnableBankingClient, "call", lambda self, *a, **k: {"url": "https://bank/auth"})
    ic.cmd_connect(SimpleNamespace(insecure=True, bank="B", country="FR", days=90, no_browser=True), cfg)
    assert dbm.connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM pending_auth").fetchone()[0] == 3


# ---- 11. llm backend validated

def test_unknown_llm_backend_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nbackend = "openai"\n')
    with pytest.raises(cm.ConfigError, match="not supported"):
        cm.load_config(p, env={})
    p.write_text('[llm]\nbackend = "claude-code"\n')
    assert cm.load_config(p, env={}).llm_backend == "claude-code"


# ---- 12. retention by parsed UTC timestamp

def test_backup_names_use_utc_and_sort_numerically(cfg, plain):
    paris = timezone(timedelta(hours=2))
    p, _ = bk.create_backup(cfg, now=datetime(2026, 3, 1, 1, 0, 0, tzinfo=paris), key=KEY)
    assert p.name == "coach-backup-20260228-230000.tar.enc"          # converted to UTC
    d = cfg.backup_dir
    for n in ("coach-backup-20260301-000000-2.tar.enc", "coach-backup-20260301-000000-10.tar.enc",
              "coach-backup-20260301-000000.tar.enc"):
        (d / n).write_bytes(b"x")
    (d / "coach-backup-notes.tar.enc").write_bytes(b"x")             # foreign file ignored
    names = [x.name for x in bk.list_backups(d)]
    assert names[1:] == ["coach-backup-20260301-000000.tar.enc", "coach-backup-20260301-000000-2.tar.enc",
                         "coach-backup-20260301-000000-10.tar.enc"]


# ---- NITs: clean errors instead of tracebacks

def test_set_secret_keychain_failure_is_a_clean_error(cfg, monkeypatch):
    def fail(*a, **k):
        raise KeyringError("locked")
    monkeypatch.setattr(keyring, "set_password", fail)
    with pytest.raises(SystemExit, match="Cannot store 'db_key'"):
        main(["--config", str(cfg.config_path), "config", "set-secret", "db_key", "--generate"])


def test_keychain_backend_error_is_not_reported_as_missing(monkeypatch):
    def fail(*a, **k):
        raise KeyringError("denied")
    monkeypatch.setattr(keyring, "get_password", fail)
    with pytest.raises(sec.SecretBackendError, match="COACH_DB_KEY"):
        sec.get_secret("db_key")
    assert dict(sec.describe())["db_key"].startswith("UNKNOWN")
    monkeypatch.setenv("COACH_DB_KEY", "env-wins")                   # env still works with a broken Keychain
    assert sec.get_secret("db_key") == "env-wins"


def test_database_locked_and_launchctl_errors_are_clean(cfg, monkeypatch):
    def locked(a, c):
        raise dbm.Error("database is locked")
    monkeypatch.setattr(ic, "cmd_stats", locked)
    with pytest.raises(SystemExit, match="database error: database is locked"):
        main(["--config", str(cfg.config_path), "stats"])

    def bad(*a, **k):
        raise RuntimeError("launchctl bootstrap failed: nope")
    monkeypatch.setattr(sch, "install", bad)
    with pytest.raises(SystemExit, match="launchctl bootstrap failed"):
        main(["--config", str(cfg.config_path), "schedule", "install", "--skip-preflight"])


def test_cli_missing_db_message_and_migrate_create(cfg, db_key, capsys):
    base = ["--config", str(cfg.config_path)]
    with pytest.raises(SystemExit, match="No database at"):
        main(base + ["stats"])
    main(base + ["db", "migrate", "--create"])
    assert "applied 0001_initial" in capsys.readouterr().out and dbm.is_plaintext(cfg.db_path) is False


def test_preflight_flags_eb_settings_coming_from_shell_env(cfg, tmp_path, db_key):
    ready_cfg(cfg, tmp_path)
    cfg.sources["enable_banking.app_id"] = "env EB_APP_ID"
    cfg.sources["enable_banking.redirect_url"] = "config.toml"
    cfg.sources["enable_banking.private_key_path"] = "legacy prototype/ingest/.env"
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    dbm.encrypt_database(cfg)
    sec.set_secret("db_key", "test-db-key")
    problems = sch.preflight(cfg)
    assert len(problems) == 1 and "app_id" in problems[0] and "launchd" in problems[0]


def test_pre_migrate_copy_uses_backup_api_same_key_and_keeps_only_two(cfg, plain, db_key):
    dbm.encrypt_database(cfg)
    folder = cfg.db_path.parent
    for ts in ("20200101T000001", "20200101T000002", "20200101T000003"):
        (folder / f"finance.db.pre-migrate-{ts}.bak").write_text("old")
    dbm.connect(cfg).close()                                          # migrate the prototype schema first
    con = dbm.connect(cfg, create=False, migrate=False)
    con.execute("DELETE FROM schema_migrations WHERE version=2")      # force a pending migration
    con.commit()
    con.close()
    dbm.connect(cfg).close()
    copies = sorted(folder.glob("finance.db.pre-migrate-*.bak"))
    assert len(copies) == 2                                           # pruned to the newest two
    newest = copies[-1]
    assert dbm.is_plaintext(newest) is False                          # same key, not plaintext
    assert dbm._open(newest, "test-db-key").execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
