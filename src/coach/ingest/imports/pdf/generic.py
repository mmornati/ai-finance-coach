"""Generic fallback PDF statement parser profile."""

from __future__ import annotations

from datetime import datetime
import re

from coach.ingest.imports.parsers import ParseError, Parsed, Row, parse_amount


class GenericPdfProfile:
    """Generic fallback PDF statement parser profile."""

    name = "generic"

    SKIP_RE = re.compile(
        r"\b(opening balance|closing balance|beginning balance|ending balance|"
        r"previous balance|new balance|vorig saldo|nieuw saldo|solde initial|"
        r"solde final|solde précédent|nouveau solde|total debit|total credit|"
        r"total debet|total credit|total dépenses|total recettes|total des opérations|"
        r"sous-total|recapitulatif|statement period|période du)\b",
        re.I,
    )

    @classmethod
    def matches(cls, text: str, profile_name: str, bank_name: str) -> bool:
        return True

    @classmethod
    def parse(cls, reader, p0: str, all_text: str, force: bool = False) -> Parsed:
        warnings: list[str] = ["statement parsed using generic fallback: please verify transactions against original PDF"]

        # IBAN detection
        iban = None
        ibans = re.findall(r"\b([A-Z]{2}\d{2}[A-Z0-9]{4,30})\b", p0)
        if ibans:
            iban = ibans[0]

        # Currency detection: look for explicit tag or standard codes
        currency = "EUR"
        m_cur = re.search(r"\b(?:currency|devise|valuta)\s*[:]?\s*([A-Z]{3})\b", all_text[:2000], re.I)
        if m_cur:
            currency = m_cur.group(1).upper()
        elif re.search(r"\bUSD\b", all_text[:2000]):
            currency = "USD"
        elif re.search(r"\bGBP\b", all_text[:2000]):
            currency = "GBP"
        elif re.search(r"\bCHF\b", all_text[:2000]):
            currency = "CHF"

        # Statement date & year from header
        m_date = (
            re.search(r"\b(?:statement date|date du relevé|datum|date)\s*:?\s*(\d{2}[-/\.]\d{2}[-/\.]\d{4})\b", p0, re.I)
            or re.search(r"(\d{2}[-/\.]\d{2}[-/\.]\d{4})", p0)
            or re.search(r"(\d{4}-\d{2}-\d{2})", p0)
        )
        stmt_year = None
        if m_date:
            d_val = m_date.group(1)
            parts = re.split(r"[-/\.]", d_val)
            if len(parts[0]) == 4:
                stmt_year = int(parts[0])
            elif len(parts[2]) == 4:
                stmt_year = int(parts[2])

        # Check for stated totals or balances for reconciliation
        stated_debit = None
        stated_credit = None
        m_deb = re.search(r"(?:total(?:\s+amount)?\s+deb[ie]t|totaal(?:\s+bedrag)?\s+debet|totaal\s+af|total\s+d[ée]penses)\s*[:]?\s*([+-]?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2})", all_text, re.I)
        if m_deb:
            try:
                stated_debit = abs(parse_amount(m_deb.group(1)))
            except ValueError:
                pass
        m_crd = re.search(r"(?:total(?:\s+amount)?\s+credit|totaal(?:\s+bedrag)?\s+credit|totaal\s+bij|total\s+recettes)\s*[:]?\s*([+-]?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2})", all_text, re.I)
        if m_crd:
            try:
                stated_credit = abs(parse_amount(m_crd.group(1)))
            except ValueError:
                pass

        rows: list[Row] = []
        lines = [line.strip() for line in all_text.splitlines() if line.strip()]
        for line in lines:
            if cls.SKIP_RE.search(line):
                continue

            m = re.search(
                r"^(\d{2}[-/\.]\d{2}(?:[-/\.]\d{2,4})?)\s+(.+?)\s+([+-]?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}[+-]?|\([0-9\.,]+\))$",
                line,
            )
            if not m:
                continue

            d_raw, desc, amt_raw = m.group(1), m.group(2).strip(), m.group(3).strip()
            parts = re.split(r"[-/\.]", d_raw)
            try:
                dd, mm = int(parts[0]), int(parts[1])
                if len(parts) == 3:
                    y = int(parts[2])
                    yy = y + 2000 if y < 100 else y
                else:
                    if stmt_year is None:
                        # Ambiguous date with no year in header: skip instead of guessing fake year
                        continue
                    yy = stmt_year
                iso_d = datetime(yy, mm, dd).date().isoformat()
            except (ValueError, IndexError):
                continue

            try:
                amt = parse_amount(amt_raw)
            except ValueError:
                continue

            # Sign disambiguation: if unsigned, determine if debit or credit.
            # Use word-boundary matching and exclude common debit card / payment terms (e.g. "credit card").
            if not (amt_raw.startswith(("+", "-")) or amt_raw.endswith(("+", "-")) or amt_raw.startswith("(")):
                low_desc = desc.lower()
                is_card_or_payment = bool(re.search(r"\b(?:credit\s+card|carte\s+(?:de\s+)?cr[ée]dit|paiement|payment|card\s+payment|pr[ée]l[èe]vement)\b", low_desc))
                is_credit_keyword = bool(re.search(
                    r"\b(?:virement\s+re[çc]u|salaire|salary|remboursement|refund|deposit|storting|inkomen|"
                    r"ontvangen|gecrediteerd|loonaanbetaling|int[ée]r[êe]ts?|interest|gutschrift|einzahlung|bijschrijving)\b",
                    low_desc,
                ))
                has_credit_term = (
                    bool(re.search(r"\b(?:credit\s+note|avoir|cr[ée]dit\s+en\s+votre\s+faveur)\b", low_desc))
                    or (bool(re.search(r"\b(?:credit|cr[ée]dit)\b", low_desc)) and not is_card_or_payment)
                )
                if not is_card_or_payment and (is_credit_keyword or has_credit_term):
                    amt = abs(amt)
                else:
                    amt = -abs(amt)

            rows.append(Row(iso_d, round(amt, 2), currency, desc, iso_d, "", None, {}, len(rows) + 1))

        if not rows:
            raise ParseError("no booked transactions found in the PDF statement")

        # Checksum reconciliation against stated totals
        if stated_debit is not None:
            actual_debit = round(sum(abs(r.amount) for r in rows if r.amount < 0), 2)
            if abs(actual_debit - stated_debit) > 0.01:
                warnings.append(f"checksum mismatch: total debit {actual_debit:.2f} does not match statement stated total {stated_debit:.2f}")
        if stated_credit is not None:
            actual_credit = round(sum(r.amount for r in rows if r.amount > 0), 2)
            if abs(actual_credit - stated_credit) > 0.01:
                warnings.append(f"checksum mismatch: total credit {actual_credit:.2f} does not match statement stated total {stated_credit:.2f}")

        return Parsed("pdf", rows, iban=iban, currency=currency, warnings=warnings)
