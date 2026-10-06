"""classify.corrections: the functions behind `classify correct`, `classify review --accept` and the web app."""
from __future__ import annotations

from argparse import Namespace

import pytest

from coach.classify import commands as cc, corrections
from coach.db import connect
from memhelpers import label, make_world


@pytest.fixture
def con(cfg):
    c = make_world(cfg)
    c.commit()
    return c


def test_invalid_regex_is_a_clear_validation_error_not_a_database_error(con, cfg, capsys):
    for bad in ("(", "[a-", "*oops", "a{2,1}"):
        with pytest.raises(corrections.CorrectionError) as e:
            corrections.correct_merchant(con, bad, "food.groceries")
        assert e.value.code == "bad_regex" and "regular expression" in str(e.value) and bad in str(e.value)
    with pytest.raises(SystemExit) as e:                                           # the CLI says it too, without a traceback
        cc.cmd_correct(Namespace(insecure=True, key="(", category="food.groceries", name=None), cfg)
    assert "regular expression" in str(e.value)
    assert con.execute("SELECT COUNT(*) FROM merchants WHERE source='user' AND merchant_key='('").fetchone()[0] == 0


def test_a_valid_regex_and_an_exact_key_still_work(con):
    out = corrections.correct_merchant(con, "^FRESH", "food.restaurants")
    assert out == [("FRESH MARKET", "Fresh Market")]
    assert con.execute("SELECT category, source FROM merchants WHERE merchant_key='FRESH MARKET'").fetchone() == ("food.restaurants", "user")
    with pytest.raises(corrections.CorrectionError, match="No merchant key"):
        corrections.correct_merchant(con, "^NOTHING LIKE THIS$", "food.groceries")
    with pytest.raises(corrections.CorrectionError, match="unknown category"):
        corrections.correct_merchant(con, "FRESH MARKET", "nope.nope")
    with pytest.raises(corrections.CorrectionError):
        corrections.correct_merchant(con, "FRESH.*", "food.groceries", exact=True)         # exact means exact


def test_commit_false_leaves_the_change_to_the_caller(con):
    corrections.correct_merchant(con, "FRESH MARKET", "food.restaurants", commit=False)
    corrections.set_override(con, "fm0", "food.fast_food", "x", commit=False)
    con.rollback()
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='FRESH MARKET'").fetchone() == ("llm",)
    assert con.execute("SELECT COUNT(*) FROM tx_overrides").fetchone() == (0,)


def test_confirm_label_codes(con):
    assert corrections.confirm_label(con, "FRESH MARKET") == "food.groceries"
    for key, code in (("FRESH MARKET", "already_user"), ("NOPE", "unlabelled")):
        with pytest.raises(corrections.CorrectionError) as e:
            corrections.confirm_label(con, key)
        assert e.value.code == code
    label(con, "KNN SHOP", "food.cafes_bars", 0.9, "knn")
    con.execute("UPDATE merchants SET model='knn:OTHER' WHERE merchant_key='KNN SHOP'")
    label(con, "UNC", "other.uncategorized", 0.2, "llm")
    for key, code in (("KNN SHOP", "knn"), ("UNC", "uncategorized")):
        with pytest.raises(corrections.CorrectionError) as e:
            corrections.confirm_label(con, key)
        assert e.value.code == code
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='KNN SHOP'").fetchone() == ("knn",)


def test_cli_review_accept_goes_through_the_single_implementation(con, cfg, monkeypatch, capsys):
    seen = []
    real = corrections.confirm_label
    monkeypatch.setattr(corrections, "confirm_label", lambda c, k: (seen.append(k), real(c, k))[1])
    cc.cmd_review(Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=["FRESH MARKET"]), cfg)
    assert seen == ["FRESH MARKET"] and "confirming food.groceries" in capsys.readouterr().out
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='FRESH MARKET'").fetchone() == ("user",)
