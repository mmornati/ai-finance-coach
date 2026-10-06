"""User corrections of categories, as functions (the CLI `classify correct` and the web app call these).

* :func:`correct_merchant`  - a label of the user for a merchant key (beats rules, entity defaults and LLM labels).
* :func:`set_override` / :func:`clear_override` - a category for ONE transaction (``tx_overrides``), which outranks
  everything except memory annotations.
"""
from __future__ import annotations

from coach.classify.rules import CATEGORIES
from coach.db import now_iso


class CorrectionError(ValueError):
    """A correction that cannot be made. `code` lets callers word the problem their own way (CLI hint vs web page)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _check_category(category: str) -> None:
    if category not in CATEGORIES:
        raise CorrectionError(f"unknown category {category!r} (see `coach taxonomy list`)")


def merchant_keys_matching(con, key: str) -> list[str]:
    """Merchant keys equal to `key`, else matching it as a regular expression (what `classify correct` accepts)."""
    keys = [r[0] for r in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key=?", (key,))]
    if not keys:
        import re
        try:
            re.compile(key)
        except re.error as e:
            raise CorrectionError(f"{key!r} is neither a merchant key nor a valid regular expression ({e})", "bad_regex") from None
        keys = [r[0] for r in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key REGEXP ?",
                                          (key,))]
    return keys


def correct_merchant(con, key: str, category: str, name: str | None = None, exact: bool = False,
                     commit: bool = True) -> list[tuple[str, str]]:
    """Label merchant `key` (or every key matching it as a regex unless `exact`) as `category`, source 'user'.
    Returns [(merchant_key, merchant_name)]."""
    _check_category(category)
    if exact:
        keys = [r[0] for r in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key=?", (key,))]
    else:
        keys = merchant_keys_matching(con, key)
    if not keys:
        raise CorrectionError("No merchant key matches")
    out = []
    for k in keys:
        cur = con.execute("SELECT merchant_name FROM merchants WHERE merchant_key=?", (k,)).fetchone()
        nm = name or (cur[0] if cur else k.title())
        con.execute("""INSERT INTO merchants VALUES (?,?,?,1.0,NULL,'user',NULL,?)
                       ON CONFLICT(merchant_key) DO UPDATE SET merchant_name=excluded.merchant_name,
                       category=excluded.category, confidence=1.0, source='user', updated_at=excluded.updated_at""",
                    (k, nm, category, now_iso()))
        out.append((k, nm))
    if commit:
        con.commit()
    return out


def set_override(con, tx_key: str, category: str, note: str | None = None, commit: bool = True) -> None:
    """This ONE transaction gets `category` whatever the merchant is labelled."""
    _check_category(category)
    if not con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (tx_key,)).fetchone():
        raise CorrectionError(f"no transaction {tx_key!r}")
    con.execute("INSERT INTO tx_overrides(tx_key, category, note) VALUES (?,?,?) "
                "ON CONFLICT(tx_key) DO UPDATE SET category=excluded.category, note=excluded.note",
                (tx_key, category, note))
    if commit:
        con.commit()


def clear_override(con, tx_key: str) -> bool:
    cur = con.execute("DELETE FROM tx_overrides WHERE tx_key=?", (tx_key,))
    con.commit()
    return cur.rowcount > 0


def confirm_label(con, key: str) -> str:
    """Make the current automatic label of merchant `key` the user's own (``classify review --accept``, the web page's
    "keep"): same category, source 'user', confidence 1. Refused (codes: unlabelled, already_user, knn, uncategorized) for a
    merchant with no label, one that is already yours, a similarity (kNN) label or an uncategorized one."""
    row = con.execute("SELECT category, source, model FROM merchants WHERE merchant_key=?", (key,)).fetchone()
    if not row:
        raise CorrectionError("this merchant has no label to confirm yet: choose a category instead", "unlabelled")
    if row[1] == "user":
        raise CorrectionError("this label is already yours", "already_user")
    if row[1] == "knn":
        raise CorrectionError(f"this is a similarity label derived from {str(row[2]).removeprefix('knn:')!r}, not a verified one: "
                              "choose the category yourself", "knn")
    if row[0] == "other.uncategorized":
        raise CorrectionError("the label is 'other.uncategorized': choose a category", "uncategorized")
    con.execute("UPDATE merchants SET source='user', confidence=1.0, updated_at=? WHERE merchant_key=?", (now_iso(), key))
    con.commit()
    return row[0]
