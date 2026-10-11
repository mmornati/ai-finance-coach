"""The AI suggestions of the review queue (E2-9): one background thread per merchant, polled by the page. A web search through `claude -p`
takes from seconds to minutes, so a request never waits for it. Nothing is written but the usage of the call (`llm_usage`): the suggestion
is a proposal the user accepts with the queue's own "Set"."""
from __future__ import annotations

import datetime as dt
import secrets
import threading
from typing import Optional

from coach import disclaimers
from coach.classify import suggest as S
from coach.classify.backends import LLMBackend, get_backend, record_usage

MAX_RUNNING = 3
MAX_KEPT = 40


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class SuggestBusy(RuntimeError):
    pass


class SuggestJob:
    def __init__(self, key: str):
        self.id = "s_" + secrets.token_hex(5)
        self.key = key
        self.state = "running"                 # running | done | failed
        self.mode: Optional[str] = None
        self.started_at, self.finished_at = _now(), None
        self.suggestion: Optional[dict] = None
        self.error: Optional[dict] = None      # {code, message}

    def to_dict(self) -> dict:
        out = {"id": self.id, "key": self.key, "state": self.state, "mode": self.mode, "started_at": self.started_at,
               "finished_at": self.finished_at, "suggestion": self.suggestion, "error": self.error}
        if self.suggestion is not None:        # E11-5: what a model wrote carries the AI label
            out.update(ai_generated=True, ai_label=disclaimers.get("ai_label"), ai_label_short=disclaimers.get("ai_label_short"))
        return out


class SuggestJobs:
    def __init__(self, state, inline: bool = False):
        self.state = state
        self.inline = inline                   # tests: run in the calling thread
        self.backend: Optional[LLMBackend] = None     # tests: a fake backend; None = the configured one
        self.jobs: dict[str, SuggestJob] = {}
        self._lock = threading.Lock()

    def get(self, jid: str) -> Optional[SuggestJob]:
        return self.jobs.get(jid)

    def start(self, key: str) -> SuggestJob:
        job = SuggestJob(key)
        with self._lock:
            running = [j for j in self.jobs.values() if j.state == "running"]
            same = next((j for j in running if j.key == key), None)
            if same is not None:
                return same                    # a second click on the same row: the same job
            if len(running) >= MAX_RUNNING:
                raise SuggestBusy(f"{MAX_RUNNING} suggestions are already running: wait for one to finish")
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_KEPT:
                oldest = next((k for k, j in self.jobs.items() if j.state != "running"), None)
                if oldest is None:
                    break
                del self.jobs[oldest]
        if self.inline:
            self._work(job)
        else:
            threading.Thread(target=self._work, args=(job,), daemon=True, name=f"coach-{job.id}").start()
        return job

    def _work(self, job: SuggestJob) -> None:
        cfg = self.state.cfg
        try:
            con = self.state.new_connection()
            try:
                backend = self.backend
                p = S.plan(con, cfg, job.key, backend)
            finally:
                con.close()
            job.mode = p.mode
            suggestion, usage = S.ask(p, backend or get_backend(cfg))
            con = self.state.new_connection()
            try:
                record_usage(con, usage)
                con.commit()
            finally:
                con.close()
            if suggestion is None:
                job.state, job.error = "failed", {"code": "unusable_answer", "message": "the model's answer could not be used: try again"}
            else:
                job.state, job.suggestion = "done", suggestion
        except S.SuggestRefused as e:
            job.state, job.error = "failed", {"code": e.code, "message": str(e)}
        except Exception as e:                  # noqa: BLE001 - reported to the page, never raised (and never the prompt: see safe_diagnostic)
            job.state, job.error = "failed", {"code": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"}
        finally:
            job.finished_at = _now()
            self.state.touch()
