"""Generic Italian bank descriptors (no Italian bank is connected yet: tested on synthetic samples, to be verified
with real exports): PAGAMENTO POS / POS / PAGAMENTO CARTA, BONIFICO A FAVORE DI / DA / ISTANTANEO, ADDEBITO SDD /
ADDEBITO DIRETTO (with MANDATO / CID creditor id), PRELIEVO BANCOMAT / ATM, COMMISSIONI / SPESE / CANONE / IMPOSTA
DI BOLLO, RATA MUTUO / FINANZIAMENTO. Falls back to the generic French patterns (VIR, CARTE...)."""
from __future__ import annotations

import re

from coach.classify.parsers import french
from coach.classify.parsers.common import (
    FX_RE, Household, RawTx, classify_transfer, collapse, looks_like_person, result, segments)

DATE_IN_TEXT = re.compile(r"\b(\d{2})[/.](\d{2})(?:[/.](\d{2,4}))?\b")
POS_RE = re.compile(r"^(?:PAGAMENTO\s+(?:POS|CARTA|PAGOBANCOMAT|CON\s+CARTA)|PAG\.?\s*POS|ACQUISTO\s+POS|POS|"
                    r"PAGAMENTO\s+TRAMITE\s+POS|PAGAMENTO\s+ELETTRONICO)\b[\s:-]*(.*)$", re.I | re.S)
PAY_RE = re.compile(r"^PAGAMENTO\b[\s:-]*(.*)$", re.I | re.S)
BONIFICO_RE = re.compile(r"^(?:BONIFICO|BON\.?|DISPOSIZIONE\s+DI\s+BONIFICO|ACCREDITO\s+BONIFICO)\b\s*"
                         r"(?P<kind>(?:SEPA|ISTANTANEO|INSTANT|ESTERO|ORDINARIO|ISTANT\.?)\s*)*"
                         r"(?P<dir>A\s+FAVORE\s+DI|A\s+FAV\.?(?:\s+DI)?|DA|DISPOSTO\s+A\s+FAVORE\s+DI|A|IN\s+ENTRATA\s+DA|"
                         r"A\s+VOSTRO\s+FAVORE\s+DA|ORDINANTE)?[\s:-]*(?P<who>.*)$", re.I | re.S)
SDD_RE = re.compile(r"^(?:ADDEBITO\s+(?:SDD|DIRETTO|DIR\.?|SEPA(?:\s+DIRECT\s+DEBIT)?|PERMANENTE)|SDD|"
                    r"DOMICILIAZIONE)\b[\s:-]*(.*)$", re.I | re.S)
ATM_IT_RE = re.compile(r"^(?:PRELIEVO\s+(?:BANCOMAT|ATM|CONTANTI|SPORTELLO|CARTA)|PRELIEVO)\b[\s:-]*(.*)$", re.I | re.S)
FEE_IT_RE = re.compile(r"^(?:COMMISSIONI?|SPESE|CANONE|IMPOSTA\s+DI\s+BOLLO|BOLLO|COMPETENZE|INTERESSI\s+PASSIVI|"
                       r"COSTO\s+OPERAZIONE|RECUPERO\s+SPESE|SPESE\s+TENUTA\s+CONTO)\b", re.I)
LOAN_IT_RE = re.compile(r"^(RATA\s+(?:MUTUO|FINANZIAMENTO|PRESTITO)|ADDEBITO\s+RATA|PAGAMENTO\s+RATA|"
                        r"RIMBORSO\s+(?:MUTUO|FINANZIAMENTO|PRESTITO))\b[\s:-]*(.*)$", re.I)
CID_RE = re.compile(r"\b(?:CID|ID\s*CREDITORE|IDENTIFICATIVO\s+CREDITORE)[\s:.]*([A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{6,})",
                    re.I)
MANDATO_RE = re.compile(r"\b(?:MANDATO|MAND\.?|RIF\.?\s*MANDATO|MNDT)[\s:.N°#-]*([A-Z0-9][A-Z0-9/_-]{3,34})", re.I)
NOISE_CUT_RE = re.compile(r"\b(?:MANDATO|MAND\b|CID\b|ID\s*CREDITORE|RIF\b|RIFERIMENTO|CRO\b|TRN\b|NUM\b|N°|"
                          r"DEL\s+\d|DATA\b|CARTA\b|CIRCUITO|\d{2}[/.]\d{2})", re.I)
LEGAL_IT = re.compile(r"\b(?:S\.?R\.?L\.?|S\.?P\.?A\.?|S\.?N\.?C\.?|S\.?A\.?S\.?|SOC\.?|COOP|ASS\.?|ONLUS|LTD)\b", re.I)


def _clean_name(text: str) -> str:
    if m := NOISE_CUT_RE.search(text):
        text = text[: m.start()]
    return collapse(re.sub(r"\s+\d{4,}\b.*$", "", text))


def _op_date(text: str, booking_date: str) -> str | None:
    m = DATE_IN_TEXT.search(text)
    if not m:
        return None
    d, mth, y = m.groups()
    year = (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else int(booking_date[:4]) - (
        1 if int(mth) > int(booking_date[5:7]) else 0)
    try:
        return f"{year}-{int(mth):02d}-{int(d):02d}"
    except ValueError:
        return None


def parse(tx: RawTx, h: Household) -> dict:
    desc = collapse(" ".join(segments(tx.description)))
    amount = tx.amount
    if m := LOAN_IT_RE.match(desc):
        return result("loan_payment", collapse(m.group(1)).upper(), reference=_clean_name(m.group(2)) or None)
    if m := SDD_RE.match(desc):
        body = m.group(1)
        name = _clean_name(re.sub(r"^(?:A\s+FAVORE\s+DI|FAVORE|DI)\b\s*", "", body, flags=re.I))
        r = result("direct_debit", name or body, counterparty=name or None)
        if c := CID_RE.search(desc):
            r["creditor_id"] = c.group(1)
        if c := MANDATO_RE.search(desc):
            r["mandate_ref"] = c.group(1)
        return r
    if m := ATM_IT_RE.match(desc):
        rest = re.sub(r"^\d{2}[/.]\d{2}(?:[/.]\d{2,4})?\s*", "", m.group(1))
        return result("atm", _clean_name(rest) or rest or desc, _op_date(desc, tx.booking_date))
    if FEE_IT_RE.match(desc):
        return result("bank_fee", desc)
    if m := BONIFICO_RE.match(desc):
        who = collapse(re.sub(r"^(?:SIG\.?(?:RA)?|DOTT\.?(?:SSA)?)\s+", "", m.group("who"), flags=re.I))
        who = _clean_name(who) or who
        who = collapse(re.sub(r"\b(?:CAUSALE|RIF|IBAN)\b.*$", "", who, flags=re.I))
        tx_type = classify_transfer(who, amount, h)
        if tx_type in ("transfer_in", "transfer_out") and (
                looks_like_person(who, h.first_names) and not LEGAL_IT.search(who)):
            tx_type = "person_transfer_in" if amount > 0 else "person_transfer_out"
        return result(tx_type, who or desc, counterparty=who or None)
    if m := POS_RE.match(desc):
        rest, fx = m.group(1), None
        if fxm := FX_RE.search(rest):
            fx, rest = (float(fxm.group(1).replace(",", ".")), fxm.group(2)), rest[: fxm.start()]
        op = _op_date(rest[:12], tx.booking_date)
        rest = re.sub(r"^\d{2}[/.]\d{2}(?:[/.]\d{2,4})?(?:\s+\d{1,2}[:.]\d{2})?\s*", "", rest)
        rest = re.sub(r"\b(?:CARTA|CARD)\s*\*+\d+\b.*$", "", rest, flags=re.I)
        return result("card" if amount <= 0 else "card_refund", collapse(rest), op, fx)
    if m := PAY_RE.match(desc):
        return result("card" if amount <= 0 else "card_refund", _clean_name(m.group(1)) or m.group(1))
    return french.parse(tx, h)
