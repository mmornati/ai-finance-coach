"""Revolut (via Enable Banking). The bank gives a transaction code (CARD_PAYMENT, TRANSFER, TOPUP, EXCHANGE, FEE,
CHARGE, ATM, REFUND...) and the counterparty name, which are more reliable than the free-text description:

* CARD_PAYMENT            -> card (card_refund when money comes in); description = merchant name
* TRANSFER on a savings account, or `To EUR [vault]` -> savings_internal (moves between the user's pockets / vaults)
* TRANSFER to / from a person -> internal_transfer when the counterparty is a household member, otherwise
  person_transfer_in/out (friends, family: never sent to an LLM). The description may be `note | To NAME`.
* TOPUP (Apple/Google Pay, bank transfer into the account) -> topup
* EXCHANGE -> fx_exchange; FEE / CHARGE -> bank_fee; ATM -> atm; REFUND / CARD_REFUND -> card_refund
"""
from __future__ import annotations

import re

from coach.classify.parsers import french
from coach.classify.parsers.common import (
    Household, RawTx, collapse, is_own_name, result, segments)

CURRENCIES = "EUR|USD|GBP|CHF|PLN|SEK|NOK|DKK|CZK|HUF|RON|BGN|JPY|CAD|AUD|NZD|TRY|AED|ISK"
POCKET_RE = re.compile(rf"^(?:To|From)\s+(?:{CURRENCIES})\b")
DIRECTION_RE = re.compile(r"^(To|From)\s+(.+)$", re.I)
WITHDRAWAL_RE = re.compile(r"^(?:Junior account withdrawal|Withdrawal by)\b\s*\(?([^)]*)\)?", re.I)


def _fx(tx: RawTx):
    ex = (tx.raw or {}).get("exchange_rate") or {}
    inst = ex.get("instructed_amount") or {}
    try:
        if inst.get("currency") and inst["currency"] != tx.currency:
            return float(inst["amount"]), inst["currency"]
    except (KeyError, TypeError, ValueError):
        pass
    return None


# lines that mean "a person sent / received money", also when the feed gives no transaction code
PERSON_LINE_RE = re.compile(r"^(?:Payment from|Sent from(?: Revolut)?|Received from|Transfer (?:to|from)|"
                            r"Money (?:from|to)|Request from)\b\s*(.*)$", re.I)


def parse(tx: RawTx, h: Household) -> dict:
    code = (tx.bank_tx_code or "").upper()
    segs = segments(tx.description)
    last = segs[-1]
    amount = tx.amount
    note = " | ".join(segs[:-1]) or None
    pm = next((m for s_ in segs if (m := PERSON_LINE_RE.match(s_))), None)
    if pm and code not in ("CARD_PAYMENT", "TOPUP", "ATM", "FEE", "CHARGE", "EXCHANGE"):
        who = collapse(tx.counterparty or pm.group(1) or "")
        if not who:     # "Sent from Revolut | <name>": the name is the other field
            who = collapse(next((s_ for s_ in segs if not PERSON_LINE_RE.match(s_)), ""))
        kind = ("internal_transfer" if who and is_own_name(who, h.holders) else
                "person_transfer_in" if amount > 0 else "person_transfer_out")
        return result(kind, who or collapse(last), counterparty=who or None)

    if code == "CARD_PAYMENT" or (not code and not DIRECTION_RE.match(last) and not POCKET_RE.match(last)):
        return result("card" if amount <= 0 else "card_refund", collapse(" ".join(segs)), fx=_fx(tx))
    if code in ("REFUND", "CARD_REFUND"):
        return result("card_refund", collapse(" ".join(segs)), fx=_fx(tx))
    if code == "ATM":
        return result("atm", collapse(" ".join(segs)))
    if code in ("FEE", "CHARGE"):
        return result("bank_fee", collapse(" ".join(segs)))
    if code == "EXCHANGE" or re.match(r"^Exchanged to\b", last, re.I):
        return result("fx_exchange", collapse(" ".join(segs)))
    if code == "TOPUP" or re.search(r"\bTop-Up\b", last, re.I):
        r = result("topup", collapse(segs[0]))
        r["counterparty"] = tx.counterparty or None
        return r
    if code == "TRANSFER" or DIRECTION_RE.match(last) or WITHDRAWAL_RE.match(last):
        if tx.account_type.upper() == "SVGS" or POCKET_RE.match(last):
            pocket = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27}\b", "", last, flags=re.I)
            return result("savings_internal", collapse(pocket))
        who = tx.counterparty or ""
        if not who and (m := DIRECTION_RE.match(last)):
            who = m.group(2)
        if not who and (m := WITHDRAWAL_RE.match(last)):
            who = m.group(1)
        who = collapse(who)
        if who and is_own_name(who, h.holders):
            tx_type = "internal_transfer"
        else:
            tx_type = "person_transfer_in" if amount > 0 else "person_transfer_out"
        r = result(tx_type, who or collapse(last), counterparty=who or None)
        if note:
            r["reference"] = None   # the note is free text between people: not stored, never sent anywhere
        return r
    return french.parse(tx, h)
