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



# CI splits the suite over N jobs (`--shard K/N`, round robin over the collected tests: their times are flat) and runs each
# with pytest-xdist (`-n auto`). The endpoint-coverage guard of test_api_zz_coverage.py needs the API calls of EVERY test:
# each xdist worker hands its calls to its controller; a shard writes the union to `--api-calls-out` and CI checks the union
# of the shards (`tests/check_api_coverage.py`); a plain `-n auto` run checks the union of its workers here.
def pytest_addoption(parser):
    parser.addoption("--shard", default=None, metavar="K/N", help="run only the K-th of N round-robin slices of the tests (CI)")
    parser.addoption("--api-calls-out", default=None, metavar="FILE",
                     help="write the API calls (and the schema, when the coverage guard ran) to FILE: tests/check_api_coverage.py checks them")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    shard = config.getoption("--shard")
    if not shard:
        return
    k, n = (int(x) for x in shard.split("/"))
    if not 1 <= k <= n:
        raise pytest.UsageError(f"--shard {shard}: K must be between 1 and N")
    keep = [item for i, item in enumerate(items) if i % n == k - 1]
    config.hook.pytest_deselected(items=[item for i, item in enumerate(items) if i % n != k - 1])
    items[:] = keep


def pytest_sessionfinish(session):
    import json

    from apihelpers import CALLS, SPEC, missing_endpoints
    config = session.config
    workeroutput = getattr(config, "workeroutput", None)
    if workeroutput is not None:                                   # an xdist worker: hand everything to the controller
        workeroutput["api_calls"] = sorted(CALLS)
        workeroutput["api_spec"] = SPEC
        return
    out = config.getoption("--api-calls-out")
    if out:                                                        # a CI shard: the union of the shards is checked later
        Path(out).write_text(json.dumps({"shard": config.getoption("--shard"), "calls": sorted(CALLS), "spec": SPEC}))
    elif SPEC:                                                     # an xdist controller, and a worker ran the guard
        missing = missing_endpoints(SPEC, CALLS)
        if missing:
            config.get_terminal_writer().line(
                f"FAILED test_api_zz_coverage (union of the xdist workers): no successful call to {missing}", red=True)
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    from apihelpers import CALLS, SPEC
    output = getattr(node, "workeroutput", {})
    CALLS.update(tuple(c) for c in output.get("api_calls", ()))
    SPEC.update(output.get("api_spec", {}))
