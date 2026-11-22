"""The environment an `agy` subprocess is started with, shared by the coach runtime (:mod:`coach.agent.runner`) and the
classification backend (:class:`coach.classify.backends.AntiGravityBackend`).

A minimal, secure environment: no COACH_* secrets (COACH_DB_KEY, COACH_BACKUP_KEY ...).
macOS authentication or local config uses HOME / USER / PATH.
Invalid SSL_CERT_FILE (e.g. from deleted pyenv) is cleaned to prevent TLS failures.
"""
from __future__ import annotations

import os

SECRET_ENV = ("COACH_DB_KEY", "COACH_BACKUP_KEY", "COACH_PROPOSAL_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "SHELL", "__CF_USER_TEXT_ENCODING",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "no_proxy")


def antigravity_env(cfg=None) -> dict[str, str]:
    """The sanitized environment for `agy`: standard allowlist, LC_* locale variables, and any AGY_* variables."""
    env = {k: os.environ[k] for k in ENV_ALLOW if os.environ.get(k)}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})
    env.update({k: v for k, v in os.environ.items() if k.startswith("AGY_") or k.startswith("GEMINI_")})

    # Sanitize SSL_CERT_FILE and REQUESTS_CA_BUNDLE if pointing to a non-existent path
    if "SSL_CERT_FILE" in env and not os.path.exists(env["SSL_CERT_FILE"]):
        del env["SSL_CERT_FILE"]
    if "REQUESTS_CA_BUNDLE" in env and not os.path.exists(env["REQUESTS_CA_BUNDLE"]):
        del env["REQUESTS_CA_BUNDLE"]

    # Ensure no secret keys leak into the subprocess
    for s in SECRET_ENV:
        env.pop(s, None)

    return env
