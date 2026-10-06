"""CIC: `ECH PRET CAP+IN <loan refs>` instalments, `F <fee>` bank charges (`F TENUE DE COMPTE`), `PRLV SEPA <creditor> |
E2E-… | free text` direct debits, `VIR <payer> | E2E-… | free text` credits (property-manager rent, own transfers).
Fields arrive as ` | `-separated remittance lines."""
from __future__ import annotations

import re

from coach.classify.parsers import french
from coach.classify.parsers.common import Household, RawTx, collapse, result, segments

CIC_FEE_RE = re.compile(r"^F\s+[A-Z]{3,}")


def parse(tx: RawTx, h: Household) -> dict:
    head = collapse(segments(tx.description)[0])
    if CIC_FEE_RE.match(head.upper()) and not french.LOAN_RE.match(head):
        return result("bank_fee", head)
    return french.parse(tx, h)
