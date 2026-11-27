"""PDF statement parser profile for ABN AMRO (current and savings accounts)."""

from __future__ import annotations

from datetime import datetime
import re

from coach.ingest.imports.parsers import ParseError, Parsed, Row, parse_amount
from coach.ingest.imports.pdf.common import year_rollover


class AbnAmroPdfProfile:
    """PDF statement parser profile for ABN AMRO (current and savings accounts)."""

    name = "abn_amro"

    @classmethod
    def matches(cls, text: str, profile_name: str, bank_name: str) -> bool:
        low_p = (profile_name or "").lower()
        low_b = (bank_name or "").lower()
        if cls.name in low_p or "abn" in low_p or "abn" in low_b:
            return True
        low_t = text[:3000].lower()
        if re.search(r"\bbic\s*:\s*abnanl2a\b", low_t):
            return True
        is_abn = "abn amro" in low_t or "abnamro" in low_t
        if is_abn and any(
            h in low_t
            for h in [
                "afschrift",
                "rekeningafschrift",
                "periodiek overzicht",
                "betalingsverkeer",
                "volgnummer",
                "blad",
            ]
        ):
            return True
        if is_abn and re.search(r"\bNL\d{2}\s*ABNA", text[:2000], re.I):
            return True
        return False

    @classmethod
    def parse(cls, reader, p0: str, all_text: str, force: bool = False) -> Parsed:
        warnings: list[str] = []

        # Account IBAN detection: Dutch format NLxxABNA..., excluding VAT numbers (NL82...B01)
        iban = None
        account_ibans = [
            ib for ib in re.findall(r"\b([A-Z]{2}\d{2}[A-Z0-9]{4,30})\b", p0)
            if len(ib) >= 15 and not (ib.startswith("NL82") and ib.endswith("B01"))
        ]
        if account_ibans:
            iban = account_ibans[0]

        # Currency
        currency = "EUR"
        m_cur = re.search(r"\b(?:Valuta|Currency)\s*[:]?\s*([A-Z]{3})\b", all_text[:2000], re.I)
        if m_cur:
            currency = m_cur.group(1).upper()
        elif "USD" in all_text[:2000] and "EUR" not in all_text[:2000]:
            currency = "USD"
        elif "GBP" in all_text[:2000] and "EUR" not in all_text[:2000]:
            currency = "GBP"

        # Statement date & year
        m_date = (
            re.search(r"(\d{2}-\d{2}-\d{4})\s*Date", p0, re.I)
            or re.search(r"Date\s*:?\s*(\d{2}-\d{2}-\d{4})", p0, re.I)
            or re.search(r"Datum\s*:?\s*(\d{2}-\d{2}-\d{4})", p0, re.I)
            or re.search(r"(\d{4}-\d{2}-\d{2})", p0)
            or re.search(r"(\d{2}/\d{2}/\d{4})", p0)
        )
        stmt_year = None
        stmt_month = None
        if m_date:
            d_val = m_date.group(1)
            if "-" in d_val and len(d_val.split("-")[0]) == 2:
                stmt_year = int(d_val.split("-")[2])
                stmt_month = int(d_val.split("-")[1])
            elif "-" in d_val and len(d_val.split("-")[0]) == 4:
                stmt_year = int(d_val.split("-")[0])
                stmt_month = int(d_val.split("-")[1])
            elif "/" in d_val:
                stmt_year = int(d_val.split("/")[2])
                stmt_month = int(d_val.split("/")[1])

        # Stated totals for checksum reconciliation
        stated_debit = None
        stated_credit = None
        m_deb = re.search(r"(?:Total amount debit|Totaal debet|Totaal af)\s*[:]?\s*([0-9\.,]+)", all_text, re.I)
        if m_deb:
            try:
                stated_debit = abs(parse_amount(m_deb.group(1), decimal=","))
            except ValueError:
                pass
        m_crd = re.search(r"(?:Total amount credit|Totaal credit|Totaal bij)\s*[:]?\s*([0-9\.,]+)", all_text, re.I)
        if m_crd:
            try:
                stated_credit = abs(parse_amount(m_crd.group(1), decimal=","))
            except ValueError:
                pass

        is_date_first = bool(re.search(r"\n\d{2}-\d{2}\s*\n?\s*\(\d{2}-\d{2}\)", p0))
        rows: list[Row] = []

        # Strategy 1: Layout-aware coordinate extraction
        for pno, p in enumerate(reader.pages):
            elems = []

            def vis(text, cm, tm, font_dict, font_size):
                if text and text.strip():
                    elems.append((tm[4], tm[5], text.strip()))

            try:
                p.extract_text(visitor_text=vis)
            except Exception:
                elems = []

            cutoff = 750 if pno == 0 else 1200
            if is_date_first and elems:
                # 2025-2026 era
                amts = [
                    el for el in elems
                    if el[1] < 200 and re.match(r"^\d{1,3}(?:\.\d{3})*,\d{2}$", el[2]) and el[0] > 350
                ]
                dates = [
                    el for el in elems
                    if el[1] < 200 and re.match(r"^\d{2}-\d{2}$", el[2]) and el[0] < 100
                ]
                for a in amts:
                    val = parse_amount(a[2], decimal=",", thousands=".")
                    is_cr = a[0] > 500
                    matching_d = [d for d in dates if abs(d[1] - a[1]) < 30]
                    if not matching_d:
                        continue
                    d_str = matching_d[0][2]
                    tx_dd, tx_mm = int(d_str.split("-")[0]), int(d_str.split("-")[1])
                    if stmt_year is None:
                        raise ParseError("cannot determine statement year from ABN AMRO statement header")
                    tx_yy = year_rollover(stmt_year, stmt_month or 1, tx_mm)
                    try:
                        iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
                    except ValueError as e:
                        raise ParseError(f"invalid transaction date {tx_dd:02d}-{tx_mm:02d}-{tx_yy}: {e}") from e

                    desc_elems = [
                        el for el in elems
                        if el[1] <= a[1] + 15 and el[1] >= a[1] - 30 and 80 < el[0] < 350
                    ]
                    desc = " ".join(el[2] for el in desc_elems) or "Transaction"
                    amt = val if is_cr else -val
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
            elif elems:
                # 2017-2024 era
                vis_amts = [
                    el for el in elems
                    if el[1] < cutoff and re.match(r"^\d{1,3}(?:\.\d{3})*,\d{2}$", el[2]) and el[0] >= 1000
                ]
                if len(vis_amts) > 0:
                    vis_amts = sorted(vis_amts, key=lambda el: -el[1])
                    for idx, a in enumerate(vis_amts):
                        val = parse_amount(a[2], decimal=",", thousands=".")
                        is_cr = a[0] >= 1500
                        y_top = a[1] + 35
                        y_bottom = vis_amts[idx + 1][1] + 35 if idx + 1 < len(vis_amts) else 0

                        d_candidates = [
                            el for el in elems
                            if abs(el[1] - a[1]) < 35 and re.match(r"^\d{2}-\d{2}$", el[2])
                        ]
                        if not d_candidates:
                            d_candidates = [
                                el for el in elems
                                if abs(el[1] - a[1]) < 35 and re.match(r"^\(\d{2}-\d{2}\)$", el[2])
                            ]
                        if not d_candidates:
                            continue
                        d_str = d_candidates[0][2].strip("()")
                        tx_dd, tx_mm = int(d_str.split("-")[0]), int(d_str.split("-")[1])
                        if stmt_year is None:
                            raise ParseError("cannot determine statement year from ABN AMRO statement header")
                        tx_yy = year_rollover(stmt_year, stmt_month or 1, tx_mm)
                        try:
                            iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
                        except ValueError as e:
                            raise ParseError(f"invalid transaction date {tx_dd:02d}-{tx_mm:02d}-{tx_yy}: {e}") from e

                        desc_elems = [
                            el for el in elems
                            if y_bottom <= el[1] <= y_top and el[0] < 1000 and not re.match(r"^\(?\d{2}-\d{2}\)?$", el[2])
                        ]
                        desc = " ".join(el[2] for el in desc_elems) or "Transaction"
                        amt = val if is_cr else -val
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
                else:
                    # Value date regex block
                    ptxt = p.extract_text() or ""
                    if "(Value date)" in ptxt:
                        ptxt = ptxt.split("(Value date)", 1)[1]
                    matches = list(
                        re.finditer(
                            r"(\s+)(\d{1,3}(?:\.\d{3})*,\d{2})(\s*)\((\d{2}-\d{2})\)\s*(\d{2}-\d{2})",
                            ptxt,
                        )
                    )
                    for idx, m in enumerate(matches):
                        val = parse_amount(m.group(2), decimal=",", thousands=".")
                        is_cr = bool(m.group(3) and "\n" not in m.group(3))
                        val_d = m.group(4)
                        book_d = m.group(5)
                        tx_dd, tx_mm = int(book_d.split("-")[0]), int(book_d.split("-")[1])
                        if stmt_year is None:
                            raise ParseError("cannot determine statement year from ABN AMRO statement header")
                        tx_yy = year_rollover(stmt_year, stmt_month or 1, tx_mm)
                        try:
                            iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
                        except ValueError as e:
                            raise ParseError(f"invalid transaction date {tx_dd:02d}-{tx_mm:02d}-{tx_yy}: {e}") from e

                        v_dd, v_mm = int(val_d.split("-")[0]), int(val_d.split("-")[1])
                        v_yy = year_rollover(stmt_year, stmt_month or 1, v_mm)
                        try:
                            iso_val_d = datetime(v_yy, v_mm, v_dd).date().isoformat()
                        except ValueError:
                            iso_val_d = iso_d

                        start_desc = m.end()
                        end_desc = matches[idx + 1].start() if idx + 1 < len(matches) else len(ptxt)
                        desc = " ".join(ptxt[start_desc:end_desc].strip().split()) or "Transaction"
                        amt = val if is_cr else -val
                        rows.append(Row(iso_d, round(amt, 2), currency, desc, iso_val_d, "", None, {}, len(rows) + 1))

        # Fallback for structured text lines in ABN AMRO statement (e.g. synthetic test fixtures)
        if not rows:
            lines = [ln.strip() for ln in all_text.splitlines() if ln.strip()]
            for line in lines:
                if re.search(r"\b(?:total|totaal|saldo|account|rekening|datum|date)\b", line, re.I):
                    continue
                m = re.search(r"^(\d{2}[-/\.]\d{2}(?:[-/\.]\d{2,4})?)\s+(.+?)\s+([+-]?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}[+-]?)$", line)
                if not m:
                    continue
                d_raw, desc, amt_raw = m.group(1), m.group(2).strip(), m.group(3).strip()
                try:
                    amt = parse_amount(amt_raw, decimal=",")
                    parts = re.split(r"[-/\.]", d_raw)
                    tx_dd, tx_mm = int(parts[0]), int(parts[1])
                    if len(parts) == 3:
                        y = int(parts[2])
                        tx_yy = y + 2000 if y < 100 else y
                    else:
                        if stmt_year is None:
                            raise ParseError("cannot determine statement year from ABN AMRO statement header")
                        tx_yy = year_rollover(stmt_year, stmt_month or 1, tx_mm)
                    iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
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
            raise ParseError("no booked transactions found in the ABN AMRO PDF statement")

        # Checksum reconciliation against stated totals
        if stated_debit is not None:
            actual_debit = round(sum(abs(r.amount) for r in rows if r.amount < 0), 2)
            if abs(actual_debit - stated_debit) > 0.01:
                warnings.append(f"checksum mismatch: total debit {actual_debit:.2f} does not match statement stated total {stated_debit:.2f}")
        if stated_credit is not None:
            actual_credit = round(sum(r.amount for r in rows if r.amount > 0), 2)
            if abs(actual_credit - stated_credit) > 0.01:
                warnings.append(f"checksum mismatch: total credit {actual_credit:.2f} does not match statement stated total {stated_credit:.2f}")

        # Counterparty & reference enrichment
        for r in rows:
            desc = r.description
            m_naam = re.search(r"Naam:\s*(.+?)(?:\s*(?:Machtiging|Omschrijving|IBAN|Kenmerk|BIC)|$)", desc)
            m_bea = re.search(r"[BG]EA\s+NR:[^\s]+\s+[\d\.\/]+\s*(.+?)(?:,PAS|\$)", desc)
            m_ken = re.search(r"Kenmerk:\s*([^\s,]+)", desc)
            m_fac = re.search(r"Factuurnummer:\s*([^\s,]+)", desc)
            if m_naam:
                r.counterparty = m_naam.group(1).strip()
            elif m_bea:
                r.counterparty = m_bea.group(1).strip()
            if m_ken:
                r.reference = m_ken.group(1).strip()
            elif m_fac:
                r.reference = m_fac.group(1).strip()

        return Parsed("pdf", rows, iban=iban, currency=currency, warnings=warnings)
