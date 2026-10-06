import dataclasses
from pathlib import Path

import pytest

from coach.cli import main
from coach.db import connect
from coach.ingest.imports import core, parsers
from coach.ingest.imports.parsers import ParseError, parse_amount, parse_date
from coach.ingest.imports.profiles import Profile, ProfileError, load_profile, save_profile
from coach.ingest.sync import sync_account
from helpers import FakeClient, add_bank, eb_tx

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config" / "import_profiles"


@pytest.fixture
def con(cfg):
    return connect(cfg, insecure=True, create=True)


# ---------------------------------------------------------------- amounts and dates

@pytest.mark.parametrize("text,dec,want", [
    ("-1 234,56", ",", -1234.56), ("1.234,56", ",", 1234.56), ("12,5", ",", 12.5), ("1 234,00 €", ",", 1234.0),
    ("1,234.56", ".", 1234.56), ("-12.50", ".", -12.5), ("(45,10)", ",", -45.1), ("45,10-", ",", -45.1),
    ("+7,00", ",", 7.0), ("1.234,56", "auto", 1234.56), ("1,234.56", "auto", 1234.56), ("12,50", "auto", 12.5),
    ("1,234", "auto", 1234.0), ("3.5", "auto", 3.5)])
def test_parse_amount(text, dec, want):
    assert parse_amount(text, dec) == pytest.approx(want)


@pytest.mark.parametrize("bad", ["", "abc", "1,2,3x", "--5"])
def test_parse_amount_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_amount(bad, ",")


def test_parse_date_formats():
    assert parse_date("03/10/2026", ["%d/%m/%Y"]) == "2026-10-03"
    assert parse_date("03/10/26", ["%d/%m/%Y", "%d/%m/%y"]) == "2026-10-03"
    assert parse_date("2026-10-03 14:05:09", ["%Y-%m-%d"]) == "2026-10-03"        # time ignored
    assert parse_date("2026-10-03T14:05:09", ["%Y-%m-%d"]) == "2026-10-03"
    with pytest.raises(ValueError):
        parse_date("31/02/2026", ["%d/%m/%Y"])


# ---------------------------------------------------------------- CSV, French shapes

FR_CSV = ("Compte;Courant\n"
          "Période;du 01/09/2026 au 30/09/2026\n"
          "\n"
          "Date;Libellé;Débit;Crédit\n"
          "01/09/2026;CARTE 30/08 BOULANGERIE ÉLISE;4,20;\n"
          "02/09/2026;VIR SEPA EMPLOYEUR SA;;2 500,00\n"
          "02/09/2026;PRLV EDF FACTURE;1 234,56;\n"
          "05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n"
          "05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n")


def fr_profile(**kw):
    p = Profile(delimiter=";", header_contains=["Date", "Libellé"], decimal=",",
                columns={"date": "Date", "description": "Libellé", "debit": "Débit", "credit": "Crédit"})
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_csv_fr_latin1_header_skipping_debit_credit_decimal_comma():
    got = parsers.parse_csv(FR_CSV.encode("latin-1"), fr_profile())               # latin-1 encoded accents
    assert [(r.date, r.amount) for r in got.rows] == [
        ("2026-09-01", -4.2), ("2026-09-02", 2500.0), ("2026-09-02", -1234.56), ("2026-09-05", -3.5), ("2026-09-05", -3.5)]
    assert "BOULANGERIE ÉLISE" in got.rows[0].description and got.rows[0].currency == "EUR"
    assert parsers.parse_csv(FR_CSV.encode("utf-8"), fr_profile()).rows[0].description == got.rows[0].description
    assert parsers.parse_csv(("﻿" + FR_CSV).encode("utf-8"), fr_profile()).rows[1].amount == 2500.0   # BOM


def test_csv_skip_rows_instead_of_header_search_and_single_amount_column():
    text = "junk\njunk\nDate;Label;Montant\n03/10/2026;SHOP;-12,30\n04/10/2026;PAY;100,00\n;TOTAL;\n"
    p = Profile(delimiter="auto", skip_rows=2, columns={"date": "Date", "description": "Label", "amount": "Montant"},
                decimal=",")
    rows = parsers.parse_csv(text.encode(), p).rows
    assert [(r.date, r.amount) for r in rows] == [("2026-10-03", -12.3), ("2026-10-04", 100.0)]    # footer w/o date ignored
    p.negate = True
    assert parsers.parse_csv(text.encode(), p).rows[0].amount == 12.3


def test_csv_no_header_uses_column_indexes():
    p = Profile(delimiter=",", has_header=False, decimal=".", date_format=["%Y-%m-%d"],
                columns={"date": 0, "description": 1, "amount": 2})
    assert parsers.parse_csv(b"2026-10-01,SHOP,-5.5\n", p).rows[0].amount == -5.5


def test_csv_errors_are_loud_not_silent():
    p = fr_profile()
    with pytest.raises(ParseError, match="line 5"):
        parsers.parse_csv(FR_CSV.replace("4,20", "4,2,0").encode(), p)
    with pytest.raises(ParseError, match="not found"):
        parsers.parse_csv(FR_CSV.encode(), fr_profile(columns={"date": "Datum", "description": "Libellé", "amount": "X"}))
    with pytest.raises(ParseError, match="no header line"):
        parsers.parse_csv(b"a;b\n1;2\n", p)


def test_csv_state_filter_and_fee(con):
    text = ("Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"
            "CARD_PAYMENT,Current,2026-09-01 10:00:00,2026-09-02 08:00:00,Corner Shop,-10.00,0.50,EUR,COMPLETED,90.00\n"
            "CARD_PAYMENT,Current,2026-09-03 10:00:00,,Pending Shop,-5.00,0.00,EUR,PENDING,85.00\n"
            "TOPUP,Current,2026-09-04 10:00:00,2026-09-04 10:00:05,Top-Up,50.00,0.00,EUR,COMPLETED,135.00\n"
            "CARD_PAYMENT,Current,2026-09-05 10:00:00,2026-09-05 10:00:05,US Shop,-20.00,0.00,USD,COMPLETED,115.00\n")
    prof = load_profile(PROFILES, "revolut-csv")
    rows = parsers.parse_csv(text.encode(), prof).rows
    assert [(r.date, r.amount, r.currency) for r in rows] == [
        ("2026-09-02", -10.5, "EUR"), ("2026-09-04", 50.0, "EUR"), ("2026-09-05", -20.0, "USD")]   # PENDING dropped, fee applied
    f = Path(con.execute("PRAGMA database_list").fetchone()[2]).parent / "rev.csv"
    f.write_text(text)
    with pytest.raises(core.ImportFailed, match="use --currency"):
        core.import_file(con, f, "new:Kids", prof)
    rep = core.import_file(con, f, "new:Kids", prof, currency_filter="eur")
    assert rep.rows_new == 2 and rep.rows_skipped == 1


def test_shipped_profiles_load_and_validate():
    names = {p.stem for p in PROFILES.glob("*.toml")}
    assert {"generic-csv-fr", "caisse-epargne-csv", "revolut-csv"} <= names
    for n in names:
        p = load_profile(PROFILES, n)
        assert "verify" in (PROFILES / f"{n}.toml").read_text().lower() or n == "generic-csv-fr"


def test_caisse_epargne_profile_parses_a_synthetic_export():
    text = ("Numéro de compte : 00000000000;;;;;;;;;;;;\n"
            "Date de comptabilisation;Libelle simplifie;Libelle operation;Reference;Informations complementaires;"
            "Type operation;Categorie;Sous categorie;Debit;Credit;Date operation;Date de valeur;Pointage operation\n"
            "01/09/26;CB SHOP;CARTE 31/08 SHOP;REF1;;Carte bancaire;Achats;Divers;-12,30;;31/08/26;01/09/26;0\n"
            "02/09/26;VIR EMPLOYEUR;VIREMENT SALAIRE;REF2;;Virement;Revenus;Salaire;;1 800,00;02/09/26;02/09/26;0\n")
    rows = parsers.parse_csv(text.encode("latin-1"), load_profile(PROFILES, "caisse-epargne-csv")).rows
    assert [(r.date, r.amount) for r in rows] == [("2026-09-01", -12.3), ("2026-09-02", 1800.0)]
    assert rows[0].reference == "REF1" and rows[0].value_date == "2026-09-01"


def test_generic_fr_profile_parses_fr_csv():
    rows = parsers.parse_csv(FR_CSV.replace("Période", "Periode").encode("latin-1"),
                             dataclasses.replace(load_profile(PROFILES, "generic-csv-fr"), header_contains=["Date", "Libellé"])).rows
    assert len(rows) == 5


def test_profile_validation_and_safe_names(tmp_path):
    with pytest.raises(ProfileError):
        load_profile(tmp_path, "../etc/passwd")
    with pytest.raises(ProfileError, match="not found"):
        load_profile(tmp_path, "nope")
    with pytest.raises(ProfileError, match="columns.amount"):
        Profile(columns={"date": "d", "description": "x"}).validate()
    with pytest.raises(ProfileError, match="unknown column key"):
        Profile(columns={"date": "d", "description": "x", "amount": "a", "bogus": "z"}).validate()


# ---------------------------------------------------------------- OFX

OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>EUR
<BANKACCTFROM><BANKID>12345<ACCTID>FR7600000000000000000000001<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260901<DTEND>20260930
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260901120000<TRNAMT>-4.20<FITID>F1<NAME>BOULANGERIE &amp; CIE<MEMO>CARTE 30/08</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260902<TRNAMT>2500.00<FITID>F2<NAME>SALAIRE</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260905<TRNAMT>-3,50<FITID>F3<NAME>CAFE DU COIN</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>EUR</CURDEF>
<BANKACCTFROM><ACCTID>FR7600000000000000000000001</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20260901</DTPOSTED><TRNAMT>-4.20</TRNAMT><FITID>F1</FITID><NAME>BOULANGERIE</NAME></STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


def test_ofx_sgml_and_xml_and_qfx_name():
    got = parsers.parse_ofx(OFX_SGML.encode("cp1252"))
    assert [(r.date, r.amount, r.reference) for r in got.rows] == [
        ("2026-09-01", -4.2, "F1"), ("2026-09-02", 2500.0, "F2"), ("2026-09-05", -3.5, "F3")]
    assert got.rows[0].description == "BOULANGERIE & CIE | CARTE 30/08" and got.iban == "FR7600000000000000000000001"
    assert parsers.parse_ofx(OFX_XML.encode()).rows[0].amount == -4.2
    assert parsers.detect_format("export.QFX", b"anything") == "ofx"
    with pytest.raises(ParseError):
        parsers.parse_ofx(b"<OFX><NOTHING></OFX>")


# ---------------------------------------------------------------- CAMT.053

CAMT = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>
<Stmt><Id>S1</Id><Acct><Id><IBAN>FR7600000000000000000000001</IBAN></Id></Acct>
<Ntry><NtryRef>N1</NtryRef><Amt Ccy="EUR">4.20</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts>
 <BookgDt><Dt>2026-09-01</Dt></BookgDt><ValDt><Dt>2026-09-02</Dt></ValDt><AcctSvcrRef>REF-1</AcctSvcrRef>
 <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Boulangerie Elise</Nm></Cdtr></RltdPties><RmtInf><Ustrd>Pain du 31/08</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">2500.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-09-02</Dt></BookgDt>
 <AddtlNtryInf>SALAIRE SEPTEMBRE</AddtlNtryInf></Ntry>
<Ntry><Amt Ccy="EUR">9.99</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>PDNG</Sts><BookgDt><Dt>2026-09-03</Dt></BookgDt></Ntry>
<Ntry><Amt Ccy="EUR">1.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><RvslInd>true</RvslInd><Sts>BOOK</Sts><BookgDt><Dt>2026-09-04</Dt></BookgDt></Ntry>
</Stmt></BkToCstmrStmt></Document>"""


def test_camt053():
    got = parsers.parse_camt053(CAMT.encode())
    assert [(r.date, r.amount) for r in got.rows] == [("2026-09-01", -4.2), ("2026-09-02", 2500.0), ("2026-09-04", 1.0)]
    assert got.rows[0].description.startswith("Boulangerie Elise | Pain du 31/08")
    assert got.rows[0].value_date == "2026-09-02" and got.rows[0].reference == "REF-1"
    assert got.iban == "FR7600000000000000000000001" and any("PDNG" in w for w in got.warnings)
    assert "SALAIRE SEPTEMBRE" in got.rows[1].description
    assert parsers.detect_format("a.xml", CAMT.encode()) == "camt053"


def test_camt_refuses_entity_declarations_and_garbage():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><Document><Stmt/></Document>'
    with pytest.raises(ParseError, match="DOCTYPE"):
        parsers.parse_camt053(evil)
    with pytest.raises(ParseError):
        parsers.parse_camt053(b"<Document></Document>")
    with pytest.raises(ParseError, match="invalid XML"):
        parsers.parse_camt053(b"<a><b></a>")


def test_pdf_is_refused_with_a_clear_message(tmp_path, con):
    f = tmp_path / "statement.pdf"
    f.write_bytes(b"%PDF-1.7 ...")
    with pytest.raises(core.ImportFailed, match="PDF statements are not supported"):
        core.import_file(con, f, "new:X")


# ---------------------------------------------------------------- dedup guarantees

def write(tmp_path, name, text, enc="utf-8"):
    f = tmp_path / name
    f.write_bytes(text.encode(enc))
    return f


def test_reimport_same_file_adds_nothing_and_identical_rows_are_kept(con, tmp_path):
    f = write(tmp_path, "sep.csv", FR_CSV, "latin-1")
    r1 = core.import_file(con, f, "new:Joint Main", fr_profile())
    assert (r1.rows_total, r1.rows_new, r1.rows_duplicate) == (5, 5, 0) and r1.account_created
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5          # two identical coffees both kept
    r2 = core.import_file(con, f, "new:Joint Main", fr_profile())
    assert (r2.rows_new, r2.rows_duplicate) == (0, 5) and not r2.account_created and r2.already_imported
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM import_rows").fetchone()[0] == 5
    uid = r1.account_uid
    assert uid == "imp-joint-main" and con.execute(
        "SELECT source, label, name, session_id FROM accounts WHERE uid=?", (uid,)).fetchone() == ("import", "Joint Main", None, None)


def test_overlapping_files_only_add_the_difference(con, tmp_path):
    a = write(tmp_path, "a.csv", FR_CSV)
    b = write(tmp_path, "b.csv", FR_CSV.replace("05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n",
                                                "05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n"
                                                "05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n"
                                                "05/09/2026;CARTE 04/09 CAFÉ DU COIN;3,50;\n"   # a third identical coffee
                                                "09/09/2026;NEW ROW;9,00;\n"))
    core.import_file(con, a, "new:A", fr_profile())
    rb = core.import_file(con, b, "imp-a", fr_profile())
    assert rb.rows_new == 2 and rb.rows_duplicate == 5          # 7 rows read, 5 already there
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
    assert core.import_file(con, b, "imp-a", fr_profile()).rows_new == 0


def test_dry_run_writes_nothing_even_for_a_new_account(con, tmp_path):
    f = write(tmp_path, "sep.csv", FR_CSV)
    rep = core.import_file(con, f, "new:Ghost", fr_profile(), dry_run=True)
    assert rep.rows_new == 5 and rep.dry_run and len(rep.new_rows) == 5
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
    text = core.format_report(rep)
    assert "DRY RUN" in text and "would insert" in text and "BOULANGERIE" in text


def test_import_then_sync_same_account_does_not_duplicate_api_rows(con, tmp_path):
    """Import covers rows the API later returns: the API version replaces the imported one (fingerprint match)."""
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR7600000000000000000000001", "CHK")])
    f = write(tmp_path, "h.csv", "Date;Libellé;Montant\n01/09/2026;CARTE 30/08 SHOP;-10,00\n02/09/2026;OLD ONE;-3,00\n")
    prof = Profile(delimiter=";", decimal=",", columns={"date": "Date", "description": "Libellé", "amount": "Montant"})
    core.import_file(con, f, "acc1", prof)
    assert con.execute("SELECT COUNT(*) FROM import_rows").fetchone()[0] == 2
    con.execute("INSERT INTO tx_overrides VALUES (?, 'food.groceries', 'mine')",
                (con.execute("SELECT tx_key FROM transactions WHERE amount=-10").fetchone()[0],))
    con.commit()
    fc = FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "carte 30/08  SHOP!"),   # same after normalisation
                                                eb_tx("r9", "2026-09-10", -1.0, "NEW")]}]})
    res = sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    assert res["new"] == 1                                                   # net: SHOP replaced, NEW added
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE amount=-10").fetchone()[0] == 1
    assert con.execute("SELECT tx_key FROM transactions WHERE amount=-10").fetchone()[0] == "acc1:ref:r1"
    assert con.execute("SELECT tx_key, note FROM tx_overrides").fetchone() == ("acc1:ref:r1", "mine")   # user data follows
    assert con.execute("SELECT COUNT(*) FROM import_rows").fetchone()[0] == 1                           # OLD ONE stays imported
    # a second sync returning the same rows adds nothing
    fc = FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "carte 30/08  SHOP!"),
                                                eb_tx("r9", "2026-09-10", -1.0, "NEW")]}]})
    assert sync_account(con, fc, "acc1", False, True, out=lambda *_: None)["new"] == 0


def test_sync_then_import_does_not_duplicate_api_rows(con, tmp_path):
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR7600000000000000000000001", "CHK")])
    fc = FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "CARTE 30/08 SHOP"),
                                                eb_tx("r2", "2026-09-02", -3.0, "Pâtisserie")]}]})
    sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    f = write(tmp_path, "h.csv", "Date;Libellé;Montant\n01/09/2026;carte 30/08 shop;-10,00\n"
                                 "02/09/2026;PATISSERIE;-3,00\n03/09/2026;OLDER ROW;-7,00\n")
    prof = Profile(delimiter=";", decimal=",", columns={"date": "Date", "description": "Libellé", "amount": "Montant"})
    rep = core.import_file(con, f, "acc1", prof)
    assert (rep.rows_new, rep.rows_duplicate) == (1, 2)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3


def test_known_limit_different_wording_is_not_deduplicated(con, tmp_path):
    """Documented limit: the fingerprint needs the same normalised description."""
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR7600000000000000000000001", "CHK")])
    sync_account(con, FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "SHOP PARIS 75")]}]}),
                 "acc1", False, False, out=lambda *_: None)
    f = write(tmp_path, "h.csv", "Date;Libellé;Montant\n01/09/2026;CARTE SHOP;-10,00\n")
    prof = Profile(delimiter=";", decimal=",", columns={"date": "Date", "description": "Libellé", "amount": "Montant"})
    assert core.import_file(con, f, "acc1", prof).rows_new == 1


def test_safety_checks_wrong_iban_and_currency(con, tmp_path):
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR7600000000000000000000002", "CHK")])
    f = write(tmp_path, "x.ofx", OFX_SGML, "cp1252")
    with pytest.raises(core.ImportFailed, match="wrong account"):
        core.import_file(con, f, "acc1")
    con.execute("UPDATE accounts SET iban='FR7600000000000000000000001', currency='USD'")
    con.commit()
    with pytest.raises(core.ImportFailed, match="account acc1 is USD"):
        core.import_file(con, f, "acc1")
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    with pytest.raises(core.ImportFailed, match="No account matches"):
        core.import_file(con, f, "nope")


def test_ofx_and_camt_idempotent_and_cross_format_overlap(con, tmp_path):
    ofx = write(tmp_path, "a.ofx", OFX_SGML, "cp1252")
    r = core.import_file(con, ofx, "new:Old Card")
    assert r.rows_new == 3 and r.format == "ofx"
    assert core.import_file(con, ofx, "imp-old-card").rows_new == 0
    camt = write(tmp_path, "a.xml", CAMT)                               # same IBAN as the OFX account
    with pytest.raises(core.ImportFailed, match="already belongs to account imp-old-card"):
        core.import_file(con, camt, "new:Camt Acct")
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1       # no duplicate account created
    with pytest.raises(core.ImportFailed, match="--account imp-old-card"):
        core.import_file(con, camt, "new:Camt Acct", dry_run=True)
    r = core.import_file(con, camt, "new:Camt Acct", force=True)
    assert r.rows_new == 3 and r.format == "camt053"
    assert core.import_file(con, camt, "imp-camt-acct").rows_new == 0


def test_new_account_label_clash_and_reuse(con, tmp_path):
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR76", "CHK")])
    con.execute("UPDATE accounts SET label='Main'")
    con.commit()
    f = write(tmp_path, "a.ofx", OFX_SGML, "cp1252")
    with pytest.raises(core.ImportFailed, match="already labelled"):
        core.import_file(con, f, "new:main")
    assert core.slug("Compte épargne n°2!") == "compte-epargne-n-2"


# ---------------------------------------------------------------- CLI

def test_cli_import_list_profiles_dry_run_save_profile_and_real_import(cfg, tmp_path, capsys):
    connect(cfg, insecure=True, create=True).close()
    pdir = tmp_path / "config" / "import_profiles"
    pdir.mkdir(parents=True)
    cfg2 = dataclasses.replace(cfg, import_profiles_dir=pdir)
    (tmp_path / "config.toml").write_text(cfg.config_path.read_text() + f'\n[import]\nprofiles_dir = "{pdir}"\n')
    base = ["--config", str(tmp_path / "config.toml"), "--insecure"]
    f = write(tmp_path, "sep.csv", FR_CSV, "latin-1")
    main(base + ["import", str(f), "--account", "new:Main", "--dry-run", "--delimiter", ";", "--decimal", ",",
                 "--header-contains", "Date", "Libellé", "--date-col", "Date", "--desc-col", "Libellé",
                 "--debit-col", "Débit", "--credit-col", "Crédit"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "new: 5" in out
    assert connect(cfg2, insecure=True).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    main(base + ["import", str(f), "--account", "new:Main", "--delimiter", ";", "--decimal", ",",
                 "--header-contains", "Date", "Libellé", "--date-col", "Date", "--desc-col", "Libellé",
                 "--debit-col", "Débit", "--credit-col", "Crédit", "--save-profile", "my-bank"])
    assert "saved mapping profile" in capsys.readouterr().out and (pdir / "my-bank.toml").exists()
    main(base + ["import", "--list-profiles"])
    assert "my-bank" in capsys.readouterr().out
    # the saved profile reproduces the import: idempotent re-run through --profile
    main(base + ["import", str(f), "--account", "imp-main", "--profile", "my-bank"])
    assert "new: 0" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already exists"):
        main(base + ["import", str(f), "--account", "imp-main", "--profile", "my-bank", "--save-profile", "my-bank",
                     "--date-col", "Date"])
    with pytest.raises(SystemExit, match="needs --profile"):
        main(base + ["import", str(f), "--account", "imp-main"])


def test_cli_import_missing_file_and_usage(cfg):
    connect(cfg, insecure=True, create=True).close()
    base = ["--config", str(cfg.config_path), "--insecure"]
    with pytest.raises(SystemExit, match="file not found"):
        main(base + ["import", "/no/such.csv", "--account", "new:X"])
    with pytest.raises(SystemExit, match="usage"):
        main(base + ["import"])


# ---------------------------------------------------------------- review round 2 (items 8, 9, 10, 11)

def _api_replaces_import(con, tmp_path, memory_dir=None):
    add_bank(con, "s1", "Bank", "FR", [("acc1", "FR7600000000000000000000001", "CHK"),
                                       ("acc2", "FR7600000000000000000000002", "OTHER")])
    f = write(tmp_path, "h.csv", "Date;Libellé;Montant\n01/09/2026;CARTE 30/08 SHOP;-10,00\n")
    prof = Profile(delimiter=";", decimal=",", columns={"date": "Date", "description": "Libellé", "amount": "Montant"})
    core.import_file(con, f, "acc1", prof)
    old = con.execute("SELECT tx_key FROM transactions").fetchone()[0]
    con.execute("INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description) "
                "VALUES ('acc2:x','acc2','2026-09-01',10,'EUR','IN')")
    con.execute("INSERT INTO tx_enriched VALUES (?, 'internal_transfer', NULL, 'x', 'x', NULL, NULL)", (old,))
    con.execute("INSERT INTO tx_enriched VALUES ('acc2:x', 'internal_transfer', NULL, 'x', 'x', NULL, NULL)")
    con.commit()
    return old


def test_item8_replaced_key_follows_transfer_links_and_rejections_and_memory_is_warned(con, tmp_path):
    from coach import transfers as tm
    old = _api_replaces_import(con, tmp_path)
    tm.link_transfer(con, old, "acc2:x")
    tm.unlink_transfer(con, "acc2:x")                                     # rejected pair (old, acc2:x)
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    (mem / "categorization.yaml").write_text(
        f"annotations:\n  - id: ann-shop\n    match: {{tx_keys: ['{old}']}}\n    category: food.groceries\n")
    tm.link_transfer(con, old, "acc2:x")
    out = []
    fc = FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "carte 30/08 SHOP")]}]})
    from coach.ingest.sync import sync_all
    res = sync_all(con, fc, "acc1", out=out.append, memory_dir=mem)
    assert res[0]["replaced"] == 1 and res[0]["new"] == 0 and res[0]["replaced_keys"] == [(old, "acc1:ref:r1")]
    assert any("replaced_imports=1" in l for l in out)
    assert any("ann-shop" in l and old in l and "update memory/categorization.yaml" in l for l in out)
    assert con.execute("SELECT out_tx_key FROM transfer_links").fetchone()[0] == "acc1:ref:r1"
    tm.unlink_transfer(con, "acc1:ref:r1")
    assert con.execute("SELECT out_tx_key, in_tx_key FROM transfer_rejections").fetchall() == [("acc1:ref:r1", "acc2:x")]
    assert (mem / "categorization.yaml").read_text().count(old) == 1       # memory/ is never edited by us
    assert tm.match_transfers(con).linked == []                           # the unlinked pair is not re-linked


def test_item8_rejection_written_before_replacement_is_rewritten(con, tmp_path):
    from coach import transfers as tm
    old = _api_replaces_import(con, tmp_path)
    tm.link_transfer(con, old, "acc2:x")
    tm.unlink_transfer(con, old)
    assert con.execute("SELECT out_tx_key FROM transfer_rejections").fetchone()[0] == old
    from coach.ingest.sync import sync_account
    sync_account(con, FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "carte 30/08 SHOP")]}]}),
                 "acc1", False, False, out=lambda *_: None)
    assert con.execute("SELECT out_tx_key, in_tx_key FROM transfer_rejections").fetchall() == [("acc1:ref:r1", "acc2:x")]
    assert tm.match_transfers(con).linked == []


@pytest.mark.parametrize("enc", ["utf-16", "utf-16-le-bom", "utf-16-be-bom"])
def test_item9_utf16_doctype_cannot_bypass_the_entity_guard(enc):
    evil = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY a "aaaa">]><Document><Stmt/></Document>'
    data = {"utf-16": evil.encode("utf-16"), "utf-16-le-bom": b"\xff\xfe" + evil.encode("utf-16-le"),
            "utf-16-be-bom": b"\xfe\xff" + evil.encode("utf-16-be")}[enc]
    with pytest.raises(ParseError, match="DOCTYPE"):
        parsers.parse_camt053(data)
    with pytest.raises(ParseError, match="unsupported XML encoding"):
        parsers.parse_camt053(evil.encode("utf-16-le"))                    # no BOM: refused outright


def test_item9_utf16_camt_without_doctype_still_parses():
    assert len(parsers.parse_camt053(CAMT.replace("UTF-8", "UTF-16").encode("utf-16")).rows) == 3


def test_item10_schedule_summary_reports_replaced(con, tmp_path):
    from coach.ingest.sync import sync_account
    _api_replaces_import(con, tmp_path)
    r = sync_account(con, FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "carte 30/08 SHOP"),
                                                                  eb_tx("r2", "2026-09-02", -1.0, "NEW")]}]}),
                     "acc1", False, False, out=lambda *_: None)
    assert (r["new"], r["replaced"]) == (1, 1)


def test_item11_import_prototype_without_default_source_explains(cfg, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--config", str(cfg.config_path), "db", "import-prototype"])
    msg = str(e.value)
    assert "no longer exists" in msg and "--source" in msg and "db migrate --create" in msg
    assert not cfg.db_path.exists()
