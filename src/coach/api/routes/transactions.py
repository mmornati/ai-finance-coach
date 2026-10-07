"""Transactions (E5-3/4/5): filtered list with totals, CSV export, detail with the decision chain, category fixes
and annotations. Reads come from the analytics dataset; writes go through the existing functions only."""
from __future__ import annotations

import csv
import unicodedata
import datetime as dt
import io
import re
from collections import Counter
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from coach.analytics.common import money_str, parse_money, to_cents
from coach.api import views
from coach.api.deps import get_member_snapshot, get_snapshot, get_state, page_params
from coach.api.errors import ApiError
from coach.api import simulate
from coach.api.models import AnnotationRequest, CategoryChange, TxList
from coach.api.routes._write import edit_out, run_edit
from coach.api.state import UI_SOURCE, AppState, Snapshot
from coach.classify import corrections, rules as R, splits as splits_mod
from coach.classify.rules import CATEGORIES
from coach.i18n_msg import server_msg
from coach.memory import explain as explain_mod, schemas, txmatch
from coach.memory.edit import jsonable
from coach import transfers as transfers_mod

router = APIRouter(tags=["transactions"])
SORTS = ("date_desc", "date_asc", "amount_desc", "amount_asc")


def fold(text: str) -> str:
    """Lower case without accents: 'Café' matches 'cafe'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


class TxFilter:
    """Filters of the transaction list. Amounts are magnitudes in EUR (a '50' filter matches -50 and +50): use
    ``direction`` for money in / out. Tags must all be present."""

    def __init__(self,
                 date_from: Optional[dt.date] = None, date_to: Optional[dt.date] = None,
                 account: list[str] = Query([]), owner: Optional[str] = None, purpose: Optional[str] = None,
                 category: list[str] = Query([]), group: list[str] = Query([]),
                 merchant: Optional[str] = Query(None, description="substring of the merchant / entity name"),
                 entity: Optional[str] = Query(None, description="exact canonical merchant name"),
                 amount_min: Optional[str] = None, amount_max: Optional[str] = None,
                 direction: str = Query("all", pattern="^(all|in|out)$"),
                 tag: list[str] = Query([]), source: list[str] = Query([]), event: Optional[str] = None,
                 q: Optional[str] = Query(None, description="text search over description, merchant, category, tags, event"),
                 exclude_transfers: bool = False, sort: str = Query("date_desc", pattern="^(date_desc|date_asc|amount_desc|amount_asc)$")):
        self.date_from, self.date_to = date_from, date_to
        self.account, self.owner, self.purpose = account, owner, purpose
        self.category, self.group = category, group
        self.merchant, self.entity = merchant, entity
        self.amount_min = self._amt(amount_min, "amount_min")
        self.amount_max = self._amt(amount_max, "amount_max")
        self.direction, self.tag, self.source, self.event, self.q = direction, tag, source, event, q
        self.exclude_transfers, self.sort = exclude_transfers, sort

    @staticmethod
    def _amt(v, name):
        if v in (None, ""):
            return None
        try:
            return abs(parse_money(v))
        except Exception:
            raise ApiError(422, "bad_amount", f"{name}: {v!r} is not an amount")


def _flags(snap: Snapshot, state: AppState) -> dict:
    def load():
        with state.read() as con:
            return {"override": {r[0] for r in con.execute("SELECT tx_key FROM tx_overrides")},
                    "linked": transfers_mod.linked_keys(con),
                    "split": {r[0] for r in con.execute("SELECT DISTINCT tx_key FROM tx_splits")}}
    return snap.once("tx_flags", load)


def _select(ds, f: TxFilter) -> list:
    accounts = None
    if f.account:
        accounts = set()
        for ref in f.account:
            a = ds.resolve_account(ref)
            accounts.add(a.uid if a else ref)
    cats, groups = set(f.category), set(f.group)
    needles = [fold(w) for w in (f.q or "").split() if w]
    merchant = fold(f.merchant) if f.merchant else None
    out = []
    for t in ds.whole:
        acc = ds.accounts.get(t.account)
        if accounts is not None and t.account not in accounts:
            continue
        if f.owner and (not acc or (acc.owner or "") != f.owner):
            continue
        if f.purpose and (not acc or (acc.purpose or "") != f.purpose):
            continue
        if f.date_from and t.date < f.date_from or f.date_to and t.date > f.date_to:
            continue
        if cats or groups:
            if t.category not in cats and t.category.split(".")[0] not in groups:
                continue
        if f.exclude_transfers and R and t.category.startswith("transfer."):
            continue
        if f.direction == "in" and t.amount_c <= 0 or f.direction == "out" and t.amount_c >= 0:
            continue
        mag = abs(t.amount_c)
        if f.amount_min is not None and mag < f.amount_min or f.amount_max is not None and mag > f.amount_max:
            continue
        if f.tag and not set(f.tag) <= t.tags:
            continue
        if f.source and t.source not in f.source:
            continue
        if f.event and t.event != f.event:
            continue
        if f.entity and t.entity != f.entity:
            continue
        if merchant and merchant not in fold(f"{t.entity} {t.merchant or ''} {t.mkey}"):
            continue
        if needles:
            mag_s = f"{abs(t.amount_c) // 100}.{abs(t.amount_c) % 100:02d}"
            hay = fold(f"{t.desc} {t.entity} {t.merchant or ''} {t.mkey} {t.category} {t.event or ''} "
                       f"{' '.join(sorted(t.tags))} {mag_s} {mag_s.replace('.', ',')}")
            if not all(n in hay for n in needles):
                continue
        out.append(t)
    key = {"date_desc": lambda t: (t.date, t.key), "date_asc": lambda t: (t.date, t.key),
           "amount_desc": lambda t: (abs(t.amount_c), t.date), "amount_asc": lambda t: (abs(t.amount_c), t.date)}[f.sort]
    out.sort(key=key, reverse=f.sort.endswith("desc"))
    return out


def _item(ds, t, flags) -> dict:
    acc = ds.accounts.get(t.account)
    return {"tx_key": t.key, "date": t.date.isoformat(), "amount": money_str(t.amount_c), "category": t.category,
            "source": t.source, "tags": sorted(t.tags), "event": t.event, "entity": t.entity, "merchant": t.merchant,
            "description": t.desc, "account": t.account, "account_label": acc.label if acc else t.account,
            "owner": acc.owner if acc else None, "purpose": acc.purpose if acc else None, "type": t.type, "person": t.person,
            "split": t.key in flags["split"] or t.source == "split", "overridden": t.key in flags["override"],
            "transfer_linked": t.key in flags["linked"]}


def _totals(txs) -> dict:
    inc = sum(t.amount_c for t in txs if t.amount_c > 0)
    out = sum(t.amount_c for t in txs if t.amount_c < 0)
    return {"count": len(txs), "sum": money_str(inc + out), "income": money_str(inc), "outflow": money_str(out)}


@router.get("/transactions", response_model=TxList, summary="Filtered transactions with the totals of the whole filtered set")
def list_transactions(f: TxFilter = Depends(), page: tuple[int, int] = Depends(page_params),
                      snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    limit, offset = page
    rows = _select(ds, f)
    flags = _flags(snap, state)
    items = [_item(ds, t, flags) for t in rows[offset:offset + limit]]
    nxt = offset + limit if offset + limit < len(rows) else None
    return {"items": items, "totals": _totals(rows), "limit": limit, "offset": offset, "total": len(rows), "next_offset": nxt}


def _csv_cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/transactions/export.csv", summary="CSV export of the filtered set (same filters as the list); locale=fr-FR / it-IT gives Excel-friendly ; and , decimals")
def export_csv(f: TxFilter = Depends(), locale: str = Query("en-GB", pattern="^(fr-FR|it-IT|en-GB)$"),
               snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    rows = _select(ds, f)
    flags = _flags(snap, state)
    fr = locale in ("fr-FR", "it-IT")                  # both write 1,50 and separate columns with ;
    delim = ";" if fr else ","

    def gen() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=delim)
        if fr:
            yield "\ufeff"                                  # the BOM makes Excel read UTF-8
        w.writerow(["date", "account", "owner", "purpose", "amount_eur", "category", "source", "merchant", "description",
                    "tags", "event"])
        yield buf.getvalue()
        for t in rows:
            it = _item(ds, t, flags)
            buf.seek(0), buf.truncate()
            amount = it["amount"].replace(".", ",") if fr else it["amount"]
            w.writerow([it["date"], _csv_cell(it["account_label"]), _csv_cell(it["owner"]), _csv_cell(it["purpose"]), amount,
                        _csv_cell(it["category"]), _csv_cell(it["source"]), _csv_cell(it["entity"]), _csv_cell(it["description"]),
                        _csv_cell(" ".join(it["tags"])), _csv_cell(it["event"])])
            yield buf.getvalue()
    name = f"transactions-{ds.today.isoformat()}.csv"
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/transactions/detail", summary="One transaction with the decision chain behind its category (explain)")
def detail(tx_key: str, snap: Snapshot = Depends(get_member_snapshot), state: AppState = Depends(get_state)):
    ds = snap.ds
    t = next((x for x in ds.whole if x.key == tx_key), None)
    with state.read() as con:
        if not con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (tx_key,)).fetchone():
            raise ApiError(404, "not_found", "no such transaction")
        ex = explain_mod.explain(con, state.store, tx_key)
        parts = splits_mod.get_split(con, tx_key)
        ov = con.execute("SELECT category, note FROM tx_overrides WHERE tx_key=?", (tx_key,)).fetchone()
        link = con.execute("SELECT id, out_tx_key, in_tx_key, method, confidence FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?",
                           (tx_key, tx_key)).fetchone()
        same_merchant = con.execute("SELECT COUNT(*) FROM tx_enriched WHERE merchant_key=? AND merchant_key<>''",
                                    (ex["parsed"]["merchant_key"] or "",)).fetchone()[0]
    ex["transaction"] = {k: v for k, v in ex["transaction"].items()}
    ex["transaction"]["amount"] = money_str(to_cents(ex["transaction"]["amount"]))
    for s in ex["steps"]:
        s.pop("edit", None)                       # CLI hints: the page offers the actions itself
    ex.pop("edit", None)
    ex["split"] = [{"amount": money_str(to_cents(a)), "category": c, "note": n} for a, c, n in parts] or None
    if ex["final"]:
        ex["final"]["tags"] = list(ex["final"]["tags"])
    ex["override"] = {"category": ov[0], "note": ov[1]} if ov else None
    ex["transfer_link"] = {"id": link[0], "out": link[1], "in": link[2], "method": link[3], "confidence": link[4]} if link else None
    ex["same_merchant_count"] = same_merchant
    ex["item"] = _item(ds, t, _flags(snap, state)) if t else None
    return ex


# ---------------------------------------------------------------- category fixes

def _merchant_key(con, tx_key: str) -> str:
    row = con.execute("SELECT merchant_key FROM tx_enriched WHERE tx_key=?", (tx_key,)).fetchone()
    if not row or not row[0]:
        raise ApiError(422, "no_merchant_key", "this transaction has no merchant key to correct: fix it for this "
                                               "transaction only, or annotate it")
    return row[0]


def key_regex(key: str) -> str:
    return "^" + re.escape(key).replace("\\ ", " ") + "$"


def _annotation_parts(state: AppState, req: AnnotationRequest):
    """-> (annotation model, ops, id). Validated by the schema; the store adds the semantic checks when it edits."""
    store = state.store
    match: dict = {}
    if req.tx_keys:
        match["tx_keys"] = list(req.tx_keys)
    if req.merchant_key:
        match["merchant_key"] = key_regex(req.merchant_key)
    if not match:
        raise ApiError(422, "no_match", "say which transactions: tx_keys or merchant_key")
    if req.tx_keys:
        with state.read() as con:
            known = {r[0] for r in con.execute(f"SELECT tx_key FROM transactions WHERE tx_key IN ({','.join('?' * len(req.tx_keys))})",
                                               tuple(req.tx_keys))}
        unknown = [k for k in req.tx_keys if k not in known]
        if unknown:
            raise ApiError(422, "unknown_transaction", f"{len(unknown)} transaction key(s) do not exist (first: {unknown[0][:24]}...)")
    ann: dict = {"match": match}
    if req.category:
        ann["category"] = req.category
    tags = [t.strip() for t in req.tags if t.strip()]
    if tags:
        ann["tags"] = tags
    if req.event:
        ann["event"] = req.event
    if req.note:
        ann["note"] = req.note
    existing = {a.id for a in store.annotations()}
    if req.id:
        aid = req.id
    else:
        base = re.sub(r"[^a-z0-9]+", "-", (req.merchant_key or (req.tx_keys[0].split(":")[-1] if req.tx_keys else "tx")).lower()).strip("-")[:28] or "tx"
        base = f"{base}-{req.category.split('.')[-1].replace('_', '-')}" if req.category else base
        aid, n = base, 1
        while aid in existing:
            n += 1
            aid = f"{base}-{n}"
    if aid in existing:
        raise ApiError(409, "annotation_exists", f"an annotation with id {aid!r} already exists")
    try:
        model = schemas.Annotation.model_validate({"id": aid, **ann})
    except Exception as e:                                           # noqa: BLE001
        from pydantic import ValidationError
        if isinstance(e, ValidationError):
            raise ApiError(422, "invalid_annotation", "; ".join(
                f"{'.'.join(str(x) for x in er['loc']) or 'annotation'}: {er['msg'].removeprefix('Value error, ')}" for er in e.errors()))
        raise
    value = jsonable(model.model_dump(mode="python", exclude_none=True))
    value["match"] = {k: v for k, v in value["match"].items() if v is not None}
    value = {k: value[k] for k in ("id", "match", "category", "tags", "event", "note") if value.get(k) not in (None, [], {})}
    from coach.memory import proposals as prop_mod
    op = {"op": "append", "path": "annotations", "value": prop_mod.rehydrate(value)}
    ops = ([{"op": "create", "value": {"annotations": []}}] if not store.exists("categorization.yaml") else []) + [op]
    return model, ops, aid


def resolved_txs(state: AppState) -> list:
    """Every transaction with its category before memory (what annotations match on): resolved once per dataset snapshot."""
    def load():
        with state.heavy() as con:
            return txmatch.load_txs(con)
    return state.snapshot().once("resolved_txs", load)


def _annotation_effect(state: AppState, model, category: Optional[str] = None) -> dict:
    """What the new annotation really does: the classifier is run with and without it on the transactions it matches."""
    new = txmatch.ann_plain(model)
    existing = [txmatch.ann_plain(x) for x in state.store.annotations()]
    match = new["match"]

    def keys_of(con):
        if "tx_keys" in match:
            return list(match["tx_keys"])
        return [r[0] for r in con.execute("SELECT tx_key FROM tx_enriched WHERE merchant_key REGEXP ?", (match["merchant_key"],))]

    before, after = simulate.simulate_write(state, keys_of, lambda con: None, annotations_after=R.load_annotations(state.cfg.memory_dir) + [new])
    target = category or new.get("category") or ""
    eff = simulate.summarise(before, after, target) if target else simulate.summarise(before, after, "")
    pv = txmatch.preview(new, existing, resolved_txs(state))
    shadowed = sum(pv.already_matched.values())
    eff.update({"applies_to": pv.count - shadowed, "shadowed": shadowed, "samples": pv.to_dict()["samples"],
                "max_account_share": pv.to_dict()["max_account_share"]})
    warnings, msgs = [], []                                  # English (CLI, stored) and the web's messages, same order
    if pv.count == 0:
        warnings.append("it matches no transaction")
        msgs.append(server_msg("annotation.matchesNothing", warnings[-1]))
    elif pv.count - shadowed == 0:
        warnings.append("it would never apply: earlier annotations already take every transaction it matches")
        msgs.append(server_msg("annotation.neverApplies", warnings[-1]))
    if pv.share_of_account > 0.5:
        warnings.append(f"it matches {pv.share_of_account:.0%} of the transactions of one account: too broad?")
        msgs.append(server_msg("annotation.tooBroad", warnings[-1], share_pct=pv.share_of_account))
    eff["warnings"], eff["warnings_msg"] = warnings, msgs
    return eff


def _with_warnings(out: dict, warnings: list, msgs: list) -> dict:
    """`out`'s warnings (the edit's own, English only) followed by `warnings`, and `warnings_msg` in the same order (None: no message)."""
    own = list(out.get("warnings", []))
    own_msgs = list(out.get("warnings_msg") or [None] * len(own))
    out["warnings"], out["warnings_msg"] = own + list(warnings), own_msgs + list(msgs)
    return out


def _merchant_tx_keys(con, mkey: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT tx_key FROM tx_enriched WHERE merchant_key=?", (mkey,))]


@router.post("/transactions/category", summary="Fix a category: this transaction, this merchant, or a memory annotation (dry_run=true: preview)")
def change_category(req: CategoryChange, dry_run: bool = False, snap: Snapshot = Depends(get_snapshot),
                    state: AppState = Depends(get_state)):
    """The preview is the real classifier run on a rolled-back copy of the change: it cannot disagree with the write."""
    if req.category not in CATEGORIES:
        raise ApiError(422, "unknown_category", f"unknown category {req.category!r}")
    with state.read() as con:
        row = con.execute("SELECT amount FROM transactions WHERE tx_key=?", (req.tx_key,)).fetchone()
        if not row:
            raise ApiError(404, "not_found", "no such transaction")
        mkey = None if req.scope == "transaction" or (req.scope == "memory" and req.match == "transaction") \
            else _merchant_key(con, req.tx_key)
    split = any(x.key == req.tx_key and x.source == "split" for x in snap.ds.whole)
    if req.scope in ("transaction", "merchant"):
        if req.scope == "transaction":
            keys_of = lambda con: [req.tx_key]                                            # noqa: E731
            apply = lambda con: corrections.set_override(con, req.tx_key, req.category, req.note, commit=False)   # noqa: E731
        else:
            keys_of = lambda con: _merchant_tx_keys(con, mkey)                            # noqa: E731
            apply = lambda con: corrections.correct_merchant(con, mkey, req.category, exact=True, commit=False)   # noqa: E731
        before, after = simulate.simulate_write(state, keys_of, apply)
        eff = simulate.summarise(before, after, req.category)
        if mkey:
            eff["merchant_key"] = mkey
        pairs = [simulate.blocked_warning(b) for b in eff["blocked"]]
        warns, warns_msg = [w for w, _ in pairs], [m for _, m in pairs]
        if split and req.scope == "transaction":
            warns.append("A split transaction keeps its split parts; clear the split to use this category.")
            warns_msg.append(server_msg("categoryEdit.splitKeeps", warns[-1]))
        changed = eff["count"] > 0
        if not dry_run:
            with state.write() as con:
                if req.scope == "transaction":
                    corrections.set_override(con, req.tx_key, req.category, req.note)
                else:
                    corrections.correct_merchant(con, mkey, req.category, exact=True)
        return {"dry_run": dry_run, "scope": req.scope, "category": req.category, "changed": changed, "affected": eff,
                "diff": "", "warnings": warns, "warnings_msg": warns_msg}
    # memory annotation
    areq = AnnotationRequest(tx_keys=[req.tx_key] if req.match == "transaction" else [], merchant_key=mkey,
                             category=req.category, tags=req.tags, event=req.event, note=req.note,
                             reason=req.note or f"category fixed in the web app: {req.category}")
    model, ops, aid = _annotation_parts(state, areq)
    pv = _annotation_effect(state, model, req.category)
    out = run_edit(state, "categorization.yaml", ops, action="annotate", reason=areq.reason, detail=aid, dry_run=dry_run,
                   extra={"id": aid})
    out.update({"scope": req.scope, "category": req.category, "affected": pv})
    return _with_warnings(out, pv["warnings"], pv["warnings_msg"])


class TxRef(BaseModel):
    tx_key: str


@router.post("/transactions/override/clear", summary="Remove the per-transaction category override")
def clear_override(req: TxRef, state: AppState = Depends(get_state)):
    with state.write() as con:
        ok = corrections.clear_override(con, req.tx_key)
    return {"tx_key": req.tx_key, "cleared": ok}


class SplitPart(BaseModel):
    category: str
    amount: str = Field(description="EUR magnitude, or 'rest' for the remainder")
    note: Optional[str] = None


class SplitRequest(BaseModel):
    tx_key: str
    parts: list[SplitPart] = Field(min_length=2)


@router.post("/transactions/split", summary="Split a transaction over categories (parts must add up exactly)")
def set_split(req: SplitRequest, state: AppState = Depends(get_state)):
    tokens = [f"{p.category}:{p.amount}" + (f":{p.note}" if p.note else "") for p in req.parts]
    with state.write() as con:
        rows = splits_mod.set_split(con, req.tx_key, tokens)
    return {"tx_key": req.tx_key, "parts": [{"amount": money_str(to_cents(a)), "category": c, "note": n} for a, c, n in rows]}


@router.post("/transactions/split/clear", summary="Remove the split of a transaction")
def clear_split(req: TxRef, state: AppState = Depends(get_state)):
    with state.write() as con:
        n = splits_mod.clear_split(con, req.tx_key)
    return {"tx_key": req.tx_key, "removed": n}


# ---------------------------------------------------------------- annotations (tags, notes, events)

@router.get("/annotations", summary="Memory annotations (categorization.yaml) with how many transactions each decides")
def annotations(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    store = state.store
    anns = [txmatch.ann_plain(a) for a in store.annotations()]
    stats = txmatch.analyse(anns, resolved_txs(state)) if anns else {}
    out = []
    for a in anns:
        s = stats.get(a["id"])
        out.append({**{k: v for k, v in a.items() if k != "match"},
                    "match": {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in a["match"].items()},
                    "matched": s.matched if s else 0, "applies_to": s.won if s else 0,
                    "total": money_str(to_cents(s.total)) if s else "0.00"})
    return {"annotations": out}


@router.post("/annotations", summary="Add tags / a note / an event / a category to transactions (dry_run=true: preview)")
def create_annotation(req: AnnotationRequest, dry_run: bool = False, state: AppState = Depends(get_state)):
    if not (req.category or req.tags or req.event):
        raise ApiError(422, "no_effect", "an annotation needs a category, tags or an event")
    if req.category and req.category not in CATEGORIES:
        raise ApiError(422, "unknown_category", f"unknown category {req.category!r}")
    model, ops, aid = _annotation_parts(state, req)
    pv = _annotation_effect(state, model)
    out = run_edit(state, "categorization.yaml", ops, action="annotate", reason=req.reason or req.note or "annotation from the web app",
                   detail=aid, dry_run=dry_run, extra={"id": aid, "affected": pv})
    return _with_warnings(out, pv["warnings"], pv["warnings_msg"])


@router.post("/annotations/{annotation_id}/delete", summary="Remove an annotation (dry_run=true: preview)")
def delete_annotation(annotation_id: str, dry_run: bool = False, state: AppState = Depends(get_state)):
    if annotation_id not in {a.id for a in state.store.annotations()}:
        raise ApiError(404, "not_found", f"no annotation {annotation_id!r}")
    return run_edit(state, "categorization.yaml", [{"op": "remove", "path": f"annotations[{annotation_id}]"}],
                    action="remove-annotation", reason=f"annotation {annotation_id} removed", detail=annotation_id,
                    dry_run=dry_run, extra={"id": annotation_id})


# ---------------------------------------------------------------- internal transfers

def _leg(l) -> dict:
    return {"tx_key": l.tx_key, "date": l.date, "amount": money_str(to_cents(l.amount)), "account": l.account,
            "description": l.description}


@router.get("/transfers", summary="Internal transfers between own accounts: links and proposed pairs")
def transfers(state: AppState = Depends(get_state)):
    cfg = state.cfg
    with state.read() as con:
        links = transfers_mod.list_links(con)
        res = transfers_mod.find_pairs(con, cfg.transfer_window_days, **transfers_mod.opts(cfg))
    return {"links": [{**l, "amount": money_str(to_cents(l["amount"]))} for l in links],
            "proposals": [{"debit": _leg(p["debit"]), "credit": _leg(p["credit"]), "confidence": p["confidence"], "days": p["days"],
                           "topup": p["topup"], "strong": p["strong"], "scope": p.get("scope", "own"), "to_child": p.get("to_child", False)} for p in [*res.linked, *res.proposals]],
            "ambiguous": len(res.ambiguous)}


class LinkRequest(BaseModel):
    out_tx: str
    in_tx: str
    allow_amount_mismatch: bool = False


@router.post("/transfers/link", summary="Link a debit and a credit as an internal transfer")
def link(req: LinkRequest, state: AppState = Depends(get_state)):
    with state.write() as con:
        lid = transfers_mod.link_transfer(con, req.out_tx, req.in_tx, req.allow_amount_mismatch)
    return {"id": lid}


class UnlinkRequest(BaseModel):
    ref: str


@router.post("/transfers/unlink", summary="Remove an internal transfer link (the pair is not proposed again)")
def unlink(req: UnlinkRequest, state: AppState = Depends(get_state)):
    with state.write() as con:
        return transfers_mod.unlink_transfer(con, req.ref)
