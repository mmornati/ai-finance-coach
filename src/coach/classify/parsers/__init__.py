"""Per-bank descriptor parsers (E2-2).

A parser is ``parse(tx: RawTx, household: Household) -> dict`` returning the same keys for every bank
(:data:`common.PARSED_KEYS`): tx_type, op_date, merchant_raw, merchant_key, fx_amount, fx_currency, counterparty,
mandate_ref, creditor_id, reference. The registry maps an account's bank to a parser; unknown banks use the generic
French/English parser, Italian banks (IBAN IT or country IT) the Italian one. A bank parser that does not recognise a
line (tx_type 'other') hands it to the generic parser before giving up.

To add a bank: write ``parsers/<bank>.py`` with ``parse``, register it with :func:`register`, and add fixtures in
``tests/test_parsers.py``.
"""
from __future__ import annotations

import re
from typing import Callable

from coach.classify.parsers import caisse_epargne, cic, fortuneo, french, italian, revolut
from coach.classify.parsers.common import (PARSED_KEYS, Household, RawTx, candidate_holders, holder_sets, household,
                                           is_own_name, lead_tokens, looks_like_person, merchant_key, owner_tokens,
                                           strip_accents, _holder_name, _name_tokens)

Parser = Callable[[RawTx, Household], dict]

PARSERS: dict[str, Parser] = {}
# (compiled regex on the normalised bank name, parser name); first match wins
BANK_PATTERNS: list[tuple[re.Pattern, str]] = []


def register(name: str, parser: Parser, bank_pattern: str | None = None) -> None:
    PARSERS[name] = parser
    if bank_pattern:
        BANK_PATTERNS.append((re.compile(bank_pattern), name))


register("fortuneo", fortuneo.parse, r"\bfortuneo\b")
register("caisse_epargne", caisse_epargne.parse, r"caisse d.?epargne|\bbpce\b|\bcaisse epargne\b")
register("cic", cic.parse, r"(^|\b)cic\b|credit industriel")
register("revolut", revolut.parse, r"\brevolut\b")
register("italian", italian.parse)
register("generic", lambda tx, h: french.parse(tx, h))


def parser_name_for(bank: str | None, iban: str | None = None, country: str | None = None) -> str:
    b = strip_accents((bank or "").lower())
    for pat, name in BANK_PATTERNS:
        if pat.search(b):
            return name
    if (iban or "").upper().startswith("IT") or (country or "").upper() == "IT":
        return "italian"
    return "generic"


def parse_tx(tx: RawTx, h: Household, bank: str | None = None, iban: str | None = None,
             country: str | None = None) -> dict:
    """Parse with the bank's parser, then the generic one if the line was not recognised."""
    name = parser_name_for(bank, iban, country)
    r = PARSERS[name](tx, h)
    if r["tx_type"] == "other" and name != "generic":
        g = PARSERS["generic"](tx, h)
        if g["tx_type"] != "other":
            r = g
    r["parser"] = name
    return r
