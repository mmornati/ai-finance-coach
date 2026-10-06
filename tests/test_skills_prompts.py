"""E7: the PromptSpecs of the skills, `coach coach ask --skill`, `coach coach skills|tool`, and runs through the fake `claude` that
talks to the real MCP server (no model, no network, no web)."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from coach.agent import commands as agc, insights as I, prompt as P
from coach.agent.runner import RunResult, claude_command, describe_payload, run_agent
from coach.classify.backends import Usage
from coach.cli import main
from coach.mcp.tools import READ_ONLY, TOOL_NAMES
from mcphelpers import TODAY, build_world, world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("monthly-review", "explain-spike", "subscription-audit", "contract-check", "mortgage-check", "what-if", "tax-helper",
          "onboarding-interview")


def cli(cfg, *argv):
    main(["--insecure", "--config", str(cfg.config_path), *argv])


@pytest.fixture
def fake_claude(tmp_path, monkeypatch, cfg):
    cfg.coach_claude_env = ("FAKE_CLAUDE", "FAKE_CLAUDE_OUT")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv[0] = {str(ROOT / 'tests' / 'fake_claude.py')!r}\n"
                   f"runpy.run_path({str(ROOT / 'tests' / 'fake_claude.py')!r}, run_name='__main__')\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    out = tmp_path / "claude-call.json"
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))

    def scenario(**s):
        monkeypatch.setenv("FAKE_CLAUDE", json.dumps(s))
        return out
    return scenario


# ---------------------------------------------------------------- the registry

def test_every_skill_has_a_prompt_spec_except_find_cheaper_which_needs_the_web():
    assert set(P.SKILLS) == set(SKILLS) | {"find-cheaper"}
    assert set(P.SKILL_SPECS) == set(SKILLS) and P.SKILLS["find-cheaper"]["spec"] is None and not P.SKILLS["find-cheaper"]["web"]
    assert {i["story"] for i in P.SKILLS.values()} == {f"E7-{n}" for n in (3, 4, 5, 6, 7, 8, 9, 10, 11)}
    for sid, spec in P.SKILL_SPECS.items():
        assert spec.id == sid and P.PROMPTS[sid] is spec
        assert spec.kind in I.KINDS and spec.stores and spec.tool_factor >= 1
        assert "{question}" in spec.user and spec.tools and set(spec.tools) <= set(TOOL_NAMES), sid
        assert set(READ_ONLY) <= set(spec.tools) or spec.id == "digest"
        # the coach runtime has no web tool and no prompt asks for one
        for word in ("WebSearch", "WebFetch", "browse the web", "search the web"):
            assert word not in spec.user, (sid, word)
    assert "monthly-review" in P.PROMPTS and "find-cheaper" not in P.PROMPTS


def test_which_skill_may_write_what():
    writers = {sid: sorted(set(s.tools) - set(READ_ONLY)) for sid, s in P.SKILL_SPECS.items()}
    assert writers["monthly-review"] == ["add_insight"] and writers["explain-spike"] == ["add_insight"]
    assert writers["what-if"] == ["add_insight"] and writers["tax-helper"] == ["add_insight"]
    assert writers["subscription-audit"] == ["add_insight", "questions_propose"] == writers["mortgage-check"]
    assert writers["contract-check"] == ["memory_propose", "questions_propose"] == writers["onboarding-interview"]
    assert "memory_propose" not in P.DIGEST_TOOLS and len(P.DIGEST_TOOLS) == 17 and len(P.ANALYTICS_TOOLS) == 16
    for sid, s in P.SKILL_SPECS.items():
        assert "accept" not in " ".join(s.tools) and "reject" not in " ".join(s.tools)


def test_user_message_fills_the_request_or_says_to_use_the_defaults():
    assert "Review last month" in P.user_message(P.MONTHLY_REVIEW, "Review last month")
    assert "(none: use the defaults)" in P.user_message(P.MONTHLY_REVIEW, "") and "(none: use the defaults)" in P.user_message(P.TAX_HELPER, None)
    assert P.user_message(P.ASK, "Why?") == "Why?" and P.user_message(P.ASK, "") == ""
    assert P.skill_request("about food", month="2026-09", year=None, country="IT") == "about food\nmonth=2026-09\ncountry=IT"


def test_the_claude_command_of_a_skill_allows_exactly_its_tools_and_never_the_web(cfg):
    for sid, spec in P.SKILL_SPECS.items():
        cmd = claude_command(cfg, spec, Path("/tmp/x.json"), "sonnet", 24)
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        assert sorted(allowed) == sorted("mcp__finance__" + n for n in spec.tools), sid
        denied = set(cmd[cmd.index("--disallowedTools") + 1].split(","))
        assert {"WebSearch", "WebFetch", "Bash", "Read", "Write", "Edit"} <= denied, sid
        assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd


def test_describe_payload_lists_the_skill_tools_for_the_dry_run(cfg, world):
    pl = describe_payload(cfg, P.SUBSCRIPTION_AUDIT, insecure=True)
    names = {t["name"] for t in pl["tools"]}
    assert names == set(P.SUBSCRIPTION_AUDIT.tools) and "questions_propose" in names and "memory_propose" not in names
    assert "subscription_audit" in names


# ---------------------------------------------------------------- real runs through the fake claude and the real MCP server

def go(cfg, spec, question):
    events = []
    res = run_agent(cfg, spec, question, emit=lambda e, d: events.append((e, d)), insecure=True)
    return res, events


def test_the_monthly_review_skill_runs_its_tools_and_may_store_an_insight(cfg, world, fake_claude):
    out = fake_claude(steps=[{"tool": "monthly_review", "args": {"month": "2026-09"}},
                             {"tool": "explain_spike", "args": {"category": "food", "month": "2026-09"}},
                             {"tool": "add_insight", "args": {"kind": "review", "title": "September review", "body": "Income 2500.00 EUR in September."}}],
                      final="Review: income was 2500.00 EUR.")
    res, events = go(cfg, P.MONTHLY_REVIEW, "Review last month")
    assert res.finish_reason == "stop" and [c["name"] for c in res.tool_calls] == ["monthly_review", "explain_spike", "add_insight"]
    assert all(d["ok"] for e, d in events if e == "tool_result")
    argv = json.loads(out.read_text())["argv"]
    assert "Review last month" in json.loads(out.read_text())["stdin"] and "EXACTLY three numbered" in json.loads(out.read_text())["stdin"]
    assert "mcp__finance__monthly_review" in argv[argv.index("--allowedTools") + 1] and "memory_propose" not in argv[argv.index("--allowedTools") + 1]
    assert res.unverified_numbers == []                       # 2500.00 came from monthly_review


def test_a_skill_that_may_not_propose_memory_is_stopped_if_the_model_tries(cfg, world, fake_claude):
    fake_claude(steps=[{"tool": "monthly_review"},
                       {"tool": "memory_propose", "args": {"file": "assets.yaml", "ops": [{"op": "set", "path": "family-house.value", "value": 9}], "reason": "x"}}],
                final="done")
    res, events = go(cfg, P.MONTHLY_REVIEW, "x")
    assert res.finish_reason == "unsafe_tools" and not res.proposals
    assert any(e == "error" and d["code"] == "unexpected_tool" for e, d in events)
    assert not list((cfg.memory_dir / ".proposals").glob("p-*.json")) if (cfg.memory_dir / ".proposals").exists() else True


def test_the_subscription_audit_skill_reads_its_tool_through_the_real_mcp_server(cfg, world, fake_claude):
    fake_claude(steps=[{"tool": "subscription_audit"}], final="done")
    res, events = go(cfg, P.SUBSCRIPTION_AUDIT, "audit")
    assert res.finish_reason == "stop" and [c["name"] for c in res.tool_calls] == ["subscription_audit"]
    assert all(d["ok"] for e, d in events if e == "tool_result")


def test_questions_proposed_by_a_skill_run_are_reported_as_proposals(cfg, world, fake_claude):
    fake_claude(steps=[{"tool": "questions_propose", "args": {"custom": [{"question": "Which subscriptions does the family really use?"}]}}],
                final="done")
    res, events = go(cfg, P.SUBSCRIPTION_AUDIT, "audit")
    assert res.finish_reason == "stop" and len(res.proposals) == 1 and res.proposals[0].startswith("p-")
    assert any(e == "proposal" and d["id"] == res.proposals[0] for e, d in events)
    assert not (cfg.memory_dir / "open-questions.yaml").exists()            # a proposal, not a write


def test_the_web_tools_and_unknown_tools_are_never_available_to_a_skill(cfg, world, fake_claude):
    fake_claude(steps=[{"tool": "raw:WebSearch", "args": {"query": "taux credit immobilier"}}], final="x")
    res, _ = go(cfg, P.MORTGAGE_CHECK, "is my rate good?")
    assert res.finish_reason == "unsafe_tools" and not res.tool_calls


# ---------------------------------------------------------------- the CLI

def test_cli_skills_lists_all_nine_and_marks_find_cheaper_as_claude_code_only(cfg, world, capsys):
    cli(cfg, "coach", "skills", "--verbose")
    out = capsys.readouterr().out
    for sid in P.SKILLS:
        assert sid in out
    assert "find-cheaper" in out and "Claude Code only" in out and "questions_propose" in out


def test_cli_ask_skill_dry_run_prints_the_prompts_and_calls_no_model(cfg, world, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no model must be called")
    monkeypatch.setattr(agc, "run_agent", boom)
    cli(cfg, "coach", "ask", "--skill", "monthly-review", "--month", "2026-09", "--dry-run", "focus on food")
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "monthly-review" in out and "month=2026-09" in out and "focus on food" in out
    assert "monthly_review" in out and "memory_propose" not in out.split("tools the model may call")[1]
    cli(cfg, "coach", "ask", "--skill", "tax-helper", "--year", "2026", "--country", "IT", "--dry-run")
    assert "year=2026" in capsys.readouterr().out


def test_cli_ask_skill_runs_the_skills_spec_with_the_request(cfg, world, monkeypatch, capsys):
    seen = []

    def fake(cfg_, spec, question=None, *, emit=None, insecure=False, **kw):
        seen.append((spec.id, question))
        return RunResult(text="ok", finish_reason="stop", usage=Usage("claude-code", "sonnet", f"coach:{spec.id}", 1, 10, 5, 0, 0, 0.01, True, 1.0),
                         tool_calls=[], session_id="j_x", backend="claude-code", model="sonnet", refs=[], unverified_numbers=[])
    monkeypatch.setattr(agc, "run_agent", fake)
    cli(cfg, "coach", "ask", "--skill", "monthly-review", "--month", "2026-09")
    assert seen == [("monthly-review", "month=2026-09")]
    cli(cfg, "coach", "ask", "Why was September high?")
    assert seen[-1] == ("ask", "Why was September high?")
    from coach.db import connect
    con = connect(cfg, insecure=True)
    rows = con.execute("SELECT kind, skill FROM insights ORDER BY created").fetchall()
    assert ("review", "monthly-review") in rows and ("answer", "ask") in rows


def test_cli_ask_refuses_find_cheaper_unknown_skills_and_an_empty_call(cfg, world, capsys):
    with pytest.raises(SystemExit) as e:
        cli(cfg, "coach", "ask", "--skill", "find-cheaper")
    assert "web search" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli(cfg, "coach", "ask", "--skill", "nonsense")
    assert "no runnable skill" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli(cfg, "coach", "ask")
    assert "--skill" in str(e.value)


def test_cli_tool_runs_a_readonly_tool_redacted_and_refuses_writers_and_unknown_tools(cfg, world, capsys):
    cli(cfg, "coach", "tool", "tax_candidates", "--args", '{"year": 2026, "country": "IT"}')
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["country"] == "IT" and "Agenzia" in data["disclaimer"]
    low = out.lower()
    for w in ("anna", "rossi", "lillebourg", "acme corp"):
        assert w not in low
    for name in ("questions_propose", "memory_propose", "add_insight"):
        with pytest.raises(SystemExit) as e:
            cli(cfg, "coach", "tool", name)
        assert "writes" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli(cfg, "coach", "tool", "rm_rf")
    assert "unknown tool" in str(e.value)
    with pytest.raises(SystemExit):
        cli(cfg, "coach", "tool", "what_if", "--args", "{not json")
    with pytest.raises(SystemExit):
        cli(cfg, "coach", "tool", "what_if", "--args", "{}")                    # a refused call exits non-zero
