"""``coach doctor``: is this installation ready, and what to do next (E13-1). Read-only: it opens nothing for writing, calls no network, and never prints a secret.

Checks: Python and the SQLCipher wheel, the configuration, the secret store (Keychain or the ``file`` backend) and the three secrets, the database,
the Enable Banking settings and key file, file permissions, the built web app, the ``claude`` command (only when a backend needs it), ``git``.
Each check is ``ok`` / ``info`` / ``warn`` / ``fail``; the exit code is 1 when any is ``fail``.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from coach import __version__, db as db_mod, secrets

MIN_PYTHON = (3, 11)
ORDER = {"fail": 0, "warn": 1, "info": 2, "ok": 3}


@dataclass
class Check:
    id: str
    level: str          # ok | info | warn | fail
    title: str
    detail: str = ""
    hint: str = ""      # the next step when the level is warn / fail

    def to_dict(self) -> dict:
        return asdict(self)


def in_container() -> bool:
    return bool(os.environ.get("COACH_IN_CONTAINER")) or Path("/.dockerenv").exists()


def _mode(path: Path) -> Optional[int]:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def check_python() -> Check:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PYTHON:
        return Check("python", "fail", "Python", f"{v.major}.{v.minor} is too old", f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer (uv does it for you: `uv tool install ai-finance-coach`)")
    return Check("python", "ok", "Python", f"{v.major}.{v.minor}.{v.micro}, coach {__version__}")


def check_sqlcipher() -> Check:
    try:
        import sqlcipher3
        con = sqlcipher3.connect(":memory:")
        con.execute("PRAGMA key = 'doctor'")
        row = con.execute("PRAGMA cipher_version").fetchone()
        con.close()
    except Exception as e:                                                     # noqa: BLE001
        return Check("sqlcipher", "fail", "SQLCipher wheel", f"{type(e).__name__}", "reinstall: the sqlcipher3-wheels package ships SQLCipher for macOS arm64 and Linux; no system library is needed")
    if not row or not row[0]:
        return Check("sqlcipher", "fail", "SQLCipher wheel", "the sqlite library has no SQLCipher support", "install sqlcipher3-wheels (not plain sqlite3)")
    return Check("sqlcipher", "ok", "SQLCipher wheel", f"cipher {row[0]}")


def check_config(cfg) -> Check:
    if cfg.config_path:
        mode = _mode(cfg.config_path)
        if mode is not None and mode & 0o077:
            return Check("config", "warn", "Configuration", f"{cfg.config_path} is readable by other users (mode {mode:04o})", f"chmod 600 {cfg.config_path}")
        return Check("config", "ok", "Configuration", str(cfg.config_path))
    return Check("config", "fail", "Configuration", f"no config.toml found (home: {cfg.root})", "run `coach init`")


def check_secret_store(env=None) -> list[Check]:
    out = []
    try:
        b = secrets.backend(env)
    except secrets.SecretBackendError as e:
        return [Check("secrets.backend", "fail", "Secret store", str(e), "set COACH_SECRETS_BACKEND to keychain or file")]
    if b == "file":
        d = secrets.secrets_dir(env)
        if not d.is_dir():
            out.append(Check("secrets.backend", "fail", "Secret store", f"file backend, but {d} is not a folder", f"create it (mode 0700) and put the secrets in it, or run `coach init --secrets-only --secrets-backend file --secrets-dir {d}`"))
        else:
            mode = _mode(d)
            try:
                uid = d.stat().st_uid
            except OSError:
                uid = -1
            # root-owned and not writable by group / others is the documented compose layout (/run/secrets, 0755): fine. Writable by others is not.
            writable = mode is not None and bool(mode & 0o022) and not (mode & stat.S_ISVTX)
            foreign = hasattr(os, "getuid") and uid not in (os.getuid(), 0)
            lvl = "warn" if (writable or foreign) else "ok"
            out.append(Check("secrets.backend", lvl, "Secret store", f"file backend: {d} (mode {mode:04o})" if mode is not None else f"file backend: {d}",
                             f"chmod 755 {d} or tighter (it must not be writable by others, and owned by you or root)" if lvl == "warn" else ""))
    else:
        out.append(Check("secrets.backend", "ok" if sys.platform == "darwin" else "warn", "Secret store",
                         "macOS Keychain" if sys.platform == "darwin" else "Keychain backend on a non-macOS system",
                         "" if sys.platform == "darwin" else "set COACH_SECRETS_BACKEND=file and COACH_SECRETS_DIR (see docs/docker.md) or provide the secrets as environment variables"))
    for name, why, required in (("db_key", "database encryption", True), ("backup_key", "backup encryption", True), ("proposal_key", "memory proposal seal", False)):
        try:
            value, origin = secrets.lookup(name, env)
        except secrets.SecretBackendError as e:
            out.append(Check(f"secret.{name}", "fail", f"Secret {name}", str(e), "fix the secret store, then run `coach doctor` again"))
            continue
        if value:
            out.append(Check(f"secret.{name}", "ok", f"Secret {name}", f"set ({origin}), {why}"))
        else:
            out.append(Check(f"secret.{name}", "fail" if required else "warn", f"Secret {name}", f"not set ({why})",
                             "run `coach init` (it generates it after a typed confirmation), or `coach config set-secret " + name + " --generate`"))
    return out


def check_database(cfg) -> Check:
    path = cfg.db_path
    state = db_mod.is_plaintext(path)
    if state is None:
        return Check("database", "fail", "Database", f"{path} does not exist", "run `coach init` (or `coach db migrate --create`)")
    if state is True and not (cfg.insecure_plaintext_db):
        return Check("database", "fail", "Database", f"{path} is NOT encrypted", "run `coach db encrypt`")
    try:
        con = db_mod.connect(cfg, insecure=bool(cfg.insecure_plaintext_db), migrate=False)
        st = db_mod.status(con)
        con.close()
    except (secrets.SecretNotFound, secrets.SecretBackendError, db_mod.WrongKeyError, db_mod.PlaintextDatabaseError, db_mod.EncryptionError, db_mod.DatabaseMissingError) as e:
        return Check("database", "fail", "Database", str(e).splitlines()[0], "check db_key (`coach config show`)")
    except db_mod.Error as e:
        return Check("database", "fail", "Database", f"database error: {e}", "is another coach process running?")
    if st["pending"]:
        return Check("database", "warn", "Database", f"encrypted, {len(st['pending'])} pending migration(s)", "run `coach db migrate`")
    return Check("database", "ok", "Database", f"encrypted, {len(st['applied'])} migration(s) applied")


def check_enable_banking(cfg) -> list[Check]:
    out = []
    missing = [k for k, v in (("app_id", cfg.eb_app_id), ("redirect_url", cfg.eb_redirect_url), ("private_key_path", cfg.eb_private_key_path)) if not v]
    if missing:
        return [Check("enablebanking", "warn", "Enable Banking", f"not configured yet (missing: {', '.join(missing)})",
                      "run `coach setup enablebanking` (file imports with `coach import` work without it)")]
    key = Path(cfg.eb_private_key_path).expanduser()
    if not key.is_absolute():
        key = cfg.root / key
    if not key.is_file():
        return [Check("enablebanking", "fail", "Enable Banking", f"the private key file {key} does not exist", "put the downloaded .pem there, or fix [enable_banking] private_key_path")]
    out.append(Check("enablebanking", "ok", "Enable Banking", f"app {cfg.eb_app_id[:8]}..., redirect {cfg.eb_redirect_url}, key file found"))
    try:
        head = key.read_text(errors="replace")[:60]
    except OSError:
        head = ""
    if "PRIVATE KEY" not in head:
        out.append(Check("enablebanking.key", "warn", "Enable Banking key", "the file does not look like a PEM private key", "download the key again from the Enable Banking control panel"))
    mode = _mode(key)
    if mode is not None and mode & 0o077:
        out.append(Check("enablebanking.perms", "warn", "Enable Banking key", f"{key} is readable by other users (mode {mode:04o})", f"chmod 600 {key}"))
    return out


def check_permissions(cfg) -> Check:
    bad = []
    for label, p in (("data_dir", cfg.data_dir), ("memory_dir", cfg.memory_dir), ("backup dir", cfg.backup_dir)):
        mode = _mode(p)
        if mode is not None and mode & 0o077:
            bad.append(f"{label} {p} (mode {mode:04o})")
    mode = _mode(cfg.db_path)
    if mode is not None and mode & 0o077:
        bad.append(f"database (mode {mode:04o})")
    if bad:
        return Check("permissions", "warn", "Permissions", "open to other users: " + "; ".join(bad), "run `coach security audit --fix-permissions`")
    return Check("permissions", "ok", "Permissions", "data, memory, backups and database are private to you")


def check_web(root: Optional[Path] = None) -> Check:
    static = (root or Path(__file__).resolve().parents[1] / "api" / "static") / "index.html"
    if static.is_file():
        return Check("web", "ok", "Web app", "built and packaged")
    return Check("web", "warn", "Web app", "the built web app is missing (`coach ui` cannot serve a page)", "from a checkout: `cd web && pnpm install && pnpm build`; an installed package ships it")


def check_claude_cli(cfg) -> Check:
    needs = "claude-code" in (cfg.llm_backend, cfg.coach_backend)
    found = shutil.which("claude")
    if in_container():
        if needs:
            return Check("claude", "fail", "claude command", "the claude-code backend is not available in a container", 'set [llm] backend and [coach] backend to "anthropic-api" (secret anthropic_api_key) or "ollama"')
        return Check("claude", "info", "claude command", "not used in a container (backend: " + cfg.coach_backend + ")")
    if found:
        return Check("claude", "ok" if needs else "info", "claude command", f"found ({'used by the claude-code backend' if needs else 'optional, not used by the current backends'})")
    if needs:
        return Check("claude", "warn", "claude command", "not found, but a backend is set to claude-code",
                     'install Claude Code, or set [llm] backend / [coach] backend to "anthropic-api" or "ollama"')
    return Check("claude", "info", "claude command", "not installed (optional: only the claude-code backend needs it)")


def check_backends(cfg, env=None) -> list[Check]:
    out = []
    if "anthropic-api" in (cfg.llm_backend, cfg.coach_backend):
        try:
            v, _ = secrets.lookup("anthropic_api_key", env)
        except secrets.SecretBackendError:
            v = None
        out.append(Check("anthropic_key", "ok" if v else "fail", "Secret anthropic_api_key", "set" if v else "a backend is set to anthropic-api but the secret is missing",
                         "" if v else "`coach config set-secret anthropic_api_key` (or the ANTHROPIC_API_KEY environment variable)"))
    return out


def check_git(cfg) -> Check:
    if not cfg.memory_history:
        return Check("git", "info", "git", "memory history is off ([memory] history = false)")
    if shutil.which("git"):
        return Check("git", "ok", "git", "found (the memory change history uses it)")
    return Check("git", "warn", "git", "not found: the memory change history needs it", "install git, or set [memory] history = false")


def privacy_summary(cfg) -> Check:
    mode = "offline" if cfg.privacy_offline else ("local_only" if cfg.privacy_local_only else "standard")
    return Check("privacy", "info", "Privacy mode", f"{mode}; classification backend {cfg.llm_backend}, coach backend {cfg.coach_backend}; see `coach privacy status`")


def run_checks(cfg, env=None) -> list[Check]:
    checks = [check_python(), check_sqlcipher(), check_config(cfg)]
    checks += check_secret_store(env)
    checks.append(check_database(cfg))
    checks += check_enable_banking(cfg)
    checks += [check_permissions(cfg), check_web(), check_claude_cli(cfg)]
    checks += check_backends(cfg, env)
    checks += [check_git(cfg), privacy_summary(cfg)]
    return checks


def next_steps(checks: list[Check]) -> list[str]:
    """What to do, in order: every fail, then every warn, each with its hint; when all is well, the first-run command."""
    todo = [c for c in checks if c.level in ("fail", "warn") and c.hint]
    todo.sort(key=lambda c: ORDER[c.level])
    steps = [f"{c.title}: {c.hint}" for c in todo]
    if not any(c.level == "fail" for c in checks):
        steps.append("coach setup            the guided first run (bank link, first sync, categories, interview)")
    return steps


def cmd_doctor(a, cfg, *, out=print) -> None:
    checks = run_checks(cfg)
    if getattr(a, "json", False):
        out(json.dumps({"checks": [c.to_dict() for c in checks], "next_steps": next_steps(checks),
                        "ok": not any(c.level == "fail" for c in checks)}, indent=2))
    else:
        mark = {"ok": "ok  ", "info": "info", "warn": "WARN", "fail": "FAIL"}
        out(f"coach doctor (coach {__version__}, home {cfg.root})")
        for c in checks:
            out(f"  [{mark[c.level]}] {c.title:<22}{c.detail}")
        steps = next_steps(checks)
        out("\nNext steps:" if steps else "\nNothing to do.")
        for i, s in enumerate(steps, 1):
            out(f"  {i}. {s}")
    if any(c.level == "fail" for c in checks):
        sys.exit(1)


def register(sub, add) -> None:
    s = add(sub, "doctor", cmd_doctor, "check this installation (python, sqlcipher, secrets, database, Enable Banking, permissions) and print the next steps")
    s.add_argument("--json", action="store_true", help="machine-readable output")
