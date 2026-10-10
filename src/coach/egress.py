"""Egress policy (E11-1, E11-4): the registry of EVERY way data can leave this machine, one check every network-capable call site goes
through, and a local journal of what was sent.

* :data:`INVENTORY` lists each outbound path: destination, data categories, redaction, how it is switched on and how to switch it off.
  `coach privacy report` prints it; docs/privacy.md is generated from the same facts.
* :func:`allow` is the single check. A call site calls ``egress.allow(kind, meta)`` right before it talks to the outside; the check
  applies the ``[privacy]`` settings (``local_only``, ``offline``, ``web_enrich``) and raises :class:`EgressDenied` when the path is
  refused. Every attempt (allowed or refused) is written to the ``egress_journal`` table: time, kind, destination HOST, bytes, purpose,
  redaction mode, outcome. The journal NEVER holds a payload, a URL path, a query, a name or a token.
* ``tests/test_egress_coverage.py`` greps the source for network / subprocess call sites and fails when one is not registered in
  :data:`CALL_SITES` or when a function that reaches the outside does not call :func:`allow`.

Nothing in this module touches the network.
"""
from __future__ import annotations

import atexit
import ipaddress
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


class EgressDenied(RuntimeError):
    """The privacy settings refuse this outbound path. The text says which setting and how to change it; it never holds a payload."""

    def __init__(self, kind: str, code: str, message: str):
        super().__init__(message)
        self.kind, self.code = kind, code


# ---------------------------------------------------------------- the inventory

@dataclass(frozen=True)
class Flow:
    kind: str                  # the identifier call sites pass to allow()
    title: str
    destination: str           # who receives it
    data: str                  # categories of data sent
    redaction: str             # what is applied before sending (short mode name used in the journal) + explanation
    opt_in: str                # what switches it on
    disable: str               # how to switch it off
    external: bool = True      # leaves this machine (False: this machine only)
    local_only: str = "refused"     # behaviour with [privacy] local_only: allowed | refused | loopback only
    offline: str = "refused"        # behaviour with [privacy] offline
    purposes: tuple = ()
    host: str = ""             # default destination host for the journal when the caller gives none
    mode: str = ""             # the redaction mode written to the journal


INVENTORY: tuple[Flow, ...] = (
    Flow("enable_banking", "Enable Banking API (bank data source)",
         "api.enablebanking.com ([enable_banking] api_url)",
         "app id + signed JWT; consent / session ids; account ids; date ranges. The answers (accounts, balances, transactions) come BACK; "
         "nothing from your database is sent.",
         "n/a: the request carries no household data. The private key never leaves the machine (only a JWT signed with it).",
         "connecting a bank (`coach connect`), `coach sync`, the scheduled job",
         "[privacy] offline = true (sync disabled, file imports only); do not run the connect / sync commands; `coach wipe` can revoke the sessions",
         local_only="allowed", offline="refused",
         purposes=("sync", "connect", "consent", "revoke"), host="api.enablebanking.com", mode="none"),
    Flow("llm.claude-code", "LLM through the `claude` CLI (Claude Code, personal subscription)",
         "Anthropic (the `claude -p` process talks to it)",
         "classify run/compare and `eval models`: redacted merchant descriptors without the known towns, payment counts and types, amounts rounded to an "
         "order of magnitude, nearest labelled merchants as hints, the category list and a few labelled examples; classify enrich: the same, plus "
         "the model searches the web with the merchant descriptor; coach ask/digest/skills: the question, and the REDACTED, pseudonymised "
         "results of the finance tools (computed figures, `h_` refs, merchant titles generalised); memory doc extract: a redacted document text",
         "item-redact: IBANs, e-mails, phones, long digits, ids, titled people and household name tokens removed; person-like merchants "
         "never sent (coach.classify.candidates); finance tools pseudonymise accounts / people (coach.analytics.privacy); documents "
         "redacted (coach.memory.docredact)",
         "`classify run|compare|enrich`, `coach coach ask|digest`, the web app's coach, the scheduled job (classify; digests are opt-in), "
         "`memory doc extract --send`",
         "[privacy] local_only = true and llm.backend / coach.backend = \"ollama\"; or do not run those commands",
         local_only="refused", offline="refused",
         purposes=("classify.label", "classify.enrich", "classify.compare", "coach.ask", "coach.digest", "coach.skill", "memory.doc_extract",
                   "eval.models"),
         host="api.anthropic.com (via the claude CLI)", mode="item-redact"),
    Flow("llm.anthropic-api", "LLM through the Anthropic API (key in the Keychain)",
         "api.anthropic.com ([llm] anthropic_base_url)",
         "same as the claude-code row (classify label/compare, coach, doc extract); web search is NOT available on this backend",
         "item-redact (see above)",
         "llm.backend / coach.backend = \"anthropic-api\" and the anthropic_api_key secret",
         "[privacy] local_only = true; or switch the backend to ollama / claude-code",
         local_only="refused", offline="refused",
         purposes=("classify.label", "classify.compare", "coach.ask", "coach.digest", "coach.skill", "memory.doc_extract", "eval.models"),
         host="api.anthropic.com", mode="item-redact"),
    Flow("llm.ollama", "LLM through a local Ollama server",
         "[llm] ollama_url (default http://localhost:11434: this machine)",
         "same as the claude-code row (web search is NOT available on this backend)",
         "item-redact (the same redaction as for a cloud model: defence in depth)",
         "llm.backend / coach.backend = \"ollama\"",
         "choose another backend; a non-loopback ollama_url needs [llm] ollama_allow_remote = true and is refused under local_only",
         external=False, local_only="loopback only", offline="loopback only",
         purposes=("classify.label", "classify.compare", "coach.ask", "coach.digest", "coach.skill", "memory.doc_extract", "eval.models"),
         host="localhost", mode="item-redact"),
    Flow("llm.openai-compatible", "LLM through an OpenAI-compatible API (OpenRouter, Eden AI, vLLM ...; key in the Keychain)",
         "the host of [llm] openai_base_url (default openrouter.ai), which may forward to the model's own provider",
         "same as the claude-code row (classify label/compare, coach, doc extract); web search is NOT available on this backend",
         "item-redact (see above); on OpenRouter, [llm] openrouter_deny_data_collection = true (default) routes only to providers "
         "that do not store or train on prompts",
         "llm.backend / coach.backend = \"openai-compatible\" and the openai_api_key secret",
         "[privacy] local_only = true; or switch the backend to ollama / claude-code / anthropic-api",
         local_only="refused", offline="refused",
         purposes=("classify.label", "classify.compare", "coach.ask", "coach.digest", "coach.skill", "memory.doc_extract", "eval.models"),
         host="openrouter.ai", mode="item-redact"),
    Flow("alerts.ntfy", "Alert channel: ntfy push",
         "the ntfy server of [alerts.ntfy] url (public ntfy.sh or your own)",
         "an alert title + one short line (minimal: no merchant, no account, no bank, no name; amounts only in `summary` detail)",
         "alert-guard: alerts/messages.py refuses anything that looks like a name / account / IBAN / merchant",
         "[alerts.ntfy] enabled = true (OFF by default)",
         "[alerts.ntfy] enabled = false, or [privacy] local_only = true",
         local_only="refused", offline="refused", purposes=("alert",), host="(ntfy server)", mode="alert-guard"),
    Flow("alerts.email", "Alert channel: e-mail (SMTP)",
         "the SMTP server of [alerts.email]", "an alert title + one short line (as ntfy)",
         "alert-guard (as ntfy)", "[alerts.email] enabled = true (OFF by default)",
         "[alerts.email] enabled = false, or [privacy] local_only = true",
         local_only="refused", offline="refused", purposes=("alert",), host="(smtp server)", mode="alert-guard"),
    Flow("alerts.telegram", "Alert channel: Telegram bot",
         "api.telegram.org", "an alert title + one short line (as ntfy); the bot token is in the URL path of the request",
         "alert-guard (as ntfy)", "[alerts.telegram] enabled = true (OFF by default)",
         "[alerts.telegram] enabled = false, or [privacy] local_only = true",
         local_only="refused", offline="refused", purposes=("alert",), host="api.telegram.org", mode="alert-guard"),
    Flow("alerts.macos", "Alert channel: macOS notification",
         "this Mac (osascript, notification centre)", "an alert title + body, shown on this machine",
         "none needed: nothing leaves the machine", "[alerts.macos] enabled = true or [notify] macos = true",
         "set them to false", external=False, local_only="allowed", offline="allowed",
         purposes=("alert",), host="localhost", mode="none"),
    Flow("mcp.finance", "The finance MCP server (stdio) read by an interactive Claude Code session",
         "the MODEL behind the MCP client (Anthropic, when the client is Claude Code); the server itself only speaks stdio",
         "REDACTED, pseudonymised tool results (figures computed by code, `h_` refs, generalised merchant titles, evidence refs)",
         "analytics-pseudonym: coach.analytics.privacy + coach.mcp.guard (untrusted_text wrapping, person guard, privacy mode "
         "[privacy] model_detail)", "you start `claude` in this repository with the project's .mcp.json approved",
         "do not approve the server in `/mcp`; [privacy] local_only = true makes `coach mcp serve` refuse to start",
         local_only="refused", offline="refused", purposes=("stdio-session",), host="(MCP client)", mode="analytics-pseudonym"),
    Flow("ui.sso", "Web app sign-in through an identity-aware proxy (E16): the provider's public keys",
         "the JWKS URL of [ui] sso_jwks_url (your own authentik, usually on the same machine)",
         "nothing of the household: an HTTP GET of the provider's PUBLIC signing keys, at most once an hour and once a minute on an unknown key id; "
         "the person's token comes IN with the request and is verified locally",
         "n/a: the request carries no household data and no token",
         "[ui] sso = \"authentik\" (OFF by default) with sso_jwks_url and a [ui.sso_users] table",
         "[ui] sso = \"none\"; [privacy] offline = true refuses it (the one-time login link still works)",
         local_only="allowed", offline="refused", purposes=("jwks",), host="(identity provider)", mode="none"),
    Flow("skill.web_search", "Web search by the interactive skills find-cheaper / mortgage-check (Claude Code WebSearch)",
         "the search engine behind Claude Code's WebSearch tool",
         "generic, non-personal queries written by the skill (a service type, a country, a rate): never a name, address, account or "
         "identifying amount (rule of the skills and of CLAUDE.md)",
         "by rule: generic queries only", "you run those skills interactively",
         "[privacy] local_only = true: the skills read `coach privacy status` first and refuse; or do not run them",
         local_only="refused", offline="refused", purposes=("find-cheaper", "mortgage-check"), host="(web search)", mode="generic-query"),
)

KINDS = {f.kind: f for f in INVENTORY}
ENFORCED = tuple(k for k in KINDS if k != "skill.web_search")     # called through allow(); the skill path is a rule + `privacy status`


# ---------------------------------------------------------------- the call-site registry (checked by tests/test_egress_coverage.py)
# Every source file that references a network / subprocess / browser API must be listed, with WHY it is acceptable. A function that
# reaches the outside must call egress.allow() itself, or be listed in EXEMPT_FUNCTIONS with the reason.

# file (relative to src/coach) -> (kinds it can reach, one-line description)
CALL_SITES: dict[str, tuple[tuple[str, ...], str]] = {
    "ingest/client.py": (("enable_banking",), "Enable Banking REST client (requests)"),
    "classify/backends.py": (("llm.claude-code", "llm.anthropic-api", "llm.ollama", "llm.openai-compatible"),
                             "LLM backends of classify run/enrich/compare and memory doc extract"),
    "agent/runner.py": (("llm.claude-code", "llm.anthropic-api", "llm.ollama", "llm.openai-compatible"), "the coach runtime (ask, digests, skills)"),
    "alerts/channels.py": (("alerts.ntfy", "alerts.email", "alerts.telegram", "alerts.macos"), "alert channels"),
    "classify/llm.py": ((), "re-exports `subprocess` for tests; no call"),
    "notify.py": (("alerts.macos",), "osascript notification (this machine)"),
    "schedule.py": ((), "launchctl / `which claude` (this machine)"),
    "memory/history.py": ((), "git of memory/ (this machine, no remote)"),
    "ingest/auth.py": ((), "opens the user's browser at the bank's consent page; the callback is a local server"),
    "ingest/callback.py": ((), "local HTTPS callback server (listens on loopback; no outbound call)"),
    "api/server.py": ((), "web app: uvicorn on loopback; opens the user's browser"),
    "api/sso.py": (("ui.sso",), "web app SSO: fetches the identity provider's public keys (JWKS) to verify the proxy's signed token"),
    "api/coachjobs.py": ((), "checks that the `claude` binary exists (shutil.which); the run goes through agent/runner.py"),
    "security.py": ((), "audit: read-only `lsof` listing of listening sockets (this machine)"),
    "setup/doctor.py": ((), "`coach doctor`: shutil.which('claude') only (is the CLI installed?); nothing is run"),
    "release.py": ((), "maintainer tool `coach dev release-check`: runs pytest / ruff / pnpm in the checkout (subprocess); no network, no data leaves"),
}

# (file, function qualname) -> why it may reach the outside without calling allow() itself
EXEMPT_FUNCTIONS: dict[tuple[str, str], str] = {
    ("alerts/channels.py", "_default_http_post"): "the real HTTP transport: send() calls egress.allow() before using it",
    ("alerts/channels.py", "_default_smtp"): "the real SMTP transport: send() calls egress.allow() before using it",
    ("alerts/channels.py", "_default_run"): "the real subprocess runner of the macOS notification: send() gates it",
    ("alerts/channels.py", "NoRedirect"): "urllib redirect handler class (refuses every redirect); no call",
    ("notify.py", "_default_run"): "osascript on this machine; the alerts.macos channel gates it, `[notify] macos` is local",
    ("notify.py", "notify_macos"): "local macOS notification (no network)",
    ("schedule.py", "_path_env"): "shutil.which('claude') only",
    ("setup/doctor.py", "check_claude_cli"): "shutil.which('claude') only",
    ("release.py", "default_runner"): "local test / lint tools of the maintainer's checkout; no network, no data leaves",
    ("schedule.py", "install"): "launchctl on this machine",
    ("schedule.py", "uninstall"): "launchctl on this machine",
    ("schedule.py", "status"): "launchctl on this machine",
    ("memory/history.py", "MemoryRepo.git"): "local git, no remote is ever configured",
    ("ingest/auth.py", "connect_flow"): "opens the user's browser (webbrowser.open) at the bank's consent page: the app itself sends nothing",
    ("ingest/callback.py", "*"): "local HTTPS callback server: binds loopback only, no outbound connection",
    ("api/server.py", "cmd_ui"): "uvicorn on loopback + opens the user's browser",
    ("api/coachjobs.py", "availability"): "shutil.which('claude') only; the run goes through agent/runner.py",
    ("agent/runner.py", "claude_command"): "builds the argv of `claude -p`; run_claude_code gates the run",
    ("classify/backends.py", "claude_classify_command"): "builds the argv of the classification `claude -p`; ClaudeCodeBackend.complete gates the run",
    ("agent/runner.py", "_kill"): "terminates the child process of an already gated run",
    ("security.py", "default_lsof"): "read-only `lsof -nP -iTCP -sTCP:LISTEN` on this machine",
}


# ---------------------------------------------------------------- policy

@dataclass
class Policy:
    local_only: bool = False
    offline: bool = False
    web_enrich: bool = False
    journal: bool = True
    cfg: object = None
    insecure: bool = False
    ollama_allow_remote: bool = False
    deny_all: bool = False                 # no policy could be established: nothing outbound is allowed (fail closed)

    @classmethod
    def from_cfg(cls, cfg, insecure: bool = False) -> "Policy":
        offline = bool(getattr(cfg, "privacy_offline", False))
        return cls(local_only=bool(getattr(cfg, "privacy_local_only", False)) or offline, offline=offline,
                   web_enrich=bool(getattr(cfg, "privacy_web_enrich", False)),
                   journal=bool(getattr(cfg, "privacy_egress_journal", True)), cfg=cfg,
                   insecure=insecure or bool(getattr(cfg, "insecure_plaintext_db", False)),
                   ollama_allow_remote=bool(getattr(cfg, "llm_ollama_allow_remote", False)))

    @property
    def mode(self) -> str:
        return "offline" if self.offline else "local_only" if self.local_only else "standard"


_ACTIVE: Optional[Policy] = None
_LAZY: Optional[Policy] = None


def activate(cfg, insecure: bool = False) -> Policy:
    """Make `cfg`'s [privacy] settings the process-wide policy (the CLI does this once, right after loading the configuration)."""
    global _ACTIVE
    _ACTIVE = Policy.from_cfg(cfg, insecure)
    return _ACTIVE


def deactivate() -> None:
    global _ACTIVE, _LAZY
    _ACTIVE = None
    _LAZY = None


def active() -> Optional[Policy]:
    return _ACTIVE


def policy_of(cfg=None) -> Policy:
    if cfg is not None:
        if _ACTIVE is not None and _ACTIVE.cfg is cfg:
            return _ACTIVE
        return Policy.from_cfg(cfg)
    if _ACTIVE is not None:
        return _ACTIVE
    return _lazy_policy()


def _lazy_policy() -> Policy:
    """No policy was activated (a call site reached without a configuration, a library use): the configuration is LOADED (config.toml,
    $COACH_CONFIG) and its [privacy] settings apply. If it cannot be loaded no policy exists and everything outbound is refused: the gate
    never fails open."""
    global _LAZY
    if _LAZY is None:
        try:
            from coach.config import load_config
            _LAZY = Policy.from_cfg(load_config())
        except Exception:                                                          # noqa: BLE001
            _LAZY = Policy(deny_all=True, journal=False)
    return _LAZY


def _loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def host_of(url: Optional[str]) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def evaluate(kind: str, meta: Optional[dict] = None, cfg=None, policy: Optional[Policy] = None) -> tuple[bool, str, str]:
    """(allowed, code, message): the decision without side effects (no journal). `meta` may carry ``host``, ``web_search``, ``purpose``."""
    meta = meta or {}
    pol = policy or policy_of(cfg)
    flow = KINDS.get(kind)
    if flow is None:
        return False, "unregistered", f"egress kind {kind!r} is not registered in coach.egress.INVENTORY"
    if pol.deny_all:
        return False, "no_policy", "no privacy policy could be established (the configuration could not be loaded): outbound calls are refused"
    if pol.offline and flow.offline == "refused":
        return False, "offline", (f"[privacy] offline = true: {flow.title} is disabled (nothing leaves this machine; bank data comes "
                                  "from file imports only). Set offline = false to allow it.")
    if pol.local_only and meta.get("web_search"):
        return False, "local_only", "[privacy] local_only = true: web search is disabled."
    if pol.local_only:
        if flow.local_only == "refused":
            extra = " Set [llm] backend / [coach] backend to \"ollama\"." if kind.startswith("llm.") else ""
            return False, "local_only", (f"[privacy] local_only = true: {flow.title} is refused.{extra} "
                                         "Set local_only = false to allow it.")
        if flow.local_only == "loopback only":
            host = meta.get("host") or ""
            if host and not _loopback(host):
                return False, "non_loopback", (f"[privacy] local_only = true: the model server {host!r} is not on this machine "
                                               "(loopback only; [llm] ollama_allow_remote does not apply in local mode).")
    if meta.get("web_search"):
        if pol.local_only:
            return False, "local_only", "[privacy] local_only = true: web search is disabled."
        if not pol.web_enrich:
            return False, "web_enrich_off", ("web enrichment sends merchant descriptors to a web search: it is OFF unless you set "
                                              "[privacy] web_enrich = true in config.toml.")
    return True, "ok", ""


# ---------------------------------------------------------------- the journal

_lock = threading.Lock()
_pending: list[tuple] = []
MAX_PENDING = 500

JOURNAL_SQL = """INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web)
                 VALUES (?,?,?,?,?,?,?,?,?)"""


def _now() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _flush(pol: Policy) -> None:
    """Write the pending rows through a short-lived connection. Never raises: a journal that cannot be written must not break a
    sync; the rows stay pending (and are retried at the next call and at exit)."""
    cfg = pol.cfg
    if cfg is None or not _pending:
        return
    with _lock:
        rows = list(_pending)
    try:
        from coach import db as db_mod
        if not cfg.db_path.exists():
            return
        con = db_mod.connect(cfg, insecure=pol.insecure, migrate=False)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='egress_journal'").fetchone():
                return                                   # migration 0019 not applied yet (auto_migrate off)
            con.executemany(JOURNAL_SQL, rows)
            con.commit()
        finally:
            con.close()
    except Exception:                                                              # noqa: BLE001
        return
    with _lock:
        for r in rows:
            try:
                _pending.remove(r)
            except ValueError:
                pass


def flush() -> None:
    if _ACTIVE is not None:
        _flush(_ACTIVE)


atexit.register(flush)


def _record(pol: Policy, kind: str, meta: dict, outcome: str, reason: str) -> None:
    if not pol.journal or pol.cfg is None:
        return
    flow = KINDS.get(kind)
    host = (meta.get("host") or (flow.host if flow else "") or "")[:120]
    row = (_now(), kind, host, int(meta.get("bytes") or 0), str(meta.get("purpose") or "")[:60],
           str(meta.get("redaction") or (flow.mode if flow else ""))[:40], outcome, reason[:40], int(bool(meta.get("web_search"))))
    with _lock:
        _pending.append(row)
        del _pending[:-MAX_PENDING]
    _flush(pol)


def allow(kind: str, meta: Optional[dict] = None, cfg=None) -> None:
    """THE egress check. Call it right before an outbound call (network, LLM subprocess, SMTP ...). Raises :class:`EgressDenied` when
    the privacy settings refuse the path; otherwise journals the attempt and returns.

    `meta` (all optional, never a payload): ``host`` destination host, ``bytes`` size of what is sent, ``purpose`` (e.g.
    ``classify.label``), ``redaction`` mode, ``web_search`` True when the call lets the model search the web.
    """
    meta = dict(meta or {})
    pol = policy_of(cfg)
    ok, code, msg = evaluate(kind, meta, policy=pol)
    _record(pol, kind, meta, "allowed" if ok else "denied", "" if ok else code)
    if not ok:
        raise EgressDenied(kind, code, msg)


def require(kind: str, meta: Optional[dict] = None, cfg=None) -> None:
    """Early, readable refusal (a configured backend, a command that needs a path): raises :class:`EgressDenied` and journals the REFUSAL;
    when the path is allowed it does nothing and journals nothing (the real call journals itself through :func:`allow`)."""
    meta = dict(meta or {})
    pol = policy_of(cfg)
    ok, code, msg = evaluate(kind, meta, policy=pol)
    if not ok:
        _record(pol, kind, meta, "denied", code)
        raise EgressDenied(kind, code, msg)


# ---------------------------------------------------------------- reports

def status(cfg) -> dict:
    """The effective privacy mode and what each path may do now (`coach privacy status`)."""
    pol = policy_of(cfg)
    llm = {"classify": getattr(cfg, "llm_backend", "claude-code"), "coach": getattr(cfg, "coach_backend", "claude-code")}
    paths = {}
    for k in ENFORCED:
        meta = {"host": host_of(getattr(cfg, "llm_ollama_url", "")) if k == "llm.ollama" else
                host_of(getattr(cfg, "llm_openai_base_url", "")) if k == "llm.openai-compatible" else ""}
        ok, code, msg = evaluate(k, meta, policy=pol)
        paths[k] = {"allowed": ok, "code": code}
    web_ok, web_code, _ = evaluate("llm.claude-code", {"web_search": True}, policy=pol)
    skills_ok = not pol.local_only
    bad = [(n, b) for n, b in llm.items() if pol.local_only and b != "ollama"]
    alerts = cfg.alert_settings if hasattr(cfg, "alert_settings") else None
    return {"mode": pol.mode, "local_only": pol.local_only, "offline": pol.offline, "web_enrich": pol.web_enrich,
            "bank_sync": paths["enable_banking"]["allowed"], "web_search": web_ok and True,
            "web_search_reason": "" if web_ok else web_code,
            "web_search_skills": skills_ok,
            "external_alert_channels": all(paths[f"alerts.{c}"]["allowed"] for c in ("ntfy", "email", "telegram")),
            "llm_backends": llm, "llm_misconfigured": [{"purpose": n, "backend": b} for n, b in bad],
            "paths": paths, "journal": pol.journal,
            "alert_channels_enabled": [n for n in ("ntfy", "email", "telegram") if alerts and alerts.channel(n).enabled]}


def journal_summary(con, days: int = 30) -> dict:
    """Aggregates of the last `days` days of the journal: rows per (kind, host, purpose), totals, denials."""
    import datetime as dt
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    try:
        rows = con.execute("""SELECT kind, host, purpose, COUNT(*), COALESCE(SUM(bytes),0), SUM(outcome='denied'), MAX(ts), MAX(web)
                              FROM egress_journal WHERE ts >= ? GROUP BY kind, host, purpose ORDER BY kind, host, purpose""",
                           (since,)).fetchall()
    except Exception:                                                              # noqa: BLE001  (table missing: migrations pending)
        return {"days": days, "rows": [], "total": 0, "bytes": 0, "denied": 0, "available": False}
    out = [{"kind": r[0], "host": r[1], "purpose": r[2], "count": r[3], "bytes": r[4], "denied": int(r[5] or 0), "last": r[6],
            "web": bool(r[7])} for r in rows]
    return {"days": days, "rows": out, "total": sum(r["count"] for r in out), "bytes": sum(r["bytes"] for r in out),
            "denied": sum(r["denied"] for r in out), "available": True}


def inventory_rows() -> list[dict]:
    return [{"kind": f.kind, "title": f.title, "destination": f.destination, "data": f.data, "redaction": f.redaction,
             "opt_in": f.opt_in, "disable": f.disable, "external": f.external, "local_only": f.local_only,
             "offline": f.offline, "purposes": list(f.purposes)} for f in INVENTORY]


def prune_journal(con, keep_days: int = 365) -> int:
    import datetime as dt
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)).isoformat(timespec="seconds")
    cur = con.execute("DELETE FROM egress_journal WHERE ts < ?", (cut,))
    con.commit()
    return cur.rowcount


def warn(msg: str) -> None:
    print(f"coach: {msg}", file=sys.stderr)
