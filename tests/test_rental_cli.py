"""E15 CLI: `coach rental ...`. Reads are computed (aggregates and statuses, JSON and text); every memory write is validated, PREVIEWED and written only
after a typed yes, recorded with the source cli; nothing is looked up and nothing is guessed."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from coach.cli import main
from coach.memory.store import MemoryStore
from memhelpers import make_world
from rentalhelpers import rental_world

ASOF = ["--as-of", "2026-10-04"]


def cli(cfg, *argv):
    args = ["--insecure", "--config", str(cfg.config_path), *argv]
    if "--yes" in argv:
        with mock.patch("coach.memory.commands._is_tty", return_value=True):
            return main(args)
    return main(args)


def out(capsys, cfg, *argv):
    cli(cfg, *argv)
    return capsys.readouterr().out


def js(capsys, cfg, *argv):
    return json.loads(out(capsys, cfg, *argv, "--json"))


def store(cfg):
    return MemoryStore(cfg.memory_dir, history=True)


@pytest.fixture
def world(cfg):
    con = rental_world(cfg)
    con.close()
    return cfg


def test_list_shows_the_links_the_last_month_the_vacancy_and_the_scheme(world, capsys):
    text = out(capsys, world, "rental", "list", *ASOF)
    assert "rental-flat-1" in text and "account declared" in text and "loan declared" in text and "2026-09: rent 620.00" in text
    assert "vacancy: 1 month(s) without rent, 1 declared" in text and "scheme pinel active, ends 2030-03-04" in text
    d = js(capsys, world, "rental", "list", *ASOF)
    (r,) = d["properties"]
    assert r["id"] == "rental-flat-1" and r["expected_rent"] == "620.00" and r["last_month"]["effort"] == "348.25" and r["vacancy"]["n_missing"] == 1
    assert r["scheme"]["months_left"] == 41 and r["net_equity"] is not None and r["missing"] == []


def test_cashflow_pnl_and_the_figures_of_the_year(world, capsys):
    text = out(capsys, world, "rental", "cashflow", "--months", "4", *ASOF)
    assert "2026-07" in text and "late paid" in text and "effort d'epargne" in text and "this month (2026-10): pending" in text
    d = js(capsys, world, "rental", "cashflow", "rental-flat-1", "--months", "12", *ASOF)
    jul = next(m for m in d["cashflow"]["months"] if m["month"] == "2026-07")
    assert jul["rent"] == "1240.00" and jul["net"] == "215.75" and d["cashflow"]["vacancy"]["missing_months"] == ["2026-05"]
    pnl = out(capsys, world, "rental", "pnl", "--year", "2026", *ASOF)
    assert "rent received" in pnl and "4340.00" in pnl and "-5637.25" in pnl and "effort d'epargne 5853.00" in pnl and "economic result" in pnl
    assert js(capsys, world, "rental", "pnl", "--year", "2026", *ASOF)["totals"]["effort"] == "5853.00"


def test_scheme_tax_indicators_and_flows(world, capsys):
    s = out(capsys, world, "rental", "scheme", *ASOF)
    assert "commitment 2021-03-05 for 9 year(s), ends 2030-03-04" in s and "rent cap: above cap" in s and "tenant income: within limit" in s
    t = out(capsys, world, "rental", "tax", "--year", "2026", *ASOF)
    assert "CANDIDATES, not a return" in t and "micro-foncier" in t and "documents to gather" in t and "Nothing is filed by the coach" in t
    td = js(capsys, world, "rental", "tax", "--year", "2026", *ASOF)
    assert td["status"] == "computed" and td["micro_foncier"]["abatement"] == "1302.00" and td["scheme_reduction"]["candidate"] == "3800.00"
    i = out(capsys, world, "rental", "indicators", "--market-rate", "2.4", "--market-date", "2026-09-20", *ASOF)
    assert "above market" in i and "net equity: value 200000.00" in i and "not financial advice" in i
    none = out(capsys, world, "rental", "indicators", *ASOF)
    assert "never looks it up" in none and "missing: a market rate you entered" in none
    fl = out(capsys, world, "rental", "flows", *ASOF)
    assert "every flow of the property account is in a property category" in fl


def test_a_missing_property_or_an_ambiguous_one_is_an_error(world, capsys):
    with pytest.raises(SystemExit) as e:
        cli(world, "rental", "pnl", "ghost", *ASOF)
    assert "no rental property with id 'ghost'" in str(e.value) and "rental-flat-1" in str(e.value)


def test_without_any_property_the_list_explains_how_to_declare_one(cfg, capsys):
    con = make_world(cfg)
    con.close()
    assert "no rental property on file" in out(capsys, cfg, "rental", "list", *ASOF)


def test_edit_previews_and_writes_only_after_a_confirmation(world, capsys):
    with pytest.raises(SystemExit) as e:                                              # no terminal, no --yes: preview only
        cli(world, "rental", "edit", "rental-flat-1", "--set", "commitment.tenant_income_limit=31000")
    assert "preview only" in str(e.value)
    assert "tenant_income_limit: 31000" in capsys.readouterr().out
    assert next(x for x in store(world).assets() if x.id == "rental-flat-1").commitment.tenant_income_limit == 30000
    cli(world, "rental", "edit", "rental-flat-1", "--yes", "--set", "commitment.tenant_income_limit=31000", "--set", "market_rate.rate_pct=2.9",
        "--set", "market_rate.as_of=2026-09-30", "--reason", "from the deed")
    a = next(x for x in store(world).assets() if x.id == "rental-flat-1")
    assert a.commitment.tenant_income_limit == 31000 and a.market_rate.rate_pct == 2.9 and str(a.market_rate.as_of) == "2026-09-30"
    ch = store(world).history(limit=1)[0]
    assert ch.source == "cli" and "from the deed" in (ch.reason or "")
    with pytest.raises(SystemExit) as e:
        cli(world, "rental", "edit", "rental-flat-1", "--yes")
    assert "give --set" in str(e.value)
    with pytest.raises(SystemExit):
        cli(world, "rental", "edit", "rental-flat-1", "--yes", "--set", "commitment.years=0")             # refused by the schema, nothing written
    assert next(x for x in store(world).assets() if x.id == "rental-flat-1").commitment.years == 9


def test_an_abbreviation_of_yes_is_a_parse_error(world):
    with pytest.raises(SystemExit):
        cli(world, "rental", "edit", "rental-flat-1", "--ye", "--set", "scheme=x")


def test_add_declares_a_property_with_a_typed_confirmation(cfg, capsys):
    con = make_world(cfg)
    con.close()
    cli(cfg, "rental", "add", "rental-flat-9", "--yes", "--set", "account=rn", "--set", "scheme=pinel")
    a = next(x for x in store(cfg).assets() if x.id == "rental-flat-9")
    assert a.kind == "real_estate_rental" and a.account == "rn" and a.scheme == "pinel"
    with pytest.raises(SystemExit) as e:
        cli(cfg, "rental", "add", "rental-flat-9", "--yes")
    assert "already exists" in str(e.value)


def test_the_extension_decision_is_recorded_after_a_preview(world, capsys):
    with pytest.raises(SystemExit) as e:
        cli(world, "rental", "extension", "rental-flat-1", "--decision", "extend")
    assert "--years" in str(e.value)
    cli(world, "rental", "extension", "rental-flat-1", "--decision", "extend", "--years", "3", "--rate", "6", "--on", "2029-06-01", "--note", "kept", "--yes")
    c = next(x for x in store(world).assets() if x.id == "rental-flat-1").commitment
    assert c.extension.decision == "extend" and c.extension.years == 3 and c.extension.additional_rate_pct == 6 and str(c.extension.decided_on) == "2029-06-01"
    capsys.readouterr()
    s = js(capsys, world, "rental", "scheme", *ASOF)
    assert s["effective_end_date"] == "2033-03-04" and s["extension"]["decision"] == "extend"


def test_a_vacancy_period_and_a_market_rate_are_recorded(world, capsys):
    cli(world, "rental", "vacancy", "rental-flat-1", "--start", "2026-05-01", "--end", "2026-05-31", "--note", "new tenant search", "--yes")
    a = next(x for x in store(world).assets() if x.id == "rental-flat-1")
    assert [(str(v.start), str(v.end)) for v in a.vacancies] == [("2026-02-01", "2026-02-28"), ("2026-05-01", "2026-05-31")]
    capsys.readouterr()
    d = js(capsys, world, "rental", "cashflow", *ASOF)
    assert d["cashflow"]["vacancy"]["missing_months"] == [] and d["cashflow"]["vacancy"]["declared_months"] == ["2026-02", "2026-05"]
    cli(world, "rental", "market-rate", "rental-flat-1", "--rate", "2.8", "--as-of", "2026-09-30", "--source", "a quote", "--yes")
    mr = next(x for x in store(world).assets() if x.id == "rental-flat-1").market_rate
    assert mr.rate_pct == 2.8 and mr.source == "a quote"
    capsys.readouterr()
    ind = js(capsys, world, "rental", "indicators", *ASOF)
    assert ind["market_rate"]["basis"] == "recorded on the property" and ind["loan_rate"]["gap_pts"] == 0.6


def test_scheme_can_add_one_open_question_for_the_missing_facts(cfg, capsys):
    asset = "\n  - id: rental-flat-1\n    kind: real_estate_rental\n    scheme: pinel\n"
    con = rental_world(cfg, asset=asset, loan=False)
    con.close()
    text = out(capsys, cfg, "rental", "scheme", *ASOF, "--propose-questions")
    assert "missing facts (never guessed)" in text and "added 1 open question" in text
    (q,) = [q for q in store(cfg).questions() if q.key == "fill:rental:rental-flat-1"]
    assert "commitment.start_date" in q.question
    assert "already on the open-questions list" in out(capsys, cfg, "rental", "scheme", *ASOF, "--propose-questions")
