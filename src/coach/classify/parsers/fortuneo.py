"""Fortuneo descriptors: CARTE dd/mm, ANN CARTE, RET DAB, VIR [INST] [WERO]. Behaviour is the original E2-1 parser
(regression-tested on the real data); only the transfer prefix handling (H3) and holder logic (H2) changed. Anything
it does not know falls through to the generic French parser."""
from __future__ import annotations

import re

from coach.classify.parsers import french
from coach.classify.parsers.common import (
    FX_RE, Household, RawTx, classify_transfer, ddmm_date, result, segments, strip_vir_prefix)


def parse(tx: RawTx, h: Household) -> dict:
    desc, amount = tx.description or "", tx.amount
    segs = segments(desc, padded=True)          # ' | '-joined remittance lines AND the padded fields

    def card(rest: str, ddmm: str | None, tx_type: str) -> dict:
        fx = None
        if m := FX_RE.search(rest):
            fx, rest = (float(m.group(1).replace(",", ".")), m.group(2)), rest[: m.start()]
        return result(tx_type, rest.strip(), ddmm_date(ddmm, tx.booking_date) if ddmm else None, fx)

    if m := re.match(r"^CARTE (\d{2}/\d{2}) (.*)$", desc, re.S):
        return card(m.group(2), m.group(1), "card")
    if m := re.match(r"^ANN CARTE (.*)$", desc, re.S):
        return card(m.group(1), None, "card_refund")
    if m := re.match(r"^RET DAB (\S+) (.*)$", desc):
        return result("atm", m.group(2).strip())
    if desc.startswith("VIR"):
        head = strip_vir_prefix(segs[0])
        if head.upper() in {"M", "MR", "MME", "MLLE"} and len(segs) > 1:  # "VIR INST M     NAME"
            head = f"{head} {segs[1]}"
        wero = " WERO " in f" {segs[0]} "
        return result(classify_transfer(head, amount, h, wero=wero, smart=False), head, counterparty=head)
    return french.parse(tx, h, smart=False, padded=True)
