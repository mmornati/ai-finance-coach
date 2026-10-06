import dataclasses

import pytest

from coach import transfers as tm
from coach.analytics.report import build_report
from coach.classify.rules import categorised, resolve
from coach.cli import main
from coach.db import connect
from helpers import add_bank, add_tx as _add_tx


def add_tx(con, uid, key, date, amount, desc, tx_type="internal_transfer"):
    """Rows the parser recognised as own-account transfers unless a test says otherwise."""
    _add_tx(con, uid, key, date, amount, desc, tx_type)

RULES = {"type_rules": {"internal_transfer": "transfer.internal", "person_transfer_out": "transfer.to_people",
                        "atm": "cash.atm_withdrawal"}, "merchant_rules": []}


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Main Bank", "FR", [("ce", "FR7600000000000000000000001", "CE")])
    add_bank(c, "s2", "Card Bank", "FR", [("fo", "FR7600000000000000000000002", "FO")])
    add_bank(c, "s3", "Rental Bank", "FR", [("cic", "FR7600000000000000000000003", "CIC")])
    return c


def links(con):
    return con.execute("SELECT out_tx_key, in_tx_key, method, confidence FROM transfer_links ORDER BY id").fetchall()


def test_exact_pair_is_linked_with_confidence(con):
    add_tx(con, "ce", "o1", "2026-09-10", -500.0, "VIR SEPA VERS FORTUNEO")
    add_tx(con, "fo", "i1", "2026-09-10", 500.0, "VIR RECU CE")
    res = tm.match_transfers(con)
    assert len(res.linked) == 1 and not res.ambiguous
    (o, i, method, conf), = links(con)
    assert (o, i, method) == ("o1", "i1", "auto") and conf >= 0.95
    assert con.execute("SELECT amount FROM transfer_links").fetchone()[0] == 500.0


@pytest.mark.parametrize("gap,linked", [(0, True), (1, True), (3, True), (-3, True), (4, False), (-4, False)])
def test_window_is_plus_minus_three_days(con, gap, linked):
    add_tx(con, "ce", "o1", "2026-09-10", -80.0, "X")
    d = {0: "2026-09-10", 1: "2026-09-11", 3: "2026-09-13", -3: "2026-09-07", 4: "2026-09-14", -4: "2026-09-06"}[gap]
    add_tx(con, "fo", "i1", d, 80.0, "Y")
    assert len(tm.match_transfers(con).linked) == int(linked)


def test_configurable_window_and_confidence_drops_with_distance(con):
    add_tx(con, "ce", "o1", "2026-09-10", -80.0, "X")
    add_tx(con, "fo", "i1", "2026-09-15", 80.0, "Y")
    assert tm.match_transfers(con, window_days=3, dry_run=True).linked == []
    assert len(tm.find_pairs(con, window_days=5).proposals) == 1             # default threshold 0.85: only proposed
    res = tm.match_transfers(con, window_days=5, min_confidence=0.8)
    assert len(res.linked) == 1
    near = tm.confidence(res.linked[0]["debit"], res.linked[0]["credit"], 0, True)
    assert res.linked[0]["confidence"] < near


def test_same_account_currency_and_amount_must_match(con):
    add_tx(con, "ce", "o1", "2026-09-10", -80.0, "X")
    add_tx(con, "ce", "i_same", "2026-09-10", 80.0, "same account refund")
    add_tx(con, "fo", "i_other", "2026-09-10", 80.01, "off by a cent")
    assert tm.match_transfers(con).linked == []
    con.execute("INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description) "
                "VALUES ('i_usd','fo','2026-09-10',80.0,'USD','dollars')")
    con.commit()
    assert tm.match_transfers(con).linked == []


def test_card_and_atm_legs_are_never_candidates(con):
    add_tx(con, "ce", "o1", "2026-09-10", -25.0, "CARTE 09/09 SHOP", "card")
    add_tx(con, "fo", "i1", "2026-09-10", 25.0, "ANN CARTE SHOP", "card_refund")
    add_tx(con, "ce", "o2", "2026-09-11", -60.0, "RET DAB", "atm")
    add_tx(con, "fo", "i2", "2026-09-11", 60.0, "VERSEMENT")
    assert tm.match_transfers(con).linked == []


def test_ambiguous_candidates_are_not_auto_linked_and_are_listed(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR B")
    add_tx(con, "cic", "i2", "2026-09-11", 100.0, "VIR C")                 # two candidate credits
    res = tm.match_transfers(con)
    assert res.linked == [] and links(con) == []
    assert len(res.ambiguous) == 1 and {c.tx_key for c in res.ambiguous[0]["candidates"]} == {"i1", "i2"}
    text = tm.format_ambiguous(res)
    assert "o1" in text and "i1" in text and "i2" in text and "coach transfers link" in text


def test_two_debits_competing_for_one_credit_are_ambiguous(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A")
    add_tx(con, "cic", "o2", "2026-09-10", -100.0, "VIR B")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR C")
    res = tm.match_transfers(con)
    assert res.linked == [] and len(res.ambiguous) == 2


def test_ambiguity_resolved_manually_unblocks_the_rest(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR B")
    add_tx(con, "cic", "i2", "2026-09-11", 100.0, "VIR C")
    tm.match_transfers(con)
    tm.link_transfer(con, "o1", "i2")                                       # the user knows it went to the rental flat
    assert links(con) == [("o1", "i2", "manual", 1.0)]
    res = tm.match_transfers(con)                                           # i1 alone: nothing left to pair it with
    assert res.linked == [] and res.ambiguous == []


def test_already_matched_legs_are_not_rematched(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR B")
    tm.match_transfers(con)
    add_tx(con, "cic", "i2", "2026-09-10", 100.0, "VIR C")                  # a later look-alike credit
    res = tm.match_transfers(con)
    assert res.linked == [] and len(links(con)) == 1
    assert tm.match_transfers(con).linked == []                              # idempotent


def test_manual_link_validation_and_unlink_with_rejection(con):
    add_tx(con, "ce", "out-key-123456", "2026-09-10", -100.0, "A")
    add_tx(con, "fo", "in-key-654321", "2026-09-20", 100.0, "B")             # 10 days apart: manual only
    add_tx(con, "ce", "same-acct", "2026-09-10", 100.0, "C")
    add_tx(con, "fo", "wrong-amount", "2026-09-10", 99.0, "D")
    with pytest.raises(tm.TransferError, match="same account"):
        tm.link_transfer(con, "out-key-123456", "same-acct")
    with pytest.raises(tm.TransferError, match="amounts differ"):
        tm.link_transfer(con, "out-key-123456", "wrong-amount")
    with pytest.raises(tm.TransferError, match="first transaction must be the debit"):
        tm.link_transfer(con, "in-key-654321", "out-key-123456")
    with pytest.raises(tm.TransferError, match="No transaction"):
        tm.link_transfer(con, "nope", "in-key-654321")
    lid = tm.link_transfer(con, "123456", "654321")                          # unique fragments are enough
    assert links(con) == [("out-key-123456", "in-key-654321", "manual", 1.0)]
    with pytest.raises(tm.TransferError, match="already part of link"):
        tm.link_transfer(con, "out-key-123456", "in-key-654321")
    r = tm.unlink_transfer(con, str(lid))
    assert r["out"] == "out-key-123456" and links(con) == []
    with pytest.raises(tm.TransferError):
        tm.unlink_transfer(con, "999")
    # unlinked pairs are not proposed again by the matcher even when inside the window
    add_tx(con, "ce", "o9", "2026-09-10", -7.0, "A")
    add_tx(con, "fo", "i9", "2026-09-10", 7.0, "B")
    tm.match_transfers(con)
    assert len(links(con)) == 1
    tm.unlink_transfer(con, "i9")                                            # by either leg's key
    assert links(con) == [] and tm.match_transfers(con).linked == []
    tm.link_transfer(con, "o9", "i9")                                        # a manual link overrides the rejection
    assert links(con)[0][2] == "manual"


def test_manual_link_allows_amount_mismatch_when_asked(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "A")
    add_tx(con, "fo", "i1", "2026-09-10", 98.5, "B")
    tm.link_transfer(con, "o1", "i1", allow_amount_mismatch=True)
    assert links(con)[0][:3] == ("o1", "i1", "manual")


# ---- classification effect

def seed_report_data(con):
    """six months of spending on account ce, plus a salary and internal transfers between ce and fo."""
    for m in range(1, 7):
        add_tx(con, "ce", f"sh{m}", f"2026-{m:02d}-05", -200.0, "CARTE SHOP", "card")
        add_tx(con, "ce", f"sal{m}", f"2026-{m:02d}-01", 3000.0, "VIR SALAIRE", "transfer_in")
    con.execute("INSERT INTO merchants VALUES ('CARTE SHOP','Shop','food.groceries',0.9,0,'user',NULL,'t')")
    con.execute("INSERT INTO merchants VALUES ('VIR SALAIRE','Salaire','income.salary',0.9,0,'user',NULL,'t')")
    con.commit()


def test_matched_legs_become_transfer_internal_and_leave_spending(con):
    seed_report_data(con)
    for m in range(1, 7):
        # money to the card account: the debit looks like a person transfer to the type rules
        add_tx(con, "ce", f"o{m}", f"2026-{m:02d}-10", -450.0, "VIR M DUPONT FR7600000000000000000000002",
               "person_transfer_out")
        add_tx(con, "fo", f"i{m}", f"2026-{m:02d}-11", 450.0, "VIR RECU", "transfer_in")
    before = {t["tx_key"]: (t["category"], t["source"]) for t in categorised(con, rules=RULES, annotations=[])}
    assert before["o1"] == ("transfer.to_people", "type_rule")
    avg_before = build_report(con, rules=RULES, annotations=[])["averages"]
    tm.match_transfers(con)
    after = {t["tx_key"]: (t["category"], t["source"]) for t in categorised(con, rules=RULES, annotations=[])}
    assert all(after[f"o{m}"] == ("transfer.internal", "transfer_link") == after[f"i{m}"] for m in range(1, 7))
    assert after["sh1"] == before["sh1"] and after["sal1"] == before["sal1"]            # nothing else moves
    avg_after = build_report(con, rules=RULES, annotations=[])["averages"]
    # Before: the card account's incoming leg (uncategorised credit) polluted "spending" (+450/month).
    # After: both legs are transfer.internal and out of spending/income; real spending is just the shop.
    assert avg_before["avg_monthly"] == pytest.approx(-200.0 + 450.0)
    assert avg_after["avg_monthly"] == pytest.approx(-200.0)
    assert dict((c, v) for c, v, _ in avg_after["by_category"]) == {"food.groceries": pytest.approx(-200.0)}


def test_precedence_override_beats_link_and_link_beats_type_rules(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "RET DAB", "atm")           # a type rule would say cash
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VERSEMENT ESPECES", "other")
    tm.link_transfer(con, "o1", "i1")
    assert resolve(con, "o1", "atm", "RET DAB", RULES) == ("transfer.internal", "transfer_link")
    con.execute("INSERT INTO tx_overrides VALUES ('o1','health.doctors','user wins')")
    assert resolve(con, "o1", "atm", "RET DAB", RULES) == ("health.doctors", "override")
    assert resolve(con, "i1", "other", "X", RULES) == ("transfer.internal", "transfer_link")
    assert resolve(con, "zzz", "atm", "RET DAB", RULES) == ("cash.atm_withdrawal", "type_rule")
    tm.unlink_transfer(con, "i1")
    assert resolve(con, "i1", "other", "X", RULES)[0] == "other.uncategorized"


def test_single_account_report_is_unchanged_by_the_transfer_machinery(con):
    seed_report_data(con)
    rep1 = build_report(con, rules=RULES, annotations=[])
    res = tm.match_transfers(con)
    assert res.linked == [] and res.ambiguous == []
    rep2 = build_report(con, rules=RULES, annotations=[])
    assert rep1 == rep2 and "transfer_link" not in rep2["coverage"]["by_source"]


# ---- CLI

def test_cli_transfers_flow(cfg, con, capsys):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR B")
    add_tx(con, "ce", "o2", "2026-09-12", -30.0, "VIR C")
    add_tx(con, "fo", "i2", "2026-09-12", 30.0, "VIR D")
    add_tx(con, "cic", "i3", "2026-09-12", 30.0, "VIR E")
    con.close()
    base = ["--config", str(cfg.config_path), "--insecure"]
    main(base + ["transfers"])
    assert "no transfer links" in capsys.readouterr().out
    main(base + ["transfers", "match", "--dry-run"])
    out = capsys.readouterr().out
    assert "would link 1" in out and "1 ambiguous" in out
    assert connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0] == 0
    main(base + ["transfers", "match"])
    capsys.readouterr()
    main(base + ["transfers"])
    out = capsys.readouterr().out
    assert "auto" in out and "100.00" in out and "CE" in out and "FO" in out
    main(base + ["transfers", "--unmatched"])
    assert "o2" in capsys.readouterr().out
    main(base + ["transfers", "link", "o2", "i3"])
    assert "linked as" in capsys.readouterr().out
    main(base + ["transfers", "unlink", "o2"])
    assert "unlinked" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="amounts differ"):
        main(base + ["transfers", "link", "o1", "i2"])


# ---- false-positive guards (review item 2)

def test_salary_vs_same_amount_payment_is_never_linked(con):
    """+2500 salary on the rental account and a -2500 car payment on the main one, same week."""
    add_tx(con, "ce", "car", "2026-09-10", -2500.0, "PRLV CREDIT AUTO", "other")
    add_tx(con, "cic", "sal", "2026-09-11", 2500.0, "VIR SEPA EMPLOYEUR SALAIRE", "transfer_in")
    con.execute("INSERT INTO merchants VALUES ('VIR SEPA EMPLOYEUR SALAIRE','Employer','income.salary',0.9,0,'user',NULL,'t')")
    con.execute("UPDATE tx_enriched SET merchant_key='VIR SEPA EMPLOYEUR SALAIRE' WHERE tx_key='sal'")
    con.commit()
    res = tm.match_transfers(con)
    assert res.linked == [] and res.proposals == [] and links(con) == []
    # even a transfer-looking debit does not pair with an income-classified credit
    add_tx(con, "ce", "car2", "2026-09-10", -2500.0, "VIR SEPA VERS QUELQU UN", "transfer_out")
    res = tm.match_transfers(con)
    assert links(con) == [] and res.proposals == []


def test_rent_paid_and_rent_received_are_proposals_not_links(con):
    add_tx(con, "ce", "rent_out", "2026-09-05", -850.0, "VIR LOYER SEPTEMBRE M DUPONT", "transfer_out")
    add_tx(con, "cic", "rent_in", "2026-09-05", 850.0, "VIR M LOCATAIRE LOYER", "transfer_in")
    res = tm.match_transfers(con)
    assert res.linked == [] and links(con) == []
    assert len(res.proposals) == 1 and res.proposals[0]["confidence"] < tm.DEFAULT_MIN_CONFIDENCE
    assert "proposal" in tm.format_proposals(res) and "rent_out" in tm.format_proposals(res)
    tm.link_transfer(con, "rent_out", "rent_in")                          # only the user can decide


def test_unnormalised_rows_with_null_tx_type_are_never_candidates(con):
    _add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR TO ME")             # imported, no tx_enriched row
    _add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR FROM ME")
    res = tm.match_transfers(con)
    assert res.linked == [] and res.proposals == [] and res.ambiguous == []
    con.execute("INSERT INTO tx_enriched VALUES ('o1', NULL, NULL, 'x', 'x', NULL, NULL)")
    con.execute("INSERT INTO tx_enriched VALUES ('i1', NULL, NULL, 'x', 'x', NULL, NULL)")
    con.commit()
    assert tm.match_transfers(con).linked == []


def test_both_legs_need_transfer_evidence(con):
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR TO SOMEONE", "transfer_out")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "REMBOURSEMENT AMAZON", "other")      # not transfer-like
    assert tm.find_pairs(con).linked == [] and tm.find_pairs(con).proposals == []
    con.execute("UPDATE transactions SET description='REMBOURSEMENT VIR AMI' WHERE tx_key='i1'")
    con.commit()
    assert len(tm.find_pairs(con).proposals) == 1                                   # now weak evidence on both


def test_other_accounts_iban_or_holder_name_is_strong_evidence(con):
    con.execute("UPDATE accounts SET name='MR JEAN DUPONT' WHERE uid='fo'")
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR M JEAN DUPONT", "transfer_out")
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR RECU", "transfer_in")
    assert len(tm.find_pairs(con).linked) == 1
    con.execute("UPDATE accounts SET name='SOMEONE ELSE' WHERE uid='fo'")
    con.commit()
    assert tm.find_pairs(con).linked == [] and len(tm.find_pairs(con).proposals) == 1


def test_scheduled_step_only_proposes_by_default(cfg, con, capsys):
    import dataclasses
    from coach import schedule as sch
    add_tx(con, "ce", "o1", "2026-09-10", -100.0, "VIR A FR7600000000000000000000002")   # strong: other IBAN
    add_tx(con, "fo", "i1", "2026-09-10", 100.0, "VIR B")
    con.close()
    assert cfg.transfer_auto_link is False
    out = []
    sch.run_daily(cfg, insecure=True, client=__import__("helpers").FakeClient(), out=out.append)
    line = (cfg.log_dir / "schedule.log").read_text()
    assert "transfers: auto_link=off proposed=1" in line
    assert connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0] == 0
    cfg2 = dataclasses.replace(cfg, transfer_auto_link=True)
    sch.run_daily(cfg2, insecure=True, client=__import__("helpers").FakeClient(), out=out.append)
    assert connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0] == 1


def test_cli_proposals_listing(cfg, con, capsys):
    add_tx(con, "ce", "rent_out", "2026-09-05", -850.0, "VIR LOYER", "transfer_out")
    add_tx(con, "cic", "rent_in", "2026-09-05", 850.0, "VIR LOCATAIRE", "transfer_in")
    con.close()
    main(["--config", str(cfg.config_path), "--insecure", "transfers", "--proposals"])
    out = capsys.readouterr().out
    assert "proposal" in out and "rent_out" in out and "rent_in" in out
