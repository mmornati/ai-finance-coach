"""Spending analytics returning plain data (the CLI formats it)."""
from __future__ import annotations

from pathlib import Path

from coach.classify.rules import NO_AVERAGE_TAGS, categorised


def coverage(txs: list[dict]) -> dict:
    """Counts transactions, not split parts (a split transaction counts once, as source 'split')."""
    txs = [t for t in txs if not t.get("split_index")]
    by_src: dict[str, int] = {}
    for t in txs:
        by_src[t["source"]] = by_src.get(t["source"], 0) + 1
    unc = [t for t in txs if t["category"] == "other.uncategorized"]
    return {"by_source": by_src, "uncategorized": len(unc), "total": len(txs)}


def llm_confidence(con) -> dict:
    hi, mid, low = con.execute("""SELECT SUM(confidence>=0.8), SUM(confidence>=0.6 AND confidence<0.8),
                                         SUM(confidence<0.6) FROM merchants WHERE source='llm'""").fetchone()
    return {"high": hi, "mid": mid, "low": low}


def monthly_averages(txs: list[dict]) -> dict:
    """Average monthly spending over full months (first/last month dropped as partial)."""
    months = sorted({t["date"][:7] for t in txs})
    full = months[1:-1]
    spend = [t for t in txs if not t["category"].startswith(("transfer.", "income."))]
    excluded = [t for t in spend if t["tags"] & NO_AVERAGE_TAGS]
    recurring = [t for t in spend if not t["tags"] & NO_AVERAGE_TAGS and t["date"][:7] in full]
    total_all = sum(t["amount"] for t in spend if t["date"][:7] in full)
    total_rec = sum(t["amount"] for t in recurring)
    n = len(full)
    last12 = full[-12:]
    agg: dict[str, float] = {}
    for t in recurring:
        if t["date"][:7] in last12:
            agg[t["category"]] = agg.get(t["category"], 0) + t["amount"]
    by_category = [(c, v / len(last12), v) for c, v in sorted(agg.items(), key=lambda x: x[1])] if last12 else []
    return {
        "full_months": full,
        "avg_monthly": total_rec / n if n else None,
        "avg_monthly_with_one_offs": total_all / n if n else None,
        "excluded": excluded,
        "last12": last12,
        "by_category": by_category,
    }


def by_entity(txs: list[dict], limit: int = 20) -> list[tuple[str, float, int]]:
    """Spending per canonical merchant (variants of a chain grouped), biggest first: (entity, total, count)."""
    agg: dict[str, list] = {}
    for t in txs:
        if t["category"].startswith(("transfer.", "income.")):
            continue
        e = agg.setdefault(t.get("entity") or t["merchant"] or t["key"], [0.0, 0])
        e[0] += t["amount"]
        e[1] += 1
    return sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda x: x[1])[:limit]


def recurring_hints(con, min_count: int = 3) -> list[tuple]:
    return con.execute("""
        SELECT m.merchant_name, m.category, COUNT(*), ROUND(AVG(t.amount),2)
        FROM merchants m JOIN tx_enriched e USING(merchant_key) JOIN transactions t USING(tx_key)
        WHERE m.recurring_hint=1 GROUP BY m.merchant_name HAVING COUNT(*)>=? ORDER BY 3 DESC""",
                       (min_count,)).fetchall()


def build_report(con, memory_dir: Path | None = None, rules=None, annotations=None) -> dict:
    txs = list(categorised(con, rules=rules, annotations=annotations, memory_dir=memory_dir))
    return {
        "coverage": coverage(txs),
        "by_entity": by_entity(txs),
        "llm_confidence": llm_confidence(con),
        "averages": monthly_averages(txs),
        "recurring_hints": recurring_hints(con),
    }
