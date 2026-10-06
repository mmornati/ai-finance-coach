"""LLM labelling of unique merchants (never of individual transactions, never of person-to-person transfers).

The prompt contract is backend independent: a static part (instructions + taxonomy, cacheable) and a dynamic part
(user-confirmed examples, nearest labelled merchants, the items). Every item is redacted first
(:mod:`coach.classify.redact`). The transport is a :class:`coach.classify.backends.LLMBackend`.
"""
from __future__ import annotations

import json
import subprocess  # noqa: F401  (kept importable here: tests patch coach.classify.llm.subprocess.run)
from concurrent.futures import ThreadPoolExecutor

from coach.classify import backends, knn as knn_mod
from coach.classify.backends import ClaudeCodeBackend, LLMBackend, record_usage
from coach.classify.redact import redact, redact_item
from coach.classify.rules import CATEGORIES, needs_llm_sql
from coach.db import now_iso

LABEL_HEAD = """You label merchants from a European (France/Italy) household's bank statements.
Bank descriptors are truncated (~25 chars), upper-cased and usually end with the city.
Payment-processor prefixes (Mol*=Mollie, SUMUP, SQ, NYX, Sunday, PAYPAL, ZTL...) precede the real merchant.
For each item return: a clean human merchant name (brand, no city), the single best category id
from the list, a confidence 0..1 (use < 0.6 when you are guessing from a vague name), and
recurring_hint=true if this merchant is typically a subscription / recurring bill.
Classify by WHAT THE MERCHANT SELLS, also when direction=in: a refund from a shop or a
utility (e.g. EDF, an airline) goes in that merchant's spending category so it nets out; use
income.* only for employers, benefits, interest and similar real income.
Expand abbreviations (e.g. "OCTOPUS EL" = Octopus Energy).
Use "other.uncategorized" only if nothing plausible fits."""

SIMILAR_NOTE = ("Items may carry `similar`: the nearest already-labelled merchants (key, category). They are hints: "
                "follow them when the item is clearly the same business or chain, ignore them otherwise.\n\n")

def label_schema() -> dict:
    """Built from the CURRENT taxonomy (a rename or an added leaf changes the enum)."""
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "merchant": {"type": "string"},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "confidence": {"type": "number"},
                "recurring_hint": {"type": "boolean"},
            },
            "required": ["id", "merchant", "category", "confidence", "recurring_hint"]}}},
        "required": ["results"],
    }


LABEL_SCHEMA = label_schema()      # snapshot at import time, for reference; calls use label_schema()


def strict_bool(v) -> bool:
    """'false' / 'no' / '0' must not become True the way bool("false") does."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return isinstance(v, str) and v.strip().lower() in ("true", "yes", "1", "oui", "y")


def validate_results(results, n: int) -> list[dict]:
    """Never trust model output: ids must be ints in range, the category must be in the taxonomy (else
    other.uncategorized with low confidence), confidence is clamped to [0, 1]."""
    out, seen = [], set()
    for r in results or []:
        if not isinstance(r, dict) or isinstance(r.get("id"), bool) or not isinstance(r.get("id"), int):
            continue
        if not 0 <= r["id"] < n or r["id"] in seen:           # out of range, or a repeated id (the first wins)
            continue
        if not isinstance(r.get("merchant"), str) or not r["merchant"].strip() or r.get("category") is None \
                or r.get("confidence") is None:
            continue                                           # incomplete entry (a non-text merchant name too)
        conf = r.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            continue                                           # a confidence that is not a number: unusable item
        conf = float(conf)
        conf = 0.0 if conf != conf else min(1.0, max(0.0, conf))
        cat = r.get("category")
        if not isinstance(cat, str) or cat not in CATEGORIES:  # also a list / dict / number from a sloppy model
            cat, conf = "other.uncategorized", min(conf, 0.3)
        seen.add(r["id"])
        out.append({"id": r["id"], "merchant": r["merchant"][:120], "category": cat,
                    "confidence": conf, "recurring_hint": strict_bool(r.get("recurring_hint"))})
    return out


def categories_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())


def label_prompt(items: list[dict], examples: list[tuple[str, str, str]]) -> tuple[str, str]:
    """(static, dynamic) prompt parts. `static` is identical for every call with the same taxonomy."""
    static = f"{LABEL_HEAD}\n\nCategories:\n{categories_text()}"
    shots = "\n".join(f'- "{k}" → {c} ({n})' for k, n, c in examples) or "(none yet)"
    note = SIMILAR_NOTE if any(i.get("similar") for i in items) else ""
    dynamic = f"""

Labels confirmed by the user (follow these conventions):
{shots}

{note}Items (direction=out means money spent; avg_amount in EUR):
{json.dumps(items, ensure_ascii=False)}"""
    return static, dynamic


def llm_label(items: list[dict], model: str, examples: list[tuple[str, str, str]],
              backend: LLMBackend | None = None, names=frozenset()) -> tuple[list[dict], float]:
    backend = backend or ClaudeCodeBackend()
    items = [redact_item(i, names) for i in items]
    examples = [(redact(k, names), redact(n, names), c) for k, n, c in examples]
    static, dynamic = label_prompt(items, examples)
    data, usage = backend.complete(static, dynamic, label_schema(), model, "label", len(items))
    llm_label.last_usage = usage
    return data["results"], usage.cost_usd


def merchant_items(con, keys: list[str], index=None, k: int = 5) -> list[dict]:
    items = []
    for i, key in enumerate(keys):
        n, avg, ex, types, fx = con.execute(f"""
            SELECT COUNT(*), ROUND(AVG(t.amount),2), MAX(e.merchant_raw), GROUP_CONCAT(DISTINCT e.tx_type),
                   GROUP_CONCAT(DISTINCT e.fx_currency)
            FROM tx_enriched e JOIN transactions t USING(tx_key)
            WHERE e.merchant_key=? AND {needs_llm_sql()}""", (key,)).fetchone()
        it = {"id": i, "key": key, "raw_example": ex, "n": n,
              "direction": "in" if (avg or 0) > 0 else "out", "avg_amount": abs(avg or 0), "types": types}
        if fx:
            it["foreign_currency"] = fx
        if index is not None and set((types or '').split(',')) <= knn_mod.MERCHANT_TYPES:
            sim = [{"key": mk, "name": name, "category": cat} for _, mk, name, cat in index.neighbours(key, k)]
            if sim:
                it["similar"] = sim
        items.append(it)
    return items


def user_examples(con, limit=40, allow=()):
    """The user's own labels shown to the LLM as conventions: shops and creditors only (P1)."""
    from coach.classify.candidates import eligible_label_keys
    ok = eligible_label_keys(con, allow)
    rows = con.execute("""SELECT m.merchant_key, m.merchant_name, m.category FROM merchants m
                          WHERE source='user' ORDER BY updated_at DESC""").fetchall()
    return [r for r in rows if r[0] in ok][:limit]


class LabelRunError(RuntimeError):
    """Some batches failed; the ones that succeeded were already stored. `failed`: [(keys, reason)]."""

    def __init__(self, failed, results, cost):
        super().__init__(f"{len(failed)} batch(es) failed ({sum(len(k) for k, _ in failed)} merchants not labelled; "
                         f"re-run to retry them): " + "; ".join(f"{r}" for _, r in failed[:3]))
        self.failed, self.results, self.cost = failed, results, cost


def prepare_jobs(con, keys, model, batch, index=None, knn_k=5, names=frozenset(), allow=(), exclude_examples=frozenset()):
    """[(batch keys, job kwargs)]: the exact (redacted) requests that would be sent. `exclude_examples` (E12-3): merchant keys that must not
    appear as a user example (the gold merchants of a shadow evaluation: the model must not be shown the answer)."""
    examples = [e for e in user_examples(con, allow=allow) if e[0] not in exclude_examples]
    out = []
    for i in range(0, len(keys), batch):
        b = keys[i:i + batch]
        items = [redact_item(it, names) for it in merchant_items(con, b, index, knn_k)]
        ex = [(redact(k, names), redact(n, names), c) for k, n, c in examples]
        static, dynamic = label_prompt(items, ex)
        out.append((b, dict(static=static, dynamic=dynamic, schema=label_schema(), model=model, purpose="label",
                            items=len(items))))
    return out


def dry_run_payload(jobs) -> list[dict]:
    """What `classify run --dry-run` prints: the redacted items, examples and hints of every request, no call."""
    import hashlib
    out = []
    for i, (b, j) in enumerate(jobs, 1):
        body = j["dynamic"]
        out.append({"request": i, "merchants": len(b), "static_prompt_chars": len(j["static"]),
                    "static_prompt_sha256": hashlib.sha256(j["static"].encode()).hexdigest()[:16],
                    "dynamic_prompt": body})
    return out


def _call_store(on_batch, keys, pairs, model):
    if on_batch is None:
        return
    import inspect
    try:
        nparams = len(inspect.signature(on_batch).parameters)
    except (TypeError, ValueError):
        nparams = 2
    on_batch(keys, pairs, model) if nparams >= 3 else on_batch(keys, pairs)


def _save_batch(con, backend, batch_id, prepared, purpose="label", status="pending") -> None:
    """Remember what each request of the batch asks about BEFORE the batch exists at the provider (status
    'submitting', placeholder id), then attach the real id."""
    con.execute("INSERT OR REPLACE INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs, purpose) "
                "VALUES (?,?,?,?,?,?,?)", (batch_id, batch_id, backend.name, now_iso(), status, len(prepared), purpose))
    for i, (b, job) in enumerate(prepared):
        con.execute("INSERT OR REPLACE INTO llm_batch_jobs(batch_id, custom_id, keys_json, model, purpose, items) "
                    "VALUES (?,?,?,?,?,?)", (batch_id, f"job{i}", json.dumps(b), job["model"], job.get("purpose"),
                                             job.get("items")))
    con.commit()


def attach_batch(con, placeholder: str, batch_id: str) -> None:
    con.execute("UPDATE llm_batch_jobs SET batch_id=? WHERE batch_id=?", (batch_id, placeholder))
    con.execute("UPDATE llm_batches SET fingerprint=?, batch_id=?, status='pending' WHERE fingerprint=?",
                (batch_id, batch_id, placeholder))
    con.commit()


def abandon_unrecorded(con, backend) -> list[tuple[str, str]]:
    """The user accepts the risk of paying again for batches that cannot be collected: those whose id was never
    recorded ('submitting'), legacy pending batches without a request mapping, and pending batches whose purpose is
    unknown. Done only for this backend, only after the provider's recent batches could be listed (and after trying to
    attach the ones that can be found). Returns [(id, why)] of what was abandoned."""
    if not hasattr(backend, "list_recent_batches"):
        raise backends.BatchUnresolved("this backend cannot list batches at the provider: nothing abandoned")
    try:
        backend.list_recent_batches(30)
    except Exception as e:                                        # noqa: BLE001
        raise backends.BatchUnresolved(f"could not list the provider's recent batches ({type(e).__name__}): "
                                       "nothing abandoned; retry when the provider is reachable")
    try:
        recover_submitting(con, backend)                          # a batch that CAN be found is attached, not dropped
    except backends.BatchUnresolved:
        pass
    out = []
    for bid, st, jobs, n_jobs, purpose in con.execute(
            """SELECT batch_id, status, jobs, (SELECT COUNT(*) FROM llm_batch_jobs j WHERE j.batch_id=b.batch_id),
                      purpose FROM llm_batches b WHERE backend=? AND status IN ('submitting','pending')""",
            (backend.name,)).fetchall():
        if st == "submitting":
            why = f"submitted without its id being recorded ({jobs} requests)"
        elif n_jobs == 0:
            why = "created by an older version, no saved request mapping"
        elif purpose is None:
            why = "created before purposes were recorded (label or compare is unknown)"
        else:
            continue
        con.execute("UPDATE llm_batches SET status='abandoned' WHERE batch_id=?", (bid,))
        out.append((bid, why))
    con.commit()
    return out


def claim_legacy_batches(con, backend, purpose: str) -> int:
    """Say what the pending batches of unknown purpose are for (you know: you launched them)."""
    n = con.execute("UPDATE llm_batches SET purpose=? WHERE purpose IS NULL AND status='pending' AND backend=? AND "
                    "EXISTS (SELECT 1 FROM llm_batch_jobs j WHERE j.batch_id=llm_batches.batch_id)",
                    (purpose, backend.name)).rowcount
    con.commit()
    return n


def recover_submitting(con, backend) -> None:
    """A run died between `submit` and recording the batch id. Look for the batch at the provider: one batch that
    we do not know, created after our row, with exactly the number of requests we planned, is ours and is
    attached. Anything else is ambiguous: raise BatchUnresolved instead of submitting (and paying) again."""
    from datetime import datetime
    rows = con.execute("SELECT fingerprint, created_at, jobs FROM llm_batches WHERE status='submitting' AND backend=?",
                       (backend.name,)).fetchall()
    if not rows:
        return
    known = {r[0] for r in con.execute("SELECT batch_id FROM llm_batches WHERE status<>'submitting'")}
    try:
        recent = backend.list_recent_batches(30) if hasattr(backend, "list_recent_batches") else []
    except Exception as e:                                        # noqa: BLE001
        raise backends.BatchUnresolved(
            f"provider unreachable ({type(e).__name__}): an earlier run may have submitted a batch it did not record "
            "and it cannot be looked for right now. Retry later; nothing new is submitted meanwhile.")
    from datetime import timezone

    def parse(t):          # naive timestamps are UTC (ours are stored as UTC, the provider's end in Z)
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    for placeholder, created, jobs in rows:
        mine = []
        for b in recent:
            try:
                if b["id"] not in known and b["requests"] == jobs and parse(b["created_at"]) >= parse(created):
                    mine.append(b)
            except ValueError:
                pass
        if len(mine) == 1:
            attach_batch(con, placeholder, mine[0]["id"])
            known.add(mine[0]["id"])
            print(f"  recovered the batch of an interrupted run: {mine[0]['id']}")
        else:
            raise backends.BatchUnresolved(
                f"an earlier run may have submitted a batch of {jobs} requests without recording it "
                f"({len(mine)} candidate(s) found at the provider): nothing new is submitted. Check the Anthropic "
                "console; `coach classify run --abandon-unrecorded` forgets it (you may pay twice).")


class Collected(int):
    """Number of labels stored by collect_pending_batches; `.failed` lists [(keys, reason)] that could not be."""
    failed: list


def process_batch(con, backend, batch_id, on_batch=None, wait_seconds=None, results=None):
    """Collect one saved batch from its custom_id -> merchants mapping. Each succeeded job is validated, its usage
    logged (once: unique per batch result), its labels stored and its job row marked 'stored' in ONE commit (an
    interruption never loses paid results, a re-run skips what is stored, a second collector cannot count it
    twice); failed jobs are marked 'failed'. The batch is marked ended LAST. Returns the failed jobs
    [(custom_id, keys, model, reason)]. A batch the provider does not know (404) is marked failed and never
    retried; a legacy batch without a request mapping is left pending with a warning. BatchPending propagates
    when it is still running."""
    rows = con.execute("SELECT custom_id, keys_json, model, purpose, items FROM llm_batch_jobs "
                       "WHERE batch_id=? AND status='pending' ORDER BY rowid", (batch_id,)).fetchall()
    total = con.execute("SELECT COUNT(*) FROM llm_batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()[0]
    if total == 0:
        print(f"  warning: batch {batch_id} was created by an older version and has no saved request mapping: it "
              "cannot be collected safely and stays pending (`coach classify run --abandon-unrecorded` forgets it)")
        return []
    failed, meta = [], {r[0]: {"model": r[2], "purpose": r[3], "items": r[4]} for r in rows}
    if rows:
        try:
            backend.wait(batch_id, wait_seconds)
        except backends.BatchMissing as e:
            con.execute("UPDATE llm_batch_jobs SET status='failed', error=? WHERE batch_id=? AND status='pending'",
                        (str(e)[:200], batch_id))
            con.execute("UPDATE llm_batches SET status='failed' WHERE batch_id=?", (batch_id,))
            con.commit()
            print(f"  {e}; marked failed, its merchants will be asked again")
            return [(r[0], json.loads(r[1]), r[2], str(e)) for r in rows]
        got = backend.fetch(batch_id, meta)
        for cid, keys_json, model, purpose, items in rows:
            keys, oc = json.loads(keys_json), got.get(cid)
            err = None
            try:
                if oc is not None and oc.usage is not None:
                    oc.usage.ref = f"{batch_id}:{cid}"
                    if not record_usage(con, oc.usage):          # someone else already collected this result
                        con.execute("UPDATE llm_batch_jobs SET status='stored' WHERE batch_id=? AND custom_id=? "
                                    "AND status='pending'", (batch_id, cid))
                        con.commit()
                        continue
                if oc is None or oc.error or oc.data is None:
                    err = (oc.error if oc else None) or "no result returned for this job"
                else:
                    pairs = [(keys[r["id"]], r) for r in validate_results(oc.data.get("results"), len(keys))]
                    _call_store(on_batch, keys, pairs, model)
                    if results is not None:
                        results.extend(pairs)
            except Exception as e:                              # noqa: BLE001
                err = f"storing the results failed: {type(e).__name__}: {str(e)[:120]}"
                con.rollback()
                if oc is not None and oc.usage is not None:     # the call was paid for: keep the usage row
                    record_usage(con, oc.usage)
            if err:
                con.execute("UPDATE llm_batch_jobs SET status='failed', error=? WHERE batch_id=? AND custom_id=?",
                            (err[:200], batch_id, cid))
                failed.append((cid, keys, model, err))
            else:
                con.execute("UPDATE llm_batch_jobs SET status='stored' WHERE batch_id=? AND custom_id=?",
                            (batch_id, cid))
            con.commit()
    if not con.execute("SELECT 1 FROM llm_batch_jobs WHERE batch_id=? AND status='pending'", (batch_id,)).fetchone():
        con.execute("UPDATE llm_batches SET status='ended' WHERE batch_id=? AND status='pending'", (batch_id,))
        con.commit()
    return failed


def collect_pending_batches(con, backend, handlers=None) -> int:
    """Before anything new is submitted: collect the batches left pending by earlier runs, but only those whose
    purpose this run handles (`handlers`: {purpose: on_batch}; a bare callable handles 'label'). A 'compare' batch is
    never stored as primary labels. Raises BatchPending if one is still running and BatchUnresolved if an earlier run
    may have submitted a batch it never recorded. Returns a Collected count (`.failed` = what could not be stored)."""
    if not hasattr(backend, "wait"):
        return Collected(0)
    if callable(handlers):
        handlers = {"label": handlers}
    handlers = handlers or {}
    recover_submitting(con, backend)
    results: list = []
    failures: list = []
    for bid, purpose in con.execute("SELECT batch_id, purpose FROM llm_batches WHERE status='pending' "
                                    "AND backend=? ORDER BY created_at", (backend.name,)).fetchall():
        if purpose is None:
            if con.execute("SELECT 1 FROM llm_batch_jobs WHERE batch_id=?", (bid,)).fetchone():
                print(f"  warning: pending batch {bid} was created before batch purposes were recorded: it may hold "
                      "primary labels or a model comparison, so it is not collected. Say which with `coach classify "
                      "run --claim-legacy-batches label|compare`, or forget it with --abandon-unrecorded")
                continue
        elif purpose not in handlers:
            continue
        for _cid, keys, _m, why in process_batch(con, backend, bid, handlers.get(purpose), wait_seconds=0, results=results):
            failures.append((keys, why))
    n = Collected(len(results))
    n.failed = failures
    return n


def label_keys(con, keys, model, batch, workers, backend: LLMBackend | None = None, index=None,
               knn_k: int = 5, names=frozenset(), on_batch=None, allow=(), purpose: str = "label", exclude_examples=frozenset()):
    """Label `keys` in batches of `batch` merchants. Returns ([(key, result)], total cost).

    Each finished batch is validated, its usage logged in `llm_usage` (also when the answer was unusable),
    handed to `on_batch(keys, results[, model])` and COMMITTED before the next one is looked at, so a later failure
    never loses what was already paid for; if storing one batch fails, the others are still processed and their
    usage logged. LabelRunError is raised at the end if anything failed. With the Anthropic Batch API the run is
    one asynchronous batch recorded BEFORE it is submitted and saved by id and per-request mapping (see
    :func:`process_batch`); only failed requests are retried, synchronously. `purpose` ('label' | 'compare') is
    saved with the batch so that a later run only collects what it can store correctly."""
    from concurrent.futures import as_completed
    import uuid
    backend = backend or ClaudeCodeBackend()
    prepared = prepare_jobs(con, keys, model, batch, index, knn_k, names, allow, exclude_examples)
    for _, j in prepared:
        j["purpose"] = purpose
    cost, results, failed = 0.0, [], []
    state = {"unknown": False}

    def book(usage):
        nonlocal cost
        record_usage(con, usage)
        if usage.cost_usd is None:
            state["unknown"] = True
        cost += usage.cost_usd or 0.0

    def finish(b, data, usage, done, total):
        book(usage)
        con.commit()                                      # the paid call is on record whatever happens next
        try:
            pairs = [(b[r["id"]], r) for r in validate_results(data.get("results") if isinstance(data, dict) else None,
                                                                len(b))]
            _call_store(on_batch, b, pairs, model)
            con.commit()
        except Exception as e:                            # noqa: BLE001  (storing failed: report, carry on)
            con.rollback()
            failed.append((b, f"storing the results failed: {type(e).__name__}: {str(e)[:120]}"))
            return False
        results.extend(pairs)
        print(f"  batch {done}/{total} ok ({len(pairs)} labels)", flush=True)
        return True

    if getattr(backend, "use_batch", False) and len(prepared) > 1:
        placeholder = f"submitting-{uuid.uuid4().hex}"
        _save_batch(con, backend, placeholder, prepared, purpose, "submitting")
        try:
            batch_id = backend.submit([j for _, j in prepared])
        except Exception as e:                            # noqa: BLE001
            if 400 <= (getattr(e, "status_code", 0) or 0) < 500:       # refused outright: nothing was created
                con.execute("DELETE FROM llm_batch_jobs WHERE batch_id=?", (placeholder,))
                con.execute("DELETE FROM llm_batches WHERE batch_id=?", (placeholder,))
                con.commit()
            raise                                         # else: left 'submitting', the next run looks for it
        attach_batch(con, placeholder, batch_id)
        stored: list = []
        bad = process_batch(con, backend, batch_id, on_batch, backend.max_wait, stored)
        results.extend(stored)
        for cid, b, _m, _why in bad:                      # retry only the jobs that failed, one at a time
            job = prepared[int(cid[3:])][1]
            try:
                data, usage = backend.complete(**job)
                if finish(b, data, usage, 1, 1):
                    con.execute("UPDATE llm_batch_jobs SET status='stored' WHERE batch_id=? AND custom_id=?",
                                (batch_id, cid))
                    con.commit()
            except Exception as e:                        # noqa: BLE001
                if getattr(e, "usage", None):
                    book(e.usage)
                    con.commit()
                failed.append((b, f"{type(e).__name__}: {str(e)[:160]}"))
    else:
        with ThreadPoolExecutor(workers) as ex_:
            futs = {ex_.submit(lambda j=j: backend.complete(**j)): b for b, j in prepared}
            done = 0
            for f in as_completed(futs):
                b = futs[f]
                try:
                    data, usage = f.result()
                except Exception as e:                    # noqa: BLE001
                    if getattr(e, "usage", None):
                        book(e.usage)
                        con.commit()
                    failed.append((b, f"{type(e).__name__}: {str(e)[:160]}"))
                    continue
                done += 1
                finish(b, data, usage, done, len(prepared))
    label_keys.unknown_cost = state["unknown"]
    if failed:
        raise LabelRunError(failed, results, cost)
    return results, cost


def web_request(items: list[dict], names=frozenset()) -> tuple[str, str, dict]:
    """(static, dynamic, schema) of one web-enrichment request, items REDACTED. Used by the real call and by `enrich --dry-run`."""
    items = [redact_item(i, names) for i in items]
    static = f"""Identify these merchants from a French/Italian household's bank statement.
Descriptors are truncated and the town has been removed. For each one, run a web search with the name
and the word France (e.g. "QUINCAILLERIE ZORBAX France"), find what kind of business it is, then pick the best category.
Classify by what the merchant sells, also for refunds. Give confidence ≥0.8 only when a search
result clearly identifies the business; put a short source/evidence note in `evidence`.

Categories:
{categories_text()}"""
    dynamic = f"\n\nItems:\n{json.dumps(items, ensure_ascii=False)}"
    schema = {"type": "object", "properties": {"results": {"type": "array", "items": {
        "type": "object", "properties": {
            "id": {"type": "integer"}, "merchant": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "confidence": {"type": "number"}, "recurring_hint": {"type": "boolean"},
            "evidence": {"type": "string"}},
        "required": ["id", "merchant", "category", "confidence", "recurring_hint", "evidence"]}}},
        "required": ["results"]}
    return static, dynamic, schema


def web_label(items: list[dict], model: str, backend: LLMBackend | None = None, names=frozenset()):
    backend = backend or ClaudeCodeBackend()
    static, dynamic, schema = web_request(items, names)
    data, usage = backend.complete(static, dynamic, schema, model, "enrich", len(items), web_search=True)
    web_label.last_usage = usage
    return data["results"], usage.cost_usd
