"""E13-1: `coach doctor`: read-only checks with a next step for each problem, never a secret value. tmp homes and the file backend only."""
import json
import os
import stat
from pathlib import Path
from argparse import Namespace

import pytest

from coach import config as config_mod
from coach.setup import doctor as D, init as I


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    I.run_init(tmp_path / "home", interactive=False, container=False, out=lambda *a: None)
    return tmp_path / "home"


def cfg_of(home):
    return config_mod.load_config(home / "config.toml", env={})


def by_id(checks):
    return {c.id: c for c in checks}


def test_a_healthy_fresh_home_has_no_failure_and_points_at_enable_banking(home):
    c = by_id(D.run_checks(cfg_of(home)))
    assert not [x for x in c.values() if x.level == "fail"]
    assert c["python"].level == c["sqlcipher"].level == c["database"].level == c["permissions"].level == "ok"
    assert c["enablebanking"].level == "warn" and "coach setup enablebanking" in c["enablebanking"].hint
    steps = D.next_steps(list(c.values()))
    assert any("coach setup enablebanking" in s for s in steps) and steps[-1].startswith("coach setup")


def test_the_sqlcipher_check_proves_the_library_encrypts():
    c = D.check_sqlcipher()
    assert c.level == "ok" and "cipher" in c.detail


def test_old_python_is_a_failure(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(D.sys, "version_info", SimpleNamespace(major=3, minor=9, micro=1))
    c = D.check_python()
    assert c.level == "fail" and "3.11" in c.hint


def test_a_missing_secret_fails_with_the_command_to_create_it(home, tmp_path):
    os.remove(tmp_path / "secrets" / "db_key")
    c = by_id(D.run_checks(cfg_of(home)))
    assert c["secret.db_key"].level == "fail" and "coach init" in c["secret.db_key"].hint
    assert c["database"].level == "fail"                                       # it cannot be opened without its key
    assert c["secret.proposal_key"].level == "ok"


def test_a_secret_file_open_to_others_is_reported_not_read(home, tmp_path):
    os.chmod(tmp_path / "secrets" / "backup_key", 0o644)
    c = by_id(D.run_checks(cfg_of(home)))
    assert c["secret.backup_key"].level == "fail" and "chmod 600" in c["secret.backup_key"].detail


def test_a_missing_database_and_a_missing_config_are_failures_with_hints(home, tmp_path):
    cfg = cfg_of(home)
    os.remove(cfg.db_path)
    assert D.check_database(cfg).level == "fail" and "coach init" in D.check_database(cfg).hint
    nocfg = type("C", (), {"config_path": None, "root": tmp_path / "nowhere"})()
    assert D.check_config(nocfg).level == "fail" and "coach init" in D.check_config(nocfg).hint


def test_loose_permissions_are_a_warning_with_the_fix_command(home):
    cfg = cfg_of(home)
    os.chmod(cfg.data_dir, 0o755)
    c = D.check_permissions(cfg)
    assert c.level == "warn" and "--fix-permissions" in c.hint and "data_dir" in c.detail
    os.chmod(home / "config.toml", 0o644)
    assert D.check_config(cfg).level == "warn"


def test_enable_banking_states(home, tmp_path):
    key = tmp_path / "eb.pem"
    text = (home / "config.toml").read_text()
    text = config_mod.set_toml_value(text, "enable_banking", "app_id", "00000000-1111-2222-3333-444444444444")
    text = config_mod.set_toml_value(text, "enable_banking", "redirect_url", "https://localhost:8443/callback")
    text = config_mod.set_toml_value(text, "enable_banking", "private_key_path", str(key))
    (home / "config.toml").write_text(text)
    cfg = cfg_of(home)
    assert D.check_enable_banking(cfg)[0].level == "fail" and "does not exist" in D.check_enable_banking(cfg)[0].detail
    key.write_text("not a key")
    os.chmod(key, 0o644)
    levels = {c.id: c.level for c in D.check_enable_banking(cfg)}
    assert levels == {"enablebanking": "ok", "enablebanking.key": "warn", "enablebanking.perms": "warn"}
    key.write_text("-----BEGIN " + "PRIVATE KEY-----\nAAAA\n-----END " + "PRIVATE KEY-----\n")
    os.chmod(key, 0o600)
    assert [c.level for c in D.check_enable_banking(cfg)] == ["ok"]


def test_the_claude_command_matters_only_for_the_claude_code_backend(home, monkeypatch):
    cfg = cfg_of(home)
    monkeypatch.setattr(D.shutil, "which", lambda name: None)
    assert D.check_claude_cli(cfg).level == "warn" and "anthropic-api" in D.check_claude_cli(cfg).hint
    cfg.llm_backend = cfg.coach_backend = "ollama"
    assert D.check_claude_cli(cfg).level == "info"
    monkeypatch.setattr(D.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert D.check_claude_cli(cfg).level == "info"


def test_in_a_container_the_claude_code_backend_is_a_failure(home, monkeypatch):
    monkeypatch.setenv("COACH_IN_CONTAINER", "1")
    cfg = cfg_of(home)
    c = D.check_claude_cli(cfg)
    assert c.level == "fail" and "anthropic-api" in c.hint
    cfg.llm_backend = cfg.coach_backend = "anthropic-api"
    assert D.check_claude_cli(cfg).level == "info"
    assert by_id(D.check_backends(cfg))["anthropic_key"].level == "fail"       # the API backend needs its key


def test_a_root_owned_0755_secrets_folder_is_ok_but_a_world_writable_one_is_not(home, tmp_path, monkeypatch):
    sdir = tmp_path / "secrets"
    os.chmod(sdir, 0o755)
    assert [c for c in D.check_secret_store() if c.id == "secrets.backend"][0].level == "ok"
    real = Path.stat

    def as_root(self, *a, **k):
        st = real(self, *a, **k)
        if self == sdir:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0, st.st_gid, st.st_size, 0, 0, 0))
        return st
    monkeypatch.setattr(Path, "stat", as_root)
    monkeypatch.setattr(D.os, "getuid", lambda: 10001)
    c = [c for c in D.check_secret_store() if c.id == "secrets.backend"][0]
    assert c.level == "ok" and c.hint == ""                                    # root-owned 0755: the compose layout
    os.chmod(sdir, 0o777)
    assert [c for c in D.check_secret_store() if c.id == "secrets.backend"][0].level == "warn"


def test_the_keychain_is_checked_through_the_backend_in_use(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.delenv("COACH_SECRETS_BACKEND", raising=False)
    cs = {c.id: c for c in D.check_secret_store()}
    assert cs["secret.db_key"].level == "fail"
    import keyring
    keyring.set_password("ai-finance-coach", "db_key", "v")
    assert {c.id: c for c in D.check_secret_store()}["secret.db_key"].detail.startswith("set (keychain)")


def test_the_command_prints_next_steps_exits_1_on_a_failure_and_never_prints_a_secret(home, tmp_path, capsys):
    cfg = cfg_of(home)
    D.cmd_doctor(Namespace(json=False), cfg)
    out = capsys.readouterr().out
    secret_values = [(tmp_path / "secrets" / n).read_text().strip() for n in ("db_key", "backup_key", "proposal_key")]
    assert "Next steps" in out and not any(v in out for v in secret_values)
    os.remove(tmp_path / "secrets" / "db_key")
    with pytest.raises(SystemExit) as e:
        D.cmd_doctor(Namespace(json=True), cfg)
    assert e.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and any(c["id"] == "secret.db_key" and c["level"] == "fail" for c in data["checks"])
    assert data["next_steps"] and not any(v in json.dumps(data) for v in secret_values[1:])


def test_doctor_is_registered_and_changes_nothing(home):
    from coach.cli import build_parser
    assert build_parser().parse_args(["doctor", "--json"]).fn is D.cmd_doctor
    before = {p: stat.S_IMODE(p.stat().st_mode) for p in home.rglob("*")}
    D.run_checks(cfg_of(home))
    assert before == {p: stat.S_IMODE(p.stat().st_mode) for p in home.rglob("*")}
