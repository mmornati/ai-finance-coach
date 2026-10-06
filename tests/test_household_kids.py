"""E14-5 / E14-6 / E14-7 / E14-9: the children's money, kid budgets and their alerts, transfers across banks, who pays what."""
from __future__ import annotations

import datetime as dt

import pytest

from coach import transfers as T
from coach.alerts import engine
from coach.alerts.settings import LOCAL_ONLY_KINDS
from coach.analytics.dataset import load_dataset
from coach.household import allocation as alloc, kidbudgets, kids, people as people_mod
from coach.household.allocation import split_cents
from coach.memory.store import MemoryStore
from helpers import add_tx
from hhhelpers import TODAY, hh_world, match_all

RULES = """\
attribution:
  - id: noa-prepaid
    member: noa
    match: { account: nk }
  - id: mia-card
    member: mia
    match: { card_last4: "4242", account: fo }
"""


def _ds(con, cfg):
    return load_dataset(con, cfg.memory_dir, today=TODAY)


# ---------------------------------------------------------------- E14-7: transfers across banks

def test_cross_bank_transfers_are_paired_with_member_evidence_and_leave_spending(cfg):
    con = hh_world(cfg)
    res = match_all(con, cfg)
    pairs = {(p["debit"].tx_key, p["credit"].tx_key): p for p in res.linked}
    # a day apart on two different banks, descriptions that do not agree, but each names the member who owns the other side
    assert ("pmo0", "pmi0") in pairs and all(("pmo%d" % i, "pmi%d" % i) in pairs for i in range(6))
    p = pairs[("pmo0", "pmi0")]
    assert p["strong"] and p["scope"] == "household" and p["to_child"] and p["days"] == 1
    assert ("xo1", "xi1") in pairs                       # Luca's top-up: 2 days apart, still inside the cross-bank window
    ds = _ds(con, cfg)
    cats = {t.key: t.category for t in ds.txs}
    assert cats["pmo0"] == cats["pmi0"] == "transfer.internal"               # excluded from spending and income of the household
    # the gift from outside is NOT an internal transfer: it stays a credit from people
    assert cats["gift1"] == "transfer.from_people"


def test_the_cross_bank_window_is_wider_than_the_same_bank_one(cfg):
    con = hh_world(cfg)
    con.execute("UPDATE transactions SET booking_date='2026-09-17', value_date='2026-09-17' WHERE tx_key='xi1'")   # 5 days after the debit
    con.commit()
    res = T.find_pairs(con, 3, cross_window_days=5, **{k: v for k, v in T.opts(cfg).items() if k != "cross_window_days"})
    assert ("xo1", "xi1") in {(p["debit"].tx_key, p["credit"].tx_key) for p in [*res.linked, *res.proposals]}
    res3 = T.find_pairs(con, 3, cross_window_days=3, **{k: v for k, v in T.opts(cfg).items() if k != "cross_window_days"})
    assert ("xo1", "xi1") not in {(p["debit"].tx_key, p["credit"].tx_key) for p in [*res3.linked, *res3.proposals]}


def test_without_member_evidence_a_weak_pair_is_only_a_proposal(cfg):
    con = hh_world(cfg)
    con.execute("UPDATE transactions SET description='VIR RECU' WHERE tx_key IN ('xo1','xi1')")
    con.commit()
    res = match_all(con, cfg)
    assert ("xo1", "xi1") not in {(p["debit"].tx_key, p["credit"].tx_key) for p in res.linked}
    assert ("xo1", "xi1") in {(p["debit"].tx_key, p["credit"].tx_key) for p in res.proposals}


# ---------------------------------------------------------------- E14-5: pocket money, top-ups, spending, balance, ratio

def test_kid_report_pocket_money_extras_ratio_spending_and_balance(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    match_all(con, cfg)
    ds = _ds(con, cfg)
    r = kids.kid_report(ds, con, "mia", months=6)
    pm = r["pocket_money"]["series"]
    assert len(pm) == 1 and pm[0]["source"] == "anna" and pm[0]["amount_c"] == 1000 and pm[0]["cadence"] == "monthly" and pm[0]["count"] == 6
    assert r["pocket_money"]["total_c"] == 6000                      # April..September: the window is the last 6 CLOSED months
    ex = r["extra_topups"]
    assert ex["total_c"] == 5500 and ex["count"] == 2
    assert ex["by_source"] == {"luca": 2500, "unknown": 3000}
    assert {i["source"]: i["linked"] for i in ex["items"]}["luca"] is True        # the linked transfer: an internal transfer within the household
    assert r["inflow"] == {"total_c": 11500, "pocket_c": 6000, "extra_c": 5500}
    assert r["ratio"]["pocket_share"] == round(6000 / 11500, 4) and r["ratio"]["extra_share"] == round(5500 / 11500, 4)
    sp = r["spending"]
    assert sp["total_c"] == 4360 and sp["by_month"][-1]["total_c"] == 4360          # her account + her card on the shared account
    assert sp["by_category"][0]["category"] == "leisure.hobbies" or sp["by_category"][0]["total_c"] >= sp["by_category"][-1]["total_c"]
    assert sp["this_month_to_date_c"] == 500
    bal = r["balance"]
    assert bal["current_c"] == 5700 and bal["estimated"] and bal["trend"][-1]["end_balance_c"] == 5700
    sept = next(p for p in bal["trend"] if p["month"] == "2026-09")
    assert sept["end_balance_c"] == 5700 + 500                          # 5.00 spent on 2026-10-01 is added back
    j = kids.to_json(r)
    assert j["inflow"]["total"] == "115.00" and j["pocket_money"]["series"][0]["amount"] == "10.00" and j["balance"]["current"] == "57.00"


def test_declared_pocket_money_marks_a_credit_without_a_clear_rhythm(cfg):
    con = hh_world(cfg, extra_yaml="")
    con.execute("DELETE FROM transactions WHERE tx_key IN ('pmi0','pmi1','pmi2','pmi3','pmo0','pmo1','pmo2','pmo3')")
    con.commit()
    yml = (cfg.memory_dir / "household.yaml").read_text().replace("    name: Mia Rossi\n    role: child\n",
                                                                  "    name: Mia Rossi\n    role: child\n    pocket_money: { amount: 10, period: monthly }\n")
    (cfg.memory_dir / "household.yaml").write_text(yml)
    r = kids.kid_report(_ds(con, cfg), con, "mia", months=6)
    assert r["pocket_money"]["declared"]["amount_c"] == 1000 and r["pocket_money"]["declared"]["matches"] >= 2
    assert r["pocket_money"]["series"] == []                         # fewer than three credits left: no detected rhythm, only the declaration
    assert r["pocket_money"]["total_c"] == 2000                      # but the two remaining 10 EUR credits are pocket money


def test_a_weekly_rhythm_and_no_pocket_money_for_irregular_credits(cfg):
    con = hh_world(cfg)
    for i in range(5):
        add_tx(con, "rl", f"wk{i}", (dt.date(2026, 9, 1) + dt.timedelta(days=7 * i)).isoformat(), 5.0, "VIREMENT RECU DE LUCA ROSSI", "person_transfer_in")
    r = kids.kid_report(_ds(con, cfg), con, "mia", months=6)
    cads = {s["cadence"]: s for s in r["pocket_money"]["series"]}
    assert "weekly" in cads and cads["weekly"]["amount_c"] == 500 and cads["weekly"]["source"] == "luca"
    items = kids._cluster([(dt.date(2026, 1, 1), 1000, "a"), (dt.date(2026, 3, 9), 1010, "b"), (dt.date(2026, 3, 12), 5000, "c")])
    assert [len(g) for g in items] == [2, 1]
    assert kids._cadence([dt.date(2026, 1, 1), dt.date(2026, 1, 9), dt.date(2026, 1, 11)]) is None


def test_children_overview_and_unknown_member(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    ds = _ds(con, cfg)
    assert [c["member"] for c in kids.children_overview(ds, con)] == ["mia", "noa"]
    with pytest.raises(ValueError):
        kids.kid_report(ds, con, "ghost")


# ---------------------------------------------------------------- E14-6: kid budgets and gentle alerts

BUDGETS = """\
kid_budgets:
  - id: mia-weekly
    member: mia
    period: weekly
    limit: 20
  - id: noa-month-food
    member: noa
    period: monthly
    limit: 12
    group: food
"""


def test_kid_budget_status_over_at_risk_and_ok(cfg):
    con = hh_world(cfg, extra_yaml=RULES + BUDGETS)
    ds = _ds(con, cfg)
    st = {s["id"]: s for s in kidbudgets.status(ds)}
    # today 2026-10-04 is a Sunday: the week is 2026-09-28 .. 2026-10-04: Mia spent 5.00 (1 Oct) on her own account
    assert st["mia-weekly"]["spent_c"] == 500 and st["mia-weekly"]["status"] == "ok" and st["mia-weekly"]["period_start"] == dt.date(2026, 9, 28)
    # Noa: the month so far (October): nothing -> ok; check September by moving the date
    ds2 = load_dataset(con, cfg.memory_dir, today=dt.date(2026, 9, 30))
    st2 = {s["id"]: s for s in kidbudgets.status(ds2)}
    assert st2["noa-month-food"]["spent_c"] == 1350 and st2["noa-month-food"]["status"] == "over"
    assert kidbudgets.status(ds, "noa")[0]["id"] == "noa-month-food" and len(kidbudgets.status(ds, "mia")) == 1


def test_unusual_payment_and_alert_candidates_are_gentle_and_local_only(cfg):
    con = hh_world(cfg, extra_yaml=RULES + BUDGETS)
    for i in range(10):
        add_tx(con, "rl", f"u{i}", (dt.date(2026, 7, 1) + dt.timedelta(days=5 * i)).isoformat(), -3.0, "CARTE BOULANGERIE DU COIN", "card")
    add_tx(con, "rl", "big", "2026-10-02", -45.0, "CARTE JEUXVIDEO SHOP", "card")
    ds = _ds(con, cfg)
    assert [t.key for t in kidbudgets.unusual(ds, "mia")] == ["big"]
    cands = kidbudgets.candidates(ds)
    kinds = {c.kind for c in cands}
    assert kinds == {"kid_budget"} and "kid_budget" in LOCAL_ONLY_KINDS
    texts = " ".join(c.title + " " + c.body for c in cands)
    assert "Mia" in texts and "unusually" in texts
    over = [c for c in cands if c.payload.get("subtype") == "limit"]
    assert over and over[0].severity in ("low", "medium")


def test_kid_alerts_never_reach_a_channel_nor_the_digest(cfg):
    from coach.alerts import digest as digest_mod, store as alert_store
    con = hh_world(cfg, extra_yaml=RULES + BUDGETS.replace("limit: 20", "limit: 5"))
    ds = _ds(con, cfg)
    s = cfg.alert_settings
    engine.evaluate(con, cfg, ds, s)
    events = alert_store.listing(con, today=ds.today)
    kid = [e for e in events if e["kind"] == "kid_budget"]
    assert kid and "Mia" in kid[0]["title"]                               # the local feed may name the child
    for channel in ("macos", "ntfy", "email", "telegram"):                # but no channel is ever offered it, not even as a count
        assert not [e for e in engine._pending_for(events, channel, s) if e["kind"] == "kid_budget"]
    d = digest_mod.build(con, cfg, ds, s, today=ds.today)
    assert not [i for i in d["alerts"]["items"] if i["kind"] == "kid_budget"]
    assert "Mia" not in digest_mod.render(d, s, cfg)


# ---------------------------------------------------------------- E14-9: who pays what

ALLOC = """\
allocations:
  - id: groceries-equal
    title: Groceries
    match: { category: food.groceries }
    method: equal
  - id: streaming-income
    match: { group: subscriptions }
    method: income
  - id: cinema-custom
    match: { category: leisure.cinema_events }
    method: custom
    shares: { anna: 70, luca: 30 }
"""


def test_split_cents_largest_remainder_is_exact():
    assert split_cents(1000, {"a": 1, "b": 1, "c": 1}) == {"a": 334, "b": 333, "c": 333}
    assert sum(split_cents(-1001, {"a": 3, "b": 1}).values()) == -1001 and split_cents(0, {"a": 1}) == {"a": 0}
    assert split_cents(5, {"a": 0, "b": 0}) == {"a": 0, "b": 0}


def test_who_pays_equal_income_custom_and_settlement(cfg):
    con = hh_world(cfg, extra_yaml=RULES + ALLOC)
    for i in range(3):                                           # personal payments of the shared groceries: Anna pays 30 + 30, Luca pays 20
        add_tx(con, "an", f"ga{i}", f"2026-0{7 + i}-10", -30.0, "CARTE SUPERMARCHE ANNA", "card")
    add_tx(con, "lu", "gl", "2026-09-11", -20.0, "CARTE SUPERMARCHE LUCA", "card")
    from memhelpers import label
    label(con, "CARTE SUPERMARCHE ANNA", "food.groceries")
    label(con, "CARTE SUPERMARCHE LUCA", "food.groceries")
    for i in range(3):                                           # incomes: Anna 3000, Luca 1000 a month -> 75 / 25 for the income rule
        add_tx(con, "an", f"ia{i}", f"2026-0{7 + i}-26", 3000.0, "VIR SALAIRE ANNA", "transfer_in")
        add_tx(con, "lu", f"il{i}", f"2026-0{7 + i}-26", 1000.0, "VIR SALAIRE LUC", "transfer_in")
    label(con, "VIR SALAIRE ANNA", "income.salary")
    label(con, "VIR SALAIRE LUC", "income.salary")
    add_tx(con, "ce", "st1", "2026-09-03", -12.0, "STREAMBOX FAMILY", "card")                   # joint account: nobody owes for it
    add_tx(con, "an", "st2", "2026-09-04", -8.0, "MUSICBOX", "card")
    label(con, "STREAMBOX FAMILY", "subscriptions.video_streaming")
    label(con, "MUSICBOX", "subscriptions.music_streaming")
    ds = _ds(con, cfg)
    rep = alloc.who_pays(ds, months=12)
    by = {r["id"]: r for r in rep["rules"]}
    g = by["groceries-equal"]
    # the make_world supermarket line on fo is joint; the personal ones are Anna 90, Luca 20: fair personal share 55 each
    members = {m["member"]: m for m in g["members"]}
    assert g["personal_paid_c"] == 11000 and members["anna"]["paid_c"] == 9000 and members["luca"]["paid_c"] == 2000
    assert members["anna"]["net_c"] == 9000 - 5500 and members["luca"]["net_c"] == 2000 - 5500 and sum(m["net_c"] for m in g["members"]) == 0
    assert g["total_c"] == g["joint_paid_c"] + g["personal_paid_c"] + g["unattributed_c"]
    s = by["streaming-income"]
    sm = {m["member"]: m for m in s["members"]}
    assert sm["anna"]["share_pct"] == 75.0 and sm["luca"]["share_pct"] == 25.0
    assert s["joint_paid_c"] > 0 and sm["anna"]["owed_c"] + sm["luca"]["owed_c"] == s["total_c"]
    c = by["cinema-custom"]
    assert [m["share_pct"] for m in c["members"]] == [70.0, 30.0] and c["n"] == 1 and c["unattributed_c"] == 1400   # Mia's cinema is attributed to Mia: not a settlement
    j = alloc.to_json(rep)
    assert j["rules"][0]["total"] and j["by_member"][0]["member"] == "anna"


def test_a_transaction_belongs_to_the_first_matching_rule_only(cfg):
    con = hh_world(cfg, extra_yaml=RULES + ALLOC.replace("group: subscriptions", "category: food.groceries"))
    ds = _ds(con, cfg)
    rep = alloc.who_pays(ds, months=12)
    by = {r["id"]: r for r in rep["rules"]}
    assert by["streaming-income"]["n"] == 0 and by["groceries-equal"]["n"] > 0
    assert "no transaction matched this rule in the window" in by["streaming-income"]["notes"]
    assert alloc.who_pays(_ds(con, cfg), 12, "nope")["rules"] == []


def test_banks_that_print_only_a_first_name_or_a_counterparty_still_give_the_source(cfg):
    """Revolut-style lines: 'To ANNA' / 'FROM LUCA R' (a first name only), a parsed counterparty, both parents together, and the child's own
    pocket and exchange moves, which are NOT money received."""
    con = hh_world(cfg)
    add_tx(con, "rl", "f1", "2026-09-03", 8.0, "Money from ANNA", "internal_transfer")
    add_tx(con, "rl", "f2", "2026-09-04", 9.0, "VIREMENT", "internal_transfer")
    con.execute("INSERT OR REPLACE INTO tx_parse_meta(tx_key, parser, counterparty) VALUES ('f2','test','LUCA R')")
    add_tx(con, "rl", "f3", "2026-09-05", 11.0, "Money from ANNA and LUCA", "internal_transfer")
    add_tx(con, "rl", "f4", "2026-09-06", 40.0, "To pocket Holidays", "savings_internal")
    add_tx(con, "rl", "f5", "2026-09-07", 6.0, "Exchange EUR to GBP", "fx_exchange")
    add_tx(con, "rl", "f6", "2026-09-08", 7.0, "Money from MIA", "internal_transfer")           # her own name only: a move between her own accounts
    con.commit()
    r = kids.kid_report(_ds(con, cfg), con, "mia", months=6)
    by = {i["tx_key"]: i["source"] for i in r["extra_topups"]["items"]}
    assert by["f1"] == "anna" and by["f2"] == "luca" and by["f3"] == "joint"
    assert not {"f4", "f5", "f6"} & set(by)
    assert r["own_moves"] == {"count": 3, "total_c": 5300}
    assert kids.to_json(r)["own_moves"]["total"] == "53.00"
    p = people_mod.load(MemoryStore(cfg.memory_dir, history=False))
    assert p.mentioned("TO ANNA") == {"anna"} and p.mentioned("Anna Rossi") == {"anna"} and p.mentioned("Rossi") == set()


def _pairs(res):
    return {(p["debit"].tx_key, p["credit"].tx_key) for p in [*res.linked, *res.proposals]}


def test_repeated_equal_amounts_a_few_days_apart_keep_their_same_day_pairs(cfg):
    """Two 12 EUR top-ups four days apart: each debit pairs with the credit of its own day (pass 1); the wider cross-bank window must not make them ambiguous."""
    con = hh_world(cfg)
    for i, d in enumerate(("2026-09-20", "2026-09-24")):
        add_tx(con, "an", f"rd{i}", d, -12.0, "VIR MIA ROSSI", "person_transfer_out")
        add_tx(con, "rl", f"rc{i}", d, 12.0, "VIREMENT RECU DE ANNA ROSSI", "person_transfer_in")
    opts = T.opts(cfg)
    res = T.find_pairs(con, cfg.transfer_window_days, **opts)
    assert {("rd0", "rc0"), ("rd1", "rc1")} <= {(p["debit"].tx_key, p["credit"].tx_key) for p in res.linked}
    assert not [g for g in res.ambiguous if g["debit"].tx_key in ("rd0", "rd1")]


def test_the_wide_pass_only_adds_pairs_and_needs_strong_evidence_beyond_the_window(cfg):
    con = hh_world(cfg)
    base = {k: v for k, v in T.opts(cfg).items() if k != "cross_window_days"}
    for i, (dd, cd) in enumerate((("2026-09-01", "2026-09-01"), ("2026-09-10", "2026-09-14"), ("2026-09-20", "2026-09-25"))):
        add_tx(con, "an", f"wd{i}", dd, -17.0 - i, "VIR MIA ROSSI", "person_transfer_out")
        add_tx(con, "rl", f"wc{i}", cd, 17.0 + i, "VIREMENT RECU DE ANNA ROSSI", "person_transfer_in")
    add_tx(con, "an", "wdw", "2026-09-02", -31.0, "VIR RECU", "person_transfer_out")            # weak evidence, 4 days apart
    add_tx(con, "rl", "wcw", "2026-09-06", 31.0, "VIR RECU", "person_transfer_in")
    narrow = T.find_pairs(con, 3, cross_window_days=None, **base)
    wide = T.find_pairs(con, 3, cross_window_days=5, **base)
    assert _pairs(narrow) <= _pairs(wide) and len(wide.ambiguous) <= len(narrow.ambiguous)      # pass 2 never takes a pair away
    assert {p["debit"].tx_key for p in narrow.linked} <= {p["debit"].tx_key for p in wide.linked}
    assert ("wd0", "wc0") in _pairs(narrow) and ("wd1", "wc1") not in _pairs(narrow)             # 4 days: beyond the window
    assert {("wd1", "wc1"), ("wd2", "wc2")} <= _pairs(wide)                                      # strong evidence, 4 and 5 days
    assert ("wdw", "wcw") not in _pairs(wide)                                                    # weak evidence beyond the window: no pair


def test_the_wide_pass_prefers_the_smallest_gap(cfg):
    con = hh_world(cfg)
    add_tx(con, "an", "td", "2026-09-20", -13.0, "VIR MIA ROSSI", "person_transfer_out")
    add_tx(con, "rl", "tc_far", "2026-09-25", 13.0, "VIREMENT RECU DE ANNA ROSSI", "person_transfer_in")
    add_tx(con, "rl", "tc_near", "2026-09-24", 13.0, "VIREMENT RECU DE ANNA ROSSI", "person_transfer_in")
    res = T.find_pairs(con, 3, **T.opts(cfg))
    assert ("td", "tc_near") in _pairs(res) and ("td", "tc_far") not in _pairs(res)
