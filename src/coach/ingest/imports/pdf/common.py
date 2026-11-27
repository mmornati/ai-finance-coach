"""Shared constants and helper functions for bank statement PDF parsing."""

from __future__ import annotations

import re

from coach.ingest.imports.parsers import ParseError, Row, parse_amount

MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_PAGES = 50


def parse_signed_amount(
    amt_str: str,
    decimal: str = ",",
    thousands: str | None = None,
    allow_unsigned: bool = False,
) -> float:
    """Parse an amount string requiring or allowing an explicit sign (+ or -)."""
    cleaned = re.sub(r"[€$£\s  ]|EUR|USD|GBP|CHF", "", (amt_str or "").strip(), flags=re.I)
    has_sign = cleaned.startswith(("+", "-")) or cleaned.endswith(("+", "-"))
    if not has_sign and not allow_unsigned:
        raise ValueError(f"missing explicit sign (+ or -): {amt_str!r}")
    return parse_amount(cleaned, decimal=decimal, thousands=thousands or "")


def year_rollover(stmt_year: int, stmt_month: int, tx_month: int) -> int:
    """Calculate transaction year accounting for year rollover at year-end boundaries.

    E.g., if statement is in January and transaction month is December, the transaction year is stmt_year - 1.
    """
    return stmt_year - 1 if tx_month == 12 and stmt_month == 1 else stmt_year


def _reconcile_balances(
    old_bal: float | None,
    new_bal: float | None,
    rows: list[Row],
    warnings: list[str],
    force: bool,
) -> None:
    """Reconcile the sum of transactions against start and end balances."""
    if old_bal is not None and new_bal is not None and rows:
        actual_sum = round(sum(r.amount for r in rows), 2)
        diff = round(old_bal + actual_sum - new_bal, 2)
        if abs(diff) >= 0.01:
            msg = (
                f"checksum mismatch: old balance {old_bal:.2f} + sum {actual_sum:.2f} != "
                f"new balance {new_bal:.2f} (diff: {diff:+.2f})"
            )
            if not force:
                raise ParseError(msg)
            warnings.append(msg)
