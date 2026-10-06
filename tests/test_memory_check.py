"""E3-6 / E3-8: `coach memory check`, stale-value reminders, manual totals, health and schedule integration."""
import datetime as dt
import json

import pytest

from coach.cli import main
from coach.memory import check as C, totals
from coach.memory.store import MemoryStore
from helpers import add_tx
from memhelpers import CATEGORIZATION, TODAY, make_memory, make_world, monthly


@pytest.fixture
def world(cfg):
    con = make_world(cfg)
    return con, MemoryStore(cfg.memory_dir)


def run_check(store, con=None, **kw):
    return C.run_check(store, con, today=TODAY, **kw)


def codes(issues, level=None):
    return [i.code for i in issues if level in (None, i.level)]


def by_code(issues, code):
    return [i for i in issues if i.code == code]


def test_a_clean_world_has_no_errors_or_warnings(world):
    con, store = world
    issues = run_check(store, con)
    assert not codes(issues, "error")
    assert sorted(codes(issues, "warning")) == ["places_not_declared", "stale_fact", "stale_value"]      # the two outdated figures, and the undeclared towns


# ---------------------------------------------------------------- schema / syntax

def test_schema_error_names_file_path_and_line(world, cfg):
    con, store = world
    (cfg.memory_dir / "assets.yaml").write_text("assets:\n  - id: a\n    kind: other\n    balance: lots\n")
    i = by_code(run_check(store, con), "schema")[0]
    assert i.level == "error" and i.file == "assets.yaml" and i.path == "assets[a].balance" and i.line == 4


def test_yaml_syntax_error_is_an_error_and_does_not_hide_other_files(world, cfg):
    con, store = world
    (cfg.memory_dir / "liabilities" / "home-loan.yaml").write_text("id: [broken\n")
    issues = run_check(store, con)
    assert by_code(issues, "yaml_syntax")[0].file == "liabilities/home-loan.yaml"
    assert not by_code(issues, "schema")                                      # the other files are still checked


def test_unknown_fields_are_info(world, cfg):
    con, store = world
    store.set_value("savings-book", "colour", "red", allow_new_fields=True)
    i = by_code(run_check(store, con), "unknown_field")
    assert i and i[0].level == "info" and "colour" in i[0].message


# ---------------------------------------------------------------- annotations

def put_annotations(cfg, body):
    (cfg.memory_dir / "categorization.yaml").write_text("annotations:\n" + body)


def test_unknown_category_is_an_error_and_unknown_event_a_warning(world, cfg):
    con, store = world
    put_annotations(cfg, "  - id: a\n    match: {merchant_key: '^STREAM'}\n    category: no.such\n    event: ghost\n")
    issues = run_check(store, con)
    assert by_code(issues, "unknown_category")[0].level == "error"
    assert by_code(issues, "unknown_event")[0].level == "warning"


def test_annotation_matching_nothing_is_a_warning(world, cfg):
    con, store = world
    put_annotations(cfg, "  - id: ghost\n    match: {merchant_key: '^NOBODY$'}\n    category: food.groceries\n")
    i = by_code(run_check(store, con), "annotation_matches_nothing")
    assert i[0].level == "warning" and i[0].path == "annotations[ghost]"


def test_annotation_matching_too_much_of_an_account(world, cfg):
    con, store = world
    put_annotations(cfg, "  - id: wide\n    match: {amount_max: 0}\n    category: food.groceries\n")
    i = by_code(run_check(store, con), "annotation_too_broad")
    assert i and "%" in i[0].message


def test_shadowed_annotation_is_reported_with_the_shadowing_one(world, cfg):
    con, store = world
    put_annotations(cfg, "  - id: first\n    match: {merchant_key: '^STREAM'}\n    category: food.groceries\n"
                         "  - id: second\n    match: {merchant_key: '^STREAMBOX$'}\n    category: subscriptions.video_streaming\n")
    i = by_code(run_check(store, con), "annotation_shadowed")
    assert i[0].path == "annotations[second]" and "first (12)" in i[0].message
    # partial overlap is only information
    put_annotations(cfg, "  - id: first\n    match: {merchant_key: '^STREAMBOX$', date_to: 2026-01-01}\n    category: food.groceries\n"
                         "  - id: second\n    match: {merchant_key: '^STREAMBOX$'}\n    category: subscriptions.video_streaming\n")
    issues = run_check(store, con)
    assert not by_code(issues, "annotation_shadowed") and by_code(issues, "annotation_overlap")[0].level == "info"


def test_tx_keys_not_found_or_remapped(world, cfg):
    con, store = world
    con.execute("INSERT INTO tx_key_remap VALUES ('old-key','stream00','test','t')")
    con.commit()
    put_annotations(cfg, "  - id: t\n    match: {tx_keys: [stream01, old-key, nope-key]}\n    category: food.groceries\n")
    issues = run_check(store, con)
    assert "nope-key" in by_code(issues, "tx_key_not_found")[0].message
    assert "stream00" in by_code(issues, "tx_key_remapped")[0].message


def test_events_defined_but_unused_are_info(world, cfg):
    con, store = world
    (cfg.memory_dir / "events.md").write_text("# E\n\n## kitchen-2026\n\ntext\n\n## move-2027\n\nmore\n")
    i = by_code(run_check(store, con), "unused_event")
    assert [x.message for x in i] == ["event 'move-2027' is defined but no annotation refers to it"]


def test_it_matches_what_the_classifier_matches(world, cfg):
    """The check's idea of 'matched' is the classifier's: kitchen-works wins its transaction in `categorised`."""
    from coach.classify.rules import categorised
    con, store = world
    t = [x for x in categorised(con, memory_dir=cfg.memory_dir) if x["tx_key"] == "reno1"][0]
    assert t["source"] == "memory" and t["event"] == "kitchen-2026"
    assert not by_code(run_check(store, con), "annotation_matches_nothing")


# ---------------------------------------------------------------- liabilities, contracts

def test_payment_match_matching_nothing(world):
    con, store = world
    store.set_value("home-loan", "payment_match", "^NEVERSEEN")
    assert by_code(run_check(store, con), "payment_match_nothing")


def test_payment_amount_inconsistent_with_monthly_payment(world):
    con, store = world
    store.set_value("home-loan", "monthly_payment", 1200)
    i = by_code(run_check(store, con), "payment_amount_mismatch")
    assert i and "1,500.00" in i[0].message and "1,200.00" in i[0].message
    store.set_value("home-loan", "monthly_payment", 1560)                  # within 10 %
    assert not by_code(run_check(store, con), "payment_amount_mismatch")


def test_payment_match_catching_two_loans_is_reported_as_overlap_and_spread(world):
    con, store = world
    monthly(con, "ce", "other", "HOMEBANK CAP IN", [-700] * 6, tx_type="direct_debit", day=20)
    store.edit("liabilities/second.yaml", [{"op": "create", "value": {
        "id": "second", "kind": "consumer_loan", "payment_match": "CAP IN", "monthly_payment": 700}}])
    issues = run_check(store, con)
    assert by_code(issues, "payment_amount_spread")[0].file == "liabilities/home-loan.yaml"
    ov = by_code(issues, "payment_match_overlap")
    assert {x.file for x in ov} == {"liabilities/home-loan.yaml", "liabilities/second.yaml"}


def test_contract_merchant_match_and_amount(world):
    con, store = world
    store.edit("contracts/stream.yaml", [{"op": "create", "value": {
        "id": "stream", "kind": "streaming", "merchant_match": "^STREAMBOX", "billing": {"amount": 20, "period": "monthly"}}}])
    assert by_code(run_check(store, con), "payment_amount_mismatch")
    store.set_value("stream", "merchant_match", "^NOPE")
    assert by_code(run_check(store, con), "merchant_match_nothing")


def test_stale_outstanding_capital_and_threshold_is_configurable(world):
    con, store = world
    assert by_code(run_check(store, con, stale_months=6), "stale_fact")        # 2026-01-15 vs today 2026-10-04
    assert not by_code(run_check(store, con, stale_months=12), "stale_fact")
    store.set_value("home-loan", "outstanding_as_of", None)
    assert by_code(run_check(store, con), "outstanding_undated")


def test_null_critical_fields_are_info(world):
    con, store = world
    store.edit("liabilities/car.yaml", [{"op": "create", "value": {"id": "car", "kind": "loa"}}])
    i = [x for x in by_code(run_check(store, con), "liability_incomplete") if x.file == "liabilities/car.yaml"][0]
    # E9-6: a lease owes no capital and has no rate: its critical fields are the end date and the residual value
    assert i.level == "info" and "residual_value" in i.message and "end_date" in i.message and "rate.nominal" not in i.message
    store.edit("liabilities/mortgage-x.yaml", [{"op": "create", "value": {"id": "mortgage-x", "kind": "mortgage"}}])
    j = [x for x in by_code(run_check(store, con), "liability_incomplete") if x.file == "liabilities/mortgage-x.yaml"][0]
    assert "outstanding" in j.message and "rate.nominal" in j.message


# ---------------------------------------------------------------- assets (E3-8)

def test_asset_staleness_threshold_is_three_months_and_configurable(world):
    con, store = world
    assert by_code(run_check(store, con), "stale_value")[0].path == "assets[savings-book].as_of"
    assert not by_code(run_check(store, con, asset_stale_months=12), "stale_value")
    store.set_value("savings-book", "as_of", dt.date(2026, 9, 1))
    assert not by_code(run_check(store, con), "stale_value")


def test_asset_without_value_is_info_and_without_date_a_warning(world):
    con, store = world
    assert by_code(run_check(store, con), "asset_no_value")[0].level == "info"
    store.set_value("savings-book", "as_of", None)
    assert by_code(run_check(store, con), "asset_undated")[0].level == "warning"


def test_manual_totals(world):
    con, store = world
    t = totals.manual_totals(store, TODAY)
    assert t["assets"]["total"] == 5000 and t["assets"]["unknown_value"] == ["family-house"]
    assert t["assets"]["stale"] == ["savings-book"] and t["liabilities"]["outstanding_total"] == 180000
    assert t["net_manual"] == -175000 and t["complete"] is False and t["liabilities"]["monthly_payments_total"] == 1500
    store.set_value("family-house", "value", 400000)
    store.set_value("family-house", "as_of", dt.date(2026, 9, 1))
    t = totals.manual_totals(store, TODAY)
    assert t["assets"]["total"] == 405000 and t["complete"] is True and t["assets"]["by_kind"]["real_estate"] == 400000


# ---------------------------------------------------------------- household / questions / documents

def test_account_owners_must_be_household_members(world):
    con, store = world
    assert not by_code(run_check(store, con), "owner_not_a_member")          # joint + mia (a member id)
    con.execute("UPDATE accounts SET owner='stranger' WHERE uid='rl'")
    con.commit()
    i = by_code(run_check(store, con), "owner_not_a_member")
    assert "stranger" in i[0].message
    con.execute("UPDATE accounts SET owner='MME ANNA ROSSI' WHERE uid='rl'")      # an alias counts
    con.commit()
    assert not by_code(run_check(store, con), "owner_not_a_member")


def test_missing_household_and_member_without_birth_year_are_info(cfg):
    con = make_world(cfg, household=False)
    st = MemoryStore(cfg.memory_dir)
    assert by_code(run_check(st, con), "no_household")[0].level == "info"
    make_memory(cfg.memory_dir)
    assert by_code(run_check(st, con), "member_no_birth_year")[0].path == "members[luca]"


def test_open_question_about_a_fact_that_is_now_known(world):
    con, store = world
    from coach.memory import questions as Q
    Q.add(store, "Value of the house?", key="fill:asset:family-house")
    assert not by_code(run_check(store, con), "question_resolved")
    store.set_value("family-house", "value", 1)
    i = by_code(run_check(store, con), "question_resolved")
    assert i and "asset now has a value" in i[0].message


def test_questions_markdown_out_of_date_is_a_warning(world, cfg):
    con, store = world
    from coach.memory import questions as Q
    Q.add(store, "x?")
    assert not by_code(run_check(store, con), "questions_md_stale")
    (cfg.memory_dir / "open-questions.md").write_text("hand written\n")
    assert by_code(run_check(store, con), "questions_md_stale")


def test_without_a_database_data_checks_are_skipped(world, cfg):
    _, store = world
    put_annotations(cfg, "  - id: ghost\n    match: {merchant_key: '^NOBODY$'}\n    category: food.groceries\n")
    issues = run_check(store, None)
    assert not by_code(issues, "annotation_matches_nothing")


# ---------------------------------------------------------------- CLI, health, schedule

def run(cfg, *argv):
    main(["--config", str(cfg.config_path), "--insecure", *argv])


def test_cli_check_json_and_exit_code(world, cfg, capsys):
    run(cfg, "memory", "check", "--json")
    d = json.loads(capsys.readouterr().out)
    assert d["database_checked"] is True and d["summary"]["errors"] == 0 and d["issues"]
    (cfg.memory_dir / "assets.yaml").write_text("assets:\n  - id: a\n    kind: other\n    balance: lots\n")
    with pytest.raises(SystemExit) as e:
        run(cfg, "memory", "check")
    assert e.value.code == 1
    assert "assets[a].balance" in capsys.readouterr().out


def test_cli_check_text_summary_line(world, cfg, capsys):
    run(cfg, "memory", "check")
    out = capsys.readouterr().out
    assert "0 error(s)" in out and "warning(s)" in out


def test_health_shows_the_memory_summary_and_keeps_its_exit_code(world, cfg, capsys):
    run(cfg, "health")
    out = capsys.readouterr().out
    assert "memory: 0 error(s)" in out
    run(cfg, "health", "--json")
    d = json.loads(capsys.readouterr().out)
    assert d["memory"]["errors"] == 0 and "by_code" in d["memory"]
    (cfg.memory_dir / "assets.yaml").write_text("assets: [oops\n")
    try:
        run(cfg, "health")
    except SystemExit as e:                                        # connector health decides the exit code, not memory
        assert e.code in (0, 1)
    assert "[!] memory: 1 error(s)" in capsys.readouterr().out


def test_schedule_run_has_a_warn_only_memory_step(world, cfg):
    from coach import schedule as sch
    from helpers import FakeClient
    (cfg.memory_dir / "assets.yaml").write_text("assets: [oops\n")
    lines = []
    sch.run_daily(cfg, insecure=True, client=FakeClient(), out=lines.append)
    text = "\n".join(lines)
    assert "WARNING memory: 1 error(s)" in text and "memory: errors=1" in text
    log = (cfg.log_dir / "schedule.log").read_text()
    assert "memory: errors=1" in log and "ERROR" not in log.split("memory:")[1]


def test_schedule_memory_step_can_be_disabled_and_never_fails_the_job(world, cfg, monkeypatch):
    from coach import schedule as sch
    from helpers import FakeClient
    cfg.schedule_memory_check = False
    lines = []
    sch.run_daily(cfg, insecure=True, client=FakeClient(), out=lines.append)
    assert "memory:" not in (cfg.log_dir / "schedule.log").read_text()
    cfg.schedule_memory_check = True
    monkeypatch.setattr("coach.memory.check.run_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    lines.clear()
    sch.run_daily(cfg, insecure=True, client=FakeClient(), out=lines.append)
    assert "memory check could not run" in "\n".join(lines)
