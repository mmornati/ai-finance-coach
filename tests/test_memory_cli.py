"""E3-4 CLI: show / set / annotate / history / diff / revert / propose / members / totals, plus E2 compatibility."""
import json

import pytest

from coach.classify.parsers.common import household, load_member_aliases, load_members
from coach.classify.rules import annotation_for, annotation_mismatch, categorised, load_annotations
from coach.cli import build_parser, main
from coach.memory.store import MemoryStore
from helpers import add_bank, add_tx
from memhelpers import fake_tty, make_memory, make_world


@pytest.fixture
def world(cfg):
    con = make_world(cfg)
    return con, MemoryStore(cfg.memory_dir)


def run(cfg, *argv):
    main(["--config", str(cfg.config_path), "--insecure", *argv])


def out_of(capsys, cfg, *argv):
    run(cfg, *argv)
    return capsys.readouterr().out


# ---------------------------------------------------------------- show / set

def test_show_a_file_an_item_and_json(world, cfg, capsys):
    assert "id: kitchen-works" in out_of(capsys, cfg, "memory", "show", "categorization.yaml")
    out = out_of(capsys, cfg, "memory", "show", "kitchen-works")
    assert out.startswith("# categorization.yaml line 3 (kitchen-works)") and "category: housing.renovation" in out
    d = json.loads(out_of(capsys, cfg, "memory", "show", "home-loan", "--json"))
    assert d["file"] == "liabilities/home-loan.yaml" and d["data"]["monthly_payment"] == 1500
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "show", "nope")
    assert "no memory file or item" in str(e.value)


def test_set_by_id_by_file_and_unset_with_reason(world, cfg, capsys):
    out = out_of(capsys, cfg, "memory", "set", "savings-book", "balance", "5300", "--reason", "March statement")
    assert "+    balance: 5300" in out and "recorded as change" in out
    out_of(capsys, cfg, "memory", "set", "assets.yaml", "savings-book.as_of", "2026-09-30")
    out_of(capsys, cfg, "memory", "set", "home-loan", "rate.nominal", "1.8")
    hist = out_of(capsys, cfg, "memory", "history")
    assert "March statement" in hist and hist.count("coach: set") == 3
    assert "coach: set assets.yaml (savings-book.balance)" in hist


def test_set_validation_errors_are_clean_exits_and_write_nothing(world, cfg, capsys):
    before = (cfg.memory_dir / "assets.yaml").read_text()
    for argv in (["savings-book", "balance", "lots"], ["savings-book", "colour", "red"], ["ghost", "balance", "1"]):
        with pytest.raises(SystemExit) as e:
            run(cfg, "memory", "set", *argv)
        assert str(e.value).startswith("error:")
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "set", "savings-book", "balance")
    assert "a value is required" in str(e.value)
    assert (cfg.memory_dir / "assets.yaml").read_text() == before


def test_set_dry_run_and_new_field(world, cfg, capsys):
    out = out_of(capsys, cfg, "memory", "set", "savings-book", "balance", "1", "--dry-run")
    assert "dry run" in out and "balance: 5000" in (cfg.memory_dir / "assets.yaml").read_text()
    run(cfg, "memory", "set", "savings-book", "colour", "red", "--new-field")
    assert "colour: red" in (cfg.memory_dir / "assets.yaml").read_text()


# ---------------------------------------------------------------- annotate

def test_annotate_previews_then_writes_and_the_classifier_uses_it(world, cfg, capsys):
    con, _ = world
    out = out_of(capsys, cfg, "memory", "annotate", "--merchant-key", "^FRESH MARKET", "--category", "food.groceries",
                 "--tags", "one_off", "--note", "weekly market", "--date-from", "2026-02-01")
    assert "matches 4 transactions" in out and "total -160.00 EUR" in out and "Fresh Market x4" in out
    assert "recorded as change" in out and "+  - id: fresh-market-groceries" in out
    hit = [t for t in categorised(con, memory_dir=cfg.memory_dir) if t["tx_key"] == "fm4"][0]
    assert hit["source"] == "memory" and hit["tags"] == {"one_off"}


def test_annotate_dry_run_writes_nothing(world, cfg, capsys):
    before = (cfg.memory_dir / "categorization.yaml").read_text()
    out = out_of(capsys, cfg, "memory", "annotate", "--merchant-key", "^FRESH", "--category", "food.groceries", "--dry-run")
    assert "matches 5 transactions" in out and "dry run: nothing written" in out
    assert (cfg.memory_dir / "categorization.yaml").read_text() == before


@pytest.mark.parametrize("argv,msg", [
    (["--merchant-key", "^NOBODY$", "--category", "food.groceries"], "matches no transaction"),
    (["--merchant-key", "^FRESH", "--category", "not.a_category"], "unknown category"),
    (["--merchant-key", "^FRESH", "--category", "food.groceries", "--event", "ghost"], "event 'ghost' does not exist"),
    (["--merchant-key", "([", "--category", "food.groceries"], "invalid regular expression"),
    (["--category", "food.groceries"], "at least one criterion"),
    (["--merchant-key", "^FRESH"], "needs an effect"),
    (["--merchant-key", "^FRESH", "--category", "food.groceries", "--date-from", "02/2026"], "ISO"),
    (["--merchant-key", "^FRESH", "--category", "food.groceries", "--id", "kitchen-works"], "already exists"),
    (["--merchant-key", "^FRESH", "--category", "food.groceries", "--amount-min", "-5", "--amount-max", "-50"], "amount_min"),
    (["--merchant-key", "^FRESH", "--category", "food.groceries", "--weekdays", "funday"], "Input should be"),
])
def test_annotate_validation(world, cfg, argv, msg):
    before = (cfg.memory_dir / "categorization.yaml").read_text()
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "annotate", *argv)
    assert msg in str(e.value)
    assert (cfg.memory_dir / "categorization.yaml").read_text() == before


def test_annotate_reports_overlap_with_earlier_annotations(world, cfg, capsys):
    with pytest.raises(SystemExit) as e:                    # the preview is printed, then the write is refused
        run(cfg, "memory", "annotate", "--merchant-key", "^STREAMBOX", "--category", "leisure.hobbies", "--id", "late",
            "--dry-run")
    out = capsys.readouterr().out
    assert "already decided by earlier annotations (streambox-sub x12)" in out and "apply to 0 transaction" in out
    assert "would never apply" in str(e.value)
    run(cfg, "memory", "annotate", "--merchant-key", "^STREAMBOX", "--category", "leisure.hobbies", "--allow-empty")


def test_annotate_with_tx_keys_dates_amounts_and_event(world, cfg, capsys):
    out = out_of(capsys, cfg, "memory", "annotate", "--tx-key", "reno1", "--tx-key", "fm0", "--category", "housing.renovation",
                 "--event", "kitchen-2026", "--amount-max", "-30", "--date-to", "2026-12-31", "--id", "two-tx")
    assert "matches 2 transactions" in out and "tx_keys=['reno1', 'fm0']" in out
    text = (cfg.memory_dir / "categorization.yaml").read_text()
    assert "tx_keys: [reno1, fm0]" in text and "date_to: 2026-12-31" in text and "amount_max: -30.0" in text


def test_annotate_propose_queues_instead_of_writing(world, cfg, capsys):
    before = (cfg.memory_dir / "categorization.yaml").read_text()
    out = out_of(capsys, cfg, "memory", "annotate", "--merchant-key", "^FRESH", "--category", "food.groceries", "--propose",
                 "--reason", "user said it is groceries", "--source", "coach-llm")
    assert "proposal p-" in out and "nothing written yet" in out
    assert (cfg.memory_dir / "categorization.yaml").read_text() == before


# ---------------------------------------------------------------- history / diff / revert / proposals

def test_history_diff_revert_roundtrip(world, cfg, capsys):
    out_of(capsys, cfg, "memory", "set", "savings-book", "balance", "1")
    out = out_of(capsys, cfg, "memory", "history", "--json")
    cid = json.loads(out)[0]["id"]
    assert "balance: 1" in (cfg.memory_dir / "assets.yaml").read_text()
    assert "+    balance: 1" in out_of(capsys, cfg, "memory", "diff", cid)
    (cfg.memory_dir / "profile.md").write_text("changed by hand\n")
    assert "changed by hand" in out_of(capsys, cfg, "memory", "diff")
    assert "changed by hand" in out_of(capsys, cfg, "memory", "diff", "profile.md")
    out = out_of(capsys, cfg, "memory", "revert", cid)
    assert f"reverted {cid}" in out and "balance: 5000" in (cfg.memory_dir / "assets.yaml").read_text()
    assert "no unrecorded changes" in out_of(capsys, cfg, "memory", "diff")
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "revert", "deadbeef")
    assert "unknown change id" in str(e.value)


def test_history_on_a_fresh_folder_says_so(world, cfg, capsys):
    assert "no recorded changes yet" in out_of(capsys, cfg, "memory", "history")


def test_propose_proposals_accept_reject_via_cli(world, cfg, capsys, monkeypatch):
    fake_tty(monkeypatch, ["y"])
    out = out_of(capsys, cfg, "memory", "propose", "savings-book", "balance", "6100", "--reason", "user said", "--source", "coach-llm")
    assert "set assets[savings-book].balance: 5000 -> 6100" in out and "nothing was written" in out
    pid = out.split()[0]
    assert "balance: 5000" in (cfg.memory_dir / "assets.yaml").read_text()
    out = out_of(capsys, cfg, "memory", "proposals", "--json")
    items = json.loads(out)
    assert items[0]["id"] == pid and items[0]["status"] == "pending" and items[0]["source"] == "coach-llm"
    out_of(capsys, cfg, "memory", "propose", "savings-book", "provider", "Other Bank", "--reason", "r")
    pid2 = [p["id"] for p in json.loads(out_of(capsys, cfg, "memory", "proposals", "--json")) if p["id"] != pid][0]
    out = out_of(capsys, cfg, "memory", "accept", pid)
    assert "accepted" in out and "balance: 6100" in (cfg.memory_dir / "assets.yaml").read_text()
    out_of(capsys, cfg, "memory", "reject", pid2, "--note", "no")
    assert json.loads(out_of(capsys, cfg, "memory", "proposals", "--json")) == []
    assert len(json.loads(out_of(capsys, cfg, "memory", "proposals", "--all", "--json"))) == 2
    with pytest.raises(SystemExit):
        run(cfg, "memory", "accept", pid)
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "propose", "savings-book", "balance", "lots", "--reason", "r")
    assert str(e.value).startswith("error:")


def test_propose_append_and_remove_and_unset(world, cfg, capsys, monkeypatch):
    fake_tty(monkeypatch)
    out_of(capsys, cfg, "memory", "propose", "categorization.yaml", "annotations",
           '{id: from-llm, match: {merchant_key: "^X"}, category: food.groceries}', "--append", "--reason", "r")
    pid = json.loads(out_of(capsys, cfg, "memory", "proposals", "--json"))[0]["id"]
    run(cfg, "memory", "accept", pid)
    assert "from-llm" in (cfg.memory_dir / "categorization.yaml").read_text()
    out_of(capsys, cfg, "memory", "propose", "home-loan", "payment_match", "--unset", "--reason", "r")
    assert json.loads(out_of(capsys, cfg, "memory", "proposals", "--json"))[0]["ops"][0]["op"] == "unset"


# ---------------------------------------------------------------- members / totals

def test_member_add_creates_household_yaml_and_validates(cfg, capsys):
    make_memory(cfg.memory_dir, household=False)
    main(["--config", str(cfg.config_path), "memory", "member", "add", "--id", "pat", "--name", "Pat Smith",
          "--role", "adult", "--birth-year", "1975", "--alias", "M PAT SMITH"])
    text = (cfg.memory_dir / "household.yaml").read_text()
    assert text.startswith("members:\n  - id: pat\n    name: Pat Smith\n    role: adult\n    birth_year: 1975\n")
    with pytest.raises(SystemExit):
        main(["--config", str(cfg.config_path), "memory", "member", "add", "--id", "pat", "--name", "X", "--role", "adult"])
    with pytest.raises(SystemExit):
        main(["--config", str(cfg.config_path), "memory", "member", "add", "--id", "kid", "--name", "X", "--role", "robot"])


def test_totals_json_and_text(world, cfg, capsys):
    t = json.loads(out_of(capsys, cfg, "memory", "totals", "--json"))
    assert t["assets"]["total"] == 5000 and t["liabilities"]["outstanding_total"] == 180000
    out = out_of(capsys, cfg, "memory", "totals")
    assert "assets (manual): 5,000.00 EUR" in out and "complete: False" in out and "family-house" in out


# ---------------------------------------------------------------- compatibility with E2

def test_household_yaml_aliases_help_recognise_the_households_own_names(cfg):
    make_memory(cfg.memory_dir)
    assert load_members(cfg.memory_dir) == ["Anna Rossi", "Luca Rossi", "Mia Rossi"]       # names only, as before
    assert load_member_aliases(cfg.memory_dir) == ["MME ANNA ROSSI", "M OU MME ROSSI ANNA"]
    con = make_world(cfg, memory=False)
    h = household(con, cfg.memory_dir)
    assert {"LUCA", "ROSSI"} <= h.family and "LUCA" in h.first_names and "MME" not in h.first_names


def test_null_criteria_in_a_hand_written_annotation_do_not_crash_the_classifier(tmp_path):
    (tmp_path / "categorization.yaml").write_text(
        "annotations:\n  - id: a\n    match:\n      merchant_key: '^X'\n      amount_min: null\n    category: food.groceries\n")
    anns = load_annotations(tmp_path)
    assert anns[0]["match"] == {"merchant_key": "^X"}
    assert annotation_for(anns, "k", "X SHOP", "d", "2026-01-01", -5.0)["id"] == "a"


def test_annotation_mismatch_names_the_failing_criterion():
    a = {"id": "a", "match": {"merchant_key": "^X", "date_from": "2026-02-01", "amount_max": -10}}
    assert annotation_mismatch(a, "k", "X SHOP", "d", "2026-03-01", -20) is None
    assert "date 2026-01-01 is before date_from" in annotation_mismatch(a, "k", "X SHOP", "d", "2026-01-01", -20)
    assert "amount -5 is above amount_max" in annotation_mismatch(a, "k", "X SHOP", "d", "2026-03-01", -5)
    assert "merchant_key" in annotation_mismatch(a, "k", "Y", "d", "2026-03-01", -20)


def test_the_new_commands_all_parse():
    p = build_parser()
    for argv in (["memory", "show", "x"], ["memory", "set", "a", "b", "c"], ["memory", "annotate", "--category", "a.b"],
                 ["memory", "history"], ["memory", "diff"], ["memory", "revert", "abc1"],
                 ["memory", "propose", "f", "p", "v", "--reason", "r"], ["memory", "proposals", "--json"],
                 ["memory", "accept", "p-1"], ["memory", "reject", "p-1"], ["memory", "check", "--json"],
                 ["memory", "context", "--max-tokens", "100", "--names"], ["memory", "totals"],
                 ["memory", "migrate-questions", "--write"], ["memory", "member", "add", "--id", "a", "--name", "A", "--role", "child"],
                 ["memory", "doc", "add", "f", "--kind", "loan", "--for", "x"], ["memory", "doc", "list"],
                 ["memory", "doc", "extract", "d", "--into", "liability", "x", "--send"],
                 ["questions", "list", "--open", "--json"], ["questions", "answer", "q", "t"], ["questions", "dismiss", "q"],
                 ["questions", "reopen", "q"], ["questions", "add", "t"], ["questions", "generate", "--dry-run"],
                 ["explain", "x", "--json"]):
        assert callable(p.parse_args(argv).fn)


def test_append_text_to_markdown_and_propose_it(world, cfg, capsys):
    out = out_of(capsys, cfg, "memory", "append", "events.md", "## move-2027\n\n- **What:** moving.", "--reason", "user plans a move")
    assert "+## move-2027" in out and "recorded as change" in out
    assert "move-2027" in MemoryStore(cfg.memory_dir).event_ids()
    # the new event can now be referenced by an annotation
    run(cfg, "memory", "annotate", "--merchant-key", "^FRESH", "--category", "food.groceries", "--event", "move-2027")
    before = (cfg.memory_dir / "profile.md").read_text()
    out = out_of(capsys, cfg, "memory", "propose", "profile.md", "--append-text", "- A new fact.", "--reason", "chat")
    assert "append_text" in out or "nothing was written" in out
    assert (cfg.memory_dir / "profile.md").read_text() == before
    with pytest.raises(SystemExit):
        run(cfg, "memory", "append", "assets.yaml", "x")
    with pytest.raises(SystemExit):
        run(cfg, "memory", "append", "open-questions.md", "x")


def test_new_liability_and_contract_files(world, cfg, capsys):
    out = out_of(capsys, cfg, "memory", "new", "liability", "car-loan", "--kind", "loa")
    assert "recorded as change" in out
    p = cfg.memory_dir / "liabilities" / "car-loan.yaml"
    assert p.exists() and "kind: loa" in p.read_text() and "monthly_payment: null" in p.read_text()
    run(cfg, "memory", "set", "car-loan", "monthly_payment", "250")
    run(cfg, "memory", "new", "contract", "power", "--kind", "energy")
    assert "kind: energy" in (cfg.memory_dir / "contracts" / "power.yaml").read_text()
    with pytest.raises(SystemExit):
        run(cfg, "memory", "new", "liability", "car-loan", "--kind", "loa")           # exists
    with pytest.raises(SystemExit):
        run(cfg, "memory", "new", "liability", "x", "--kind", "yacht")
