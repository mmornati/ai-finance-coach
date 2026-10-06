"""E4 through the CLI and the database: load_dataset on real tables, every command, memory files budgets.yaml / goals.yaml,
the scheduled analytics step (warn only) and the `classify report` change."""
import json
import re
import time

import pytest

from coach import schedule as sch
from coach.analytics import api
from coach.analytics.dataset import load_dataset
from coach.cli import main
from coach.db import connect
from coach.memory.store import MemoryStore, ValidationFailed
from helpers import FakeClient, add_tx, eb_tx
from memhelpers import fake_tty, make_world

ASOF = "2026-10-04"


@pytest.fixture
def world(cfg):
    return make_world(cfg)


def run(cfg, *argv):
    """--yes only skips the typed prompt for a person at a terminal: a call with --yes pretends to be one (the refusals: test_cli_confirmation)."""
    from unittest import mock
    args = ["--config", str(cfg.config_path), "--insecure", *argv]
    if "--yes" in argv:
        with mock.patch("coach.memory.commands._is_tty", return_value=True):
            return main(args)
    return main(args)


def out_of(capsys, cfg, *argv):
    run(cfg, *argv)
    return capsys.readouterr().out


def js(capsys, cfg, *argv):
    return json.loads(out_of(capsys, cfg, *argv, "--json"))


# ---------------------------------------------------------------- dataset from the database

def test_load_dataset_applies_categories_tags_splits_and_account_filters(world, cfg):
    con = world
    con.execute("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES ('fm0', -25.0, 'food.groceries', NULL)")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES ('fm0', -15.0, 'housing.maintenance_diy', NULL)")
    con.execute("UPDATE accounts SET exclude=1 WHERE uid='rl'")
    con.commit()
    import datetime as dt
    ds = load_dataset(con, cfg.memory_dir, today=dt.date(2026, 10, 4))
    assert set(ds.accounts) == {"fo", "ce"}                                   # the excluded account is out
    reno = next(t for t in ds.txs if t.key == "reno1")
    assert reno.category == "housing.renovation" and reno.tags == {"one_off", "capital"} and reno.event == "kitchen-2026"
    parts = [t for t in ds.txs if t.key == "fm0"]
    assert sorted((t.category, t.amount_c) for t in parts) == [("food.groceries", -2500), ("housing.maintenance_diy", -1500)]
    assert sum(1 for t in ds.whole if t.key == "fm0") == 1 and next(t for t in ds.whole if t.key == "fm0").amount_c == -4000
    link = [t for t in ds.txs if t.key in ("tr_out", "tr_in")]
    assert {t.category for t in link} == {"transfer.internal"}                   # a matched internal transfer
    assert ds.coverage.of("ce").first is not None and ds.memory.contracts == [] and len(ds.memory.liabilities) == 1


def test_load_dataset_balances_prefer_the_booked_type_and_use_the_latest(world, cfg):
    import datetime as dt
    con = world
    con.executemany("INSERT INTO balances VALUES ('ce',?,?,?,'EUR',NULL)", [
        ("2026-10-01T08:00:00+00:00", "CLBD", 900.0), ("2026-10-04T08:00:00+00:00", "CLBD", 1234.56),
        ("2026-10-04T08:00:00+00:00", "ITAV", 9999.0)])
    con.commit()
    ds = load_dataset(con, cfg.memory_dir, today=dt.date(2026, 10, 4))
    b = ds.balances["ce"]
    assert (b.type, b.amount_c, b.as_of.isoformat()) == ("CLBD", 123456, "2026-10-04")


# ---------------------------------------------------------------- commands

def test_coverage_and_cashflow_commands(world, cfg, capsys):
    text = out_of(capsys, cfg, "coverage", "--as-of", ASOF)
    assert "Fortuneo" in text and "full months" in text
    rows = js(capsys, cfg, "coverage", "--as-of", ASOF)
    assert {r["account"] for r in rows} == {"fo", "ce", "rl"} and next(r for r in rows if r["account"] == "fo")["n_months"] >= 1
    cf = js(capsys, cfg, "cashflow", "--as-of", ASOF, "--months", "3")
    assert [m["month"] for m in cf["household"]["months"]] == ["2026-07", "2026-08", "2026-09"]
    assert cf["household"]["months"][0]["income"] == "0.00" and cf["household"]["months"][0]["savings_rate"] is None
    assert set(cf["by_purpose"]) == {"cards", "main", "kids"} and set(cf["by_owner"]) == {"joint", "mia"}
    assert cf["coverage"]["rule"] and "evidence" in cf
    t = out_of(capsys, cfg, "cashflow", "--as-of", ASOF, "--months", "3", "--by", "purpose")
    assert "purpose=kids" in t and "net" in t
    m0 = cf["household"]["months"][0]       # debited 1,500; E9-3 / MJ-2: the principal part comes from the schedule. The declared capital (180,000 on
    # 2026-01-15) differs from the theoretical table, so it is rolled forward: annuity of 180,000 over the 168 instalments from 2026-02-01 = 1,237.57;
    # instalment 6 (due 2026-07-01) = 1,237.57 - 306.90 interest (175,370.98 x 2.1 % / 12) = 930.67 of principal
    assert m0["debt_service"] == "1500.00" and m0["loan_principal"] == "930.67"
    assert "savings_rate_incl_principal" in m0 and "drawn_unconnected" in m0
    assert out_of(capsys, cfg, "cashflow", "--as-of", ASOF, "--owner", "mia", "--months", "3").count("household") >= 1


def test_averages_command_is_coverage_aware(world, cfg, capsys):
    d = js(capsys, cfg, "averages", "--as-of", ASOF)
    by = {c["category"]: c for c in d["categories"]}
    assert by["housing.mortgage"]["n_months"] <= 12 and by["housing.mortgage"]["monthly_avg"] == "1500.00"
    assert by["housing.renovation"]["monthly_avg"] == "0.00" and d["excluded"][0]["tags"] == ["capital", "one_off"]
    assert out_of(capsys, cfg, "averages", "--as-of", ASOF).startswith("monthly averages, coverage-aware")


def test_recurring_commands_read_without_writing_and_refresh_stores(world, cfg, capsys):
    text = out_of(capsys, cfg, "recurring", "--as-of", ASOF)
    assert "l:home-loan" in text and "Streambox" in text and "(no contract)" in text
    assert world.execute("SELECT COUNT(*) FROM recurring_series").fetchone()[0] == 0          # a read never writes
    d = js(capsys, cfg, "recurring", "--as-of", ASOF, "--all")
    ids = {s["id"] for s in d["series"]}
    assert len(ids) >= 3 and all(i.startswith("rec_") for i in ids) and d["counts"]["active"] >= 3
    with pytest.raises(SystemExit) as e:
        run(cfg, "recurring", "refresh", "--as-of", ASOF)
    assert "read-only" in str(e.value) and world.execute("SELECT COUNT(*) FROM recurring_series").fetchone()[0] == 0
    out_of(capsys, cfg, "recurring", "refresh")
    stored = {r[0] for r in world.execute("SELECT id FROM recurring_series")}
    refresh = out_of(capsys, cfg, "recurring", "refresh")
    assert "created 0, updated 0, removed 0" in refresh and f"unchanged {len(stored)}" in refresh
    assert {r[0] for r in world.execute("SELECT id FROM recurring_series")} == stored
    miss = out_of(capsys, cfg, "recurring", "missing", "--as-of", ASOF)
    assert "Streambox" in miss and "Homebank" not in miss
    assert "price changes" in out_of(capsys, cfg, "recurring", "changes", "--as-of", ASOF)
    run(cfg, "recurring", "--refresh")
    assert world.execute("SELECT COUNT(*) FROM recurring_series").fetchone()[0] == len(stored)


def test_anomalies_commands_refresh_list_dismiss(world, cfg, capsys):
    import datetime as dt
    day = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    for i in range(3):                                         # a payment that looks wrong on its own
        add_tx(world, "fo", f"dup{i}", day, -90.0, "GADGET SHOP", "card")
    d = js(capsys, cfg, "anomalies")
    assert d["anomalies"], d
    assert world.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0                 # listing does not write
    run(cfg, "anomalies", "refresh")
    assert "anomalies:" in capsys.readouterr().out
    aid = d["anomalies"][0]["id"]
    out_of(capsys, cfg, "anomalies", "dismiss", aid, "--note", "known")
    after = js(capsys, cfg, "anomalies")
    assert aid not in {a["id"] for a in after["anomalies"]} and after["dismissed"] == 1
    assert aid in {a["id"] for a in js(capsys, cfg, "anomalies", "--all")["anomalies"]}
    with pytest.raises(SystemExit) as e:
        run(cfg, "anomalies", "dismiss", "anm_nope")
    assert "no anomaly" in str(e.value)
    with pytest.raises(SystemExit):
        run(cfg, "anomalies", "dismiss")
    with pytest.raises(SystemExit) as e:
        run(cfg, "anomalies", "dismiss", aid, "--as-of", ASOF)
    assert "read-only" in str(e.value)
    out_of(capsys, cfg, "anomalies", "undismiss", aid)
    assert aid in {a["id"] for a in js(capsys, cfg, "anomalies")["anomalies"]}


def test_a_dismissal_works_on_a_live_anomaly_without_a_prior_refresh(world, cfg, capsys):
    import datetime as dt
    day = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    for i in range(2):
        add_tx(world, "fo", f"dd{i}", day, -90.0, "GADGET SHOP", "card")
    aid = js(capsys, cfg, "anomalies")["anomalies"][0]["id"]
    assert world.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0
    out_of(capsys, cfg, "anomalies", "dismiss", aid)
    assert world.execute("SELECT dismissed_at IS NOT NULL FROM anomalies WHERE id=?", (aid,)).fetchone() == (1,)


def test_forecast_command_uses_balances(world, cfg, capsys):
    world.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',10000.0,'EUR',NULL)")
    world.commit()
    d = js(capsys, cfg, "forecast", "--as-of", ASOF, "--days", "60", "--account", "ce")
    assert [a["label"] for a in d["accounts"]] == ["CPT COURANT TEST"] and d["household"]["start_balance"] == "10000.00"
    assert [m["days"] for m in d["household"]["milestones"]] == [30, 60] and len(d["household"]["points"]) == 60
    text = out_of(capsys, cfg, "forecast", "--as-of", ASOF, "--events")
    assert "household" in text and "assumption:" in text and "expected events:" in text
    with pytest.raises(SystemExit) as e:
        run(cfg, "forecast", "--account", "nope", "--as-of", ASOF)
    assert "unknown account" in str(e.value)


def test_budget_flow_suggest_set_list_status_with_preview_and_source(world, cfg, capsys, monkeypatch):
    sug = js(capsys, cfg, "budget", "suggest", "--as-of", ASOF)
    assert any(s["category"] == "housing.mortgage" for s in sug["suggestions"])
    # preview only
    out = out_of(capsys, cfg, "budget", "set", "food.groceries", "800", "--dry-run")
    assert "+    category: food.groceries" in out and "dry run" in out and not (cfg.memory_dir / "budgets.yaml").exists()
    # non-interactive without --yes writes nothing
    out = out_of(capsys, cfg, "budget", "set", "food.groceries", "800")
    assert "run this yourself in a terminal" in out and not (cfg.memory_dir / "budgets.yaml").exists()
    # --yes writes through the store, with its source in the history
    out = out_of(capsys, cfg, "budget", "set", "food.groceries", "800", "--rollover", "--start", "2026-06-01", "--source", "coach", "--yes", "--reason", "new year")
    assert "written as change" in out
    text = (cfg.memory_dir / "budgets.yaml").read_text()
    assert "id: food-groceries" in text and "monthly: 800" in text and "rollover: true" in text and "start: 2026-06-01" in text
    ch = MemoryStore(cfg.memory_dir).history(limit=1)[0]
    assert ch.source == "coach" and "budgets.yaml" in ch.files and ch.reason == "new year"
    # a second set updates the same budget
    out_of(capsys, cfg, "budget", "set", "food.groceries", "850", "--yes")
    assert (cfg.memory_dir / "budgets.yaml").read_text().count("- id:") == 1 and "monthly: 850" in (cfg.memory_dir / "budgets.yaml").read_text()
    out_of(capsys, cfg, "budget", "set", "group:housing", "4000", "--owner", "joint", "--yes")
    lst = json.loads(out_of(capsys, cfg, "budget", "list", "--json"))
    assert [b["id"] for b in lst] == ["food-groceries", "group-housing"] and lst[1]["group"] == "housing" and lst[1]["owner"] == "joint"
    st = js(capsys, cfg, "budget", "status", "--as-of", "2026-09-20")
    assert {b["id"] for b in st["budgets"]} == {"food-groceries", "group-housing"} and st["month"] == "2026-09"
    for argv in (["budget", "set", "nonsense.category", "10", "--yes"], ["budget", "set", "food.groceries", "-5", "--yes"],
                 ["budget", "set", "food.groceries", "lots", "--yes"], ["budget", "set", "group:nope", "10", "--yes"]):
        with pytest.raises(SystemExit):
            run(cfg, *argv)
    # an interactive 'no' writes nothing
    fake_tty(monkeypatch, ["n"])
    before = (cfg.memory_dir / "budgets.yaml").read_text()
    run(cfg, "budget", "set", "food.restaurants", "100")
    assert (cfg.memory_dir / "budgets.yaml").read_text() == before


def test_a_rollover_budget_starts_counting_this_month(world, cfg, capsys):
    import datetime as dt
    out_of(capsys, cfg, "budget", "set", "food.groceries", "800", "--rollover", "--yes")
    assert f"start: {dt.date.today().replace(day=1)}" in (cfg.memory_dir / "budgets.yaml").read_text()


def test_budget_set_propose_never_writes(world, cfg, capsys):
    out = out_of(capsys, cfg, "budget", "set", "food.groceries", "800", "--propose", "--source", "coach-llm")
    assert "nothing was written: queued as proposal" in out and not (cfg.memory_dir / "budgets.yaml").exists()
    props = MemoryStore(cfg.memory_dir)
    from coach.memory import proposals
    (p,) = proposals.listing(props)
    assert p.file == "budgets.yaml" and p.source == "coach-llm" and p.status == "pending"


def test_budgets_and_goals_are_validated_by_the_memory_store(cfg):
    store = MemoryStore(cfg.memory_dir)
    (cfg.memory_dir).mkdir(exist_ok=True)
    bad = ["budgets:\n  - id: a\n    monthly: 10\n",                                      # neither category nor group
           "budgets:\n  - id: a\n    category: food.groceries\n    group: food\n    monthly: 10\n",
           "budgets:\n  - id: a\n    category: food.groceries\n    monthly: -3\n",
           "budgets:\n  - id: a\n    category: food.groceries\n    monthly: 1\n  - id: a\n    group: food\n    monthly: 1\n"]
    for text in bad:
        _m, issues = store.validate_text("budgets.yaml", text)
        assert issues and issues[0].level == "error"
    for text in ["goals:\n  - id: g\n    target_amount: 100\n",                              # no source
                 "goals:\n  - id: g\n    target_amount: 100\n    asset: a\n    tag: t\n",
                 "goals:\n  - id: g\n    target_amount: 0\n    tag: t\n"]:
        assert store.validate_text("goals.yaml", text)[1]
    ok, issues = store.validate_text("budgets.yaml", "budgets:\n  - id: a\n    category: food.groceries\n    monthly: 10.5\n")
    assert not issues and ok.budgets[0].rollover is False
    with pytest.raises(ValidationFailed) as e:
        store.write_text("budgets.yaml", "budgets:\n  - id: a\n    category: not.a_category\n    monthly: 10\n", action="x")
    assert "unknown category" in str(e.value)
    with pytest.raises(ValidationFailed):
        store.write_text("budgets.yaml", "budgets:\n  - id: a\n    group: nope\n    monthly: 10\n", action="x")


def test_memory_check_flags_dangling_goal_references(world, cfg, capsys):
    (cfg.memory_dir / "goals.yaml").write_text("goals:\n  - id: g1\n    target_amount: 1000\n    asset: ghost\n"
                                               "  - id: g2\n    target_amount: 1000\n    account: ghost-account\n")
    (cfg.memory_dir / "budgets.yaml").write_text("budgets:\n  - id: b1\n    category: food.groceries\n    monthly: 10\n"
                                                 "  - id: b2\n    category: food.groceries\n    monthly: 20\n")
    out = out_of(capsys, cfg, "memory", "check", "--json")
    codes = {i["code"] for i in json.loads(out)["issues"]}
    assert {"goal_unknown_asset", "goal_unknown_account", "budget_duplicate"} <= codes


def test_goals_commands(world, cfg, capsys):
    out = out_of(capsys, cfg, "goals", "set", "pot", "--target", "6000", "--asset", "savings-book", "--date", "2027-12-31",
                 "--monthly", "200", "--yes", "--source", "coach")
    assert "written" in out and MemoryStore(cfg.memory_dir).history(limit=1)[0].source == "coach"
    g = js(capsys, cfg, "goals", "list", "--as-of", ASOF)["goals"][0]
    assert g["id"] == "pot" and g["current"] == "5000.00" and g["status"] == "on_track" and g["pace_basis"] == "planned"
    assert g["projected_date"] == "2027-03-04" and g["required_monthly"] == "66.67" and g["months_left"] == 15
    assert "stale_value" in g["flags"]                                                   # the asset value is from 2025-12-01
    out_of(capsys, cfg, "goals", "set", "pot", "--monthly", "300", "--yes")
    assert "monthly_contribution: 300" in (cfg.memory_dir / "goals.yaml").read_text()
    with pytest.raises(SystemExit) as e:
        run(cfg, "goals", "set", "new", "--tag", "savings", "--yes")
    assert "needs --target" in str(e.value)
    assert "pot" in out_of(capsys, cfg, "goals", "status", "--as-of", ASOF)


def test_calendar_command_and_ics_file(world, cfg, capsys, tmp_path):
    (cfg.memory_dir / "contracts").mkdir(exist_ok=True)
    (cfg.memory_dir / "contracts" / "internet.yaml").write_text("id: internet\nprovider: NetCo\nrenewal: 2026-11-20\nnotice_period_days: 30\n")
    ics = tmp_path / "plan.ics"
    out = out_of(capsys, cfg, "calendar", "--as-of", ASOF, "--days", "60", "--ics", str(ics))
    raw = ics.read_bytes().decode()
    assert "wrote" in out and raw.startswith("BEGIN:VCALENDAR\r\n") and "NetCo" in raw and "\n" not in raw.replace("\r\n", "")
    d = js(capsys, cfg, "calendar", "--as-of", ASOF, "--days", "60")
    kinds = {(i["source"], i["kind"]) for i in d["items"]}
    assert ("contract", "renewal") in kinds and ("contract", "notice_deadline") in kinds and ("recurring", "payment") in kinds
    run(cfg, "calendar", "--as-of", ASOF, "--days", "60", "--ics", str(ics))
    assert ics.read_bytes().decode() == raw                                              # reproducible


def test_review_year_command(world, cfg, capsys):
    md = out_of(capsys, cfg, "review", "year", "2026", "--as-of", ASOF)
    assert md.startswith("# Year in review 2026") and "kitchen-2026" in md
    d = js(capsys, cfg, "review", "year", "2026", "--as-of", ASOF)
    assert d["year"] == 2026 and d["by_event"][0]["event"] == "kitchen-2026" and d["by_event"][0]["spending"] == "6500.00"


def test_analytics_refresh_command(world, cfg, capsys):
    out = out_of(capsys, cfg, "analytics", "refresh")
    assert out.startswith("recurring: ") and "anomalies:" in out
    with pytest.raises(SystemExit):
        run(cfg, "analytics", "refresh", "--as-of", ASOF)
    d = js(capsys, cfg, "analytics", "refresh")
    assert d["recurring"]["created"] == 0 and d["recurring"]["updated"] == 0 and d["recurring"]["unchanged"] == d["recurring"]["series"]


def _original_report_averages(avg):
    """The averages block of `classify report` exactly as it was written before E4 (copied from the former cmd_report)."""
    out = []
    full = avg["full_months"]
    if full:
        out.append(f"average monthly spending over {len(full)} full months ({full[0]} \u2192 {full[-1]}): "
                   f"{avg['avg_monthly']:.0f} EUR  (with one-offs it would be {avg['avg_monthly_with_one_offs']:.0f})")
    else:
        out.append("average monthly spending: not enough data (need at least 3 months)")
    out.append("excluded from averages (memory tags):")
    for t in avg["excluded"]:
        out.append(f"  {t['date']} {t['amount']:>10.2f}  {t['category']:<22} {t['event'] or ''}  {sorted(t['tags'])}")
    if avg["last12"]:
        last12 = avg["last12"]
        out.append(f"\nmonthly average by category, {last12[0]} \u2192 {last12[-1]} (one-offs excluded, refunds netted):")
        for c, per_month, total in avg["by_category"][:25]:
            out.append(f"  {c:<34}{per_month:>9.0f}/month  {total:>9.0f} total")
    return "\n".join(out) + "\n"


def test_legacy_report_is_byte_identical_to_the_former_one(world, cfg, capsys):
    from coach.analytics.report import build_report
    legacy = out_of(capsys, cfg, "classify", "report", "--legacy")
    expected = _original_report_averages(build_report(world, memory_dir=cfg.memory_dir)["averages"])
    assert expected in legacy                                              # the block, character for character
    assert legacy.index("average monthly spending") > legacy.index("llm merchant confidence")


def test_classify_report_is_coverage_aware_and_uses_one_window_for_both_figures(world, cfg, capsys):
    new = out_of(capsys, cfg, "classify", "report")
    head = next(ln for ln in new.splitlines() if ln.startswith("average monthly spending over"))
    m = re.match(r"average monthly spending over (\d+) months fully covered by every account carrying spending "
                 r"\((\S+) -> (\S+)\): (\d+) EUR  \(with one-offs it would be (\d+)\)", head)
    assert m, head
    from coach.analytics import api, averages
    r = averages.category_averages(api.build_dataset(world, cfg))
    assert (int(m.group(1)), m.group(2), m.group(3)) == (len(r.household_months), r.household_months[0], r.household_months[-1])
    assert (int(m.group(4)), int(m.group(5))) == (round(r.household_monthly_avg_c / 100), round(r.household_monthly_avg_with_one_offs_c / 100))
    assert "monthly average by category over the months covered" in new and "coverage by source" in new


# ---------------------------------------------------------------- scheduled step and config

def _daily_world(cfg, monkeypatch):
    con = connect(cfg, create=True)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Test','FR','2099-01-01','t','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','MR ALICE TESTOWNER','FR76','EUR','CACC','{}')")
    con.commit()
    con.close()
    from test_schedule import fake_claude
    monkeypatch.setattr("coach.classify.llm.subprocess.run", fake_claude([]))
    return FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2025-10-03", -4.5, "CARTE 02/10 RELAY BEAUVAIS")]}]})


def test_schedule_runs_the_analytics_step_and_logs_it(cfg, monkeypatch, db_key):
    client = _daily_world(cfg, monkeypatch)
    out = []
    assert sch.run_daily(cfg, client=client, out=out.append) == 0
    line = (cfg.log_dir / "schedule.log").read_text()
    assert "analytics: recurring=" in line and "schedule run ok" in line


def test_a_failing_analytics_step_only_warns_and_never_fails_the_job(cfg, monkeypatch, db_key):
    client = _daily_world(cfg, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("analytics exploded")
    monkeypatch.setattr(api, "refresh_all", boom)
    out = []
    assert sch.run_daily(cfg, client=client, out=out.append) == 0
    line = (cfg.log_dir / "schedule.log").read_text()
    assert "schedule run ok" in line and "analytics: could not run" in line
    assert any("WARNING analytics could not run: RuntimeError" in o for o in out)
    cfg.schedule_analytics = False
    assert sch.run_daily(cfg, client=FakeClient({"acc1": [{"transactions": []}]}), out=lambda *_: None) == 0
    assert "analytics:" not in (cfg.log_dir / "schedule.log").read_text().splitlines()[-1]


def test_config_analytics_table_is_validated_and_shown(cfg, tmp_path, capsys):
    from coach.config import ConfigError, effective, load_config
    p = tmp_path / "c2.toml"
    p.write_text('[analytics]\nrecurring_amount_tolerance = 0.2\nanomaly_z = 4\n[schedule]\nanalytics = false\n')
    c = load_config(p, env={})
    rows = {k: (v, s) for k, v, s in effective(c)}
    assert rows["analytics.recurring_amount_tolerance"] == ("0.2", "config.toml") and rows["analytics.anomaly_z"][0] == "4.0"
    assert rows["analytics.average_window_months"][1] == "default" and c.schedule_analytics is False
    for bad in ('[analytics]\nnope = 1\n', '[analytics]\nrecurring_amount_tolerance = 3\n', '[analytics]\nanomaly_z = "x"\n'):
        p.write_text(bad)
        with pytest.raises(ConfigError):
            load_config(p, env={})


# ---------------------------------------------------------------- the registry (what E6-1 will expose)

def test_registry_functions_are_pure_json_safe_and_fast(world, cfg):
    import datetime as dt
    ds = api.build_dataset(world, cfg, dt.date(2026, 10, 4))
    t0 = time.perf_counter()
    reg = api.registry()
    outputs = {}
    for name in ("category_averages", "cashflow", "recurring", "price_changes", "anomalies", "forecast",
                 "budget_suggestions", "budget_status", "calendar", "goals", "year_review"):
        res = reg[name](ds)
        outputs[name] = res.to_dict()
        json.dumps(outputs[name])
        assert "coverage" in outputs[name], name
        assert "evidence" in outputs[name], name
    assert time.perf_counter() - t0 < 2.0
    again = api.build_dataset(world, cfg, dt.date(2026, 10, 4))
    assert reg["recurring"](again).to_dict() == outputs["recurring"]                    # same input, same output
    assert reg["coverage"](ds)[0]["account"]


def test_the_analytics_skill_states_the_agent_rules():
    from pathlib import Path
    skill = (Path(__file__).resolve().parents[1] / ".claude" / "skills" / "analytics-overview" / "SKILL.md").read_text()
    assert "never run `coach memory accept`" in skill and "--propose --source coach-llm" in skill
    assert "Never pass `--yes`" in skill and "--yes" not in skill.replace("Never pass `--yes`", "")
    assert "never compute" in skill.lower() or "You\nnever compute" in skill or "never compute, add or average" in skill
    commands = [m.split() for m in __import__("re").findall(r"`uv run coach ([a-z][^`]*)`", skill)]
    from test_cli_flags import check
    bad = [(" ".join(c), check(c)) for c in commands if check(c)]
    assert not bad, bad
