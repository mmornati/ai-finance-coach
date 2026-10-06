"""``eval_runs`` (E12-2/3/5): every evaluation run is stored, so that a model, a prompt or a taxonomy change can be compared with the
previous result. Nothing in a run row is a payload: headline numbers and aggregates only (a model comparison keeps per-merchant
verdicts as counts, never descriptors)."""
from __future__ import annotations

import json
from typing import Optional

from coach.db import now_iso

KINDS = ("classify", "models", "coach")


def record(con, kind: str, *, label: Optional[str] = None, backend: Optional[str] = None, model: Optional[str] = None, n: int = 0,
           fingerprint: Optional[str] = None, cost_usd: Optional[float] = None, duration_s: Optional[float] = None,
           summary: Optional[dict] = None, result: Optional[dict] = None, commit: bool = True) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown eval kind {kind!r}")
    cur = con.execute("""INSERT INTO eval_runs(ts, kind, label, backend, model, n, fingerprint, cost_usd, duration_s, summary, result)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                      (now_iso(), kind, label, backend, model, int(n), fingerprint, cost_usd, duration_s,
                       json.dumps(summary or {}, ensure_ascii=False, default=str), json.dumps(result or {}, ensure_ascii=False, default=str)))
    if commit:
        con.commit()
    return cur.lastrowid


COLS = ("id", "ts", "kind", "label", "backend", "model", "n", "fingerprint", "cost_usd", "duration_s", "summary", "result")


def _row(r, full: bool) -> dict:
    d = dict(zip(COLS, r))
    d["summary"] = _loads(d["summary"])
    if d["kind"] == "classify" and "method" not in d["summary"]:
        d["summary"]["method"] = "method_v1"             # E12 v1 mixed the classifier and the memory in one figure: deprecated, not comparable with v2
    d["result"] = _loads(d["result"]) if full else None
    if not full:
        del d["result"]
    return d


def _loads(s):
    try:
        return json.loads(s or "{}")
    except ValueError:
        return {}


def listing(con, kind: Optional[str] = None, limit: int = 20) -> list[dict]:
    where, args = ("WHERE kind=?", (kind,)) if kind else ("", ())
    return [_row(r, False) for r in con.execute(
        f"SELECT {', '.join(COLS)} FROM eval_runs {where} ORDER BY id DESC LIMIT ?", (*args, limit))]


def get(con, run_id: int) -> Optional[dict]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM eval_runs WHERE id=?", (run_id,)).fetchone()
    return _row(r, True) if r else None


def previous(con, kind: str, label: Optional[str] = None, before_id: Optional[int] = None) -> Optional[dict]:
    """The latest earlier run of this kind (and label)."""
    q, a = f"SELECT {', '.join(COLS)} FROM eval_runs WHERE kind=?", [kind]
    if label is not None:
        q, a = q + " AND label=?", a + [label]
    if before_id is not None:
        q, a = q + " AND id<?", a + [before_id]
    r = con.execute(q + " ORDER BY id DESC LIMIT 1", a).fetchone()
    return _row(r, True) if r else None
