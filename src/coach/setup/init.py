"""``coach init``: create a fresh, private home (E13-1). Every step is idempotent and never overwrites anything that exists.

1. the home folder and ``config.toml`` (copied from the commented example, only when missing)
2. ``data_dir`` (0700), the memory skeleton (templates only: no household data) and the CSV import profile examples
3. the secrets ``db_key`` / ``backup_key`` / ``proposal_key``, generated in the secret store (Keychain, or the secrets folder of the
   ``file`` backend) after an explicit confirmation typed in a terminal. A secret already provided (environment or store) is kept.
4. the empty encrypted database (``coach db migrate --create``)

The values of the secrets are never printed.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from coach import config as config_mod, db as db_mod, home as home_mod, secrets

GENERATED = (
    ("db_key", "encrypts the database (SQLCipher)"),
    ("backup_key", "encrypts the backups"),
    ("proposal_key", "seals the memory proposals against tampering"),
)
CONFIRM_WORD = "generate"


@dataclass
class StepResult:
    name: str
    status: str            # created | exists | skipped | failed | planned
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


@dataclass
class InitReport:
    home: Path
    config_path: Path
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def get(self, name: str) -> Optional[StepResult]:
        return next((s for s in self.steps if s.name == name), None)


def _is_tty() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def _sub_top_level(text: str, key: str, value: str) -> str:
    """Set a top-level `key = "value"` of the configuration text (before the first [section]), keeping its comment."""
    pat = re.compile(rf'^({re.escape(key)}\s*=\s*)"[^"]*"(.*)$', re.M)
    m = pat.search(text)
    first_section = re.search(r"^\[", text, re.M)
    if m and (not first_section or m.start() < first_section.start()):
        return text[:m.start()] + f'{m.group(1)}"{value}"{m.group(2)}' + text[m.end():]
    return f'{key} = "{value}"\n' + text


# What differs in a container: no `claude` command (cloud backends only, or ollama), the app listens on the container's interface (the
# compose file publishes it on the host's loopback only), no browser to open.
CONTAINER_DEFAULTS = (("llm", "backend", "anthropic-api"), ("coach", "backend", "anthropic-api"), ("ui", "host", "0.0.0.0"))


def render_config(template_text: str, data_dir: Optional[str], memory_dir: Optional[str], container: bool = False) -> str:
    text = template_text
    if data_dir:
        text = _sub_top_level(text, "data_dir", data_dir)
    if memory_dir:
        text = _sub_top_level(text, "memory_dir", memory_dir)
    if container:
        for section, key, value in CONTAINER_DEFAULTS:
            text = config_mod.set_toml_value(text, section, key, value)
        text = config_mod.set_toml_bool(text, "ui", "open_browser", False)
        text = config_mod.set_toml_bool(text, "callback", "container_bind", True)    # the bank redirect server: same rule as the web app
        text = config_mod.set_toml_bool(text, "ui", "container_bind", True)       # MJ-1: the one switch that lets the app listen on 0.0.0.0, in a real container only
    return text


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _copy_missing(src_root: Path, dest_root: Path, private: bool = True) -> tuple[list[str], list[str]]:
    """Copy every template file that does not exist yet; returns (created, kept)."""
    created, kept = [], []
    for rel in home_mod.template_files(src_root):
        dest = dest_root / rel
        if dest.exists():
            kept.append(rel.as_posix())
            continue
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write((src_root / rel).read_bytes())
        created.append(rel.as_posix())
    return created, kept


class _Env:
    """Set environment variables for a block (the secrets backend chosen on the command line), then restore them."""

    def __init__(self, **kv):
        self.kv = {k: v for k, v in kv.items() if v}
        self.old: dict = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _explain_secrets(backend: str, out) -> None:
    where = secrets.store_label()
    if backend == "keychain":
        where += f" (service '{secrets.SERVICE}')"
    out(f"\nThe coach needs {len(GENERATED)} secrets, stored in {where}. Their values are never shown:")
    for name, why in GENERATED:
        out(f"  {name:<14}{why}")
    out("Back them up in your password manager (`coach config show` says which are set): losing db_key makes the "
        "database unrecoverable, losing backup_key makes the backups unreadable.")


def generate_secrets(*, interactive: bool, isatty: Callable[[], bool], input_fn: Callable[[str], str], out=print) -> StepResult:
    """Create the missing secrets in the active store. The Keychain always needs a terminal and a typed confirmation; the file backend
    also does unless `interactive` is false (an explicit --non-interactive: a container's entrypoint, a script)."""
    try:
        backend = secrets.backend()
        missing = [name for name, _ in GENERATED if not secrets.lookup(name)[0]]
    except secrets.SecretBackendError as e:
        return StepResult("secrets", "failed", str(e))
    if not missing:
        return StepResult("secrets", "exists", "db_key, backup_key and proposal_key are already available")
    if interactive or backend == "keychain":
        if not isatty():
            return StepResult("secrets", "skipped", "creating secrets needs a terminal and a typed confirmation" + (
                " (with COACH_SECRETS_BACKEND=file, --non-interactive creates them without a prompt)" if backend == "keychain" else ""))
        _explain_secrets(backend, out)
        out(f"To create the missing ones ({', '.join(missing)}) type {CONFIRM_WORD!r}; anything else skips this step.")
        if input_fn("> ").strip().lower() != CONFIRM_WORD:
            return StepResult("secrets", "skipped", "not confirmed: create them later with `coach init` or `coach config set-secret NAME --generate`")
    try:
        for name in missing:
            secrets.set_secret(name, secrets.generate())
    except secrets.SecretBackendError as e:
        return StepResult("secrets", "failed", str(e))
    return StepResult("secrets", "created", f"generated {', '.join(missing)} in {secrets.store_label()}; back them up in your password manager")


def _planned(home: Path, value: Optional[str], default: str) -> Path:
    p = Path(value or default).expanduser()
    return p if p.is_absolute() else home / p


def run_init(home: Path, *, data_dir: Optional[str] = None, memory_dir: Optional[str] = None,
             secrets_backend: Optional[str] = None, secrets_dir: Optional[str] = None,
             interactive: bool = True, do_secrets: bool = True, do_db: bool = True, secrets_only: bool = False,
             container: Optional[bool] = None, dry_run: bool = False, isatty: Optional[Callable[[], bool]] = None,
             input_fn: Callable[[str], str] = input, out=print) -> InitReport:
    isatty = isatty or _is_tty
    if container is None:
        from coach.setup.doctor import in_container
        container = in_container()
    home = Path(home).expanduser().resolve()
    cfg_path = home / config_mod.CONFIG_NAME
    rep = InitReport(home=home, config_path=cfg_path)
    add = rep.steps.append
    cfg = None

    with _Env(**{secrets.BACKEND_VAR: secrets_backend, secrets.DIR_VAR: secrets_dir}):
        if not secrets_only:
            # 1. home + configuration
            if cfg_path.exists():
                add(StepResult("config", "exists", f"{cfg_path} (left untouched)"))
            elif dry_run:
                add(StepResult("config", "planned", str(cfg_path)))
            else:
                try:
                    if not home.exists():
                        home.mkdir(parents=True, mode=0o700)
                    _write_private(cfg_path, render_config(home_mod.config_template().read_text(), data_dir, memory_dir, container))
                    add(StepResult("config", "created", str(cfg_path)))
                except OSError as e:
                    add(StepResult("config", "failed", f"{cfg_path}: {type(e).__name__}"))
                    return rep
            if cfg_path.exists():
                try:
                    cfg = config_mod.load_config(cfg_path, env={})
                except config_mod.ConfigError as e:
                    add(StepResult("config", "failed", str(e)))
                    return rep
            data_p = cfg.data_dir if cfg else _planned(home, data_dir, "data")
            mem_p = cfg.memory_dir if cfg else _planned(home, memory_dir, "memory")
            # 2. folders and the memory skeleton
            if dry_run:
                add(StepResult("data_dir", "exists" if data_p.exists() else "planned", str(data_p)))
                add(StepResult("memory", "exists" if (mem_p / "household.yaml").exists() else "planned", str(mem_p)))
            else:
                fresh = not data_p.exists()
                db_mod.ensure_private_dir(data_p)
                add(StepResult("data_dir", "created" if fresh else "exists", f"{data_p} (mode 0700)"))
                created, kept = _copy_missing(home_mod.memory_template(), mem_p)
                add(StepResult("memory", "created" if created else "exists",
                               f"{mem_p}: {len(created)} template file(s) added, {len(kept)} existing kept (templates only, no household data)"))
                prof_created, _ = _copy_missing(home_mod.import_profiles_template(), cfg.import_profiles_dir, private=False)
                if prof_created:
                    add(StepResult("import_profiles", "created", f"{len(prof_created)} CSV profile example(s) in {cfg.import_profiles_dir}"))

        # 3. secrets
        if do_secrets:
            if dry_run:
                add(StepResult("secrets", "planned", f"would create the missing secrets in {secrets.store_label()} after a typed confirmation"))
            else:
                add(generate_secrets(interactive=interactive, isatty=isatty, input_fn=input_fn, out=out))

        # 4. the empty encrypted database
        if do_db and not secrets_only:
            if dry_run:
                add(StepResult("database", "planned", "would create the empty encrypted database and apply the migrations"))
            elif cfg is None:
                add(StepResult("database", "skipped", "no configuration"))
            else:
                add(_create_database(cfg))
    return rep


def _create_database(cfg) -> StepResult:
    existed = db_mod.is_plaintext(cfg.db_path) is not None
    try:
        if secrets.get_secret("db_key", required=False) is None and not cfg.insecure_plaintext_db:
            return StepResult("database", "skipped", "db_key is not available yet (the secrets step was skipped): run `coach init` again")
        con = db_mod.connect(cfg, insecure=False, migrate=True, create=True)
        con.close()
    except (secrets.SecretNotFound, secrets.SecretBackendError, db_mod.PlaintextDatabaseError, db_mod.WrongKeyError,
            db_mod.EncryptionError, db_mod.MigrationError, db_mod.DatabaseMissingError) as e:
        return StepResult("database", "failed", str(e))
    except db_mod.Error as e:
        return StepResult("database", "failed", f"database error: {e}")
    if existed:
        return StepResult("database", "exists", f"{cfg.db_path} (opened; pending migrations applied)")
    return StepResult("database", "created", f"{cfg.db_path} (empty, encrypted with db_key)")


NEXT_STEPS = """
Next steps
  1. coach doctor                       check this installation
  2. coach setup enablebanking          create your own Enable Banking application (restricted mode) and link it
  3. coach setup                        the guided first run: bank link, first sync, categories, interview"""


def print_report(rep: InitReport, out=print) -> None:
    mark = {"created": "+", "exists": "=", "skipped": "-", "failed": "!", "planned": "?"}
    out(f"coach init: home {rep.home}")
    for s in rep.steps:
        out(f"  [{mark[s.status]}] {s.name:<16}{s.status:<8} {s.detail}")
    out("  (+ created, = already there, - skipped, ! failed, ? planned)")
    out(NEXT_STEPS)


def cmd_init(a, cfg, *, isatty=None, input_fn=input, out=print) -> None:
    if getattr(a, "home", None):
        home = Path(a.home)
    elif cfg.config_path:
        home = cfg.config_path.parent
    else:
        home = cfg.root
    rep = run_init(home, data_dir=a.data_dir, memory_dir=a.memory_dir, secrets_backend=a.secrets_backend,
                   secrets_dir=a.secrets_dir, interactive=not a.non_interactive, do_secrets=not a.no_secrets,
                   do_db=not a.no_db, secrets_only=a.secrets_only, container=True if a.container else None, dry_run=a.dry_run, isatty=isatty, input_fn=input_fn, out=out)
    print_report(rep, out)
    if a.secrets_backend == "file":
        out(f"The file backend was used for this run only. To keep using it, export {secrets.BACKEND_VAR}=file "
            f"and {secrets.DIR_VAR}={a.secrets_dir or secrets.DEFAULT_DIR} in your environment.")
    if not rep.ok:
        sys.exit(1)


def register(sub, add) -> None:
    s = add(sub, "init", cmd_init, "create a fresh home: config.toml, private data folder, memory skeleton, secrets, empty encrypted database")
    s.add_argument("--home", metavar="DIR", help="the folder that holds config.toml (default: $COACH_HOME, the checkout, or ~/.ai-finance-coach)")
    s.add_argument("--data-dir", metavar="DIR", help="data_dir written into a NEW configuration (default: data)")
    s.add_argument("--memory-dir", metavar="DIR", help="memory_dir written into a NEW configuration (default: memory)")
    s.add_argument("--secrets-backend", choices=list(secrets.BACKENDS), help="keychain (macOS, default) or file (containers, Linux)")
    s.add_argument("--secrets-dir", metavar="DIR", help="folder of the file backend (default /run/secrets)")
    s.add_argument("--non-interactive", action="store_true",
                   help="no prompt: only with the file backend (the Keychain always asks in a terminal); for containers and scripts")
    s.add_argument("--no-secrets", action="store_true", help="do not create secrets")
    s.add_argument("--no-db", action="store_true", help="do not create the database")
    s.add_argument("--secrets-only", action="store_true", help="only create the missing secrets (e.g. into ./secrets for docker compose)")
    s.add_argument("--container", action="store_true", help="write the container defaults into a NEW configuration (automatic inside the Docker image)")
    s.add_argument("--dry-run", action="store_true", help="show what would be created; write nothing")
