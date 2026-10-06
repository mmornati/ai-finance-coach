"""E11-6: `coach export` (encrypted archive, --plain with TTY + typed phrase) and `coach wipe` (TTY + typed phrase, dry run, separate questions).

Tmp dirs only; the Keychain is the in-memory fake; the bank client is a fake: no network, no real data is touched.
"""
import io
import json
import os
import stat
import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from apihelpers import world  # noqa: F401
from coach import cli, export as E, secrets, wipe as W
from coach.backup import BackupError, create_backup, decrypt_bytes, encrypt_bytes, restore_backup
from coach.db import connect
from coach.ingest.client import ApiError
from helpers import add_tx

KEY = "backup-key-for-tests"


@pytest.fixture
def data(world, fake_keyring, db_key):
    """The E5 synthetic world + memory files (history, proposals, documents) + keys in the fake Keychain + one backup."""
    cfg = world
    from coach.db import encrypt_database
    encrypt_database(cfg)                                                       # the synthetic world is plaintext: encrypt it like a real one
    for p in cfg.data_dir.glob("*.plaintext.bak"):
        p.unlink()
    secrets.set_secret("backup_key", KEY)
    secrets.set_secret("db_key", "test-db-key")
    secrets.set_secret("proposal_key", "pk")
    mem = cfg.memory_dir
    (mem / "household.yaml").write_text("members:\n  - id: anna\n    name: Anna Rossi\n    role: adult\n")
    (mem / "profile.md").write_text("# Profile\nWe live in Montpellier.\n")
    (mem / "liabilities").mkdir(exist_ok=True)
    (mem / "liabilities" / "mortgage.yaml").write_text("id: mortgage\n")
    (mem / "documents").mkdir(exist_ok=True)
    (mem / "documents" / "contract.pdf").write_bytes(b"%PDF-1.4 fake")
    (mem / ".history.git").mkdir(exist_ok=True)
    (mem / ".history.git" / "HEAD").write_text("ref: refs/heads/main\n")
    (mem / ".proposals").mkdir(exist_ok=True)
    (mem / ".proposals" / "p-20260101-aaaaaa.json").write_text("{}")
    con = connect(cfg)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw, status) "
                "VALUES ('sess-secret-1','Bank','FR','2099-01-01','t','{}','active')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw, bank) "
                "VALUES ('acc-x','sess-secret-1','Joint','FR7600000000000000000009','EUR','CACC','{}','Bank')")
    add_tx(con, "acc-x", "evil1", "2026-09-30", -5.0, "=HYPERLINK(\"http://x\",\"click\")", "card")
    con.execute("INSERT INTO insights(id, created, kind, title, body, backend) VALUES ('cin_1','t','answer','Q','A','claude-code')")
    con.commit()
    con.close()
    create_backup(cfg)
    return cfg


def read_zip(path: Path, key=KEY) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(decrypt_bytes(path.read_bytes(), key, E.EXPORT_MAGIC)))


# ---------------------------------------------------------------- export


def test_the_encrypted_export_holds_everything_and_nothing_readable(data):
    out = E.export_encrypted(data)
    assert out.parent == data.data_dir / "exports" and out.name.endswith(".zip.enc")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and stat.S_IMODE(out.parent.stat().st_mode) == 0o700
    raw = out.read_bytes()
    assert raw.startswith(E.EXPORT_MAGIC)
    for clear in (b"PRICEUP", b"Anna Rossi", b"FR7600000000000000000009", b"PK\x03\x04", b"Montpellier"):
        assert clear not in raw, clear                                         # not a zip, not readable
    z = read_zip(out)
    names = set(z.namelist())
    assert {"manifest.json", "transactions.csv", "README.txt", "categories.json"} <= names
    for t in ("accounts", "transactions", "merchants", "insights", "decisions", "alternatives", "net_worth_history", "alert_events", "tx_enriched", "balances"):
        assert f"data/{t}.json" in names, t
    assert {"memory/household.yaml", "memory/profile.md", "memory/liabilities/mortgage.yaml", "memory/documents/contract.pdf"} <= names
    assert not any(n.startswith(("memory/.history.git", "memory/.proposals")) for n in names)
    assert "data/sessions.json" not in names and "data/schema_migrations.json" not in names          # bank session credentials stay out
    man = json.loads(z.read("manifest.json"))
    assert man["tables"]["transactions"] == len(json.loads(z.read("data/transactions.json"))) > 0
    assert "sessions" in man["not_included"] and man["memory_files"] == len([n for n in names if n.startswith("memory/")]) >= 4
    assert any(a["uid"] == "acc-x" and a["iban"].endswith("9") for a in json.loads(z.read("data/accounts.json")))
    assert json.loads(z.read("data/insights.json"))[0]["id"] == "cin_1"


def test_the_csv_has_one_row_per_transaction_and_defuses_formulas(data):
    z = read_zip(E.export_encrypted(data))
    import csv
    rows = list(csv.reader(io.StringIO(z.read("transactions.csv").decode())))
    con = connect(data)
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    con.close()
    assert rows[0][:3] == ["date", "amount", "currency"] and len(rows) == n + 1
    evil = next(r for r in rows if r[-1] == "evil1")
    assert evil[6].startswith("'=") and not any(c.startswith("=") for r in rows for c in r)
    assert any(r[8] == "subscriptions.software_cloud" for r in rows)            # the category column is filled


def test_export_refuses_to_overwrite_and_needs_the_backup_key(data, fake_keyring):
    out = E.export_encrypted(data, data.data_dir / "exports" / "one.zip.enc")
    with pytest.raises(E.ExportError, match="already exists"):
        E.export_encrypted(data, out)
    with pytest.raises(BackupError):
        read_zip(out, key="wrong-key")
    del fake_keyring.store[("ai-finance-coach", "backup_key")]
    with pytest.raises(secrets.SecretNotFound):
        E.export_encrypted(data, data.data_dir / "exports" / "two.zip.enc")


def test_export_and_backup_files_are_not_interchangeable(data):
    out = E.export_encrypted(data)
    with pytest.raises(BackupError, match="bad header"):
        restore_backup(out, data.root / "r", key=KEY)
    bak = next(data.backup_dir.glob("coach-backup-*"))
    with pytest.raises(BackupError, match="export"):
        decrypt_bytes(bak.read_bytes(), KEY, E.EXPORT_MAGIC)


def test_decrypt_extracts_with_private_files_and_refuses_unsafe_paths(data, tmp_path):
    out = E.export_encrypted(data)
    files = E.decrypt_export(out, tmp_path / "back")
    assert files and all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    assert (tmp_path / "back" / "manifest.json").exists()
    with pytest.raises(E.ExportError, match="overwrite"):
        E.decrypt_export(out, tmp_path / "back")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.txt", "x")
    evil = tmp_path / "evil.zip.enc"
    evil.write_bytes(encrypt_bytes(buf.getvalue(), KEY, E.EXPORT_MAGIC))
    with pytest.raises(E.ExportError, match="unsafe"):
        E.decrypt_export(evil, tmp_path / "e2")
    assert not (tmp_path / "escape.txt").exists()


def ns(**kw):
    base = dict(out=None, format="zip", plain=False, decrypt=None, to=None, insecure=False)
    return Namespace(**{**base, **kw})


def test_plain_export_needs_a_terminal_and_the_typed_phrase(data, capsys):
    with pytest.raises(SystemExit, match="aborted"):
        E.cmd_export(ns(plain=True), data, isatty=lambda: False, input_fn=lambda p: pytest.fail("no prompt without a terminal"))
    assert "needs a terminal" in capsys.readouterr().out
    assert not (data.data_dir / "exports").exists()
    with pytest.raises(SystemExit, match="aborted"):
        E.cmd_export(ns(plain=True), data, isatty=lambda: True, input_fn=lambda p: "yes")
    assert not (data.data_dir / "exports").exists()
    E.cmd_export(ns(plain=True), data, isatty=lambda: True, input_fn=lambda p: E.PLAIN_PHRASE)
    out = capsys.readouterr().out
    assert "UNENCRYPTED" in out
    (path,) = list((data.data_dir / "exports").glob("*.zip"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with zipfile.ZipFile(path) as z:
        assert "transactions.csv" in z.namelist() and b"PRICEUP" in z.read("transactions.csv")


def test_cli_export_encrypted_and_decrypt_round_trip(data, tmp_path, capsys):
    E.cmd_export(ns(out=str(tmp_path / "mine.zip.enc")), data)
    assert "encrypted export written" in capsys.readouterr().out
    outside = tmp_path.parent / (tmp_path.name + "-extract")
    yes = dict(isatty=lambda: True, input_fn=lambda p: E.PLAIN_PHRASE)
    E.cmd_export(ns(decrypt=str(tmp_path / "mine.zip.enc"), to=str(outside)), data, **yes)
    assert (outside / "transactions.csv").exists()
    # decrypting needs a terminal and the typed phrase, and may not write inside the repository, data_dir or memory
    with pytest.raises(SystemExit, match="aborted"):
        E.cmd_export(ns(decrypt=str(tmp_path / "mine.zip.enc"), to=str(outside) + "2"), data, isatty=lambda: False, input_fn=lambda p: pytest.fail("prompt"))
    with pytest.raises(SystemExit, match="aborted"):
        E.cmd_export(ns(decrypt=str(tmp_path / "mine.zip.enc"), to=str(outside) + "2"), data, isatty=lambda: True, input_fn=lambda p: "yes")
    assert not Path(str(outside) + "2").exists()
    for inside in (tmp_path / "x", data.data_dir / "x", data.memory_dir / "x", data.root):
        with pytest.raises(SystemExit, match="refusing"):
            E.cmd_export(ns(decrypt=str(tmp_path / "mine.zip.enc"), to=str(inside)), data, **yes)
        assert not (inside / "transactions.csv").exists()
    with pytest.raises(SystemExit, match="--to"):
        E.cmd_export(ns(decrypt=str(tmp_path / "mine.zip.enc")), data, isatty=lambda: True)
    with pytest.raises(SystemExit, match="zip"):
        E.cmd_export(ns(format="tar"), data)


def test_parser_has_export_and_wipe_without_a_yes_flag():
    p = cli.build_parser()
    assert p.parse_args(["export", "--plain"]).plain and p.parse_args(["wipe", "--dry-run"]).dry_run
    for argv in (["wipe", "--yes"], ["wipe", "-y"], ["wipe", "--ye"], ["export", "--yes"]):
        with pytest.raises(SystemExit):
            p.parse_args(argv)


# ---------------------------------------------------------------- wipe


class FakeBank:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    def call(self, method, path, **kw):
        self.calls.append((method, path))
        if path in self.fail:
            raise self.fail[path]
        return {}


def answers(*a):
    it = iter(a)

    def inp(prompt=""):
        try:
            return next(it)
        except StopIteration:
            pytest.fail(f"unexpected extra prompt: {prompt!r}")
    return inp


def wargs(**kw):
    return Namespace(**{**dict(dry_run=False, no_export=False, export_to=None, insecure=False), **kw})


def snapshot(cfg):
    return sorted(str(p) for base in (cfg.data_dir, cfg.memory_dir, cfg.backup_dir) for p in base.rglob("*")), dict(secrets_store())


def secrets_store():
    import keyring
    return keyring.get_keyring().store


def test_dry_run_lists_everything_and_changes_nothing(data, capsys):
    before = snapshot(data)
    bank = FakeBank()
    code = W.run_wipe(wargs(dry_run=True), data, isatty=lambda: False, input_fn=lambda p: pytest.fail("no prompt in a dry run"), client=bank)
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "finance.db" in out and "household.yaml" in out and ".history.git" in out
    assert "db_key" in out and "backup_key" in out and "coach-backup-" in out
    assert snapshot(data) == before and bank.calls == []


def test_wipe_needs_a_terminal(data, capsys):
    before = snapshot(data)
    assert W.run_wipe(wargs(), data, isatty=lambda: False, input_fn=lambda p: pytest.fail("prompt")) == 2
    assert "needs a terminal" in capsys.readouterr().out and snapshot(data) == before


def test_a_wrong_phrase_aborts_and_nothing_is_deleted_or_sent(data, tmp_path, capsys):
    before = snapshot(data)
    bank = FakeBank()
    code = W.run_wipe(wargs(export_to=str(tmp_path / "ex.zip.enc")), data, isatty=lambda: True,
                      input_fn=answers("y", "n", "n", "delete my data"), client=bank)
    assert code == 1 and "aborted" in capsys.readouterr().out
    assert snapshot(data) == before and bank.calls == [] and not (tmp_path / "ex.zip.enc").exists()


def test_the_full_wipe_exports_first_then_revokes_then_deletes_but_keeps_what_was_not_asked(data, tmp_path, capsys):
    order = []
    bank = FakeBank()
    real_call = bank.call
    bank.call = lambda m, p, **k: (order.append(("revoke", (tmp_path / "ex.zip.enc").exists(), data.db_path.exists())), real_call(m, p, **k))[1]
    ex = tmp_path / "ex.zip.enc"
    code = W.run_wipe(wargs(export_to=str(ex)), data, isatty=lambda: True, input_fn=answers("y", "n", "n", W.PHRASE), client=bank)
    out = capsys.readouterr().out
    assert code == 0
    assert order and all(o == ("revoke", True, True) for o in order)           # the export existed, the database still did
    assert ("DELETE", "/sessions/s1") in bank.calls or any(m == "DELETE" and p.startswith("/sessions/") for m, p in bank.calls)
    assert all(m == "DELETE" for m, _ in bank.calls)                            # the ONLY network call is the revocation
    assert not data.db_path.exists() and not data.data_dir.exists() and not any(data.memory_dir.glob("*"))
    assert not list(data.root.glob("data*")) and "done." in out
    assert data.backup_dir.exists() and list(data.backup_dir.glob("coach-backup-*"))      # backups were not asked to go
    store = secrets_store()
    assert ("ai-finance-coach", "db_key") in store and ("ai-finance-coach", "backup_key") in store      # Keychain untouched
    z = read_zip(ex)                                                              # the export is complete and readable with the key
    assert "transactions.csv" in z.namelist() and "memory/household.yaml" in z.namelist()


def test_backups_and_keychain_are_deleted_only_when_asked_separately_and_backup_key_is_kept_for_the_export(data, tmp_path):
    ex = tmp_path / "ex.zip.enc"
    W.run_wipe(wargs(export_to=str(ex)), data, isatty=lambda: True, input_fn=answers("n", "y", "y", "n", W.PHRASE), client=FakeBank())
    assert not data.backup_dir.exists()
    assert set(secrets_store()) == {("ai-finance-coach", "backup_key")}            # the others are gone; backup_key stays: the export needs it
    assert ex.exists() and read_zip(ex)


def test_backup_key_is_shown_once_on_the_terminal_when_the_user_chooses_to_delete_it(data, tmp_path, capsys):
    ex = tmp_path / "ex.zip.enc"
    seen = []
    ans = answers("n", "n", "y", "y", W.PHRASE, "")
    W.run_wipe(wargs(export_to=str(ex)), data, isatty=lambda: True, input_fn=lambda p: (seen.append(p), ans(p))[1], client=FakeBank())
    out = capsys.readouterr().out
    assert out.count(KEY) == 1 and "shown once" in out and any("Press Enter" in p for p in seen)
    assert secrets_store() == {}


def test_without_an_export_there_is_no_backup_key_question(data):
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("n", "n", "y", W.PHRASE), client=FakeBank())
    assert secrets_store() == {}


def test_backups_and_exports_inside_data_dir_survive_when_the_user_declined(data, tmp_path):
    inside = data.data_dir / "bk"
    inside.mkdir()
    (inside / data_backup_name()).write_bytes(b"AFCBK1" + b"x" * 40)
    (data.data_dir / "exports").mkdir()
    (data.data_dir / "exports" / "e.zip.enc").write_bytes(b"AFCEX1" + b"x" * 40)
    data.backup_dir = inside
    plan = W.build_plan(data)
    assert set(plan.preserved) == {inside.resolve(), (data.data_dir / "exports").resolve()}
    assert not any("e.zip.enc" in str(p) or "bk" in p.parts for p in plan.data_dir)
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("n", "n", "n", W.PHRASE), client=FakeBank())
    assert not data.db_path.exists() and not (data.data_dir / "logs").exists() and not (data.data_dir / "tls").exists()
    assert (inside / data_backup_name()).exists() and (data.data_dir / "exports" / "e.zip.enc").exists()
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("y", "n", W.PHRASE), client=FakeBank())     # no sessions left to revoke; now the backups go
    assert not data.data_dir.exists()


def data_backup_name():
    return "coach-backup-20260101-000000.tar.enc"


def test_well_known_user_folders_and_foreign_folders_are_never_deleted(data, tmp_path, monkeypatch):
    from coach.config import load_config
    home = tmp_path / "home"
    for d in ("Documents", "Desktop", "Downloads", "Library"):
        (home / d).mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for target in ("Documents", "Desktop", "Downloads", "Library", ""):
        d = home / target if target else home
        with pytest.raises(W.WipeError, match="refusing"):
            W._safe_target(d, data)
    (home / "Documents" / "taxes.pdf").write_text("x")
    # a folder that was not created by coach: no marker file -> refused even when it is a perfectly normal path
    foreign = tmp_path / "proj" / "mydata"
    foreign.mkdir(parents=True)
    (foreign / "thesis.docx").write_text("x")
    with pytest.raises(W.WipeError, match="does not look like"):
        W._safe_target(foreign, data, "data")
    (foreign / "finance.db").write_text("x")
    W._safe_target(foreign, data, "data")                                          # now it carries a coach marker
    stray = tmp_path / "proj" / "bk"
    stray.mkdir()
    (stray / "holiday.jpg").write_text("x")
    with pytest.raises(W.WipeError, match="not coach backups"):
        W._safe_target(stray, data, "backups")
    (tmp_path / "c3").mkdir()
    (tmp_path / "c3" / "config.toml").write_text('data_dir = "mydata_unknown"\nmemory_dir = "memory"\n')
    (tmp_path / "c3" / "mydata_unknown").mkdir()
    (tmp_path / "c3" / "mydata_unknown" / "notes.txt").write_text("x")
    bad = load_config(tmp_path / "c3" / "config.toml", env={})
    with pytest.raises(W.WipeError, match="does not look like"):
        W.build_plan(bad)


def test_without_the_revoke_answer_the_bank_is_never_contacted(data, tmp_path):
    bank = FakeBank()
    W.run_wipe(wargs(export_to=str(tmp_path / "e.zip.enc")), data, isatty=lambda: True, input_fn=answers("n", "n", "n", W.PHRASE), client=bank)
    assert bank.calls == []
    assert not data.db_path.exists()


def test_revocation_failures_are_reported_and_do_not_stop_the_wipe(data, tmp_path, capsys):
    ids = W.build_plan(data, with_sessions=True).sessions
    assert len(ids) >= 3
    bank = FakeBank(fail={f"/sessions/{ids[0]}": ApiError(500, "boom"), f"/sessions/{ids[1]}": ApiError(404, "gone")})
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("y", "n", "n", W.PHRASE), client=bank)
    out = capsys.readouterr().out
    assert f"revoked {len(ids) - 1} of {len(ids)}" in out and "HTTP 500" in out and len(bank.calls) == len(ids)
    assert not data.db_path.exists()


def test_offline_refuses_the_revocation_but_the_wipe_goes_on(data, tmp_path, capsys, monkeypatch):
    data.privacy_offline = True
    from coach.ingest.client import EnableBankingClient
    monkeypatch.setattr("coach.ingest.client.requests.request", lambda *a, **k: pytest.fail("network"))
    c = EnableBankingClient("app", "k.pem", "https://api.example.test", cfg=data)
    c.token = lambda: "jwt"
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("y", "n", "n", W.PHRASE), client=c)
    out = capsys.readouterr().out
    assert "revoked 0 of" in out and "offline" in out and not data.db_path.exists()


def test_a_failed_export_stops_before_anything_is_deleted(data, tmp_path, capsys):
    before = snapshot(data)

    def broken(cfg, out, **kw):
        raise RuntimeError("disk full")
    code = W.run_wipe(wargs(export_to=str(tmp_path / "e.zip.enc")), data, isatty=lambda: True, input_fn=answers("n", "n", "n", W.PHRASE),
                      client=FakeBank(), export_fn=broken)
    assert code == 2 and "nothing was deleted" in capsys.readouterr().out and snapshot(data) == before


def test_no_backup_key_means_no_deletion_without_no_export(data, fake_keyring, capsys):
    del fake_keyring.store[("ai-finance-coach", "backup_key")]
    code = W.run_wipe(wargs(), data, isatty=lambda: True, input_fn=answers("n", "n", "n"), client=FakeBank())
    assert code == 2 and data.db_path.exists() and "backup_key" in capsys.readouterr().out


def test_the_export_may_not_live_inside_what_is_deleted(data, capsys):
    code = W.run_wipe(wargs(export_to=str(data.data_dir / "ex.zip.enc")), data, isatty=lambda: True, input_fn=answers("n", "n", "n"), client=FakeBank())
    assert code == 2 and data.db_path.exists() and "will be deleted" in capsys.readouterr().out


def test_no_export_flag_skips_the_safety_export(data, tmp_path):
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("n", "n", "n", W.PHRASE), client=FakeBank())
    assert not data.db_path.exists()
    assert not list(tmp_path.glob("*.zip.enc"))


def test_template_files_of_the_repository_survive_inside_memory(data, tmp_path):
    mem = data.memory_dir
    (mem / "README.md").write_text("template readme")
    (mem / "contracts").mkdir()
    (mem / "contracts" / "_template.yaml").write_text("id: x\n")
    (mem / "contracts" / "energy.yaml").write_text("id: energy\n")
    W.run_wipe(wargs(no_export=True), data, isatty=lambda: True, input_fn=answers("n", "n", "n", W.PHRASE), client=FakeBank())
    left = sorted(p.relative_to(mem).as_posix() for p in mem.rglob("*") if p.is_file())
    assert left == ["README.md", "contracts/_template.yaml"]
    assert not (mem / ".history.git").exists() and not (mem / ".proposals").exists() and not (mem / "documents").exists()


def test_a_dangerous_target_is_refused(data, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: data.data_dir))
    with pytest.raises(W.WipeError, match="refusing"):
        W.build_plan(data)
    from coach.config import load_config
    (tmp_path / "c2").mkdir()
    (tmp_path / "c2" / "config.toml").write_text('data_dir = ".."\nmemory_dir = "memory"\n')
    bad = load_config(tmp_path / "c2" / "config.toml", env={})
    with pytest.raises(W.WipeError, match="refusing"):
        W.build_plan(bad)


def test_plan_reads_session_ids_only_when_asked(data):
    assert W.build_plan(data).sessions == []
    plan = W.build_plan(data, with_sessions=True)
    assert set(plan.sessions) >= {"sess-secret-1"} and "db_key" in plan.keychain


def test_keychain_deletion_goes_through_one_function(data, monkeypatch):
    deleted = []
    monkeypatch.setattr(secrets, "delete_secret", lambda n: deleted.append(n))
    plan = W.build_plan(data)
    W.execute(plan, data, delete_backups=False, delete_keychain=True, out=lambda *a: None)
    assert set(deleted) == set(plan.keychain) and "db_key" in deleted
