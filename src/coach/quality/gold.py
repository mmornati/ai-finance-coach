"""The gold set (E12-2): transactions whose category the USER has confirmed, and how it grows.

* Bootstrap: what the user already decided, marked by ORIGIN, never invented: a transaction override (``override``), the dominant part
  of a split (``split``), a memory annotation with an explicit category (``annotation``), a merchant label of the user
  (``merchant_label``). ``coach eval gold bootstrap`` refreshes those rows (and drops the ones the user's own decision no longer
  supports); a label given by hand (``manual``: the terminal loop or the web page) is never touched by a bootstrap.
* Sampling: transactions to label next, never ones already in the gold set, never ones the user already decided: by money
  (probability proportional to the amount, so that the money-weighted accuracy is estimated without bias) or stratified (an even number
  per prediction source and category group).
* Labelling writes ``gold_labels`` and nothing else: no merchant label, no override, no annotation changes.
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from typing import Callable, Optional

from coach.classify import rules as R
from coach.db import now_iso

ORIGINS = ("manual", "merchant_label", "annotation", "override", "split")
LABELERS = ("user", "memory", "correction")
BOOTSTRAP_ORIGINS = ("merchant_label", "annotation", "override", "split")
# the pipeline source that a label of this kind would produce (a prediction from that source agrees with itself by construction)
ORIGIN_SOURCE = {"merchant_label": "user", "annotation": "memory", "override": "override", "split": "split"}
LABELER_SOURCE = {"user": "user", "memory": "memory", "correction": "override"}
# who is the labeler of each origin
ORIGIN_LABELER = {"merchant_label": "user", "annotation": "memory", "override": "correction", "split": "correction", "manual": "user"}
# strongest first: when several decisions of the user reach one transaction the strongest one is its origin
ORIGIN_RANK = {"manual": 0, "split": 1, "override": 2, "annotation": 3, "merchant_label": 4}
SAMPLE_SOURCES = ("llm", "llm_web", "knn", "entity", "rule", "none")        # what is worth labelling: the automatic decisions
STRATEGIES = ("money", "stratified")


class GoldError(ValueError):
    pass


# ---------------------------------------------------------------- what a gold row can be used to evaluate (method v2)
# merchant_truth: "this merchant is this category" - a hand label, a merchant label of the user, a memory annotation WITHOUT a context condition.
#                 The classifier (memory off) can be scored on it.
# context_rule:   a decision that depends on the transaction's context (an annotation with category_in / weekdays / amount / date bounds /
#                 tx_keys, a per-transaction override, a split): the classifier is not expected to know it. It is scored only with memory ON.
CONTEXT_KEYS = ("category_in", "weekdays", "amount_min", "amount_max", "date_from", "date_to", "tx_keys")
KIND_OF_KEY = {"amount_min": "amount", "amount_max": "amount", "date_from": "date", "date_to": "date"}


def annotation_kind(ann) -> str:
    """'context_free' or the conditions of an annotation joined with '+' (e.g. 'category_in', 'amount+date')."""
    m = (ann or {}).get("match") or {}
    kinds = sorted({KIND_OF_KEY.get(k, k) for k in CONTEXT_KEYS if k in m})
    return "+".join(kinds) or "context_free"


def annotation_index(cfg) -> dict:
    return {a.get("id"): a for a in R.load_annotations(cfg.memory_dir)}


def truth_of(g: dict, anns: dict) -> tuple[str, str, Optional[dict]]:
    """(truth class, kind for breakdowns, the annotation) of a gold row."""
    o = g["origin"]
    if o in ("manual", "merchant_label"):
        return "merchant_truth", o, None
    if o == "annotation":
        aid = (g.get("note") or "").removeprefix("annotation ").strip()
        a = anns.get(aid)
        if a is None:
            return "context_rule", "annotation:unknown", None
        k = annotation_kind(a)
        return ("merchant_truth" if k == "context_free" else "context_rule"), f"annotation:{k}", a
    return "context_rule", o, None


def label_source(labeled_by: str, origin: str) -> str:
    """The pipeline source this gold label is the same thing as (tautology guard of the evaluation)."""
    return ORIGIN_SOURCE.get(origin) or LABELER_SOURCE.get(labeled_by, "user")


# ---------------------------------------------------------------- the rows

def set_gold(con, tx_key: str, category: str, *, labeled_by: str = "user", origin: str = "manual", note: Optional[str] = None,
             commit: bool = True) -> None:
    if category not in R.CATEGORIES or category == "other.uncategorized":
        raise GoldError(f"{category!r} is not a category of the taxonomy that can be a label (see `coach taxonomy list`)")
    if labeled_by not in LABELERS or origin not in ORIGINS:
        raise GoldError("bad labeler or origin")
    if not con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (tx_key,)).fetchone():
        raise GoldError(f"no transaction {tx_key!r}")
    con.execute("""INSERT INTO gold_labels(tx_key, category, labeled_by, labeled_at, note, origin) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(tx_key) DO UPDATE SET category=excluded.category, labeled_by=excluded.labeled_by,
                   labeled_at=excluded.labeled_at, note=excluded.note, origin=excluded.origin""",
                (tx_key, category, labeled_by, now_iso(), (note or None) and note[:300], origin))
    if commit:
        con.commit()


def remove_gold(con, tx_key: str, commit: bool = True) -> bool:
    cur = con.execute("DELETE FROM gold_labels WHERE tx_key=?", (tx_key,))
    if commit:
        con.commit()
    return cur.rowcount > 0


def gold_rows(con) -> list[dict]:
    return [dict(zip(("tx_key", "category", "labeled_by", "labeled_at", "note", "origin"), r)) for r in con.execute(
        "SELECT tx_key, category, labeled_by, labeled_at, note, origin FROM gold_labels ORDER BY tx_key")]


def counts(con) -> dict:
    by_origin = dict(con.execute("SELECT origin, COUNT(*) FROM gold_labels GROUP BY 1 ORDER BY 1"))
    by_by = dict(con.execute("SELECT labeled_by, COUNT(*) FROM gold_labels GROUP BY 1 ORDER BY 1"))
    cats = con.execute("SELECT COUNT(DISTINCT category) FROM gold_labels").fetchone()[0]
    return {"total": sum(by_origin.values()), "by_origin": by_origin, "by_labeled_by": by_by, "categories": cats}


# ---------------------------------------------------------------- bootstrap

def desired_bootstrap(con, cfg) -> dict[str, dict]:
    """tx_key -> {category, labeled_by, origin, note}: what the user has already decided, one origin per transaction (the strongest)."""
    want: dict[str, dict] = {}

    def offer(tx_key, category, origin, note=None):
        if category in (None, "other.uncategorized") or category not in R.CATEGORIES:
            return
        cur = want.get(tx_key)
        if cur is None or ORIGIN_RANK[origin] < ORIGIN_RANK[cur["origin"]]:
            want[tx_key] = {"category": category, "labeled_by": ORIGIN_LABELER[origin], "origin": origin, "note": note}

    for r in R.categorised(con, memory_dir=cfg.memory_dir, use_splits=False):
        src = r["source"]
        if src == "override":
            offer(r["tx_key"], r["category"], "override")
        elif src == "memory":
            offer(r["tx_key"], r["category"], "annotation", f"annotation {r['annotation']}" if r.get("annotation") else None)
        elif src == "user":
            offer(r["tx_key"], r["category"], "merchant_label")
    # an override that an annotation outranks is still the user's own decision on THAT transaction: keep it as the origin
    for tx_key, cat in con.execute("SELECT tx_key, category FROM tx_overrides"):
        if con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (tx_key,)).fetchone():
            offer(tx_key, cat, "override")
    parts: dict[str, list] = {}
    for tx_key, amount, cat in con.execute("SELECT tx_key, amount, category FROM tx_splits ORDER BY id"):
        parts.setdefault(tx_key, []).append((abs(amount), cat))
    for tx_key, ps in parts.items():
        if con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (tx_key,)).fetchone():
            offer(tx_key, max(ps, key=lambda p: p[0])[1], "split", f"dominant part of {len(ps)}")
    return want


def bootstrap(con, cfg, dry_run: bool = False) -> dict:
    """Refresh the bootstrap rows of the gold set. Returns the counts by origin: wanted, added, updated, removed, and the manual rows kept."""
    want = desired_bootstrap(con, cfg)
    have = {r["tx_key"]: r for r in gold_rows(con)}
    added, updated, removed = Counter(), Counter(), Counter()
    for k, w in want.items():
        old = have.get(k)
        if old is not None and old["origin"] == "manual":
            continue                                               # a label given by hand always wins
        if old is None:
            added[w["origin"]] += 1
        elif (old["category"], old["origin"]) != (w["category"], w["origin"]):
            updated[w["origin"]] += 1
        else:
            continue
        if not dry_run:
            set_gold(con, k, w["category"], labeled_by=w["labeled_by"], origin=w["origin"], note=w["note"], commit=False)
    for k, old in have.items():
        if old["origin"] != "manual" and k not in want:
            removed[old["origin"]] += 1
            if not dry_run:
                remove_gold(con, k, commit=False)
    if not dry_run:
        con.commit()
    return {"wanted": dict(Counter(w["origin"] for w in want.values())), "added": dict(added), "updated": dict(updated),
            "removed": dict(removed), "manual_kept": sum(1 for r in have.values() if r["origin"] == "manual"),
            "dry_run": dry_run, "gold": counts(con) if not dry_run else None}


# ---------------------------------------------------------------- sampling

def _candidates(con, cfg) -> list[dict]:
    gold = {r[0] for r in con.execute("SELECT tx_key FROM gold_labels")}
    out = []
    for r in R.categorised(con, memory_dir=cfg.memory_dir, use_splits=False):
        if r["tx_key"] in gold or r["source"] not in SAMPLE_SOURCES:
            continue
        out.append(r)
    out.sort(key=lambda r: r["tx_key"])                           # a stable base order: the sample depends on the seed only
    return out


def _display(con, rows: list[dict]) -> list[dict]:
    keys = [r["tx_key"] for r in rows]
    info = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        for k, desc, acct, mname, conf in con.execute(
                f"""SELECT t.tx_key, t.description, COALESCE(a.label, a.name, a.uid), m.merchant_name, m.confidence
                    FROM transactions t LEFT JOIN accounts a ON a.uid=t.account_uid
                    LEFT JOIN tx_enriched e ON e.tx_key=t.tx_key LEFT JOIN merchants m ON m.merchant_key=e.merchant_key
                    WHERE t.tx_key IN ({','.join('?' * len(chunk))})""", chunk):
            info[k] = (desc, acct, mname, conf)
    out = []
    for r in rows:
        desc, acct, mname, conf = info.get(r["tx_key"], ("", "", None, None))
        out.append({"tx_key": r["tx_key"], "date": r["date"], "amount": r["amount"], "description": desc or "", "account": acct or "",
                    "merchant_key": r["key"], "merchant": mname or r["merchant"], "category": r["category"], "source": r["source"],
                    "confidence": conf, "type": r["type"]})
    return out


def sample(con, cfg, n: int = 300, strategy: str = "money", seed: int = 7) -> list[dict]:
    """`n` transactions to label, not already gold, among the ones an automatic step decided."""
    if strategy not in STRATEGIES:
        raise GoldError(f"strategy must be one of {', '.join(STRATEGIES)}")
    cands = _candidates(con, cfg)
    rng = random.Random(seed)
    if strategy == "money":
        # weighted sampling without replacement (Efraimidis-Spirakis): the chance of a transaction is proportional to its amount
        keyed = sorted(((rng.random() ** (1.0 / max(abs(r["amount"]), 0.01)), r["tx_key"], r) for r in cands),
                       key=lambda t: (-t[0], t[1]))
        chosen = [r for _, _, r in keyed[:n]]
    else:
        strata: dict[tuple, list] = {}
        for r in cands:
            strata.setdefault((r["source"], r["category"].split(".")[0]), []).append(r)
        for rows in strata.values():
            rng.shuffle(rows)
        order = sorted(strata, key=lambda k: (len(strata[k]), k))
        chosen = []
        while len(chosen) < n and any(strata[k] for k in order):
            for k in order:
                if strata[k] and len(chosen) < n:
                    chosen.append(strata[k].pop())
    return _display(con, chosen)


# ---------------------------------------------------------------- the terminal loop

def _pick_category(input_fn, out) -> Optional[str]:
    while True:
        ans = input_fn("  category id (or a word to search, empty to cancel): ").strip()
        if not ans:
            return None
        if ans in R.CATEGORIES and ans != "other.uncategorized":
            return ans
        hits = [c for c in R.CATEGORIES if ans.lower() in c.lower() or ans.lower() in R.CATEGORIES[c].lower()][:12]
        out("  no such category id; matches: " + (", ".join(hits) if hits else "(none)"))


def label_loop(con, cfg, items: list[dict], *, input_fn: Callable[[str], str] = input, out=print) -> dict:
    """Interactive labelling of `items` (output of :func:`sample`): shows explain() and the current label, then accept / fix / skip / quit.
    Writes gold_labels only."""
    from coach.memory import explain as explain_mod
    from coach.memory.store import MemoryStore
    store = MemoryStore(cfg.memory_dir, history=False)
    res = {"accepted": 0, "fixed": 0, "skipped": 0, "left": 0}
    for i, it in enumerate(items):
        out(f"\n=== [{i + 1}/{len(items)}] " + "=" * 50)
        try:
            out(explain_mod.format_explanation(explain_mod.explain(con, store, it["tx_key"])))
        except Exception as e:                                                      # noqa: BLE001
            out(f"{it['tx_key']}  {it['date']}  {it['amount']:+.2f}  {it['description']}  (no explanation: {type(e).__name__})")
        out(f"\ncurrent label: {it['category']} (source {it['source']}"
            + (f", confidence {it['confidence']:.2f}" if it.get("confidence") is not None else "") + ")")
        while True:
            a = input_fn("[a]ccept the current label, [f]ix it, [s]kip, [q]uit > ").strip().lower()[:1]
            if a == "a":
                if it["category"] == "other.uncategorized":
                    out("  there is no label to accept: fix it with f")
                    continue
                set_gold(con, it["tx_key"], it["category"])
                res["accepted"] += 1
                break
            if a == "f":
                cat = _pick_category(input_fn, out)
                if cat:
                    set_gold(con, it["tx_key"], cat)
                    res["fixed"] += 1
                    break
                continue
            if a == "s":
                res["skipped"] += 1
                break
            if a == "q":
                res["left"] = len(items) - i
                return res
    return res


def tty() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
