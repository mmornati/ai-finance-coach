"""Background jobs of the web app: sync now, and the bank authorisation (connect / reconnect).

Both run in their own thread with their own database connection and use the existing ingest functions unchanged
(daily limits included). Nothing here ever returns a token, a key or the Enable Banking credentials: results carry
statuses and counts; the authorisation URL is the bank's own login page the user must open.
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Callable, Optional

from coach.classify.normalize import normalize_all
from coach.ingest import auth, consent as consent_mod
from coach.ingest.client import EnableBankingClient
from coach.ingest.sync import sync_all


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Job:
    def __init__(self, kind: str):
        self.kind = kind
        self.state = "idle"             # idle | running | done | failed
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.message: Optional[str] = None
        self.results: list = []
        self.log: list[str] = []
        self.url: Optional[str] = None  # connect: the bank's login page
        self.extra: dict = {}

    def to_dict(self) -> dict:
        return {"kind": self.kind, "state": self.state, "started_at": self.started_at,
                "finished_at": self.finished_at, "message": self.message, "results": self.results,
                "log": self.log[-30:], "url": self.url, **self.extra}


class Busy(RuntimeError):
    pass


class Jobs:
    def __init__(self, state, inline: bool = False):
        self.state = state
        self.inline = inline                       # tests: run the job in the calling thread
        self.sync_job = Job("sync")
        self.auth_job = Job("connect")
        self._lock = threading.Lock()

    def _start(self, job: Job, fn: Callable) -> None:
        with self._lock:
            if job.state == "running":
                raise Busy(f"a {job.kind} is already running")
            job.__init__(job.kind)
            job.state, job.started_at = "running", _now()
        if self.inline:
            fn()
        else:
            threading.Thread(target=fn, daemon=True, name=f"coach-{job.kind}").start()

    # ------------------------------------------------------------ sync
    def start_sync(self, account: Optional[str] = None) -> Job:
        cfg = self.state.cfg
        job = self.sync_job

        def run():
            try:
                con = self.state.new_connection()
                try:
                    res = sync_all(con, EnableBankingClient.from_config(cfg), account, False, False,
                                   cfg.sync_daily_limit, out=job.log.append, memory_dir=cfg.memory_dir)
                    job.results = [{k: r.get(k) for k in ("uid", "bank", "status", "new", "pending", "note", "consent")
                                    if r.get(k) is not None} for r in res]
                    n_new = sum(r.get("new", 0) or 0 for r in res)
                    if n_new:
                        # what `coach schedule run` does without an LLM: parse the new rows, pair internal transfers
                        normalize_all(con, cfg.memory_dir)
                        from coach import transfers as transfers_mod
                        transfers_mod.match_transfers(con, cfg.transfer_window_days, auto_link=cfg.transfer_auto_link,
                                                      **transfers_mod.opts(cfg))
                    failed = sum(1 for r in res if r["status"] == "failed")
                    job.state = "failed" if failed and failed == len(res) else "done"
                    job.message = (f"{n_new} new transaction(s); "
                                   f"{sum(1 for r in res if r['status'] == 'skipped')} account(s) skipped (daily limit); "
                                   f"{failed} failed")
                finally:
                    con.close()
            except Exception as e:                      # noqa: BLE001 - reported to the page, never raised
                job.state, job.message = "failed", f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                job.finished_at = _now()
                self.state.touch()
        self._start(job, run)
        return job

    # ------------------------------------------------------------ connect / reconnect
    def start_connect(self, bank: str, country: str, days: int, replaces: Optional[str] = None,
                      replace: bool = False) -> Job:
        cfg = self.state.cfg
        cfg.require_redirect()
        client = EnableBankingClient.from_config(cfg)           # validates the Enable Banking settings now
        job = self.auth_job
        # a clash (second consent for the same bank) is refused before anything starts
        con0 = self.state.new_connection()
        try:
            auth.check_single_consent(con0, bank, country, replace or bool(replaces))
        finally:
            con0.close()

        def run():
            con = self.state.new_connection()
            try:
                def open_url(url: str):                       # the page opens it: the server never launches a browser
                    job.url = url

                code = auth.connect_flow(con, client, cfg, bank, country, days, replaces, no_server=False,
                                         no_browser=False, open_browser=open_url,
                                         out=lambda m="": job.log.append(str(m)))
                job.state = "done" if code == 0 else "failed"
                job.message = ("connected" if code == 0 else "the authorisation did not complete: "
                               + (job.log[-1] if job.log else "see the terminal"))
            except Exception as e:                        # noqa: BLE001
                job.state, job.message = "failed", f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                job.finished_at = _now()
                con.close()
                self.state.touch()
        self._start(job, run)
        return job

    def reconnect_args(self, target: str) -> tuple[str, str, str]:
        """(bank, country, session id) of the consent to renew."""
        con = self.state.new_connection()
        try:
            c = auth.find_session(con, target)
            return c.bank, c.country, c.session_id
        finally:
            con.close()
