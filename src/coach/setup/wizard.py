"""``coach setup``: the first-run wizard (E13-2), and the read-only status the web Setup page shows.

Seven steps, in order. Each one DETECTS its own state from the real installation (so it is idempotent and a state file can never lie about
what exists), runs only when you say so, and does nothing that leaves the machine without your explicit consent at that step:

1. ``init``          home, secrets, encrypted database (`coach init` + `coach doctor`)
2. ``enablebanking`` your own Enable Banking application (`coach setup enablebanking`), validated by `coach check` if you agree
3. ``connect``       link the first bank (`coach connect`: your browser, your bank's login page)
4. ``sync``          the first sync, longest history the bank allows
5. ``classify``      normalize (local), a dry run that prints exactly what a model would receive, then the privacy choice and, if you agree, the run
6. ``onboarding``    the interview about your household (`coach onboarding run`, every write previewed)
7. ``schedule``      optional: the daily job (launchd on macOS, `coach schedule loop` in a container); alert channels stay OFF

The state file ``<data_dir>/setup-state.json`` (0600) records only step names, statuses, timestamps and the choices made: no name, merchant or amount.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from coach import config as config_mod, db as db_mod, egress
from coach.i18n_msg import server_msg
from coach.setup import doctor as doctor_mod, eb_guide, init as init_mod

STATE_NAME = "setup-state.json"
STEPS = ("init", "enablebanking", "connect", "sync", "classify", "onboarding", "schedule")
# the web shows labels.setupStep.<step> (web/src/locales/<lang>/server.json); this English title is the fallback and the CLI's
TITLES = {
    "init": "Home, secrets and encrypted database",
    "enablebanking": "Your Enable Banking application",
    "connect": "Connect your first bank",
    "sync": "First sync (longest history)",
    "classify": "Normalize and categorize (privacy choice)",
    "onboarding": "Onboarding interview",
    "schedule": "Daily job and alerts (optional)",
}
OPTIONAL = ("classify", "onboarding", "schedule")      # may be skipped; the others are needed for the coach to work
NEEDS = {"enablebanking": "init", "connect": "enablebanking", "sync": "connect", "classify": "sync", "onboarding": "init", "schedule": "init"}
COMMANDS = {
    "init": "coach init && coach doctor",
    "enablebanking": "coach setup enablebanking",
    "connect": "coach connect --bank \"<bank name>\" --country FR",
    "sync": "coach sync",
    "classify": "coach normalize && coach classify run --dry-run",
    "onboarding": "coach onboarding run",
    "schedule": "coach schedule install",
}


DOCKER = "docker compose run --rm "
CONTAINER_COMMANDS = {
    "init": DOCKER + "coach doctor",
    "enablebanking": DOCKER + "-v \"$PWD/eb.pem:/tmp/eb.pem:ro\" coach setup enablebanking",
    "connect": DOCKER + "-p 127.0.0.1:8443:8443 coach connect --bank \"<bank name>\" --country FR",
    "sync": DOCKER + "coach sync",
    "classify": DOCKER + "coach classify run --dry-run",
    "onboarding": DOCKER + "coach onboarding run",
    "schedule": "docker compose --profile scheduler up -d",
}


def in_container() -> bool:
    """A real container (marker file / cgroup) or the image's own environment flag."""
    from coach import home as home_mod
    return home_mod.on_container_filesystem() or doctor_mod.in_container()


def command_for(step: str, container: bool) -> str:
    return CONTAINER_COMMANDS[step] if container else COMMANDS[step]


# ---------------------------------------------------------------- state file

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"version": 1, "steps": {}, "choices": {}}
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("steps"), dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    @classmethod
    def for_cfg(cls, cfg) -> "State":
        return cls(cfg.data_dir / STATE_NAME)

    def step(self, name: str) -> dict:
        return self.data["steps"].get(name, {})

    def mark(self, name: str, status: str) -> None:
        self.data["steps"][name] = {"status": status, "at": _now()}
        self.save()

    def choose(self, key: str, value) -> None:
        self.data["choices"][key] = value
        self.save()

    def choice(self, key: str, default=None):
        return self.data["choices"].get(key, default)

    def save(self) -> None:
        self.data["updated"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".setup-state-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def reset(self) -> None:
        self.data = {"version": 1, "steps": {}, "choices": {}}
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- detection (read-only, no network)

@dataclass
class Detected:
    id: str
    status: str            # done | partial | todo | skipped | blocked
    detail: str = ""
    detail_msg: Optional[dict] = None      # the detail for the web app: {code, params, text} (docs/i18n.md "Server text")

    @classmethod
    def of(cls, id: str, status: str, msg: dict) -> "Detected":
        return cls(id, status, msg["text"], msg)

    @property
    def title(self) -> str:
        return TITLES[self.id]

    def to_dict(self, container: bool = False) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status, "detail": self.detail, "detail_msg": self.detail_msg,
                "command": command_for(self.id, container), "optional": self.id in OPTIONAL}


def _counts(cfg) -> Optional[dict]:
    """Row counts of the database, or None when it cannot be opened (missing, key unavailable...). Nothing is written."""
    try:
        if db_mod.is_plaintext(cfg.db_path) is None:
            return None
        con = db_mod.connect(cfg, insecure=bool(cfg.insecure_plaintext_db), migrate=False)
    except Exception:                                                              # noqa: BLE001
        return None
    try:
        one = lambda q: con.execute(q).fetchone()[0]                                # noqa: E731
        return {"accounts": one("SELECT COUNT(*) FROM accounts WHERE source='api'"), "transactions": one("SELECT COUNT(*) FROM transactions"),
                "normalized": one("SELECT COUNT(*) FROM tx_enriched"), "labelled": one("SELECT COUNT(*) FROM merchants")}
    except Exception:                                                              # noqa: BLE001
        return None
    finally:
        con.close()


def _members(cfg) -> int:
    try:
        import yaml
        data = yaml.safe_load((cfg.memory_dir / "household.yaml").read_text()) or {}
        return len(data.get("members") or [])
    except Exception:                                                              # noqa: BLE001
        return 0


def _schedule_installed() -> bool:
    from coach import schedule as schedule_mod
    try:
        return schedule_mod.plist_path().exists()
    except Exception:                                                              # noqa: BLE001
        return False


def detect(cfg, state: Optional[State] = None) -> list[Detected]:
    """The state of the seven steps, from the installation itself. Read-only; no network."""
    state = state or State.for_cfg(cfg)
    core_ids = {"python", "sqlcipher", "config", "secret.db_key", "secret.backup_key", "database"}
    checks = [c for c in doctor_mod.run_checks(cfg) if c.id in core_ids]
    bad = [c for c in checks if c.level == "fail"]
    out: dict[str, Detected] = {}
    if bad:                                   # the doctor's own titles and details (English), passed as they are
        problems = "; ".join(f"{c.title}: {c.detail}" for c in bad)[:240]
        out["init"] = Detected.of("init", "todo", server_msg("wizard.initProblems", problems, problems=problems))
    else:
        out["init"] = Detected.of("init", "done", server_msg("wizard.ready", "ready"))

    eb_ok = bool(cfg.eb_app_id and cfg.eb_redirect_url and cfg.eb_private_key_path)
    if not eb_ok:
        out["enablebanking"] = Detected.of("enablebanking", "todo", server_msg("wizard.ebNotSet", "app id, redirect URL or key not set"))
    elif state.step("enablebanking").get("status") == "done":
        out["enablebanking"] = Detected.of("enablebanking", "done", server_msg("wizard.ebValidated", "configured and validated"))
    else:
        out["enablebanking"] = Detected.of("enablebanking", "partial", server_msg("wizard.ebNotValidated", "configured, not validated with "
                                                                                  "`coach check` yet", command="coach check"))

    counts = _counts(cfg) if not bad else None
    if counts is None:
        for s in ("connect", "sync", "classify"):
            out[s] = Detected.of(s, "blocked", server_msg("wizard.needsDatabase", "needs a working database (step 1)"))
    else:
        n_acc, n_tx, n_lab = counts["accounts"], counts["transactions"], counts["labelled"]
        out["connect"] = Detected.of("connect", "done" if n_acc else "todo",
                                     server_msg("wizard.accountsLinked", f"{n_acc} bank account(s) linked", count=n_acc))
        out["sync"] = Detected.of("sync", "done" if n_tx else "todo", server_msg("wizard.transactions", f"{n_tx} transaction(s)", count=n_tx))
        if state.step("classify").get("status") == "skipped":
            out["classify"] = Detected.of("classify", "skipped", server_msg("wizard.classifySkipped", "skipped for now (rules and memory still apply)"))
        elif n_lab:
            out["classify"] = Detected.of("classify", "done", server_msg("wizard.merchantsLabelled", f"{n_lab} merchant(s) labelled", count=n_lab))
        elif counts["normalized"]:
            out["classify"] = Detected.of("classify", "partial", server_msg("wizard.normalizedOnly", "normalized, no model labelling yet"))
        else:
            out["classify"] = Detected.of("classify", "todo", server_msg("wizard.nothingNormalized", "nothing normalized yet"))

    n = _members(cfg)
    if state.step("onboarding").get("status") == "skipped" and not n:
        out["onboarding"] = Detected.of("onboarding", "skipped", server_msg("wizard.skipped", "skipped for now"))
    else:
        out["onboarding"] = Detected.of("onboarding", "done" if n else "todo",
                                        server_msg("wizard.members", f"{n} household member(s) declared", count=n))

    if state.step("schedule").get("status") == "skipped":
        out["schedule"] = Detected.of("schedule", "skipped", server_msg("wizard.skipped", "skipped for now"))
    elif state.step("schedule").get("status") == "done" or _schedule_installed():
        out["schedule"] = Detected.of("schedule", "done", server_msg("wizard.scheduleInstalled", "daily job installed"))
    else:
        out["schedule"] = Detected.of("schedule", "todo", server_msg("wizard.noSchedule", "no daily job"))

    for step, need in NEEDS.items():          # a step whose predecessor is not done cannot start
        ready = out[need].status in ("done", "skipped") or (need == "enablebanking" and out[need].status == "partial")   # configured counts
        if out[step].status in ("todo", "partial") and not ready:
            # `need` is the step id: the web shows its label (labels.setupStep.<need>) in place of the English title `step`
            out[step] = Detected.of(step, "blocked", server_msg("wizard.finishFirst", f"finish '{TITLES[need]}' first", step=TITLES[need],
                                                                 need=need))
    return [out[s] for s in STEPS]


def status(cfg, state: Optional[State] = None) -> dict:
    """Read-only summary for `coach setup --status` and the web Setup page."""
    steps = detect(cfg, state)
    done = sum(1 for s in steps if s.status in ("done", "skipped"))
    nxt = next((s.id for s in steps if s.status not in ("done", "skipped")), None)
    container = in_container()
    return {"steps": [s.to_dict(container) for s in steps], "progress": {"done": done, "total": len(steps), "next_step": nxt},
            "container": container,
            # what to type, computed here because only the server knows where it runs (host: uv run; container: docker compose run)
            "command": ("docker compose run --rm coach setup" if container else "uv run coach setup"),
            "commands": {"setup": ("docker compose run --rm coach setup" if container else "uv run coach setup"),
                         "enablebanking": CONTAINER_COMMANDS["enablebanking"] if container else "uv run coach setup enablebanking"}}


# ---------------------------------------------------------------- the interactive run

@dataclass
class Actions:
    """What the steps do to the outside world and to the database. Tests pass fakes; the defaults call the real commands."""
    init: Callable = None
    eb_guide: Callable = None
    check: Callable = None
    banks: Callable = None
    connect: Callable = None
    sync: Callable = None
    normalize: Callable = None
    classify: Callable = None          # (cfg, dry_run) -> None
    onboarding: Callable = None
    schedule_install: Callable = None

    def __post_init__(self):
        d = default_actions()
        for k, v in d.__dict__.items():
            if getattr(self, k) is None:
                setattr(self, k, v)


def _ns(insecure: bool, **kw) -> Namespace:
    return Namespace(insecure=insecure, **kw)


def default_actions() -> Actions:
    a = object.__new__(Actions)

    def check(cfg, insecure=False):
        from coach.ingest import commands as ic
        ic.cmd_check(_ns(insecure), cfg)

    def banks(cfg, country, q, insecure=False):
        from coach.ingest import commands as ic
        ic.cmd_banks(_ns(insecure, country=country, q=q), cfg)

    def connect(cfg, bank, country, insecure=False, no_server=False):
        from coach.ingest import commands as ic
        ic.cmd_connect(_ns(insecure, bank=bank, country=country, days=180, no_browser=False,
                           no_server=no_server, timeout=None, replace=False), cfg)

    def sync(cfg, insecure=False):
        from coach.ingest import commands as ic
        ic.cmd_sync(_ns(insecure, account=None, full=False, force=False), cfg)

    def normalize(cfg, insecure=False):
        from coach.classify import commands as cc
        return cc.cmd_normalize(_ns(insecure), cfg)

    def classify(cfg, dry_run, insecure=False):
        from coach.classify import commands as cc
        cc.cmd_run(_ns(insecure, model=None, batch=50, workers=4, limit=10 if dry_run else None, refresh=False, include_ruled=False,
                       dry_run=dry_run, no_knn=False, knn_threshold=None, abandon_unrecorded=False, claim_legacy_batches=None), cfg)

    def onboarding(cfg, insecure=False):
        from coach.skills import commands as skc
        skc.cmd_run(_ns(insecure), cfg)

    def schedule_install(cfg):
        from coach import schedule as schedule_mod
        schedule_mod.install(cfg)

    a.init = lambda **kw: init_mod.run_init(**kw)
    a.eb_guide = eb_guide.run_guide
    a.check, a.banks, a.connect, a.sync, a.normalize = check, banks, connect, sync, normalize
    a.classify, a.onboarding, a.schedule_install = classify, onboarding, schedule_install
    return a


@dataclass
class Ctx:
    cfg: object
    state: State
    insecure: bool = False
    isatty: Callable[[], bool] = lambda: bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    input_fn: Callable[[str], str] = input
    out: Callable = print
    actions: Actions = field(default_factory=Actions)

    def ask(self, prompt: str) -> str:
        return self.input_fn(prompt).strip()

    def typed(self, word: str, prompt: str) -> bool:
        """An explicit consent: the person must TYPE the word (anything else is a no)."""
        return self.ask(f"{prompt} Type {word!r} to continue, anything else cancels: ").lower() == word


def _reload(ctx: Ctx) -> None:
    """Re-read the configuration after a step edited it."""
    if ctx.cfg.config_path:
        ctx.cfg = config_mod.load_config(ctx.cfg.config_path)


def step_init(ctx: Ctx) -> None:
    out = ctx.out
    out("This creates your private home: the commented configuration, a 0700 data folder, the memory skeleton (templates only), "
        "the secrets (generated after a typed confirmation) and the empty encrypted database.")
    home = ctx.cfg.config_path.parent if ctx.cfg.config_path else ctx.cfg.root
    rep = ctx.actions.init(home=home, isatty=ctx.isatty, input_fn=ctx.input_fn, out=out)
    init_mod.print_report(rep, out)
    _reload(ctx)
    for c in doctor_mod.run_checks(ctx.cfg):
        if c.level in ("fail", "warn"):
            out(f"  [{c.level.upper()}] {c.title}: {c.detail}" + (f" -> {c.hint}" if c.hint else ""))


def step_enablebanking(ctx: Ctx) -> None:
    res = ctx.actions.eb_guide(ctx.cfg, isatty=ctx.isatty, input_fn=ctx.input_fn, out=ctx.out,
                               check_fn=lambda: ctx.actions.check(config_mod.load_config(ctx.cfg.config_path), ctx.insecure))
    _reload(ctx)
    if res.get("checked"):
        ctx.state.mark("enablebanking", "done")


def step_connect(ctx: Ctx) -> None:
    out = ctx.out
    out("This links your FIRST bank. `coach connect` contacts Enable Banking and opens your browser on your bank's own login page "
        "(strong authentication happens there; the coach never sees your bank password). The result is a consent valid for up to 180 days.")
    country = (ctx.ask("Country of the bank (2 letters, e.g. FR or IT): ") or "").upper()
    if len(country) != 2:
        out("A two-letter country code is needed.")
        return
    bank = ctx.ask("Exact bank name as Enable Banking lists it (type ? to list the banks of that country): ")
    if bank == "?":
        if not ctx.typed("list", "Listing the banks asks api.enablebanking.com (no personal data is sent)."):
            return
        q = ctx.ask("Filter by name (Enter for all): ")
        ctx.actions.banks(ctx.cfg, country, q or None, ctx.insecure)
        bank = ctx.ask("Exact bank name: ")
    if not bank:
        return
    manual = False
    if in_container():
        out("\nIn Docker the bank redirects your browser to https://localhost:8443/callback, which must reach the container: publish that port on your "
            "host's 127.0.0.1 ONLY.\n  [1] (recommended) run this on your host, in another terminal, then run `docker compose run --rm coach setup` again:\n\n"
            f"      {CONTAINER_COMMANDS['connect'].replace('<bank name>', bank).replace('FR', country)}\n\n"
            "      (your browser warns once about the self-signed certificate: accept it)\n"
            "  [2] copy-paste here: you open the bank URL, then paste the address your browser ends on")
        if ctx.ask("Choose 1 or 2: ") != "2":
            return
        manual = True
    if not ctx.typed("connect", f"Connect {bank} ({country}) now? This contacts Enable Banking" + ("." if manual else " and opens your browser.")):
        out("Cancelled; nothing was sent.")
        return
    try:
        ctx.actions.connect(ctx.cfg, bank, country, ctx.insecure, manual)
    except SystemExit as e:
        out(f"The connection did not complete: {e}")


def step_sync(ctx: Ctx) -> None:
    ctx.out("The first sync fetches the longest history the bank gives (often up to 2 years, some banks only 90 days) and the balances, "
            "from Enable Banking. Nothing is sent from your database; the answers are stored encrypted on this machine.")
    if not ctx.typed("sync", "Run the first sync now?"):
        ctx.out("Cancelled; nothing was sent.")
        return
    try:
        ctx.actions.sync(ctx.cfg, ctx.insecure)
    except SystemExit as e:
        ctx.out(f"The sync did not complete: {e}")


def llm_explanation(cfg) -> list[str]:
    flow = egress.KINDS.get(f"llm.{cfg.llm_backend}")
    pol = egress.policy_of(cfg)
    lines = [f"Categories come from rules and your memory first; a model labels only the merchants they leave open (backend: {cfg.llm_backend})."]
    if flow is not None:
        lines += [f"  goes to:   {flow.destination}", "  sent:      redacted merchant descriptors, the category list, a few labelled examples",
                  f"  redaction: {flow.redaction.split(':')[0]}"]
    lines += ["  never sent: your name, account numbers or IBANs, amounts, dates, person-like merchants (they are held back).",
              "  you can read the exact payloads above (the dry run); every call is journaled locally (`coach privacy report`)."]
    if pol.offline:
        lines.append("  [privacy] offline is on: no model call is possible.")
    elif pol.local_only:
        lines.append("  [privacy] local_only is on: only a local Ollama model is allowed.")
    return lines


def step_classify(ctx: Ctx) -> None:
    out = ctx.out
    out("1/3 Normalizing the descriptions (local, no network)...")
    ctx.actions.normalize(ctx.cfg, ctx.insecure)
    out("\n2/3 Dry run: the exact (redacted) requests a model would receive. Nothing is sent.")
    try:
        ctx.actions.classify(ctx.cfg, True, ctx.insecure)
    except (egress.EgressDenied, SystemExit) as e:
        out(f"(dry run unavailable: {e})")
    out("\n3/3 Your privacy choice")
    for line in llm_explanation(ctx.cfg):
        out(line)
    pol = egress.policy_of(ctx.cfg)
    out("\n  [1] run it with the current backend now\n  [2] go fully local: set [privacy] local_only = true and the backends to \"ollama\" "
        "(needs an Ollama server on this machine); nothing is labelled now\n  [3] skip for now (rules and your memory still categorize; `coach classify run` later)")
    choice = ctx.ask("Choose 1, 2 or 3: ")
    if choice == "1":
        if pol.offline or (pol.local_only and ctx.cfg.llm_backend != "ollama"):
            out("That is not allowed under your [privacy] settings: choose 2 or 3.")
            return
        if not ctx.typed("send", f"Send the redacted merchant descriptors to the {ctx.cfg.llm_backend} backend?"):
            out("Cancelled; nothing was sent.")
            return
        ctx.state.choose("classify_backend", ctx.cfg.llm_backend)
        try:
            ctx.actions.classify(ctx.cfg, False, ctx.insecure)
        except (egress.EgressDenied, SystemExit) as e:
            out(f"The run did not complete: {e}")
    elif choice == "2":
        if not ctx.cfg.config_path:
            out("No configuration file: run `coach init` first.")
            return
        if not ctx.typed("local", "This edits your configuration ([privacy] local_only, [llm] and [coach] backend)."):
            return
        text = ctx.cfg.config_path.read_text()
        text = config_mod.set_toml_bool(text, "privacy", "local_only", True)
        text = config_mod.set_toml_value(text, "llm", "backend", "ollama")
        text = config_mod.set_toml_value(text, "coach", "backend", "ollama")
        ctx.cfg.config_path.write_text(text)
        _reload(ctx)
        ctx.state.choose("classify_backend", "ollama")
        ctx.state.mark("classify", "skipped")
        out("Local mode is on. Start Ollama, pull a model that supports tool calling, then run `coach classify run`.")
    elif choice == "3":
        ctx.state.mark("classify", "skipped")
        out("Skipped. Run `coach classify run --dry-run`, then `coach classify run`, whenever you want.")
    else:
        out("No choice made; nothing was sent.")


def step_onboarding(ctx: Ctx) -> None:
    ctx.out("The interview asks about your household (members, country, accounts' owners, loans, contracts, preferences). Every answer is a "
            "previewed memory edit that is written only after a typed yes. Nothing leaves this machine.")
    if not ctx.typed("start", "Start the interview now?"):
        return
    try:
        ctx.actions.onboarding(ctx.cfg, ctx.insecure)
    except SystemExit as e:
        ctx.out(f"The interview stopped: {e}")


def step_schedule(ctx: Ctx) -> None:
    out = ctx.out
    out("Alert channels (ntfy, e-mail, Telegram) are all OFF and stay off: the in-app alerts are local. To enable one, edit its [alerts.*] "
        "section yourself and read docs/alerts.md. This wizard never enables a channel.")
    if doctor_mod.in_container() or sys.platform != "darwin":
        out("\nDaily job: run the loop `coach schedule loop` (in Docker: `docker compose --profile scheduler up -d`). It runs the same job as launchd, "
            f"once a day at [schedule] time ({ctx.cfg.schedule_time}).")
        if ctx.typed("noted", "Mark this step as done?"):
            ctx.state.mark("schedule", "done")
        return
    out(f"\nDaily job: a launchd LaunchAgent runs `coach schedule run` at {ctx.cfg.schedule_time}: sync (Enable Banking), normalize, "
        "classify with the backend you chose above, analytics, alerts (local), a weekly check. Remove it with `coach schedule uninstall`.")
    if not ctx.typed("install", "Install the daily job?"):
        return
    try:
        ctx.actions.schedule_install(ctx.cfg)
        ctx.state.mark("schedule", "done")
    except (RuntimeError, config_mod.ConfigError, SystemExit) as e:
        out(f"The job was not installed: {e}")


RUNNERS = {"init": step_init, "enablebanking": step_enablebanking, "connect": step_connect, "sync": step_sync,
           "classify": step_classify, "onboarding": step_onboarding, "schedule": step_schedule}


def print_status(cfg, state: Optional[State] = None, out=print) -> dict:
    st = status(cfg, state)
    mark = {"done": "[x]", "skipped": "[-]", "partial": "[~]", "todo": "[ ]", "blocked": "[#]"}
    p = st["progress"]
    out(f"first-run setup: {p['done']}/{p['total']} steps done" + (f"; next: {p['next_step']}" if p["next_step"] else " (finished)"))
    for s in st["steps"]:
        out(f"  {mark[s['status']]} {s['id']:<14}{s['title']:<46}{s['detail']}")
    out("  ([x] done, [-] skipped, [~] partly, [ ] to do, [#] needs an earlier step)")
    return st


def run_wizard(ctx: Ctx, only: Optional[str] = None) -> None:
    out = ctx.out
    if not ctx.isatty():
        raise SystemExit("error: `coach setup` is interactive and needs a terminal (`coach setup --status` shows where you are)")
    steps = [only] if only else list(STEPS)
    out("First-run setup. Nothing leaves this machine without a typed confirmation at the step that does it. Ctrl-C stops safely: run "
        "`coach setup` again to resume where you left off.\n")
    for i, name in enumerate(steps, 1):
        det = {d.id: d for d in detect(ctx.cfg, ctx.state)}[name]
        out(f"--- Step {STEPS.index(name) + 1}/{len(STEPS)}: {TITLES[name]}" + (" (optional)" if name in OPTIONAL else ""))
        if det.status == "done" and not only:
            out(f"  already done: {det.detail}\n")
            continue
        if det.status == "skipped" and not only:
            out(f"  skipped earlier ({det.detail}); `coach setup --step {name}` to do it now\n")
            continue
        if det.status == "blocked":
            out(f"  cannot start yet: {det.detail}\n")
            return
        if det.detail:
            out(f"  now: {det.detail}")
        prompt = "Run this step now? [y]es / [s]kip for now / [q]uit: " if name in OPTIONAL else "Run this step now? [y]es / [q]uit: "
        for _attempt in range(3):
            ans = ctx.ask(prompt).lower()
            if ans in ("y", "yes") or (ans in ("s", "skip") and name in OPTIONAL):
                break
            if ans in ("q", "quit", ""):                  # an empty answer is never a yes
                out("Stopped. Run `coach setup` to resume.")
                return
        else:
            out("No valid answer. Stopped: run `coach setup` to resume.")
            return
        if ans in ("s", "skip"):
            ctx.state.mark(name, "skipped")
            out("  skipped.\n")
            continue
        RUNNERS[name](ctx)
        after = {d.id: d for d in detect(ctx.cfg, ctx.state)}[name]
        out(f"  -> {after.status}: {after.detail}\n")
        if name not in OPTIONAL and after.status not in ("done", "skipped") and name != "enablebanking":
            out("This step is not finished yet. Fix what is reported above, then run `coach setup` again.")
            return
    st = status(ctx.cfg, ctx.state)
    out("Setup finished." if st["progress"]["next_step"] is None else f"Next: {st['progress']['next_step']} (`coach setup`).")


def cmd_setup(a, cfg, *, ctx: Optional[Ctx] = None) -> None:
    sub = getattr(a, "setup_cmd", None)
    if sub == "enablebanking":
        eb_guide.cmd_setup_enablebanking(a, cfg)
        return
    state = State.for_cfg(cfg)
    if getattr(a, "reset", False):
        state.reset()
        print("setup state cleared (nothing else was touched)")
        return
    if getattr(a, "status", False) or getattr(a, "json", False):
        if getattr(a, "json", False):
            from coach.i18n_msg import strip_msgs
            print(json.dumps(strip_msgs(status(cfg, state)), indent=2))
        else:
            print_status(cfg, state)
        return
    only = getattr(a, "step", None)
    ctx = ctx or Ctx(cfg=cfg, state=state, insecure=getattr(a, "insecure", False))
    try:
        run_wizard(ctx, only)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Run `coach setup` to resume.")


def register(sub, add) -> None:
    s = add(sub, "setup", cmd_setup, "the first-run wizard: init, Enable Banking, first bank, sync, categories, interview, schedule")
    s.add_argument("--status", action="store_true", help="show the seven steps and where you are (read-only, no terminal needed)")
    s.add_argument("--json", action="store_true", help="the status as JSON")
    s.add_argument("--step", choices=list(STEPS), help="run one step only")
    s.add_argument("--reset", action="store_true", help="forget the wizard's own state file (skipped steps, validation); nothing else is touched")
    ssub = s.add_subparsers(dest="setup_cmd", metavar="SUBCOMMAND")
    add(ssub, "enablebanking", cmd_setup, "guided creation of your own Enable Banking application (restricted mode), validated with `coach check` if you agree")
