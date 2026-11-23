"""The environment and CLI arguments for `agy` subprocesses, shared by the coach runtime
(:mod:`coach.agent.runner`) and the classification backend (:class:`coach.classify.backends.AntiGravityBackend`).

A minimal, secure execution model:
- Strict environment allowlist: standard system variables and strictly necessary AGY / GEMINI keys.
- Never any COACH_* secrets (COACH_DB_KEY, COACH_BACKUP_KEY, etc.) or unrelated API keys.
- Clean isolation: no dangerous flags (--dangerously-skip-permissions is forbidden), isolated sandbox mode,
  slash command / skill expansion disabled (--disable-slash-commands), and non-existent CA bundle paths sanitized.
"""
from __future__ import annotations

import os

SECRET_ENV = ("COACH_DB_KEY", "COACH_BACKUP_KEY", "COACH_PROPOSAL_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
              "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "SHELL", "__CF_USER_TEXT_ENCODING",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "no_proxy")

# Strictly allowed AGY / GEMINI environment variable names
AGY_ALLOWED_VARS = (
    "GEMINI_API_KEY",
    "AGY_API_KEY",
    "AGY_ACCOUNT",
    "AGY_CLI_AUTO_COMPACT_OVERRIDE",
    "AGY_CLI_DISABLE_LATEX",
    "AGY_CLI_HIDE_ACCOUNT_INFO",
)

# Isolation arguments passed to every agy execution:
# - `--disable-slash-commands`: Prevents expansion of user skills, slash commands, or prompt hooks.
# - `--sandbox`: Runs in an isolated sandbox with terminal execution restrictions enabled.
ISOLATION_ARGS = ("--disable-slash-commands", "--sandbox")


def antigravity_env(cfg=None) -> dict[str, str]:
    """The sanitized environment for `agy`: standard allowlist, LC_* locale variables, and strictly needed keys."""
    env = {k: os.environ[k] for k in ENV_ALLOW if os.environ.get(k)}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})

    # Forward strictly allowed AGY / GEMINI keys if present in the environment
    for k in AGY_ALLOWED_VARS:
        if k in os.environ:
            env[k] = os.environ[k]

    # Optional extra environment variable names configured in [coach] antigravity_env (if any)
    for k in getattr(cfg, "coach_antigravity_env", ()) or ():
        if k in os.environ and k not in SECRET_ENV:
            env[k] = os.environ[k]

    # Sanitize SSL_CERT_FILE and REQUESTS_CA_BUNDLE if pointing to a non-existent path
    if "SSL_CERT_FILE" in env and not os.path.exists(env["SSL_CERT_FILE"]):
        del env["SSL_CERT_FILE"]
    if "REQUESTS_CA_BUNDLE" in env and not os.path.exists(env["REQUESTS_CA_BUNDLE"]):
        del env["REQUESTS_CA_BUNDLE"]

    # Ensure no secret keys leak into the subprocess
    for s in SECRET_ENV:
        env.pop(s, None)

    return env

