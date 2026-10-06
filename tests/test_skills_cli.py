"""E7-6 / E7-11: `coach onboarding status|run` and `coach contract check` - the interactive interview previews every change and writes
only after a typed yes (source cli); it refuses to run without a terminal."""
from __future__ import annotations

import json

import pytest

from coach.cli import main
from coach.db import connect
from coach.memory.store import MemoryStore
from mcphelpers import world  # noqa: F401
from memhelpers import fake_tty


def cli(cfg, *argv):
    main(["--insecure", "--config", str(cfg.config_path), *argv])


def scripted(monkeypatch, answers: dict, default=""):
    """A terminal whose typed answers are chosen by a word of the prompt; every prompt is recorded."""
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        hits = [k for k in answers if k.lower() in prompt.lower()]
        return answers[max(hits, key=len)] if hits else default            # the most specific word of the prompt wins
    monkeypatch.setattr("coach.memory.commands._is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)
    return seen


def test_onboarding_status_lists_steps_and_next_actions(cfg, world, capsys):
    cli(cfg, "onboarding", "status")
    out = capsys.readouterr().out
    assert "steps done" in out and "[ ] household" in out or "[~] household" in out
    assert "insurance.monthly" in out and "uv run coach loans edit <liability-id>" in out and "coach loans infer" in out and "next actions" in out
    cli(cfg, "onboarding", "status", "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["progress"]["total"] == 7 and {s["id"] for s in data["steps"]} >= {"household", "loans", "contracts"}


def test_the_interview_refuses_to_run_without_a_terminal(cfg, world, capsys):
    with pytest.raises(SystemExit) as e:
        cli(cfg, "onboarding", "run")
    assert "interactive" in str(e.value) and "onboarding status" in str(e.value)


def test_the_interview_previews_every_change_and_writes_after_a_typed_yes(cfg, world, monkeypatch, capsys):
    (cfg.memory_dir / "household.yaml").write_text("members:\n  - id: anna\n    name: Anna Rossi\n    role: adult\n    birth_year: 1984\n"
                                                   "  - id: mia\n    name: Mia Rossi\n    role: child\n    birth_year: 2012\n")
    seen = scripted(monkeypatch, {"write this change": "y", "Country": "IT", "employer": "Acme Corp", "town": "Lillebourg",
                                  "school": "Ecole Saint Exupery", "insurance.monthly": "60", "Language": "fr", "Tone": "short and direct",
                                  "add them to the open questions": "n"})
    cli(cfg, "onboarding", "run")
    out = capsys.readouterr().out
    assert "written only after you type y" in out and "== Household ==" in out and "== Loans" in out and "== Check ==" in out
    assert "memory check:" in out and "0 error(s)" in out
    assert out.count("write this change?") == 0 and any("write this change?" in p for p in seen)           # the prompt is the question, not the output
    store = MemoryStore(cfg.memory_dir, history=False)
    hh = store.load_plain("household.yaml")
    assert hh["country"] == "IT" and hh["employers"] == ["Acme Corp"] and hh["places"] == ["Lillebourg"] and hh["schools"] == ["Ecole Saint Exupery"]
    loan = store.liabilities()[0][1]
    assert loan.insurance.monthly == 60
    prefs = (cfg.memory_dir / "preferences.md").read_text()
    assert "- Language: fr" in prefs and "- Tone: short and direct" in prefs
    hist = MemoryStore(cfg.memory_dir, history=True).history(None, 20)
    mine = [c for c in hist if c.source == "cli"]
    assert len(mine) >= 6 and all("onboarding" in (c.reason or "") for c in mine)               # country, employers, places, schools, loan, preferences
    assert "+  country: IT" in out or "country: IT" in out                              # the diff was shown first


def test_declining_the_preview_writes_nothing(cfg, world, monkeypatch, capsys):
    before = {p.name: p.read_text() for p in cfg.memory_dir.rglob("*.yaml")} | {"p": (cfg.memory_dir / "preferences.md").read_text()}
    scripted(monkeypatch, {"write this change": "n", "Country": "IT", "insurance.monthly": "60", "Language": "fr", "add them": "n"})
    cli(cfg, "onboarding", "run")
    out = capsys.readouterr().out
    assert "skipped" in out
    after = {p.name: p.read_text() for p in cfg.memory_dir.rglob("*.yaml")} | {"p": (cfg.memory_dir / "preferences.md").read_text()}
    assert after == before


def test_the_interview_sets_account_owner_and_purpose_through_the_accounts_command(cfg, world, monkeypatch, capsys):
    world.execute("UPDATE accounts SET owner=NULL, purpose=NULL WHERE uid='rl'")
    world.commit()
    scripted(monkeypatch, {"owner": "mia", "purpose": "kids", "set owner": "y", "add them": "n"})
    cli(cfg, "onboarding", "run")
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT owner, purpose FROM accounts WHERE uid='rl'").fetchone() == ("mia", "kids")


def test_the_interview_can_add_the_first_member_and_can_add_the_generated_questions(cfg, tmp_path, world, monkeypatch, capsys):
    (cfg.memory_dir / "household.yaml").unlink()
    scripted(monkeypatch, {"Member id": "adult-a", "display name": "Pat Example", "role": "adult", "birth year": "1985", "write this change": "y",
                           "add them to the open questions": "y"})
    # one member only: the id prompt is asked again after the first member, answer blank the second time
    answers = iter(["adult-a", "", ""])
    orig = __import__("builtins").input

    def fake(prompt=""):
        if "Member id" in prompt:
            return next(answers)
        return orig(prompt)
    monkeypatch.setattr("builtins.input", fake)
    cli(cfg, "onboarding", "run")
    store = MemoryStore(cfg.memory_dir, history=False)
    m = store.members()
    assert [(x.id, x.role, x.birth_year) for x in m] == [("adult-a", "adult", 1985)]
    assert len(store.questions()) >= 1 and all(q.status == "open" for q in store.questions())


# ---------------------------------------------------------------- contract check

def test_contract_check_prints_the_cancellability_of_the_contracts_on_file(cfg, world, capsys):
    mem = cfg.memory_dir
    (mem / "contracts").mkdir(exist_ok=True)
    (mem / "contracts" / "car.yaml").write_text("id: car-cover\nprovider: SafeCar\nkind: insurance_car\nstart_date: 2024-01-10\nrenewal: 2027-01-10\n"
                                               "notice_period_days: 30\n")
    (mem / "contracts" / "net.yaml").write_text("id: home-net\nprovider: NetCo\nkind: telecom\nstart_date: 2025-06-01\ncommitment_end: 2099-01-01\n")
    cli(cfg, "contract", "check")
    out = capsys.readouterr().out
    assert "car-cover (SafeCar, insurance_car): can cancel: YES, now" in out
    assert "home-net (NetCo, telecom): can cancel: YES, now" in out and "early exit costs" in out and "free from 2099-01-01" in out
    assert "Loi Hamon" in out and "Loi Chatel" in out and "verify with your contract" in out.lower()
    cli(cfg, "contract", "check", "car-cover", "--country", "IT")
    it = capsys.readouterr().out
    assert "RC auto" in it and "home-net" not in it
    with pytest.raises(SystemExit) as e:
        cli(cfg, "contract", "check", "nope")
    assert "no contract on file" in str(e.value)


def test_contract_check_without_any_contract_says_how_to_add_one(cfg, world):
    with pytest.raises(SystemExit) as e:
        cli(cfg, "contract", "check")
    assert "memory new contract" in str(e.value)
