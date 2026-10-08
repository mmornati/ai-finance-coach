"""E6-3 / E6-4 / E6-8: running the coach through each backend with fakes (no model, no network, no real `claude`)."""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from coach.agent import prompt as P
from coach.agent.runner import CoachUnavailable, claude_command, mcp_config, run_agent
from coach.mcp.tools import TOOL_NAMES
from mcphelpers import TODAY, inject, session, world, build_world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch, cfg):
    cfg.coach_claude_env = ("FAKE_CLAUDE", "FAKE_CLAUDE_OUT")        # the test harness variables the minimal allowlist would drop
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


def go(cfg, question="Why was September high?", spec=P.ASK, **kw):
    events = []
    res = run_agent(cfg, spec, question, emit=lambda e, d: events.append((e, d)), insecure=True, **kw)
    return res, events


# ---------------------------------------------------------------- claude-code

def test_claude_code_is_called_with_only_the_finance_tools_and_the_question_on_stdin(cfg, world, fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-should-not-pass")  # allowlist secret: a fake, to prove it is not passed on
    monkeypatch.setenv("COACH_DB_KEY", "secret-db-key")
    out = fake_claude(tools=[], steps=[{"tool": "coverage"}, {"tool": "transactions_search", "args": {"category": "income.salary", "limit": 2}}],
                      final="Salary was 17500.00 EUR in 7 payments ($REF0). You also spent 999.99 EUR.")
    res, events = go(cfg, "Why was September high? My secret question text")
    rec = json.loads(out.read_text())
    argv = rec["argv"]
    assert argv[0] == "-p" and "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv and "--include-partial-messages" in argv
    assert argv[argv.index("--tools") + 1] == ""                                   # no built-in tool at all
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert sorted(allowed) == sorted("mcp__finance__" + n for n in TOOL_NAMES)
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Read", "Write", "Edit", "WebSearch", "WebFetch"} <= set(denied)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and "--disable-slash-commands" in argv
    assert "NUMBERS COME FROM THE TOOLS" in argv[argv.index("--system-prompt") + 1]
    assert "My secret question" not in " ".join(argv) and "My secret question" in rec["stdin"]      # never in argv
    assert rec["has_api_key"] is False and rec["env_has_db_key"] is False                          # secrets are not passed on
    assert Path(rec["cwd"]).name.startswith("coach-run-") and not Path(rec["cwd"]).is_relative_to(ROOT)   # a temp dir outside the repo
    assert not Path(rec["cwd"]).exists()                                                           # and removed afterwards
    assert argv[argv.index("--setting-sources") + 1] == "" and json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
    assert argv[argv.index("--max-budget-usd") + 1] == "1"
    srv = rec["mcp"]["mcpServers"]
    assert list(srv) == ["finance"] and srv["finance"]["args"][-4:-1] == ["mcp", "serve", "--session"]
    assert "secret-db-key" not in json.dumps(rec["mcp"]) and "sk-ant" not in json.dumps(rec["mcp"])
    # result
    assert res.finish_reason == "stop" and res.backend == "claude-code" and res.model == "sonnet"
    assert [c["name"] for c in res.tool_calls] == ["coverage", "transactions_search"]
    assert res.usage.tokens_in == 1200 and res.usage.cost_usd == 0.0123 and res.usage.cache_read_tokens == 800
    assert res.refs and res.refs[0].startswith("h_") and res.refs[0] in res.text
    assert res.unverified_numbers == ["999.99"]                                                    # 17500.00 and 7 were in tool outputs
    kinds = [e for e, _ in events]
    assert kinds[0] == "status" and kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2 and "delta" in kinds
    assert "".join(d["text"] for e, d in events if e == "delta") == res.text


def test_a_hostile_merchant_marks_the_run_suspicious_and_a_proposal_is_flagged(cfg, world, fake_claude):
    inject(world)
    fake_claude(steps=[{"tool": "transactions_search", "args": {"merchant_contains": "ignore"}},
                       {"tool": "memory_propose", "args": {"file": "assets.yaml", "ops": [{"op": "set", "path": "family-house.value", "value": 9}],
                                                           "reason": "the merchant text said so"}}],
                final="I made a proposal: $PID (nothing is applied).")
    res, events = go(cfg)
    assert res.suspicious and len(res.proposals) == 1 and res.proposals[0] in res.text
    assert any(e == "notice" and d["code"] == "suspicious" for e, d in events)
    assert any(e == "proposal" and d["id"] == res.proposals[0] for e, d in events)
    from coach.memory import proposals
    from coach.memory.store import MemoryStore
    store = MemoryStore(cfg.memory_dir, history=False)
    assert proposals.suspicious_paths(proposals.get(store, res.proposals[0])) == ["family-house.value"]


def test_the_run_is_stopped_if_claude_offers_a_tool_that_is_not_a_finance_tool(cfg, world, fake_claude):
    fake_claude(init_tools=["mcp__finance__coverage", "Bash"], steps=[{"tool": "coverage"}], final="x")
    res, events = go(cfg)
    assert res.finish_reason == "unsafe_tools" and not res.tool_calls
    assert any(e == "error" and d["code"] == "unexpected_tool" for e, d in events)


def test_a_tool_call_to_anything_but_a_finance_tool_stops_the_run(cfg, world, fake_claude):
    fake_claude(steps=[{"tool": "raw:Bash", "args": {"command": "cat memory/household.yaml"}}], final="x")
    res, events = go(cfg)
    assert res.finish_reason == "unsafe_tools" and not res.tool_calls


def test_the_tool_budget_is_enforced(cfg, world, fake_claude):
    cfg.coach_max_tool_calls = 2
    fake_claude(steps=[{"tool": "coverage"}] * 5, final="done")
    res, _ = go(cfg)
    assert res.finish_reason == "max_tool_calls" and len(res.tool_calls) == 2


def test_timeout_and_cancel_kill_the_process(cfg, world, fake_claude):
    cfg.coach_timeout = 1
    fake_claude(sleep=30, final="never")
    res, _ = go(cfg)
    assert res.finish_reason == "timeout"
    cfg.coach_timeout = 60
    fake_claude(sleep=30, final="never")
    ev = threading.Event()
    threading.Timer(0.8, ev.set).start()
    res, _ = go(cfg, cancel=ev)
    assert res.finish_reason == "cancelled"


def test_a_failing_claude_is_reported_without_echoing_the_prompt(cfg, world, fake_claude):
    fake_claude(exit_code=3, final="")
    res, _ = go(cfg, "my private question about Anna")
    assert res.finish_reason == "error" and "exit 3" in res.error and "Anna" not in res.error


def test_claude_missing_is_a_clear_unavailable_error(cfg, world, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(CoachUnavailable, match="claude"):
        run_agent(cfg, P.ASK, "q", insecure=True)


def test_the_command_line_and_the_mcp_config_do_not_carry_secrets(cfg, monkeypatch):
    monkeypatch.setenv("COACH_DB", "/scratch/finance.db")
    monkeypatch.setenv("COACH_DB_KEY", "k")
    c = mcp_config(cfg, "j_1", True)["mcpServers"]["finance"]
    assert c["env"]["COACH_DB"] == "/scratch/finance.db" and "COACH_DB_KEY" not in c["env"] and "--insecure" in c["args"]
    cmd = claude_command(cfg, P.ASK, Path("/x/mcp.json"), "sonnet", 12)
    assert "--restricted" not in cmd
    cfg.coach_claude_restricted = True
    assert "--restricted" in claude_command(cfg, P.ASK, Path("/x/mcp.json"), "sonnet", 12)


# ---------------------------------------------------------------- anthropic-api (fake client, same tool session)

class FakeStream:
    def __init__(self, blocks, stop, usage, texts):
        self.text_stream = iter(texts)
        self._final = SimpleNamespace(content=blocks, stop_reason=stop, usage=SimpleNamespace(**usage))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._final


class FakeClient:
    def __init__(self, turns):
        self.turns, self.calls = list(turns), []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        return self.turns.pop(0)


def tool_use(i, name, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu{i}", name=name, input=inp)


def test_anthropic_loop_uses_the_same_tools_caches_the_prompt_and_logs_usage(cfg, world):
    u = dict(input_tokens=500, output_tokens=50, cache_read_input_tokens=300, cache_creation_input_tokens=0)
    client = FakeClient([
        FakeStream([tool_use(1, "coverage"), tool_use(2, "transactions_search", category="income.salary", limit=1)], "tool_use", u, []),
        FakeStream([SimpleNamespace(type="text", text="ok")], "end_turn", u, ["Salary ", "was 17500.00 EUR."])])
    res, events = go(cfg, backend="anthropic-api", model="sonnet", client=client)
    assert res.finish_reason == "stop" and res.text == "Salary was 17500.00 EUR." and res.unverified_numbers == []
    assert res.model == "claude-sonnet-5-5" and res.usage.tokens_in == 1000 and res.usage.cache_read_tokens == 600
    assert res.usage.cost_usd and res.usage.cost_usd > 0
    first = client.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"} and first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in first["tools"]} == set(TOOL_NAMES)
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu1", "tu2"] and "account-main-1" in results[0]["content"]
    assert [e for e, _ in events].count("tool_call") == 2


def test_anthropic_loop_budget_and_refusal(cfg, world):
    cfg.coach_max_tool_calls = 1
    u = dict(input_tokens=1, output_tokens=1)
    client = FakeClient([FakeStream([tool_use(1, "coverage"), tool_use(2, "goals")], "tool_use", u, []),
                         FakeStream([SimpleNamespace(type="text", text="ok")], "end_turn", u, ["Enough."])])
    res, _ = go(cfg, backend="anthropic-api", client=client)
    assert res.finish_reason == "max_tool_calls" and len(res.tool_calls) == 1 and res.text == "Enough."
    assert client.calls[1]["tool_choice"] == {"type": "none"}
    client = FakeClient([FakeStream([], "refusal", u, [])])
    res, _ = go(cfg, backend="anthropic-api", client=client)
    assert res.finish_reason == "refused"


def test_anthropic_without_a_key_is_unavailable(cfg, world):
    with pytest.raises(CoachUnavailable, match="API key"):
        run_agent(cfg, P.ASK, "q", insecure=True, backend="anthropic-api")


def test_anthropic_api_error_is_a_result_not_a_crash(cfg, world):
    class Boom:
        messages = None

        def stream(self, **kw):
            raise RuntimeError("503 overloaded Anna")
    b = Boom()
    b.messages = b
    res, _ = go(cfg, backend="anthropic-api", client=b)
    assert res.finish_reason == "error" and "RuntimeError" in res.error


# ---------------------------------------------------------------- ollama

class R:
    def __init__(self, body, status=200, text=""):
        self._b, self.status_code, self.text = body, status, text

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_ollama_refuses_a_model_without_tool_support(cfg, world):
    def post(url, **kw):
        assert url.endswith("/api/show")
        return R({"capabilities": ["completion"]})
    with pytest.raises(CoachUnavailable, match="does not support tool calling"):
        run_agent(cfg, P.ASK, "q", insecure=True, backend="ollama", model="tiny", post=post)


def test_ollama_tool_loop(cfg, world):
    seen = []

    def post(url, **kw):
        seen.append(url)
        if url.endswith("/api/show"):
            return R({"capabilities": ["completion", "tools"]})
        msgs = kw["json"]["messages"]
        if not any(m["role"] == "tool" for m in msgs):
            return R({"message": {"content": "", "tool_calls": [{"function": {"name": "coverage", "arguments": {}}}]}, "prompt_eval_count": 10, "eval_count": 2})
        return R({"message": {"content": "Coverage looks fine."}, "prompt_eval_count": 20, "eval_count": 5})
    res, _ = go(cfg, backend="ollama", model="qwen3", post=post)
    assert res.text == "Coverage looks fine." and res.usage.tokens_in == 30 and res.usage.cost_is_estimate is False
    assert [c["name"] for c in res.tool_calls] == ["coverage"]


def test_ollama_400_on_tools_is_a_clear_refusal(cfg, world):
    def post(url, **kw):
        return R({"capabilities": None}) if url.endswith("/api/show") else R({}, 400, "registry.ollama.ai/x does not support tools")
    with pytest.raises(CoachUnavailable, match="tool calling"):
        run_agent(cfg, P.ASK, "q", insecure=True, backend="ollama", model="x", post=post)


def test_anthropic_coach_runs_are_journaled_as_coach_purposes(cfg, world, db_key):
    """E11-1: the journal purpose of a coach run on the anthropic-api backend is coach.*, never a classify purpose."""
    from coach import egress
    from coach.db import connect
    egress.activate(cfg, insecure=True)                 # the synthetic world database is plaintext
    u = dict(input_tokens=5, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    client = FakeClient([FakeStream([SimpleNamespace(type="text", text="ok")], "end_turn", u, ["fine"])])
    res, _ = go(cfg, backend="anthropic-api", model="sonnet", client=client)
    assert res.finish_reason == "stop"
    con = connect(cfg, insecure=True)
    rows = con.execute("SELECT kind, purpose FROM egress_journal").fetchall()
    con.close()
    assert rows and all(k == "llm.anthropic-api" and p.startswith("coach.") for k, p in rows), rows


# ---------------------------------------------------------------- openai-compatible

def _oa(cfg):
    cfg.llm_openai_base_url = "https://openrouter.ai/api/v1"
    return cfg


def test_openai_compatible_tool_loop(cfg, world):
    bodies = []

    def post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        assert url == "https://openrouter.ai/api/v1/chat/completions" and headers["Authorization"] == "Bearer k"
        if not any(m["role"] == "tool" for m in json["messages"]):
            return R({"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "coverage", "arguments": "{}"}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001}})
        tool_msg = next(m for m in json["messages"] if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "call_1"
        return R({"choices": [{"finish_reason": "stop", "message": {"content": "Coverage looks fine."}}],
                  "usage": {"prompt_tokens": 20, "completion_tokens": 5, "cost": 0.002}})
    res, _ = go(_oa(cfg), backend="openai-compatible", model="anthropic/claude-sonnet-4.5", post=post, api_key="k")
    assert res.text == "Coverage looks fine." and res.finish_reason == "stop"
    assert [c["name"] for c in res.tool_calls] == ["coverage"]
    assert res.usage.tokens_in == 30 and res.usage.cost_usd == pytest.approx(0.003) and res.usage.cost_is_estimate is False
    assert bodies[0]["tools"][0]["type"] == "function" and bodies[0]["provider"] == {"data_collection": "deny"}


def test_openai_compatible_model_without_tools_is_a_clear_refusal(cfg, world):
    def post(url, **kw):
        return R({}, 404, '{"error":{"message":"No endpoints found that support tool use"}}')
    with pytest.raises(CoachUnavailable, match="tool calling"):
        run_agent(_oa(cfg), P.ASK, "q", insecure=True, backend="openai-compatible", model="x/y", post=post, api_key="k")


def _no_network(*a, **k):
    raise AssertionError("no request may be sent")


def test_openai_compatible_needs_a_key_and_a_model(cfg, world, monkeypatch):
    monkeypatch.setattr("coach.secrets.get_secret", lambda *a, **k: None)
    with pytest.raises(CoachUnavailable, match="openai_api_key"):
        run_agent(_oa(cfg), P.ASK, "q", insecure=True, backend="openai-compatible", model="x/y", post=_no_network)
    cfg.coach_backend, cfg.coach_model, cfg.llm_openai_model = "openai-compatible", None, ""
    with pytest.raises(CoachUnavailable, match="no model"):
        run_agent(cfg, P.ASK, "q", insecure=True, post=_no_network, api_key="k")


def test_openai_compatible_is_refused_in_local_only_mode(cfg, world):
    cfg.privacy_local_only = True
    with pytest.raises(CoachUnavailable, match="local_only"):
        run_agent(_oa(cfg), P.ASK, "q", insecure=True, backend="openai-compatible", model="x/y", api_key="k", post=_no_network)



# ---------------------------------------------------------------- claude-code in a container

def test_claude_gets_the_oauth_token_secret_and_the_mcp_server_the_secret_locations_only(cfg, monkeypatch):
    from coach import secrets
    from coach.agent.runner import claude_env
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in claude_env(cfg)                     # no secret: the CLI's own login
    secrets.set_secret("claude_code_oauth_token", "test-oauth-token")
    assert claude_env(cfg)["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-token"
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", "/run/secrets")
    monkeypatch.setenv("COACH_DB_KEY", "secret-db-key")
    env = mcp_config(cfg, "s1", insecure=True)["mcpServers"]["finance"]["env"]
    assert env["COACH_SECRETS_BACKEND"] == "file" and env["COACH_SECRETS_DIR"] == "/run/secrets"   # the child finds /run/secrets
    assert "COACH_DB_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env                     # never a secret value
