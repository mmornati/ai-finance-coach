"""After a run: log the usage (``llm_usage``), stamp the insights the tool session created, store the answer / digest as
an insight with its evidence and checks (E6-3, E6-7)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach import compliance
from coach.agent import insights as I
from coach.agent.prompt import PromptSpec
from coach.agent.runner import RunResult
from coach.classify.backends import record_usage

STORABLE = ("stop", "max_tokens", "max_tool_calls")


def finalize(con, spec: PromptSpec, res: RunResult, *, question: Optional[str] = None, data_through: Optional[str] = None,
             title: Optional[str] = None, today: Optional[dt.date] = None) -> dict:
    usage_ref = None
    if res.usage is not None and (res.usage.tokens_in or res.usage.tokens_out or res.usage.cost_usd):
        record_usage(con, res.usage)
        con.commit()
        usage_ref = con.execute("SELECT MAX(id) FROM llm_usage").fetchone()[0]
    I.attach_run(con, res.session_id, backend=res.backend, model=res.model, usage_ref=usage_ref, skill=spec.id,
                 suspicious=res.suspicious)
    iid = None
    if spec.stores and res.text and res.finish_reason in STORABLE:
        today = today or dt.date.today()
        iid = I.add(con, kind=spec.kind, title=title or ((question or spec.title).strip().splitlines() or [spec.title])[0][:120]
                    or spec.title, body=res.text, evidence=res.refs, skill=spec.id, backend=res.backend, model=res.model,
                    usage_ref=usage_ref, session_id=res.session_id, status="read" if spec.kind == "answer" else "new",
                    unverified_numbers=res.unverified_numbers, suspicious=res.suspicious, question=question,
                    data_through=data_through)
    # E11-5: the AI label and the investment-advice check of what the user will read (also when nothing was stored as an insight)
    assessed = compliance.assess(res.text or "")
    if assessed["flagged"] and iid is None:
        compliance.record(con, source="answer", flags=compliance.check(res.text or ""), lang=assessed["lang"])
    return {"insight_id": iid, "usage_ref": usage_ref, "compliance": assessed}
