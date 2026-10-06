import pytest

from coach.classify.parser import merchant_key, parse

OWNER = {"ALICE", "TESTOWNER"}


def p(desc, amount=-10.0, bdate="2025-10-03", owner=OWNER):
    return parse(desc, amount, bdate, owner)


def test_card_payment():
    r = p("CARTE 02/10 RELAY BEAUVAIS")
    assert r["tx_type"] == "card"
    assert r["op_date"] == "2025-10-02"
    assert r["merchant_raw"] == "RELAY BEAUVAIS"
    assert r["merchant_key"] == "RELAY BEAUVAIS"
    assert r["fx_amount"] is None


def test_card_date_rolls_back_a_year_across_new_year():
    r = p("CARTE 30/12 BOULANGERIE TEST", bdate="2026-01-02")
    assert r["op_date"] == "2025-12-30"


def test_card_fx_line():
    r = p("CARTE 05/10 AIRBNB PARIS 95,00 USD COURS 1,09", bdate="2025-10-07")
    assert r["tx_type"] == "card"
    assert (r["fx_amount"], r["fx_currency"]) == (95.0, "USD")
    assert r["merchant_raw"] == "AIRBNB PARIS"


def test_card_refund():
    r = p("ANN CARTE SPORTMAX QUIMPER", amount=30.0)
    assert r["tx_type"] == "card_refund"
    assert r["op_date"] is None
    assert r["merchant_key"] == "SPORTMAX QUIMPER"


def test_atm_withdrawal():
    r = p("RET DAB 12345 BANQUE TEST BORDEAUX", amount=-60)
    assert r["tx_type"] == "atm"
    assert r["merchant_raw"] == "BANQUE TEST BORDEAUX"


@pytest.mark.parametrize("amount,expected", [(-25.0, "wero_out"), (25.0, "wero_in")])
def test_wero_transfers(amount, expected):
    r = p("VIR INST WERO Jean MARTIN", amount=amount)
    assert r["tx_type"] == expected
    assert r["merchant_raw"] == "Jean MARTIN"


def test_internal_transfer_by_owner_name_and_by_livret_keyword():
    assert p("VIR ALICE TESTOWNER")["tx_type"] == "internal_transfer"
    assert p("VIR Virement avec Livret A")["tx_type"] == "internal_transfer"


def test_titled_person_transfer():
    assert p("VIR MME LUCIE MARTIN", amount=-50)["tx_type"] == "person_transfer_out"
    assert p("VIR MR PAUL MARTIN", amount=50)["tx_type"] == "person_transfer_in"


def test_split_title_and_name_on_two_segments():
    r = p("VIR INST M     PAUL MARTIN", amount=-5)
    assert r["tx_type"] == "person_transfer_out"
    assert r["merchant_raw"] == "M PAUL MARTIN"


def test_company_transfer():
    r = p("VIR SEPA EMPLOYEUR SA SALAIRE", amount=1500)
    assert r["tx_type"] == "transfer_in"
    r = p("VIR SEPA IMPOTS", amount=-100)
    assert r["tx_type"] == "transfer_out"


def test_unknown_descriptor_is_other():
    r = p("PRIME BIENVENUE PARRAINEUR")
    assert r["tx_type"] == "other"
    assert r["merchant_raw"] == "PRIME BIENVENUE PARRAINEUR"
    # direct debits are now understood by the generic parser (E2-2)
    assert p("PRLV SEPA EDF CLIENTS")["tx_type"] == "direct_debit"


@pytest.mark.parametrize("raw,expected", [
    ("MOL*BOULANGERIE PAUL 33700 BEAUVAIS", "BOULANGERIE PAUL BEAUVAIS"),
    ("SUMUP *CAFE DU PORT", "SUMUP CAFE DU PORT"),
    ("PAYPAL*SPOTIFY", "SPOTIFY"),
    ("SQ*COFFEE SHOP 12", "COFFEE SHOP"),
    ("MOL*123456", ""),               # processor prefix + only digits: nothing left
    ("AMAZON*2X4 PARIS", "AMAZON PARIS"),  # non-processor prefix keeps the left side
    ("L'ATELIER 33 BORDEAUX", "L'ATELIER BORDEAUX"),
    ("a b", ""),                      # single letters dropped
])
def test_merchant_key_normalisation(raw, expected):
    assert merchant_key(raw) == expected
