"""The environment a `claude` subprocess is started with, shared by the coach runtime (:mod:`coach.agent.runner`) and the
classification backend (:class:`coach.classify.backends.ClaudeCodeBackend`).

A MINIMAL allowlist: no proxy variable, no ANTHROPIC_BASE_URL, no API key, no COACH_* secret. macOS authentication of a
subscription login reads the Keychain through HOME / USER; extra names (a proxy, a config dir, CLAUDE_CODE_OAUTH_TOKEN ...) are
passed only when listed in [coach] claude_env, and a name in SECRET_ENV never is.
"""
from __future__ import annotations

import os

SECRET_ENV = ("ANTHROPIC_API_KEY", "COACH_DB_KEY", "COACH_BACKUP_KEY", "COACH_PROPOSAL_KEY", "ANTHROPIC_AUTH_TOKEN")
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "SHELL", "__CF_USER_TEXT_ENCODING")


def claude_env(cfg) -> dict:
    """The environment for `claude`: the allowlist, the LC_* locale names and the [coach] claude_env extras (``cfg`` may be None)."""
    env = {k: os.environ[k] for k in ENV_ALLOW if os.environ.get(k)}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})
    for k in getattr(cfg, "coach_claude_env", ()) or ():
        if k in os.environ and k not in SECRET_ENV:
            env[k] = os.environ[k]
    return env
