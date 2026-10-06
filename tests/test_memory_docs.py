"""E3-5 documents: private storage, local text extraction, redaction, dry-run payload, LLM extraction -> proposal."""
import stat

import pytest

from coach.classify.backends import LLMBackend, Usage
from coach.cli import main
from coach.memory import documents as D, proposals as P
from coach.memory.store import MemoryStore
from memhelpers import make_memory, make_world

LOAN_TEXT = [
    "LOAN OFFER - Homebank",
    "Borrower: Anna Rossi, born on 12/03/1984",
    "Address: 12 rue des Lilas, 59000 Quimper",
    "Contact anna.rossi@example.org or +33 6 12 34 56 78",
    "Account IBAN FR7630006000011234567890189 reference 123456789012",
    "Amount borrowed: 250 000,00 EUR over 240 months",
    "Nominal rate: 2,10 % fixed. Monthly payment: 1 502,36 EUR",
    "Borrower insurance by SafeCover: 38,00 EUR per month",
    "First instalment on 2020-02-05, last instalment on 2040-01-05",
]


def make_pdf(lines):
    """A minimal valid one-page PDF whose text layer holds `lines` (no external tool needed)."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "BT /F1 11 Tf 40 780 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class FakeBackend(LLMBackend):
    name = "fake"

    def __init__(self, fields=None, error=None):
        self.fields, self.error, self.calls = fields, error, []

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        self.calls.append(dict(static=static, dynamic=dynamic, schema=schema, model=model, purpose=purpose))
        if self.error:
            raise self.error
        return {"fields": self.fields or []}, Usage("fake", model, purpose, items, 10, 5, 0, 0, 0.0, False, 0.1)


@pytest.fixture
def mem(tmp_path):
    return make_memory(tmp_path / "memory")


@pytest.fixture
def store(mem):
    return MemoryStore(mem)


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "Anna Rossi loan offer.pdf"
    p.write_bytes(make_pdf(LOAN_TEXT))
    return p


# ---------------------------------------------------------------- storage

def test_add_copies_privately_under_a_hashed_name_and_records_metadata(store, mem, pdf):
    d = D.add_document(store, pdf, "loan", "home-loan")
    stored = mem / "documents" / d.stored_as
    assert stored.read_bytes() == pdf.read_bytes()
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600 and stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    assert "Rossi" not in d.stored_as and d.stored_as.endswith(".pdf") and d.id == f"doc-{d.sha256[:8]}"
    meta = store.documents()[0]
    assert meta.filename == f"loan-document-{d.sha256[:8]}.pdf" and "Rossi" not in meta.filename and meta.kind == "loan" and meta.for_id == "home-loan" and meta.size == len(pdf.read_bytes())
    assert "for: home-loan" in (mem / "documents.yaml").read_text()
    # the liability file records the path in its documents list (comments of the file survive)
    loan = (mem / "liabilities" / "home-loan.yaml").read_text()
    assert f"documents: [documents/{d.stored_as}]" in loan and "# synthetic loan" in loan


def test_duplicates_bad_kinds_and_unknown_targets_are_refused(store, pdf, tmp_path):
    D.add_document(store, pdf, "loan")
    with pytest.raises(D.DocumentError) as e:
        D.add_document(store, pdf, "loan")
    assert "already stored" in str(e.value)
    other = tmp_path / "o.txt"
    other.write_text("x")
    with pytest.raises(D.DocumentError):
        D.add_document(store, other, "poem")
    with pytest.raises(D.DocumentError):
        D.add_document(store, other, "other", "no-such-id")
    with pytest.raises(D.DocumentError):
        D.add_document(store, tmp_path / "missing.pdf", "other")


def test_documents_are_not_in_the_history_but_the_metadata_is(store, mem, pdf):
    D.add_document(store, pdf, "loan")
    log = store.history()
    assert any("add-document documents.yaml" in c.subject for c in log)
    tracked = store.repo.git("ls-files").stdout.split()
    assert "documents.yaml" in tracked and not any(t.startswith("documents/") for t in tracked)


# ---------------------------------------------------------------- text + redaction

def test_pdf_text_is_extracted_locally(pdf):
    assert "Amount borrowed: 250 000,00 EUR" in D.extract_text(pdf)


def test_a_pdf_without_text_layer_is_refused_with_the_no_ocr_message(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(make_pdf([]))
    with pytest.raises(D.DocumentError) as e:
        D.extract_text(p)
    assert "OCR is out of scope" in str(e.value)
    q = tmp_path / "x.png"
    q.write_bytes(b"\x89PNG")
    with pytest.raises(D.DocumentError) as e:
        D.extract_text(q)
    assert "no OCR" in str(e.value)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(D.DocumentError):
        D.extract_text(bad)


def test_redaction_removes_personal_data_and_keeps_the_amounts():
    r = D.redact_document("\n".join(LOAN_TEXT), {"Anna", "Rossi"})
    t = r.text
    for secret in ("anna.rossi@example.org", "+33 6 12 34 56 78", "FR7630006000011234567890189", "123456789012",
                   "12 rue des Lilas", "59000 Quimper", "12/03/1984", "Anna", "Rossi"):
        assert secret not in t, secret
    for kept in ("250 000,00 EUR", "2,10 %", "1 502,36 EUR", "38,00 EUR", "2040-01-05", "SafeCover", "240 months"):
        assert kept in t, kept
    assert {"e-mail", "iban", "phone", "address", "birth date / place", "name next to a cue"} <= set(r.counts)


def test_household_names_come_from_members_aliases_and_the_database(cfg, store):
    con = make_world(cfg)
    names = D.household_name_tokens(store, con)
    assert {"Anna", "Rossi", "Luca", "Mia"} <= {n.title() for n in names}


# ---------------------------------------------------------------- extraction

def doc(store, pdf):
    return D.add_document(store, pdf, "loan", "home-loan")


def test_dry_run_is_the_default_shows_the_exact_payload_and_sends_nothing(store, pdf):
    d = doc(store, pdf)
    backend = FakeBackend()
    res = D.extract(store, d.id, "liability", "home-loan", backend=backend)       # send=False
    assert not res.sent and backend.calls == []
    payload = res.payload_text
    assert "250 000,00 EUR" in payload and "anna.rossi" not in payload and "Rossi" not in payload
    assert '"enum"' in payload and "monthly_payment" in payload and "rate.nominal" in payload
    assert res.proposal is None
    assert not (store.root / ".proposals").exists() or not list((store.root / ".proposals").iterdir())


def test_send_calls_the_backend_with_exactly_the_shown_payload_and_creates_a_proposal(store, mem, pdf):
    d = doc(store, pdf)
    fields = [
        {"path": "monthly_payment", "value": "1502.36", "snippet": "Monthly payment: 1 502,36 EUR", "confidence": 0.95},
        {"path": "rate.nominal", "value": "2.1", "snippet": "Nominal rate: 2,10 % fixed", "confidence": 0.9},
        {"path": "insurance.provider", "value": "SafeCover", "snippet": "Borrower insurance by SafeCover", "confidence": 0.9},
        {"path": "end_date", "value": "2040-01-05", "snippet": "last instalment on 2040-01-05", "confidence": 0.9},
        {"path": "principal", "value": "250000", "snippet": "Amount borrowed: 250 000,00 EUR", "confidence": 0.9},
    ]
    backend = FakeBackend(fields)
    shown = D.extract(store, d.id, "liability", "home-loan")
    before = (mem / "liabilities" / "home-loan.yaml").read_text()
    res = D.extract(store, d.id, "liability", "home-loan", send=True, backend=backend, model="sonnet")
    assert res.sent and len(backend.calls) == 1
    call = backend.calls[0]
    assert call["static"] == shown.static and call["dynamic"] == shown.dynamic and call["schema"] == shown.schema
    assert call["purpose"] == "doc_extract"
    assert (mem / "liabilities" / "home-loan.yaml").read_text() == before        # NEVER written directly
    p = res.proposal
    assert p and p.status == "pending" and p.source == f"doc-extract:{d.id}" and p.file == "liabilities/home-loan.yaml"
    paths = {c["path"]: c for c in P.describe(store, p)["changes"]}
    assert set(paths) == {"monthly_payment", "insurance.provider", "end_date"}   # principal / nominal rate: already equal
    assert paths["monthly_payment"]["old"] == 1500 and paths["monthly_payment"]["new"] == 1502.36
    assert paths["insurance.provider"]["snippet"] == "Borrower insurance by SafeCover"
    P.accept(store, p.id, confirmed=True)
    text = (mem / "liabilities" / "home-loan.yaml").read_text()
    assert "monthly_payment: 1502.36" in text and "nominal: 2.1" in text and "end_date: 2040-01-05" in text
    assert "provider: SafeCover" in text and "# from the January statement" in text


def test_unverifiable_unknown_and_invalid_fields_are_dropped_not_proposed(store, pdf):
    d = doc(store, pdf)
    fields = [
        {"path": "monthly_payment", "value": "1502.36", "snippet": "this sentence is invented", "confidence": 0.99},
        {"path": "colour", "value": "red", "snippet": "Nominal rate", "confidence": 0.9},
        {"path": "start_date", "value": "someday", "snippet": "First instalment on 2020-02-05", "confidence": 0.9},
        {"path": "lender", "value": "Homebank", "snippet": "LOAN OFFER - Homebank", "confidence": 0.8},
        {"path": "lender", "value": "Other", "snippet": "LOAN OFFER - Homebank", "confidence": 0.8},
    ]
    res = D.extract(store, d.id, "liability", "home-loan", send=True, backend=FakeBackend(fields))
    why = {f["path"]: f["why"] for f in res.rejected_fields}
    assert "not verifiable" in why["monthly_payment"] and "not a field" in why["colour"]
    assert "not a date" in why["start_date"] and why["lender"] == "duplicate"
    assert res.proposal is None                                                  # lender is unchanged: nothing to propose


def test_the_snippet_must_be_in_the_redacted_text_not_in_the_original(store, pdf):
    d = doc(store, pdf)
    fields = [{"path": "lender", "value": "Anna", "snippet": "Borrower: Anna Rossi", "confidence": 0.9}]
    res = D.extract(store, d.id, "liability", "home-loan", send=True, backend=FakeBackend(fields))
    assert res.accepted_fields == [] and "not verifiable" in res.rejected_fields[0]["why"]


def test_extraction_into_a_missing_target_needs_create(store, pdf, mem):
    d = doc(store, pdf)
    with pytest.raises(D.DocumentError) as e:
        D.extract(store, d.id, "liability", "new-loan")
    assert "--create" in str(e.value)
    fields = [{"path": "lender", "value": "Homebank", "snippet": "LOAN OFFER - Homebank", "confidence": 0.9}]
    res = D.extract(store, d.id, "liability", "new-loan", send=True, backend=FakeBackend(fields), create_kind="car_loan")
    assert res.proposal.file == "liabilities/new-loan.yaml" and res.proposal.ops[0]["op"] == "create"
    assert not (mem / "liabilities" / "new-loan.yaml").exists()
    P.accept(store, res.proposal.id, confirmed=True)
    assert "lender: Homebank" in (mem / "liabilities" / "new-loan.yaml").read_text()


def test_asset_targets_and_unknown_target_kinds(store, mem, tmp_path):
    t = tmp_path / "statement.txt"
    t.write_text("Savings statement. Balance: 5 400,00 EUR as of 2026-09-30. Opened 2019-05-02.")
    d = D.add_document(store, t, "statement", "savings-book")
    fields = [{"path": "balance", "value": "5400", "snippet": "Balance: 5 400,00 EUR", "confidence": 0.9},
              {"path": "as_of", "value": "2026-09-30", "snippet": "as of 2026-09-30", "confidence": 0.9}]
    res = D.extract(store, d.id, "asset", "savings-book", send=True, backend=FakeBackend(fields))
    assert {c["path"] for c in P.describe(store, res.proposal)["changes"]} == {"assets[savings-book].balance", "assets[savings-book].as_of"}
    P.accept(store, res.proposal.id, confirmed=True)
    text = (mem / "assets.yaml").read_text()
    assert "balance: 5400" in text and "as_of: 2026-09-30" in text and "# tax-free savings" in text
    with pytest.raises(D.DocumentError):
        D.extract(store, d.id, "gadget", "x")


def test_usage_is_logged_when_a_database_is_given(cfg, store, pdf):
    con = make_world(cfg)
    d = doc(store, pdf)
    D.extract(store, d.id, "liability", "home-loan", send=True, backend=FakeBackend(), con=con)
    assert con.execute("SELECT purpose, backend FROM llm_usage").fetchall() == [("doc_extract", "fake")]


def test_the_backend_error_leaves_memory_untouched(store, mem, pdf):
    d = doc(store, pdf)
    before = (mem / "liabilities" / "home-loan.yaml").read_text()
    with pytest.raises(RuntimeError):
        D.extract(store, d.id, "liability", "home-loan", send=True, backend=FakeBackend(error=RuntimeError("down")))
    assert (mem / "liabilities" / "home-loan.yaml").read_text() == before and P.listing(store) == []


# ---------------------------------------------------------------- CLI

def run(cfg, *argv):
    main(["--config", str(cfg.config_path), "--insecure", *argv])


def test_cli_doc_add_list_and_dry_run_extract(cfg, pdf, capsys, monkeypatch):
    make_world(cfg)
    run(cfg, "memory", "doc", "add", str(pdf), "--kind", "loan", "--for", "home-loan")
    assert "stored as doc-" in capsys.readouterr().out
    run(cfg, "memory", "doc", "list")
    out = capsys.readouterr().out
    assert "loan" in out and "Rossi" not in out
    doc_id = out.split()[0]
    monkeypatch.setattr("coach.classify.backends.get_backend",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no backend in a dry run")))
    run(cfg, "memory", "doc", "extract", doc_id, "--into", "liability", "home-loan")
    out = capsys.readouterr().out
    assert "EXACTLY WHAT WOULD BE SENT" in out and "DRY RUN: nothing was sent" in out and "Rossi" not in out
    assert "redacted:" in out and "e-mail" in out
    with pytest.raises(SystemExit):
        run(cfg, "memory", "doc", "extract", doc_id, "--into", "liability", "home-loan", "--send", "--dry-run")


def test_cli_send_uses_the_configured_backend_and_prints_a_proposal(cfg, pdf, capsys, monkeypatch):
    make_world(cfg)
    run(cfg, "memory", "doc", "add", str(pdf), "--kind", "loan")
    capsys.readouterr()
    doc_id = MemoryStore(cfg.memory_dir).documents()[0].id
    backend = FakeBackend([{"path": "monthly_payment", "value": "1502.36", "snippet": "Monthly payment: 1 502,36 EUR",
                            "confidence": 0.9}])
    monkeypatch.setattr("coach.classify.backends.get_backend", lambda *a, **k: backend)
    run(cfg, "memory", "doc", "extract", doc_id, "--into", "liability", "home-loan", "--send")
    out = capsys.readouterr().out
    assert "snippet-found  monthly_payment" in out and "nothing was written to memory" in out and "accept p-" in out
    assert len(backend.calls) == 1
    run(cfg, "memory", "proposals")
    assert "set monthly_payment: 1500 -> 1502.36" in capsys.readouterr().out


def test_the_e9_loan_fields_can_be_extracted_with_their_units_and_verified_against_a_snippet(store, mem, pdf):
    """E9-1: the contract extraction covers the first payment date and the term (the other new fields follow the same verified path)."""
    d = doc(store, pdf)
    fields = [
        {"path": "first_payment_date", "value": "2020-02-05", "snippet": "First instalment on 2020-02-05", "confidence": 0.9},
        {"path": "term_months", "value": "240", "snippet": "Amount borrowed: 250 000,00 EUR over 240 months", "confidence": 0.9},
        {"path": "deferral.months", "value": "6", "snippet": "no such sentence about a deferral in the offer", "confidence": 0.9},     # not verifiable
        {"path": "insurance.basis", "value": "initial", "snippet": "Borrower insurance by SafeCover: 38,00 EUR per month", "confidence": 0.9},   # no such word
        {"path": "payment_day", "value": "5", "snippet": "First instalment on 2020-02-05", "confidence": 0.9},     # not extractable: not a field of the extraction
    ]
    res = D.extract(store, d.id, "liability", "home-loan", send=True, backend=FakeBackend(fields))
    why = {f["path"]: f["why"] for f in res.rejected_fields}
    assert {f["path"] for f in res.accepted_fields} == {"first_payment_date", "term_months"}
    assert "not verifiable" in why["deferral.months"] and "does not appear as a word" in why["insurance.basis"] and "not a field" in why["payment_day"]
    assert {o["path"] for o in res.proposal.ops} == {"first_payment_date", "term_months"} and res.proposal.status == "pending"
    P.accept(store, res.proposal.id, confirmed=True)
    text = (mem / "liabilities" / "home-loan.yaml").read_text()
    assert "first_payment_date: 2020-02-05" in text and "term_months: 240" in text
