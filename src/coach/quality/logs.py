"""Structured run logs (E12 observability): one JSON line per event of a scheduled run, in ``<data_dir>/logs/runs.jsonl``.

    {"ts": "...", "run": "20261005-073000-ab12", "event": "run_start", "version": "0.1.0"}
    {"ts": "...", "run": "...", "event": "step", "step": "sync", "status": "ok", "ms": 1234, "counts": {"new": 12, "failed": 0}}
    {"ts": "...", "run": "...", "event": "run_end", "outcome": "ok", "exit": 0, "ms": 5678, "steps": {"ok": 9, "warn": 1}}

What a line may hold is a CLOSED list: the run id, the step name (from a fixed list of steps), a status, a duration, integers named like
``new`` / ``failed`` and, for an exception, its CLASS name. Never a message, a description, a merchant, an amount, a name, a path or a
bank: an exception text can quote the data that failed, so it is not kept. ``tests/test_quality_logs.py`` runs a scheduled job on synthetic
data full of sentinels and greps every file of the log folder.

Rotation: every log of the folder is rotated by size (``<name>.1`` ... ``<name>.<keep>``) and rotated files older than ``max_age_days``
are deleted. The files this process appends to (``runs.jsonl``, ``schedule.log``, ``coach-init.log``) rotate at the START of a run; launchd's
stdout / stderr files are held open by THIS process for the whole run (renaming them at the start would send the rest of the run's output
to the renamed file), so they rotate at the END of the run and the next run opens a fresh file.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets as _secrets
import time
from pathlib import Path
from typing import Iterator, Optional

LOG_NAME = "runs.jsonl"
# the logs of the folder and when each is rotated (bytes): the JSON lines, the one-line-per-run summary, launchd's stdout / stderr, the coach init log
ROTATED = ("runs.jsonl", "schedule.log", "schedule.out.log", "schedule.err.log", "coach-init.log")
LAUNCHD_LOGS = ("schedule.out.log", "schedule.err.log")           # open in the running process: rotated when the run is over
STEPS = ("sync", "consents", "normalize", "transfers", "classify", "memory", "analytics", "networth", "coach", "alerts", "security",
         "egress-journal", "eval", "logs")
STATUSES = ("ok", "warn", "error", "skipped")
_KEY = re.compile(r"[a-z][a-z_]{0,23}")
_COUNT_RE = re.compile(r"\b([a-z][a-z_]{0,23})=(-?\d{1,9})\b")


def new_run_id(now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{_secrets.token_hex(2)}"


def counts_of(result: str) -> dict:
    """The integers of a step result like ``accounts=3 new=2 failed=0``: the only part of a result text that is logged."""
    out: dict = {}
    for k, v in _COUNT_RE.findall(result or ""):
        out.setdefault(k, int(v))
    if m := re.fullmatch(r"\s*(\d{1,9}) tx\s*", result or ""):          # the normalize step's result ("4047 tx")
        out["tx"] = int(m.group(1))
    return out


def status_of(result: str) -> str:
    """ok | warn | error | skipped, from the step's result text. An exception is `error` (the caller decides that); a result is `error`
    when something it counted failed (``failed=N``), `warn` when it counted problems it only reports (``errors=`` / ``warnings=`` /
    ``expired_or_revoked=``) or could not run, `skipped` when it did nothing on purpose."""
    r = (result or "").lower()
    if "could not run" in r:
        return "warn"
    if r.startswith(("skipped", "off", "not due")) or r == "off":
        return "skipped"
    c = counts_of(result)
    if c.get("failed", 0) > 0:
        return "error"
    if any(c.get(k, 0) > 0 for k in ("errors", "warnings", "expired_or_revoked", "critical", "warn")):
        return "warn"
    return "ok"


def _clean_counts(counts: Optional[dict]) -> dict:
    return {k: int(v) for k, v in (counts or {}).items() if isinstance(k, str) and _KEY.fullmatch(k) and isinstance(v, (int, float))
            and not isinstance(v, bool)}


class RunLog:
    """Appends the events of ONE run. Never raises: a log that cannot be written must not make a run fail."""

    def __init__(self, log_dir: Path, run_id: Optional[str] = None, *, max_kb: int = 512, keep: int = 5, max_age_days: int = 90,
                 version: str = ""):
        self.dir, self.run_id = Path(log_dir), run_id or new_run_id()
        self.max_bytes, self.keep, self.max_age_days, self.version = max_kb * 1024, keep, max_age_days, version
        self.t0 = time.monotonic()
        self.statuses: dict[str, int] = {}

    @property
    def path(self) -> Path:
        return self.dir / LOG_NAME

    def _write(self, event: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            rotate(self.path, self.max_bytes, self.keep)
            line = json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "run": self.run_id, **event}, ensure_ascii=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def start(self) -> list[str]:
        """Rotate the logs this process appends to, delete the old rotated files, write the first line. Returns what was rotated / deleted."""
        done = maintain(self.dir, self.max_bytes, self.keep, self.max_age_days, skip=LAUNCHD_LOGS)
        self._write({"event": "run_start", "version": self.version})
        return done

    def step(self, name: str, status: str, ms: int, counts: Optional[dict] = None, error: Optional[str] = None) -> None:
        name = name if name in STEPS else "other"
        status = status if status in STATUSES else "ok"
        self.statuses[status] = self.statuses.get(status, 0) + 1
        ev: dict = {"event": "step", "step": name, "status": status, "ms": int(ms), "counts": _clean_counts(counts)}
        if error and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", error):
            ev["error"] = error
        self._write(ev)

    def end(self, exit_code: int) -> None:
        self._write({"event": "run_end", "outcome": "failed" if exit_code else "ok", "exit": int(exit_code),
                     "ms": int((time.monotonic() - self.t0) * 1000), "steps": dict(sorted(self.statuses.items()))})
        maintain(self.dir, self.max_bytes, self.keep, self.max_age_days, only=LAUNCHD_LOGS)


# ---------------------------------------------------------------- rotation

def rotate(path: Path, max_bytes: int, keep: int) -> bool:
    """path -> path.1 -> ... -> path.<keep> when `path` is at least `max_bytes` (the oldest is deleted). True if it rotated."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return False
    except OSError:
        return False
    for i in range(keep, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        dst = path.with_name(f"{path.name}.{i}")
        if src.exists():
            if i == keep and dst.exists():
                dst.unlink()
            os.replace(src, dst)
    return True


def prune_old(log_dir: Path, max_age_days: int, now: Optional[float] = None) -> list[str]:
    """Delete ROTATED files (``<log>.<n>``) older than `max_age_days`. The live files are never deleted by age."""
    now = now if now is not None else time.time()
    gone = []
    for base in ROTATED:
        for p in log_dir.glob(base + ".*"):
            if re.fullmatch(re.escape(base) + r"\.\d+", p.name):
                try:
                    if now - p.stat().st_mtime > max_age_days * 86400:
                        p.unlink()
                        gone.append(p.name)
                except OSError:
                    pass
    return gone


def maintain(log_dir: Path, max_bytes: int, keep: int, max_age_days: int, now: Optional[float] = None, *, only=None, skip=()) -> list[str]:
    """Rotate by size, delete old rotated files. `only` / `skip`: restrict the logs looked at. Returns short descriptions (names only)."""
    out = []
    if not Path(log_dir).is_dir():
        return out
    for name in ROTATED:
        if (only is not None and name not in only) or name in skip:
            continue
        # the one-line summaries and launchd's files are small: they rotate at a quarter of the JSON log's size (never below 64 KiB)
        limit = max_bytes if name == LOG_NAME else max(max_bytes // 4, 64 * 1024)
        if rotate(Path(log_dir) / name, limit, keep):
            out.append(f"rotated {name}")
    out += [f"deleted {n}" for n in prune_old(Path(log_dir), max_age_days, now)]
    return out


# ---------------------------------------------------------------- reading

def log_files(log_dir: Path) -> list[Path]:
    """Oldest first: runs.jsonl.5 ... runs.jsonl.1, runs.jsonl."""
    d = Path(log_dir)
    rotated = sorted((p for p in d.glob(LOG_NAME + ".*") if re.fullmatch(re.escape(LOG_NAME) + r"\.\d+", p.name)),
                     key=lambda p: -int(p.name.rsplit(".", 1)[1]))
    return rotated + ([d / LOG_NAME] if (d / LOG_NAME).exists() else [])


def read_events(log_dir: Path, run: Optional[str] = None) -> Iterator[dict]:
    for p in log_files(log_dir):
        try:
            text = p.read_text()
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if isinstance(ev, dict) and (run is None or str(ev.get("run", "")).startswith(run)):
                yield ev


def runs_summary(events) -> list[dict]:
    """One dict per run, oldest first: {run, started, ended, outcome, duration_s, steps: [{step, status, ms, counts}], failed}."""
    by: dict[str, dict] = {}
    for ev in events:
        r = by.setdefault(ev.get("run", "?"), {"run": ev.get("run", "?"), "started": None, "ended": None, "outcome": "running",
                                                  "duration_s": None, "exit": None, "steps": []})
        if ev.get("event") == "run_start":
            r["started"] = ev.get("ts")
        elif ev.get("event") == "step":
            r["steps"].append({k: ev.get(k) for k in ("step", "status", "ms", "counts", "error")})
        elif ev.get("event") == "run_end":
            r.update(ended=ev.get("ts"), outcome=ev.get("outcome"), exit=ev.get("exit"), duration_s=round((ev.get("ms") or 0) / 1000, 1))
    out = list(by.values())
    for r in out:
        r["failed"] = [s["step"] for s in r["steps"] if s["status"] == "error"]
        r["warned"] = [s["step"] for s in r["steps"] if s["status"] == "warn"]
    return out


def last_run(log_dir: Path) -> Optional[dict]:
    runs = runs_summary(read_events(log_dir))
    return runs[-1] if runs else None


def format_event(ev: dict) -> str:
    if ev.get("event") == "step":
        c = " ".join(f"{k}={v}" for k, v in (ev.get("counts") or {}).items())
        return (f"{ev.get('ts', '')}  {ev.get('run', '')}  {ev.get('step', ''):<14} {ev.get('status', ''):<8} {ev.get('ms', 0):>7} ms  {c}"
                + (f"  error={ev['error']}" if ev.get("error") else ""))
    if ev.get("event") == "run_end":
        return (f"{ev.get('ts', '')}  {ev.get('run', '')}  RUN END {ev.get('outcome')} (exit {ev.get('exit')}) in {ev.get('ms', 0)} ms "
                f"steps {ev.get('steps')}")
    return f"{ev.get('ts', '')}  {ev.get('run', '')}  {ev.get('event', '')}" + (f" v{ev['version']}" if ev.get("version") else "")
