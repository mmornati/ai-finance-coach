"""Test statement import endpoints via the web API."""
from __future__ import annotations

import base64

from apihelpers import Ctx, ctx, world  # noqa: F401

MINIMAL_OFX = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <CURDEF>EUR
        <BANKACCTFROM>
          <BANKID>12345
          <ACCTID>NL00ABNA0123456789
          <ACCTTYPE>CHECKING
        </BANKACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20260101
          <DTEND>20260131
          <STMTTRN>
            <TRNTYPE>DEBIT
            <DTPOSTED>20260115
            <TRNAMT>-42.50
            <FITID>TX001
            <NAME>Supermarket Groceries
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT
            <DTPOSTED>20260120
            <TRNAMT>1500.00
            <FITID>TX002
            <NAME>Salary Employer
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>
"""


def test_import_profiles_list(ctx: Ctx):
    r = ctx.get("/connections/import/profiles")
    assert r.status_code == 200
    d = r.json()
    assert "profiles" in d
    assert isinstance(d["profiles"], list)


def test_import_preview_and_execute_ofx(ctx: Ctx):
    b64 = base64.b64encode(MINIMAL_OFX).decode("ascii")

    # 1. Preview without specifying account (returns parsed metadata and rows)
    pv = ctx.post("/connections/import/preview", {"file_name": "statement.ofx", "content_base64": b64})
    assert pv.status_code == 200, pv.text
    d = pv.json()
    assert d["format"] == "ofx"
    assert d["rows_total"] == 2
    assert d["rows_new"] == 2
    assert d["date_min"] == "2026-01-15"
    assert d["date_max"] == "2026-01-20"
    assert len(d["sample_rows"]) == 2
    assert d["sample_rows"][0]["amount"] == -42.5

    # 2. Execute import into a new manual account
    ex = ctx.post(
        "/connections/import/execute",
        {
            "file_name": "statement.ofx",
            "content_base64": b64,
            "account": "new:ABN Test Checking",
        },
    )
    assert ex.status_code == 200, ex.text
    rep = ex.json()
    assert rep["account_created"] is True
    assert rep["account_uid"] == "imp-abn-test-checking"
    assert rep["rows_total"] == 2
    assert rep["rows_new"] == 2
    assert rep["rows_duplicate"] == 0

    # 3. Preview again with the same account (dedup check should detect 2 duplicates)
    pv2 = ctx.post(
        "/connections/import/preview",
        {
            "file_name": "statement.ofx",
            "content_base64": b64,
            "account": "imp-abn-test-checking",
        },
    )
    assert pv2.status_code == 200, pv2.text
    d2 = pv2.json()
    assert d2["rows_total"] == 2
    assert d2["rows_new"] == 0
    assert d2["rows_duplicate"] == 2
    assert d2["already_imported"] is not None

    # 4. Execute again should insert 0 new rows
    ex2 = ctx.post(
        "/connections/import/execute",
        {
            "file_name": "statement.ofx",
            "content_base64": b64,
            "account": "imp-abn-test-checking",
        },
    )
    assert ex2.status_code == 200
    assert ex2.json()["rows_new"] == 0
    assert ex2.json()["rows_duplicate"] == 2


def _create_test_pdf(lines: list[str]) -> bytes:
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    stream_content = ["BT", "/F1 12 Tf", "72 700 Td"]
    for idx, l in enumerate(lines):
        if idx > 0:
            stream_content.append("0 -20 Td")
        safe_l = l.replace("(", "").replace(")", "")
        stream_content.append(f"({safe_l}) Tj")
    stream_content.append("ET")

    stream = DecodedStreamObject()
    stream.set_data("\n".join(stream_content).encode("latin1"))

    page[NameObject("/Contents")] = writer._add_object(stream)
    font_dict = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_dict)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): font_ref
        })
    })

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_import_preview_and_execute_pdf(ctx: Ctx):
    pdf_bytes = _create_test_pdf([
        "Date: 01-10-2026",
        "Account: NL00ABNA0123456789",
        "Total amount debit 100,00",
        "Total amount credit 250,00",
        "01-10 Salary Invented Corp 250,00",
        "02-10 Supermarket Groceries 100,00-",
    ])
    b64 = base64.b64encode(pdf_bytes).decode("ascii")

    pv = ctx.post("/connections/import/preview", {"file_name": "statement.pdf", "content_base64": b64})
    assert pv.status_code == 200, pv.text
    d = pv.json()
    assert d["format"] == "pdf"
    assert d["iban"] == "NL00ABNA0123456789"
    assert d["rows_total"] == 2
    assert d["rows_new"] == 2

    ex = ctx.post(
        "/connections/import/execute",
        {
            "file_name": "statement.pdf",
            "content_base64": b64,
            "account": "new:Primary Current",
        },
    )
    assert ex.status_code == 200, ex.text
    rep = ex.json()
    assert rep["account_created"] is True
    assert rep["account_uid"] == "imp-primary-current"
    assert rep["rows_new"] == 2


def test_import_validation_errors(ctx: Ctx):
    # Invalid base64
    r = ctx.post("/connections/import/preview", {"file_name": "test.pdf", "content_base64": "!!!not-valid-base64!!!"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_base64"

    # CSV without profile
    csv_bytes = b"date,amount,description\n2026-01-01,10.0,Test\n"
    b64_csv = base64.b64encode(csv_bytes).decode("ascii")
    r_csv = ctx.post("/connections/import/preview", {"file_name": "test.csv", "content_base64": b64_csv})
    assert r_csv.status_code == 400
    assert r_csv.json()["error"]["code"] == "profile_required"


def test_import_arbitrary_file_write_path_traversal_prevented(ctx: Ctx, tmp_path):
    from pathlib import Path

    target_escape_1 = Path("/tmp/malicious_payload_test.ofx")
    target_escape_2 = Path("/tmp/evil_test.ofx")
    for p in (target_escape_1, target_escape_2):
        if p.exists():
            p.unlink()

    b64 = base64.b64encode(MINIMAL_OFX).decode("ascii")

    # 1. Absolute path injection
    res_abs = ctx.post("/connections/import/preview", {
        "file_name": "/tmp/malicious_payload_test.ofx",
        "content_base64": b64,
    })
    assert res_abs.status_code == 200
    # Proves file was never written to the target absolute path outside the secure temp dir
    assert not target_escape_1.exists()
    # Response filename is sanitized to pure basename without leading slash
    assert res_abs.json()["file_name"] == "malicious_payload_test.ofx"

    # 2. Directory traversal attempt with ../
    res_trav = ctx.post("/connections/import/preview", {
        "file_name": "../../../../tmp/evil_test.ofx",
        "content_base64": b64,
    })
    assert res_trav.status_code == 200
    assert not target_escape_2.exists()
    assert res_trav.json()["file_name"] == "evil_test.ofx"

    # 3. Execution endpoint with directory traversal attempt
    res_exec = ctx.post("/connections/import/execute", {
        "file_name": "../../../escape.ofx",
        "content_base64": b64,
        "account": "new:Protected Account",
    })
    assert res_exec.status_code == 200
    assert not Path("/tmp/escape.ofx").exists()
    assert res_exec.json()["file"] == "escape.ofx"


def test_import_oversized_payload_rejected(ctx: Ctx):
    # Base64 string exceeding length limit
    large_b64 = "A" * (20 * 1024 * 1024 + 10)
    r = ctx.post("/connections/import/preview", {"file_name": "large.ofx", "content_base64": large_b64})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "file_too_large"



