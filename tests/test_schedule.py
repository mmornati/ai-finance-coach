import json
import plistlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from coach import schedule as sch
from coach.cli import main
from coach.db import connect
from helpers import FakeClient, eb_tx


class Runner:
    def __init__(self, rc=0):
        self.cmds, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        return SimpleNamespace(returncode=self.rc, stdout="", stderr="boom")


def test_plist_content(cfg):
    p = sch.build_plist(cfg, python="/venv/bin/python")
    assert p["Label"] == "com.ai-finance-coach.daily"
    assert p["ProgramArguments"] == ["/venv/bin/python", "-m", "coach", "--config", str(cfg.config_path),
                                     "schedule", "run"]
    assert p["StartCalendarInterval"] == {"Hour": 6, "Minute": 45}      # from config schedule.time
    assert p["StandardOutPath"] == str(cfg.data_dir / "logs" / "schedule.out.log")
    assert p["StandardErrorPath"] == str(cfg.data_dir / "logs" / "schedule.err.log")
    assert p["WorkingDirectory"] == str(cfg.root)
    assert "/usr/bin" in p["EnvironmentVariables"]["PATH"]


def test_install_dry_run_prints_and_writes_nothing(cfg, tmp_path):
    agents = tmp_path / "LaunchAgents"
    runner, lines = Runner(), []
    path = sch.install(cfg, agents, dry_run=True, runner=runner, out=lines.append)
    text = "\n".join(lines)
    assert path == agents / "com.ai-finance-coach.daily.plist"
    assert "<key>StartCalendarInterval</key>" in text and "<integer>45</integer>" in text
    assert "schedule" in text and "launchctl bootstrap" in text and "launchctl bootout" in text
    assert not agents.exists() and runner.cmds == [] and not cfg.log_dir.exists()


def test_install_uninstall_status_with_overridden_agents_dir(cfg, tmp_path):
    agents = tmp_path / "LA"
    runner, lines = Runner(), []
    path = sch.install(cfg, agents, runner=runner, out=lines.append, do_preflight=False)
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == sch.LABEL and cfg.log_dir.is_dir()
    assert runner.cmds[0][:2] == ["launchctl", "bootout"] and runner.cmds[1][:2] == ["launchctl", "bootstrap"]
    assert runner.cmds[1][-1] == str(path)
    info = sch.status(cfg, agents, runner=Runner(), out=lines.append)
    assert info["installed"] and info["time"] == "06:45" and info["loaded"] is True
    sch.uninstall(agents, runner=runner, out=lines.append)
    assert not path.exists()
    assert not sch.status(cfg, agents, runner=Runner(), out=lines.append)["installed"]


def test_install_env_override_and_no_load(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_LAUNCHAGENTS_DIR", str(tmp_path / "envdir"))
    runner = Runner()
    path = sch.install(cfg, load=False, runner=runner, out=lambda *_: None, do_preflight=False)
    assert path.parent == tmp_path / "envdir" and path.exists() and runner.cmds == []


def test_install_reports_launchctl_failure(cfg, tmp_path):
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        sch.install(cfg, tmp_path / "LA", runner=Runner(rc=5), out=lambda *_: None, do_preflight=False)


def test_cli_dry_run_does_not_touch_launchagents(cfg, tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    main(["--config", str(cfg.config_path), "schedule", "install", "--dry-run", "--agents-dir", str(tmp_path / "x")])
    out = capsys.readouterr().out
    assert "com.ai-finance-coach.daily" in out and "# would run: launchctl bootstrap" in out
    assert called == [] and not (tmp_path / "x").exists()


# ---- the job: sync -> normalize -> classify run, with HTTP and LLM mocked

def fake_claude(captured):
    def run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured.append((cmd, input))
        items = json.loads(input.strip().splitlines()[-1])
        results = [{"id": it["id"], "merchant": it["key"].title(), "category": "food.groceries",
                    "confidence": 0.9, "recurring_hint": False} for it in items]
        out = json.dumps({"structured_output": {"results": results}, "total_cost_usd": 0.01})
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    return run


def test_run_daily_syncs_normalizes_classifies_and_logs(cfg, monkeypatch, db_key):
    con = connect(cfg, create=True)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Test','FR','2099-01-01','t','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','MR ALICE TESTOWNER','FR76','EUR','CACC','{}')")
    con.commit()
    con.close()
    client = FakeClient({"acc1": [{"transactions": [
        eb_tx("r1", "2025-10-03", -4.5, "CARTE 02/10 RELAY BEAUVAIS"),
        eb_tx("r2", "2025-10-04", -9.0, "CARTE 03/10 BOULANGERIE PAUL BEAUVAIS"),
        eb_tx("r3", "2025-10-05", -60.0, "RET DAB 12345 BANQUE TEST"),
    ]}]})
    captured = []
    monkeypatch.setattr("coach.classify.llm.subprocess.run", fake_claude(captured))
    out = []
    code = sch.run_daily(cfg, client=client, out=out.append)
    assert code == 0
    cmd, prompt = captured[0]
    assert cmd[:2] == ["claude", "-p"] and "--tools" in cmd and "--json-schema" in cmd
    assert "RET DAB" not in prompt and "BANQUE TEST" not in prompt     # ATM lines never reach the LLM
    con = connect(cfg)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM merchants WHERE source='llm'").fetchone()[0] == 2
    log = (cfg.log_dir / "schedule.log").read_text().strip().splitlines()
    assert len(log) == 1 and "schedule run ok" in log[0] and "sync: accounts=1 new=3" in log[0]
    assert "labelled=2" in log[0]
    # E9-4: the job stores a net-worth snapshot (warn only) and the sync itself did too
    assert "networth: net_worth=" in log[0] and "backfilled=" in log[0]
    snaps = con.execute("SELECT source, COUNT(*) FROM net_worth_history WHERE source='snapshot' GROUP BY source").fetchall()
    assert snaps == [("snapshot", 1)]                       # one row per day: the sync's snapshot and the job's are the same day
    # second run: nothing new to label, daily sync counter respected
    captured.clear()
    assert sch.run_daily(cfg, client=client, out=lambda *_: None) in (0, 1)
    assert captured == []                                              # only NEW merchants are sent


def test_run_daily_continues_after_sync_failure(cfg, monkeypatch, db_key):
    connect(cfg, create=True).close()
    monkeypatch.setattr("coach.classify.llm.subprocess.run", fake_claude([]))
    # Enable Banking not configured -> sync step errors, others still run, exit code 1, logged
    code = sch.run_daily(cfg, out=lambda *_: None)
    assert code == 1
    line = (cfg.log_dir / "schedule.log").read_text()
    assert "FAILED" in line and "sync: ERROR" in line and "normalize:" in line and "classify:" in line
