"""Caisse d'Epargne: `PRLV <creditor>` direct debits, `ECH PRET <loan ref> DU dd/mm/yy` loan instalments,
`VIR SEPA <name>` / `VIR INST <name>` transfers, `CHEQUE N°…`, `* PARTICIP FRAIS TENUE COMPTE` fees and the usual
French card / ATM lines. Fields are truncated to ~32 characters by the bank."""
from __future__ import annotations

from coach.classify.parsers import french
from coach.classify.parsers.common import Household, RawTx


def parse(tx: RawTx, h: Household) -> dict:
    return french.parse(tx, h)
