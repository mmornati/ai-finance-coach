"""Traceable insights (E6-7): the ``insights`` table. An insight is text + structured findings + evidence refs + the
prompt / skill + backend + model + usage row + status. Written by the coach runtime and by the finance MCP server's
``add_insight``; read and triaged (read / dismissed / done / snoozed) by the web app."""
from __future__ import annotations

import datetime as dt
import json
import secrets as _secrets
from typing import Optional

from coach import compliance

KINDS = ("digest", "answer", "anomaly-explain", "finding", "review")
STATUSES = ("new", "read", "dismissed", "done", "snoozed")
ID_PREFIX = "cin_"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return ID_PREFIX + _secrets.token_hex(5)


def add(con, *, kind: str, title: str, body: str, findings=None, evidence=None, skill: Optional[str] = None,
        backend: Optional[str] = None, model: Optional[str] = None, usage_ref: Optional[int] = None,
        session_id: Optional[str] = None, status: str = "new", unverified_numbers=None, suspicious: bool = False,
        question: Optional[str] = None, data_through: Optional[str] = None, redacted_content: bool = False,
        ai_generated: Optional[bool] = None) -> str:
    """Store an insight. E11-5: a text written by a model (the default; backend 'local' / 'code' are deterministic) is labelled
    AI-generated and checked by coach.compliance: a flagged text keeps its flag codes and is recorded in compliance_events."""
    if status not in STATUSES:
        raise ValueError(f"bad status {status!r}")
    if ai_generated is None:
        ai_generated = backend not in ("local", "code")
    flags = compliance.check(f"{title}\n{body}") if ai_generated else []
    iid = new_id()
    con.execute("""INSERT INTO insights(id, created, kind, title, body, findings, evidence, skill, backend, model, usage_ref,
                   session_id, status, unverified_numbers, suspicious, question, data_through, redacted_content,
                   ai_generated, compliance)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, _now(), kind, title[:200], body, json.dumps(findings or [], ensure_ascii=False),
                 json.dumps(list(evidence or []), ensure_ascii=False), skill, backend, model, usage_ref, session_id,
                 status, json.dumps(list(unverified_numbers or []), ensure_ascii=False), int(bool(suspicious)),
                 question, data_through, int(bool(redacted_content)), int(bool(ai_generated)),
                 json.dumps(compliance.codes(flags))))
    con.commit()
    if flags:
        compliance.record(con, source="insight", flags=flags, insight_id=iid, lang=compliance.detect_lang(body))
    return iid


COLS = ("id", "created", "kind", "title", "body", "findings", "evidence", "skill", "backend", "model", "usage_ref",
        "session_id", "status", "snoozed_until", "unverified_numbers", "suspicious", "question", "data_through", "redacted_content",
        "ai_generated", "compliance")


def _row(r) -> dict:
    d = dict(zip(COLS, r))
    for k in ("findings", "evidence", "unverified_numbers", "compliance"):
        try:
            d[k] = json.loads(d[k] or "[]")
        except ValueError:
            d[k] = []
    d["suspicious"] = bool(d["suspicious"])
    d["redacted_content"] = bool(d["redacted_content"])
    d["ai_generated"] = bool(d["ai_generated"])
    lang = compliance.detect_lang(d.get("body") or "")
    d["ai_label"] = compliance.label(lang) if d["ai_generated"] else None
    d["compliance_banner"] = compliance.banner(d["compliance"], lang) if d["compliance"] else None
    return d


def get(con, iid: str) -> Optional[dict]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM insights WHERE id=?", (iid,)).fetchone()
    return _row(r) if r else None


def listing(con, *, today: Optional[dt.date] = None, include_hidden: bool = False, kind: Optional[str] = None,
            limit: int = 100) -> tuple[list[dict], int]:
    """(visible insights newest first, number hidden). Hidden = dismissed, done, or snoozed until a future day."""
    today = (today or dt.date.today()).isoformat()
    rows = [_row(r) for r in con.execute(f"SELECT {', '.join(COLS)} FROM insights ORDER BY created DESC LIMIT 500")]
    shown, hidden = [], 0
    for d in rows:
        if kind and d["kind"] != kind:
            continue
        snoozed = d["status"] == "snoozed" and (d["snoozed_until"] or "") >= today
        gone = d["status"] in ("dismissed", "done") or snoozed
        if gone and not include_hidden:
            hidden += 1
            continue
        shown.append(d)
    return shown[:limit], hidden


def set_status(con, iid: str, status: str, snoozed_until: Optional[str] = None) -> bool:
    if status not in STATUSES:
        raise ValueError(f"bad status {status!r}")
    cur = con.execute("UPDATE insights SET status=?, snoozed_until=? WHERE id=?", (status, snoozed_until, iid))
    con.commit()
    return cur.rowcount > 0


def attach_run(con, session_id: str, *, backend: str, model: str, usage_ref: Optional[int], skill: Optional[str] = None,
               suspicious: bool = False) -> int:
    """After a run: stamp the insights its tool session created with the backend, model and usage row that produced them
    (the MCP server process does not know them)."""
    cur = con.execute("""UPDATE insights SET backend=COALESCE(?, backend), model=COALESCE(?, model), usage_ref=?,
                         skill=COALESCE(skill, ?), suspicious=MAX(suspicious, ?) WHERE session_id=?""",
                      (backend, model, usage_ref, skill, int(suspicious), session_id))
    con.commit()
    return cur.rowcount


def last_digest(con, skill: str) -> Optional[dict]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM insights WHERE skill=? AND kind='digest' ORDER BY created DESC LIMIT 1",
                    (skill,)).fetchone()
    return _row(r) if r else None
