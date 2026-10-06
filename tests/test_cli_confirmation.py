"""MJ-1: `--yes` only skips the typed prompt for a person at a terminal. No abbreviation of a flag is accepted (`--ye` / `--y` are not `--yes`),
and without a terminal (stdin and stdout) every memory-writing command refuses, whatever it is given."""
from __future__ import annotations

import pytest

from coach.cli import build_parser, main
from coach.memory.store import MemoryStore
from mcphelpers import world  # noqa: F401
from subshelpers import TODAY, build_subs_world


def run(cfg, *argv):
    main(["--insecure", "--config", str(cfg.config_path), *argv])


def snapshot(cfg):
    out = {}
    for p in sorted(cfg.memory_dir.rglob("*")):
        if p.is_file() and ".history.git" not in p.parts and ".proposals" not in p.parts:
            out[str(p)] = p.read_bytes()
    return out


LOAN_WRITES = [
    ("loans", "add", "newl", "--kind", "car_loan", "--set", "lender=X"),
    ("loans", "edit", "home-loan", "--set", "payment_day=3"),
    ("loans", "odometer", "home-loan", "--km", "5"),
    ("budget", "set", "food.groceries", "300"),
    ("goals", "set", "g1", "--target", "100", "--tag", "savings"),
]


@pytest.mark.parametrize("flag", ["--yes", "--ye", "--y"])
@pytest.mark.parametrize("cmd", LOAN_WRITES, ids=lambda c: " ".join(c[:2]))
def test_no_form_of_yes_writes_without_a_terminal(cfg, world, capsys, cmd, flag):
    before = snapshot(cfg)
    try:
        run(cfg, *cmd, flag)
    except SystemExit:
        pass                                                   # an abbreviation is a parse error (exit 2); --yes is a refusal
    assert snapshot(cfg) == before, (cmd, flag)


@pytest.mark.parametrize("flag", ["--ye", "--y", "--ye=1"])
def test_abbreviated_flags_are_parse_errors_on_every_level(cfg, world, capsys, flag):
    for cmd in LOAN_WRITES[:3]:
        with pytest.raises(SystemExit) as e:
            run(cfg, *cmd, flag)
        assert e.value.code == 2, (cmd, flag)
        assert "unrecognized arguments" in capsys.readouterr().err


def test_every_parser_in_the_tree_disallows_abbreviations():
    import argparse
    seen = []

    def walk(p):
        seen.append(p)
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for sp in a.choices.values():
                    walk(sp)
    walk(build_parser())
    assert len(seen) > 100 and all(p.allow_abbrev is False for p in seen)


def test_the_refusal_says_what_to_do(cfg, world, capsys):
    with pytest.raises(SystemExit) as e:
        run(cfg, "loans", "edit", "home-loan", "--set", "payment_day=3", "--yes")
    assert "run this yourself in a terminal" in str(e.value) and "--propose" in str(e.value)
    run(cfg, "budget", "set", "food.groceries", "300", "--yes")
    assert "nothing written" in capsys.readouterr().out
    assert not (cfg.memory_dir / "budgets.yaml").exists()


@pytest.fixture
def subs_world(cfg, monkeypatch):
    monkeypatch.setattr("coach.subs.commands._today", lambda: TODAY)
    build_subs_world(cfg).close()
    return cfg


SUBS_WRITES = [
    ("subs", "draft-contracts", "--write"),
    ("subs", "usage", "telco", "--frequency", "weekly"),
    ("subs", "contact", "set", "--address", "1 rue Test"),
    ("subs", "decide", "oldapp", "cancelled", "--date", "2026-01-10", "--before", "3.99", "--after", "1"),
]


@pytest.mark.parametrize("flag", ["--yes", "--ye", "--y"])
@pytest.mark.parametrize("cmd", SUBS_WRITES, ids=lambda c: " ".join(c[:3]))
def test_subs_writes_refuse_every_form_of_yes_without_a_terminal(subs_world, capsys, cmd, flag):
    before = snapshot(subs_world)
    with pytest.raises(SystemExit):
        run(subs_world, *cmd, flag)
    assert snapshot(subs_world) == before


@pytest.mark.parametrize("cmd,path", [(("budget", "set", "food.groceries", "300"), "budgets.yaml"), (("goals", "set", "g1", "--target", "100", "--tag", "savings"), "goals.yaml"),
                                      (("loans", "odometer", "home-loan", "--km", "5"), None)])
def test_positive_control_at_a_terminal_yes_does_write(cfg, world, monkeypatch, cmd, path):
    """The refusals above are not vacuous: with a terminal, --yes writes."""
    monkeypatch.setattr("coach.memory.commands._is_tty", lambda: True)
    before = snapshot(cfg)
    try:
        run(cfg, *cmd, "--yes")
    except SystemExit as e:
        assert cmd[0] == "loans"                              # a non-lease has no odometer: refused for its own reason, not the terminal
    assert (snapshot(cfg) != before) == (path is not None)
