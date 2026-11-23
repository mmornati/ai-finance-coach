"""Statement parsers: CSV (profile), OFX/QFX (SGML 1.x and XML 2.x) and CAMT.053 XML.

Every parser returns a :class:`Parsed` with booked rows only (ISO dates, signed amounts, negative = money out).
They are strict: a row whose date or amount cannot be read aborts the import with the line number instead of
being silently dropped.
"""
from __future__ import annotations

import csv
import html
import re
import unicodedata
import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException
from dataclasses import dataclass, field
from datetime import datetime

from coach.ingest.imports.profiles import Profile


class ParseError(Exception):
    pass


@dataclass
class Row:
    date: str
    amount: float
    currency: str
    description: str
    value_date: str | None = None
    counterparty: str = ""
    reference: str | None = None
    raw: dict = field(default_factory=dict)
    line: int = 0


@dataclass
class Parsed:
    format: str
    rows: list[Row]
    iban: str | None = None
    currency: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- text helpers

def decode(data: bytes, encoding: str = "auto") -> str:
    """utf-8 (with or without BOM) first, then cp1252, then latin-1 (never fails)."""
    if encoding != "auto":
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError) as e:
            raise ParseError(f"cannot decode the file as {encoding}: {e}") from e
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def fold(s: str) -> str:
    """Lowercase, accent-free, single-spaced: header names compare on this."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("﻿", "")).strip().lower()


def parse_amount(text: str, decimal: str = "auto", thousands: str = "") -> float:
    t = (text or "").strip()
    if not t:
        raise ValueError("empty amount")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = re.sub(r"[€$£\s  ]|EUR|USD|GBP", "", t, flags=re.I)
    if t.endswith("-"):
        neg, t = True, t[:-1]
    if t.startswith("+"):
        t = t[1:]
    if thousands:
        t = t.replace(thousands, "")
    if decimal == "auto":
        if "," in t and "." in t:
            decimal = "," if t.rfind(",") > t.rfind(".") else "."
        elif "," in t:
            decimal = "," if re.search(r",\d{1,2}$", t) else "."   # "1,234" = thousands
        else:
            decimal = "."
    if decimal == ",":
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise ValueError(f"not an amount: {text!r}")
    v = float(t)
    return -v if neg else v


def parse_date(text: str, formats: list[str]) -> str:
    t = (text or "").strip()
    cands = [t] + ([t.split()[0]] if " " in t else []) + ([t.split("T")[0]] if "T" in t else [])
    for c in cands:
        for f in formats:
            try:
                return datetime.strptime(c, f).date().isoformat()
            except ValueError:
                continue
    raise ValueError(f"unreadable date {text!r} (expected {' or '.join(formats)})")


# ---------------------------------------------------------------- CSV

def _delimiter(sample: str, wanted: str) -> str:
    if wanted != "auto":
        return "\t" if wanted in ("\\t", "tab") else wanted
    counts = {d: sample.count(d) for d in (";", ",", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ";"


def parse_csv(data: bytes, p: Profile) -> Parsed:
    text = decode(data, p.encoding)
    lines = text.splitlines()
    if p.header_contains:
        want = [fold(x) for x in p.header_contains]
        idx = next((i for i, ln in enumerate(lines) if all(w in fold(ln) for w in want)), None)
        if idx is None:
            raise ParseError(f"no header line containing {p.header_contains} found (wrong file or profile?)")
        start = idx
    else:
        start = p.skip_rows
    if p.skip_footer:
        lines = lines[: len(lines) - p.skip_footer]
    body = lines[start:]
    if not body:
        raise ParseError("the file has no data after the skipped lines")
    delim = _delimiter(body[0], p.delimiter)
    reader = list(csv.reader(body, delimiter=delim, quotechar='"'))
    if p.has_header:
        header = [fold(h) for h in reader[0]]
        data_rows, first_line = reader[1:], start + 2
    else:
        header, data_rows, first_line = [], reader, start + 1

    def col(spec):
        """Index for a column spec (0-based int, or a header name); None if absent."""
        if isinstance(spec, int):
            return spec
        if not header:
            raise ParseError(f"column {spec!r} given by name but the profile says has_header = false")
        name = fold(str(spec))
        if name not in header:
            raise ParseError(f"column {spec!r} not found; the file has: {', '.join(h for h in header if h)}")
        return header.index(name)

    cols = {}
    for key, spec in p.columns.items():
        cols[key] = [col(s) for s in spec] if isinstance(spec, list) else col(spec)
    fcol = col(p.filter_column) if p.filter_column else None
    keep = {fold(k) for k in p.filter_keep}

    def cell(row, idx):
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    rows: list[Row] = []
    bad: list[str] = []
    for n, r in enumerate(data_rows, start=first_line):
        if not any(c.strip() for c in r):
            continue
        if fcol is not None and fold(cell(r, fcol)) not in keep:
            continue
        dtxt = cell(r, cols["date"])
        if not dtxt:
            continue                      # no date: a total / footer / blank-ish line
        try:
            d = parse_date(dtxt, p.date_format)
            vd = parse_date(cell(r, cols["value_date"]), p.date_format) if cell(r, cols.get("value_date")) else None
            if "amount" in cols:
                amt = parse_amount(cell(r, cols["amount"]), p.decimal, p.thousands)
            else:
                deb, cre = cell(r, cols.get("debit")), cell(r, cols.get("credit"))
                if not deb and not cre:
                    raise ValueError("no debit nor credit amount")
                amt = (abs(parse_amount(cre, p.decimal, p.thousands)) if cre else 0.0) \
                    - (abs(parse_amount(deb, p.decimal, p.thousands)) if deb else 0.0)
            if "fee" in cols and cell(r, cols["fee"]):
                amt -= parse_amount(cell(r, cols["fee"]), p.decimal, p.thousands)
        except ValueError as e:
            bad.append(f"line {n}: {e}")
            continue
        if p.negate:
            amt = -amt
        dspec = cols["description"]
        parts = [cell(r, i) for i in (dspec if isinstance(dspec, list) else [dspec])]
        desc = " | ".join(dict.fromkeys(x for x in parts if x))
        cur = cell(r, cols.get("currency")) or p.currency
        rows.append(Row(d, round(amt, 2), cur.upper(), re.sub(r"\s+", " ", desc), vd,
                        cell(r, cols.get("counterparty")), cell(r, cols.get("reference")) or None,
                        {h: c for h, c in zip(header, r) if h} if header else {"row": r}, n))
    if bad:
        shown = "; ".join(bad[:5]) + (f"; ... {len(bad) - 5} more" if len(bad) > 5 else "")
        raise ParseError(f"{len(bad)} unreadable row(s), nothing imported: {shown}")
    if not rows:
        raise ParseError("no transaction rows found (check the profile: header, skip_rows, filter)")
    return Parsed("csv", rows)


# ---------------------------------------------------------------- OFX / QFX

def _ofx_date(v: str) -> str:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", v.strip())
    if not m:
        raise ValueError(f"unreadable OFX date {v!r}")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def parse_ofx(data: bytes) -> Parsed:
    """OFX 1.x (SGML, unclosed tags) and 2.x (XML) bank/credit-card statements, QFX included."""
    text = decode(data)
    iban = (re.search(r"<ACCTID>\s*([^<\r\n]+)", text) or [None, None])[1]
    cur = (re.search(r"<CURDEF>\s*([A-Za-z]{3})", text) or [None, None])[1]
    blocks = re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|</CCSTMTRS>|</STMTRS>|$)",
                        text, flags=re.S | re.I)
    if not blocks:
        raise ParseError("no <STMTTRN> transactions found: not an OFX bank/card statement")
    rows, bad = [], []
    for n, b in enumerate(blocks, start=1):
        f = {}
        for tag, val in re.findall(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", b):
            f.setdefault(tag.upper(), html.unescape(val.strip()))
        try:
            d = _ofx_date(f["DTPOSTED"])
            amt = parse_amount(f["TRNAMT"], "auto")
        except (KeyError, ValueError) as e:
            bad.append(f"transaction {n}: {e!r}")
            continue
        desc = " | ".join(dict.fromkeys(x for x in (f.get("NAME"), f.get("MEMO")) if x))
        vd = _ofx_date(f["DTUSER"]) if f.get("DTUSER") else None
        rows.append(Row(d, round(amt, 2), (f.get("CURRENCY") or cur or "EUR").upper(),
                        re.sub(r"\s+", " ", desc), vd, f.get("NAME", ""), f.get("FITID"), dict(f), n))
    if bad:
        raise ParseError(f"{len(bad)} unreadable transaction(s), nothing imported: {'; '.join(bad[:5])}")
    return Parsed("ofx", rows, iban=iban.replace(" ", "") if iban and re.match(r"[A-Z]{2}\d{2}", iban.replace(" ", ""))
                  else None, currency=(cur or "").upper() or None)


# ---------------------------------------------------------------- CAMT.053

def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.split("}", 1)[-1]


def _t(el, path: str) -> str:
    x = el.find(path) if el is not None else None
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_camt053(data: bytes) -> Parsed:
    # check the DECODED text too: a UTF-16/32 document hides the declaration from a byte-level search
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ParseError("unsupported XML encoding (UTF-16/32 without a byte-order mark)")
    text = decode(data)
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I) or re.search(rb"<!(DOCTYPE|ENTITY)", data, re.I):
        raise ParseError("refusing an XML file with a DOCTYPE/ENTITY declaration (not valid CAMT.053)")
    try:
        root = DET.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException) as e:
        raise ParseError(f"invalid XML: {e}") from e
    _strip_ns(root)
    stmts = root.findall(".//Stmt") or root.findall(".//Rpt")
    if not stmts:
        raise ParseError("no <Stmt> element: not a CAMT.053 bank statement")
    rows, warnings, ibans = [], [], []
    for stmt in stmts:
        iban = _t(stmt, "Acct/Id/IBAN") or _t(stmt, "Acct/Id/Othr/Id")
        if iban and iban not in ibans:
            ibans.append(iban)
        for ntry in stmt.findall("Ntry"):
            sts = _t(ntry, "Sts/Cd") or _t(ntry, "Sts")
            if sts and sts.upper() not in ("BOOK", "BOOKED"):
                warnings.append(f"skipped a {sts} entry")
                continue
            amt_el = ntry.find("Amt")
            try:
                amt = parse_amount(amt_el.text, ".")
                cur = amt_el.get("Ccy", "EUR")
                d = _t(ntry, "BookgDt/Dt") or _t(ntry, "BookgDt/DtTm")[:10] or _t(ntry, "ValDt/Dt")
                if not d:
                    raise ValueError("entry without booking date")
                d = parse_date(d, ["%Y-%m-%d"])
            except (ValueError, AttributeError) as e:
                raise ParseError(f"unreadable CAMT entry ({e}); nothing imported") from e
            debit = _t(ntry, "CdtDbtInd").upper() == "DBIT"
            if _t(ntry, "RvslInd").lower() == "true":
                debit = not debit
            amt = -abs(amt) if debit else abs(amt)
            parts = [x.text.strip() for x in ntry.iter() if x.tag == "Ustrd" and x.text and x.text.strip()]
            parts += [x.text.strip() for x in ntry.iter() if x.tag == "Ref" and x.text and x.text.strip()
                      and x.text.strip() not in parts][:1]
            if _t(ntry, "AddtlNtryInf"):
                parts.append(_t(ntry, "AddtlNtryInf"))
            cp_path = "NtryDtls/TxDtls/RltdPties/Cdtr/Nm" if debit else "NtryDtls/TxDtls/RltdPties/Dbtr/Nm"
            cp = _t(ntry, cp_path) or _t(ntry, cp_path.replace("/Nm", "/Pty/Nm"))
            desc = " | ".join(dict.fromkeys(([cp] if cp else []) + parts))
            vd = _t(ntry, "ValDt/Dt") or None
            rows.append(Row(d, round(amt, 2), cur.upper(), re.sub(r"\s+", " ", desc), vd, cp,
                            _t(ntry, "AcctSvcrRef") or _t(ntry, "NtryRef") or None,
                            {"status": sts, "amount": amt_el.text}, len(rows) + 1))
    if len(ibans) > 1:
        warnings.append(f"the file holds statements of several accounts: {', '.join(ibans)}")
    if not rows:
        raise ParseError("no booked entries found in the CAMT.053 file")
    return Parsed("camt053", rows, iban=(ibans[0].replace(" ", "") if len(ibans) == 1 and
                                          re.match(r"[A-Z]{2}\d{2}", ibans[0]) else None), warnings=warnings)


# ---------------------------------------------------------------- PDF statements (E1-10b)

MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_PAGES = 50


class AbnAmroPdfProfile:
    """PDF statement parser profile for ABN AMRO (current and savings accounts)."""
    name = "abn_amro"

    @classmethod
    def matches(cls, text: str, profile_name: str, bank_name: str) -> bool:
        low_p = (profile_name or "").lower()
        low_b = (bank_name or "").lower()
        if "abn" in low_p or "abn" in low_b:
            return True
        low_t = text[:3000].lower()
        return "abn amro" in low_t or "abnamro" in low_t

    @classmethod
    def parse(cls, reader, p0: str, all_text: str) -> Parsed:
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
                    tx_yy = stmt_year - 1 if tx_mm == 12 and (stmt_month or 1) == 1 else stmt_year
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
                    rows.append(Row(iso_d, round(amt, 2), currency, desc, iso_d, "", None, {}, len(rows) + 1))
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
                        tx_yy = stmt_year - 1 if tx_mm == 12 and (stmt_month or 1) == 1 else stmt_year
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
                        rows.append(Row(iso_d, round(amt, 2), currency, desc, iso_d, "", None, {}, len(rows) + 1))
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
                        tx_yy = stmt_year - 1 if tx_mm == 12 and (stmt_month or 1) == 1 else stmt_year
                        try:
                            iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
                        except ValueError as e:
                            raise ParseError(f"invalid transaction date {tx_dd:02d}-{tx_mm:02d}-{tx_yy}: {e}") from e

                        v_dd, v_mm = int(val_d.split("-")[0]), int(val_d.split("-")[1])
                        v_yy = stmt_year - 1 if v_mm == 12 and (stmt_month or 1) == 1 else stmt_year
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
                        tx_yy = stmt_year - 1 if tx_mm == 12 and (stmt_month or 1) == 1 else stmt_year
                    iso_d = datetime(tx_yy, tx_mm, tx_dd).date().isoformat()
                    rows.append(Row(iso_d, round(amt, 2), currency, desc, iso_d, "", None, {}, len(rows) + 1))
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
    def parse(cls, reader, p0: str, all_text: str) -> Parsed:
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

            # Sign disambiguation: if unsigned, determine if debit or credit
            if not (amt_raw.startswith(("+", "-")) or amt_raw.endswith(("+", "-")) or amt_raw.startswith("(")):
                low_desc = desc.lower()
                if any(w in low_desc for w in (
                    "virement reçu", "salaire", "salary", "remboursement", "refund",
                    "credit", "crédit", "deposit", "bij", "storting", "inkomen",
                    "ontvangen", "gecrediteerd", "loonaanbetaling", "interest", "intérêts"
                )):
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


PDF_PROFILES = [
    AbnAmroPdfProfile,
    GenericPdfProfile,
]


def parse_pdf(data: bytes, profile: Profile | str | None = None, bank: str | None = None) -> Parsed:
    """Parse a bank statement PDF into a Parsed structure.

    Enforces input file size and page count limits. Dispatches parsing to registered
    per-bank profiles (e.g. ABN AMRO with coordinate extraction, checksum reconciliation,
    and layout handling) or a safe generic statement parser with balance exclusion and
    reconciliation warnings.
    """
    import io
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if len(data) > MAX_PDF_SIZE_BYTES:
        raise ParseError(f"PDF file exceeds maximum allowed size ({MAX_PDF_SIZE_BYTES // (1024 * 1024)} MB)")

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, Exception) as e:
        raise ParseError("unreadable or corrupted PDF file") from e

    if not reader.pages:
        raise ParseError("empty PDF file: no pages found")

    if len(reader.pages) > MAX_PDF_PAGES:
        raise ParseError(f"PDF statement exceeds maximum allowed page count ({MAX_PDF_PAGES})")

    p0 = reader.pages[0].extract_text() or ""
    all_text = "\n".join(p.extract_text() or "" for p in reader.pages)

    prof_name = profile.name if isinstance(profile, Profile) else (profile or "")
    bank_name = bank or ""

    selected = None
    for p_cls in PDF_PROFILES:
        if p_cls.matches(all_text, prof_name, bank_name):
            selected = p_cls
            break
    if not selected:
        selected = GenericPdfProfile

    return selected.parse(reader, p0, all_text)


# ---------------------------------------------------------------- detection

def detect_format(name: str, data: bytes) -> str:
    low = name.lower()
    head = data[:4096].lstrip(b"\xef\xbb\xbf \r\n\t")
    if low.endswith((".ofx", ".qfx")) or b"OFXHEADER" in data[:512] or b"<OFX>" in data[:4096].upper():
        return "ofx"
    if low.endswith(".xml") or head.startswith(b"<?xml") or b"BkToCstmrStmt" in data[:8192]:
        return "camt053"
    if low.endswith(".pdf") or data[:4] == b"%PDF":
        return "pdf"
    return "csv"
