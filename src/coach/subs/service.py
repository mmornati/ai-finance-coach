"""Glue shared by the CLI (``coach subs``), the web routes and the MCP tools: load the bundle, resolve a reference to an inventory
row, build the edits of a contract (usage, drafts, contact) and render a letter. Pure functions over a database connection and a
memory store; nothing here sends anything anywhere."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from coach.analytics import api as analytics_api, pricechanges
from coach.analytics.recurring import detect_recurring
from coach.memory.store import MemoryStore
from coach.skills import cancel as C
from coach.subs import alternatives as A, decisions as DEC, inventory as INV, letters as L, usage as U


class RefError(ValueError):
    pass


@dataclass
class Bundle:
    ds: object
    rec: object
    pcs: object
    alts: list
    decisions: list
    inv: dict
    country: str


def load_bundle(con, cfg, today: Optional[dt.date] = None, *, ds=None, rec=None, store=None, include_ended: bool = False,
                series_filter=None) -> Bundle:
    ds = ds if ds is not None else analytics_api.build_dataset(con, cfg, today)
    rec = rec if rec is not None else detect_recurring(ds)
    try:
        dism = pricechanges.dismissed_ids(con)
    except Exception:                                                     # noqa: BLE001
        dism = frozenset()
    pcs = pricechanges.price_changes(ds, recurring=rec, dismissed=dism)
    try:
        alts = A.load(con)
        decs = DEC.load(con, states=("confirmed", "proposed"))
    except Exception:                                                     # noqa: BLE001 - migration 0014/0015 pending
        alts, decs = [], []
    store = store or MemoryStore(cfg.memory_dir, history=False)
    asked = frozenset(q.key for q in store.questions() if q.key)
    country = country_of(store, ds)
    inv = INV.build(ds, rec, pcs, country=country, alternatives=alts, decisions=decs, asked=asked, include_ended=include_ended,
                    series_filter=series_filter)
    return Bundle(ds, rec, pcs, alts, decs, inv, country)


def country_of(store, ds=None) -> str:
    if ds is not None and ds.memory.country:
        return ds.memory.country
    try:
        c = (store.load_plain("household.yaml") if store.exists("household.yaml") else {}).get("country")
    except Exception:                                                     # noqa: BLE001
        c = None
    return c.upper() if isinstance(c, str) and c.upper() in ("FR", "IT") else "FR"


def resolve(inv: dict, ref: str, *, allow_name: bool = True) -> dict:
    """An inventory row from ``rec_...``, ``contract:<id>``, a contract id, or (``allow_name``) a unique name fragment. The model
    path passes ``allow_name=False``: a name search would be an oracle on the real provider names."""
    rows = inv["rows"]
    ref = (ref or "").strip()
    if not allow_name:                                      # the model path: only the exact `ref` an inventory returned
        for r in rows:
            if ref == r["ref"]:
                return r
        raise RefError("unknown subscription ref")
    for r in rows:
        if ref in (r["ref"], r["series_id"], r["contract_id"], r["ref"].replace("contract:", "")):
            return r
    hits = [r for r in rows if ref and ref.lower() in (r["name"] or "").lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise RefError(f"{ref!r} matches {len(hits)} services ({', '.join(h['ref'] for h in hits[:5])}): use the exact ref")
    raise RefError(f"no subscription {ref!r}: use a ref from `coach subs list` (rec_... or contract:<id>)")


def contract_file(store, contract_id: str) -> Optional[str]:
    return next((rel for rel, c in store.contracts() if c.id == contract_id), None)


def usage_ops(frequency: str, last_used: Optional[dt.date], note: Optional[str]) -> list:
    return [{"op": "set", "path": "usage", "value": U.usage_value(frequency, last_used, note)}]


def contact_of(store) -> dict:
    """The household's local contact block (address, e-mail, phone); {} when none."""
    try:
        hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
    except Exception:                                                     # noqa: BLE001
        return {}
    c = hh.get("contact") if isinstance(hh, dict) else None
    return {k: str(v).strip() for k, v in (c or {}).items() if k in ("address", "email", "phone") and v and str(v).strip()}


def holder_name(store, holder: Optional[str] = None, contract=None) -> Optional[str]:
    """The name to sign with: ``holder`` (a member id or name), else the contract's own ``holder`` (a member id, or ``joint`` = every adult,
    joined by "&"); None when neither is known (the letter then carries a placeholder, never the first adult by default)."""
    members = store.members()
    which = holder or (getattr(contract, "holder", None) if contract is not None else None)
    if not which:
        return None
    if which.lower() == "joint":
        names = [m.name for m in members if m.role == "adult"]
        return " & ".join(names) if names else None
    m = next((m for m in members if m.id == which or m.name.lower() == which.lower()), None)
    if m is None:
        raise RefError(f"no household member {which!r}")
    return m.name


def render_letter(store, ds, contract_id: str, *, lang: Optional[str] = None, channel: str = "lrar", holder: Optional[str] = None,
                  today: Optional[dt.date] = None, private: bool = True) -> dict:
    """The letter of a contract on file (local only: it holds the household's name and address). ``private=False`` leaves the holder's
    name, the address / e-mail / phone and the contract number as placeholders (a process that is not a human at a terminal)."""
    today = today or ds.today
    c = next((m for _r, m in ds.memory.contracts if m.id == contract_id), None)
    if c is None:
        c = next((m for _r, m in store.contracts() if m.id == contract_id), None)
    if c is None:
        raise RefError(f"no contract {contract_id!r}: draft one first (`coach subs draft-contracts`)")
    country = country_of(store, ds)
    lang = lang or {"FR": "fr", "IT": "it"}.get(country, "en")
    res = C.cancellability_of(c, today, country)
    if private:
        name, contact = holder_name(store, holder, c), contact_of(store)
    else:
        name, contact = None, {}
        c = c.model_copy(update={"contract_number": None})
    out = L.build(c, res, today=today, lang=lang, channel=channel, holder_name=name, contact=contact)
    out["country"] = country
    out["private_data_included"] = private
    return out
