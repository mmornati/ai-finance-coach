"""Split a transaction over several categories (E2-13). The parts always sum EXACTLY (in cents) to the transaction
amount and carry its sign; analytics (`rules.categorised`) then count each part under its own category."""
from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation

from coach.classify.rules import CATEGORIES
from coach.db import connect
from coach.transfers import TransferError, resolve_tx


class SplitError(Exception):
    pass


def _cents(x) -> int:
    """Exact cents of a number / string; NaN, Infinity and fractions of a cent are rejected."""
    try:
        d = Decimal(str(x).strip())
    except InvalidOperation:
        raise SplitError(f"{x!r} is not an amount")
    if not d.is_finite():
        raise SplitError(f"{x!r} is not a finite amount")
    c = d * 100
    if c != c.to_integral_value():
        raise SplitError(f"{x} has fractions of a cent")
    return int(c)


def parse_parts(tokens: list[str]) -> list[tuple[str, str | None, str | None]]:
    """'food.groceries:70' / 'household:12.50:note' / 'shopping.clothing:rest' -> [(category, amount|'rest', note)]."""
    out = []
    for t in tokens:
        bits = t.split(":", 2)
        if len(bits) < 2:
            raise SplitError(f"{t!r}: expected CATEGORY:AMOUNT (or CATEGORY:rest)")
        out.append((bits[0], bits[1], bits[2] if len(bits) > 2 else None))
    return out


def set_split(con, tx_ref: str, tokens: list[str]) -> list[tuple[float, str, str | None]]:
    try:
        key = resolve_tx(con, tx_ref)
    except TransferError as e:
        raise SplitError(str(e))
    total = con.execute("SELECT amount FROM transactions WHERE tx_key=?", (key,)).fetchone()[0]
    tc = _cents(total)
    if tc == 0:
        raise SplitError("a zero-amount transaction cannot be split")
    sign = 1 if tc > 0 else -1
    parts = parse_parts(tokens)
    if len(parts) < 2:
        raise SplitError("give at least two parts")
    cents: list[int | None] = []
    for cat, amt, _ in parts:
        if cat not in CATEGORIES:
            raise SplitError(f"unknown category {cat}")
        if amt.lower() == "rest":
            cents.append(None)
            continue
        c = _cents(amt)
        if amt.strip()[:1] in "+-" and c * sign < 0:      # an explicit sign must agree with the transaction's
            raise SplitError(f"part {amt} has the opposite sign of the transaction ({total})")
        cents.append(abs(c) * sign)
    if cents.count(None) > 1:
        raise SplitError("only one part can be 'rest'")
    known = sum(c for c in cents if c is not None)
    if None in cents:
        rest = tc - known
        if rest * sign <= 0:
            raise SplitError("the other parts already use up the whole amount")
        cents[cents.index(None)] = rest
    elif sum(cents) != tc:
        raise SplitError(f"the parts add up to {sum(cents) / 100:.2f} but the transaction is {tc / 100:.2f}")
    if any(c == 0 for c in cents):
        raise SplitError("a part cannot be zero")
    con.execute("DELETE FROM tx_splits WHERE tx_key=?", (key,))
    rows = [(key, c / 100, cat, note) for c, (cat, _, note) in zip(cents, parts)]
    con.executemany("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES (?,?,?,?)", rows)
    con.commit()
    return [(r[1], r[2], r[3]) for r in rows]


def clear_split(con, tx_ref: str) -> int:
    key = resolve_tx(con, tx_ref)
    n = con.execute("DELETE FROM tx_splits WHERE tx_key=?", (key,)).rowcount
    con.commit()
    return n


def get_split(con, tx_key: str):
    return con.execute("SELECT amount, category, note FROM tx_splits WHERE tx_key=? ORDER BY id", (tx_key,)).fetchall()


def memory_conflicts(con, tx_key: str, memory_dir) -> list[str]:
    """Precedence: the parts of a split use THEIR categories; a memory annotation still adds its tags / event to
    every part, but its category does not apply to a split transaction. Say so when that would surprise."""
    from coach.classify.rules import annotation_for, load_annotations
    row = con.execute("""SELECT t.booking_date, t.amount, t.description, e.merchant_key, e.op_date
                         FROM transactions t JOIN tx_enriched e USING(tx_key) WHERE t.tx_key=?""", (tx_key,)).fetchone()
    if not row or not memory_dir:
        return []
    old = [r[0] for r in con.execute("SELECT old_key FROM tx_key_remap WHERE new_key=?", (tx_key,))]
    ann = annotation_for(load_annotations(memory_dir), tx_key, row[3], row[2], row[0], row[1], None, row[4],
                         aliases=old)
    if ann and ann.get("category"):
        return [f"memory annotation {ann.get('id', '?')!r} sets the category {ann['category']} for this transaction; "
                "the split parts take precedence (its tags and event still apply to every part)"]
    return []


def cmd_split(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    try:
        if a.clear:
            print(f"removed {clear_split(con, a.tx)} split part(s)")
            return
        if not a.parts:
            key = resolve_tx(con, a.tx)
            rows = get_split(con, key)
            amt = con.execute("SELECT amount FROM transactions WHERE tx_key=?", (key,)).fetchone()[0]
            print(f"{key}  amount {amt:.2f}")
            for r in rows or []:
                print(f"  {r[0]:>10.2f}  {r[1]}" + (f"  ({r[2]})" if r[2] else ""))
            if not rows:
                print("  not split")
            return
        for amount, cat, note in set_split(con, a.tx, a.parts):
            print(f"  {amount:>10.2f}  {cat}" + (f"  ({note})" if note else ""))
        for w in memory_conflicts(con, resolve_tx(con, a.tx), cfg.memory_dir):
            print(f"warning: {w}")
    except (SplitError, TransferError) as e:
        sys.exit(f"error: {e}")
