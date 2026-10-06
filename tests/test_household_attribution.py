"""E14-1..E14-4: members, account owners, who a transaction belongs to (default, rules, manual, reversible), person views."""
from __future__ import annotations

import pytest

from anhelpers import TODAY as _T  # noqa: F401
from coach.analytics import api as analytics_api, cashflow as cashflow_mod
from coach.analytics.dataset import load_dataset
from coach.household import attribution as attr, people as people_mod
from coach.household.attribution import HouseholdError, card_last4
from coach.memory.store import MemoryStore, ValidationFailed
from hhhelpers import TODAY, hh_world

RULES = """\
attribution:
  - id: noa-prepaid
    member: noa
    match: { account: nk }
  - id: mia-card
    member: mia
    match: { card_last4: "4242", account: fo }
    note: Mia's card on the shared account
"""


def _people(cfg):
    return people_mod.load(MemoryStore(cfg.memory_dir, history=False))


def _ds(con, cfg):
    return load_dataset(con, cfg.memory_dir, today=TODAY)


# ---------------------------------------------------------------- people (E14-1)

def test_people_resolve_owner_values(cfg):
    hh_world(cfg)
    p = _people(cfg)
    assert p.ids() == ["anna", "luca", "mia", "noa"] and p.adults() == ["anna", "luca"] and p.children() == ["mia", "noa"]
    assert p.resolve("MIA") == "mia" and p.resolve("Mia Rossi") == "mia" and p.resolve("mme anna rossi") == "anna"
    assert p.resolve("Joint") == "joint" and p.resolve("stranger") is None and p.resolve(None) is None
    assert p.text_mentions("VIR MIA ROSSI POCHE", "mia") and not p.text_mentions("VIR ROSSI", "mia")
    assert p.name("joint") == "Joint" and p.first_name("anna") == "Anna"


def test_schema_rules_are_validated(cfg):
    hh_world(cfg, extra_yaml=RULES)
    store = MemoryStore(cfg.memory_dir, history=False)
    assert [r.id for r in store.model("household.yaml").attribution] == ["noa-prepaid", "mia-card"]
    bad = (cfg.memory_dir / "household.yaml").read_text().replace("member: noa", "member: ghost")
    (cfg.memory_dir / "household.yaml").write_text(bad)
    issues = MemoryStore(cfg.memory_dir, history=False).semantic_issues("household.yaml", MemoryStore(cfg.memory_dir, history=False).model("household.yaml"))
    assert any(i.code == "unknown_member" for i in issues)
    with pytest.raises(ValidationFailed):                                    # a rule with no condition, a bad card number
        MemoryStore(cfg.memory_dir, history=False).edit("household.yaml", [{"op": "append", "path": "attribution", "value": {
            "id": "x", "member": "mia", "match": {"card_last4": "12"}}}], dry_run=True)


# ---------------------------------------------------------------- attribution (E14-3)

def test_card_last4_reads_only_masked_numbers():
    assert card_last4("CARTE X4242 12/10 SHOP") == "4242" and card_last4("CB*1234 SHOP") == "1234" and card_last4("****9876") == "9876"
    assert card_last4("PAIEMENT CB 0210 SHOP") is None and card_last4("CARTE 02/10 RELAY") is None and card_last4(None) is None


def test_default_is_the_account_owner_then_rules_then_manual(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    ds = _ds(con, cfg)
    by = {t.key: t.person for t in ds.txs}
    assert by["mp0"] == "mia"                       # Mia's own account
    assert by["np0"] == "noa"                       # a rule: the prepaid card account belongs to Noa (the account is owned 'joint')
    assert by["mc1"] == "mia"                       # a rule: her card ending 4242 on the shared account
    assert by["mc2"] == "joint"                     # another card on the shared account stays joint
    assert by["pmo0"] == "anna"
    # manual outranks every rule; clearing it restores the rule
    people = _people(cfg)
    r = attr.assign(con, people, "mc1", "joint", by="cli", note="it was a family outing")
    assert r["changed"] and r["previous"] is None
    assert {t.key: t.person for t in _ds(con, cfg).txs}["mc1"] == "joint"
    assert attr.assign(con, people, "mc1", "joint")["changed"] is False
    attr.assign(con, people, "mc1", "luca")
    assert [l["action"] for l in attr.log(con, "mc1")] == ["set", "set"]
    attr.clear(con, "mc1")
    assert {t.key: t.person for t in _ds(con, cfg).txs}["mc1"] == "mia"
    # reversible: undo the last two lines, newest first
    lines = attr.log(con, "mc1")
    assert lines[0]["action"] == "clear"
    attr.revert(con, people, lines[0]["id"])                                  # back to luca
    assert {t.key: t.person for t in _ds(con, cfg).txs}["mc1"] == "luca"
    attr.revert(con, people, lines[1]["id"])                                  # the state is what that line produced: back to joint
    assert {t.key: t.person for t in _ds(con, cfg).txs}["mc1"] == "joint"
    with pytest.raises(HouseholdError):                                       # the transaction moved on since that line
        attr.revert(con, people, lines[1]["id"])
    with pytest.raises(HouseholdError):
        attr.assign(con, people, "mc1", "nobody")


def test_unknown_owner_is_nobody_and_explain_says_why(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    con.execute("UPDATE accounts SET owner='Somebody Else' WHERE uid='lu'")
    con.commit()
    people = _people(cfg)
    ds = _ds(con, cfg)
    assert {t.key: t.person for t in ds.txs}["xo1"] is None
    x = attr.explain(con, people, "mc1")
    assert x["person"] == "mia" and x["source"] == "rule" and x["rule"] == "mia-card" and x["card_last4"] == "4242"
    assert [r["matched"] for r in x["rules"]] == [False, True]
    assert "account" in x["rules"][0]["why_not"]
    y = attr.explain(con, people, "mc2")
    assert y["source"] == "account" and y["person"] == "joint" and all(not r["matched"] for r in y["rules"])
    z = attr.explain(con, people, "xo1")
    assert z["source"] == "none" and z["person"] is None
    with pytest.raises(HouseholdError):
        attr.explain(con, people, "nope")


# ---------------------------------------------------------------- person views (E14-4)

def test_member_view_filters_every_analysis_and_leaves_the_household_unchanged(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    ds = _ds(con, cfg)
    whole = cashflow_mod.cashflow(ds, months=3)
    mia = ds.member_view("mia")
    assert {t.person for t in mia.txs} == {"mia"} and mia.member == "mia"
    assert set(mia.balances) == {"rl"}                                           # only the account she OWNS has a balance of hers
    spend_mia = cashflow_mod.cashflow(mia, months=2).to_dict()
    spend_all = whole.to_dict()
    assert spend_mia["household"]["months"] != spend_all["household"]["months"]
    # the household total is untouched by a view being built
    assert cashflow_mod.cashflow(ds, months=3).to_dict() == spend_all
    # the card on the shared account counts for Mia: 14.00 in September
    sep = next(m for m in spend_mia["household"]["months"] if m["month"] == "2026-09")
    assert sep["spending"] == "43.60"          # 4.50 + 12.00 + 3.20 + 9.90 on her own account, and 14.00 with her card on the shared one


def test_a_persons_forecast_and_balances_cover_only_the_accounts_they_own(cfg):
    from coach.analytics import forecast as forecast_mod
    con = hh_world(cfg, extra_yaml=RULES)
    ds = _ds(con, cfg)
    mia = ds.member_view("mia")
    assert mia.balance_uids() == ["rl"] and ds.balance_uids() == sorted(ds.accounts)
    fc = forecast_mod.forecast(mia, 30, points=False).to_dict()
    assert fc["household"]["start_balance"] == "57.00" and [a["account"] for a in fc["accounts"]] == ["rl"]
    assert forecast_mod.forecast(ds, 30, points=False).to_dict()["household"]["start_balance"] != "57.00"


def test_explain_shows_whose_a_transaction_is_and_why(cfg):
    from coach.memory import explain as explain_mod
    con = hh_world(cfg, extra_yaml=RULES)
    store = MemoryStore(cfg.memory_dir, history=False)
    x = explain_mod.explain(con, store, "mc1")
    assert x["attribution"]["person"] == "mia" and x["attribution"]["rule"] == "mia-card"
    text = explain_mod.format_explanation(x)
    assert "Person (E14-3" in text and "belongs to: mia  [rule]" in text and "rule mia-card -> mia: MATCHES" in text
    assert explain_mod.explain(con, store, "mc2")["attribution"]["source"] == "account"
