import tomllib

import keyring
import pytest

from coach import config as cm
from coach import secrets as sec
from coach.cli import main


def test_secret_lookup_order_env_then_keychain_then_error(fake_keyring, monkeypatch):
    with pytest.raises(sec.SecretNotFound) as ei:
        sec.get_secret("db_key")
    assert "COACH_DB_KEY" in str(ei.value) and "ai-finance-coach" in str(ei.value)
    keyring.set_password(sec.SERVICE, "db_key", "from-keychain")
    assert sec.get_secret("db_key") == "from-keychain"
    assert sec.lookup("db_key") == ("from-keychain", "keychain")
    monkeypatch.setenv("COACH_DB_KEY", "from-env")
    assert sec.lookup("db_key") == ("from-env", "env")           # env wins
    assert sec.get_secret("db_key") == "from-env"


def test_optional_secret_and_unknown_name(fake_keyring):
    assert sec.get_secret("anthropic_api_key", required=False) is None
    with pytest.raises(KeyError):
        sec.get_secret("nope")


def test_set_secret_writes_to_keyring_service(fake_keyring):
    sec.set_secret("backup_key", "abc")
    assert fake_keyring.store[("ai-finance-coach", "backup_key")] == "abc"
    assert sec.generate() != sec.generate()


def test_describe_never_leaks_values(fake_keyring, monkeypatch):
    monkeypatch.setenv("COACH_DB_KEY", "SUPERSECRETVALUE")
    keyring.set_password(sec.SERVICE, "backup_key", "OTHERSECRET")
    text = " ".join(f"{n} {s}" for n, s in sec.describe())
    assert "SUPERSECRETVALUE" not in text and "OTHERSECRET" not in text
    assert "set (env)" in text and "set (keychain)" in text and "not set (optional)" in text


def test_config_defaults_relative_to_root(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[sync]\ndaily_limit = 2\n')
    c = cm.load_config(p, env={})
    assert c.data_dir == tmp_path / "data" and c.db_path == tmp_path / "data" / "finance.db"
    assert c.memory_dir == tmp_path / "memory" and c.sync_daily_limit == 2
    assert c.llm_model == "sonnet" and c.schedule_time == "07:30" and c.backup_retention == 14
    assert c.log_dir == tmp_path / "data" / "logs"


def test_config_validation(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[schedule]\ntime = "25:99"\n')
    with pytest.raises(cm.ConfigError, match="HH:MM"):
        cm.load_config(p, env={})
    with pytest.raises(cm.ConfigError, match="not found"):
        cm.load_config(tmp_path / "missing.toml", env={})


def test_enable_banking_precedence_env_over_toml_over_legacy_env(tmp_path):
    legacy = tmp_path / "prototype" / "ingest"
    legacy.mkdir(parents=True)
    (legacy / ".env").write_text("EB_APP_ID=legacy-app\nEB_PRIVATE_KEY_PATH=/legacy/key.pem\n"
                                 "EB_REDIRECT_URL=https://legacy/cb\n")
    p = tmp_path / "config.toml"
    p.write_text('[enable_banking]\napp_id = "toml-app"\n')
    c = cm.load_config(p, env={})
    assert c.eb_app_id == "toml-app" and c.sources["enable_banking.app_id"] == "config.toml"
    assert c.eb_private_key_path == "/legacy/key.pem" and "legacy" in c.sources["enable_banking.private_key_path"]
    c = cm.load_config(p, env={"EB_APP_ID": "env-app"})
    assert c.eb_app_id == "env-app"


def test_import_env_migrates_values_preserving_comments(tmp_path):
    (tmp_path / "prototype" / "ingest").mkdir(parents=True)
    (tmp_path / "prototype" / "ingest" / ".env").write_text(
        "EB_APP_ID=app-1\nEB_PRIVATE_KEY_PATH=~/keys/eb.pem\nEB_REDIRECT_URL=https://localhost:8443/callback\n")
    example = (cm.Path(cm.__file__).parents[2] / "config.example.toml").read_text()
    (tmp_path / "config.example.toml").write_text(example)
    (tmp_path / "config.toml").write_text('data_dir = "data"\n')
    c = cm.load_config(tmp_path / "config.toml", env={})
    dest, changed = cm.import_env(c)
    assert sorted(changed) == ["app_id", "private_key_path", "redirect_url"]
    data = tomllib.loads(dest.read_text())
    assert data["enable_banking"] == {"app_id": "app-1", "private_key_path": "~/keys/eb.pem",
                                      "redirect_url": "https://localhost:8443/callback"}
    # idempotent: second run changes nothing unless forced
    assert cm.import_env(cm.load_config(dest, env={}))[1] == []


def test_import_env_from_example_template(tmp_path):
    (tmp_path / "prototype" / "ingest").mkdir(parents=True)
    (tmp_path / "prototype" / "ingest" / ".env").write_text("EB_APP_ID=app-2\n")
    example = (cm.Path(cm.__file__).parents[2] / "config.example.toml").read_text()
    (tmp_path / "config.example.toml").write_text(example)
    c = cm.Config(
        root=tmp_path, config_path=None, data_dir=tmp_path / "data", memory_dir=tmp_path / "memory",
        db_path=tmp_path / "data" / "f.db")
    dest, changed = cm.import_env(c)
    assert dest == tmp_path / "config.toml" and changed == ["app_id"]
    text = dest.read_text()
    assert "# Secrets (db_key" in text                                  # comments from the template kept
    assert tomllib.loads(text)["enable_banking"]["app_id"] == "app-2"


def test_set_toml_value_adds_missing_section_and_key():
    t = cm.set_toml_value('a = 1\n', "enable_banking", "app_id", 'x"y')
    assert tomllib.loads(t) == {"a": 1, "enable_banking": {"app_id": 'x"y'}}
    t = cm.set_toml_value(t, "enable_banking", "redirect_url", "u")
    t = cm.set_toml_value(t, "enable_banking", "app_id", "z")
    assert tomllib.loads(t)["enable_banking"] == {"app_id": "z", "redirect_url": "u"}


def test_config_show_masks_secrets(cfg, monkeypatch, capsys):
    monkeypatch.setenv("COACH_DB_KEY", "TOPSECRET123")
    monkeypatch.setenv("EB_APP_ID", "app-visible")
    main(["--config", str(cfg.config_path), "config", "show"])
    out = capsys.readouterr().out
    assert "TOPSECRET123" not in out
    assert "app-visible" in out and "db_key" in out and "set (env)" in out
    assert "backup.retention" in out and "schedule.time" in out


def test_cli_set_secret_generate_uses_fake_keychain(cfg, fake_keyring, capsys):
    main(["--config", str(cfg.config_path), "config", "set-secret", "db_key", "--generate"])
    assert ("ai-finance-coach", "db_key") in fake_keyring.store
    assert fake_keyring.store[("ai-finance-coach", "db_key")] not in capsys.readouterr().out
