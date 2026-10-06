"""Daily scheduler without extra infra: a launchd LaunchAgent that runs ``coach schedule run``."""
from __future__ import annotations

import contextlib
import io
import os
import plistlib
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path

from coach import secrets
from coach.config import Config, ConfigError
from coach.db import ensure_private_dir

LABEL = "com.ai-finance-coach.daily"


def agents_dir(override: str | os.PathLike | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    if env := os.getenv("COACH_LAUNCHAGENTS_DIR"):
        return Path(env).expanduser()
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(override=None) -> Path:
    return agents_dir(override) / f"{LABEL}.plist"


def _path_env() -> str:
    dirs = []
    if claude := shutil.which("claude"):
        dirs.append(str(Path(claude).parent))
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


def build_plist(cfg: Config, python: str | None = None) -> dict:
    hour, minute = (int(x) for x in cfg.schedule_time.split(":"))
    args = [python or sys.executable, "-m", "coach"]
    if cfg.config_path:
        args += ["--config", str(cfg.config_path)]
    args += ["schedule", "run"]
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(cfg.root),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(cfg.log_dir / "schedule.out.log"),
        "StandardErrorPath": str(cfg.log_dir / "schedule.err.log"),
        "EnvironmentVariables": {"PATH": _path_env(), "COACH_HOME": str(cfg.root)},
        "RunAtLoad": False,
    }


def _write_private(path: Path, text: str) -> None:
    """A small state file of the log folder, 0600 (the folder is 0700)."""
    path.write_text(text)
    os.chmod(path, 0o600)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install_commands(path: Path) -> list[list[str]]:
    return [["launchctl", "bootout", f"{_domain()}/{LABEL}"],
            ["launchctl", "bootstrap", _domain(), str(path)]]


def uninstall_commands() -> list[list[str]]:
    return [["launchctl", "bootout", f"{_domain()}/{LABEL}"]]


def preflight(cfg: Config) -> list[str]:
    """Problems that would make the daily job fail (empty list = ready). The job runs under launchd:
    no shell environment and no --insecure. So secrets must be in the Keychain and Enable Banking settings in
    config.toml / the legacy .env file; a plaintext DB only passes with insecure_plaintext_db = true."""
    from coach import db as dbm
    problems = []
    try:
        cfg.require_eb()
        cfg.require_redirect()
    except ConfigError as e:
        problems.append(str(e))
    else:
        if not Path(cfg.eb_private_key_path).expanduser().exists():
            problems.append(f"Enable Banking private key not found: {cfg.eb_private_key_path}")
    for key in ("app_id", "redirect_url", "private_key_path"):
        origin = cfg.sources.get(f"enable_banking.{key}", "")
        if origin.startswith("env"):
            problems.append(f"enable_banking.{key} comes from the shell environment ({origin}), which launchd "
                            "does not have: put it in config.toml (`coach config import-env`)")
    try:
        con = dbm.connect(cfg, insecure=False, migrate=False)
        con.close()
    except Exception as e:  # missing DB, plaintext refused, db_key missing/wrong...
        problems.append(f"database: {e}")
    else:
        if dbm.is_plaintext(cfg.db_path) is False:
            try:
                if not secrets.in_keychain("db_key"):
                    problems.append("db_key is not in the macOS Keychain (an environment variable from your "
                                    "shell is not available to launchd): `coach config set-secret db_key`")
            except secrets.SecretBackendError as e:
                problems.append(str(e))
    return problems


def install(cfg: Config, agents_override=None, dry_run=False, load=True, runner=subprocess.run,
            out=print, do_preflight=True) -> Path:
    path = plist_path(agents_override)
    plist = build_plist(cfg)
    if do_preflight:
        problems = preflight(cfg)
        if problems and not dry_run:
            raise ConfigError("Not installing the scheduler, the daily job would fail:\n  - "
                              + "\n  - ".join(problems) + "\n(fix these, or pass --skip-preflight)")
        for pr in problems:
            out(f"# preflight problem: {pr}")
    if dry_run:
        out(f"# would write {path}")
        out(plistlib.dumps(plist).decode())
        for c in install_commands(path):
            out("# would run: " + " ".join(c))
        return path
    ensure_private_dir(cfg.data_dir)
    ensure_private_dir(cfg.log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist))
    out(f"wrote {path}")
    if load:
        cmds = install_commands(path)
        runner(cmds[0], capture_output=True, text=True)  # not loaded yet on first install: ignore errors
        r = runner(cmds[1], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()}")
        out(f"loaded {LABEL}; runs daily at {cfg.schedule_time}")
    return path


def uninstall(agents_override=None, dry_run=False, runner=subprocess.run, out=print) -> None:
    path = plist_path(agents_override)
    if dry_run:
        for c in uninstall_commands():
            out("# would run: " + " ".join(c))
        out(f"# would remove {path}")
        return
    runner(uninstall_commands()[0], capture_output=True, text=True)
    if path.exists():
        path.unlink()
        out(f"removed {path}")
    else:
        out(f"{path} not installed")


def status(cfg: Config, agents_override=None, runner=subprocess.run, out=print) -> dict:
    path = plist_path(agents_override)
    info = {"installed": path.exists(), "path": str(path), "loaded": None, "time": None}
    if path.exists():
        p = plistlib.loads(path.read_bytes())
        sci = p.get("StartCalendarInterval", {})
        info["time"] = f"{sci.get('Hour', 0):02d}:{sci.get('Minute', 0):02d}"
        try:
            info["loaded"] = runner(["launchctl", "print", f"{_domain()}/{LABEL}"],
                                    capture_output=True, text=True).returncode == 0
        except FileNotFoundError:
            info["loaded"] = None
    out(f"launchd agent {LABEL}: {'installed' if info['installed'] else 'not installed'} ({path})")
    if info["installed"]:
        out(f"  scheduled daily at {info['time']}; loaded in launchd: {info['loaded']}")
    log = cfg.log_dir / "schedule.log"
    if log.exists():
        last = log.read_text().strip().splitlines()[-1:]
        if last:
            out(f"  last run: {last[0]}")
    return info


# ---------------------------------------------------------------- the job itself

def run_daily(cfg: Config, insecure: bool = False, client=None, out=print, notifier=None, alert_transports=None) -> int:
    """sync (respecting the per-account daily limit) -> consent check -> normalize -> internal transfer matching
    -> classify run (new merchants only). Consent warnings are logged at <= 14 and <= 3 days; an expired or
    revoked consent makes the run fail (exit 1). `notifier(message)` is called for new warnings when
    [notify] macos = true.
    Appends one line to <data_dir>/logs/schedule.log (a closed vocabulary: step names, counts, exception CLASSES; E12) and the structured
    events of the run (run id, step durations, outcomes, counts) to <data_dir>/logs/runs.jsonl, rotated by size and age.
    Returns a process exit code."""
    from coach.classify import commands as cc
    from coach.db import connect
    from coach.ingest.client import EnableBankingClient
    from coach import transfers as transfers_mod
    from coach.ingest import consent as consent_mod
    from coach.ingest.sync import sync_all
    from coach.notify import notify_macos

    from coach import __version__
    from coach.quality import logs as runlogs
    ns = Namespace(insecure=insecure, model=None, batch=50, workers=4, limit=None,
                   refresh=False, include_ruled=False)
    parts, failed, sync_failed, consent_dead = [], False, [], []
    ensure_private_dir(cfg.data_dir)
    ensure_private_dir(cfg.log_dir)
    runlog = runlogs.RunLog(cfg.log_dir, max_kb=cfg.log_max_kb, keep=cfg.log_keep, max_age_days=cfg.log_max_age_days, version=__version__)
    runlog.start()                                   # rotates the logs by size / age, then writes the first line

    def step(name, fn):
        nonlocal failed
        buf = io.StringIO()
        t0, status, counts, err = time.monotonic(), "ok", {}, None
        try:
            with contextlib.redirect_stdout(buf):
                res = fn()
            text = str(res)
            status, counts = runlogs.status_of(text), runlogs.counts_of(text)
            parts.append(f"{name}: {text}")
        except Exception as e:  # keep going: a failed sync must not block classification
            failed = True
            status, err = "error", type(e).__name__
            # the exception's TEXT can quote the data that failed (a merchant, an amount): the log keeps its class only
            parts.append(f"{name}: ERROR {type(e).__name__}")
        finally:
            text = buf.getvalue()
            if text:
                out(text.rstrip("\n"))
            runlog.step(name, status, int((time.monotonic() - t0) * 1000), counts, err)

    def do_sync():
        con = connect(cfg, insecure=insecure)
        res = sync_all(con, client or EnableBankingClient.from_config(cfg), daily_limit=cfg.sync_daily_limit,
                       memory_dir=cfg.memory_dir)
        sync_failed.extend(r for r in res if r["status"] == "failed")
        return (f"accounts={len(res)} new={sum(r.get('new', 0) for r in res)} "
                f"replaced={sum(r.get('replaced', 0) for r in res)} "
                f"skipped={sum(r['status'] == 'skipped' for r in res)} "
                f"failed={sum(r['status'] == 'failed' for r in res)}")

    def do_consents():
        con = connect(cfg, insecure=insecure)
        ws = consent_mod.warnings(con)
        for lvl, c in ws:
            print(f"WARNING {consent_mod.describe(c)}")
            if lvl in consent_mod.DEAD:
                consent_dead.append(c)
        if cfg.notify_macos and not cfg.alert_settings.macos.enabled:      # [alerts.macos] supersedes this older, consent-only notification
            send = notifier or notify_macos
            for threshold, c in consent_mod.pending_alerts(con):
                if send(consent_mod.describe(c)):
                    consent_mod.mark_alerted(con, c.session_id, threshold)
        detail = "; ".join(consent_mod.describe(c) for _, c in ws)       # an institution's name, in the one-line summary only: runs.jsonl keeps the counts
        return f"warnings={len(ws)} expired_or_revoked={len(consent_dead)}" + (f" [{detail}]" if detail else "")

    def do_transfers():
        con = connect(cfg, insecure=insecure)
        res = transfers_mod.match_transfers(con, cfg.transfer_window_days, auto_link=cfg.transfer_auto_link,
                                            **transfers_mod.opts(cfg))
        if cfg.transfer_auto_link:
            return (f"linked={len(res.linked)} proposals={len(res.proposals)} ambiguous={len(res.ambiguous)}")
        return (f"auto_link=off proposed={len(res.linked) + len(res.proposals)} ambiguous={len(res.ambiguous)} "
                "(see `coach transfers --proposals`)")

    from coach import egress as _egress
    _pol = _egress.policy_of(cfg)
    if _pol.offline:                          # E11-4: nothing leaves the machine; bank data comes from file imports only
        step("sync", lambda: "skipped ([privacy] offline = true)")
    else:
        step("sync", do_sync)
    if sync_failed:  # an account that failed to sync (bank error, 429, expired consent) is not "ok"
        failed = True
    step("consents", do_consents)
    if consent_dead:
        failed = True
    step("normalize", lambda: f"{cc.cmd_normalize(ns, cfg)} tx")
    step("transfers", do_transfers)
    def do_classify():
        from coach import egress
        if _pol.offline:                                        # E12: say WHY (offline implies local_only)
            return "skipped ([privacy] offline = true)"
        if _pol.local_only and cfg.llm_backend != "ollama":     # E11-4: a refused backend is a skipped step, not a failed run
            return f"skipped ([privacy] local_only needs [llm] backend = \"ollama\", it is {cfg.llm_backend!r})"
        try:
            return f"labelled={cc.cmd_run(ns, cfg)}"
        except egress.EgressDenied as e:
            return f"skipped ({e.code})"

    step("classify", do_classify)

    def do_memory_check():
        """Warn only: whatever happens here never makes the daily job fail."""
        from coach.memory.commands import memory_summary_line
        try:
            m = memory_summary_line(cfg, connect(cfg, insecure=insecure))
        except Exception as e:                                       # noqa: BLE001
            m = {"failed": f"{type(e).__name__}"}
        if m.get("failed"):
            print(f"WARNING memory check could not run: {m['failed']}")
            return "could not run"
        if m["errors"] or m["warnings"]:
            print(f"WARNING memory: {m['errors']} error(s), {m['warnings']} warning(s): run `coach memory check`")
        return f"errors={m['errors']} warnings={m['warnings']} info={m['info']}"

    if cfg.schedule_memory_check:
        step("memory", do_memory_check)

    def do_analytics():
        """Warn only (E4): refresh the recurring series and the anomalies, look at the budgets. Whatever happens here
        never makes the daily job fail."""
        from coach.analytics import api
        try:
            o = api.refresh_all(connect(cfg, insecure=insecure), cfg)
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING analytics could not run: {type(e).__name__}")
            return "could not run"
        a, b = o["anomalies"], o["budgets"]
        if a["new"]:
            print(f"WARNING analytics: {a['new']} new anomaly(ies): run `coach anomalies`")
        if b.get("over") or b.get("at_risk"):
            print(f"WARNING budgets: {b.get('over', 0)} over, {b.get('at_risk', 0)} at risk: run `coach budget status`")
        return (f"recurring={o['recurring']['series']} anomalies_open={a['open']} anomalies_new={a['new']} "
                f"budgets={b['set']}")

    if cfg.schedule_analytics:
        step("analytics", do_analytics)

    def do_networth():
        """Warn only (E9-4): store today's net worth snapshot and back-fill the past months. Never makes the daily job fail."""
        from coach.loans import service as loans_service
        try:
            r = loans_service.record_networth(connect(cfg, insecure=insecure), cfg)
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING net worth snapshot could not run: {type(e).__name__}")
            return "could not run"
        return f"net_worth={r['net_worth']} unknown={r['n_unknown']} backfilled={r['backfilled_months']}"

    if cfg.schedule_analytics:
        step("networth", do_networth)

    def do_coach():
        """Warn only (E6-6, opt-in): the weekly digest / the monthly review through the coach backend. Skipped when nothing
        is new; whatever happens here never makes the daily job fail."""
        from coach.agent import digest as digest_mod
        res = []
        for kind, on in (("weekly", cfg.coach_schedule_weekly), ("monthly", cfg.coach_schedule_monthly)):
            if not on:
                continue
            try:
                r = digest_mod.run_digest(cfg, kind, insecure=insecure, out=lambda *a, **k: None)
            except Exception as e:                                   # noqa: BLE001
                print(f"WARNING coach {kind} digest could not run: {type(e).__name__}")
                res.append(f"{kind}=could not run")
                continue
            if r["status"] == "failed":
                print(f"WARNING coach {kind} digest failed: {str(r.get('reason'))[:160]}")
            res.append(f"{kind}={r['status']}")
        return " ".join(res) or "off"

    if cfg.coach_schedule_weekly or cfg.coach_schedule_monthly:
        step("coach", do_coach)

    def do_alerts():
        """Warn only (E10): evaluate the alert signals, keep the events, send to the channels the user enabled (none by default), then the
        weekly digest when due. Whatever happens here never makes the daily job fail."""
        from coach.alerts import digest as alert_digest, engine as alert_engine
        from coach.analytics import api as analytics_api
        try:
            s = cfg.alert_settings
            if not s.enabled:
                return "off"
            con = connect(cfg, insecure=insecure)
            ds = analytics_api.build_dataset(con, cfg)
            r = alert_engine.run(con, cfg, ds, s=s, transports=alert_transports)
            ev = r["evaluated"]
            sent = ",".join(f"{d['channel']}={d['status']}" for d in r["dispatch"]) or "no channel"
            try:
                dg = alert_digest.run_weekly(con, cfg, ds, s, transports=alert_transports)["status"]
            except Exception as e:                                   # noqa: BLE001
                print(f"WARNING weekly digest could not run: {type(e).__name__}")
                dg = "could not run"
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING alerts could not run: {type(e).__name__}")
            return "could not run"
        if ev["new"] or ev["escalated"]:
            print(f"WARNING alerts: {len(ev['new'])} new, {len(ev['escalated'])} escalated: run `coach alerts list`")
        return f"new={len(ev['new'])} escalated={len(ev['escalated'])} resolved={len(ev['resolved'])} send=[{sent}] digest={dg}"

    step("alerts", do_alerts)

    def do_security():
        """Warn only (E11-2): the security audit once a week (the date of the last one is kept in the log folder). Whatever happens here
        never makes the daily job fail."""
        import json as _json
        from coach import security
        marker = cfg.log_dir / "security-audit.last"
        today = datetime.now().date()
        try:
            if marker.exists() and (today - datetime.fromisoformat(marker.read_text().strip()).date()).days < 7:
                return "not due (weekly)"
            checks = security.audit(cfg, lsof=lambda: None)       # the listening-socket check belongs to the interactive audit
            sm = security.summary(checks)
            ensure_private_dir(cfg.log_dir)
            _write_private(marker, datetime.now().isoformat(timespec="seconds"))
            if sm["critical"] or sm["warn"]:
                print(f"WARNING security audit: {sm['critical']} critical, {sm['warn']} warning(s): run `coach security audit`")
            return f"critical={sm['critical']} warn={sm['warn']}"
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING security audit could not run: {type(e).__name__}")
            return "could not run"

    step("security", do_security)

    def do_eval():
        """Warn only (E12-2): the offline classification evaluation on the gold set once a week (the date of the last one is kept in the log
        folder). No model is called. Whatever happens here never makes the daily job fail."""
        from coach.quality import classify_eval as CE, gold as G
        marker = cfg.log_dir / "eval-classify.last"
        today = datetime.now().date()
        try:
            if marker.exists() and (today - datetime.fromisoformat(marker.read_text().strip()).date()).days < 7:
                return "not due (weekly)"
            con = connect(cfg, insecure=insecure)
            try:
                if not G.counts(con)["total"]:
                    return "skipped (no gold set yet: `coach eval gold bootstrap`)"
                res = CE.run(con, cfg, label="schedule")
            finally:
                con.close()
            _write_private(marker, datetime.now().isoformat(timespec="seconds"))
            for w in res["warnings"]:
                print(f"WARNING eval: {w}")
            s_ = res["summary"]
            pct = lambda x: -1 if x is None else int(round(x * 100))                       # noqa: E731  (the log keeps integers only)
            # classifier (memory off) on merchant-level truth, and the pipeline with memory (a regression test of memory / rules), apart
            return (f"scored={s_['n']} classifier_tx_pct={pct(s_['accuracy_tx'])} classifier_money_pct={pct(s_['accuracy_money'])} "
                    f"pipeline_tx_pct={pct(s_['pipeline_accuracy_tx'])} llm_scored={s_['llm_n']} warnings={len(res['warnings'])}")
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING classification evaluation could not run: {type(e).__name__}")
            return "could not run"

    if cfg.schedule_eval:
        step("eval", do_eval)

    def do_journal_retention():
        """Warn only (E11-1): keep the egress journal for [privacy] egress_journal_days."""
        from coach import egress
        try:
            con = connect(cfg, insecure=insecure)
            try:
                n = egress.prune_journal(con, cfg.privacy_egress_journal_days)
            finally:
                con.close()
        except Exception as e:                                       # noqa: BLE001
            return f"could not run ({type(e).__name__})"
        return f"pruned={n}"

    if cfg.privacy_egress_journal:
        step("egress-journal", do_journal_retention)

    ensure_private_dir(cfg.data_dir)
    ensure_private_dir(cfg.log_dir)
    line = f"{datetime.now().isoformat(timespec='seconds')} schedule run {'FAILED' if failed else 'ok'} | " \
           + " | ".join(parts)
    fd = os.open(cfg.log_dir / "schedule.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(line + "\n")
    os.chmod(cfg.log_dir / "schedule.log", 0o600)
    out(line)
    runlog.end(1 if failed else 0)
    return 1 if failed else 0


# ---------------------------------------------------------------- the loop (containers, Linux: no launchd)

def next_run_after(now: datetime, hhmm: str) -> datetime:
    """The next local time at HH:MM strictly after `now`."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


def loop(cfg: Config, *, insecure: bool = False, run_now: bool = False, clock=datetime.now, sleep=time.sleep, runner=None,
         out=print, max_runs: int | None = None) -> int:
    """`coach schedule loop` (E13-1): the daily job without launchd. Sleeps until the next [schedule] time (local), runs
    :func:`run_daily` in this process, repeats. A failing run is reported and never stops the loop; a restart does not run the missed
    time (a restart must not trigger a sync: the PSD2 limit is a few calls per account and day), `run_now` does it on purpose.
    The sleep is cut into slices of at most a minute so that a stop signal is honoured quickly."""
    runner = runner or (lambda: run_daily(cfg, insecure=insecure))
    runs = 0

    def once():
        nonlocal runs
        code = runner()
        runs += 1
        out(f"schedule loop: run {runs} finished with exit code {code}")

    if run_now:
        once()
    while max_runs is None or runs < max_runs:
        target = next_run_after(clock(), cfg.schedule_time)
        out(f"schedule loop: next run at {target.isoformat(timespec='minutes')}")
        while (remaining := (target - clock()).total_seconds()) > 0:
            sleep(min(remaining, 60))
        once()
    return 0
