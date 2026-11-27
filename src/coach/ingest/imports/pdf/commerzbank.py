"""PDF statement parser profile for Commerzbank (Kontoauszug & Umsatzübersicht)."""

from __future__ import annotations

import re

from coach.ingest.imports.parsers import ParseError, Parsed, Row
from coach.ingest.imports.pdf.common import _reconcile_balances, parse_signed_amount, year_rollover


class CommerzbankPdfProfile:
    """PDF statement parser profile for Commerzbank (Kontoauszug & Umsatzübersicht)."""

    name = "commerzbank"

    @classmethod
    def matches(cls, text: str, profile_name: str, bank_name: str) -> bool:
        low_p = (profile_name or "").lower()
        low_b = (bank_name or "").lower()
        if cls.name in low_p or "commerz" in low_p or "commerz" in low_b:
            return True
        low_t = text[:3000].lower()
        if re.search(r"\bbic\s*:\s*cobadeff", low_t):
            return True
        if re.search(r"commerzbank\s+(?:ag|aktiengesellschaft)", low_t):
            return True
        if "commerzbank" in low_t and any(h in low_t for h in ["kontoauszug", "umsatzübersicht"]):
            return True
        return False

    @classmethod
    def parse(cls, reader, p0: str, all_text: str, force: bool = False) -> Parsed:
        warnings: list[str] = []

        # IBAN detection: German IBAN is DE followed by 20 digits (often grouped in 4s)
        iban = None
        m_iban = re.search(r"\b(DE\d{2}(?:\s*\d{4}){4}\s*\d{2})\b", p0)
        if m_iban:
            iban = m_iban.group(1).replace(" ", "")
        else:
            m_iban_fallback = re.search(r"IBAN:\s*([A-Z0-9\s]{18,36})", p0)
            if m_iban_fallback:
                cleaned = m_iban_fallback.group(1).replace(" ", "").strip()
                if cleaned.startswith("DE") and len(cleaned) >= 22:
                    cleaned = cleaned[:22]
                if re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", cleaned):
                    iban = cleaned

        # Currency
        currency = "EUR"
        if "kontowährung euro" in all_text[:3000].lower():
            currency = "EUR"
        elif "usd" in all_text[:2000].lower() and "eur" not in all_text[:2000].lower():
            currency = "USD"

        rows: list[Row] = []

        # Check if this is an online export (Umsatzübersicht)
        if "umsatzübersicht" in all_text[:2000].lower():
            lines = [ln.strip() for ln in all_text.splitlines() if ln.strip()]
            i = 0
            skipped_entries = 0
            while i < len(lines):
                m_d = re.match(r"^(\d{2}\.\d{2}\.\d{4})$", lines[i])
                if m_d:
                    tx_date_raw = m_d.group(1)
                    i += 1
                    desc_parts = []
                    amt_val = None
                    while i < len(lines) and not re.match(r"^\d{2}\.\d{2}\.\d{4}$", lines[i]):
                        m_amt = re.match(r"^([+-]\s*[0-9\.,]+)\s*EUR$", lines[i])
                        if m_amt:
                            try:
                                amt_val = parse_signed_amount(
                                    m_amt.group(1), decimal=",", thousands=".", allow_unsigned=False
                                )
                            except ValueError:
                                pass
                        elif not any(h in lines[i].lower() for h in ["seite ", "umsatzübersicht", "www.commerzbank"]):
                            desc_parts.append(lines[i])
                        i += 1
                    if amt_val is not None:
                        d, m, y = tx_date_raw.split(".")
                        iso_d = f"{y}-{m}-{d}"
                        desc = " ".join(desc_parts).strip() or "Transaction"
                        rows.append(
                            Row(
                                date=iso_d,
                                amount=round(amt_val, 2),
                                currency=currency,
                                description=desc,
                                value_date=iso_d,
                                line=len(rows) + 1,
                            )
                        )
                    else:
                        skipped_entries += 1
                else:
                    i += 1
            if skipped_entries:
                warnings.append(
                    f"skipped {skipped_entries} Umsatzübersicht entry/entries without valid amount line ([+-] ... EUR)"
                )
            if not rows:
                raise ParseError("no booked transactions found in the Commerzbank Umsatzübersicht PDF")
            return Parsed("pdf", rows, iban=iban, currency=currency, warnings=warnings)

        # Standard Kontoauszug
        m_old = re.search(r"Alter Kontostand vom\s+(\d{2}\.\d{2}\.\d{4})\s+([0-9\.]*,\d{2}\s*[-+]?)", all_text)
        m_new = re.search(r"Neuer Kontostand vom\s+(\d{2}\.\d{2}\.\d{4})\s+([0-9\.]*,\d{2}\s*[-+]?)", all_text)

        old_bal = None
        new_bal = None
        if m_old:
            try:
                old_bal = parse_signed_amount(m_old.group(2), decimal=",", thousands=".", allow_unsigned=True)
            except ValueError:
                pass
        if m_new:
            try:
                new_bal = parse_signed_amount(m_new.group(2), decimal=",", thousands=".", allow_unsigned=True)
            except ValueError:
                pass

        stmt_year = None
        stmt_month = None
        if m_new:
            parts = m_new.group(1).split(".")
            stmt_month, stmt_year = int(parts[1]), int(parts[2])
        elif m_old:
            parts = m_old.group(1).split(".")
            stmt_month, stmt_year = int(parts[1]), int(parts[2])
        else:
            m_header_d = re.search(r"Kontoauszug vom\s+(\d{2}\.\d{2}\.\d{4})", all_text)
            if m_header_d:
                parts = m_header_d.group(1).split(".")
                stmt_month, stmt_year = int(parts[1]), int(parts[2])

        tx_starter_re = re.compile(r"^(.*?)\s+(\d{2}\.\d{2})\s+([0-9\.]*,\d{2}\s*[-+]?)$")
        stop_words = [
            "alter kontostand", "neuer kontostand", "angaben zu den umsätzen",
            "umsätze vom", "zwischensumme", "kontostand", "guthaben sind als",
            "seite-nr", "folgeseite", "der angegebene kontostand",
        ]
        skip_headers = [
            "iban:", "bic :", "ust-idnr", "telefonnummer", "kontowährung",
            "zu ihren lasten", "ihr ansprechpartner", "beratungscenter",
        ]

        current_bdate = None
        for page in reader.pages:
            ptxt = page.extract_text() or ""
            lines = [ln.strip() for ln in ptxt.splitlines() if ln.strip()]

            i = 0
            while i < len(lines):
                line = lines[i]
                m_b = re.match(r"^Buchungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", line)
                if m_b:
                    current_bdate = m_b.group(1)
                    i += 1
                    continue

                m_tx = tx_starter_re.match(line)
                if m_tx:
                    desc_head, valuta_str, amt_str = m_tx.groups()
                    desc_lower = desc_head.lower()
                    if any(sw in desc_lower for sw in stop_words):
                        i += 1
                        continue
                    try:
                        amt = parse_signed_amount(
                            amt_str, decimal=",", thousands=".", allow_unsigned=False
                        )
                    except ValueError as e:
                        warnings.append(f"skipped transaction line with invalid or unsigned amount {amt_str!r}: {e}")
                        i += 1
                        continue

                    i += 1
                    desc_lines = [desc_head]
                    while i < len(lines):
                        next_l = lines[i]
                        if (
                            re.match(r"^Buchungsdatum:\s*\d{2}\.\d{2}\.\d{4}", next_l)
                            or tx_starter_re.match(next_l)
                            or any(sw in next_l.lower() for sw in stop_words)
                        ):
                            break
                        if not any(h in next_l.lower() for h in skip_headers):
                            desc_lines.append(next_l)
                        i += 1

                    full_desc = " ".join(desc_lines).strip()
                    valuta_iso = None
                    if valuta_str and stmt_year:
                        vd, vm = valuta_str.split(".")
                        v_dd, v_mm = int(vd), int(vm)
                        v_yy = year_rollover(stmt_year, stmt_month or 1, v_mm)
                        valuta_iso = f"{v_yy}-{v_mm:02d}-{v_dd:02d}"

                    if current_bdate:
                        d, m, y = current_bdate.split(".")
                        iso_d = f"{y}-{m}-{d}"
                    elif valuta_iso:
                        iso_d = valuta_iso
                    else:
                        warnings.append(f"skipped transaction without booking date or statement year: {desc_head}")
                        continue

                    valuta_iso = valuta_iso or iso_d

                    rows.append(
                        Row(
                            date=iso_d,
                            amount=round(amt, 2),
                            currency=currency,
                            description=full_desc,
                            value_date=valuta_iso,
                            line=len(rows) + 1,
                        )
                    )
                else:
                    i += 1

        # Fallback for synthetic single-line test statements
        if not rows:
            lines = [ln.strip() for ln in all_text.splitlines() if ln.strip()]
            for line in lines:
                if any(sw in line.lower() for sw in stop_words):
                    continue
                m = re.match(r"^(\d{2}\.\d{2}(?:\.\d{4})?)\s+(.+?)\s+([0-9\.]*,\d{2}\s*[-+]?)$", line)
                if not m:
                    continue
                d_raw, desc, amt_raw = m.group(1), m.group(2).strip(), m.group(3).strip()
                try:
                    amt = parse_signed_amount(amt_raw, decimal=",", thousands=".", allow_unsigned=False)
                    parts = d_raw.split(".")
                    if len(parts) == 3:
                        iso_d = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    elif stmt_year:
                        d_mm = int(parts[1])
                        d_yy = year_rollover(stmt_year, stmt_month or 1, d_mm)
                        iso_d = f"{d_yy}-{parts[1]}-{parts[0]}"
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

        if not rows:
            raise ParseError("no booked transactions found in the Commerzbank PDF statement")

        # Checksum reconciliation against statement balances
        _reconcile_balances(old_bal, new_bal, rows, warnings, force)

        # Counterparty and reference extraction
        for r in rows:
            desc = r.description
            m_ref = re.search(r"End-to-End-Ref\.:\s*([^\s]+)", desc)
            m_mandat = re.search(r"Mandatsref:\s*([^\s]+)", desc)
            m_kunden = re.search(r"Kundenreferenz:\s*([^\s]+)", desc)
            if m_ref and m_ref.group(1) != "NOTPROVIDED":
                r.reference = m_ref.group(1)
            elif m_mandat:
                r.reference = m_mandat.group(1)
            elif m_kunden:
                r.reference = m_kunden.group(1)

            # Counterparty extraction
            m_card = re.match(
                r"^Kartenzahlung\s+(.+?)(?://|\s+\d{4}-\d{2}-\d{2}|\s+End-to-End-Ref|\s+Mandatsref|\s+Kundenreferenz|$)",
                desc,
            )
            if m_card:
                r.counterparty = m_card.group(1).strip()
            else:
                s = re.sub(
                    r"^(?:Gutschrift|Überweisung|Dauerauftrag|Lastschrift|Basislastschrift)\s+",
                    "",
                    desc,
                    flags=re.I,
                )
                m_bic_iban = re.match(
                    r"^(.+?)\s+[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\s+[A-Z]{2}\d{2}[A-Z0-9]+",
                    s,
                )
                if m_bic_iban:
                    r.counterparty = m_bic_iban.group(1).strip()
                else:
                    m_corp = re.match(
                        r"^(.+?\b(?:GmbH|Aktiengesellschaft|AG|e\.V\.|SE|S\.a\.r\.l\.|Cie|S\.C\.A\.))\b",
                        s,
                    )
                    if m_corp:
                        r.counterparty = m_corp.group(1).strip()

        return Parsed("pdf", rows, iban=iban, currency=currency, warnings=warnings)
