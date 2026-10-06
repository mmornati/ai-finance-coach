"""E2-2 per-bank parsers + H2 (holder sets) + H3 (VIR prefixes). Synthetic descriptors only: invented names,
creditors, amounts and references that mimic the formats of the real banks."""
import pytest

from coach.classify.parsers import PARSERS, parse_tx, parser_name_for
from coach.classify.parsers.common import (
    Household, PARSED_KEYS, RawTx, holder_sets, household, looks_like_person, owner_tokens, strip_vir_prefix)
from helpers import add_bank

HOUSE = Household(holders=[{"DURAND", "PAUL"}, {"DURAND", "LEA"}], family={"DURAND", "PAUL", "LEA", "LUCIE"},
                  first_names={"PAUL", "LEA", "LUCIE"}, own_banks={"BANQUEA": "BanqueA", "CREDITB": "CreditB"})


def P(bank, desc, amount=-10.0, bdate="2026-03-15", **kw):
    return parse_tx(RawTx(desc, amount, bdate, **kw), HOUSE, bank=bank)


# ---------------------------------------------------------------- structure + registry

def test_every_parser_returns_the_same_keys():
    for bank, desc in [("Fortuneo", "CARTE 02/10 RELAY BEAUVAIS"), ("Caisse d'Epargne X", "PRLV ACME ENERGIE"),
                       ("CIC", "F TENUE DE COMPTE"), ("Revolut", "Lidl"), ("Intesa", "PAGAMENTO POS 03/10 BAR ROMA"),
                       ("Unknown Bank", "SOMETHING ELSE")]:
        r = P(bank, desc, bdate="2026-10-04")
        assert set(PARSED_KEYS) <= set(r), bank
        assert r["merchant_key"] == r["merchant_key"].upper()


def test_registry_picks_the_parser_by_bank_then_iban_country():
    assert parser_name_for("Fortuneo") == "fortuneo"
    assert parser_name_for("Caisse d'Epargne Normandie") == "caisse_epargne"
    assert parser_name_for("CIC") == "cic"
    assert parser_name_for("Revolut") == "revolut"
    assert parser_name_for("Banca Test", iban="IT60X0542811101000000123456") == "italian"
    assert parser_name_for("Banca Test", country="IT") == "italian"
    assert parser_name_for("Some Bank", iban="FR76") == "generic"
    assert parser_name_for(None) == "generic"
    assert set(PARSERS) >= {"fortuneo", "caisse_epargne", "cic", "revolut", "italian", "generic"}


def test_bank_parser_hands_unknown_lines_to_the_generic_one():
    from coach.classify import parsers
    parsers.register("toybank", lambda tx, h: {**parsers.french.result("other", tx.description)},
                     r"\btoybank\b")
    try:
        r = P("ToyBank", "PRLV SEPA ACME ENERGIE")
        assert r["tx_type"] == "direct_debit" and r["parser"] == "toybank"   # recognised by the generic parser
        assert P("ToyBank", "NOTHING KNOWN HERE")["tx_type"] == "other"
    finally:
        parsers.PARSERS.pop("toybank")
        parsers.BANK_PATTERNS.pop()
    assert P("Fortuneo", "PRIME BIENVENUE PARRAINEUR")["tx_type"] == "other"


# ---------------------------------------------------------------- Fortuneo (regression of the E2-1 behaviour)

def test_fortuneo_card_with_fx_and_date_rollover():
    r = P("Fortuneo", "CARTE 05/10 AIRBNB PARIS 95,00 USD COURS 1,09", -87.1, "2025-10-07")
    assert (r["tx_type"], r["op_date"], r["merchant_raw"], r["merchant_key"]) == ("card", "2025-10-05", "AIRBNB PARIS", "AIRBNB PARIS")
    assert (r["fx_amount"], r["fx_currency"]) == (95.0, "USD")
    assert P("Fortuneo", "CARTE 30/12 BOULANGERIE TEST", bdate="2026-01-02")["op_date"] == "2025-12-30"


def test_fortuneo_other_shapes():
    assert P("Fortuneo", "ANN CARTE SPORTMAX QUIMPER", 30)["tx_type"] == "card_refund"
    r = P("Fortuneo", "RET DAB 12345 BANQUE TEST BORDEAUX", -60)
    assert (r["tx_type"], r["merchant_raw"]) == ("atm", "BANQUE TEST BORDEAUX")
    assert P("Fortuneo", "VIR INST WERO Jean MARTIN", -25)["tx_type"] == "wero_out"
    r = P("Fortuneo", "VIR INST M     PAUL MARTIN", -5)
    assert (r["tx_type"], r["merchant_raw"]) == ("person_transfer_out", "M PAUL MARTIN")
    assert P("Fortuneo", "VIR MR OU MME DURAND PAUL  VIREMENT DE MR OU MME DURAND PAUL  2518229445410506",
             2500)["tx_type"] == "internal_transfer"
    assert P("Fortuneo", "VIR Virement avec Fortuneo Livret A  Virement avec Fortuneo Livret", -50)["tx_type"] == "internal_transfer"


def test_fortuneo_keeps_untitled_names_as_company_transfers_legacy_behaviour():
    # the original parser only recognised titled names / the family surname; this stays byte-identical
    assert P("Fortuneo", "VIR INST Paylib Thibault RENVOISE", -210)["tx_type"] == "transfer_out"


# ---------------------------------------------------------------- H2: surname-only holder sets

def test_h2_surname_only_holder_is_never_used_for_own_account_detection(con_with_cic):
    con = con_with_cic
    assert holder_sets(con) == [{"DURAND", "PAUL"}] or all(len(h) >= 2 for h in holder_sets(con))
    assert {"VENDRAME"} not in holder_sets(con)
    assert "VENDRAME" in owner_tokens(con)          # still known for family detection
    h = household(con)
    assert all(len(x) >= 2 for x in h.holders)
    inv = parse_tx(RawTx("VIR FAC20310042 VENDRAME", -10000.0, "2025-01-17"), h, bank="Fortuneo")
    assert inv["tx_type"] == "transfer_out"       # a contractor invoice that happens to carry the surname
    inv2 = parse_tx(RawTx("VIR SEPA FAC20310042 VENDRAME", -10000.0, "2025-01-17"), h, bank="Caisse d'Epargne X")
    assert inv2["tx_type"] == "transfer_out"
    # a real family member (name tokens, no reference) is still a person transfer, never internal
    fam = parse_tx(RawTx("VIR MME CLARA VENDRAME", -50.0, "2025-01-17"), h, bank="Fortuneo")
    assert fam["tx_type"] == "person_transfer_out"


@pytest.fixture
def con_with_cic(cfg):
    from coach.db import connect
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Fortuneo", "FR", [("fo", "FR76A", "M OU MME VENDRAME PAUL")])
    add_bank(con, "s2", "CIC", "FR", [("cic", "FR76B", "MME J VENDRAME ET M P VENDRAME")])
    return con


def test_h2_owner_member_id_is_completed_by_the_account_name(cfg):
    from coach.db import connect
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s", "Revolut", "LT", [("k1", "LT1", "Zoe VENDRAME")])
    con.execute("UPDATE accounts SET owner='zoe'")
    assert holder_sets(con) == [{"ZOE", "VENDRAME"}]
    # household members from memory/household.yaml add holders and given names
    (cfg.memory_dir / "household.yaml").write_text("members:\n  - {id: leo, name: Leo VENDRAME}\n")
    h = household(con, cfg.memory_dir)
    assert {"LEO", "VENDRAME"} in h.holders and "LEO" in h.first_names


# ---------------------------------------------------------------- H3: leftover prefixes

@pytest.mark.parametrize("text,expected", [
    ("VIR SEPA M DURAND PAUL", "M DURAND PAUL"),
    ("VIR INST M DURAND PAUL", "M DURAND PAUL"),
    ("VIREMENT SEPA SPORTMAX", "SPORTMAX"),
    ("VIR SEPA RECU SPORTMAX", "SPORTMAX"),
    ("VIR INST WERO Jean MARTIN", "Jean MARTIN"),
    ("VIREMENT EMIS ACME SARL", "ACME SARL"),
    ("VIREMENT RECU ACME SARL", "ACME SARL"),
    ("VIR ACME", "ACME"),
])
def test_h3_vir_prefix_stripping(text, expected):
    assert strip_vir_prefix(text) == expected


def test_h3_no_leftover_sepa_in_the_counterparty_of_ce_transfers():
    r = P("Caisse d'Epargne Normandie", "VIR SEPA M DURAND PAUL", -500)
    assert r["tx_type"] == "internal_transfer" and not r["merchant_raw"].startswith("SEPA")
    r = P("Caisse d'Epargne Normandie", "VIR SEPA RECU ACME SERVICES", 500)
    assert (r["tx_type"], r["merchant_raw"]) == ("transfer_in", "ACME SERVICES")


# ---------------------------------------------------------------- Caisse d'Epargne

CE = "Caisse d'Epargne Normandie"


def test_ce_direct_debits_share_one_key_whatever_the_references():
    a = P(CE, "PRLV ACME ASSURANCES IARD")
    b = P(CE, "PRLV ACME ASSURANCES IARD  REF 83747 MANDAT 000812 FR12ZZZ123456")
    c = P(CE, "PRLV SEPA Acme Assurances Iard ICS FR12ZZZ123456 RUM MD99887766 12/03")
    assert a["tx_type"] == b["tx_type"] == c["tx_type"] == "direct_debit"
    assert a["merchant_key"] == b["merchant_key"] == c["merchant_key"] == "ACME ASSURANCES IARD"
    assert c["creditor_id"] == "FR12ZZZ123456" and c["mandate_ref"] == "MD99887766"
    assert a["creditor_id"] is None


def test_ce_loan_instalment_fees_and_cheques():
    r = P(CE, "ECH PRET   123456E DU 05/09/26", -1500)
    assert (r["tx_type"], r["merchant_key"], r["reference"], r["op_date"]) == ("loan_payment", "ECH PRET", "123456E", "2026-09-05")
    assert P(CE, "* PARTICIP FRAIS TENUE COMPTE", -4.85)["tx_type"] == "bank_fee"
    assert P(CE, "CHEQUE N°0000123", -20)["tx_type"] == "cheque"
    assert P(CE, "REMISE CHEQUES N° 0004567", 120)["tx_type"] == "cheque_deposit"


def test_ce_card_and_atm_lines_if_the_account_has_a_card():
    r = P(CE, "PAIEMENT CB 1203 PARIS MONOPRIX", -12.5, "2026-03-14")
    assert (r["tx_type"], r["op_date"], r["merchant_raw"]) == ("card", "2026-03-12", "PARIS MONOPRIX")
    assert P(CE, "RETRAIT DAB 12/03 QUIMPER", -40)["tx_type"] == "atm"


def test_ce_transfers_person_company_and_own_bank():
    assert P(CE, "VIR SEPA ACME ENERGIE", 100)["tx_type"] == "transfer_in"
    assert P(CE, "VIR INST VASSEUR T", -17)["tx_type"] == "person_transfer_out"        # name + initial
    assert P(CE, "VIR INST M. VANDERLEY OLIVIER", 20)["tx_type"] == "person_transfer_in"
    r = P(CE, "VIR INST CREDITB", -1000)               # the user's own account at another connected bank
    assert r["tx_type"] == "internal_transfer"
    r = P(CE, "VIR SEPA CREDITB PAUL ET LUCIE", -900)
    assert r["tx_type"] == "internal_transfer"
    assert P(CE, "VIR SEPA CREDITB CONSEIL SARL", -900)["tx_type"] != "internal_transfer"


# ---------------------------------------------------------------- CIC

def test_cic_loan_fee_rent_and_direct_debit():
    r = P("CIC", "ECH PRET CAP+IN 17003 211696 02", -1500)
    assert (r["tx_type"], r["merchant_raw"], r["reference"]) == ("loan_payment", "ECH PRET CAP+IN", "17003 211696 02")
    assert P("CIC", " F TENUE DE COMPTE", -2.25)["tx_type"] == "bank_fee"
    rent = P("CIC", "VIR SAS   NEXITY NORMAND | E2E-0F1E2D3C4B5A69788796A5B4 | PAIEMENT CRG", 612.40)
    assert (rent["tx_type"], rent["merchant_raw"]) == ("transfer_in", "SAS NEXITY NORMAND")
    assert rent["reference"].startswith("E2E-")
    dd = P("CIC", "PRLV SEPA SYNDICAT RESIDENCE LES TILLEULS 3 | E2E-6A2F2CABCC9C68C1390F07FC | APPEL PROVISIONS 01 07 | 2026 2 4", -594.36)
    assert dd["tx_type"] == "direct_debit" and dd["merchant_key"] == "SYNDICAT RESIDENCE LES TILLEULS"
    own = P("CIC", "VIR MR OU MME DURAND PAUL | VIREMENT VERS CIC PAUL ET LUCIE", 900)
    assert own["tx_type"] == "internal_transfer"


# ---------------------------------------------------------------- Revolut

def test_revolut_card_refund_fx_and_fee():
    r = P("Revolut", "Lidl", -12.3, bank_tx_code="CARD_PAYMENT", account_type="CACC")
    assert (r["tx_type"], r["merchant_key"]) == ("card", "LIDL")
    r = P("Revolut", "Xsolla _game", -5.88, bank_tx_code="CARD_PAYMENT",
          raw={"exchange_rate": {"instructed_amount": {"currency": "USD", "amount": "6.40"}}}, currency="EUR")
    assert (r["fx_amount"], r["fx_currency"]) == (6.4, "USD")
    assert P("Revolut", "Shop X", 9.0, bank_tx_code="REFUND")["tx_type"] == "card_refund"
    assert P("Revolut", "Plus plan fee", -3.99, bank_tx_code="FEE")["tx_type"] == "bank_fee"
    assert P("Revolut", "Exchanged to USD", -50, bank_tx_code="EXCHANGE")["tx_type"] == "fx_exchange"


def test_revolut_topups_and_vault_moves_are_internal_types():
    assert P("Revolut", "Apple Pay Top-Up by *1234", 100, bank_tx_code="TOPUP")["tx_type"] == "topup"
    assert P("Revolut", "VIREMENT INSTANTANE | Payment from Mr Ou Mme Durand Paul", 500,
             bank_tx_code="TOPUP", counterparty="MR OU MME DURAND PAUL")["tx_type"] == "topup"
    r = P("Revolut", "To EUR Holidays", -50, bank_tx_code="TRANSFER", account_type="CACC")
    assert (r["tx_type"], r["merchant_key"]) == ("savings_internal", "TO EUR HOLIDAYS")
    r = P("Revolut", "To EUR 72bb4f4f-edab-4eb9-b538-6fe598a04f53", 20, bank_tx_code="TRANSFER", account_type="SVGS")
    assert r["tx_type"] == "savings_internal" and "72BB" not in r["merchant_key"]


def test_revolut_transfers_between_household_members_vs_friends():
    own = P("Revolut", "To LEA DURAND", -10, bank_tx_code="TRANSFER", counterparty="Lea Durand")
    assert own["tx_type"] == "internal_transfer"
    truncated = P("Revolut", "From Paul D", 49, bank_tx_code="TRANSFER", counterparty="Paul Durand")
    assert truncated["tx_type"] == "internal_transfer"          # the counterparty field has the full name
    friend = P("Revolut", "Cinema | To Sam Rivers", -12, bank_tx_code="TRANSFER", counterparty="Sam Rivers")
    assert friend["tx_type"] == "person_transfer_out" and friend["merchant_raw"] == "Sam Rivers"
    back = P("Revolut", "Thanks | From Sam Rivers", 12, bank_tx_code="TRANSFER", counterparty="Sam Rivers")
    assert back["tx_type"] == "person_transfer_in"
    junior = P("Revolut", "Phone | Junior account withdrawal (Lea Durand)", 9.29, bank_tx_code="TRANSFER",
               counterparty="Lea Durand")
    assert junior["tx_type"] == "internal_transfer"
    # the free-text note between people is not kept
    assert friend["reference"] is None


# ---------------------------------------------------------------- Italian (synthetic: no Italian bank connected yet)

IT = dict(bank="Banca Test", iban="IT60X0542811101000000123456")


@pytest.mark.parametrize("desc,amount,tx_type,key", [
    ("PAGAMENTO POS 03/10 ESSELUNGA MILANO", -45.2, "card", "ESSELUNGA MILANO"),
    ("PAGAMENTO POS 03/10/2026 BAR ROMA CARTA ****1234", -2.5, "card", "BAR ROMA"),
    ("POS CONAD VIA ROMA", -20.0, "card", "CONAD VIA ROMA"),
    ("PAGAMENTO CARTA FARMACIA CENTRALE", -12.0, "card", "FARMACIA CENTRALE"),
    ("PRELIEVO BANCOMAT 05/10 ATM MILANO", -100.0, "atm", "ATM MILANO"),
    ("BONIFICO A FAVORE DI IDRAULICA ROSSI SRL", -300.0, "transfer_out", "IDRAULICA ROSSI SRL"),
    ("BONIFICO A FAVORE DI SIG. MARIO BIANCHI CAUSALE REGALO", -50.0, "person_transfer_out", "MARIO BIANCHI"),
    ("BONIFICO DA ACME SPA STIPENDIO OTTOBRE", 2000.0, "transfer_in", "ACME SPA STIPENDIO OTTOBRE"),
    ("BONIFICO ISTANTANEO DA LUCA VERDI", 80.0, "person_transfer_in", "LUCA VERDI"),
    ("COMMISSIONI BONIFICO", -1.0, "bank_fee", "COMMISSIONI BONIFICO"),
    ("IMPOSTA DI BOLLO", -34.2, "bank_fee", "IMPOSTA DI BOLLO"),
    ("RATA MUTUO 10/26", -650.0, "loan_payment", "RATA MUTUO"),
])
def test_italian_parser(desc, amount, tx_type, key):
    r = parse_tx(RawTx(desc, amount, "2026-10-05"), HOUSE, **IT)
    assert r["tx_type"] == tx_type, r
    assert r["merchant_key"] == key, r
    assert r["parser"] == "italian"


def test_italian_direct_debit_mandate_and_creditor_id():
    r = parse_tx(RawTx("ADDEBITO SDD ENEL ENERGIA SPA MANDATO ABC1234567 CID IT12ZZZ0000012345678 RIF 998877",
                       -64.2, "2026-10-05"), HOUSE, **IT)
    assert r["tx_type"] == "direct_debit" and r["merchant_key"] == "ENEL ENERGIA SPA"
    assert r["mandate_ref"] == "ABC1234567" and r["creditor_id"] == "IT12ZZZ0000012345678"
    r2 = parse_tx(RawTx("ADDEBITO DIRETTO ENEL ENERGIA SPA MANDATO ZZZ9988776 12/10", -70.1, "2026-10-12"), HOUSE, **IT)
    assert r2["merchant_key"] == r["merchant_key"]       # recurring debits of one creditor share a key


# ---------------------------------------------------------------- person heuristic

@pytest.mark.parametrize("text,person", [
    ("VASSEUR T", True), ("M. VANDERLEY OLIVIER", True), ("Dr HALBERT CLAIRE", True), ("Philippe HOLLANDER", True),
    ("ORMEKO SRL", False), ("CAF DE LA MANCHE", False), ("SPORTMAX", False), ("CLUB VOILE SAS", False),
    ("LA CAVE D ANTOINE", False), ("SARL TARQUIN", False), ("FAC20310042 VENDRAME", False), ("ACME ENERGIE", False),
])
def test_looks_like_person(text, person):
    assert looks_like_person(text) is person
