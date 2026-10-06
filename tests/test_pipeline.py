from argparse import Namespace

import pytest

from coach.classify import commands as cc
from coach.classify.rules import annotation_for, categorised, resolve
from coach.db import connect
from helpers import make_prototype_db

RULES = {
    "type_rules": {"atm": "cash.atm_withdrawal", "wero_out": "transfer.to_people"},
    "merchant_rules": [
        {"match": r"\bNETFLIX\b", "category": "subscriptions.video_streaming"},
        {"match": r"\bRELAY\b", "category": "shopping.books_media"},
    ],
}


@pytest.fixture
def con(cfg):
    make_prototype_db(cfg.db_path.parent.mkdir(parents=True, exist_ok=True) or cfg.db_path)
    c = connect(cfg, insecure=True)
    return c


def put_enriched(con, tx_key, tx_type, mkey, op_date=None):
    con.execute("INSERT OR REPLACE INTO tx_enriched VALUES (?,?,?,?,?,NULL,NULL)",
                (tx_key, tx_type, op_date, mkey, mkey))


def put_merchant(con, key, cat, source, conf=0.9):
    con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,NULL,'t')", (key, key.title(), cat, conf, source))


def test_precedence_override_type_user_rule_llm_none(con):
    put_merchant(con, "NETFLIX", "leisure.cinema_events", "llm")           # rule must beat llm
    put_merchant(con, "SOME SHOP", "shopping.clothing", "user")            # user beats rule
    put_merchant(con, "RELAY", "other.other", "user")                      # user beats rule
    put_merchant(con, "LLM ONLY", "food.groceries", "llm")
    con.execute("INSERT INTO tx_overrides VALUES ('t-over','health.doctors','manual')")
    assert resolve(con, "t-over", "atm", "NETFLIX", RULES) == ("health.doctors", "override")
    assert resolve(con, "t1", "atm", "NETFLIX", RULES) == ("cash.atm_withdrawal", "type_rule")
    assert resolve(con, "t2", "card", "RELAY", RULES) == ("other.other", "user")
    assert resolve(con, "t3", "card", "NETFLIX", RULES) == ("subscriptions.video_streaming", "rule")
    assert resolve(con, "t4", "card", "LLM ONLY", RULES) == ("food.groceries", "llm")
    assert resolve(con, "t5", "card", "NOBODY", RULES) == ("other.uncategorized", "none")


def test_normalize_then_categorise_end_to_end(cfg, con):
    n = cc.cmd_normalize(Namespace(insecure=True), cfg)
    assert n == 7
    by_key = {t["tx_key"]: t for t in categorised(con, rules=RULES, annotations=[])}
    assert by_key["acc1:ref:3"]["category"] == "cash.atm_withdrawal"          # RET DAB
    assert by_key["acc1:ref:2"]["category"] == "transfer.to_people"           # WERO out
    assert by_key["acc1:ref:6"]["category"] == "subscriptions.video_streaming"  # NETFLIX rule
    assert by_key["acc1:ref:0"]["category"] == "shopping.books_media"         # llm label from fixture


def ann(**match):
    return {"id": "a", "match": match, "category": "x.y", "tags": ["one_off"]}


def test_annotation_merchant_description_and_tx_keys():
    a = [ann(merchant_key="^RELAY")]
    assert annotation_for(a, "k", "RELAY BEAUVAIS", "d", "2025-10-03", -1) is a[0]
    assert annotation_for(a, "k", "OTHER", "d", "2025-10-03", -1) is None
    a = [ann(description="FAC ?2031")]
    assert annotation_for(a, "k", "M", "FAC2031 0412", "2025-10-03", -1)
    assert annotation_for(a, "k", "M", "nothing", "2025-10-03", -1) is None
    a = [ann(tx_keys=["k1"])]
    assert annotation_for(a, "k1", "M", "d", "2025-10-03", -1)
    assert annotation_for(a, "k2", "M", "d", "2025-10-03", -1) is None


def test_annotation_date_and_amount_bounds():
    a = [ann(date_from="2025-01-01", date_to="2025-12-31", amount_min=-100, amount_max=-10)]
    f = lambda d, amt: annotation_for(a, "k", "M", "d", d, amt)
    assert f("2025-06-01", -50)
    assert f("2025-01-01", -10) and f("2025-12-31", -100)      # inclusive bounds
    assert not f("2024-12-31", -50) and not f("2026-01-01", -50)
    assert not f("2025-06-01", -9.99) and not f("2025-06-01", -100.01)


def test_annotation_category_in_and_weekdays_use_op_date():
    a = [ann(category_in=["food.restaurants"], weekdays=["mon", "tue", "wed", "thu", "fri"])]
    # booked Monday 2025-10-06, but paid on Saturday 2025-10-04 -> weekend, no match
    assert annotation_for(a, "k", "M", "d", "2025-10-06", -20, "food.restaurants", "2025-10-04") is None
    # paid on Friday -> match
    assert annotation_for(a, "k", "M", "d", "2025-10-06", -20, "food.restaurants", "2025-10-03")
    # without op_date falls back to booking date (Monday)
    assert annotation_for(a, "k", "M", "d", "2025-10-06", -20, "food.restaurants")
    # wrong pipeline category
    assert annotation_for(a, "k", "M", "d", "2025-10-06", -20, "food.groceries", "2025-10-03") is None


def test_memory_annotation_overrides_pipeline_and_sets_tags(con):
    put_enriched(con, "acc1:ref:0", "card", "RELAY BEAUVAIS")
    annotations = [{"id": "a", "match": {"merchant_key": "^RELAY"}, "category": "work.meals",
                    "tags": ["work", "one_off"], "event": "ev1"}]
    t = next(x for x in categorised(con, rules=RULES, annotations=annotations) if x["tx_key"] == "acc1:ref:0")
    assert (t["category"], t["source"], t["event"]) == ("work.meals", "memory", "ev1")
    assert t["tags"] == {"work", "one_off"}


def test_annotation_without_category_only_adds_tags(con):
    put_enriched(con, "acc1:ref:0", "card", "RELAY BEAUVAIS")
    annotations = [{"id": "a", "match": {"merchant_key": "^RELAY"}, "tags": ["exclude_from_averages"]}]
    t = next(x for x in categorised(con, rules=RULES, annotations=annotations) if x["tx_key"] == "acc1:ref:0")
    assert t["category"] == "shopping.books_media" and t["source"] == "rule"
    assert "exclude_from_averages" in t["tags"]


def test_regexp_function_available_in_connection(con):
    assert con.execute("SELECT 'Relay BEAUVAIS' REGEXP '^relay'").fetchone()[0] == 1
    assert con.execute("SELECT 'x' REGEXP 'y'").fetchone()[0] == 0
    assert con.execute("SELECT NULL REGEXP 'y'").fetchone()[0] in (0, None)


def test_correct_uses_regexp_and_saves_user_source(cfg, con):
    cc.cmd_normalize(Namespace(insecure=True), cfg)
    cc.cmd_correct(Namespace(insecure=True, key="^RELAY", category="shopping.clothing", name="Relay Shop"), cfg)
    row = con.execute("SELECT category, source, merchant_name FROM merchants WHERE merchant_key='RELAY BEAUVAIS'").fetchone()
    assert row == ("shopping.clothing", "user", "Relay Shop")
    with pytest.raises(SystemExit):
        cc.cmd_correct(Namespace(insecure=True, key="^RELAY", category="nope.nope", name=None), cfg)
