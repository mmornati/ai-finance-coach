"""E13-1: `coach setup enablebanking`: the guide for creating your own Enable Banking application. Validation (`coach check`, the only network
call) runs only after an explicit yes, and here it is a fake: no network, no real key (a throwaway RSA key is generated in tmp_path)."""
import os
import re
import stat

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from coach import config as config_mod
from coach.setup import eb_guide as G, init as I

APP = "00000000-1111-2222-3333-444444444444"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    I.run_init(tmp_path / "home", interactive=False, container=False, out=lambda *a: None)
    return tmp_path / "home"


@pytest.fixture
def pem(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "downloaded.pem"
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return p


def cfg_of(home):
    return config_mod.load_config(home / "config.toml", env={})


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_the_guide_names_every_step_and_the_whitelisted_redirect_url():
    t = G.guide_text("https://localhost:9443/callback")
    for needle in ("enablebanking.com", "Production", "https://localhost:9443/callback", "PEM private key", "linking accounts", "coach check", "restricted"):
        assert needle in t
    assert "one active consent" in t


def test_without_a_terminal_it_only_prints_and_changes_nothing(home):
    said = []
    before = (home / "config.toml").read_text()
    res = G.run_guide(cfg_of(home), isatty=lambda: False, input_fn=lambda p: pytest.fail("no prompt without a terminal"), out=said.append)
    assert res == {"configured": False, "checked": False, "changed": []}
    assert "No terminal" in said[-1] and (home / "config.toml").read_text() == before


def test_the_full_walk_stores_the_three_values_keeps_the_comments_and_copies_the_key_privately(home, pem):
    checked = []
    said = []
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=said.append, check_fn=lambda: checked.append(1),
                      input_fn=scripted(["y", "", APP, str(pem), "y"]))
    assert res["configured"] and res["checked"] and checked == [1]
    cfg = cfg_of(home)
    assert cfg.eb_app_id == APP and cfg.eb_redirect_url == G.DEFAULT_REDIRECT
    stored = home / "enablebanking" / "eb-private-key.pem"
    assert cfg.eb_private_key_path == str(stored) and stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored.parent.stat().st_mode) == 0o700 and pem.exists()      # the download is left for the user to delete
    text = (home / "config.toml").read_text()
    assert "# application id from the Enable Banking control panel" in text and "# must be whitelisted in your Enable Banking app" in text
    assert len(re.findall(r"^\[enable_banking\]", text, re.M)) == 1
    assert "PRIVATE KEY" not in "\n".join(map(str, said))


def test_the_check_runs_only_after_an_explicit_yes(home, pem):
    checked = []
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=lambda *a: None, check_fn=lambda: checked.append(1),
                      input_fn=scripted(["y", "", APP, str(pem), ""]))                  # Enter = no
    assert res["configured"] and not res["checked"] and checked == []
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=lambda *a: None, check_fn=lambda: checked.append(1),
                      input_fn=scripted(["y", "", APP, "", "n"]))
    assert checked == [] and res["configured"]                                         # the key already stored is kept


def test_a_failing_check_is_reported_not_raised(home, pem):
    said = []

    def boom():
        raise RuntimeError("HTTP 401")
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=said.append, check_fn=boom, input_fn=scripted(["y", "", APP, str(pem), "yes"]))
    assert res["configured"] and not res["checked"] and any("check failed" in str(s) for s in said)


@pytest.mark.parametrize("answers,why", [
    (["n"], "not done yet"),
    (["y", "http://localhost:8443/callback"], "http redirect"),
    (["y", "", ""], "no application id"),
])
def test_bad_or_missing_answers_change_nothing(home, answers, why):
    before = (home / "config.toml").read_text()
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=lambda *a: None, input_fn=scripted(answers))
    assert not res["configured"] and (home / "config.toml").read_text() == before, why


def test_a_file_that_is_not_a_pem_private_key_is_refused_and_nothing_is_stored(home, tmp_path):
    bad = tmp_path / "x.pem"
    bad.write_text("hello")
    said = []
    res = G.run_guide(cfg_of(home), isatty=lambda: True, out=said.append, input_fn=scripted(["y", "", APP, str(bad)]))
    assert not res["configured"] and any("Problem with the key" in str(s) for s in said)
    assert not (home / "enablebanking").exists() and cfg_of(home).eb_app_id is None
    assert G.pem_problem(tmp_path / "missing.pem")


def test_an_encrypted_key_is_refused_with_a_reason(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "enc.pem"
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"pw")))
    assert "unencrypted" in G.pem_problem(p)


def test_without_a_configuration_file_it_says_to_run_init(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_HOME", str(tmp_path / "empty"))
    cfg = config_mod.load_config(None)
    assert cfg.config_path is None
    said = []
    res = G.run_guide(cfg, isatty=lambda: True, out=said.append, input_fn=lambda p: pytest.fail("must not ask"))
    assert not res["configured"] and any("coach init" in str(s) for s in said)


def test_the_stored_key_works_for_signing_with_the_real_client(home, pem):
    G.run_guide(cfg_of(home), isatty=lambda: True, out=lambda *a: None, input_fn=scripted(["y", "", APP, str(pem), "n"]), check_fn=lambda: None)
    from coach.ingest.client import EnableBankingClient
    client = EnableBankingClient.from_config(cfg_of(home))
    tok = client.token()                                                               # signs locally; nothing is sent
    import jwt
    assert jwt.get_unverified_header(tok)["kid"] == APP and jwt.get_unverified_header(tok)["alg"] == "RS256"
    assert not os.path.exists(home / "enablebanking" / "eb-private-key.pem.tmp")
