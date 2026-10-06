"""Run the coach (E6-3 / E6-4): one prompt + the finance tools through the configured backend, streaming events.

Backends (``[coach] backend``):

* ``claude-code``   headless ``claude -p`` with ONLY the finance MCP server (``--strict-mcp-config``), ``--tools ""`` (no
                    built-in tool at all), ``--allowedTools`` = the finance tools, ``--permission-mode dontAsk``, no session
                    file, our system prompt; a fresh empty working directory so no project file or setting is read. The MCP
                    server is a child process of claude (stdio): ``python -m coach mcp serve``.
* ``anthropic-api`` the SDK's tool-use loop in this process over the SAME :class:`~coach.mcp.tools.ToolSession`, with prompt
                    caching (system prompt + tool definitions) and the base url pinned.
* ``ollama``        a tool-calling loop against the local server; refuses clearly when the model has no tool support.

Every backend feeds one :class:`RunState`, which enforces the tool budget, collects what the safety checks need (numbers,
evidence refs, instruction-like text, proposal and insight ids) and emits the events the UI shows. Nothing here ever sees a
raw transaction: it only sees what the tools return.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from coach import egress
from coach.agent import prompt as P
from coach.classify.backends import Usage, estimate_cost
from coach.mcp import guard as G
from coach.mcp.tools import REF_RE, TOOL_NAMES, ToolSession

MCP_PREFIX = "mcp__finance__"
ALLOWED_EXTRA_TOOLS = {"ListMcpResourcesTool", "ReadMcpResourceTool"}        # claude's own helpers for MCP resources
SECRET_ENV = ("ANTHROPIC_API_KEY", "COACH_DB_KEY", "COACH_BACKUP_KEY", "COACH_PROPOSAL_KEY", "ANTHROPIC_AUTH_TOKEN")
FORWARD_ENV = ("COACH_HOME", "COACH_DB", "COACH_MEMORY_DIR", "COACH_DATA_DIR", "COACH_CONFIG_DIR", "COACH_CONFIG",
               "COACH_TAXONOMY_FILE", "COACH_RULES_FILE", "PYTHONPATH", "PATH", "HOME", "LANG", "LC_ALL")
DENIED_BUILTINS = "Bash,Read,Write,Edit,MultiEdit,Glob,Grep,WebSearch,WebFetch,Task,NotebookEdit,TodoWrite"
Emit = Callable[[str, dict], None]


class CoachUnavailable(RuntimeError):
    """The backend cannot run (not installed, no key, model without tools): the message is for the user."""


@dataclass
class RunResult:
    text: str = ""
    finish_reason: str = "stop"           # stop | cancelled | timeout | max_tool_calls | max_tokens | error | refused
    error: Optional[str] = None
    usage: Optional[Usage] = None
    tool_calls: list = field(default_factory=list)
    suspicious: bool = False
    proposals: list = field(default_factory=list)
    insights: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    unverified_numbers: list = field(default_factory=list)
    session_id: str = ""
    backend: str = ""
    model: str = ""
    session_text: str = ""


class RunState:
    """What one run accumulates, whichever backend drives it."""

    def __init__(self, cfg, spec: P.PromptSpec, emit: Emit, cancel: threading.Event, session_id: str):
        self.cfg, self.spec, self.emit, self.cancel, self.session_id = cfg, spec, emit, cancel, session_id
        self.max_calls = cfg.coach_max_tool_calls * spec.tool_factor
        self.text_parts: list[str] = []
        self.ledger = G.NumberLedger()
        self.refs: set[str] = set()
        self.suspicious = False
        self.proposals: list[str] = []
        self.insights: list[str] = []
        self.calls: list[dict] = []
        self.pending: dict[str, dict] = {}
        self.over_budget = False

    def text(self, t: str) -> None:
        if t:
            self.text_parts.append(t)
            self.emit("delta", {"text": t})

    def tool_call(self, call_id: str, name: str, args) -> bool:
        """False when the budget is spent (the backend must stop calling tools)."""
        if len(self.calls) >= self.max_calls:
            self.over_budget = True
            return False
        short = name[len(MCP_PREFIX):] if name.startswith(MCP_PREFIX) else name
        summary = json.dumps(args, ensure_ascii=False)[:160] if args else ""
        self.calls.append({"id": call_id, "name": short, "args": summary})
        self.pending[call_id] = {"name": short}
        self.emit("tool_call", {"id": call_id, "name": short, "args": summary, "n": len(self.calls), "max": self.max_calls})
        return True

    def tool_result(self, call_id: str, text: str, ok: bool = True) -> None:
        name = (self.pending.pop(call_id, None) or {}).get("name", "?")
        flagged = False
        parsed = None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if parsed is not None:
            self.ledger.add(parsed)
            if isinstance(parsed, dict):
                if parsed.get("security_notice"):
                    flagged = True
                if parsed.get("proposal_id"):
                    self.proposals.append(parsed["proposal_id"])
                    self.emit("proposal", {"id": parsed["proposal_id"], "file": parsed.get("file"),
                                           "command": parsed.get("accept_command")})
                if parsed.get("insight_id"):
                    self.insights.append(parsed["insight_id"])
            if G.scan_all(parsed):
                flagged = True
        elif text and G.scan_injection(text):
            flagged = True
        self.refs.update(REF_RE.findall(text or ""))
        if flagged and not self.suspicious:
            self.suspicious = True
            self.emit("notice", {"code": "suspicious", "message": "Text in your data looks like an instruction aimed at the "
                                 "coach (for example inside a merchant name). It was treated as data and ignored; any "
                                 "memory proposal from this session will need field-by-field confirmation."})
        self.emit("tool_result", {"id": call_id, "name": name, "ok": ok, "chars": len(text or ""), "suspicious": flagged})

    def result(self, finish: str, usage: Optional[Usage], backend: str, model: str, error: Optional[str] = None) -> RunResult:
        text = "".join(self.text_parts).strip()
        cited = [r for r in dict.fromkeys(REF_RE.findall(text)) if r in self.refs]
        return RunResult(text=text, finish_reason=finish, error=error, usage=usage, tool_calls=list(self.calls),
                         suspicious=self.suspicious, proposals=list(dict.fromkeys(self.proposals)),
                         insights=list(dict.fromkeys(self.insights)), refs=cited,
                         unverified_numbers=self.ledger.unverified(text, strict=False) if text else [],
                         session_id=self.session_id, backend=backend, model=model)


# ---------------------------------------------------------------- claude -p

# What `claude` is started with. A MINIMAL allowlist: no proxy variable, no ANTHROPIC_BASE_URL, no API key, no COACH_* secret.
# macOS authentication of a subscription login reads the Keychain through HOME / USER; extra names (a proxy, a config dir,
# CLAUDE_CODE_OAUTH_TOKEN ...) are passed only when listed in [coach] claude_env.
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "SHELL", "__CF_USER_TEXT_ENCODING")
BUILTIN_AGENTS = {"general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide"}
INIT_TIMEOUT = 45.0
ISOLATION = ("claude starts in an EMPTY temporary directory outside the repository (no CLAUDE.md, no .claude/, no project "
             "settings), with --setting-sources \"\" (no user / project / local settings file), --settings disableAllHooks, "
             "--strict-mcp-config (only the finance server), --disable-slash-commands, --tools \"\" and an allowlist of the finance "
             "tools. The run is stopped if the init event reports any plugin, skill, hook, agent beyond the built-in ones, or MCP "
             "server other than `finance`. What this does NOT do: it does not sandbox the `claude` binary itself, and the user-level "
             "~/.claude.json (login) is still read for authentication.")


def claude_env(cfg) -> dict:
    env = {k: os.environ[k] for k in ENV_ALLOW if os.environ.get(k)}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})
    for k in getattr(cfg, "coach_claude_env", ()) or ():
        if k in os.environ and k not in SECRET_ENV:
            env[k] = os.environ[k]
    return env


def mcp_config(cfg, session_id: str, insecure: bool, only=None) -> dict:
    """The only MCP server claude gets. No secret goes in it: the database key comes from the Keychain, in the child."""
    env = {k: os.environ[k] for k in FORWARD_ENV if os.environ.get(k)}
    env.setdefault("COACH_HOME", str(cfg.root))
    args = ["-m", "coach"] + (["--insecure"] if insecure else []) + (["--config", str(cfg.config_path)] if cfg.config_path else []) \
        + ["mcp", "serve", "--session", session_id] + (["--only", ",".join(only)] if only else [])
    return {"mcpServers": {"finance": {"command": sys.executable, "args": args, "env": env}}}


def claude_command(cfg, spec: P.PromptSpec, mcp_path: Path, model: str, max_calls: int) -> list[str]:
    allowed = ",".join(MCP_PREFIX + n for n in (spec.tools or TOOL_NAMES))
    cmd = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--no-session-persistence", "--strict-mcp-config", "--mcp-config", str(mcp_path),
           "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
           "--tools", "", "--allowedTools", allowed, "--disallowedTools", DENIED_BUILTINS,
           "--permission-mode", "dontAsk", "--disable-slash-commands",
           "--system-prompt", P.system_prompt(max_calls)]
    budget = float(getattr(cfg, "coach_max_budget_usd", 0) or 0) * spec.tool_factor
    if budget > 0:
        cmd += ["--max-budget-usd", f"{budget:g}"]
    if cfg.coach_claude_restricted:
        cmd.append("--restricted")
    return cmd


def describe_payload(cfg, spec: P.PromptSpec, session_id: str = "<job>", insecure: bool = False, model: Optional[str] = None) -> dict:
    """Everything a run would send / start, built by the SAME functions as the real run (the dry run prints it)."""
    from coach.mcp.server import INSTRUCTIONS
    tools = ToolSession(cfg, insecure=insecure, session_id="describe", only=spec.tools)
    model = model or cfg.coach_model_effective
    mc = mcp_config(cfg, session_id, insecure, only=spec.tools)
    return {"backend": cfg.coach_backend, "model": model, "system_prompt": P.system_prompt(cfg.coach_max_tool_calls * spec.tool_factor),
            "user_prompt": P.user_message(spec), "tools": tools.listing(), "mcp_instructions": INSTRUCTIONS,
            "claude_command": claude_command(cfg, spec, Path("<tmp>/mcp.json"), model, cfg.coach_max_tool_calls * spec.tool_factor),
            "mcp_config": mc, "claude_env": sorted(claude_env(cfg)), "isolation": ISOLATION}


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()


def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x) -> list:
    return x if isinstance(x, list) else []


def _names(x) -> list[str]:
    out = []
    for i in _l(x):
        out.append(str(_d(i).get("name", "?")) if isinstance(i, dict) else str(i))
    return [n[:60] for n in out]


def _tool_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(_d(b).get("text", "")) for b in _l(content) if _d(b).get("type") == "text")


def _idents(i) -> list[str]:
    """Every identifier an init entry (a string or an object) carries: name / id / identifier / plugin / source / key."""
    if isinstance(i, dict):
        return [str(i[k])[:80] for k in ("name", "id", "identifier", "plugin", "source", "key") if isinstance(i.get(k), (str, int))]
    return [str(i)[:80]]


def _entry_names(x) -> list[str]:
    return [(_idents(i) or ["?"])[0] for i in _l(x)]


def plugin_allowed(entry, allowed_plugins=(), allow_builtin=True) -> bool:
    ids = _idents(entry)
    return any(i in set(allowed_plugins) or (allow_builtin and i.endswith("@builtin")) for i in ids)


def init_problems(ev: dict, allowed_agents=None, allowed_plugins=(), allow_builtin_plugins=True) -> list[str]:
    """Anything in claude's init event that the isolation should have excluded (fail closed)."""
    bad = []
    tools = [t for t in _l(ev.get("tools")) if isinstance(t, str)]
    extra = [t for t in tools if not t.startswith(MCP_PREFIX) and t not in ALLOWED_EXTRA_TOOLS]
    if extra:
        bad.append("tools other than the finance tools: " + ", ".join(extra[:4]))
    servers = [n for n in _names(ev.get("mcp_servers")) if n != "finance"]
    if servers:
        bad.append("MCP servers other than finance: " + ", ".join(servers[:4]))
    foreign = [(_idents(p) or ["?"])[0] for p in _l(ev.get("plugins")) if not plugin_allowed(p, allowed_plugins, allow_builtin_plugins)]
    if foreign:
        bad.append("plugins other than the allowed ones: " + ", ".join(foreign[:4]))
    for key in ("skills", "hooks"):
        if _l(ev.get(key)) or (isinstance(ev.get(key), dict) and ev.get(key)):
            bad.append(f"{key} are loaded")
    agents = [a for a in _names(ev.get("agents")) if a not in set(allowed_agents if allowed_agents is not None else BUILTIN_AGENTS)]
    if agents:
        bad.append("agents other than the built-in ones: " + ", ".join(agents[:4]))
    return bad


def init_summary(ev: dict) -> dict:
    """The init event, reduced to counts and short names (what the job log keeps)."""
    return {"model": str(ev.get("model", ""))[:60], "version": str(ev.get("claude_code_version", ""))[:20],
            "tools": len(_l(ev.get("tools"))), "mcp_servers": _names(ev.get("mcp_servers")), "plugins": _entry_names(ev.get("plugins"))[:12],
            "skills": _entry_names(ev.get("skills"))[:12], "agents": _entry_names(ev.get("agents"))[:12], "permission_mode": str(ev.get("permissionMode", ""))[:20]}


def log_init(cfg, session_id: str, summary: dict, problems=()) -> None:
    """Append what claude reported at start-up (names only) to <data_dir>/logs/coach-init.log: written by every route (web, CLI)."""
    try:
        logs = cfg.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        with open(logs / "coach-init.log", "a") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {session_id} {json.dumps(summary, ensure_ascii=False)}"
                    + (f" REFUSED: {'; '.join(problems)[:300]}" if problems else "") + "\n")
    except OSError:
        pass


def egress_purpose(spec) -> str:
    """The purpose written to the egress journal for a coach run."""
    return "coach.ask" if spec.id == "ask" else "coach.digest" if spec.kind == "digest" else "coach.skill"


def preflight(cfg, backend: str, spec) -> None:
    """E11-4: a backend the privacy mode forbids is refused BEFORE a run starts, as 'the coach is unavailable' with the reason (the
    refusal is journaled). The per-request gates below stay the authority."""
    host = egress.host_of(getattr(cfg, "llm_ollama_url", "")) if backend == "ollama" else ""
    try:
        egress.require(f"llm.{backend}", {"host": host, "purpose": egress_purpose(spec)}, cfg=cfg)
    except egress.EgressDenied as e:
        raise CoachUnavailable(str(e)) from e


def run_claude_code(cfg, spec, user: str, st: RunState, model: str, *, insecure: bool, popen=subprocess.Popen) -> RunResult:
    # E11-1 / E11-4: the egress gate (refused under [privacy] local_only), before anything is started
    egress.allow("llm.claude-code", {"purpose": egress_purpose(spec), "bytes": len(user.encode("utf-8")) + len(P.system_prompt(st.max_calls))},
                 cfg=cfg)
    if shutil.which("claude") is None:
        raise CoachUnavailable("the `claude` command is not installed or not in PATH (backend claude-code): install Claude "
                               "Code, or set [coach] backend = \"anthropic-api\" in config.toml")
    run_dir = Path(tempfile.mkdtemp(prefix="coach-run-"))              # system temp: outside the repository tree
    os.chmod(run_dir, 0o700)
    mcp_path = run_dir / "mcp.json"
    fd = os.open(mcp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(mcp_config(cfg, st.session_id, insecure, only=spec.tools), f)
    cmd = claude_command(cfg, spec, mcp_path, model, st.max_calls)
    env = claude_env(cfg)
    timeout = cfg.coach_timeout * spec.timeout_factor
    t0 = time.monotonic()
    reason = {"v": None}
    result_ev: dict = {}
    partial_seen = False
    stderr_tail: list[str] = []
    proc = None
    finished = threading.Event()
    got_init = threading.Event()
    try:
        proc = popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(run_dir),
                     env=env, start_new_session=True)

        def drain():
            for ln in proc.stderr:
                stderr_tail.append(ln.rstrip())
                del stderr_tail[:-5]

        def watchdog():
            end = time.monotonic() + timeout
            init_end = time.monotonic() + INIT_TIMEOUT
            while not finished.is_set():
                now = time.monotonic()
                why = "cancelled" if st.cancel.is_set() else "timeout" if now > end else \
                    "no_init" if (not got_init.is_set() and now > init_end) else None
                if why:
                    if proc.poll() is None:
                        reason["v"] = why
                        _kill(proc)
                    return
                finished.wait(0.2)

        threading.Thread(target=drain, daemon=True).start()
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            proc.stdin.write(user)                     # the question goes through stdin, never through argv
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            try:
                if kind == "system" and ev.get("subtype") == "init":
                    bad = init_problems(ev, getattr(cfg, "coach_allowed_builtin_agents", None), getattr(cfg, "coach_allowed_plugins", ()),
                                        getattr(cfg, "coach_allow_builtin_plugins", True))
                    summary = init_summary(ev)
                    st.emit("init", summary)
                    log_init(cfg, st.session_id, summary, bad)
                    if bad:
                        reason["v"] = "unsafe_tools"
                        st.emit("error", {"code": "unexpected_tool", "message": "the coach run was stopped: the claude CLI did not "
                                          "start with the expected isolation (" + "; ".join(bad)[:200] + ")."})
                        _kill(proc)
                        break
                    got_init.set()
                elif not got_init.is_set():
                    continue                                   # fail closed: nothing counts before a verified init event
                elif kind == "stream_event":
                    e = _d(ev.get("event"))
                    d = _d(e.get("delta"))
                    if e.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                        partial_seen = True
                        st.text(str(d.get("text", "")))
                elif kind == "assistant":
                    for b in _l(_d(ev.get("message")).get("content")):
                        b = _d(b)
                        if b.get("type") == "text" and not partial_seen:
                            st.text(str(b.get("text", "")))
                        elif b.get("type") == "tool_use":
                            name = str(b.get("name", ""))
                            if name not in {MCP_PREFIX + n for n in (spec.tools or TOOL_NAMES)}:
                                reason["v"] = "unsafe_tools"
                                st.emit("error", {"code": "unexpected_tool", "message": f"the coach tried to use a tool that is not "
                                                  f"an allowed finance tool ({name[:40]}): the run was stopped."})
                                _kill(proc)
                                break
                            if not st.tool_call(str(b.get("id", "")), name, b.get("input")):
                                reason["v"] = "max_tool_calls"
                                _kill(proc)
                                break
                    partial_seen = False
                elif kind == "user":
                    for b in _l(_d(ev.get("message")).get("content")):
                        b = _d(b)
                        if b.get("type") == "tool_result":
                            st.tool_result(str(b.get("tool_use_id", "")), _tool_text(b.get("content")), not b.get("is_error"))
                elif kind == "result":
                    result_ev = ev
            except (TypeError, AttributeError, ValueError):
                continue                                       # a field of an unexpected shape never crashes the run
            if reason["v"]:
                break
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill(proc)
    finally:
        finished.set()
        shutil.rmtree(run_dir, ignore_errors=True)
        if proc is not None and proc.poll() is None:
            _kill(proc)
    u = _d(result_ev.get("usage"))
    num = lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0       # noqa: E731
    cost = result_ev.get("total_cost_usd")
    usage = Usage("claude-code", model, f"coach:{spec.id}", len(st.calls), num(u.get("input_tokens")), num(u.get("output_tokens")),
                  num(u.get("cache_read_input_tokens")), num(u.get("cache_creation_input_tokens")),
                  float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0, True,
                  round(time.monotonic() - t0, 2))
    finish, err = reason["v"], None
    if finish is None and not got_init.is_set():
        finish, err = "no_init", "claude did not report its tools and settings (no init event): the run was not trusted"
    elif finish == "no_init":
        err = f"claude sent no init event within {int(INIT_TIMEOUT)} s: the run was stopped"
    if finish is None:
        if result_ev.get("is_error") or (proc and proc.returncode not in (0, None)):
            finish = "error"
            from coach.classify.backends import safe_diagnostic
            err = f"claude -p failed (exit {proc.returncode if proc else '?'}" + \
                  (f", {str(result_ev.get('subtype'))[:40]}" if result_ev.get("subtype") else "") + ")"
            diag = safe_diagnostic(" ".join(stderr_tail), user, 160)
            if diag:
                err += ": " + diag
        else:
            finish = "stop"
    if not st.text_parts and result_ev.get("result") and finish == "stop":
        st.text(str(result_ev["result"]))
    return st.result(finish, usage, "claude-code", model, err)


# ---------------------------------------------------------------- anthropic-api

def run_anthropic(cfg, spec, user: str, st: RunState, model: str, *, insecure: bool, client=None, tools: Optional[ToolSession] = None) -> RunResult:
    from coach.classify.backends import AnthropicBackend
    be = AnthropicBackend(client=client, base_url=getattr(cfg, "llm_anthropic_base_url", None), cfg=cfg)
    if client is None:
        from coach import secrets
        if not secrets.get_secret("anthropic_api_key", required=False):
            raise CoachUnavailable("no Anthropic API key: run `uv run coach config set-secret anthropic_api_key` (backend "
                                   "anthropic-api), or choose another [coach] backend")
    mid = be.model_id(model)
    own = tools is None
    tools = tools or ToolSession(cfg, insecure=insecure, session_id=st.session_id)
    names = spec.tools or TOOL_NAMES
    tools.specs = {k: v for k, v in tools.specs.items() if k in names}          # the session itself refuses anything else
    defs = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools.listing()]
    if defs:
        defs[-1] = {**defs[-1], "cache_control": {"type": "ephemeral"}}        # caches every tool definition + the system prompt
    system = [{"type": "text", "text": P.system_prompt(st.max_calls), "cache_control": {"type": "ephemeral"}}]
    msgs: list = [{"role": "user", "content": user}]
    tin = tout = cr = cw = 0
    t0 = time.monotonic()
    finish, err = "stop", None
    deadline = t0 + cfg.coach_timeout * spec.timeout_factor
    try:
        while True:
            if st.cancel.is_set():
                finish = "cancelled"
                break
            if time.monotonic() > deadline:
                finish = "timeout"
                break
            kw = dict(model=mid, max_tokens=cfg.coach_max_tokens, system=system, tools=defs, messages=msgs)
            if st.over_budget:
                kw["tool_choice"] = {"type": "none"}
            with be.gated_client(egress_purpose(spec), len(json.dumps(msgs, default=str)) + len(user)).messages.stream(**kw) as stream:
                for piece in stream.text_stream:
                    st.text(piece)
                    if st.cancel.is_set():
                        break
                final = stream.get_final_message()
            u = final.usage
            tin += int(getattr(u, "input_tokens", 0) or 0)
            tout += int(getattr(u, "output_tokens", 0) or 0)
            cr += int(getattr(u, "cache_read_input_tokens", 0) or 0)
            cw += int(getattr(u, "cache_creation_input_tokens", 0) or 0)
            if st.cancel.is_set():
                finish = "cancelled"
                break
            if final.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": final.content})
                results = []
                for b in final.content:
                    if getattr(b, "type", "") != "tool_use":
                        continue
                    if not st.tool_call(b.id, b.name, b.input):
                        results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                        "content": "tool budget spent: answer now with what you already have"})
                        finish = "max_tool_calls"
                        continue
                    res = tools.call(b.name, dict(b.input or {}))
                    st.tool_result(b.id, res.text, res.ok)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": res.text, "is_error": not res.ok})
                msgs.append({"role": "user", "content": results})
                continue
            if final.stop_reason == "max_tokens":
                finish = "max_tokens"
            elif final.stop_reason == "refusal":
                finish = "refused"
            break
    except CoachUnavailable:
        raise
    except Exception as e:                                                     # noqa: BLE001
        finish, err = "error", f"anthropic API error ({type(e).__name__}): {str(e)[:160]}"
    finally:
        if own:
            tools.close()
    usage = Usage("anthropic-api", mid, f"coach:{spec.id}", len(st.calls), tin, tout, cr, cw,
                  estimate_cost(mid, tin, tout, cr, cw), True, round(time.monotonic() - t0, 2))
    return st.result(finish, usage, "anthropic-api", mid, err)


# ---------------------------------------------------------------- ollama

def run_ollama(cfg, spec, user: str, st: RunState, model: str, *, insecure: bool, post=None, tools: Optional[ToolSession] = None) -> RunResult:
    import requests
    from coach.classify.backends import check_ollama_url
    base = cfg.llm_ollama_url.rstrip("/")
    check_ollama_url(base, cfg.llm_ollama_allow_remote)
    post = post or requests.post
    ohost = egress.host_of(base)
    egress.allow("llm.ollama", {"host": ohost, "purpose": egress_purpose(spec), "bytes": len(user)}, cfg=cfg)
    try:
        shown = post(f"{base}/api/show", json={"model": model}, timeout=20)
        caps = (shown.json() or {}).get("capabilities") if shown.status_code == 200 else None
    except Exception as e:                                                     # noqa: BLE001
        raise CoachUnavailable(f"the ollama server at {base} is not reachable ({type(e).__name__})") from e
    if caps is not None and "tools" not in caps:
        raise CoachUnavailable(f"the ollama model {model!r} does not support tool calling, which the coach needs to read your "
                               "figures without receiving raw data. Pick a model with tool support (for example a recent "
                               "llama3.1 / qwen3 build) in [coach] model, or use another backend.")
    own = tools is None
    tools = tools or ToolSession(cfg, insecure=insecure, session_id=st.session_id)
    names = spec.tools or TOOL_NAMES
    tools.specs = {k: v for k, v in tools.specs.items() if k in names}
    fdefs = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
             for t in tools.listing()]
    msgs: list = [{"role": "system", "content": P.system_prompt(st.max_calls)}, {"role": "user", "content": user}]
    tin = tout = 0
    t0 = time.monotonic()
    finish, err = "stop", None
    deadline = t0 + cfg.coach_timeout * spec.timeout_factor
    try:
        for _ in range(st.max_calls + 2):
            if st.cancel.is_set():
                finish = "cancelled"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                finish = "timeout"
                break
            egress.allow("llm.ollama", {"host": ohost, "purpose": egress_purpose(spec), "bytes": len(json.dumps(msgs, default=str))}, cfg=cfg)
            r = post(f"{base}/api/chat", timeout=min(remaining, 300), json={
                "model": model, "stream": False, "messages": msgs, "options": {"temperature": 0, "num_predict": cfg.coach_max_tokens},
                **({} if st.over_budget else {"tools": fdefs})})
            if r.status_code == 400 and "tool" in (r.text or "").lower():
                raise CoachUnavailable(f"the ollama model {model!r} does not support tool calling: pick another model or backend")
            r.raise_for_status()
            body = r.json()
            tin += int(body.get("prompt_eval_count") or 0)
            tout += int(body.get("eval_count") or 0)
            m = body.get("message") or {}
            calls = m.get("tool_calls") or []
            if m.get("content"):
                st.text(m["content"])
            if not calls:
                break
            msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
            for i, c in enumerate(calls):
                fn = c.get("function") or {}
                cid = f"ol{len(st.calls)}_{i}"
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if not st.tool_call(cid, fn.get("name", ""), args):
                    msgs.append({"role": "tool", "content": "tool budget spent: answer now with what you already have"})
                    finish = "max_tool_calls"
                    continue
                res = tools.call(fn.get("name", ""), args)
                st.tool_result(cid, res.text, res.ok)
                msgs.append({"role": "tool", "content": res.text})
        else:
            finish = "max_tool_calls"
    except CoachUnavailable:
        raise
    except Exception as e:                                                     # noqa: BLE001
        finish, err = "error", f"ollama error ({type(e).__name__}): {str(e)[:160]}"
    finally:
        if own:
            tools.close()
    usage = Usage("ollama", model, f"coach:{spec.id}", len(st.calls), tin, tout, 0, 0, 0.0, False, round(time.monotonic() - t0, 2))
    return st.result(finish, usage, "ollama", model, err)


# ---------------------------------------------------------------- entry point

def run_agent(cfg, spec: P.PromptSpec, question: Optional[str] = None, *, emit: Optional[Emit] = None,
              cancel: Optional[threading.Event] = None, insecure: bool = False, session_id: Optional[str] = None,
              backend: Optional[str] = None, model: Optional[str] = None, **backend_kw) -> RunResult:
    """Run one prompt through the configured backend. Raises :class:`CoachUnavailable` when it cannot start; every other
    outcome (cancel, timeout, tool budget, API error) is a RunResult with a finish_reason."""
    import secrets as _s
    emit = emit or (lambda e, d: None)
    cancel = cancel or threading.Event()
    sid = session_id or "j_" + _s.token_hex(4)
    backend = backend or cfg.coach_backend
    model = model or cfg.coach_model_effective
    st = RunState(cfg, spec, emit, cancel, sid)
    user = P.user_message(spec, question)
    preflight(cfg, backend, spec)
    emit("status", {"state": "running", "backend": backend, "model": model, "session_id": sid, "max_tool_calls": st.max_calls})
    if backend == "claude-code":
        return run_claude_code(cfg, spec, user, st, model, insecure=insecure, **backend_kw)
    if backend == "anthropic-api":
        return run_anthropic(cfg, spec, user, st, model, insecure=insecure, **backend_kw)
    if backend == "ollama":
        return run_ollama(cfg, spec, user, st, model, insecure=insecure, **backend_kw)
    raise CoachUnavailable(f"unknown coach backend {backend!r}")
