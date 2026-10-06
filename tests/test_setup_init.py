"""E13-1: `coach init` builds a fresh, private home: configuration with its comments, 0700 data folder, memory skeleton (templates only),
secrets after a typed confirmation, an empty encrypted database. Idempotent, never overwrites. Tmp dirs and the fake keyring only: the
real Keychain, the real database and the real memory are never touched."""
import os
import stat
from argparse import Namespace

import keyring
import pytest

from coach import config as config_mod, db as db_mod, home as home_mod
from coach.setup import init as I


@pytest.fixture
def filesec(tmp_path, monkeypatch):
    """The file backend in tmp_path: no Keychain, no prompt needed."""
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    return tmp_path / "secrets"


def run(home, **kw):
    kw.setdefault("interactive", False)
    kw.setdefault("container", False)
    kw.setdefault("out", lambda *a: None)
    return I.run_init(home, **kw)


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_a_fresh_home_gets_config_data_memory_secrets_and_an_encrypted_empty_database(tmp_path, filesec):
    home = tmp_path / "home"
    rep = run(home)
    assert rep.ok and [s.status for s in rep.steps if s.name in ("config", "data_dir", "memory", "secrets", "database")] == ["created"] * 5
    cfg = config_mod.load_config(home / "config.toml", env={})
    assert cfg.data_dir == home / "data" and cfg.memory_dir == home / "memory"
    assert mode(home) == 0o700 and mode(home / "config.toml") == 0o600 and mode(cfg.data_dir) == 0o700
    assert db_mod.is_plaintext(cfg.db_path) is False                       # SQLCipher, not plaintext
    con = db_mod.connect(cfg)
    assert db_mod.status(con)["pending"] == [] and db_mod.table_counts(con)["transactions"] == 0
    con.close()
    for name in ("db_key", "backup_key", "proposal_key"):
        assert mode(filesec / name) == 0o600 and (filesec / name).read_text().strip()
    assert mode(filesec) == 0o700


def test_the_configuration_keeps_its_comments_and_the_import_profiles_are_copied(tmp_path, filesec):
    home = tmp_path / "home"
    run(home)
    text = (home / "config.toml").read_text()
    template = home_mod.config_template().read_text()
    assert text == template                                                 # a default init changes nothing in the commented example
    assert "# Copy to" in text and "[enable_banking]" in text
    assert sorted(p.name for p in (home / "config" / "import_profiles").iterdir()) == sorted(p.name for p in home_mod.import_profiles_template().iterdir())


def test_data_and_memory_dirs_are_written_into_a_new_configuration_with_the_comment_kept(tmp_path, filesec):
    home = tmp_path / "home"
    run(home, data_dir=str(tmp_path / "vol-data"), memory_dir=str(tmp_path / "vol-mem"))
    cfg = config_mod.load_config(home / "config.toml", env={})
    assert cfg.data_dir == tmp_path / "vol-data" and cfg.memory_dir == tmp_path / "vol-mem"
    assert "# database, logs" in (home / "config.toml").read_text()
    assert (tmp_path / "vol-mem" / "household.yaml").is_file() and mode(tmp_path / "vol-data") == 0o700


def test_the_memory_skeleton_is_templates_only_and_valid(tmp_path, filesec):
    home = tmp_path / "home"
    run(home)
    mem = home / "memory"
    shipped = {p.as_posix() for p in home_mod.template_files(home_mod.memory_template())}
    created = {p.as_posix() for p in home_mod.template_files(mem)}
    assert created == shipped and "liabilities/_template.yaml" in created and "contracts/_template.yaml" in created
    assert all(mode(mem / rel) == 0o600 for rel in created)
    from coach.memory.store import MemoryStore
    store = MemoryStore(mem, history=False)
    assert store.model("household.yaml").members == [] and store.model("assets.yaml").assets == []
    from coach.memory import check as check_mod
    assert not [i for i in check_mod.run_check(store, None) if i.level == "error"]


def test_a_second_run_changes_nothing_and_never_overwrites(tmp_path, filesec):
    home = tmp_path / "home"
    run(home)
    (home / "config.toml").write_text('data_dir = "data"\nmemory_dir = "memory"\n# my edit\n')
    (home / "memory" / "profile.md").write_text("my own profile\n")
    key_before = (filesec / "db_key").read_text()
    rep = run(home)
    assert [s.status for s in rep.steps if s.name in ("config", "data_dir", "memory", "secrets", "database")] == ["exists"] * 5
    assert "# my edit" in (home / "config.toml").read_text()
    assert (home / "memory" / "profile.md").read_text() == "my own profile\n"
    assert (filesec / "db_key").read_text() == key_before


def test_a_secret_from_the_environment_is_kept_and_not_regenerated(tmp_path, filesec, monkeypatch):
    monkeypatch.setenv("COACH_DB_KEY", "env-key")
    rep = run(tmp_path / "home")
    assert rep.get("secrets").status == "created" and not (filesec / "db_key").exists()
    assert (filesec / "backup_key").exists() and (filesec / "proposal_key").exists()
    assert rep.get("database").status == "created"                          # opened with the key from the environment


def test_keychain_without_a_terminal_creates_nothing(tmp_path, fake_keyring):
    rep = run(tmp_path / "home", interactive=False, isatty=lambda: False)     # --non-interactive does NOT bypass the Keychain confirmation
    assert rep.get("secrets").status == "skipped" and "terminal" in rep.get("secrets").detail
    assert rep.get("database").status == "skipped" and fake_keyring.store == {}
    assert rep.ok


def test_keychain_needs_the_typed_word_and_prints_no_value(tmp_path, fake_keyring):
    said = []
    rep = run(tmp_path / "h1", isatty=lambda: True, input_fn=lambda p: "yes please", out=said.append)
    assert rep.get("secrets").status == "skipped" and fake_keyring.store == {}
    said.clear()
    rep = run(tmp_path / "h2", isatty=lambda: True, input_fn=lambda p: "Generate", out=said.append)
    assert rep.get("secrets").status == "created" and rep.get("database").status == "created"
    names = sorted(k[1] for k in fake_keyring.store)
    assert names == ["backup_key", "db_key", "proposal_key"] and all(k[0] == "ai-finance-coach" for k in fake_keyring.store)
    text = "\n".join(map(str, said)) + "\n".join(f"{s.name} {s.detail}" for s in rep.steps)
    assert not any(v in text for v in fake_keyring.store.values())            # the values are never shown
    assert "password manager" in text


def test_the_dry_run_writes_nothing(tmp_path, filesec):
    home = tmp_path / "home"
    rep = run(home, dry_run=True)
    assert not home.exists() and not filesec.exists()
    assert {s.status for s in rep.steps} == {"planned"}


def test_secrets_only_creates_just_the_secret_files(tmp_path, monkeypatch):
    d = tmp_path / "out"
    rep = run(tmp_path / "home", secrets_only=True, secrets_backend="file", secrets_dir=str(d))
    assert [s.name for s in rep.steps] == ["secrets"] and sorted(p.name for p in d.iterdir()) == ["backup_key", "db_key", "proposal_key"]
    assert not (tmp_path / "home").exists()
    assert "COACH_SECRETS_BACKEND" not in os.environ                           # the backend choice applied to this run only


def test_the_container_defaults_apply_to_a_new_configuration_only(tmp_path, filesec):
    run(tmp_path / "c", container=True)
    cfg = config_mod.load_config(tmp_path / "c" / "config.toml", env={})
    assert cfg.llm_backend == "anthropic-api" and cfg.coach_backend == "anthropic-api"
    assert cfg.ui_host == "0.0.0.0" and cfg.ui_open_browser is False and cfg.ui_allow_remote is False
    run(tmp_path / "n", container=False)
    plain = config_mod.load_config(tmp_path / "n" / "config.toml", env={})
    assert plain.llm_backend == "claude-code" and plain.ui_host == "127.0.0.1"


def test_a_missing_key_with_the_file_backend_fails_the_database_step_clearly(tmp_path, filesec):
    rep = run(tmp_path / "home", do_secrets=False)
    assert rep.get("database").status == "skipped" and "db_key" in rep.get("database").detail


def test_the_command_resolves_the_home_from_the_loaded_configuration(tmp_path, filesec, capsys):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "cfg.toml").write_text('data_dir = "data"\nmemory_dir = "memory"\n')
    cfg = config_mod.load_config(tmp_path / "x" / "cfg.toml", env={})
    a = Namespace(home=None, data_dir=None, memory_dir=None, secrets_backend=None, secrets_dir=None, non_interactive=True, no_secrets=False,
                  no_db=False, secrets_only=False, container=False, dry_run=False)
    I.cmd_init(a, cfg)
    out = capsys.readouterr().out
    assert "Next steps" in out and "coach setup enablebanking" in out
    assert (tmp_path / "x" / "data" / "finance.db").exists() and (tmp_path / "x" / "memory" / "household.yaml").exists()
    assert keyring.get_password("ai-finance-coach", "db_key") is None           # the fake keyring stayed empty: the file backend was used
