"""Shared helpers of the routes that write memory through the store (always ``source="ui"``)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.api.state import AppState, ui_source


def edit_out(state: AppState, res, dry_run: bool, extra: Optional[dict] = None) -> dict:
    """The JSON of an :class:`~coach.memory.store.EditResult`: a preview (dry run) or what was written."""
    if not dry_run and res.changed:
        state.touch()
    out = {"dry_run": dry_run, "changed": res.changed, "diff": res.diff,
           "change_id": getattr(res, "change_id", None),
           "warnings": [i.message for i in res.issues if i.level != "error"]}
    if extra:
        out.update(extra)
    return out


def run_edit(state: AppState, rel: str, ops: list, *, action: str, reason: Optional[str], detail: Optional[str],
             dry_run: bool, extra: Optional[dict] = None, **kw) -> dict:
    res = state.store.edit(rel, ops, action=action, reason=reason, source=ui_source(), dry_run=dry_run, detail=detail, **kw)
    return edit_out(state, res, dry_run, extra)


def num(v: float):
    """Whole amounts are written as integers (house style of the CLI)."""
    return int(v) if float(v).is_integer() else v


DATE_FIELDS = {"start_date", "end_date", "outstanding_as_of", "as_of", "opened", "purchase_date", "renewal",
               "commitment_end", "start", "end", "target_date", "date_from", "date_to", "last_used", "decided_on"}


def to_dates(fields: dict) -> dict:
    """ISO strings of date fields become dates, so they are written unquoted (YAML dates)."""
    out = {}
    for k, v in fields.items():
        if isinstance(v, dict):
            out[k] = to_dates(v)
        elif k in DATE_FIELDS and isinstance(v, str) and v:
            try:
                out[k] = dt.date.fromisoformat(v)
            except ValueError:
                from coach.api.errors import ApiError
                raise ApiError(422, "bad_date", f"{k}: {v!r} is not a date (use YYYY-MM-DD)") from None
        elif k in DATE_FIELDS and v == "":
            out[k] = None
        elif k in DATE_FIELDS and v is not None:
            from coach.api.errors import ApiError
            raise ApiError(422, "bad_date", f"{k}: a date is expected (YYYY-MM-DD)")
        else:
            out[k] = v
    return out
