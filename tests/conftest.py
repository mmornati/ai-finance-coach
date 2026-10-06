import os
import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

sys.path.insert(0, str(Path(__file__).parent))

from coach.config import load_config  # noqa: E402

# The shell's COACH_* / EB_* / ANTHROPIC_API_KEY must never reach a test, not even through monkeypatch.undo(), which
# restores what it saved: they are removed for the whole session here, before any fixture can record them.
for _var in list(os.environ):
    if _var.startswith(("COACH_", "EB_")) or _var == "ANTHROPIC_API_KEY":
        del os.environ[_var]


class MemoryKeyring(KeyringBackend):
    """In-memory keyring: tests never touch the real macOS Keychain."""
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError
        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Fake keyring + a clean environment for every test."""
    kr = MemoryKeyring()
    old = keyring.get_keyring()
    keyring.set_keyring(kr)
    for var in list(os.environ):
        if var.startswith(("COACH_", "EB_")) or var == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(var, raising=False)
    yield kr
    keyring.set_keyring(old)


@pytest.fixture
def fake_keyring(isolated_env):
    return isolated_env


@pytest.fixture
def cfg(tmp_path):
    """A Config rooted in tmp_path (config.toml written there)."""
    (tmp_path / "memory").mkdir()
    p = tmp_path / "config.toml"
    p.write_text('data_dir = "data"\nmemory_dir = "memory"\n[backup]\ndir = "backups"\nretention = 3\n'
                 '[schedule]\ntime = "06:45"\n')
    return load_config(p, env={})


@pytest.fixture
def db_key(monkeypatch):
    monkeypatch.setenv("COACH_DB_KEY", "test-db-key")
    return "test-db-key"


@pytest.fixture(autouse=True)
def no_osascript(monkeypatch):
    """The macOS notification must never run in tests: the default runner of coach.notify fails loudly."""
    def boom(*a, **k):
        raise AssertionError("osascript/subprocess must not run in tests; inject a runner")
    monkeypatch.setattr("coach.notify._default_run", boom)


# ---------------------------------------------------------------- the packaged files are never written

import hashlib  # noqa: E402

PACKAGED = [Path(__file__).resolve().parents[1] / "src" / "coach" / "classify" / n
            for n in ("taxonomy.yaml", "rules.yaml")]


def _hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PACKAGED}


@pytest.fixture(scope="session", autouse=True)
def packaged_files_untouched():
    """taxonomy.yaml / rules.yaml inside src/ are defaults shipped with the package: no test (and no command a test
    runs) may write them. Checked at the end of the whole session."""
    before = _hashes()
    yield
    assert _hashes() == before, "a test modified the packaged taxonomy.yaml / rules.yaml"


@pytest.fixture(autouse=True)
def user_config_in_tmp(isolated_env, monkeypatch, tmp_path):
    """Every test starts with an EMPTY user config dir under tmp_path, so edits can never reach src/ or the project's
    real config/ directory. Tests that need a particular dir override the variable themselves."""
    from coach.classify import rules
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "_user_config"))
    rules.reload_taxonomy()
    yield
    rules.reload_taxonomy()


@pytest.fixture(autouse=True)
def no_alert_network(monkeypatch):
    """E10: no alert channel may reach the network in tests. The real transports of coach.alerts.channels fail loudly; tests that
    exercise a channel inject a coach.alerts.channels.Transports of fakes (or patch urllib / smtplib themselves)."""
    def boom(*a, **k):
        raise AssertionError("the network (HTTP / SMTP) must not be used in tests; inject Transports")
    monkeypatch.setattr("coach.alerts.channels._default_http_post", boom)
    monkeypatch.setattr("coach.alerts.channels._default_smtp", boom)
    monkeypatch.setattr("coach.alerts.channels._default_run", boom)


@pytest.fixture(autouse=True)
def egress_policy_reset():
    """E11-1: the egress policy is process-wide state (the CLI activates it once). Every test starts and ends with none, and no pending journal row."""
    from coach import egress
    egress.deactivate()
    egress._pending.clear()
    egress._ACTIVE = egress.Policy()          # the permissive default policy of a test; policy tests activate their own (or deactivate this)
    yield
    egress.deactivate()
    egress._pending.clear()
