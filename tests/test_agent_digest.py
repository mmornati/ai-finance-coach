"""E6-6 / E6-4: scheduled digests (opt-in, skip without new data, dry run), `coach coach ...` commands, config validation, the
scheduler step. No model is ever called: run_agent is replaced by a fake."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from coach import schedule as sch
from coach.agent import digest as dg, insights as I, prompt as P
from coach.agent.runner import CoachUnavailable, RunResult
from coach.classify.backends import Usage
from coach.cli import main
from coach.config import ConfigError, load_config
from coach.db import connect
from helpers import FakeClient, add_tx
from mcphelpers import BANNED, TODAY, build_world, world  # noqa: F401

D = dt.date


def fake_run(text="Quiet week: spending in line (see h_0123456789).", finish="stop", calls=None):
    def run(cfg, spec, question=None, *, insecure=False, **kw):
        if calls is not None:
            calls.append(spec.id)
        return RunResult(text=text, finish_reason=finish, usage=Usage("claude-code", "sonnet", f"coach:{spec.id}", 3, 900, 100, 0, 0, 0.02, True, 4.0),
                         tool_calls=[{"name": "coverage"}] * 3, session_id="j_x", backend="claude-code", model="sonnet",
                         refs=[], unverified_numbers=[])
    return run


def cli(cfg, *argv):
    main(["--insecure", "--config", str(cfg.config_path), *argv])


# ---------------------------------------------------------------- the schedule of digests

def test_a_weekly_digest_is_stored_with_its_provenance_and_usage(cfg, world, monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "run_agent", fake_run(calls=calls))
    r = dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 4))
    assert r["status"] == "done" and calls == ["digest-weekly"] and r["insight_id"].startswith("cin_")
    con = connect(cfg, insecure=True)
    row = I.get(con, r["insight_id"])
    assert row["kind"] == "digest" and row["skill"] == "digest-weekly" and row["backend"] == "claude-code" and row["model"] == "sonnet"
    assert row["title"] == "Weekly digest 2026-10-04" and row["status"] == "new" and row["data_through"]
    u = con.execute("SELECT purpose, tokens_in, cost_usd FROM llm_usage").fetchall()
    assert u == [("coach:digest-weekly", 900, 0.02)] and row["usage_ref"] == 1


def test_skipped_when_nothing_is_new_or_too_soon_and_run_again_after_new_data(cfg, world, monkeypatch):
    calls = []
    monkeypatch.setattr(I, "_now", lambda: "2026-10-04T09:00:00+00:00")        # the insight's stamp follows the pinned dates, not the wall clock
    monkeypatch.setattr(dg, "run_agent", fake_run(calls=calls))
    assert dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 4))["status"] == "done"
    r = dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 5))
    assert r["status"] == "skipped" and "less than 7 days" in r["reason"]
    r = dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 12))
    assert r["status"] == "skipped" and "no new data" in r["reason"] and calls == ["digest-weekly"]
    add_tx(world, "fo", "newtx", "2026-10-09", -12.0, "ACME GROCERS", "card")
    world.commit()
    assert dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 12))["status"] == "done"
    assert dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 20), force=True)["status"] == "done"
    assert calls == ["digest-weekly"] * 3


def test_the_monthly_review_runs_once_per_month_when_there_is_new_data(cfg, world, monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "run_agent", fake_run(calls=calls))
    assert dg.run_digest(cfg, "monthly", insecure=True, today=D(2026, 10, 4))["status"] == "done"
    add_tx(world, "fo", "newtx", "2026-10-09", -12.0, "ACME GROCERS", "card")
    world.commit()
    assert dg.run_digest(cfg, "monthly", insecure=True, today=D(2026, 10, 20))["status"] == "skipped"          # same month
    assert dg.run_digest(cfg, "monthly", insecure=True, today=D(2026, 11, 2))["status"] == "done"
    assert calls == ["digest-monthly"] * 2
    assert P.MONTHLY.tool_factor > P.WEEKLY.tool_factor >= 2


def test_a_failed_run_is_reported_and_stores_no_digest(cfg, world, monkeypatch):
    monkeypatch.setattr(dg, "run_agent", fake_run(text="", finish="error"))
    r = dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 4))
    assert r["status"] == "failed"
    monkeypatch.setattr(dg, "run_agent", lambda *a, **k: (_ for _ in ()).throw(CoachUnavailable("no claude")))
    assert dg.run_digest(cfg, "weekly", insecure=True, today=D(2026, 10, 4)) == {"status": "failed", "reason": "no claude"}
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 0


# ---------------------------------------------------------------- dry run

def test_dry_run_prints_the_exact_prompt_and_redacted_tool_outputs_and_calls_no_model(cfg, world, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("a dry run must not call any model")
    monkeypatch.setattr(dg, "run_agent", boom)
    monkeypatch.setattr("coach.agent.runner.shutil.which", boom)
    cli(cfg, "coach", "digest", "--weekly", "--dry-run")
    out = capsys.readouterr().out
    assert P.WEEKLY.user in out and P.SYSTEM_PROMPT.split("\n")[0] in out
    for t in ("coverage", "cashflow", "anomalies", "price_changes", "budget_status", "forecast", "calendar"):
        assert f"## {t} " in out
    assert "add_insight  [WRITES: insight]" in out and "- memory_propose" not in out           # digests never propose memory changes
    assert "account-main-1" in out and "dry run: nothing was sent to any model" in out
    low = out.lower()
    for w in BANNED:
        assert w not in low, w
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


def test_dry_run_summary_numbers(cfg, world):
    lines = []
    r = dg.run_digest(cfg, "monthly", insecure=True, dry=True, out=lines.append)
    assert r["status"] == "dry-run" and len(r["outputs"]) == 9 and all(o["ok"] for o in r["outputs"])
    assert r["prompt_chars"] == r["system_chars"] + r["user_chars"] and len(r["tools"]) == 17


def test_cli_digest_needs_exactly_one_kind_and_reports_skips(cfg, world, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        cli(cfg, "coach", "digest")
    with pytest.raises(SystemExit):
        cli(cfg, "coach", "digest", "--weekly", "--monthly")
    monkeypatch.setattr(dg, "run_agent", fake_run())
    cli(cfg, "coach", "digest", "--weekly")
    assert "weekly digest stored as cin_" in capsys.readouterr().out
    cli(cfg, "coach", "digest", "--weekly")
    assert "skipped:" in capsys.readouterr().out


def test_cli_tools_lists_and_sizes_every_tool(cfg, world, capsys):
    cli(cfg, "coach", "tools", "--sizes")
    out = capsys.readouterr().out
    for t in ("coverage", "transactions_search", "explain_transaction", "memory_context"):
        assert t in out
    assert "WRITES proposal" in out and "WRITES insight" in out and "REFUSED" not in out and "session suspicious: False" in out


def test_cli_ask_runs_the_coach_and_stores_the_answer(cfg, world, monkeypatch, capsys):
    import coach.agent.commands as cmds
    monkeypatch.setattr(cmds, "run_agent", fake_run(text="Because of groceries."))
    cli(cfg, "coach", "ask", "Why was September high?")
    cap = capsys.readouterr()
    assert "Because of groceries." in cap.out and "claude-code sonnet" in cap.err and "stored as cin_" in cap.err
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT kind, question FROM insights").fetchall() == [("answer", "Why was September high?")]


# ---------------------------------------------------------------- config [coach]

def test_coach_config_defaults_validation_and_show(cfg, tmp_path, capsys):
    assert (cfg.coach_backend, cfg.coach_model_effective, cfg.coach_max_tool_calls, cfg.coach_schedule_weekly, cfg.coach_schedule_monthly) == \
        ("claude-code", "sonnet", 12, False, False)

    def load(body):
        p = tmp_path / "c.toml"
        p.write_text('data_dir = "data"\nmemory_dir = "memory"\n[coach]\n' + body)
        return load_config(p, env={})
    c = load('backend = "anthropic-api"\nmax_tool_calls = 5\ntimeout_seconds = 60\nschedule_weekly = true\n')
    assert c.coach_backend == "anthropic-api" and c.coach_model_effective == "claude-sonnet-5-5" and c.coach_schedule_weekly
    assert load('backend = "ollama"\nmodel = "qwen3"\n').coach_model_effective == "qwen3"
    for bad, msg in (('backend = "gpt"', "coach.backend"), ("max_tool_calls = 0", "max_tool_calls"), ("max_tool_calls = 500", "max_tool_calls"),
                     ("timeout_seconds = 1", "timeout_seconds"), ("max_tokens = 10", "max_tokens"), ('schedule_weekly = "yes"', "true or false"),
                     ('model = "a b; rm"', "coach.model")):
        with pytest.raises(ConfigError, match=msg):
            load(bad)
    cli(cfg, "config", "show")
    out = capsys.readouterr().out
    assert "coach.backend" in out and "coach.model" in out and "backend default" in out and "coach.schedule_weekly" in out


# ---------------------------------------------------------------- the scheduler step (opt-in, warn only)

def test_the_scheduler_step_is_off_by_default_and_warn_only_when_on(cfg, monkeypatch, db_key):
    con = connect(cfg, create=True)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Test','FR','2099-01-01','t','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','X','FR76','EUR','CACC','{}')")
    con.commit()
    con.close()
    def client():
        return FakeClient({"acc1": [{"transactions": []}]})
    called = []
    monkeypatch.setattr(dg, "run_digest", lambda cfg, kind, **kw: called.append(kind) or {"status": "done"})
    out = []
    assert sch.run_daily(cfg, client=client(), out=out.append) == 0
    assert called == [] and "coach:" not in (cfg.log_dir / "schedule.log").read_text()
    cfg.coach_schedule_weekly = True
    cfg.coach_schedule_monthly = True
    assert sch.run_daily(cfg, client=client(), out=out.append) == 0
    assert called == ["weekly", "monthly"] and "coach: weekly=done monthly=done" in (cfg.log_dir / "schedule.log").read_text()
    monkeypatch.setattr(dg, "run_digest", lambda cfg, kind, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    out.clear()
    assert sch.run_daily(cfg, client=client(), out=out.append) == 0           # never fails the daily job
    assert any("WARNING coach weekly digest could not run" in o for o in out)
    monkeypatch.setattr(dg, "run_digest", lambda cfg, kind, **kw: {"status": "failed", "reason": "no claude"})
    out.clear()
    assert sch.run_daily(cfg, client=client(), out=out.append) == 0
    assert any("WARNING coach weekly digest failed: no claude" in o for o in out)
