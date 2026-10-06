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


# ---------------------------------------------------------------- detection

def detect_format(name: str, data: bytes) -> str:
    low = name.lower()
    head = data[:4096].lstrip(b"\xef\xbb\xbf \r\n\t")
    if low.endswith((".ofx", ".qfx")) or b"OFXHEADER" in data[:512] or b"<OFX>" in data[:4096].upper():
        return "ofx"
    if low.endswith(".xml") or head.startswith(b"<?xml") or b"BkToCstmrStmt" in data[:8192]:
        return "camt053"
    if low.endswith(".pdf") or data[:4] == b"%PDF":
        raise ParseError("PDF statements are not supported yet (follow-up: E1-10b); export CSV/OFX/CAMT.053 "
                         "from your bank instead")
    return "csv"
