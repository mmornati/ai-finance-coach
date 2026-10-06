"""E14 CLI: `coach household ...`, `coach users ...`, the account owner check, the memory check of the household rules and the transfer
window setting. Writes preview first; only a terminal and a typed yes write (or --propose queues a proposal, --dry-run writes nothing)."""
from __future__ import annotations

import json

import pytest

from coach import db as dbm
from coach.cli import main
from coach.household import users as users_mod
from coach.memory import check as check_mod
from coach.memory.store import MemoryStore
from hhhelpers import TODAY, hh_world, match_all
from memhelpers import fake_tty


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr("coach.household.commands._today", lambda: TODAY)


@pytest.fixture
def world(cfg):
    con = hh_world(cfg)
    match_all(con, cfg)
    con.close()
    return cfg


def run(cfg, capsys, *argv, expect_exit=False):
    args = ["--insecure", "--config", str(cfg.config_path), *argv]
    if expect_exit:
        with pytest.raises(SystemExit) as e:
            main(args)
        return capsys.readouterr().out + str(e.value)
    main(args)
    return capsys.readouterr().out


def js(cfg, capsys, *argv):
    return json.loads(run(cfg, capsys, *argv, "--json"))


def hh_text(cfg) -> str:
    return (cfg.memory_dir / "household.yaml").read_text()


# ---------------------------------------------------------------- show, accounts

def test_household_show_lists_members_owners_and_counts(world, capsys):
    d = js(world, capsys, "household", "show")
    assert [m["id"] for m in d["members"]] == ["anna", "luca", "mia", "noa"]
    owners = {a["label"]: a["owner_member"] for a in d["accounts"]}
    assert "mia" in owners.values() and "anna" in owners.values() and "joint" in owners.values()
    assert d["transactions_by_person"]["mia"] > 0 and d["manual_reassignments"] == 0
    out = run(world, capsys, "household", "show")
    assert "mia" in out and "transactions by person" in out and "joint" in out


def test_accounts_set_checks_the_owner_against_the_members_and_stores_the_id(world, capsys):
    out = run(world, capsys, "accounts", "set", "lu", "--owner", "Luca Rossi", "--purpose", "main")
    assert "luca" in out
    bad = run(world, capsys, "accounts", "set", "lu", "--owner", "Nobody Known", expect_exit=True)
    assert "not 'joint' nor a household member" in bad or "neither 'joint' nor a household member" in bad
    assert "joint" in run(world, capsys, "accounts", "set", "lu", "--owner", "joint")


# ---------------------------------------------------------------- assignment (E14-3)

def test_assign_why_log_undo_and_unassign(world, capsys):
    out = run(world, capsys, "household", "assign", "mc2", "luca", "--note", "he paid")
    assert "reassigned: mc2 -> luca" in out
    assert "already assigned" in run(world, capsys, "household", "assign", "mc2", "luca")
    why = js(world, capsys, "household", "why", "mc2")
    assert why["person"] == "luca" and why["source"] == "manual" and why["manual"]["note"] == "he paid" and why["card_last4"] == "1111"
    assert "MANUAL" in run(world, capsys, "household", "why", "mc2").upper()
    log = js(world, capsys, "household", "log", "mc2")
    assert log[0]["action"] == "set" and log[0]["by"] == "cli"
    assert "decided by the rules and the owner again" in run(world, capsys, "household", "undo", str(log[0]["id"]))
    assert js(world, capsys, "household", "why", "mc2")["person"] == "joint"
    run(world, capsys, "household", "assign", "mc2", "anna")
    assert "manual reassignment removed" in run(world, capsys, "household", "unassign", "mc2")
    assert "there was no manual reassignment" in run(world, capsys, "household", "unassign", "mc2")
    assert "not a household member" in run(world, capsys, "household", "assign", "mc2", "ghost", expect_exit=True)


# ---------------------------------------------------------------- rules (preview first, terminal + typed yes, or a proposal)

def test_rule_add_previews_and_needs_a_terminal_or_a_proposal(world, capsys, monkeypatch):
    argv = ("household", "rule", "add", "games", "--member", "mia", "--merchant", "^CARTE JEUXVIDEO", "--note", "her games")
    dry = run(world, capsys, *argv, "--dry-run")
    assert "attribution:" in dry and "dry run" in dry and "attribution" not in hh_text(world)
    no_tty = run(world, capsys, *argv)
    assert "not an interactive terminal" in no_tty and "attribution" not in hh_text(world)
    prop = run(world, capsys, *argv, "--propose")
    assert "queued as proposal" in prop and "attribution" not in hh_text(world)                # the coach's door: nothing is written
    fake_tty(monkeypatch, ["y"])
    out = run(world, capsys, *argv)
    assert "written" in out and "id: games" in hh_text(world)
    assert "games" in run(world, capsys, "household", "rule", "list")
    fake_tty(monkeypatch, ["n"])
    run(world, capsys, "household", "rule", "remove", "games")
    assert "id: games" in hh_text(world)                                                         # the answer was no
    fake_tty(monkeypatch, ["y"])
    run(world, capsys, "household", "rule", "remove", "games")
    assert "id: games" not in hh_text(world)
    assert "no such item" in run(world, capsys, "household", "rule", "remove", "games", expect_exit=True)


def test_rule_with_no_condition_or_an_unknown_member_is_refused(world, capsys):
    assert "needs at least one condition" in run(world, capsys, "household", "rule", "add", "x", "--member", "mia", "--dry-run", expect_exit=True)
    out = run(world, capsys, "household", "rule", "add", "x", "--member", "ghost", "--account", "fo", "--dry-run", expect_exit=True)
    assert "not a household member" in out


def test_kid_budget_pocket_money_and_allocation_commands(world, capsys, monkeypatch):
    fake_tty(monkeypatch, ["y", "y", "y"])
    assert "written" in run(world, capsys, "household", "budget", "set", "mia-weekly", "--member", "mia", "--period", "weekly", "--limit", "20")
    assert "written" in run(world, capsys, "household", "pocket", "set", "mia", "--amount", "10", "--period", "monthly", "--day", "5")
    assert "written" in run(world, capsys, "household", "allocate", "set", "groceries", "--category", "food.groceries", "--method", "equal")
    txt = hh_text(world)
    assert "kid_budgets:" in txt and "pocket_money" in txt and "allocations:" in txt
    b = js(world, capsys, "household", "budget", "list")
    assert b[0]["id"] == "mia-weekly" and b[0]["status"] == "ok" and b[0]["spent"] == "5.00"
    al = js(world, capsys, "household", "allocation")
    assert al["rules"][0]["id"] == "groceries" and "settlement" in run(world, capsys, "household", "allocation")
    bad = run(world, capsys, "household", "allocate", "set", "c", "--category", "leisure.cinema_events", "--method", "custom", "--share", "anna=60",
              "--share", "luca=30", "--dry-run", expect_exit=True)
    assert "add up to 100" in bad
    assert "MEMBER=PERCENT" in run(world, capsys, "household", "allocate", "set", "c", "--category", "leisure.cinema_events", "--method", "custom",
                                   "--share", "oops", expect_exit=True)
    fake_tty(monkeypatch, ["y", "y"])
    run(world, capsys, "household", "budget", "remove", "mia-weekly")
    run(world, capsys, "household", "allocate", "remove", "groceries")
    assert "mia-weekly" not in hh_text(world) and "groceries" not in hh_text(world)


def test_kids_command_prints_pocket_money_extras_and_the_ratio(world, capsys):
    out = run(world, capsys, "household", "kids", "mia")
    assert "pocket money: 10.00 EUR monthly from anna" in out and "extra top-ups: 55.00 EUR in 2 credit(s)" in out
    assert "pocket money vs extra: 52 % pocket money, 48 % extra" in out and "balance: 57.00 EUR" in out
    d = js(world, capsys, "household", "kids", "--months", "3")
    assert [c["member"] for c in d] == ["mia", "noa"]
    assert "not a household member" in run(world, capsys, "household", "kids", "ghost", expect_exit=True)


# ---------------------------------------------------------------- users (E14-8): terminal only

def test_users_lifecycle_through_the_cli(world, capsys):
    assert "no login" in run(world, capsys, "users", "list")
    assert "created (adult, member luca)" in run(world, capsys, "users", "add", "papa", "--role", "adult", "--member", "luca")
    out = run(world, capsys, "users", "add", "mia-kid", "--role", "child", "--member", "mia")
    assert "created (child, member mia)" in out and "ui --login-link --user mia-kid" in out
    for args, msg in ((("add", "x", "--role", "child"), "needs --member"), (("add", "y", "--role", "child", "--member", "luca"), "not declared as a child"),
                      (("add", "owner", "--role", "adult"), "reserved"), (("add", "root", "--role", "adult"), "reserved"), (("add", "coach-llm", "--role", "adult"), "reserved"), (("add", "Bad Id", "--role", "adult"), "login id"),
                      (("add", "papa", "--role", "adult"), "already exists"), (("add", "z", "--role", "adult", "--member", "ghost"), "not a household member"),
                      (("disable", "ghost"), "no login")):
        assert msg in run(world, capsys, "users", *args, expect_exit=True), args
    assert {u["id"] for u in js(world, capsys, "users", "list")} == {"papa", "mia-kid"}
    assert "disabled" in run(world, capsys, "users", "disable", "mia-kid")
    assert "DISABLED" in run(world, capsys, "users", "list")
    run(world, capsys, "users", "enable", "mia-kid")
    assert "is now adult" in run(world, capsys, "users", "set-role", "mia-kid", "--role", "adult")
    assert "child login needs" in run(world, capsys, "users", "set-role", "papa", "--role", "child", expect_exit=True)
    run(world, capsys, "users", "set-role", "mia-kid", "--role", "child", "--member", "mia")
    out = run(world, capsys, "users", "prefs", "mia-kid", "--set", "theme=dark")
    assert json.loads(out) == {"theme": "dark"}
    assert "unknown preference" in run(world, capsys, "users", "prefs", "mia-kid", "--set", "colour=red", expect_exit=True)
    audit = js(world, capsys, "users", "audit")
    assert audit["requests"] == [] and isinstance(audit["memory"], list)               # the CLI writes no web audit line
    run(world, capsys, "users", "remove", "mia-kid")
    assert [u["id"] for u in js(world, capsys, "users", "list")] == ["papa"]


def test_the_web_app_cannot_create_a_login_but_the_cli_can(world):
    con = dbm.connect(world, insecure=True)
    from coach.household import people as people_mod
    people = people_mod.load(MemoryStore(world.memory_dir, history=False))
    assert users_mod.get(con, "nope") is None
    assert users_mod.add(con, people, "papa", "adult").member_id is None
    assert users_mod.audit_rows(con) == []


# ---------------------------------------------------------------- memory check and transfers config

def test_memory_check_reports_the_household_rule_problems(world):
    yml = hh_text(world) + ("attribution:\n  - id: ghost-rule\n    member: ghost\n    match: { account: nowhere }\n"
                            "kid_budgets:\n  - id: kb\n    member: mia\n    period: weekly\n    limit: 5\n    category: nothing.here\n")
    (world.memory_dir / "household.yaml").write_text(yml)
    store = MemoryStore(world.memory_dir, history=False)
    con = dbm.connect(world, insecure=True)
    issues = check_mod.run_check(store, con, stale_months=6, asset_stale_months=3, cfg=world)
    codes = {i.code for i in issues}
    assert {"unknown_member", "unknown_category", "attribution_unknown_account"} <= codes


def test_the_cross_bank_window_is_configurable_and_defaults_to_five(cfg):
    assert cfg.transfer_cross_bank_window_days == 5
    cfg.config_path.write_text(cfg.config_path.read_text() + "\n[transfers]\ncross_bank_window_days = 7\n")
    from coach.config import load_config
    assert load_config(cfg.config_path, env={}).transfer_cross_bank_window_days == 7
