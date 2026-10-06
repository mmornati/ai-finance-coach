"""Descriptor patterns shared by the French banks (PRLV, VIR, ECH PRET, CARTE/CB, RET DAB, fees, cheques).
Bank modules add their own patterns first and fall back to this one; it is also the generic parser for banks
without a dedicated module."""
from __future__ import annotations

import re

from coach.classify.parsers.common import (
    FX_RE, Household, RawTx, classify_transfer, collapse, ddmm_date, counterparty_from_tags, result, segments, strip_reason, strip_vir_prefix)

DD_PREFIX_RE = re.compile(r"^(?:PRLV|PRELEVEMENT|PRELEV|PRLVT)\b(?:\s+SEPA)?(?:\s+(?:RECU|EMIS))?\s*", re.I)
# everything after these markers is reference noise, not the creditor's name
DD_CUT_RE = re.compile(r"\b(REF\b|RUM\b|MANDAT|MDT\b|ICS\b|ID\s*CREANCIER|IDCREANCIER|NNE\b|E2E|ECH\b|ECHEANCE|"
                       r"FACT\b|FACTURE|N°|CONTRAT|ABONNEMENT|DU\s+\d|\d{1,2}[/.]\d{1,2}\b)", re.I)
ICS_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{3}\d{6,}[A-Z0-9]*\b")           # FR12ZZZ123456 (creditor id)
MANDATE_RE = re.compile(r"(?:\bRUM\b|\bMANDAT(?:O|E)?\b|\bMDT\b)[\s:.N°#-]*([A-Z0-9][A-Z0-9/_-]{3,34})", re.I)
E2E_RE = re.compile(r"\bE2E[- ]?([A-Z0-9]+)", re.I)
LONG_ID_RE = re.compile(r"\b(?=[A-Z0-9/_-]*\d)[A-Z0-9/_-]{9,}\b", re.I)

LOAN_RE = re.compile(r"^(?:ECH(?:EANCE)?\.?\s+(?:DE\s+)?PRET|REMBOURSEMENT\s+PRET|REMBT\s+PRET|PRLV\s+ECH\w*\s+PRET)\b\s*(.*)$",
                     re.I)
FEE_RE = re.compile(r"^\*?\s*(?:PARTICIP\w*\s+(?:AUX\s+)?FRAIS|COTISATION\b|COTIS\b|FRAIS\b|F\s+TENUE|"
                    r"TENUE\s+DE\s+COMPTE|AGIOS\b|INTERETS?\s+DEBITEURS?|COMMISSION\b|FACTURATION\b|"
                    r"ABONNEMENT\s+(?:PACK|OFFRE|CONVENTION|SERVICE)|CONVENTION\s+COMPTE|FRAIS\s+DE)", re.I)
STAR_FEE_RE = re.compile(r"^\*\s+\S")
ATM_RE = re.compile(r"^(?:RET(?:RAIT)?\s+(?:DAB|ESPECES|DISTRIBUTEUR|AUTOMATE)|RETRAIT\s+DAB)\s*(?:\d{2}/\d{2}\s+)?(.*)$",
                    re.I)
CARD_RE = re.compile(r"^(?:CARTE|CB|PAIEMENT\s+CB|PAIEMENT\s+PAR\s+CARTE|PAIEMENT\s+CARTE|ACHAT\s+CB|FACTURE\s+CARTE)"
                     r"\s+(?:(\d{2}/\d{2}|\d{4})\s+)?(.*)$", re.I | re.S)
CARD_REFUND_RE = re.compile(r"^(?:ANN(?:ULATION)?\.?\s+(?:CARTE|CB)|REMBOURSEMENT\s+CB|REMB\.?\s+CB|ANNUL\s+CB)\s*(.*)$",
                            re.I | re.S)
CHEQUE_RE = re.compile(r"^(?:CHEQUE|CHQ)\b", re.I)
CHEQUE_DEPOSIT_RE = re.compile(r"^REMISE\s+(?:DE\s+)?CHEQUES?\b", re.I)
P2P_RE = re.compile(r"^(?:PAYLIB|LYDIA|SATISPAY|WERO)\b\s*(.*)$", re.I)
DU_DATE_RE = re.compile(r"\bDU\s+(\d{2})/(\d{2})/(\d{2,4})\b", re.I)


def direct_debit(tx: RawTx, segs: list[str]) -> dict:
    head, text = segs[0], " ".join(segs)
    name = strip_reason(DD_PREFIX_RE.sub("", head, count=1).strip())     # a reason ('/MOTIF', 'MOTIF:') is not the creditor
    if m := DD_CUT_RE.search(name):
        name = name[: m.start()]
    name = collapse(LONG_ID_RE.sub(" ", ICS_RE.sub(" ", name)))
    if not name:     # creditor on the next field ("PRLV SEPA" | "CREDITOR ...")
        for s in segs[1:]:
            cand = collapse(LONG_ID_RE.sub(" ", ICS_RE.sub(" ", s.split("  ")[0])))
            if cand and not E2E_RE.match(s):
                name = cand
                break
    tagged = counterparty_from_tags(DD_PREFIX_RE.sub("", head, count=1).strip()) is not None
    r = result("direct_debit", name or ("" if tagged else collapse(head)), counterparty=name or None)
    if m := ICS_RE.search(text):
        r["creditor_id"] = m.group(0)
    if m := MANDATE_RE.search(text):
        r["mandate_ref"] = m.group(1)
    if m := E2E_RE.search(text):
        r["reference"] = "E2E-" + m.group(1)
    return r


def loan(tx: RawTx, rest: str) -> dict:
    toks, refs = rest.split(), []
    name = ["ECH PRET"]
    for t in toks:
        if t.upper() == "DU":
            break
        if re.search(r"\d", t):
            refs.append(t)
        elif not refs:
            name.append(t)
    r = result("loan_payment", collapse(" ".join(name)), reference=" ".join(refs) or None)
    if m := DU_DATE_RE.search(rest):
        d, mth, y = m.groups()
        r["op_date"] = f"{int(y) + 2000 if len(y) == 2 else int(y)}-{mth}-{d}"
    return r


def parse(tx: RawTx, h: Household, smart: bool = True, padded: bool = False) -> dict:
    desc = tx.description or ""
    segs = segments(desc, padded)
    head = collapse(segs[0])
    up = head.upper()
    amount = tx.amount

    if CHEQUE_DEPOSIT_RE.match(head):
        return result("cheque_deposit", "CHEQUE")
    if CHEQUE_RE.match(head):
        return result("cheque", "CHEQUE")
    if m := LOAN_RE.match(head):
        return loan(tx, m.group(1))
    if DD_PREFIX_RE.match(head) and not re.match(r"^PRLV\s+ECH", head, re.I):
        return direct_debit(tx, segs)
    if m := CARD_REFUND_RE.match(head):
        r = result("card_refund", collapse(m.group(1)))
        return r
    if m := ATM_RE.match(head):
        return result("atm", collapse(m.group(1)) or head)
    if m := CARD_RE.match(desc.strip()):
        rest, fx = m.group(2), None
        if fxm := FX_RE.search(rest):
            fx, rest = (float(fxm.group(1).replace(",", ".")), fxm.group(2)), rest[: fxm.start()]
        ddmm = m.group(1)
        op = None
        if ddmm:
            op = ddmm_date(ddmm, tx.booking_date) if "/" in ddmm else ddmm_date(f"{ddmm[:2]}/{ddmm[2:]}",
                                                                              tx.booking_date)
        return result("card" if amount <= 0 else "card_refund", collapse(rest), op, fx)
    if FEE_RE.match(head) or STAR_FEE_RE.match(head):
        return result("bank_fee", collapse(re.sub(r"^\*\s*", "", head)))
    if re.match(r"^VIR(?:EMENT)?\b", head, re.I):
        t = strip_vir_prefix(collapse(segs[0]))
        if t.upper() in {"M", "MR", "MME", "MLLE"} and len(segs) > 1:
            t = f"{t} {segs[1]}"
        wero = " WERO " in f" {segs[0].upper()} "
        tx_type = classify_transfer(t, amount, h, wero=wero, smart=smart, own_bank=tx.bank)
        r = result(tx_type, t, counterparty=t)
        for s in segs[1:]:
            if m2 := E2E_RE.match(s.strip()):
                r["reference"] = "E2E-" + m2.group(1)
        return r
    if m := P2P_RE.match(head):      # peer-to-peer apps: the rest is a person
        who = collapse(m.group(1))
        return result("person_transfer_in" if amount > 0 else "person_transfer_out", who or head,
                      counterparty=who or None)
    return result("other", desc)
