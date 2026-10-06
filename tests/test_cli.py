import pytest

from coach import db as dbm
from coach.cli import build_parser, main
from helpers import make_prototype_db

TOP = ["check", "banks", "connect", "reconnect", "finish", "sessions", "consents", "accounts", "health", "import",
       "transfers", "sync", "stats", "normalize", "classify", "db", "schedule", "backup", "restore", "config"]


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for c in TOP:
        assert f" {c} " in out or f"  {c}" in out
    for sub in ["run|enrich|review|correct|compare|report", "migrate|status|encrypt|import-prototype",
                "install|uninstall|status|run", "show|import-env"]:
        assert sub in out


@pytest.mark.parametrize("argv", [
    ["classify", "run"], ["classify", "enrich"], ["classify", "review"], ["classify", "correct", "k", "a.b"],
    ["classify", "compare"], ["classify", "report"], ["db", "migrate"], ["db", "status"], ["db", "encrypt"],
    ["db", "import-prototype"], ["schedule", "install"], ["schedule", "uninstall"], ["schedule", "status"],
    ["schedule", "run"], ["config", "show"], ["config", "import-env"], ["backup"], ["restore", "f", "--to", "d"],
    ["normalize"], ["sync"], ["stats"], ["finish", "x"], ["connect", "--bank", "B"], ["banks"], ["check"],
    ["sessions"], ["consents"], ["consents", "--refresh", "--json"], ["reconnect", "Fortuneo", "--no-server"],
    ["connect", "--bank", "B", "--no-server", "--replace", "--timeout", "5"], ["accounts"],
    ["accounts", "set", "x", "--label", "L", "--owner", "joint", "--purpose", "main", "--exclude"],
    ["health"], ["health", "--json"], ["import", "f.csv", "--account", "new:X", "--dry-run", "--profile", "p"],
    ["import", "--list-profiles"], ["transfers"], ["transfers", "--unmatched"], ["transfers", "match", "--dry-run"],
    ["transfers", "link", "a", "b"], ["transfers", "unlink", "1"],
])
def test_every_documented_command_parses(argv):
    a = build_parser().parse_args(argv)
    assert callable(a.fn)


def test_insecure_flag_accepted_before_and_after_subcommand():
    p = build_parser()
    assert p.parse_args(["--insecure", "stats"]).insecure is True
    assert p.parse_args(["stats", "--insecure"]).insecure is True
    assert p.parse_args(["stats"]).insecure is False


def test_cli_refuses_plaintext_then_works_with_insecure(cfg, capsys):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    with pytest.raises(SystemExit) as e:
        main(["--config", str(cfg.config_path), "normalize"])
    assert "Refusing to open plaintext database" in str(e.value)
    main(["--config", str(cfg.config_path), "--insecure", "normalize"])
    assert "normalised 7 transactions" in capsys.readouterr().out


def test_report_runs_on_synthetic_data_and_is_data_driven(cfg, capsys):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: a\n    match: {merchant_key: '^RELAY'}\n    category: food.work_meals\n"
        "    tags: [one_off]\n")
    base = ["--config", str(cfg.config_path), "--insecure"]
    main(base + ["normalize"])
    capsys.readouterr()
    main(base + ["classify", "report"])
    out = capsys.readouterr().out
    assert "coverage by source:" in out and "'memory': 1" in out
    assert "uncategorized:" in out and "recurring hints" in out


def test_analytics_returns_data(cfg):
    from coach.analytics.report import build_report
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    from coach.classify import commands as cc
    from argparse import Namespace
    cc.cmd_normalize(Namespace(insecure=True), cfg)
    r = build_report(dbm.connect(cfg, insecure=True), memory_dir=cfg.memory_dir)
    assert r["coverage"]["total"] == 7 and isinstance(r["coverage"]["by_source"], dict)
    assert r["averages"]["avg_monthly"] is None                # only one month of data: no full months


def test_db_cli_roundtrip_import_status_encrypt(cfg, tmp_path, monkeypatch, capsys):
    src = make_prototype_db(tmp_path / "proto.db")
    base = ["--config", str(cfg.config_path)]
    main(base + ["db", "import-prototype", "--source", str(src)])
    main(base + ["--insecure", "db", "status"])
    out = capsys.readouterr().out
    assert "NONE (plaintext)" in out and "0002_indexes" in out and "transactions" in out
    monkeypatch.setenv("COACH_DB_KEY", "k")
    main(base + ["db", "encrypt"])
    out = capsys.readouterr().out
    assert "WARNING" in out and "plaintext.bak" in out
    main(base + ["db", "status"])                              # no --insecure needed any more
    assert "SQLCipher" in capsys.readouterr().out
