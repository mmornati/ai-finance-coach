"""E11-2 / E11-3: `coach security audit` on crafted installations (bad permissions, plaintext leftovers, planted secrets, exposed sockets).

Every path is under tmp_path; lsof is injected (the real command never runs); the Keychain is the in-memory fake of conftest.
"""
import json
import os
import sqlite3
import stat
import time
from argparse import Namespace
from pathlib import Path

import pytest

from coach import secrets, security as S
from coach.backup import create_backup
from coach.config import load_config
from coach.db import connect

REPO = Path(__file__).resolve().parents[1]
SECRET_VALUE = "SUPER-SECRET-VALUE-0123456789-abcdefXYZ"  # allowlist secret: a fake
GOOD_GITIGNORE = (REPO / ".gitignore").read_text()


def by_id(checks, cid):
    return next(c for c in checks if c.id == cid)


def mkcfg(root: Path, extra: str = ""):
    (root / "memory").mkdir(exist_ok=True)
    (root / "config.toml").write_text('data_dir = "data"\nmemory_dir = "memory"\n[backup]\ndir = "backups"\nretention = 3\n' + extra)
    return load_config(root / "config.toml", env={})


@pytest.fixture
def install(tmp_path, fake_keyring, monkeypatch):
    """A clean installation: encrypted DB, secrets in the (fake) Keychain, private modes, .gitignore, deny rules, a backup."""
    root = tmp_path / "proj"
    root.mkdir()
    key = tmp_path / "outside" / "eb.pem"
    key.parent.mkdir()
    key.write_text("not a key\n")
    os.chmod(key, 0o600)
    cfg = mkcfg(root, f'[enable_banking]\napp_id = "app"\nprivate_key_path = "{key}"\nredirect_url = "https://localhost:8443/callback"\n')
    for n in ("db_key", "backup_key", "proposal_key"):
        secrets.set_secret(n, SECRET_VALUE + n)
    (root / ".gitignore").write_text(GOOD_GITIGNORE)
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Bash(*memory accept*)"]}}))
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"finance": {"command": "uv", "args": ["run", "coach", "mcp", "serve"]}}}))
    con = connect(cfg, create=True)
    con.close()
    create_backup(cfg)
    for p in [cfg.data_dir, cfg.backup_dir, cfg.memory_dir]:
        os.chmod(p, 0o700)
    (cfg.memory_dir / "household.yaml").write_text("members: []\n")
    os.chmod(cfg.memory_dir / "household.yaml", 0o600)
    return cfg


def audit(cfg, **kw):
    kw.setdefault("lsof", lambda: "")
    return S.audit(cfg, **kw)


def test_a_clean_installation_has_no_critical(install):
    checks = audit(install)
    assert S.exit_code(checks) == 0, [(c.id, c.status, c.title) for c in checks if c.status in ("critical",)]
    assert by_id(checks, "db_encrypted").status == "ok" and by_id(checks, "eb_key").status == "ok"
    assert by_id(checks, "backups_encrypted").status == "ok" and by_id(checks, "gitignore").status == "ok"
    assert by_id(checks, "secret_scan").status == "ok" and by_id(checks, "mcp_stdio").status == "ok"
    assert by_id(checks, "ui_bind").status == "ok" and by_id(checks, "callback_loopback").status == "ok"
    assert by_id(checks, "plaintext_leftovers").status == "ok" and by_id(checks, "perms_secret_files").status == "ok"


def test_no_secret_value_is_ever_printed(install):
    checks = audit(install)
    text = S.format_report(checks) + json.dumps([c.as_dict() for c in checks])
    assert SECRET_VALUE not in text
    assert "set (keychain)" in text.replace("is in the Keychain", "set (keychain)") or "Keychain" in text


# ---------------------------------------------------------------- secrets


def test_missing_keys_are_reported(install, fake_keyring):
    del fake_keyring.store[("ai-finance-coach", "db_key")]
    del fake_keyring.store[("ai-finance-coach", "proposal_key")]
    checks = audit(install)
    assert by_id(checks, "db_key").status == "critical"
    assert by_id(checks, "proposal_key").status == "warn" and "SHA-256" in by_id(checks, "proposal_key").detail
    assert S.exit_code(checks) == 1


def test_a_key_from_the_environment_is_a_warning(install, fake_keyring):
    del fake_keyring.store[("ai-finance-coach", "db_key")]
    checks = audit(install, env={"COACH_DB_KEY": SECRET_VALUE})
    assert by_id(checks, "db_key").status == "warn"
    assert SECRET_VALUE not in S.format_report(checks)


def test_an_enabled_channel_without_its_secret_warns(install):
    cfg = mkcfg(install.root, '[alerts.telegram]\nenabled = true\nchat_id = "424242"\n')
    checks = audit(cfg)
    assert by_id(checks, "telegram_bot_token").status == "warn"


def test_eb_key_permissions_and_location(install, tmp_path):
    kp = Path(install.eb_private_key_path)
    os.chmod(kp, 0o644)
    c = by_id(audit(install), "eb_key")
    assert c.status == "critical" and "0o644" in c.title
    os.chmod(kp, 0o600)
    inside = install.root / "keys" / "eb.pem"
    inside.parent.mkdir()
    inside.write_text("x")
    os.chmod(inside, 0o600)
    cfg = mkcfg(install.root, f'[enable_banking]\napp_id = "a"\nprivate_key_path = "{inside}"\n')
    checks = audit(cfg)
    assert by_id(checks, "eb_key").status == "warn" and "inside the project" in by_id(checks, "eb_key").title
    assert by_id(checks, "pem_in_repo").status == "warn"
    missing = mkcfg(install.root, '[enable_banking]\napp_id = "a"\nprivate_key_path = "/nonexistent/k.pem"\n')
    assert by_id(audit(missing), "eb_key").status == "warn"


def test_session_key_age(install):
    kf = install.data_dir / "ui-session.key"
    kf.write_bytes(b"k" * 32)
    os.chmod(kf, 0o600)
    assert by_id(audit(install), "session_key_age").status == "ok"
    old = time.time() - 90 * 86400
    os.utime(kf, (old, old))
    c = by_id(audit(install), "session_key_age")
    assert c.status == "warn" and "rotate-session-key" in c.detail


# ---------------------------------------------------------------- storage


def test_a_plaintext_database_is_critical(tmp_path, fake_keyring):
    root = tmp_path / "p"
    root.mkdir()
    cfg = mkcfg(root)
    cfg.data_dir.mkdir()
    sqlite3.connect(cfg.db_path).execute("CREATE TABLE t(x)").connection.commit()
    c = by_id(audit(cfg), "db_encrypted")
    assert c.status == "critical" and "PLAINTEXT" in c.title


def test_plaintext_leftovers_and_scratch_copies_are_found(install):
    leftover = install.data_dir / "finance.db.plaintext.bak"
    sqlite3.connect(leftover).execute("CREATE TABLE t(x)").connection.commit()
    scratch = install.root / "scratch.db"
    sqlite3.connect(scratch).execute("CREATE TABLE t(x)").connection.commit()
    enc_scratch = install.root / "restored" / "finance.db"
    enc_scratch.parent.mkdir()
    enc_scratch.write_bytes(install.db_path.read_bytes())
    checks = audit(install)
    assert by_id(checks, "plaintext_leftovers").status == "critical"
    assert any("finance.db.plaintext.bak" in i for i in by_id(checks, "plaintext_leftovers").items)
    plain = by_id(checks, "plaintext_db_files")
    assert plain.status == "critical" and any("scratch.db" in i for i in plain.items)
    assert by_id(checks, "scratch_db").status == "warn" and any("restored" in i for i in by_id(checks, "scratch_db").items)
    assert S.exit_code(checks) == 1


def test_open_permissions_are_reported_and_secret_files_are_critical(install):
    key = install.data_dir / "ui-session.key"
    key.write_bytes(b"x" * 32)
    os.chmod(key, 0o644)
    os.chmod(install.memory_dir, 0o755)
    (install.memory_dir / "profile.md").write_text("p")
    os.chmod(install.memory_dir / "profile.md", 0o644)
    os.chmod(install.backup_dir, 0o755)
    checks = audit(install)
    assert by_id(checks, "perms_secret_files").status == "critical"
    d = by_id(checks, "perms_dirs")
    assert d.status == "warn" and any("memory" in i for i in d.items) and any("backups" in i for i in d.items)
    # the fix touches only data_dir / backups / memory and makes the audit clean
    changed = S.fix_permissions(install)
    assert changed and stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(install.memory_dir.stat().st_mode) == 0o700
    again = audit(install)
    assert by_id(again, "perms_secret_files").status == "ok" and by_id(again, "perms_dirs").status == "ok"


def test_backups_must_be_encrypted_and_recent(install):
    (install.backup_dir / "notes.tar").write_bytes(b"plain tar")
    (install.backup_dir / "copy.db").write_bytes(b"SQLite format 3\x00" + b"x" * 100)
    c = by_id(audit(install), "backups_encrypted")
    assert c.status == "critical" and len(c.items) == 2
    for p in install.backup_dir.glob("coach-backup-*"):
        p.unlink()
    for p in ("notes.tar", "copy.db"):
        (install.backup_dir / p).unlink()
    assert by_id(audit(install), "backups_present").status == "warn"


def test_retention_and_a_stale_backup(install):
    for i in range(5):
        p = install.backup_dir / f"coach-backup-2026010{i + 1}-000000.tar.enc"
        p.write_bytes(S.BACKUP_MAGIC + b"x" * 40)
    c = by_id(audit(install), "backups_retention")
    assert c.status == "warn" and "retention" in c.title


# ---------------------------------------------------------------- .gitignore


def test_gitignore_matcher():
    pats = S.gitignore_patterns("data/\n**/secret/\n*.pem\n/config.toml\nmemory/*\n!memory/README.md\n.env.*\n!.env.example\n# c\n")
    for yes in ("data/finance.db", "a/b/data/x", "x/secret/k", "k.pem", "a/b/k.pem", "config.toml", "memory/household.yaml", ".env.local", "web/.env.local"):
        assert S.is_ignored(pats, yes), yes
    for no in ("src/data_loader.py", "sub/config.toml", "memory/README.md", ".env.example", "src/coach/x.py"):
        assert not S.is_ignored(pats, no), no


def test_the_projects_own_gitignore_covers_every_probe():
    pats = S.gitignore_patterns(GOOD_GITIGNORE)
    missing = [p for p, _ in S.GITIGNORE_PROBES if not S.is_ignored(pats, p)]
    assert missing == []


def test_gitignore_gaps_are_reported(install):
    (install.root / ".gitignore").write_text("data/\n")
    c = by_id(audit(install), "gitignore")
    assert c.status == "critical" and any("config.toml" in i for i in c.items) and any("*.pem" in i for i in c.items)
    (install.root / ".gitignore").unlink()
    assert by_id(audit(install), "gitignore").status == "critical"
    (install.root / ".gitignore").write_text(GOOD_GITIGNORE.replace("ui-*.json\n", ""))
    c = by_id(audit(install), "gitignore")
    assert c.status == "warn" and any("ui-state.json" in i for i in c.items)


# ---------------------------------------------------------------- the working-tree secret scan


def plant(cfg, rel, text):
    p = cfg.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_secret_scan_finds_planted_secrets_without_printing_them(install):
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"
    plant(install, "notes/key.txt", pem)
    plant(install, "src/x.py", 'ANTHROPIC = "sk-ant-api03-' + "A1b2C3d4E5f6G7h8I9j0" * 2 + '"\n')
    plant(install, "src/y.py", 'telegram = "123456789:AAH' + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5pQ" + '"\n')
    plant(install, "cfg/app.py", 'api_key = "' + "kJ8s9Df7Gh3Lq2Wx5Zc1Vb4Nm6Rt0Yp" + '"\n')
    plant(install, "docs/ok.py", 'password = "ChangeMeChangeMeChangeMeChangeMe"  # allowlist secret\n')
    plant(install, "docs/fixture.py", 'sk-ant-api03-' + "Z" * 30 + "  # allowlist secret\n")
    c = by_id(audit(install), "secret_scan")
    assert c.status == "critical"
    text = " ".join(c.items)
    for expect in ("notes/key.txt:1", "src/x.py:1", "src/y.py:1"):
        assert expect in text, (expect, text)
    assert "cfg/app.py:1" in text and "docs/ok.py" not in text and "docs/fixture.py" not in text
    rep = S.format_report(audit(install)) + json.dumps([x.as_dict() for x in audit(install)])
    assert "MIIE" not in rep and "A1b2C3d4E5f6" not in rep and "kJ8s9Df7" not in rep and "AAHa1B2" not in rep


def test_secret_scan_skips_data_memory_backups_and_build_folders(install):
    pem = "-----BEGIN " + "PRIVATE KEY-----\n"
    for rel in ("data/k.txt", "memory/k.txt", "backups/k.txt", "node_modules/x/k.txt", ".venv/lib/k.txt", "src/coach/api/static/k.txt"):
        plant(install, rel, pem)
    assert by_id(audit(install), "secret_scan").status == "ok"


def test_scan_text_only_flags_secret_like_assignments():
    assert S.scan_text("x = 1\nname = 'hello world'\nhash = 'a' * 40\n") == []
    assert S.scan_text("token = 'abcdefabcdefabcdefabcdefabcdefab'\n") == []              # hex digest: not random-looking mixed case
    sev = S.scan_text("secret_key = 'Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm'\n")              # allowlist secret: a fake
    assert sev and sev[0][1] == "warn"


# ---------------------------------------------------------------- exposure (E11-3)

LSOF_SAMPLE = """COMMAND   PID    USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
rapportd  512 appuser    8u  IPv4 0x1234567890      0t0  TCP *:49152 (LISTEN)
Python  71234 appuser    5u  IPv4 0xabcdef0123      0t0  TCP 127.0.0.1:8765 (LISTEN)
Python  71240 appuser    7u  IPv6 0xabcdef0124      0t0  TCP [::1]:8443 (LISTEN)
Python  71300 appuser    9u  IPv4 0xabcdef0999      0t0  TCP *:5000 (LISTEN)
"""


def test_parse_lsof():
    rows = S.parse_lsof(LSOF_SAMPLE)
    assert {(r["command"], r["addr"], r["port"]) for r in rows} == {
        ("rapportd", "*", 49152), ("Python", "127.0.0.1", 8765), ("Python", "::1", 8443), ("Python", "*", 5000)}
    assert S.parse_lsof("") == [] and S.parse_lsof("garbage line\n") == []


def test_listening_sockets_loopback_is_ok_and_wildcard_is_critical(install):
    ok = by_id(audit(install, lsof=lambda: "\n".join(LSOF_SAMPLE.splitlines()[:4])), "listening_sockets")
    assert ok.status == "ok" and "web app 8765" in ok.title
    open_ui = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nPython 7 me 5u IPv4 0x1 0t0 TCP *:8765 (LISTEN)\n"
    c = by_id(audit(install, lsof=lambda: open_ui), "listening_sockets")
    assert c.status == "critical" and "8765" in c.items[0] and S.exit_code(audit(install, lsof=lambda: open_ui)) == 1
    other = by_id(audit(install, lsof=lambda: LSOF_SAMPLE), "listening_sockets")
    assert other.status == "warn" and any("5000" in i for i in other.items)
    assert by_id(audit(install, lsof=lambda: None), "listening_sockets").status == "skip"


def test_ui_bind_configuration(install):
    assert by_id(audit(install), "ui_bind").status == "ok"
    wide = mkcfg(install.root, '[ui]\nhost = "0.0.0.0"\n')
    assert by_id(audit(wide), "ui_bind").status == "critical"
    remote = mkcfg(install.root, '[ui]\nallow_remote = true\nhost = "0.0.0.0"\n')
    c = by_id(audit(remote), "ui_bind")
    assert c.status == "critical" and "remote_tls_ack" in c.title and "allowed_hosts" in c.title
    safe = mkcfg(install.root, '[ui]\nallow_remote = true\nremote_tls_ack = true\nallowed_hosts = ["mac.tail.ts.net"]\n')
    assert by_id(audit(safe), "ui_bind").status == "warn"


def test_mcp_must_be_stdio_only(install):
    assert by_id(audit(install), "mcp_stdio").status == "ok"
    (install.root / ".mcp.json").write_text(json.dumps({"mcpServers": {"finance": {"type": "sse", "url": "http://localhost:9000/sse"}}}))
    c = by_id(audit(install), "mcp_stdio")
    assert c.status == "critical" and "network MCP server" in c.title


def test_the_shipped_mcp_server_is_stdio_only():
    src = (REPO / "src" / "coach" / "mcp" / "server.py").read_text()
    assert "stdio_server" in src
    for bad in ("uvicorn", "sse_server", "streamable_http", "socket", "HTTPServer"):
        assert bad not in src


def test_callback_server_loopback(install):
    ok = by_id(audit(install), "callback_loopback")
    assert ok.status == "ok"
    remote = mkcfg(install.root, '[enable_banking]\nredirect_url = "https://example.org/callback"\n')
    assert by_id(audit(remote), "callback_loopback").status == "warn"


def test_tls_files_must_be_private(install):
    install.tls_dir.mkdir()
    k = install.tls_dir / "localhost.key"
    k.write_text("k")
    os.chmod(k, 0o644)
    assert by_id(audit(install), "tls_perms").status == "critical"


def test_agent_rules(install):
    assert by_id(audit(install), "agent_rules").status == "ok"
    (install.root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": []}}))
    assert by_id(audit(install), "agent_rules").status == "warn"
    (install.root / ".claude" / "settings.json").unlink()
    assert by_id(audit(install), "agent_rules").status == "warn"


# ---------------------------------------------------------------- the command


def test_command_exit_code_text_and_json(install, capsys, monkeypatch):
    monkeypatch.setattr(S, "default_lsof", lambda: "")
    S.cmd_audit(Namespace(json=False, fix_permissions=False), install)
    out = capsys.readouterr().out
    assert "== secrets ==" in out and "== storage ==" in out and "== exposure ==" in out and "critical" in out
    S.cmd_audit(Namespace(json=True, fix_permissions=False), install)
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["critical"] == 0 and {c["area"] for c in data["checks"]} >= {"secrets", "storage", "repository", "exposure"}
    install.db_path.with_name("finance.db.plaintext.bak").write_bytes(b"SQLite format 3\x00" + b"x" * 50)
    with pytest.raises(SystemExit) as e:
        S.cmd_audit(Namespace(json=True, fix_permissions=False), install)
    assert e.value.code == 1
    assert json.loads(capsys.readouterr().out)["summary"]["critical"] >= 1


def test_the_command_is_registered():
    from coach import cli
    args = cli.build_parser().parse_args(["security", "audit", "--json", "--fix-permissions"])
    assert args.json and args.fix_permissions and args.fn is S.cmd_audit


# ---------------------------------------------------------------- the weekly warn-only step of `schedule run`


def test_schedule_runs_the_audit_weekly_and_never_fails_the_job(tmp_path, db_key, monkeypatch):
    from coach import schedule as sch
    from types import SimpleNamespace
    root = tmp_path / "p"
    root.mkdir()
    cfg = mkcfg(root, "[privacy]\noffline = true\n")
    connect(cfg, create=True).close()
    monkeypatch.setattr(S, "default_lsof", lambda: pytest.fail("lsof must not run from the scheduled job"))
    out = []
    code = sch.run_daily(cfg, client=SimpleNamespace(), out=out.append)
    first = "\n".join(out)
    assert code == 0 and "security: critical=" in first
    out.clear()
    sch.run_daily(cfg, client=SimpleNamespace(), out=out.append)
    assert "security: not due (weekly)" in "\n".join(out)
    marker = cfg.log_dir / "security-audit.last"
    assert marker.exists()
    old = time.time() - 8 * 86400
    marker.write_text("2020-01-01T00:00:00")
    out.clear()
    sch.run_daily(cfg, client=SimpleNamespace(), out=out.append)
    assert "security: critical=" in "\n".join(out)


def test_a_failing_audit_does_not_fail_the_job(tmp_path, db_key, monkeypatch):
    from coach import schedule as sch
    from types import SimpleNamespace
    root = tmp_path / "p"
    root.mkdir()
    cfg = mkcfg(root, "[privacy]\noffline = true\n")
    connect(cfg, create=True).close()
    monkeypatch.setattr(S, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = []
    code = sch.run_daily(cfg, client=SimpleNamespace(), out=out.append)
    assert code == 0 and "security: could not run" in "\n".join(out)


# ---------------------------------------------------------------- review round: header detection, exports, fix-permissions


def test_plaintext_sqlite_is_found_by_header_in_every_file_of_data_dir(install):
    for name, head in (("copy.dat", S.SQLITE_MAGIC), ("finance.db-journal", S.JOURNAL_MAGIC + b"x" * 8), ("finance.db-wal", S.WAL_MAGICS[0] + b"x" * 12),
                       ("logs/old.txt", S.SQLITE_MAGIC)):
        p = install.data_dir / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(head + b"\0" * 50)
        os.chmod(p, 0o600)
    c = by_id(audit(install), "plaintext_db_files")
    assert c.status == "critical" and len(c.items) == 4
    for n in ("copy.dat", "finance.db-journal", "finance.db-wal", "old.txt"):
        assert any(n in i for i in c.items), n


def test_exports_must_be_encrypted(install):
    d = install.data_dir / "exports"
    d.mkdir()
    ok = d / "a.zip.enc"
    ok.write_bytes(S.EXPORT_MAGIC + b"x" * 40)
    assert by_id(audit(install), "exports_plaintext").status == "ok"
    (d / "b.zip").write_bytes(b"PK\x03\x04 zip")
    (d / "c.zip.enc").write_bytes(b"not encrypted at all")
    (install.root / "coach-export-1.zip").write_bytes(b"PK\x03\x04")
    c = by_id(audit(install), "exports_plaintext")
    assert c.status == "critical" and len(c.items) == 3


def test_fix_permissions_only_tightens(install):
    ro = install.memory_dir / "readonly.yaml"
    ro.write_text("x")
    os.chmod(ro, 0o444)                                   # group / other can read: tightened to 0400 (the owner bit stays, nothing is added)
    ex = install.memory_dir / "tool.sh"
    ex.write_text("x")
    os.chmod(ex, 0o755)
    private = install.memory_dir / "private.yaml"
    private.write_text("x")
    os.chmod(private, 0o400)
    os.chmod(install.memory_dir, 0o755)
    S.fix_permissions(install)
    assert stat.S_IMODE(ro.stat().st_mode) == 0o400 and stat.S_IMODE(ex.stat().st_mode) == 0o700
    assert stat.S_IMODE(private.stat().st_mode) == 0o400                      # an already-private file keeps its (smaller) mode
    assert stat.S_IMODE(install.memory_dir.stat().st_mode) == 0o700


def test_fix_permissions_refuses_the_home_folder_and_its_parents(install, monkeypatch):
    victim = install.data_dir
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: victim))
    os.chmod(victim / "finance.db", 0o644)
    out = S.fix_permissions(install)
    assert any("refused" in o and "home folder" in o for o in out)
    assert stat.S_IMODE((victim / "finance.db").stat().st_mode) == 0o644         # untouched
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: victim / "sub" / "deeper"))      # the base is a PARENT of home
    assert any("refused" in o for o in S.fix_permissions(install))


def test_the_open_key_file_fix_is_offered_on_a_terminal_only(install):
    kp = Path(install.eb_private_key_path)
    os.chmod(kp, 0o644)
    note = S.fix_key_file(install, isatty=lambda: False, input_fn=lambda p: pytest.fail("no prompt without a terminal"))
    assert "terminal" in note and stat.S_IMODE(kp.stat().st_mode) == 0o644
    assert S.fix_key_file(install, isatty=lambda: True, input_fn=lambda p: "n") is None and stat.S_IMODE(kp.stat().st_mode) == 0o644
    assert "->" in S.fix_key_file(install, isatty=lambda: True, input_fn=lambda p: "y") and stat.S_IMODE(kp.stat().st_mode) == 0o600
    assert S.fix_key_file(install, isatty=lambda: True, input_fn=lambda p: pytest.fail("already private")) is None
