"""PDF statement parser profile for Crédit Agricole (relevés de compte & livrets CSL)."""

from __future__ import annotations

from datetime import datetime
import re

from coach.ingest.imports.parsers import ParseError, Parsed, Row, parse_amount
from coach.ingest.imports.pdf.common import _reconcile_balances, parse_signed_amount


class CreditAgricolePdfProfile:
    """PDF statement parser profile for Crédit Agricole (relevés de compte & livrets CSL)."""

    name = "credit_agricole"

    @staticmethod
    def _normalize_ca_text(text: str) -> str:
        """Decode characters from Crédit Agricole PDFs using non-standard font encodings.

        Some Crédit Agricole PDF generators use subsetted Type 3 / TrueType fonts where glyphs
        are mapped into Unicode's Private Use Area (PUA, U+E000 to U+F8FF). In these fonts,
        printable ASCII characters (code points 32..126) are shifted by 0xE000 + 2 (0xE002),
        e.g., ' ' (0x20) -> 0xE022, 'A' (0x41) -> 0xE043.
        Subtracting (0xE000 + 2) restores the original standard ASCII characters.
        """
        if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
            return "".join(chr(ord(c) - 0xE000 - 2) if 0xE000 <= ord(c) <= 0xF8FF else c for c in text)
        return text

    @classmethod
    def matches(cls, text: str, profile_name: str, bank_name: str) -> bool:
        low_p = (profile_name or "").lower()
        low_b = (bank_name or "").lower()
        if cls.name in low_p or any(k in low_p for k in ("credit agricole", "crédit agricole", "ca nord", "ca pca", "credit_agricole")):
            return True
        if any(k in low_b for k in ("credit agricole", "crédit agricole", "ca nord", "ca pca", "credit_agricole")):
            return True
        low_t = cls._normalize_ca_text(text[:3000]).lower()
        return (
            "crédit agricole" in low_t
            or "credit agricole" in low_t
            or "crèdit agricole" in low_t
            or "agrifrpp" in low_t
            or "caisse regionale de credit agricole" in low_t
            or "caisse régionale de crédit agricole" in low_t
        )

    @classmethod
    def parse(cls, reader, p0: str, all_text: str, force: bool = False) -> Parsed:
        warnings: list[str] = []
        p0 = cls._normalize_ca_text(p0)
        all_text = cls._normalize_ca_text(all_text)

        # IBAN detection: French IBAN is FR followed by 25 alphanumerics
        iban = None
        m_iban = re.search(r"IBAN\s*:\s*(FR\d{2}(?:\s*[A-Z0-9]{4}){5}\s*[A-Z0-9]{3})", all_text, re.I)
        if m_iban:
            iban = m_iban.group(1).replace(" ", "").upper()
        else:
            m_iban_any = re.search(r"\b(FR\d{2}(?:\s*[A-Z0-9]{4}){5}\s*[A-Z0-9]{3})\b", all_text)
            if m_iban_any:
                iban = m_iban_any.group(1).replace(" ", "").upper()

        currency = "EUR"

        def _parse_ca_balance(qualifier: str | None, amt_str: str) -> float:
            val = parse_amount(amt_str, decimal=",")
            if qualifier and re.search(r"d[ée\xd8\xf8]?bit", qualifier, re.I):
                return -abs(val)
            if qualifier and re.search(r"cr[ée\xd8\xf8]?dit", qualifier, re.I):
                return abs(val)
            return val

        rows: list[Row] = []

        # Case 1: Compte Sur Livret (CSL) annual statement
        is_csl = "compte sur livret" in all_text.lower() or ("releve annuel" in all_text.lower() and "csl" in all_text.lower())
        if is_csl:
            m_old = re.search(r"ANCIEN SOLDE(?:\s+([^0-9\s]+))?\s+AU\s+(\d{2}\.\d{2}\.\d{4})\s*:?\s*([0-9\s]+,\d{2}\s*[-+]?)", all_text, re.I)
            m_new = re.search(r"NOUVEAU SOLDE(?:\s+([^0-9\s]+))?\s+AU\s+(\d{2}\.\d{2}\.\d{4})\s*:?\s*([0-9\s]+,\d{2}\s*[-+]?)", all_text, re.I)
            old_bal = _parse_ca_balance(m_old.group(1), m_old.group(3)) if m_old else None
            new_bal = _parse_ca_balance(m_new.group(1), m_new.group(3)) if m_new else None

            dropped_csl = 0
            for line in all_text.splitlines():
                m_tx = re.match(r"^(\d{2}\.\d{2}\.\d{4})\s+(.+?)\s+(\d{2}\.\d{2}\.\d{4})\s+(-?[0-9\s]+,\d{2})$", line.strip())
                if m_tx:
                    d_op, desc, d_val, amt_str = m_tx.groups()
                    try:
                        amt = parse_amount(amt_str, decimal=",")
                        d_parts = d_op.split(".")
                        v_parts = d_val.split(".")
                        datetime(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
                        datetime(int(v_parts[2]), int(v_parts[1]), int(v_parts[0]))
                        iso_d = f"{d_parts[2]}-{d_parts[1]}-{d_parts[0]}"
                        iso_v = f"{v_parts[2]}-{v_parts[1]}-{v_parts[0]}"
                        rows.append(
                            Row(
                                date=iso_d,
                                amount=round(amt, 2),
                                currency=currency,
                                description=desc.strip(),
                                value_date=iso_v,
                                line=len(rows) + 1,
                            )
                        )
                    except Exception:
                        dropped_csl += 1
                        continue

            if dropped_csl > 0:
                warnings.append(f"skipped {dropped_csl} transaction line(s) in CSL statement due to parsing error")

            _reconcile_balances(old_bal, new_bal, rows, warnings, force)

        # Case 2: Standard checking account statement
        if not rows:
            m_old = re.search(r"Ancien solde(?:\s+([^0-9\s]+))?\s+au\s+(\d{2}\.\d{2}\.\d{4})\s*:?\s*([0-9\s]+,\d{2}\s*[-+]?)", all_text, re.I)
            m_new = re.search(r"Nouveau solde(?:\s+([^0-9\s]+))?\s+au\s+(\d{2}\.\d{2}\.\d{4})\s*:?\s*([0-9\s]+,\d{2}\s*[-+]?)", all_text, re.I)

            start_dt = None
            end_dt = None
            old_bal = None
            new_bal = None
            if m_old:
                start_dt = datetime.strptime(m_old.group(2), "%d.%m.%Y")
                old_bal = _parse_ca_balance(m_old.group(1), m_old.group(3))
            if m_new:
                end_dt = datetime.strptime(m_new.group(2), "%d.%m.%Y")
                new_bal = _parse_ca_balance(m_new.group(1), m_new.group(3))

            m_arr = re.search(r"(?:Date d'arr[ée\xd8\xf8]?t[ée\xd8\xf8]?|du relev[ée\xd8\xf8]? au)\s*:?\s*(\d{2}[-/\.]\d{2}[-/\.]\d{4})", all_text, re.I)
            arrete_dt = datetime.strptime(m_arr.group(1).replace("-", ".").replace("/", "."), "%d.%m.%Y") if m_arr else None

            has_balance_headers = bool(re.search(r"\bancien solde\b", all_text, re.I) or re.search(r"\bnouveau solde\b", all_text, re.I))
            if has_balance_headers and (old_bal is None or new_bal is None):
                warnings.append("statement contains balance headers but balances could not be parsed for checksum reconciliation")
            elif old_bal is None and new_bal is None:
                warnings.append("statement contains no balance lines for checksum reconciliation")

            transactions: list[dict] = []
            default_col_split: float = 135.0

            for page in reader.pages:
                layout_text = page.extract_text(extraction_mode="layout") or ""
                layout_lines = layout_text.splitlines()

                # Derive Debit / Credit column boundary from page header line
                page_col_split: float | None = None
                for line in layout_lines:
                    m_hdr = re.search(r"\b(d[ée\xd8\xf8]?bit(?:s)?)\b.*?\b(cr[ée\xd8\xf8]?dit(?:s)?)\b", line, re.I)
                    if m_hdr:
                        page_col_split = (m_hdr.start(1) + m_hdr.start(2)) / 2.0
                        default_col_split = page_col_split
                        break

                active_col_split = page_col_split if page_col_split is not None else default_col_split

                in_tx_table = False
                current_tx = None

                for line in layout_lines:
                    raw_line = line
                    stripped = raw_line.strip()
                    if not stripped:
                        continue

                    if "Total des opérations" in stripped or "Nouveau solde" in stripped:
                        if current_tx:
                            transactions.append(current_tx)
                            current_tx = None
                        in_tx_table = False
                        break

                    if re.search(r"Date\s+Op[ée\xd8\xf8]?ration", stripped, re.I):
                        in_tx_table = True
                        continue

                    if in_tx_table or re.match(r"^\s{0,10}\d{2}\.\d{2}\s+", raw_line):
                        m_start = re.match(r"^\s{0,10}(\d{2}\.\d{2})\s+(.+)$", raw_line)
                        if m_start:
                            if current_tx:
                                transactions.append(current_tx)
                                current_tx = None

                            op_dm = m_start.group(1)
                            rest = m_start.group(2)

                            d_dd, d_mm = int(op_dm[:2]), int(op_dm[3:5])
                            if start_dt and end_dt:
                                if start_dt.year < end_dt.year:
                                    tx_year = start_dt.year if d_mm >= start_dt.month else end_dt.year
                                else:
                                    tx_year = start_dt.year
                            elif end_dt:
                                tx_year = end_dt.year
                            elif start_dt:
                                tx_year = start_dt.year
                            elif arrete_dt:
                                tx_year = arrete_dt.year
                            else:
                                tx_year = None

                            if tx_year is None:
                                warnings.append(f"skipped transaction without statement year: {rest.strip()[:40]}")
                                continue

                            tx_date = f"{tx_year:04d}-{d_mm:02d}-{d_dd:02d}"

                            current_tx = {
                                "date": tx_date,
                                "desc_lines": [rest.strip()],
                                "amount": None,
                                "raw_lines": [raw_line],
                                "col_split": active_col_split,
                            }
                        elif current_tx:
                            current_tx["desc_lines"].append(stripped)
                            current_tx["raw_lines"].append(raw_line)

                if current_tx:
                    transactions.append(current_tx)
                    current_tx = None

            for tx in transactions:
                desc = " ".join(tx["desc_lines"])
                amt = None
                tx_col_split = tx.get("col_split", default_col_split)
                for raw in tx["raw_lines"]:
                    cleaned_raw = re.sub(r"\b\d{2}[/\.]\d{2}\b", "     ", raw)
                    matches = list(re.finditer(r"\b([0-9]{1,3}(?:[ \xa0][0-9]{3})*,[0-9]{2}\s*[-+]?)", cleaned_raw))
                    for m in matches:
                        amt_str = m.group(1).strip()
                        pos = m.start(1)
                        try:
                            val = parse_amount(amt_str, decimal=",")
                        except ValueError:
                            continue
                        if amt_str.endswith("+"):
                            amt = abs(val)
                        elif amt_str.endswith("-"):
                            amt = -abs(val)
                        elif pos >= tx_col_split:
                            amt = abs(val)
                        else:
                            amt = -abs(val)
                        break
                    if amt is not None:
                        break

                if amt is not None:
                    cleaned_desc = re.sub(r"\b[0-9]{1,3}(?:[ \xa0][0-9]{3})*,[0-9]{2}\s*[-+]?", "", desc).strip()
                    cleaned_desc = re.sub(r"\s+", " ", cleaned_desc)
                    rows.append(
                        Row(
                            date=tx["date"],
                            amount=round(amt, 2),
                            currency=currency,
                            description=cleaned_desc or desc,
                            value_date=tx["date"],
                            line=len(rows) + 1,
                        )
                    )

            _reconcile_balances(old_bal, new_bal, rows, warnings, force)

        # Fallback for synthetic single-line test statements
        if not rows:
            lines = [ln.strip() for ln in all_text.splitlines() if ln.strip()]
            for line in lines:
                if any(sw in line.lower() for sw in ("solde", "total", "relevé", "releve", "iban", "bic")):
                    continue
                m = re.match(r"^(\d{2}\.\d{2}(?:\.\d{4})?)\s+(.+?)\s+([0-9\s]*,\d{2}\s*[-+]?)$", line)
                if not m:
                    continue
                d_raw, desc, amt_raw = m.group(1), m.group(2).strip(), m.group(3).strip()
                try:
                    amt = parse_signed_amount(amt_raw, decimal=",", allow_unsigned=False)
                    parts = d_raw.split(".")
                    if len(parts) == 3:
                        iso_d = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    elif end_dt:
                        iso_d = f"{end_dt.year}-{parts[1]}-{parts[0]}"
                    elif start_dt:
                        iso_d = f"{start_dt.year}-{parts[1]}-{parts[0]}"
                    elif arrete_dt:
                        iso_d = f"{arrete_dt.year}-{parts[1]}-{parts[0]}"
                    else:
                        warnings.append(f"skipped single-line transaction without statement year: {desc}")
                        continue
                    rows.append(
                        Row(
                            date=iso_d,
                            amount=round(amt, 2),
                            currency=currency,
                            description=desc,
                            value_date=iso_d,
                            line=len(rows) + 1,
                        )
                    )
                except Exception:
                    continue

            if rows:
                _reconcile_balances(old_bal, new_bal, rows, warnings, force)

        if not rows:
            raise ParseError("no booked transactions found in the Crédit Agricole PDF statement")

        # Extract counterparty & reference from descriptions
        for r in rows:
            desc = r.description
            # Card purchases
            m_cb = re.match(r"^(?:Carte|Paiement CB)\s+(?:\d{2}/\d{2}\s+)?(.+?)(?:\s+\d{6,}|\s+\d{2}/\d{2}|$)", desc, re.I)
            if m_cb:
                r.counterparty = m_cb.group(1).strip()
            # SEPA Virement
            m_vir = re.match(r"^Virement\s+(?:de\s+|en votre faveur de\s+|recu de\s+|vers\s+|a\s+)?(.+?)(?:\s+Ref|\s+Motif|\s+FR\d{2}|$)", desc, re.I)
            if m_vir:
                r.counterparty = m_vir.group(1).strip()
            # SEPA Prélèvement
            m_prlv = re.match(r"^Pr[ée]l[èe]vement\s+(?:sepa\s+)?(.+?)(?:\s+Ref|\s+Mandat|\s+De\s+|$)", desc, re.I)
            if m_prlv:
                r.counterparty = m_prlv.group(1).strip()

            # References
            m_ref = re.search(r"(?:Ref|Mandat|Identifiant|Ref\.:)\s*([A-Za-z0-9_-]+)", desc, re.I)
            if m_ref:
                r.reference = m_ref.group(1)

        return Parsed("pdf", rows, iban=iban, currency=currency, warnings=warnings)
