"""Connecting a bank from the Docker image: the redirect server listens on 0.0.0.0 inside a real container only (marker AND `[callback] container_bind`), the
hints name the container commands, and `finish` tolerates a mangled paste. Fakes only: no bank, no network, no docker."""
import dataclasses
import os

import pytest
import requests

from coach import home as home_mod
from coach.db import connect
from coach.ingest import auth, callback as cb
from coach.ingest.client import ApiError
from helpers import FakeClient, session_payload
from test_connect import IBAN_A, eb_cfg, get, run_flow_in_thread  # noqa: F401  (fixtures + helpers of the existing flow tests)


def marker(value):
    return lambda *a, **k: value


# ---------------------------------------------------------------- the gate

def test_all_interfaces_need_the_marker_and_the_setting(eb_cfg, monkeypatch):
    on = dataclasses.replace(eb_cfg, callback_container_bind=True)
    assert cb.container_bind_allowed(eb_cfg, marker=lambda: True) is False                      # a container without the setting: loopback, as before
    assert cb.container_bind_allowed(eb_cfg, marker=lambda: False) is False                     # a host without the setting
    with pytest.raises(cb.CallbackError) as e:
        cb.container_bind_allowed(on, marker=lambda: False)                                      # the setting on a host: refused, not silently loopback
    assert "not running in a container" in str(e.value)
    assert cb.container_bind_allowed(on, marker=lambda: True) is True
    monkeypatch.setenv("COACH_IN_CONTAINER", "1")                                                # an environment variable alone proves nothing
    monkeypatch.setattr(home_mod, "on_container_filesystem", marker(False))
    with pytest.raises(cb.CallbackError):
        cb.container_bind_allowed(on)


def test_the_flow_refuses_the_setting_on_a_host(eb_cfg, monkeypatch):
    monkeypatch.setattr(home_mod, "on_container_filesystem", marker(False))
    cfg = dataclasses.replace(eb_cfg, callback_container_bind=True)
    con = connect(cfg, insecure=True, create=True)
    with pytest.raises(auth.ConnectError) as e:
        auth.connect_flow(con, FakeClient(), cfg, "Bank", "FR", 90, no_browser=True, out=lambda *a: None)
    assert "not running in a container" in str(e.value)
    assert con.execute("SELECT COUNT(*) FROM pending_auth").fetchone()[0] == 0                   # refused before any bank call


def test_in_a_container_the_server_listens_on_all_interfaces_warns_and_serves_one_valid_callback(eb_cfg, monkeypatch, capsys):
    monkeypatch.setattr(home_mod, "on_container_filesystem", marker(True))
    cfg = dataclasses.replace(eb_cfg, callback_container_bind=True)
    binds = []
    real_bind = cb.CallbackServer.bind

    def spy(self):
        port = real_bind(self)
        binds.append((self.bind_all, self.httpd.server_address[0]))
        return port
    monkeypatch.setattr(cb.CallbackServer, "bind", spy)
    connect(cfg, insecure=True, create=True).close()
    fc = FakeClient(codes={"SECRETCODE": session_payload("s-new", "Bank", "FR", [("uid-A1", IBAN_A, "CHK")])})
    t, box, ready = run_flow_in_thread(cfg, fc)
    assert ready.wait(10) and "exc" not in box
    assert binds == [(True, "0.0.0.0")]
    warning = [l for l in box["out"] if "WARNING" in l]
    assert warning and "127.0.0.1" in warning[0] and "ONLY" in warning[0]
    bad = get(cfg, box, "/callback", state="wrong", code="SECRETCODE")                             # state is validated as before
    assert bad.status_code == 400 and t.is_alive()
    ok = get(cfg, box, "/callback", state=box["state"], code="SECRETCODE")
    assert ok.status_code == 200 and "SECRETCODE" not in ok.text
    t.join(10)
    assert not t.is_alive() and box["code"] == 0                                                   # one valid request, then it stops
    with pytest.raises(requests.ConnectionError):
        get(cfg, box, "/callback", state=box["state"], code="SECRETCODE")
    seen = capsys.readouterr()
    assert "SECRETCODE" not in seen.out + seen.err + "\n".join(box["out"]) and box["state"] not in seen.out + seen.err        # no code, no state in any log
    assert not any(ln.startswith("uv run") for ln in box["out"])


def test_without_the_setting_a_container_still_binds_loopback(eb_cfg, monkeypatch):
    monkeypatch.setattr(home_mod, "on_container_filesystem", marker(True))
    binds = []
    real_bind = cb.CallbackServer.bind
    monkeypatch.setattr(cb.CallbackServer, "bind", lambda self: (lambda p: (binds.append(self.bind_all), p)[1])(real_bind(self)))
    connect(eb_cfg, insecure=True, create=True).close()
    fc = FakeClient(codes={"C": session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])})
    t, box, ready = run_flow_in_thread(eb_cfg, fc)
    assert ready.wait(10)
    get(eb_cfg, box, "/callback", state=box["state"], code="C")
    t.join(10)
    assert binds == [False] and not any("WARNING" in l for l in box["out"])


def test_the_image_init_writes_the_setting_and_the_example_defaults_to_false(tmp_path, monkeypatch):
    from coach import config as config_mod
    from coach.setup import init as I
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    I.run_init(tmp_path / "c", container=True, interactive=False, out=lambda *a: None)
    I.run_init(tmp_path / "n", container=False, interactive=False, out=lambda *a: None)
    assert config_mod.load_config(tmp_path / "c" / "config.toml", env={}).callback_container_bind is True
    assert config_mod.load_config(tmp_path / "n" / "config.toml", env={}).callback_container_bind is False
    root = os.path.dirname(os.path.dirname(__file__))
    assert "container_bind = false" in open(os.path.join(root, "config.example.toml")).read().split("[callback]")[1].split("[notify]")[0]


# ---------------------------------------------------------------- hints

def test_the_hints_name_the_container_commands_in_a_container_and_uv_elsewhere(eb_cfg, monkeypatch):
    connect(eb_cfg, insecure=True, create=True).close()
    for in_c, want, banned in ((True, "docker compose run --rm coach finish '<that url>'", "uv run"), (False, "uv run coach finish '<that url>'", "docker compose")):
        monkeypatch.setattr(home_mod, "in_container", lambda v=in_c: v)
        lines = []
        con = connect(eb_cfg, insecure=True)
        auth.connect_flow(con, FakeClient(), eb_cfg, "Bank", "FR", 90, no_server=True, no_browser=True, out=lines.append)
        text = "\n".join(lines)
        assert want in text and banned not in text and "single quotes" in text and "no backslashes" in text
        info = {"session_id": "s", "bank": "B", "valid_until": "x", "accounts": [], "retired": [], "orphans": []}
        done = []
        auth.print_completion(info, done.append)
        assert (("docker compose run --rm coach sync" in "\n".join(done)) if in_c else ("uv run coach sync" in "\n".join(done)))


def test_connect_does_not_try_to_open_a_browser_in_a_container(eb_cfg, monkeypatch):
    from argparse import Namespace
    from coach.ingest import commands as ic
    monkeypatch.setattr(home_mod, "in_container", lambda: True)
    assert ic._no_browser(Namespace(no_browser=False)) is True
    monkeypatch.setattr(home_mod, "in_container", lambda: False)
    assert ic._no_browser(Namespace(no_browser=False)) is False and ic._no_browser(Namespace(no_browser=True)) is True


# ---------------------------------------------------------------- a mangled paste

@pytest.fixture
def pending(eb_cfg):
    con = connect(eb_cfg, insecure=True, create=True)
    con.execute("INSERT INTO pending_auth(state, created_at) VALUES ('st-1', ?)", (auth.now_iso(),))
    con.commit()
    return con


@pytest.mark.parametrize("pasted", [
    "https://localhost:8443/callback?state=st-1&code=ABC123",
    "'https://localhost:8443/callback?state=st-1&code=ABC123'",
    '"https://localhost:8443/callback?state=st-1&code=ABC123"',
    "  https://localhost:8443/callback?state=st-1&code=ABC123\n",
    r"https://localhost:8443/callback\?state\=st-1\&code\=ABC123",
    "https://localhost:8443/callback?state=st-1&\ncode=ABC123",
    "‘https://localhost:8443/callback?state=st-1&code=ABC123’",
    r"'https://localhost:8443/callback\?state\=st-1\&code\=ABC123'",
])
def test_a_shell_escaped_quoted_or_wrapped_paste_is_understood(pending, pasted):
    assert auth.parse_finish_arg(pending, pasted) == ("ABC123", "st-1")


def test_a_bare_code_still_works_and_nonsense_is_refused_without_echo(pending):
    assert auth.parse_finish_arg(pending, " 'ABC123' ") == ("ABC123", None)
    with pytest.raises(auth.ConnectError) as e:
        auth.parse_finish_arg(pending, "localhost:8443/callback?state=st-1&code=SECRET9")
    assert "SECRET9" not in str(e.value) and "single quotes" in str(e.value)


def test_an_address_without_a_code_gets_a_clear_message_that_never_echoes_it(pending, monkeypatch):
    monkeypatch.setattr(home_mod, "in_container", lambda: True)
    with pytest.raises(auth.ConnectError) as e:
        auth.parse_finish_arg(pending, "https://localhost:8443/callback?state=st-1")
    msg = str(e.value)
    assert "no ?code=" in msg and "single quotes" in msg and "backslashes" in msg and "docker compose run --rm coach finish" in msg and "st-1" not in msg
    with pytest.raises(auth.ConnectError) as e:
        auth.parse_finish_arg(pending, "https://localhost:8443/callback?state=st-1&code=C&error=access_denied")
    assert "nothing was connected" in str(e.value) and "connect again" in str(e.value) and "st-1" not in str(e.value)


def test_a_rejected_or_expired_code_says_to_run_connect_again_without_the_bank_body(monkeypatch):
    monkeypatch.setattr(home_mod, "in_container", lambda: True)
    msg = auth._friendly(ApiError(422, '{"detail": "code CODE777 expired"}'))
    assert "run connect again" in msg.lower() or "Run connect again" in msg
    assert "CODE777" not in msg and "docker compose run --rm coach connect" in msg
    assert auth._friendly(ApiError(500, "boom")).startswith("HTTP 500")


def test_cmd_finish_turns_a_rejected_code_into_the_friendly_message(eb_cfg, pending, monkeypatch):
    from argparse import Namespace
    from coach.ingest import commands as ic

    class Rejecting:
        @classmethod
        def from_config(cls, cfg):
            return cls()

        def call(self, *a, **k):
            raise ApiError(400, "body CODE777")
    monkeypatch.setattr(ic, "EnableBankingClient", Rejecting)
    with pytest.raises(SystemExit) as e:
        ic.cmd_finish(Namespace(insecure=True, replay=None, code_or_url=r"'https://localhost:8443/callback\?state\=st-1\&code\=CODE777'"), eb_cfg)
    assert "CODE777" not in str(e.value) and "connect again" in str(e.value).lower()


# ---------------------------------------------------------------- the wizard in a container

def test_the_wizard_bank_step_in_a_container_offers_the_callback_flow_and_copy_paste_as_fallback(eb_cfg, tmp_path, monkeypatch):
    from coach.setup import wizard as W
    monkeypatch.setattr(W, "in_container", lambda: True)
    calls = []
    actions = W.Actions(connect=lambda cfg, bank, country, insecure=False, no_server=False: calls.append((bank, country, no_server)))

    def ctx_with(answers):
        it = iter(answers)
        said = []
        ctx = W.Ctx(cfg=eb_cfg, state=W.State(tmp_path / "s.json"), isatty=lambda: True, input_fn=lambda p: next(it), out=said.append, actions=actions)
        ctx.said = said
        return ctx
    ctx = ctx_with(["fr", "Bank A", "1"])
    W.step_connect(ctx)
    text = "\n".join(ctx.said)
    assert calls == [] and "-p 127.0.0.1:8443:8443" in text and "Bank A" in text and "coach connect" in text        # the command for the host, nothing run here
    ctx = ctx_with(["fr", "Bank A", "2", "connect"])
    W.step_connect(ctx)
    assert calls == [("Bank A", "FR", True)]                                                          # copy-paste fallback
    assert W.CONTAINER_COMMANDS["connect"].startswith("docker compose run --rm -p 127.0.0.1:8443:8443 coach connect")


def test_no_cli_hint_is_html_escaped(eb_cfg, monkeypatch):
    """The hints are terminal text: `<that url>` must never come out as `&lt;that url&gt;` (or any other entity)."""
    import re
    for in_c in (True, False):
        monkeypatch.setattr(home_mod, "in_container", lambda v=in_c: v)
        lines = []
        con = connect(eb_cfg, insecure=True, create=True)
        auth.connect_flow(con, FakeClient(), eb_cfg, "Bank", "FR", 90, no_server=True, no_browser=True, out=lines.append)
        done = []
        auth.print_completion({"session_id": "s", "bank": "B", "valid_until": "x", "accounts": [], "retired": [], "orphans": []}, done.append)
        text = "\n".join(lines + done + [auth.finish_hint(), auth._friendly(ApiError(400, "x"))])
        assert "<that url>" in text and not re.search(r"&(lt|gt|amp|quot|#\d+);", text)
        for msg in ("no ?code=",):
            try:
                auth.parse_finish_arg(con, "https://localhost:8443/callback")
            except auth.ConnectError as e:
                assert "<address>" in str(e) and not re.search(r"&(lt|gt|amp|quot|#\d+);", str(e))
