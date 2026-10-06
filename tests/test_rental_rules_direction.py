"""E13 review: money that COMES IN from a letting agency is rent (income.rental), not a management fee; only an explicit refund of fees is netted against the cost,
and a category whose refunds exceed its spending is reported as a refund, never as a negative spending line. Synthetic agencies and amounts."""
import pytest

from anhelpers import D, make_ds, tx
from coach.analytics import averages
from coach.analytics.common import add_months
from coach.classify.rules import load_rules, rule_category

@pytest.mark.parametrize("key,direction,expected", [
    ("PRLV GESTION LOCATIVE AGENCE TEST", "out", "housing.property_management"),                # a fee leaving
    ("HONORAIRES DE GESTION LOCATIVE", "out", "housing.property_management"),
    ("VIR NEXITY NORMAND PAIEMENT CRG GESTION LOCATIVE", "in", "income.rental"),                 # the agency paying the owner the collected rent
    ("VIR CITYA REVERSEMENT GERANCE", "in", "income.rental"),
    ("VIR NEXITY NORMAND LOYER OCTOBRE", "in", "income.rental"),
    ("VIR NEXITY NORMAND", "in", "income.rental"),
    ("VIR NEXITY REMBOURSEMENT FRAIS DE GESTION LOCATIVE", "in", "housing.property_management"),   # an explicit refund of fees
    ("VIR ORPI REGULARISATION HONORAIRES GESTION LOCATIVE", "in", "housing.property_management"),
    ("VIR NEXITY REGULARISATION", "in", None),                                                    # an agency adjustment that names no fee: not called rent
    ("VIR LOYER REGULARISATION", "in", None),                                                    # not rent, and no agency named: left to the other layers
])
def test_the_direction_decides_between_rent_a_fee_and_a_refund_of_fees(key, direction, expected):
    assert rule_category(key, load_rules(), direction) == expected


def test_a_management_word_on_money_in_is_never_a_cost():
    R = load_rules()
    for key in ("GESTION LOCATIVE CRG", "GERANCE IMMOBILIER", "FRAIS DE GESTION LOCATIVE LOYERS"):
        assert rule_category(key, R, "in") != "housing.property_management" or "REMBOURSEMENT" in key
        assert rule_category(key, R, "out") == "housing.property_management"
    assert rule_category("GESTION LOCATIVE", R) is None                                       # no direction known (key listings): not forced either way


def _fees_and_credits(refund_each=147.0, fee_each=60.0, months=5):
    start = D("2026-04-10")
    txs = []
    for i in range(months):
        txs.append(tx(add_months(start, i), -fee_each, "housing.property_management", account="a", entity="AGENCE TEST", key=f"f{i}"))
        txs.append(tx(add_months(start, i), refund_each, "housing.property_management", account="a", entity="AGENCE TEST", key=f"r{i}"))
        txs.append(tx(add_months(start, i), 712.0, "income.rental", account="a", entity="NEXITY NORMAND", key=f"i{i}"))
    return make_ds(txs, today=D("2026-10-20"))


def test_a_cost_category_with_more_refunds_than_spending_is_a_net_refund_not_a_spending_line():
    r = averages.category_averages(_fees_and_credits())
    (c,) = [c for c in r.categories if c.category == "housing.property_management"]
    assert c.total_c < 0 and c.net_refund is True and c.monthly_avg_c < 0
    ok = averages.category_averages(_fees_and_credits(refund_each=10.0))
    (c2,) = [c for c in ok.categories if c.category == "housing.property_management"]
    assert c2.net_refund is False and c2.monthly_avg_c > 0


def _classify_report_lines(cfg, monkeypatch, capsys, ds):
    from coach.classify import commands as cc
    from coach.analytics import api
    monkeypatch.setattr(api, "build_dataset", lambda con, cfg_, *a, **k: ds)
    cc._coverage_averages(None, cfg)
    return capsys.readouterr().out


def test_the_classify_report_shows_a_net_refund_apart_from_spending(cfg, monkeypatch, capsys):
    out = _classify_report_lines(cfg, monkeypatch, capsys, _fees_and_credits())
    lines = out.splitlines()
    spend = [l for l in lines if l.startswith("  housing.property_management")]
    refund = [l for l in lines if "net refund: housing.property_management" in l]
    assert spend == [] and len(refund) == 1 and "back" in refund[0] and "not spending" in refund[0]
    assert not any(l.strip().startswith("housing.property_management") and "-" in l.split()[1] for l in lines)


def test_the_averages_command_does_the_same(cfg, monkeypatch, capsys):
    from argparse import Namespace
    from coach.analytics import commands as ac
    ds = _fees_and_credits()
    monkeypatch.setattr(ac, "_dataset", lambda a, cfg_: (None, ds))
    ac.cmd_averages(Namespace(insecure=True, json=False, as_of=None, owner=None, purpose=None, account=None, window=None, top=30), cfg)
    out = capsys.readouterr().out
    assert "net refunds" in out
    spending = out.split("net refunds")[0]
    assert "housing.property_management" not in spending and "income.rental" not in spending
    assert "housing.property_management" in out.split("net refunds")[1]
