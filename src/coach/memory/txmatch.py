"""Matching memory annotations against the transactions in the database: used by `memory check` (does an
annotation match 0 / too many transactions, is it shadowed), `memory annotate` (preview) and `explain`.

Semantics are exactly those of the classifier: :func:`coach.classify.rules.annotation_mismatch` decides, the FIRST
matching annotation of the file wins (``annotation_for``)."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from coach.classify import rules as R
from coach.memory import schemas


def ann_plain(a: schemas.Annotation) -> dict:
    """The dict form the classifier works with (None criteria dropped)."""
    match = {k: v for k, v in a.match.model_dump(mode="python").items() if v is not None}
    d = {"id": a.id, "match": match}
    if a.category:
        d["category"] = a.category
    if a.tags:
        d["tags"] = list(a.tags)
    if a.event:
        d["event"] = a.event
    if a.note:
        d["note"] = a.note
    return d


def load_txs(con, rules: Optional[dict] = None) -> list[dict]:
    """All analysable transactions with their category BEFORE memory (split parts not expanded)."""
    return list(R.resolved_rows(con, rules, include_excluded=False, use_splits=False))


def matches(ann: dict, t: dict) -> bool:
    return R.annotation_mismatch(ann, t["tx_key"], t["key"], t["desc"], t["date"], t["amount"], t["category"],
                                 t["op_date"], t["aliases"]) is None


@dataclass
class AnnStats:
    id: str
    matched: int = 0
    won: int = 0
    total: float = 0.0
    shadowed_by: Counter = field(default_factory=Counter)      # earlier annotation id -> txs it takes from this one
    per_account: Counter = field(default_factory=Counter)


def analyse(anns: list[dict], txs: list[dict]) -> dict[str, AnnStats]:
    stats = {a["id"]: AnnStats(a["id"]) for a in anns}
    for t in txs:
        hit = [a for a in anns if matches(a, t)]
        for rank, a in enumerate(hit):
            s = stats[a["id"]]
            s.matched += 1
            s.total += t["amount"]
            s.per_account[t["account"]] += 1
            if rank == 0:
                s.won += 1
            else:
                s.shadowed_by[hit[0]["id"]] += 1
    return stats


@dataclass
class Preview:
    count: int
    total: float
    date_min: Optional[str]
    date_max: Optional[str]
    samples: list[tuple[str, int, float]]       # (merchant, count, total) biggest first
    already_matched: Counter = field(default_factory=Counter)   # existing annotation id -> n of these txs it wins
    share_of_account: float = 0.0               # largest share of one account's transactions

    def to_dict(self) -> dict:
        return {"count": self.count, "total": round(self.total, 2), "date_min": self.date_min,
                "date_max": self.date_max, "samples": [{"merchant": m, "count": n, "total": round(v, 2)}
                                                       for m, n, v in self.samples],
                "already_matched_by": dict(self.already_matched), "max_account_share": round(self.share_of_account, 3)}


def preview(new: dict, existing: list[dict], txs: list[dict], sample: int = 5) -> Preview:
    """What a NEW annotation (appended after `existing`) would match."""
    hits = [t for t in txs if matches(new, t)]
    prior: Counter = Counter()
    for t in hits:
        first = next((a["id"] for a in existing if matches(a, t)), None)
        if first:
            prior[first] += 1
    by_m: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for t in hits:
        e = by_m[t["merchant"] or t["key"] or "?"]
        e[0] += 1
        e[1] += t["amount"]
    samples = sorted(((m, n, v) for m, (n, v) in by_m.items()), key=lambda x: -abs(x[2]))[:sample]
    acc_total = Counter(t["account"] for t in txs)
    acc_hit = Counter(t["account"] for t in hits)
    share = max((acc_hit[a] / acc_total[a] for a in acc_hit if acc_total[a] >= 30), default=0.0)
    dates = sorted(t["date"] for t in hits)
    return Preview(len(hits), sum(t["amount"] for t in hits), dates[0] if dates else None,
                   dates[-1] if dates else None, samples, prior, share)
