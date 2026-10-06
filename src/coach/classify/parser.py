"""Bank-descriptor parsing entry points. The per-bank parsers live in :mod:`coach.classify.parsers`; this module
keeps the original names (``parse`` = the Fortuneo parser, ``merchant_key``, holder helpers) for existing callers."""
from __future__ import annotations

from coach.classify.parsers import parse_tx
from coach.classify.parsers.common import (  # noqa: F401  (re-exported)
    FX_RE, PERSON_RE, PREFIX_WORDS, PROCESSORS, PRODUCT_WORDS, TITLES, Household, RawTx, _holder_name,
    _name_tokens, candidate_holders, holder_sets, is_own_name, lead_tokens, merchant_key, owner_tokens)


def parse(desc: str, amount: float, booking_date: str, owner: set[str], holders: list[set[str]] | None = None) -> dict:
    """Parse one Fortuneo-format descriptor. `owner`: family name tokens; `holders`: own-account holder sets
    (>= 2 tokens each; smaller sets are ignored). Kept for compatibility: the pipeline uses :func:`parse_tx`."""
    holders = [owner] if holders is None and owner else (holders or [])
    h = Household(holders=[x for x in holders if len(x) >= 2], family=set(owner or ()))
    return parse_tx(RawTx(desc, amount, booking_date), h, bank="Fortuneo")
