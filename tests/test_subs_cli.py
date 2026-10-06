"""`coach subs ...` (E8): list / show / draft-contracts / usage / alternatives / letter / contact / decide / decisions / savings.
Every memory write is previewed and applied after a typed yes (source cli); proposals for the drafts by default; nothing is sent."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from coach.cli import main
from coach.db import connect
from coach.memory import proposals as P
from coach.memory.store import MemoryStore
from subshelpers import TODAY, build_subs_world


def cli(cfg, *argv):
    """--yes only skips the typed prompt for a human at a terminal: a call with --yes pretends to be one (the refusals are tested apart)."""
    from unittest import mock
    args = ["--insecure", "--config", str(cfg.config_path), *argv]
    if "--yes" in argv:
        with mock.patch("coach.memory.commands._is_tty", return_value=True):
            return main(args)
    return main(args)


@pytest.fixture
def world(cfg, monkeypatch):
    monkeypatch.setattr("coach.subs.commands._today", lambda: TODAY)
    con = build_subs_world(cfg)
    con.close()
    return cfg


def tty(monkeypatch, answers=("y",)):
    it = iter(answers)
    monkeypatch.setattr("coach.memory.commands._is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def jlist(cfg, capsys, *extra):
    cli(cfg, "subs", "list", "--json", *extra)
    return json.loads(capsys.readouterr().out)


def ref_of(inv, name):
    return next(r["ref"] for r in inv["rows"] if r["name"] == name)


# ---------------------------------------------------------------- list / show

def test_list_text_and_json(world, capsys):
    cli(world, "subs", "list")
    out = capsys.readouterr().out
    assert "6 recurring costs: 196.37 EUR/month, 2356.44 EUR/year" in out and "4 without a file" in out
    assert "Energy & utilities" in out and "NO contract" in out and "usage never" in out and "paid_but_never_used" in out
    inv = jlist(world, capsys)
    assert inv["totals"]["yearly"] == "2356.44" and len(inv["rows"]) == 6          # the ended series is hidden
    assert len(jlist(world, capsys, "--all")["rows"]) == 7
    assert {r["name"] for r in jlist(world, capsys, "--group", "telecom")["rows"]} == {"TelcoCo"}
    assert {r["name"] for r in jlist(world, capsys, "--missing-contract")["rows"]} == {"Cloudbox", "Fitclub", "Homesure Assurances", "Sunpower Energie"}


def test_show_one_service_in_full(world, capsys):
    cli(world, "subs", "show", "telco")
    out = capsys.readouterr().out
    assert "TelcoCo" in out and "29.99 EUR/month, 359.88 EUR/year" in out and "can cancel: YES, now" in out
    assert "early exit costs: 29.99 EUR" in out and "free from 2027-01-15" in out and "basis: Telecom contracts (Loi Chatel)" in out
    assert "Verify with your contract" in out
    cli(world, "subs", "show", "cloudbox")
    out = capsys.readouterr().out
    assert "price from 2026-01-06: 4.99" in out and "price from 2026-07-06: 5.99" in out and "contract: missing" in out and "not in the bank data" in out
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "show", "nothing-here")
    assert "no subscription" in str(e.value)


# ---------------------------------------------------------------- draft-contracts

def test_draft_contracts_dry_run_creates_nothing(world, capsys):
    before = sorted(p.name for p in world.memory_dir.rglob("*"))
    cli(world, "subs", "draft-contracts", "--dry-run")
    out = capsys.readouterr().out
    assert "4 contract draft(s); by kind: energy 1, insurance_home 1, membership 1, software 1" in out and "(dry run: nothing created)" in out
    assert "fitclub" in out and "^FITCLUB" in out and "missing: renewal, commitment_end, notice_period_days" in out
    assert sorted(p.name for p in world.memory_dir.rglob("*")) == before


def test_draft_contracts_by_default_creates_proposals_only(world, capsys):
    cli(world, "subs", "draft-contracts")
    out = capsys.readouterr().out
    store = MemoryStore(world.memory_dir, history=False)
    pend = P.listing(store, "pending")
    assert len(pend) == 5 and "5 proposal(s) created" in out and "accept them yourself" in out       # 4 contracts + the questions
    assert {p.file for p in pend} == {"contracts/fitclub.yaml", "contracts/cloudbox.yaml", "contracts/homesure-assurances.yaml",
                                      "contracts/sunpower-energie.yaml", "open-questions.yaml"}
    assert all(p.source == "cli" for p in pend)
    assert not (world.memory_dir / "contracts" / "fitclub.yaml").exists()                         # nothing applied
    capsys.readouterr()
    cli(world, "subs", "draft-contracts")                                                         # a second run: already proposed, nothing duplicated
    out = capsys.readouterr().out
    assert out.count("already proposed (pending)") == 4 and len(P.listing(MemoryStore(world.memory_dir, history=False), "pending")) == 5


def test_draft_contracts_write_previews_and_needs_a_typed_yes(world, capsys, monkeypatch):
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "draft-contracts", "--write", "--series", ref_of(jlist(world, capsys, "--all"), "Fitclub"))
    assert "needs a person at a terminal" in str(e.value) and not (world.memory_dir / "contracts" / "fitclub.yaml").exists()
    capsys.readouterr()
    tty(monkeypatch, ["n"])
    cli(world, "subs", "draft-contracts", "--write", "--series", ref_of(jlist(world, capsys, "--all"), "Fitclub"))
    out = capsys.readouterr().out
    assert "skipped" in out and "provider: Fitclub" in out and not (world.memory_dir / "contracts" / "fitclub.yaml").exists()      # the diff was shown, nothing written
    tty(monkeypatch, ["y"])
    cli(world, "subs", "draft-contracts", "--write", "--series", ref_of(jlist(world, capsys, "--all"), "Fitclub"))
    out = capsys.readouterr().out
    assert "1 contract file(s) written" in out
    store = MemoryStore(world.memory_dir, history=False)
    c = next(m for _r, m in store.contracts() if m.id == "fitclub")
    assert c.kind == "membership" and c.billing.period == "monthly"
    assert "fill:contract:fitclub" in {q.key for q in store.questions()}                          # an open question for the missing fields
    hist = MemoryStore(world.memory_dir, history=True).history(None, 10)
    assert any(h.source == "cli" and "drafted from recurring series" in (h.reason or "") for h in hist)
    inv = jlist(world, capsys)
    assert next(r for r in inv["rows"] if r["name"] == "Fitclub")["contract"]["status"] == "on_file"       # the regex links to the series


def test_draft_contracts_with_nothing_to_do(world, capsys, monkeypatch):
    tty(monkeypatch, ["y"] * 8)
    cli(world, "subs", "draft-contracts", "--write")
    capsys.readouterr()
    cli(world, "subs", "draft-contracts", "--dry-run")
    assert "no contract-like recurring series without a contract file" in capsys.readouterr().out


# ---------------------------------------------------------------- usage

def test_usage_is_previewed_validated_and_written(world, capsys, monkeypatch):
    cli(world, "subs", "usage", "telco", "--frequency", "rarely", "--last-used", "2026-09-01", "--note", "kids", "--dry-run")
    out = capsys.readouterr().out
    assert "frequency: rarely" in out and "(dry run: nothing written)" in out
    assert "usage:" not in (world.memory_dir / "contracts" / "telco.yaml").read_text()
    cli(world, "subs", "usage", "telco", "--frequency", "rarely", "--last-used", "2026-09-01", "--note", "kids", "--yes")
    c = next(m for _r, m in MemoryStore(world.memory_dir, history=False).contracts() if m.id == "telco")
    assert c.usage.frequency == "rarely" and c.usage.last_used == dt.date(2026, 9, 1)
    for bad in (["--frequency", "sometimes"],):
        with pytest.raises(SystemExit):
            cli(world, "subs", "usage", "telco", *bad)
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "usage", "telco", "--frequency", "weekly", "--last-used", "yesterday", "--yes")
    assert "YYYY-MM-DD" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "usage", "fitclub", "--frequency", "weekly", "--yes")
    assert "no subscription" in str(e.value) or "no contract file" in str(e.value)


def test_usage_for_a_series_without_a_contract_says_how_to_draft_one(world, capsys):
    ref = ref_of(jlist(world, capsys), "Fitclub")
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "usage", ref, "--frequency", "weekly", "--yes")
    assert "no contract file" in str(e.value) and "draft-contracts" in str(e.value)


def test_usage_questions_are_added_once(world, capsys):
    cli(world, "subs", "usage-questions")
    out = capsys.readouterr().out
    assert "2 usage question(s) to ask (0 already asked" in out and "--add" in out
    cli(world, "subs", "usage-questions", "--add")
    capsys.readouterr()
    cli(world, "subs", "usage-questions", "--add")
    assert "0 usage question(s) to ask (2 already asked" in capsys.readouterr().out
    qs = [q for q in MemoryStore(world.memory_dir, history=False).questions() if (q.key or "").startswith("usage:")]
    assert len(qs) == 2


# ---------------------------------------------------------------- alternatives

def test_alternatives_add_list_remove(world, capsys):
    cli(world, "subs", "alternatives", "add", "streambox", "--provider", "CheapStream", "--offer", "Basic", "--price", "8.99",
        "--url", "https://example.org/cheap", "--date", "2026-09-20", "--method", "find-cheaper")
    out = capsys.readouterr().out
    assert "stored alt_" in out and "8.99 EUR/month, seen 2026-09-20" in out
    cli(world, "subs", "alternatives", "list")
    out = capsys.readouterr().out
    # 12.99 - 8.99 = 4.00 a month = 48.00 a year; seen 14 days ago: current
    assert "StreamBox" in out and "saves 48.00/year" in out and "saves 48.00/yr, net 12m 48.00, break-even 0 mo" in out and "seen 14 day(s) ago" in out
    cli(world, "subs", "alternatives", "list", "--json")
    data = json.loads(capsys.readouterr().out)
    alt = next(iter(data.values()))["items"][0]
    cli(world, "subs", "alternatives", "remove", alt["id"])
    assert "removed" in capsys.readouterr().out
    cli(world, "subs", "alternatives", "list")
    assert "no alternatives stored" in capsys.readouterr().out


@pytest.mark.parametrize("extra,msg", [(["--price", "0"], "greater than 0"), (["--price", "5", "--url", "http://example.org/x"], "https"),
                                       (["--price", "5", "--date", "2099-01-01"], "in the future"), (["--price", "5", "--url", "https://10.0.0.1/x"], "host name")])
def test_alternatives_add_validation(world, capsys, extra, msg):
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "alternatives", "add", "streambox", "--provider", "P", "--offer", "O", *extra)
    assert msg in str(e.value)
    con = connect(world, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM alternatives").fetchone()[0] == 0                   # nothing stored
    con.close()


# ---------------------------------------------------------------- letters and contact

def test_contact_is_previewed_and_local(world, capsys, monkeypatch):
    cli(world, "subs", "contact", "show")
    assert "not set" in capsys.readouterr().out
    cli(world, "subs", "contact", "set", "--address", "12 rue de l'Exemple\\n59000 Montfort-Test", "--email", "jeanne@example.org", "--dry-run")
    assert "(dry run" in capsys.readouterr().out
    assert "contact" not in (world.memory_dir / "household.yaml").read_text()
    cli(world, "subs", "contact", "set", "--address", "12 rue de l'Exemple\\n59000 Montfort-Test", "--email", "jeanne@example.org", "--yes")
    capsys.readouterr()
    cli(world, "subs", "contact", "show")                                    # not a terminal (an agent-driven shell): the values stay hidden
    out = capsys.readouterr().out
    assert "set (address, email)" in out and "shown only in a terminal" in out and "Exemple" not in out and "jeanne" not in out
    tty(monkeypatch, [])
    cli(world, "subs", "contact", "show")
    out = capsys.readouterr().out
    assert "12 rue de l'Exemple\\n59000 Montfort-Test" in out and "jeanne@example.org" in out
    with pytest.raises(SystemExit):
        cli(world, "subs", "contact", "set")


def test_letter_prints_a_local_text_and_writes_nothing_else(world, capsys, tmp_path, monkeypatch):
    cli(world, "subs", "contact", "set", "--address", "12 rue de l'Exemple\\n59000 Montfort-Test", "--yes")
    capsys.readouterr()
    tty(monkeypatch, [])
    cli(world, "subs", "letter", "telco", "--lang", "en", "--channel", "email", "--holder", "luca")
    out = capsys.readouterr().out
    assert "Subject: cancellation of contract no. TC-778899" in out and "Luca Rossi" in out and "59000 Montfort-Test" in out
    assert "town" not in out.split("---")[1]                                                    # an e-mail has no city placeholder
    assert "text only: no PDF writer" in out and "Nothing was sent" in out
    cli(world, "subs", "letter", "telco", "--out", str(tmp_path / "l.txt"))
    assert "letter written" in capsys.readouterr().out
    text = (tmp_path / "l.txt").read_text(encoding="utf-8")
    assert "Lettre recommandée" in text and "TC-778899" in text and "Anna Rossi" in text            # default: French, registered letter, first adult
    cli(world, "subs", "letter", "telco", "--json")
    d = json.loads(capsys.readouterr().out)
    assert d["sent"] is False and d["lang"] == "fr" and d["channel"] == "lrar"
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "letter", ref_of(jlist(world, capsys), "Fitclub"))
    assert "no contract file" in str(e.value)


# ---------------------------------------------------------------- decisions and savings

def test_without_a_terminal_a_letter_carries_no_name_address_or_contract_number(world, capsys):
    """An agent-driven shell is not a terminal: the letter keeps [placeholders] where the household's private details would go."""
    cli(world, "subs", "contact", "set", "--address", "12 rue de l'Exemple\\n59000 Montfort-Test", "--email", "jeanne@example.org", "--yes")
    capsys.readouterr()
    for extra, marker in (([], "[titulaire du contrat]"), (["--channel", "email", "--lang", "en"], "[contract holder]"), (["--json"], '"private_data_included": false')):
        cli(world, "subs", "letter", "telco", *extra)
        out = capsys.readouterr().out
        for private in ("Anna", "Rossi", "Luca", "Exemple", "Montfort", "jeanne", "TC-778899"):
            assert private not in out, (extra, private)
        assert marker in out and "not run in a terminal" in out
        if not extra:
            assert "TelcoCo" in out                                                              # a business name is not private


def test_decide_previews_validates_and_records(world, capsys, monkeypatch):
    cli(world, "subs", "decide", "oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99", "--dry-run")
    out = capsys.readouterr().out
    assert "decision: ref=" in out and "after=0" in out and "(dry run: valid, nothing stored)" in out
    con = connect(world, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    con.close()
    with pytest.raises(SystemExit):                                                              # no terminal and no --yes: it asks the user
        cli(world, "subs", "decide", "oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99")
    with pytest.raises(SystemExit) as e:
        cli(world, "subs", "decide", "oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99", "--after", "1", "--yes")
    assert "costs 0" in str(e.value)
    capsys.readouterr()
    cli(world, "subs", "decide", "oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99", "--note", "unused", "--yes")
    out = capsys.readouterr().out
    assert "recorded dec_" in out and "monthly saving 3.99 EUR" in out
    con = connect(world, insecure=True)
    row = con.execute("SELECT decision, decided_on, before_monthly_c, after_monthly_c, source, state, note FROM decisions").fetchall()
    con.close()
    assert row == [("cancelled", "2026-01-10", 399, 0, "cli", "confirmed", "unused")]


def test_savings_shows_verified_pending_and_contradicted(world, capsys):
    for args in (("oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99"),
                 ("fitclub", "cancelled", "--date", "2026-08-20", "--effective", "2026-09-01", "--before", "39.90"),
                 ("cloudbox", "cancelled", "--date", "2026-10-03", "--before", "5.99")):
        cli(world, "subs", "decide", *args, "--yes")
    capsys.readouterr()
    cli(world, "subs", "savings")
    out = capsys.readouterr().out
    assert "3.99 EUR/month, 47.88 EUR/year run rate, 31.92 EUR since the decisions" in out
    assert "verified 1, pending 1 (claimed 5.99 EUR/month), contradicted 1" in out
    assert "latest 2026-09-12 (39.90)" in out
    cli(world, "subs", "savings", "--json")
    s = json.loads(capsys.readouterr().out)
    assert s["realised_monthly"] == "3.99" and {r["status"] for r in s["decisions"]} == {"verified", "pending", "contradicted"}
    cli(world, "subs", "decisions", "list")
    out = capsys.readouterr().out
    assert out.count("dec_") == 3 and "contradicted" in out
    cli(world, "subs", "list", "--all")
    assert "cancelled (verified)" in capsys.readouterr().out


def test_a_decision_proposed_by_the_coach_is_confirmed_only_in_a_terminal(world, capsys, monkeypatch):
    from coach.subs import decisions as DEC
    con = connect(world, insecure=True)
    d = DEC.add(con, decision="cancelled", today=TODAY, series_id="rec_x", name="Whatever", before=10, source="coach-llm")
    con.close()
    assert d.state == "proposed"
    cli(world, "subs", "decisions", "list")
    out = capsys.readouterr().out
    assert "PROPOSED by coach-llm" in out and f"coach subs decisions confirm {d.id}" in out
    with pytest.raises(SystemExit) as e:                                                          # no terminal: the agent cannot confirm its own proposal
        cli(world, "subs", "decisions", "confirm", d.id)
    assert "run it yourself in a terminal" in str(e.value)
    tty(monkeypatch, ["n"])
    cli(world, "subs", "decisions", "confirm", d.id)
    assert "skipped" in capsys.readouterr().out
    con = connect(world, insecure=True)
    assert DEC.get(con, d.id).state == "proposed"
    con.close()
    tty(monkeypatch, ["y"])
    cli(world, "subs", "decisions", "confirm", d.id)
    con = connect(world, insecure=True)
    assert DEC.get(con, d.id).state == "confirmed"
    con.close()
    cli(world, "subs", "decisions", "remove", d.id)
    assert "removed" in capsys.readouterr().out


def test_every_suggestion_in_the_cli_help_parses():
    from coach.cli import build_parser
    p = build_parser()
    for argv in (["subs", "list", "--json", "--all", "--group", "telecom", "--missing-contract"], ["subs", "show", "x", "--json"],
                 ["subs", "draft-contracts", "--dry-run"], ["subs", "draft-contracts", "--write", "--yes", "--series", "rec_1", "--limit", "2"],
                 ["subs", "usage", "x", "--frequency", "never", "--last-used", "2026-01-01", "--note", "n", "--dry-run", "--yes"],
                 ["subs", "usage-questions", "--add", "--limit", "3"], ["subs", "alternatives", "add", "x", "--provider", "p", "--offer", "o", "--price", "1"],
                 ["subs", "alternatives", "list", "--json"], ["subs", "alternatives", "remove", "alt_1"],
                 ["subs", "letter", "x", "--lang", "it", "--channel", "online", "--holder", "a", "--out", "f", "--json"],
                 ["subs", "contact", "show"], ["subs", "contact", "set", "--address", "a", "--email", "e", "--phone", "p", "--dry-run", "--yes"],
                 ["subs", "decide", "x", "kept", "--before", "1", "--after", "1", "--date", "2026-01-01", "--effective", "2026-01-02", "--note", "n", "--dry-run", "--yes"],
                 ["subs", "decisions", "list", "--json"], ["subs", "decisions", "confirm", "dec_1"], ["subs", "decisions", "reject", "dec_1"],
                 ["subs", "decisions", "remove", "dec_1"], ["subs", "savings", "--json"]):
        assert p.parse_args(argv).fn, argv
