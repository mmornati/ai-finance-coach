"""The coach job of the web app (E6-3): one question at a time, run in a background thread through the configured backend,
its events kept in memory for the SSE stream (and for a page that reconnects), cancellable, with a timeout enforced by the
runner. The answer is stored as an insight and the usage in ``llm_usage`` when the run ends."""
from __future__ import annotations

import datetime as dt
import json
import secrets
import shutil
import threading
import time
from typing import Callable, Optional

from coach.agent import insights as I, prompt as P, service
from coach.agent.runner import CoachUnavailable, run_agent

MAX_KEPT = 12
ENDED = ("done", "failed", "cancelled")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class CoachBusy(RuntimeError):
    pass


class CoachJob:
    def __init__(self, question: str, skill: Optional[str] = None):
        self.id = "j_" + secrets.token_hex(5)
        self.question = question
        self.skill = skill
        self.state = "running"                 # running | done | failed | cancelled
        self.finish_reason: Optional[str] = None
        self.started_at, self.finished_at = _now(), None
        self.events: list[tuple[str, dict]] = []
        self.cond = threading.Condition()
        self.cancel = threading.Event()
        self.insight_id: Optional[str] = None
        self.text = ""
        self.log: list = []
        self.compliance: Optional[dict] = None     # E11-5: the AI label and the investment-advice check of the answer (set when it ends)

    def emit(self, event: str, data: dict) -> None:
        if event == "init":                               # the observed claude init summary (counts and short names): for the job log
            self.log.append("init " + json.dumps(data, ensure_ascii=False))
        with self.cond:
            self.events.append((event, data))
            if event == "delta":
                self.text += data.get("text", "")
            self.cond.notify_all()

    def wait_events(self, after: int, timeout: float = 15.0) -> tuple[list[tuple[int, str, dict]], bool]:
        """Events with index >= after (blocking up to `timeout`), and whether the job is over."""
        with self.cond:
            if len(self.events) <= after and self.state not in ENDED:
                self.cond.wait(timeout)
            out = [(i, e, d) for i, (e, d) in enumerate(self.events[after:], start=after)]
            return out, self.state in ENDED and len(self.events) <= after + len(out)

    def snapshot(self) -> dict:
        return {"id": self.id, "state": self.state, "question": self.question, "skill": self.skill, "finish_reason": self.finish_reason,
                "started_at": self.started_at, "finished_at": self.finished_at, "text": self.text,
                "events": len(self.events), "insight_id": self.insight_id, "log": self.log[-20:],
                "ai_generated": True, "compliance": self.compliance}


def availability(cfg) -> tuple[bool, str]:
    """(usable, message) of the configured backend, without calling anything."""
    b = cfg.coach_backend
    from coach import egress
    host = egress.host_of(getattr(cfg, "llm_ollama_url", "")) if b == "ollama" else ""
    ok_policy, _, why = egress.evaluate(f"llm.{b}", {"host": host}, cfg=cfg)
    if not ok_policy:                       # E11-4: the privacy mode refuses this backend
        return False, why
    if b == "claude-code":
        if shutil.which("claude") is None:
            return False, "The `claude` command is not installed or not in PATH: install Claude Code, or set [coach] backend in config.toml."
        return True, ""
    if b == "anthropic-api":
        from coach import secrets as sec
        try:
            ok = bool(sec.lookup("anthropic_api_key")[0])
        except Exception:                                                      # noqa: BLE001
            ok = False
        return (True, "") if ok else (False, "No Anthropic API key: run `uv run coach config set-secret anthropic_api_key`, or choose another [coach] backend.")
    return True, ""


class CoachJobs:
    def __init__(self, state, runner: Callable = run_agent):
        self.state = state
        self.runner = runner
        self.jobs: dict[str, CoachJob] = {}
        self._lock = threading.Lock()
        self.inline = False

    def current(self) -> Optional[CoachJob]:
        return next((j for j in self.jobs.values() if j.state == "running"), None)

    def get(self, jid: str) -> Optional[CoachJob]:
        return self.jobs.get(jid)

    def start(self, question: str, skill: Optional[str] = None) -> CoachJob:
        cfg = self.state.cfg
        ok, msg = availability(cfg)
        job = CoachJob(question.strip(), skill)
        with self._lock:
            if self.current() is not None:
                raise CoachBusy("the coach is already answering a question: wait for it or cancel it")
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_KEPT:
                oldest = next(k for k, j in self.jobs.items() if j.state in ENDED)
                del self.jobs[oldest]
        job.emit("meta", {"job_id": job.id, "configured": ok, "backend": cfg.coach_backend, "model": cfg.coach_model_effective})
        if not ok:
            job.emit("notice", {"code": "coach_not_configured", "message": msg})
            self._end(job, "failed", "not_configured")
            return job
        t = threading.Thread(target=self._work, args=(job,), daemon=True, name=f"coach-{job.id}")
        if self.inline:
            self._work(job)
        else:
            t.start()
        return job

    def cancel(self, jid: str) -> Optional[CoachJob]:
        job = self.jobs.get(jid)
        if job and job.state == "running":
            job.cancel.set()
        return job

    def _end(self, job: CoachJob, state: str, reason: str) -> None:
        job.finish_reason, job.finished_at = reason, _now()
        with job.cond:                              # the last event and the final state change atomically
            job.events.append(("done", {"finish_reason": reason, "job_id": job.id, "insight_id": job.insight_id}))
            job.state = state
            job.cond.notify_all()

    def _work(self, job: CoachJob) -> None:
        cfg = self.state.cfg
        spec = P.SKILL_SPECS.get(job.skill) if job.skill else P.ASK
        try:
            try:
                res = self.runner(cfg, spec, job.question, emit=job.emit, cancel=job.cancel, insecure=self.state.insecure,
                                  session_id=job.id)
            except CoachUnavailable as e:
                job.emit("error", {"code": "coach_unavailable", "message": str(e)})
                self._end(job, "failed", "unavailable")
                return
            con = self.state.new_connection()
            try:
                fin = service.finalize(con, spec, res, question=job.question)
            finally:
                con.close()
            job.insight_id = fin["insight_id"]
            job.compliance = fin["compliance"]
            self.state.touch()
            for ref in res.refs:
                job.emit("citation", {"label": ref, "ref": ref, "kind": "transaction" if ref.startswith("h_") else "series"})
            if res.usage:
                u = res.usage
                job.emit("usage", {"backend": u.backend, "model": u.model, "tokens_in": u.tokens_in, "tokens_out": u.tokens_out,
                                   "cache_read_tokens": u.cache_read_tokens, "cost_usd": u.cost_usd,
                                   "cost_is_estimate": u.cost_is_estimate, "tool_calls": len(res.tool_calls),
                                   "duration_s": u.duration_s})
            job.emit("answer", {"insight_id": fin["insight_id"], "suspicious": res.suspicious, "proposals": res.proposals,
                                "unverified_numbers": res.unverified_numbers, "finish_reason": res.finish_reason,
                                "ai_generated": True, "compliance": fin["compliance"]})
            if res.finish_reason == "cancelled":
                job.emit("notice", {"code": "cancelled", "message": "Cancelled."})
                self._end(job, "cancelled", "cancelled")
            elif res.finish_reason in ("error", "unsafe_tools", "no_init"):
                if res.finish_reason in ("error", "no_init"):
                    job.emit("error", {"code": "coach_error", "message": res.error or "the coach run failed"})
                self._end(job, "failed", res.finish_reason)
            else:
                if res.finish_reason == "timeout":
                    job.emit("notice", {"code": "timeout", "message": "The coach took too long and was stopped; the answer above may be incomplete."})
                elif res.finish_reason == "max_tool_calls":
                    job.emit("notice", {"code": "max_tool_calls", "message": "The coach used its whole tool budget; the answer may be incomplete."})
                elif res.finish_reason == "max_tokens":
                    job.emit("notice", {"code": "max_tokens", "message": "The answer was cut at the token limit."})
                self._end(job, "failed" if res.finish_reason == "timeout" and not res.text else "done", res.finish_reason)
        except Exception as e:                                                  # noqa: BLE001 - reported to the page
            job.emit("error", {"code": "coach_error", "message": f"{type(e).__name__}: {str(e)[:160]}"})
            self._end(job, "failed", "error")
