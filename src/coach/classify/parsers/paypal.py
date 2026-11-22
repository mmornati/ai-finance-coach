"""PayPal (via Enable Banking).

PayPal bank feeds via PSD2 provide:
- `tx.counterparty` (creditor name for debits, debtor name for credits): contains the real merchant or counterparty name.
- `tx.description`: often empty or contains additional notes/identifiers.
- `tx.bank_tx_code`: e.g. "PAYMENT".
- `tx.raw`: contains creditor/debtor objects, contact details (email), and exchange rate if multi-currency.

Behavior:
* Merchant payments (amount < 0) -> "card" (or person_transfer_out if counterparty is a person).
* Merchant refunds (amount > 0) -> "card_refund" (or person_transfer_in if from a person).
* Transfers between household members / own names -> "internal_transfer".
"""
from __future__ import annotations

from coach.classify.parsers import french
from coach.classify.parsers.common import (
    Household, RawTx, collapse, is_own_name, looks_like_person, result, segments
)


def _fx(tx: RawTx):
    ex = (tx.raw or {}).get("exchange_rate") or {}
    inst = ex.get("instructed_amount") or {}
    try:
        if inst.get("currency") and inst["currency"] != tx.currency:
            return float(inst["amount"]), inst["currency"]
    except (KeyError, TypeError, ValueError):
        pass
    return None


def parse(tx: RawTx, h: Household) -> dict:
    amount = tx.amount
    who = collapse(tx.counterparty or "")
    if not who:
        segs = segments(tx.description)
        who = collapse(" ".join(segs))

    if not who:
        return french.parse(tx, h)

    if is_own_name(who, h.holders):
        return result("internal_transfer", who, fx=_fx(tx), counterparty=who or None)

    if looks_like_person(who, h.first_names):
        tx_type = "person_transfer_in" if amount > 0 else "person_transfer_out"
        return result(tx_type, who, fx=_fx(tx), counterparty=who or None)

    tx_type = "card" if amount < 0 else "card_refund"
    return result(tx_type, who, fx=_fx(tx), counterparty=who or None)
