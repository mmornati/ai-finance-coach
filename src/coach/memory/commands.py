"""CLI handlers of `coach memory ...`, `coach questions ...` and `coach explain` (E3). Thin wrappers around the
functions of this package, which are the API the future UI (E5) and the coach runtime (E6) call."""
from __future__ import annotations

import datetime as dt
import functools
import json
import sys
from pathlib import Path

from coach.memory import (check as check_mod, context as context_mod, documents as docs_mod, explain as explain_mod,
                          proposals as prop_mod, qgen, questions as q_mod, schemas, totals as totals_mod, txmatch, yamlio)
from coach.memory.edit import jsonable
from coach.memory.history import HistoryError
from coach.memory.store import MemoryStore, MemoryStoreError


def _store(cfg, source: str = "cli", a=None) -> MemoryStore:
    """`a`: the parsed arguments; a write command's --source (e.g. `coach` for what the coach runs on the user's
    behalf) is recorded in the history of every change it makes."""
    return MemoryStore(cfg.memory_dir, history=cfg.memory_history, source=getattr(a, "source", None) or source)


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(a, cfg):
        try:
            return fn(a, cfg)
        except (MemoryStoreError, HistoryError, yamlio.YamlError) as e:
            sys.exit(f"error: {e}")
    return wrapper


def _con(a, cfg, required: bool = True):
    from coach.db import DatabaseMissingError, connect
    try:
        return connect(cfg, insecure=a.insecure)
    except DatabaseMissingError:
        if required:
            raise
        return None


def _j(obj) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False, indent=2)


def _print_change(res, what: str = "") -> None:
    if not res.changed:
        print("no change (the value is already there)")
        return
    print(res.diff.rstrip("\n"))
    if res.change_id:
        print(f"\nrecorded as change {res.change_id}  (`coach memory history`, `coach memory revert {res.change_id}`)")
    else:
        print("\nwritten (history is off: [memory] history = false)")


# ---------------------------------------------------------------- show / set

@_guard
def cmd_show(a, cfg):
    store = _store(cfg)
    rel, item = store.resolve_ref(a.ref)
    if item is None:
        if not store.exists(rel):
            sys.exit(f"error: {rel} does not exist")
        text = store.read_text(rel)
        if a.json:
            print(_j({"file": rel, "data": yamlio.to_plain(yamlio.loads(text)) if store.kind_of(rel) == "yaml" else text}))
        else:
            print(f"# {store.path(rel)}\n{text}", end="" if text.endswith("\n") else "\n")
        return
    lk = store.list_key_of(rel)
    doc = store.load_doc(rel)
    node = doc
    line = None
    if lk:
        node = next(it for it in doc[lk] if str(it.get("id")) == item)
        line = node.lc.line + 1
    if a.json:
        print(_j({"file": rel, "id": item, "line": line, "data": yamlio.to_plain(node)}))
    else:
        print(f"# {rel}" + (f" line {line}" if line else "") + f" ({item})")
        print(yamlio.dumps(node), end="")


@_guard
def cmd_set(a, cfg):
    store = _store(cfg, a=a)
    if a.value is None and not a.unset:
        sys.exit("error: a value is required (write `null` to empty the field, or use --unset to remove the key)")
    value = yamlio.to_plain(yamlio.parse_scalar(a.value)) if not a.unset else None
    rel, item = store.resolve_ref(a.ref)
    lk = store.list_key_of(rel)
    full = f"{lk}[{item}].{a.path}" if item and lk else a.path
    op = {"op": "unset", "path": full} if a.unset else {"op": "set", "path": full, "value": value}
    res = store.edit(rel, [op], action="unset" if a.unset else "set", reason=a.reason,
                     allow_comment_loss=a.drop_comments, allow_new_fields=a.new_field, dry_run=a.dry_run,
                     detail=(f"{item}." if item else "") + a.path)
    for i in res.issues:
        print(f"{i.level}: {i.message}", file=sys.stderr)
    if a.dry_run:
        print(res.diff.rstrip("\n") or "no change")
        print("(dry run: nothing written)")
        return
    _print_change(res)


@_guard
def cmd_append(a, cfg):
    store = _store(cfg, a=a)
    rel, item = store.resolve_ref(a.file)
    if store.kind_of(rel) != "markdown":
        sys.exit(f"error: {rel} is not a Markdown file (use `coach memory set` for YAML)")
    res = store.edit(rel, [{"op": "append_text", "value": a.text}], action="append", reason=a.reason,
                     dry_run=a.dry_run)
    if a.dry_run:
        print(res.diff.rstrip("\n") or "no change")
        print("(dry run: nothing written)")
        return
    _print_change(res)


@_guard
def cmd_new(a, cfg):
    """Create liabilities/<id>.yaml or contracts/<id>.yaml with the key fields present (null = unknown)."""
    store = _store(cfg, a=a)
    if a.what == "liability":
        value = {"id": a.id, "kind": a.kind, "lender": None, "asset": None, "start_date": None, "end_date": None,
                 "principal": None, "outstanding": None, "outstanding_as_of": None,
                 "rate": {"type": None, "nominal": None, "taeg": None}, "monthly_payment": None,
                 "payment_match": None, "documents": [], "notes": ""}
        rel = f"liabilities/{a.id}.yaml"
    else:
        value = {"id": a.id, "provider": None, "kind": a.kind, "merchant_match": None, "renewal": None,
                 "billing": {"amount": None, "period": None}, "notice_period_days": None, "documents": [], "notes": ""}
        rel = f"contracts/{a.id}.yaml"
    res = store.edit(rel, [{"op": "create", "value": value}], action=f"new-{a.what}", reason=a.reason)
    _print_change(res)
    print(f"fill it with `coach memory set {a.id} <field> <value>`; unknown facts stay null")


# ---------------------------------------------------------------- annotate

def _auto_id(store, a, category) -> str:
    base = yamlio.slug((a.merchant_key or a.description or (a.tx_key[0] if a.tx_key else "tx")).replace("^", "").replace("$", ""), 28)
    cand = f"{base}-{category.split('.')[-1].replace('_', '-')}" if category else base
    existing = {x.id for x in store.annotations()}
    n, out = 1, cand
    while out in existing:
        n += 1
        out = f"{cand}-{n}"
    return out


@_guard
def cmd_annotate(a, cfg):
    store = _store(cfg, a=a)
    match = {}
    for flag, key in (("merchant_key", "merchant_key"), ("description", "description")):
        if getattr(a, flag):
            match[key] = getattr(a, flag)
    if a.tx_key:
        match["tx_keys"] = list(a.tx_key)
    try:
        for flag in ("date_from", "date_to"):
            if getattr(a, flag):
                match[flag] = dt.date.fromisoformat(getattr(a, flag))
    except ValueError as e:
        sys.exit(f"error: dates must be ISO (YYYY-MM-DD): {e}")
    if a.amount_min is not None:
        match["amount_min"] = a.amount_min
    if a.amount_max is not None:
        match["amount_max"] = a.amount_max
    if a.category_in:
        match["category_in"] = [c.strip() for c in a.category_in.split(",") if c.strip()]
    if a.weekdays:
        match["weekdays"] = [d.strip().lower() for d in a.weekdays.split(",") if d.strip()]
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    ann = {"id": a.id or _auto_id(store, a, a.category), "match": match}
    if a.category:
        ann["category"] = a.category
    if tags:
        ann["tags"] = tags
    if a.event:
        ann["event"] = a.event
    if a.note:
        ann["note"] = a.note
    # validate the annotation itself (types, regexes, at least one criterion and one effect)
    try:
        model = schemas.Annotation.model_validate(ann)
    except Exception as e:                                          # noqa: BLE001
        from pydantic import ValidationError
        if isinstance(e, ValidationError):
            sys.exit("error: invalid annotation: " + "; ".join(
                f"{'.'.join(str(x) for x in er['loc']) or 'annotation'}: {er['msg'].removeprefix('Value error, ')}" for er in e.errors()))
        raise
    if any(x.id == model.id for x in store.annotations()):
        sys.exit(f"error: an annotation with id {model.id!r} already exists (use --id)")
    from coach.classify.rules import CATEGORIES
    for c in ([model.category] if model.category else []) + (model.match.category_in or []):
        if c not in CATEGORIES:
            sys.exit(f"error: unknown category {c!r}; see `coach taxonomy list`")
    if model.event and model.event not in store.event_ids():
        sys.exit(f"error: event {model.event!r} does not exist: add a `## {model.event}` section to events.md first "
                 f"(known: {', '.join(sorted(store.event_ids())) or 'none'})")
    con = _con(a, cfg)
    txs = txmatch.load_txs(con)
    existing = [txmatch.ann_plain(x) for x in store.annotations()]
    pv = txmatch.preview(txmatch.ann_plain(model), existing, txs)
    print(f"annotation {model.id!r}: " + ", ".join(f"{k}={v}" for k, v in match.items()))
    print(f"  effect: category {model.category or '-'}, tags {tags or '-'}, event {model.event or '-'}")
    print(f"  matches {pv.count} transactions, total {pv.total:+,.2f} EUR"
          + (f", {pv.date_min} .. {pv.date_max}" if pv.count else ""))
    if pv.samples:
        print("  sample merchants: " + "; ".join(f"{m} x{n} ({v:+,.0f})" for m, n, v in pv.samples))
    shadowed = sum(pv.already_matched.values())
    if shadowed:
        print(f"  {shadowed} of them are already decided by earlier annotations "
              f"({', '.join(f'{k} x{n}' for k, n in pv.already_matched.most_common(3))}): the first match wins, so this "
              f"annotation would apply to {pv.count - shadowed} transaction(s) only")
    problems = []
    if pv.count == 0:
        problems.append("it matches no transaction")
    elif pv.count - shadowed == 0:
        problems.append("it would never apply: earlier annotations take every transaction it matches")
    if pv.share_of_account > check_mod.SHARE_WARN:
        print(f"  warning: it matches {pv.share_of_account:.0%} of the transactions of one account: too broad?")
    if problems and not a.allow_empty:
        sys.exit("error: " + "; ".join(problems) + " (nothing written; fix the criteria, or pass --allow-empty)")
    if a.dry_run:
        print("dry run: nothing written")
        return
    op = {"op": "append", "path": "annotations", "value": jsonable(model.model_dump(mode="python", exclude_none=True,
                                                                                    exclude_defaults=False))}
    # keep the file tidy: omit empty tags, order keys id, match, category, tags, event, note
    d = op["value"]
    d["match"] = {k: v for k, v in d["match"].items() if v is not None}
    d = {k: d[k] for k in ("id", "match", "category", "tags", "event", "note") if d.get(k) not in (None, [], {})}
    op["value"] = _rehydrate_dates(d)
    reason = a.reason or a.note
    if a.propose:
        p = prop_mod.create(store, "categorization.yaml", [op], reason or "annotation proposed from the CLI",
                            a.source or "user")
        print(f"\nproposal {p.id} created (nothing written yet): `coach memory accept {p.id}`")
        return
    if not (store.exists("categorization.yaml")):
        op_list = [{"op": "create", "value": {"annotations": []}}, op]
    else:
        op_list = [op]
    res = store.edit("categorization.yaml", op_list, action="annotate", reason=reason, detail=model.id)
    print()
    _print_change(res)


def _rehydrate_dates(d):
    return prop_mod.rehydrate(d)


# ---------------------------------------------------------------- history / diff / revert

@_guard
def cmd_history(a, cfg):
    store = _store(cfg)
    rows = store.history(a.file, a.limit)
    if a.json:
        print(_j([r.__dict__ for r in rows]))
        return
    if not rows:
        print("no recorded changes yet (history starts with the first `coach memory` write)")
    for r in rows:
        print(f"{r.id}  {r.date[:16].replace('T', ' ')}  {r.subject}" + (f"  [{r.source}]" if r.source else ""))
        if r.reason:
            print(f"          {r.reason}")


@_guard
def cmd_diff(a, cfg):
    store = _store(cfg)
    ref, file = None, None
    if a.ref:
        if store.kind_of(a.ref) is not None:
            file = a.ref
        else:
            ref = a.ref
    out = store.diff(ref, file)
    print(out.rstrip("\n") if out.strip() else "no unrecorded changes (the memory folder matches the last recorded change)")


@_guard
def cmd_revert(a, cfg):
    store = _store(cfg)
    ch = store.revert(a.change_id)
    print(f"reverted {a.change_id}: {', '.join(ch.files)} restored as a new change {ch.id}")
    if "documents.yaml" in ch.files:
        recorded = {d.stored_as for d in store.documents()}
        ddir = store.root / "documents"
        orphans = [f.name for f in sorted(ddir.iterdir()) if f.is_file() and f.name not in recorded] if ddir.is_dir() else []
        for o in orphans:
            print(f"WARNING: documents/{o} is no longer recorded in documents.yaml: delete it, or record it again with "
                  "`coach memory doc add`")
        for rel, m in [*store.liabilities(), *store.contracts()]:
            for ref in m.documents:
                if not store.exists(ref):
                    print(f"WARNING: {rel} still lists {ref}, which does not exist: `coach memory set {m.id} documents "
                          "'[]'` removes the link")


# ---------------------------------------------------------------- proposals

def _is_tty() -> bool:
    """Accepting a proposal (or purging the history) is a human act: stdin AND stdout must be a terminal, so a piped /
    agent-driven process cannot do it. (A process running as the same user can always edit the files directly; the
    history attribution, this gate and the file permissions are the mitigations.)"""
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def _print_proposal(store, p, full: bool = True) -> None:
    d = prop_mod.public_dict(store, p)
    print(f"{p.id}  [{d['status']}]  {p.file}  (source: {p.source}, {p.created[:16].replace('T', ' ')})"
          + ("" if d["sealed"] else "  *** MODIFIED AFTER CREATION: it cannot be accepted ***"))
    print(f"  reason: {p.reason}")
    for c in d["changes"]:
        if c["op"] in ("set", "unset", "remove"):
            print(f"  {c['op']} {c['path']}: {c['old']!r} -> {c['new']!r}" if c["op"] == "set" else f"  {c['op']} {c['path']}")
        elif c["op"] == "append":
            print(f"  append to {c['path']}: {json.dumps(c['new'], ensure_ascii=False, default=str)[:200]}")
        else:
            print(f"  {c['op']}")
        if c.get("snippet"):
            print(f"      source: \"{c['snippet']}\"")
        if c.get("suspicious"):
            print("      !! the document contains instruction-like text: this field needs --confirm-field to be accepted")
        if c.get("conflicts_with") is not None:
            print(f"      !! differs from the value in memory today: {c['conflicts_with']!r}")
    if not d["applicable"]:
        print(f"  cannot be applied to the file as it is now: {d['error']}")
    if full and d["diff"]:
        print("\n" + d["diff"].rstrip("\n"))


@_guard
def cmd_propose(a, cfg):
    store = _store(cfg, source=a.source)
    rel, item = store.resolve_ref(a.ref)
    lk = store.list_key_of(rel)
    path = f"{lk}[{item}].{a.path}" if item and lk and a.path else a.path
    if a.append_text:
        op = {"op": "append_text", "value": a.append_text}
        p = prop_mod.create(store, rel, [op], a.reason, a.source)
        _print_proposal(store, p)
        print(f"\nnothing was written. `coach memory accept {p.id}` applies it, `coach memory reject {p.id}` closes it.")
        return
    if not path:
        sys.exit("error: a path is required (or --append-text for a Markdown file)")
    kind = "unset" if a.unset else "append" if a.append else "remove" if a.remove else "set"
    op = {"op": kind, "path": path}
    if kind in ("set", "append"):
        if a.value is None:
            sys.exit("error: a value is required")
        op["value"] = yamlio.to_plain(yamlio.parse_scalar(a.value))
    p = prop_mod.create(store, rel, [op], a.reason, a.source)
    _print_proposal(store, p)
    print(f"\nnothing was written. `coach memory accept {p.id}` applies it, `coach memory reject {p.id}` closes it.")


@_guard
def cmd_proposals(a, cfg):
    store = _store(cfg)
    ps = prop_mod.listing(store, "all" if a.all else "pending")
    if a.json:
        print(_j([prop_mod.public_dict(store, p) for p in ps]))
        return
    if not ps:
        print("no pending proposals" if not a.all else "no proposals")
    for p in ps:
        _print_proposal(store, p, full=bool(a.id) or len(ps) == 1)
        print()


@_guard
def cmd_accept(a, cfg):
    if not _is_tty():
        sys.exit("error: `coach memory accept` is a human decision and needs an interactive terminal (stdin and stdout "
                 "must be a TTY): run it yourself in a terminal. There is deliberately no --yes.")
    store = _store(cfg)
    pv = prop_mod.preview(store, a.id)            # recomputed from the operations: stored display fields are never trusted
    p = pv.proposal
    print(f"proposal {p.id} from {p.source}: {p.reason}")
    if not pv.sealed:
        sys.exit(f"error: proposal {p.id} was modified after it was created (or is unsealed): refusing to apply it. "
                 f"`coach memory reject {p.id}` and ask for a new one")
    print("what accepting would change RIGHT NOW:\n")
    print(pv.result.diff.rstrip("\n") or "(no change)")
    sus = prop_mod.suspicious_paths(p)
    if sus:
        print("\nWARNING: the source document contains instruction-like text (possible prompt injection). Fields: "
              + ", ".join(sus))
        missing = [x for x in sus if x not in set(a.confirm_field or [])]
        if missing:
            sys.exit("error: confirm each suspicious field explicitly after checking it against the document: "
                     + " ".join(f"--confirm-field {x}" for x in missing))
    if pv.stale:
        print(f"\nWARNING: {p.file} changed since this proposal was made; it may overwrite a newer value.")
        if not a.force:
            sys.exit("error: refusing a stale proposal (read the diff above; accept with --force if it is still right)")
    if input("\nApply this change? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("not applied")
    p, res = prop_mod.accept(store, a.id, confirmed=True, force=a.force, accepted_by=a.source,
                             confirm_fields=a.confirm_field or [])
    print(f"\naccepted {p.id}: recorded as change {res.change_id or '(history off)'} (source {a.source})")


@_guard
def cmd_reject(a, cfg):
    p = prop_mod.reject(_store(cfg), a.id, a.note)
    print(f"rejected {p.id}")


# ---------------------------------------------------------------- check / context / totals

@_guard
def cmd_check(a, cfg):
    store = _store(cfg)
    con = None if a.no_db else _con(a, cfg, required=False)
    issues = check_mod.run_check(store, con, stale_months=cfg.memory_stale_months,
                                 asset_stale_months=cfg.memory_asset_stale_months, cfg=cfg)
    s = check_mod.summarize(issues)
    if a.json:
        print(_j({"summary": s, "database_checked": con is not None, "issues": [i.to_dict() for i in issues]}))
    else:
        print(check_mod.format_issues(issues))
        if con is None:
            print("(no database: checks against the transactions were skipped)")
    if s["errors"]:
        sys.exit(1)


def memory_summary_line(cfg, con=None) -> dict:
    """For `coach health` / `schedule run`: counts only, never raises."""
    try:
        store = MemoryStore(cfg.memory_dir, history=False)
        issues = check_mod.run_check(store, con, stale_months=cfg.memory_stale_months,
                                     asset_stale_months=cfg.memory_asset_stale_months, cfg=cfg)
        return check_mod.summarize(issues)
    except Exception as e:                                          # noqa: BLE001 - a health summary must not crash
        return {"errors": 0, "warnings": 0, "info": 0, "by_code": {}, "failed": f"{type(e).__name__}: {str(e)[:120]}"}


@_guard
def cmd_context(a, cfg):
    store = _store(cfg)
    con = None if a.no_db else _con(a, cfg, required=False)
    ctx = context_mod.build_context(store, con, cfg, names=a.names, coarse=a.coarse)
    if a.max_tokens:
        ctx, md = context_mod.fit(ctx, a.max_tokens)
    else:
        md = context_mod.render_markdown(ctx)
    if a.json:
        print(_j(ctx))
    else:
        print(md, end="")
    print(f"(~{context_mod.est_tokens(md)} tokens)", file=sys.stderr)


@_guard
def cmd_totals(a, cfg):
    t = totals_mod.manual_totals(_store(cfg), asset_stale_months=cfg.memory_asset_stale_months,
                                 stale_months=cfg.memory_stale_months)
    if a.json:
        print(_j(t))
        return
    print(f"assets (manual): {t['assets']['total']:,.2f} EUR   " + ", ".join(f"{k} {v:,.0f}" for k, v in t["assets"]["by_kind"].items()))
    print(f"liabilities outstanding: {t['liabilities']['outstanding_total']:,.2f} EUR   "
          f"monthly payments: {t['liabilities']['monthly_payments_total']:,.2f} EUR")
    print(f"net (manual figures only): {t['net_manual']:,.2f} EUR   complete: {t['complete']}")
    for label, ids in (("assets without a value", t["assets"]["unknown_value"]), ("stale asset values", t["assets"]["stale"]),
                       ("liabilities without outstanding", t["liabilities"]["unknown_outstanding"]),
                       ("stale outstanding", t["liabilities"]["stale"])):
        if ids:
            print(f"  {label}: {', '.join(ids)}")


@_guard
def cmd_member_add(a, cfg):
    store = _store(cfg, a=a)
    member = {"id": a.id, "name": a.name, "role": a.role}
    if a.birth_year:
        member["birth_year"] = a.birth_year
    if a.alias:
        member["aliases"] = list(a.alias)
    ops = [{"op": "append", "path": "members", "value": member}]
    if not store.exists("household.yaml"):
        ops = [{"op": "create", "value": {"members": []}}] + ops
    res = store.edit("household.yaml", ops, action="add-member", detail=a.id)
    _print_change(res)


# ---------------------------------------------------------------- documents

@_guard
def cmd_doc_add(a, cfg):
    d = docs_mod.add_document(_store(cfg, a=a), Path(a.file), a.kind, a.for_id)
    print(f"stored as {d.id} ({d.kind}, {d.size} bytes) -> memory/documents/{d.stored_as} (mode 0600)"
          + (f", attached to {d.for_id}" if d.for_id else ""))


@_guard
def cmd_doc_list(a, cfg):
    docs = _store(cfg).documents()
    if a.json:
        print(_j([d.model_dump(mode="python", by_alias=True) for d in docs]))
        return
    if not docs:
        print("no documents (`coach memory doc add FILE --kind loan`)")
    for d in docs:
        print(f"{d.id}  {d.kind:<10} {d.size:>9} B  {d.added}  {d.filename}" + (f"  -> {d.for_id}" if d.for_id else ""))


@_guard
def cmd_doc_extract(a, cfg):
    store = _store(cfg)
    con = _con(a, cfg, required=False)
    kind, tid = a.into
    if a.send and a.dry_run:
        sys.exit("error: --send and --dry-run exclude each other")
    backend = model = None
    if a.send:
        from coach.classify.backends import default_model, get_backend
        backend = get_backend(cfg)
        model = a.model or default_model(cfg, backend)
    res = docs_mod.extract(store, a.doc, kind, tid, send=a.send, backend=backend, model=model, con=con,
                           create_kind=a.target_kind if a.create else None)
    r = res.redaction
    print(f"document {res.document.id} ({res.document.kind}) -> {kind} {tid}"
          + (f"  [{res.target_file}]" if res.target_file else "  [new file]"))
    print("redacted: " + (", ".join(f"{n} {k}" for k, n in sorted(r.counts.items())) or "nothing to redact") + "  (no OCR: text layer only)")
    if res.suspicious:
        print("WARNING: the document contains instruction-like text (possible prompt injection):")
        for x in res.suspicious:
            print(f"   ... {x} ...")
        print("every field extracted from it is marked suspicious and needs an explicit --confirm-field to be accepted")
    if res.truncated:
        print(f"WARNING: the document text is longer than {docs_mod.MAX_PROMPT_CHARS} characters: only the first "
              "part is sent, facts further down are NOT extracted")
    if not res.sent:
        print("\n===== EXACTLY WHAT WOULD BE SENT =====")
        print(res.payload_text)
        print("===== END =====")
        print(f"\nDRY RUN: nothing was sent. `--send` would call the {cfg.llm_backend} backend with the payload above.")
        return
    print(f"sent to {cfg.llm_backend} ({model}).")
    for f in res.accepted_fields:
        print(f"  snippet-found  {f['path']} = {f['value']!r}  (confidence {f['confidence']})\n            \"{f['snippet'][:120]}\"")
    for f in res.rejected_fields:
        print(f"  dropped   {f['path']}: {f['why']}")
    if res.proposal:
        print()
        _print_proposal(store, res.proposal)
        print(f"\nnothing was written to memory. `coach memory accept {res.proposal.id}` to apply it.")
    else:
        print("no change proposed")


# ---------------------------------------------------------------- questions

@_guard
def cmd_migrate_questions(a, cfg):
    store = _store(cfg, a=a)
    plan, new_md = q_mod.migrate_preview(store)
    print(f"open-questions.md -> open-questions.yaml: {len(plan.questions)} items "
          f"({plan.n_open} open, {plan.n_answered} answered) from {plan.md_items} checkbox lines; lossless: {plan.lossless}")
    if plan.ignored:
        print(f"  header/intro lines not converted (regenerated in the view): {len(plan.ignored)}")
    by_topic: dict[str, int] = {}
    for q in plan.questions:
        by_topic[q.topic] = by_topic.get(q.topic, 0) + 1
    for t, n in by_topic.items():
        print(f"  {n:>3}  {t}")
    old = store.read_text(q_mod.MD_FILE)
    import difflib
    diff = "".join(difflib.unified_diff(old.splitlines(True), new_md.splitlines(True), "open-questions.md (current)",
                                        "open-questions.md (regenerated)", n=0))
    print("\nthe regenerated Markdown view differs from the current file by:\n" + (diff.rstrip() or "(nothing)"))
    if not a.write:
        print("\ndry run: nothing written. `--write` converts (a copy of the Markdown is kept in memory/.backups/).")
        return
    plan, backup, res = q_mod.migrate_write(store)
    print(f"\nwritten: open-questions.yaml + regenerated open-questions.md; backup {backup}; change {res.change_id}")


def _qline(q) -> str:
    stake = f"{q.stake:>8,.0f} EUR" if q.stake else " " * 12
    text = q.question if len(q.question) <= 110 else q.question[:107] + "..."
    return f"{q.id:<22}{q.status:<10}{stake}  {text}"


@_guard
def cmd_q_list(a, cfg):
    qs = q_mod.listing(_store(cfg), "open" if a.open else a.status)
    if a.json:
        from coach.i18n_msg import strip_msgs                  # the *_msg translations are the web app's only
        print(_j([strip_msgs(q.model_dump(mode="python", exclude_none=True)) for q in qs]))
        return
    if not qs:
        print("no questions" + (" open" if a.open else "") + " (`coach questions generate`)")
    for q in qs:
        print(_qline(q))


@_guard
def cmd_q_answer(a, cfg):
    q = q_mod.answer(_store(cfg, a=a), a.id, a.text)
    print(f"{q.id}: answered ({q.answered}). The answer is only RECORDED; no other memory file was changed.")
    if q.suggested_target:
        t = q.suggested_target
        print(f"  likely place to record it: {t.file}" + (f" {t.field}" if t.field else "")
              + " (`coach memory annotate` / `coach memory set`)")


@_guard
def cmd_q_dismiss(a, cfg):
    q = q_mod.dismiss(_store(cfg, a=a), a.id, a.reason)
    print(f"{q.id}: dismissed (it will not be asked again)")


@_guard
def cmd_q_reopen(a, cfg):
    q = q_mod.reopen(_store(cfg, a=a), a.id)
    print(f"{q.id}: open again")


@_guard
def cmd_q_add(a, cfg):
    ev = {}
    for kv in a.evidence or []:
        k, _, v = kv.partition("=")
        ev[k] = yamlio.to_plain(yamlio.parse_scalar(v))
    q = q_mod.add(_store(cfg, a=a), a.text, topic=a.topic, target=a.target, evidence=ev, stake=a.stake)
    print(f"added {q.id}")


@_guard
def cmd_q_generate(a, cfg):
    store = _store(cfg, a=a)
    con = _con(a, cfg)
    res = qgen.generate(store, con, cfg, max_merchants=a.max_merchants)
    print(f"{len(res.new)} new question(s), {res.skipped_existing} already asked (any status: not repeated)")
    for k, n in sorted(res.by_type.items()):
        print(f"  {n:>3}  {k}")
    for q in res.new:
        print(_qline(q))
    if a.dry_run:
        print("\ndry run: nothing written")
        return
    if res.new:
        q_mod.add_many(store, res.new)
        print(f"\nwritten to open-questions.yaml (view regenerated: open-questions.md)")


@_guard
def cmd_q_regen(a, cfg):
    backup = q_mod.regenerate_view(_store(cfg))
    print("open-questions.md regenerated from open-questions.yaml" + (f"; your edited copy is saved as {backup}" if backup else ""))


@_guard
def cmd_purge_history(a, cfg):
    store = _store(cfg)
    try:
        dt.date.fromisoformat(a.before)
    except ValueError:
        sys.exit("error: --before must be an ISO date YYYY-MM-DD")
    revs, keep = store.repo.purge_plan(a.before)
    dropped = 0 if len(keep) == len(revs) else len(revs) - len(keep)
    if not dropped:
        print("nothing to purge: no recorded change is older than that date")
        return
    print(f"{dropped} recorded change(s) older than {a.before} would be deleted for good ({len(keep)} kept"
          + ("; that cutoff is after the newest change, so only the current state is kept" if len(keep) == 1 else "")
          + "). Their past values, notes and reasons disappear from the history; the current files are untouched. "
          "An encrypted backup (`coach backup`) is taken first.")
    if not _is_tty():
        sys.exit("error: not applied: purging history is a human decision and needs an interactive terminal (stdin and "
                 "stdout must be a TTY): run this yourself in a terminal")
    if input("Purge now? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("not purged")
    from coach import backup as backup_mod
    try:
        path, _ = backup_mod.create_backup(cfg)
    except Exception as e:                                          # noqa: BLE001
        sys.exit(f"error: could not take the backup ({type(e).__name__}: {e}); the history was NOT purged")
    print(f"backup written: {path}")
    with store.locked():
        done, kept = store.repo.purge_before(a.before)
        gone = prop_mod.purge_resolved(store, a.before)
    print(f"history rewritten: {done} older change(s) dropped, {kept} kept; {gone} resolved proposal file(s) deleted")
    old = backup_mod.list_backups(cfg.backup_dir)
    if old:
        print("NOTE: backups (including the one just taken before the purge) still contain the old values (past values, notes and reasons); delete them yourself "
              "if that matters:")
        for b in old:
            print(f"   {b}")


# ---------------------------------------------------------------- explain

@_guard
def cmd_explain(a, cfg):
    store = _store(cfg)
    con = _con(a, cfg)
    rows = explain_mod.find_transactions(con, a.ref)
    if not rows:
        sys.exit(f"error: no transaction matches {a.ref!r} (tx_key, or a fragment of the key / description / merchant key)")
    if len(rows) > 1 and not any(r[0] == a.ref for r in rows):
        if a.json:
            print(_j({"candidates": [dict(zip(("tx_key", "date", "amount", "merchant_key", "description"), r)) for r in rows]}))
        else:
            print(f"{len(rows)} transactions match {a.ref!r}; pass one tx_key:")
            for r in rows:
                print(f"  {r[0]}  {r[1]}  {r[2]:+9.2f}  {r[3]}  {r[4]}")
        sys.exit(1)
    x = explain_mod.explain(con, store, rows[0][0])
    print(_j(explain_mod.plain(x)) if a.json else explain_mod.format_explanation(x))
