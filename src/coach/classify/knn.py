"""Similar-merchant lookup (E2-11): character n-gram TF-IDF vectors over merchant keys, cosine similarity, pure
Python (no model download, no network, no extra dependency).

Labelled corpus = merchants the user confirmed (source 'user') plus high-confidence LLM labels. Labels produced by
this module itself (source 'knn') are never part of the corpus, so errors cannot compound.

* ``neighbours`` -> the k nearest labelled examples of a key (passed to the LLM as few-shot context)
* ``auto_label`` -> labels a key WITHOUT the LLM only when the evidence is strong: the nearest neighbour is at least
  `threshold` similar AND every neighbour above `threshold` (at most 3 considered) agrees on the category.
"""
from __future__ import annotations

import math
import re
from collections import Counter

DEFAULT_THRESHOLD = 0.92
HIGH_CONFIDENCE = 0.85
NGRAM = 3
# similarity is only meaningful between shops / creditors; a transfer (salary, rent...) is not 'like' a shop that
# happens to share a word with its sender
MERCHANT_TYPES = {'card', 'card_refund', 'direct_debit'}


def ngrams(key: str, n: int = NGRAM) -> Counter:
    s = f" {re.sub(r'[^A-Z0-9 ]+', ' ', (key or '').upper())} "
    s = re.sub(r"\s+", " ", s)
    return Counter(s[i:i + n] for i in range(max(len(s) - n + 1, 0)))


class Index:
    def __init__(self, labelled: list[tuple[str, str, str]]):
        """labelled: (merchant_key, merchant_name, category)."""
        self.items = labelled
        grams = [ngrams(k) for k, _, _ in labelled]
        df: Counter = Counter()
        for g in grams:
            df.update(g.keys())
        self.n = max(len(labelled), 1)
        self.idf = {t: math.log((1 + self.n) / (1 + c)) + 1.0 for t, c in df.items()}
        self.vecs = [self._vec(g) for g in grams]

    def _vec(self, g: Counter) -> dict:
        v = {t: (1 + math.log(c)) * self.idf.get(t, math.log(1 + self.n) + 1.0) for t, c in g.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def neighbours(self, key: str, k: int = 5, exclude_self: bool = True) -> list[tuple[float, str, str, str]]:
        q = self._vec(ngrams(key))
        scored = []
        for (mk, name, cat), v in zip(self.items, self.vecs):
            if exclude_self and mk == key:
                continue
            small, big = (q, v) if len(q) < len(v) else (v, q)
            sim = sum(x * big.get(t, 0.0) for t, x in small.items())
            if sim > 0:
                scored.append((round(sim, 4), mk, name, cat))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[:k]


def labelled_corpus(con, allow: tuple = ()) -> list[tuple[str, str, str]]:
    """Confirmed / high-confidence labels of shops and creditors only: keys seen exclusively on card payments or
    direct debits, labelled outside transfer.* / income.*, and passing the person guard (P1). The result is what an
    LLM may be shown as hints, so nothing that could be a person's name is in it."""
    from coach.classify.candidates import eligible_label_keys
    ok = eligible_label_keys(con, allow)
    rows = con.execute("""SELECT merchant_key, COALESCE(merchant_name, merchant_key), category FROM merchants
                          WHERE category IS NOT NULL AND category <> 'other.uncategorized'
                            AND (source='user' OR (source IN ('llm','llm_web') AND confidence >= ?))
                          ORDER BY merchant_key""", (HIGH_CONFIDENCE,)).fetchall()
    return [r for r in rows if r[0] in ok]


def build_index(con, allow: tuple = ()) -> Index:
    return Index(labelled_corpus(con, allow))


def auto_label(index: Index, key: str, threshold: float = DEFAULT_THRESHOLD) -> tuple[str, float, str] | None:
    """(category, similarity, nearest key) when the evidence is strong enough, else None."""
    near = index.neighbours(key, k=3)
    strong = [n for n in near if n[0] >= threshold]
    if not strong or len({n[3] for n in strong}) != 1:
        return None
    return strong[0][3], strong[0][0], strong[0][1]


def refresh_knn_labels(con, index: Index, threshold: float) -> dict:
    """Labels written by similarity (source 'knn') follow their sources: when the neighbour they were derived from
    was corrected, removed or no longer qualifies, the label is re-derived, or dropped so the key is asked again."""
    from coach.db import now_iso
    changed = dropped = 0
    for key, cat in con.execute("SELECT merchant_key, category FROM merchants WHERE source='knn'").fetchall():
        hit = auto_label(index, key, threshold) if index.items else None
        if hit is None:
            con.execute("DELETE FROM merchants WHERE merchant_key=? AND source='knn'", (key,))
            dropped += 1
        elif hit[0] != cat:
            con.execute("UPDATE merchants SET category=?, confidence=?, model=?, updated_at=? WHERE merchant_key=?",
                        (hit[0], round(hit[1], 3), f"knn:{hit[2]}"[:60], now_iso(), key))
            changed += 1
    con.commit()
    return {"changed": changed, "dropped": dropped}
