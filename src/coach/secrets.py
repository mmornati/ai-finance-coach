"""Secret lookup: environment variable -> secret store -> helpful error.

The secret store is the macOS Keychain (via ``keyring``, the default) or, where there is no Keychain (a container, a Linux server),
a folder of files: ``COACH_SECRETS_BACKEND=file`` with ``COACH_SECRETS_DIR`` (default ``/run/secrets``, where Docker mounts its secrets).
One file per secret, named like the secret (``db_key``), holding only the value, mode 0600 (E13-1).

Secrets are never read from config.toml and never printed (see ``describe`` for masked status).
"""
from __future__ import annotations

import os
import secrets as _stdlib_secrets
import stat
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "ai-finance-coach"

# secret name -> environment variable that overrides the Keychain
SECRETS: dict[str, str] = {
    "db_key": "COACH_DB_KEY",
    "backup_key": "COACH_BACKUP_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",  # optional, for llm.backend = anthropic-api
    # optional, for llm.backend = openai-compatible (OpenRouter, Eden AI ...). Deliberately NOT OPENAI_API_KEY: an unrelated OpenAI key in
    # the environment must never be sent to a third-party provider by accident.
    "openai_api_key": "COACH_OPENAI_API_KEY",
    # optional, for the claude-code backend where no interactive login exists (a container): a long-lived token from `claude setup-token`
    "claude_code_oauth_token": "CLAUDE_CODE_OAUTH_TOKEN",
    "proposal_key": "COACH_PROPOSAL_KEY",      # optional: HMAC key that seals memory proposals against tampering
    "ntfy_token": "COACH_NTFY_TOKEN",          # optional: access token of a protected ntfy topic ([alerts.ntfy])
    "smtp_password": "COACH_SMTP_PASSWORD",    # needed only when [alerts.email] is enabled
    "telegram_bot_token": "COACH_TELEGRAM_BOT_TOKEN",   # needed only when [alerts.telegram] is enabled
}
OPTIONAL = {"anthropic_api_key", "openai_api_key", "claude_code_oauth_token", "proposal_key", "ntfy_token", "smtp_password", "telegram_bot_token"}


BACKENDS = ("keychain", "file")
BACKEND_VAR = "COACH_SECRETS_BACKEND"
DIR_VAR = "COACH_SECRETS_DIR"
LOOSE_VAR = "COACH_SECRETS_ALLOW_READABLE"       # file backend: accept group / other READABLE files (Docker swarm secrets are 0444)
DEFAULT_DIR = "/run/secrets"


class SecretNotFound(Exception):
    pass


class SecretBackendError(Exception):
    """The Keychain backend itself failed (locked, unavailable, denied), as opposed to 'not found'."""


def _check(name: str) -> str:
    if name not in SECRETS:
        raise KeyError(f"Unknown secret {name!r}; known: {', '.join(SECRETS)}")
    return SECRETS[name]


def backend(env=None) -> str:
    """The secret store in use: 'keychain' (default) or 'file' (``COACH_SECRETS_BACKEND=file``). Anything else is an error."""
    env = os.environ if env is None else env
    b = (env.get(BACKEND_VAR) or "keychain").strip().lower()
    if b not in BACKENDS:
        raise SecretBackendError(f"{BACKEND_VAR} must be one of {', '.join(BACKENDS)}, got {b!r}")
    return b


def secrets_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get(DIR_VAR) or DEFAULT_DIR).expanduser()


def store_label(env=None) -> str:
    """Human wording of the store, for messages."""
    return f"the secrets folder {secrets_dir(env)}" if backend(env) == "file" else "the macOS Keychain"


def _file_for(name: str, env=None) -> Path:
    _check(name)
    return secrets_dir(env) / name


def _stat(p: Path):
    return p.stat()                       # follows a symlink (Kubernetes mounts secrets that way); a seam for tests


def _owner_ok(st) -> bool:
    """Owned by this user or by root (Docker / compose mount /run/secrets root-owned)."""
    return not hasattr(os, "getuid") or st.st_uid in (os.getuid(), 0)


def _check_dir(d: Path, env=None) -> None:
    """The secrets folder must be owned by this user or by root and must not be writable by group / others (a sticky bit, as on a Kubernetes tmpfs, aside).
    Reading it may be open to others: compose mounts /run/secrets root-owned, mode 0755, for a container user that is not root."""
    try:
        st = _stat(d)
    except FileNotFoundError:
        return
    except OSError as e:
        raise SecretBackendError(f"Cannot inspect the secrets folder {d} ({type(e).__name__}).") from e
    if not stat.S_ISDIR(st.st_mode):
        raise SecretBackendError(f"{d} is not a folder.")
    mode = stat.S_IMODE(st.st_mode)
    if (mode & 0o022) and not (mode & stat.S_ISVTX):
        raise SecretBackendError(f"The secrets folder {d} is writable by others (mode {mode:04o}): run `chmod 755 {d}` or tighter.")
    if not _owner_ok(st):
        raise SecretBackendError(f"The secrets folder {d} is owned by another user (not you, not root): refused.")


def _read_file(name: str, env=None) -> str | None:
    d = secrets_dir(env)
    _check_dir(d, env)
    p = _file_for(name, env)
    try:
        st = _stat(p)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SecretBackendError(f"Cannot read secret '{name}' from {p} ({type(e).__name__}).") from e
    if not stat.S_ISREG(st.st_mode):
        raise SecretBackendError(f"{p} is not a regular file.")
    if not _owner_ok(st):
        raise SecretBackendError(f"{p} is owned by another user (not you, not root): refused.")
    env_ = os.environ if env is None else env
    bad = 0o022 if env_.get(LOOSE_VAR) == "1" else 0o077
    if st.st_mode & bad:
        raise SecretBackendError(
            f"{p} is accessible to other users (mode {stat.S_IMODE(st.st_mode):04o}): run `chmod 600 {p}`"
            + ("" if env_.get(LOOSE_VAR) == "1" else f" (a root-owned secret mounted by Docker / Kubernetes is read-only: set {LOOSE_VAR}=1, or give it mode 0400 and the container user's uid in the compose `secrets:` long syntax)."))
    try:
        value = p.read_text().strip()
    except OSError as e:
        raise SecretBackendError(f"Cannot read secret '{name}' from {p} ({type(e).__name__}).") from e
    return value or None


def _write_file(name: str, value: str, env=None) -> Path:
    """Create the secret atomically: a random temporary name opened O_CREAT|O_EXCL|O_NOFOLLOW (never through a planted symlink), mode 0600,
    flushed to disk, then renamed over the old file."""
    d = secrets_dir(env)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    _check_dir(d, env)
    p = d / name
    tmp = d / f".{name}.{_stdlib_secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return p


def lookup(name: str, env=None) -> tuple[str | None, str | None]:
    """Return (value, origin) with origin 'env', 'file' or 'keychain'; (None, None) if absent."""
    env = os.environ if env is None else env
    var = _check(name)
    if env.get(var):
        return env[var], "env"
    if backend(env) == "file":
        v = _read_file(name, env)
        return (v, "file") if v else (None, None)
    try:
        v = keyring.get_password(SERVICE, name)
    except KeyringError as e:
        raise SecretBackendError(
            f"Cannot read secret '{name}' from the Keychain ({type(e).__name__}: {e}). "
            f"Unlock/allow the Keychain, or provide it as the environment variable {var}.") from e
    return (v, "keychain") if v else (None, None)


def get_secret(name: str, required: bool = True, env=None) -> str | None:
    value, _ = lookup(name, env)
    if value:
        return value
    if not required:
        return None
    var = SECRETS[name]
    if backend() == "file":
        raise SecretNotFound(
            f"Secret '{name}' not found. Provide it either as the environment variable {var} or as the file "
            f"{_file_for(name)} (mode 0600, the value only): `coach config set-secret {name}` (add --generate to create a random one).")
    raise SecretNotFound(
        f"Secret '{name}' not found. Provide it either as the environment variable {var} or in the "
        f"macOS Keychain (service '{SERVICE}'): `uv run coach config set-secret {name}` "
        f"(add --generate to create a random one).", name)


def set_secret(name: str, value: str) -> None:
    _check(name)
    if backend() == "file":
        try:
            _write_file(name, value)
        except OSError as e:
            raise SecretBackendError(f"Cannot store '{name}' in {secrets_dir()} ({type(e).__name__}: {e})") from e
        return
    try:
        keyring.set_password(SERVICE, name, value)
    except KeyringError as e:
        raise SecretBackendError(f"Cannot store '{name}' in the Keychain ({type(e).__name__}: {e})") from e


def generate() -> str:
    return _stdlib_secrets.token_urlsafe(32)


def delete_secret(name: str) -> None:
    _check(name)
    if backend() == "file":
        try:
            _file_for(name).unlink()
        except FileNotFoundError:
            pass
        return
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def describe(env=None) -> list[tuple[str, str]]:
    """(name, masked status) for each known secret: never the value."""
    out = []
    for name in SECRETS:
        try:
            value, origin = lookup(name, env)
        except SecretBackendError:
            out.append((name, "UNKNOWN: secret store unavailable"))
            continue
        if value:
            out.append((name, f"set ({origin})  ********"))
        else:
            out.append((name, "not set" + (" (optional)" if name in OPTIONAL else "")))
    return out


def in_keychain(name: str) -> bool:
    """True if the secret is in the persistent secret store: the Keychain, or the secrets folder with the file backend. It ignores
    environment variables (the name is historical)."""
    _check(name)
    if backend() == "file":
        return _read_file(name) is not None
    try:
        return bool(keyring.get_password(SERVICE, name))
    except KeyringError as e:
        raise SecretBackendError(f"Cannot read '{name}' from the Keychain ({type(e).__name__}: {e})") from e


def claude_auth_env() -> dict:
    """{"CLAUDE_CODE_OAUTH_TOKEN": ...} for the `claude` process when the secret claude_code_oauth_token is set (`claude setup-token`; a container
    has no interactive login), else {}: the CLI then uses its own login (the macOS Keychain on a Mac)."""
    try:
        token = get_secret("claude_code_oauth_token", required=False)
    except SecretBackendError:
        token = None
    return {"CLAUDE_CODE_OAUTH_TOKEN": token} if token else {}
