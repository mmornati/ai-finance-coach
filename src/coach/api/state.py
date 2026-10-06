"""Shared state of the web app: one database connection (read-only unless a write is explicitly opened), one memory
store (source ``ui``), a snapshot cache of the analytics dataset and the small UI-state file (snoozed insights)."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import sqlcipher3

from coach import db as db_mod
from coach.analytics import api as analytics_api
from coach.config import Config
from coach.memory.store import MemoryStore

UI_SOURCE = "ui"
# E14-8: the login that makes the current web request ("" / None = the owner login); set by the guard middleware. A memory write made from
# the web app is recorded in the history with the source `ui:<login>`.
CURRENT_ACTOR: ContextVar = ContextVar("coach_actor", default=None)


def ui_source() -> str:
    a = CURRENT_ACTOR.get()
    return f"{UI_SOURCE}:{a}" if a else UI_SOURCE


class UiMemoryStore(MemoryStore):
    """The memory store of the web app: its history source is `ui` or `ui:<login>`, whoever the current request belongs to."""

    @property
    def source(self) -> str:
        return ui_source()

    @source.setter
    def source(self, value) -> None:                       # MemoryStore.__init__ assigns it; the value is derived per request
        pass
# what the shared read connection may do outside AppState.write(): reads, SQL functions, transaction control and a few
# value-less read pragmas. Everything else (INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, ATTACH, PRAGMA ... = value) is denied
# by SQLite itself, whatever code runs on the connection.
_READ_ACTIONS = {sqlcipher3.SQLITE_SELECT, sqlcipher3.SQLITE_READ, sqlcipher3.SQLITE_FUNCTION, sqlcipher3.SQLITE_RECURSIVE,
                 sqlcipher3.SQLITE_TRANSACTION, sqlcipher3.SQLITE_SAVEPOINT}
_READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list", "database_list", "busy_timeout",
                 "query_only", "user_version", "schema_version"}
STATE_FILE = "ui-state.json"


@dataclass
class Snapshot:
    ds: Any
    token: tuple
    memo: dict = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def once(self, key, fn: Callable):
        """Compute an expensive analytics result once per snapshot (thread-safe)."""
        with self.lock:
            if key not in self.memo:
                self.memo[key] = fn()
            return self.memo[key]

    def view(self, member: str) -> "Snapshot":
        """The same snapshot seen as one member (E14-4): the dataset's member view with its own memo, built once per snapshot."""
        with self.lock:
            key = ("view", member)
            if key not in self.memo:
                self.memo[key] = Snapshot(self.ds.member_view(member), (*self.token, member))
            return self.memo[key]

    def recurring(self):
        from coach.analytics import recurring
        return self.once("recurring", lambda: recurring.detect_recurring(self.ds))


class UiState:
    """``<data_dir>/ui-state.json`` (0600): which insight cards the user snoozed or dismissed. Presentation state only;
    anomalies and price changes are dismissed through their own functions (stored in the database)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def snoozed(self, today: dt.date) -> dict[str, str]:
        with self._lock:
            return {k: v for k, v in (self._load().get("snoozed") or {}).items() if v >= today.isoformat()}

    def dismissed(self) -> set[str]:
        with self._lock:
            return set(self._load().get("dismissed") or [])

    def snooze(self, insight_id: str, until: dt.date) -> None:
        with self._lock:
            d = self._load()
            d.setdefault("snoozed", {})[insight_id] = until.isoformat()
            self._save(d)

    def unsnooze(self, insight_id: str) -> None:
        with self._lock:
            d = self._load()
            (d.get("snoozed") or {}).pop(insight_id, None)
            d["dismissed"] = [x for x in d.get("dismissed") or [] if x != insight_id]
            self._save(d)

    def dismiss(self, insight_id: str) -> None:
        with self._lock:
            d = self._load()
            ids = set(d.get("dismissed") or [])
            ids.add(insight_id)
            d["dismissed"] = sorted(ids)
            self._save(d)


class AppState:
    def __init__(self, cfg: Config, *, insecure: bool = False, con=None):
        self.cfg = cfg
        self.insecure = insecure or cfg.insecure_plaintext_db
        self.lock = threading.RLock()
        self._con = con if con is not None else db_mod.connect(cfg, insecure=self.insecure, shared=True)
        self._writing = False
        self._con2 = None
        self._heavy_lock = threading.RLock()
        self._con.execute("PRAGMA busy_timeout=8000")
        self._con.execute("PRAGMA query_only=ON")
        self._con.set_authorizer(self._authorize)
        self.store = UiMemoryStore(cfg.memory_dir, history=cfg.memory_history, source=UI_SOURCE)
        self.version = 0
        self._snap: Snapshot | None = None
        self._snap_lock = threading.RLock()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.ui = UiState(cfg.data_dir / STATE_FILE)
        self.jobs = None            # set by the app factory (coach.api.jobs.Jobs)
        self.coach_jobs = None      # set by the app factory (coach.api.coachjobs.CoachJobs)
        self.clock = dt.date.today  # tests pin the date


    # ------------------------------------------------------------ database
    def _authorize(self, action, arg1, arg2, db_name, source):
        if self._writing or action in _READ_ACTIONS:
            return sqlcipher3.SQLITE_OK
        if action == sqlcipher3.SQLITE_PRAGMA and arg2 is None and (arg1 or "").lower() in _READ_PRAGMAS:
            return sqlcipher3.SQLITE_OK
        return sqlcipher3.SQLITE_DENY

    @contextmanager
    def read(self):
        with self.lock:
            yield self._con

    @contextmanager
    def heavy(self):
        """A second, permanently read-only connection for the slow reads (memory check, review queue, annotation matching),
        so that they do not hold up the quick requests that share the main connection."""
        with self._heavy_lock:
            if self._con2 is None:
                con = db_mod.connect(self.cfg, insecure=self.insecure, shared=True, migrate=False)
                con.execute("PRAGMA busy_timeout=8000")
                con.execute("PRAGMA query_only=ON")
                con.set_authorizer(lambda action, a1, a2, db, src: sqlcipher3.SQLITE_OK if action in _READ_ACTIONS
                                   or (action == sqlcipher3.SQLITE_PRAGMA and a2 is None and (a1 or "").lower() in _READ_PRAGMAS)
                                   else sqlcipher3.SQLITE_DENY)
                self._con2 = con
            yield self._con2

    @contextmanager
    def write(self, quiet: bool = False):
        """The one door to a write: the connection is read-only (query_only + an authorizer) except inside this block.
        `quiet`: nothing is kept (a preview that rolls back), so the dataset cache is not invalidated."""
        with self.lock:
            self._writing = True                       # the authorizer lets writes through only in here
            try:
                self._con.execute("PRAGMA query_only=OFF")
                yield self._con
            except BaseException:
                if self._con.in_transaction:
                    self._con.rollback()
                raise
            finally:
                try:
                    self._con.execute("PRAGMA query_only=ON")
                finally:
                    self._writing = False
                    if not quiet:
                        self.touch()

    def new_connection(self):
        """A private connection for a background job (the shared one stays read-only for the UI)."""
        return db_mod.connect(self.cfg, insecure=self.insecure, migrate=False)

    def touch(self) -> None:
        """Something was written (database or memory): the next request rebuilds the snapshot."""
        self.version += 1

    # ------------------------------------------------------------ snapshot
    def _stat(self, p: Path):
        try:
            s = p.stat()
            return (str(p), s.st_mtime_ns, s.st_size)
        except OSError:
            return (str(p), 0, 0)

    def fingerprint(self) -> tuple:
        cfg = self.cfg
        parts: list = [self.version, self.clock().isoformat(), self._stat(cfg.db_path),
                       self._stat(cfg.db_path.with_name(cfg.db_path.name + "-wal"))]
        mem = cfg.memory_dir
        if mem.is_dir():
            for sub in ("", "liabilities", "contracts"):
                d = mem / sub if sub else mem
                if d.is_dir():
                    for p in sorted(d.iterdir()):
                        if p.is_file() and p.suffix in (".yaml", ".md"):
                            parts.append(self._stat(p))
        from coach.classify import rules
        for f in (rules.taxonomy_file, rules.rules_file):
            try:
                parts.append(self._stat(f()))
            except Exception:                                  # noqa: BLE001
                pass
        return tuple(parts)

    def memory_check(self):
        """(issues, summary) of `coach memory check`, computed once per dataset snapshot (it takes about a second)."""
        from coach.memory import check as check_mod

        def run():
            with self.heavy() as con:
                issues = check_mod.run_check(self.store, con, stale_months=self.cfg.memory_stale_months,
                                             asset_stale_months=self.cfg.memory_asset_stale_months, cfg=self.cfg)
            return issues, check_mod.summarize(issues)
        return self.snapshot().once("memory_check", run)

    def snapshot(self) -> Snapshot:
        tok = self.fingerprint()
        with self._snap_lock:
            if self._snap is not None and self._snap.token == tok:
                return self._snap
            from coach.classify import rules
            rules.reload_taxonomy()                            # a taxonomy / rules edit made elsewhere
            with self.read() as con:
                ds = analytics_api.build_dataset(con, self.cfg, self.clock())
            self._snap = Snapshot(ds, tok)
            return self._snap
