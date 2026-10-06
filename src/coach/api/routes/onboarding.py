"""Onboarding (E7-11): the checklist of what the coach still does not know, and the two small writes the Memory page has no form
for (the household's country / privacy declarations, and the coach rules of ``preferences.md``).

Every write goes through the memory store (validated, recorded in the history with the source ``ui``) and can be previewed with
``dry_run=true``. Members, loans, contracts, accounts and budgets are edited with the forms that already exist
(``PUT /memory/{kind}/{id}``, ``PATCH /accounts/{uid}``, ``POST /budgets``); this page links to them.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from coach.analytics.common import _plain
from coach.api.deps import get_snapshot, get_state
from coach.api.errors import ApiError
from coach.api.routes._write import run_edit
from coach.api.state import AppState, Snapshot
from coach.skills import onboarding as OB

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding", summary="What the coach still does not know, as an ordered checklist with the next actions")
def onboarding(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    st = OB.onboarding_status(ds, state.store, None, state.cfg, snap.recurring())
    for s in st["steps"]:
        if s["id"] == "accounts":
            for m in s["missing"]:
                m["label"] = ds.label(m["account"])
    st["declared"] = declared(state.store)
    return _plain(st)


MAX_TERMS = 50
LISTS = ("employers", "places", "schools")


class ListChange(BaseModel):
    """Add / remove semantics for one privacy list: the current list is NEVER replaced wholesale."""
    add: list[str] = Field(default_factory=list, max_length=MAX_TERMS)
    remove: list[str] = Field(default_factory=list, max_length=MAX_TERMS)


class HouseholdRequest(BaseModel):
    country: Optional[Literal["FR", "IT"]] = None
    employers: Optional[ListChange] = None
    places: Optional[ListChange] = None
    schools: Optional[ListChange] = None
    confirm_removal: bool = Field(False, description="required to write a change that REMOVES declared terms (they stop being masked)")


def _fold(x: str) -> str:
    return " ".join(x.split()).casefold()


def declared(store) -> dict:
    try:
        hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
    except Exception:                                                     # noqa: BLE001
        hh = {}
    return {k: [x for x in (hh.get(k) or []) if isinstance(x, str)] for k in LISTS}


@router.put("/onboarding/household", summary="Add / remove privacy declarations (employers, places, schools) and set the country; "
                                              "dry_run=true: preview. Removing terms needs confirm_removal=true")
def household(req: HouseholdRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    store = state.store
    cur = declared(store)
    ops: list = []
    if not store.exists("household.yaml"):
        ops.append({"op": "create", "value": {"members": []}})
    if req.country is not None:
        ops.append({"op": "set", "path": "country", "value": req.country})
    added: dict = {}
    removed: dict = {}
    for key in LISTS:
        ch = getattr(req, key)
        if ch is None:
            continue
        drop = {_fold(x) for x in ch.remove if x.strip()}
        kept = [x for x in cur[key] if _fold(x) not in drop]
        removed[key] = [x for x in cur[key] if _fold(x) in drop]
        seen = {_fold(x) for x in kept}
        new = []
        for x in ch.add:
            x = " ".join(x.split())
            if x and _fold(x) not in seen:
                seen.add(_fold(x))
                new.append(x)
        added[key] = new
        merged = kept + new
        if len(merged) > MAX_TERMS:
            raise ApiError(422, "too_many_terms", f"{key}: at most {MAX_TERMS} entries")
        if merged != cur[key]:
            ops.append({"op": "set", "path": key, "value": merged})
    if not any(o["op"] == "set" for o in ops):
        raise ApiError(422, "nothing_to_change", "no change was given (or every term is already declared)")
    gone = {k: v for k, v in removed.items() if v}
    extra = {"added": {k: v for k, v in added.items() if v}, "removed": gone, "removes_privacy_terms": bool(gone)}
    if gone:
        extra["warning"] = ("these terms will no longer be masked for the model: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in gone.items()))
        if not dry_run and not req.confirm_removal:
            raise ApiError(422, "confirm_required", extra["warning"] + ". Send confirm_removal=true to write this change.")
    return run_edit(state, "household.yaml", ops, action="onboarding-household", reason="onboarding", detail=None, dry_run=dry_run,
                    extra=extra)


class PreferencesRequest(BaseModel):
    language: Optional[str] = Field(None, max_length=60)
    tone: Optional[str] = Field(None, max_length=120)
    goals: Optional[str] = Field(None, max_length=300)
    avoid: Optional[str] = Field(None, max_length=300)


@router.post("/onboarding/preferences", summary="Append coach rules to preferences.md; dry_run=true: preview")
def preferences(req: PreferencesRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    lines = [f"- {label}: {v.strip()}" for label, v in (("Language", req.language), ("Tone", req.tone), ("Goals", req.goals),
                                                        ("Never", req.avoid)) if v and v.strip()]
    if not lines:
        raise ApiError(422, "nothing_to_change", "no field was given")
    if not state.store.exists("preferences.md"):
        raise ApiError(409, "missing_file", "preferences.md does not exist: create it with `uv run coach memory append preferences.md ...`")
    return run_edit(state, "preferences.md", [{"op": "append_text", "value": "\n".join(lines) + "\n"}], action="append",
                    reason="onboarding", detail=None, dry_run=dry_run)
