"""Previews that are the real thing: a change is applied inside a transaction that is rolled back, and the category of every
affected transaction is recomputed by the classifier itself (:func:`coach.classify.rules.categorised`), before and after.
So what the preview announces is, by construction, what the write does (``tests/test_api.py`` checks preview == effect).
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Iterable

from coach.analytics.common import money_str, to_cents
from coach.classify import rules as R

SOURCE_REASON = {"override": "a per-transaction override", "transfer_link": "a linked internal transfer",
                 "type_rule": "a rule on the kind of operation (card, direct debit...)"}


def decide(con, annotations: list, tx_keys: Iterable[str]) -> dict[str, dict]:
    """tx_key -> the classifier's final row (category, source, tags, annotation id, amount...) for those transactions."""
    return {r["tx_key"]: r for r in R.categorised(con, annotations=annotations, only_tx_keys=set(tx_keys), use_splits=False)}


def _reason(row: dict) -> str:
    if row["source"] == "memory":
        return f"the memory annotation {row.get('annotation')!r}"
    if row["pre_source"] in SOURCE_REASON:
        return SOURCE_REASON[row["pre_source"]]
    return f"its {row['pre_source']} category"


def summarise(before: dict, after: dict, category: str) -> dict:
    """What a change to `category` really does to these transactions."""
    changing = [k for k in after if after[k]["category"] != before[k]["category"]]
    already = [k for k in after if before[k]["category"] == category]
    blocked = Counter(_reason(after[k]) for k in after if after[k]["category"] != category and before[k]["category"] != category)
    froms = Counter(before[k]["category"] for k in changing)
    tos = Counter(after[k]["category"] for k in changing)
    return {"count": len(changing), "already": len(already), "matched": len(after),
            "total": money_str(sum(to_cents(after[k]["amount"]) for k in changing)),
            "from_categories": [{"category": c, "n": n} for c, n in froms.most_common(6)],
            "to_categories": [{"category": c, "n": n} for c, n in tos.most_common(6)],
            "blocked": [{"reason": r, "n": n} for r, n in blocked.items()],
            "tag_changes": sum(1 for k in after if after[k]["tags"] != before[k]["tags"]),
            "date_min": min((after[k]["date"] for k in after), default=None),
            "date_max": max((after[k]["date"] for k in after), default=None)}


def simulate_write(state, keys_of: Callable, apply: Callable, annotations_after=None) -> tuple[dict, dict]:
    """(before, after) for the transactions `keys_of(con)` returns. `apply(con)` makes the change WITHOUT committing; it is
    rolled back whatever happens. `annotations_after`: the annotation list to use after the change (annotation previews)."""
    from coach.classify import rules
    anns = rules.load_annotations(state.cfg.memory_dir)
    with state.write(quiet=True) as con:
        keys = list(keys_of(con))
        before = decide(con, anns, keys)
        try:
            apply(con)
            after = decide(con, anns if annotations_after is None else annotations_after, keys)
        finally:
            con.rollback()
    return before, after
