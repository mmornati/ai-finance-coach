"""PDF statement parser profiles and dispatching engine."""

from __future__ import annotations

import io
from pypdf import PdfReader

from coach.ingest.imports.parsers import ParseError, Parsed, Profile
from coach.ingest.imports.pdf.common import (
    MAX_PDF_PAGES,
    MAX_PDF_SIZE_BYTES,
    _reconcile_balances,
    parse_signed_amount,
    year_rollover,
)
from coach.ingest.imports.pdf.abn_amro import AbnAmroPdfProfile
from coach.ingest.imports.pdf.commerzbank import CommerzbankPdfProfile
from coach.ingest.imports.pdf.credit_agricole import CreditAgricolePdfProfile
from coach.ingest.imports.pdf.generic import GenericPdfProfile

PDF_PROFILES = [
    AbnAmroPdfProfile,
    CommerzbankPdfProfile,
    CreditAgricolePdfProfile,
    GenericPdfProfile,
]


def parse_pdf(
    data: bytes,
    profile: Profile | str | None = None,
    bank: str | None = None,
    force: bool = False,
) -> Parsed:
    """Parse a bank statement PDF into a Parsed structure.

    Enforces input file size and page count limits. Dispatches parsing to registered
    per-bank profiles (e.g. ABN AMRO with coordinate extraction, Commerzbank with
    balance reconciliation, Crédit Agricole with layout extraction) or a safe
    generic statement parser with balance exclusion and reconciliation warnings.
    """
    if len(data) > MAX_PDF_SIZE_BYTES:
        raise ParseError(f"PDF file exceeds maximum allowed size ({MAX_PDF_SIZE_BYTES // (1024 * 1024)} MB)")

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
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

    return selected.parse(reader, p0, all_text, force=force)


__all__ = [
    "MAX_PDF_PAGES",
    "MAX_PDF_SIZE_BYTES",
    "PDF_PROFILES",
    "AbnAmroPdfProfile",
    "CommerzbankPdfProfile",
    "CreditAgricolePdfProfile",
    "GenericPdfProfile",
    "_reconcile_balances",
    "parse_pdf",
    "parse_signed_amount",
    "year_rollover",
]
