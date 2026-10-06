"""E2 pipeline: normalisation across banks, type rules (loan_payment by account purpose, fees, vault moves),
direction-aware rules, recurring direct debits sharing a key, and nothing person-like reaching the LLM."""
import json
from argparse import Namespace

import pytest

from coach.classify import commands as cc
from coach.classify.candidates import llm_candidates
from coach.classify.normalize import normalize_all
from coach.classify.rules import categorised, load_rules, resolve
from coach.db import connect
from helpers import add_bank

IT_IBAN = "IT60X0542811101000000123456"


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Fortuneo", "FR", [("fo", "FR76A", "M OU MME DURAND PAUL")])
    add_bank(c, "s2", "Caisse d'Epargne X", "FR", [("ce", "FR76B", "CPT COURANT TEST")])
    add_bank(c, "s3", "CIC", "FR", [("cic", "FR76C", "MME J DURAND ET M P DURAND")])
    add_bank(c, "s4", "Revolut", "LT", [("rp", "LT1", "Paul Durand"), ("rl", "LT2", "Lea Durand"),
                                        ("rv", "LT3", "Lea Durand")])
    add_bank(c, "s5", "Banca Test", "IT", [("it", IT_IBAN, "MARIO ROSSI")])
    c.execute("UPDATE accounts SET owner='joint', purpose='main' WHERE uid='ce'")
    c.execute("UPDATE accounts SET owner='joint', purpose='rental' WHERE uid='cic'")
    c.execute("UPDATE accounts SET owner='paul', purpose='kids' WHERE uid='rp'")
    c.execute("UPDATE accounts SET owner='lea', purpose='kids' WHERE uid='rl'")
    c.execute("UPDATE accounts SET owner='lea', purpose='savings', cash_account_type='SVGS' WHERE uid='rv'")
    c.commit()
    return c


def put(con, uid, key, date, amount, desc, code="", cp=""):
    con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, amount, currency, "
                "counterparty, description, bank_tx_code, raw, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,'EUR',?,?,?,'{}','t','t')", (key, uid, key, date, amount, cp, desc, code))


def build(con):
    rows = [
        ("fo", "f1", "2026-03-02", -4.5, "CARTE 01/03 RELAY BEAUVAIS", "", ""),
        ("ce", "c1", "2026-03-05", -57.35, "PRLV Acme Assurances Iard", "B1", ""),
        ("ce", "c2", "2026-04-05", -57.35, "PRLV SEPA ACME ASSURANCES IARD REF 8837 MANDAT 000812 FR12ZZZ123456", "B1", ""),
        ("ce", "c3", "2026-03-06", -2000.0, "ECH PRET   123456E DU 05/03/26", "72", ""),
        ("ce", "c4", "2026-03-07", -4.85, "* PARTICIP FRAIS TENUE COMPTE", "", ""),
        ("ce", "c5", "2026-03-08", 5000.0, "VIR SEPA ACME EMPLOYEUR SAS", "05", ""),
        ("ce", "c6", "2026-03-08", -17.0, "VIR INST VASSEUR T", "C1", ""),
        ("cic", "i1", "2026-03-06", -1570.0, "ECH PRET CAP+IN 17003 211696 02", "", ""),
        ("cic", "i2", "2026-03-09", 612.40, "VIR SAS   NEXITY NORMAND | E2E-0F1E2D3C4B5A69788796A5B4 | PAIEMENT CRG", "", ""),
        ("cic", "i3", "2026-03-10", -2.25, " F TENUE DE COMPTE", "", ""),
        ("cic", "i4", "2026-03-11", -594.36, "PRLV SEPA SYNDICAT RESIDENCE TILLEULS 3 | E2E-6A2F2CAB | APPEL PROVISIONS", "", ""),
        ("rp", "r1", "2026-03-02", -12.0, "Lidl", "CARD_PAYMENT", "Lidl"),
        ("rp", "r2", "2026-03-02", -10.0, "To LEA DURAND", "TRANSFER", "Lea Durand"),
        ("rl", "r3", "2026-03-02", 10.0, "From Paul D", "TRANSFER", "Paul Durand"),
        ("rl", "r4", "2026-03-03", -8.0, "Cinema | To Sam Rivers", "TRANSFER", "Sam Rivers"),
        ("rl", "r5", "2026-03-03", -50.0, "To EUR Holidays", "TRANSFER", ""),
        ("rv", "r6", "2026-03-03", 50.0, "To EUR Holidays", "TRANSFER", ""),
        ("rp", "r7", "2026-03-04", 100.0, "Apple Pay Top-Up by *1234", "TOPUP", ""),
        ("it", "t1", "2026-03-04", -45.0, "PAGAMENTO POS 03/03 ESSELUNGA MILANO", "", ""),
        ("it", "t2", "2026-03-04", -64.2, "ADDEBITO SDD ENEL ENERGIA SPA MANDATO ABC1234567", "", ""),
    ]
    for r in rows:
        put(con, *r)
    con.commit()
    return {r[1]: r for r in rows}


def types(con):
    return dict(con.execute("SELECT tx_key, tx_type FROM tx_enriched"))


def test_normalize_all_banks_and_no_unparsed_left(con, cfg):
    build(con)
    assert normalize_all(con, cfg.memory_dir) == 20
    t = types(con)
    assert t == {"f1": "card", "c1": "direct_debit", "c2": "direct_debit", "c3": "loan_payment", "c4": "bank_fee",
                 "c5": "transfer_in", "c6": "person_transfer_out", "i1": "loan_payment", "i2": "transfer_in",
                 "i3": "bank_fee", "i4": "direct_debit", "r1": "card", "r2": "internal_transfer",
                 "r3": "internal_transfer", "r4": "person_transfer_out", "r5": "savings_internal",
                 "r6": "savings_internal", "r7": "topup", "t1": "card", "t2": "direct_debit"}
    assert "other" not in t.values()
    meta = dict(con.execute("SELECT tx_key, parser FROM tx_parse_meta"))
    assert meta["c1"] == "caisse_epargne" and meta["i1"] == "cic" and meta["r1"] == "revolut" and meta["t1"] == "italian"
    assert con.execute("SELECT mandate_ref, creditor_id FROM tx_parse_meta WHERE tx_key='c2'").fetchone() == ("000812", "FR12ZZZ123456")
    # normalising twice changes nothing
    snap = con.execute("SELECT * FROM tx_enriched ORDER BY tx_key").fetchall()
    normalize_all(con, cfg.memory_dir)
    assert con.execute("SELECT * FROM tx_enriched ORDER BY tx_key").fetchall() == snap


def test_recurring_direct_debits_share_one_merchant_key(con, cfg):
    build(con)
    normalize_all(con, cfg.memory_dir)
    keys = dict(con.execute("SELECT tx_key, merchant_key FROM tx_enriched"))
    assert keys["c1"] == keys["c2"] == "ACME ASSURANCES IARD"
    assert keys["i4"] == "SYNDICAT RESIDENCE TILLEULS"


def cats(con, cfg):
    return {t["tx_key"]: (t["category"], t["source"]) for t in categorised(con, memory_dir=cfg.memory_dir)}


def test_type_rules_loan_by_account_purpose_fees_and_internal_moves(con, cfg):
    build(con)
    normalize_all(con, cfg.memory_dir)
    c = cats(con, cfg)
    assert c["c3"] == ("debt.loan_repayment", "type_rule")                  # a loan of the main account: not a mortgage
    assert c["i1"] == ("housing.rental_property_loan", "type_rule")         # the account of the rental flat
    assert c["c4"] == ("fees.bank_fees", "type_rule") and c["i3"] == ("fees.bank_fees", "type_rule")
    for k in ("r2", "r3", "r5", "r6", "r7"):
        assert c[k] == ("transfer.internal", "type_rule"), k
    assert c["c6"][0] == "transfer.to_people" and c["r4"][0] == "transfer.to_people"
    assert c["i4"] == ("housing.property_charges", "rule")                  # syndic rule
    # rent: incoming money from a property manager (direction-aware rule); the same name going out is not rent
    assert c["i2"] == ("income.rental", "rule")
    r = load_rules()
    from coach.classify.rules import rule_category
    assert rule_category("NEXITY NORMAND", r, "in") == "income.rental"
    assert rule_category("NEXITY NORMAND", r, "out") is None
    assert rule_category("NEXITY NORMAND", r) is None
    # a memory annotation still wins over the type rule
    ann = [{"id": "m", "match": {"description": "ECH PRET   123456E"}, "category": "housing.mortgage"}]
    t = next(x for x in categorised(con, annotations=ann) if x["tx_key"] == "c3")
    assert (t["category"], t["source"]) == ("housing.mortgage", "memory")


def test_user_override_and_merchant_label_beat_type_rules(con, cfg):
    build(con)
    normalize_all(con, cfg.memory_dir)
    con.execute("INSERT INTO tx_overrides VALUES ('c3','housing.mortgage','mine')")
    assert resolve(con, "c3", "loan_payment", "ECH PRET", purpose="main") == ("housing.mortgage", "override")
    assert resolve(con, "zz", "loan_payment", "ECH PRET", purpose="rental")[0] == "housing.rental_property_loan"
    assert resolve(con, "zz", "loan_payment", "ECH PRET", purpose=None)[0] == "debt.loan_repayment"


def test_no_person_name_is_ever_an_llm_candidate(con, cfg):
    build(con)
    normalize_all(con, cfg.memory_dir)
    cands, withheld = llm_candidates(con)
    keys = {c["key"] for c in cands}
    # companies and merchants are candidates...
    assert {"ACME ASSURANCES IARD", "LIDL", "RELAY BEAUVAIS", "ACME EMPLOYEUR SAS", "ENEL ENERGIA SPA"} <= keys
    # ...person-to-person transfers, own-account moves, fees and loans are not
    joined = " ".join(keys)
    for forbidden in ("VASSEUR", "SAM RIVERS", "DURAND", "LEA", "PAUL", "ECH PRET", "TENUE", "TOP UP", "HOLIDAYS"):
        assert forbidden not in joined, forbidden


def test_cmd_normalize_prints_and_warns_about_remapped_memory_keys(con, cfg, capsys):
    build(con)
    con.execute("INSERT INTO tx_key_remap VALUES ('old-key', 'f1', 'test', 't')")
    con.commit()
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: pinned\n    match: {tx_keys: [old-key]}\n    category: food.groceries\n")
    assert cc.cmd_normalize(Namespace(insecure=True), cfg) == 20
    out = capsys.readouterr().out
    assert "direct_debit" in out and "warning" in out and "pinned" in out


def test_vault_moves_pair_automatically_in_the_transfer_matcher(con, cfg):
    from coach.transfers import find_pairs
    build(con)
    normalize_all(con, cfg.memory_dir)
    res = find_pairs(con)
    pairs = {(p["debit"].tx_key, p["credit"].tx_key): p for p in res.linked}
    assert ("r5", "r6") in pairs and pairs[("r5", "r6")]["strong"] and pairs[("r5", "r6")]["confidence"] >= 0.95
    # the parent -> child transfer (internal on both legs) as well
    assert ("r2", "r3") in pairs
