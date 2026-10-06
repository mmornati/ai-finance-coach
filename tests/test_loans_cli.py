"""E9 CLI: `coach loans ...` and `coach networth`. Memory writes are validated, previewed and written only after a typed yes (or --yes typed by
the person), recorded with the source cli; inferred values are only ever PROPOSED."""
from __future__ import annotations

import json

import pytest

from coach.cli import main
from coach.db import connect
from coach.memory import proposals
from coach.memory.store import MemoryStore
from mcphelpers import world  # noqa: F401
from memhelpers import fake_tty

ASOF = ["--as-of", "2026-10-04"]


def cli(cfg, *argv):
    """--yes only skips the typed prompt for a human at a terminal: a call with --yes pretends to be one (the refusals are tested apart)."""
    from unittest import mock
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


def test_list_show_and_schedule_of_the_synthetic_mortgage(cfg, world, capsys):
    rows = js(capsys, cfg, "loans", "list", *ASOF)
    (r,) = rows
    assert r["id"] == "home-loan" and r["schedule"] == "computed" and r["capital_source"] == "declared_rolled" and r["payments_seen"] == 12
    text = out(capsys, cfg, "loans", "list", *ASOF)
    assert "home-loan" in text and "(declared rolled)" in text
    shown = out(capsys, cfg, "loans", "show", "home-loan", *ASOF)
    assert "schedule (from_outstanding)" in shown and "payments seen: 12" in shown and "interest = the figure for" in shown
    sch = js(capsys, cfg, "loans", "schedule", "home-loan", *ASOF)
    assert sch["status"] == "computed" and "rows" not in sch and sch["payment"] == "1237.57" and sch["term_instalments"] == 168 and sch["source"] == "declared_rolled"
    rows = js(capsys, cfg, "loans", "schedule", "home-loan", "--rows", "--year", "2026", *ASOF)["rows"]
    assert len(rows) == 11 and rows[0]["due"] == "2026-02-01" and rows[0]["interest"] == "315.00"      # 180,000 declared x 2.1 % / 12; Feb-Dec 2026
    listing = out(capsys, cli_args := cfg, "loans", "schedule", "home-loan", "--rows", "--year", "2026", *ASOF)
    assert "kind" in listing and "(* = already due)" in listing
    with pytest.raises(SystemExit) as e:
        cli(cfg, "loans", "show", "nope")
    assert "no loan with id" in str(e.value)


def test_a_loan_that_cannot_be_scheduled_lists_what_is_missing_and_can_ask_a_question(cfg, world, capsys):
    s = store(cfg)
    s.edit("liabilities/bare.yaml", [{"op": "create", "value": {"id": "bare", "kind": "consumer_loan", "monthly_payment": 120}}])
    text = out(capsys, cfg, "loans", "schedule", "bare", *ASOF, "--propose-questions")
    assert "not computable" in text and "principal" in text and "added 1 open question" in text
    assert any(q.key == "fill:loan-schedule:bare" for q in store(cfg).questions())
    again = out(capsys, cfg, "loans", "schedule", "bare", *ASOF, "--propose-questions")
    assert "already on the open-questions list" in again


def test_add_previews_and_writes_only_after_a_confirmation(cfg, world, capsys, monkeypatch):
    with pytest.raises(SystemExit) as e:                                              # no terminal, no --yes: preview only
        cli(cfg, "loans", "add", "new-car", "--kind", "car_loan", "--set", "lender=CarFin", "--set", "principal=12000")
    assert "preview only" in str(e.value) and not store(cfg).exists("liabilities/new-car.yaml")
    assert "lender: CarFin" in capsys.readouterr().out                                   # the preview shows the diff
    cli(cfg, "loans", "add", "new-car", "--kind", "car_loan", "--yes", "--set", "lender=CarFin", "--set", "principal=12000",
        "--set", "rate.nominal=4.5", "--set", "start_date=2026-03-10", "--set", "term_months=36", "--set", "insurance.monthly=15",
        "--set", "deferral.months=2", "--set", "deferral.kind=partial", "--reason", "from the offer")
    m = next(x for _r, x in store(cfg).liabilities() if x.id == "new-car")
    assert m.principal == 12000 and m.rate.nominal == 4.5 and str(m.start_date) == "2026-03-10" and m.term_months == 36
    assert m.insurance.monthly == 15 and m.deferral.months == 2 and m.deferral.kind == "partial"
    last = store(cfg).history("liabilities/new-car.yaml", 1)[0]
    assert last.source == "cli" and "from the offer" in (last.reason or "")
    with pytest.raises(SystemExit):
        cli(cfg, "loans", "add", "new-car", "--kind", "car_loan", "--yes", "--set", "lender=x")             # already exists


def test_invalid_values_are_refused_and_nothing_is_written(cfg, world, capsys):
    for bad in ("rate.nominal=-3", "term_months=0", "payment_day=40", "insurance.basis=weekly", "deferral.kind=maybe", "first_payment_date=2019-01-01"):
        with pytest.raises(SystemExit) as e:
            cli(cfg, "loans", "add", "bad-loan", "--kind", "car_loan", "--yes", "--set", "start_date=2020-01-01", "--set", bad)
        assert "error" in str(e.value).lower() or e.value.code not in (0, None), bad
        assert not store(cfg).exists("liabilities/bad-loan.yaml"), bad


def test_edit_set_and_unset_with_a_typed_yes(cfg, world, capsys, monkeypatch):
    fake_tty(monkeypatch, answers=("n",))
    cli(cfg, "loans", "edit", "home-loan", "--set", "insurance.monthly=40")
    assert "skipped" in capsys.readouterr().out
    assert next(m for _r, m in store(cfg).liabilities() if m.id == "home-loan").insurance.monthly is None
    fake_tty(monkeypatch, answers=("y",))
    cli(cfg, "loans", "edit", "home-loan", "--set", "insurance.monthly=40", "--set", "payment_day=3")
    m = next(x for _r, x in store(cfg).liabilities() if x.id == "home-loan")
    assert m.insurance.monthly == 40 and m.payment_day == 3
    cli(cfg, "loans", "edit", "home-loan", "--unset", "payment_day", "--yes")
    assert next(x for _r, x in store(cfg).liabilities() if x.id == "home-loan").payment_day is None
    with pytest.raises(SystemExit):
        cli(cfg, "loans", "edit", "ghost", "--set", "lender=x", "--yes")


def test_the_guided_questions_collect_values_and_preview_them(cfg, world, capsys, monkeypatch):
    answers = iter(["CarFin", "the car", "", "2026-03-10", "", "", "36", "", "12000", "fixed", "4.5"] + ["x"] + ["y"])
    monkeypatch.setattr("coach.memory.commands._is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    cli(cfg, "loans", "add", "guided", "--kind", "car_loan")
    m = next(x for _r, x in store(cfg).liabilities() if x.id == "guided")
    assert (m.lender, m.asset, m.term_months, m.principal, m.rate.type, m.rate.nominal) == ("CarFin", "the car", 36, 12000, "fixed", 4.5)


def test_odometer_readings_are_recorded_sorted_and_replaced_on_the_same_day(cfg, world, capsys):
    s = store(cfg)
    s.edit("liabilities/lease.yaml", [{"op": "create", "value": {"id": "lease", "kind": "loa", "monthly_payment": 300,
                                                                 "start_date": __import__("datetime").date(2025, 1, 1),
                                                                 "end_date": __import__("datetime").date(2027, 1, 1)}}])
    cli(cfg, "loans", "odometer", "lease", "--km", "9000", "--date", "2025-09-01", "--yes")
    cli(cfg, "loans", "odometer", "lease", "--km", "15000", "--date", "2026-01-01", "--yes")
    cli(cfg, "loans", "odometer", "lease", "--km", "15100", "--date", "2026-01-01", "--yes")       # same day: replaced
    m = next(x for _r, x in store(cfg).liabilities() if x.id == "lease")
    assert [(str(o.date), o.km) for o in m.odometer] == [("2025-09-01", 9000), ("2026-01-01", 15100)]
    with pytest.raises(SystemExit):
        cli(cfg, "loans", "odometer", "lease", "--km", "100", "--date", "2026-06-01", "--yes")       # a decreasing reading is refused
    capsys.readouterr()
    lease = js(capsys, cfg, "loans", "lease", "lease", *ASOF)["lease"]
    assert lease["mileage"]["latest"]["km"] == 15100 and lease["end"]["reminder_active"] is True and lease["checklist"]
    text = out(capsys, cfg, "loans", "lease", "lease", *ASOF, "--market-value", "18000")
    assert "end of the lease" in text and "return checklist" in text


def test_infer_only_ever_proposes(cfg, world, capsys):
    s = store(cfg)
    s.edit("liabilities/inferme.yaml", [{"op": "create", "value": {"id": "inferme", "kind": "consumer_loan", "payment_match": "^HOMEBANK",
                                                                    "principal": 180000, "start_date": __import__("datetime").date(2020, 1, 1),
                                                                    "end_date": __import__("datetime").date(2040, 1, 1)}}])
    before = s.read_text("liabilities/inferme.yaml")
    text = out(capsys, cfg, "loans", "infer", "inferme", *ASOF)
    assert "INFERRED, not stored" in text and "rate.nominal" in text
    assert store(cfg).read_text("liabilities/inferme.yaml") == before                       # nothing written
    # 1,500 a month on 180,000 over 240 months: the annuity of 180,000 at 7.95 % is exactly 1,500.00 (7.94 % gives 1,498.88, 7.96 % 1,501.11)
    j = js(capsys, cfg, "loans", "infer", "inferme", *ASOF)
    f = {x["field"]: x for x in j["fields"]}
    assert f["rate.nominal"]["value"] == 7.95 and f["rate.nominal"]["confidence"] == "medium"
    text = out(capsys, cfg, "loans", "infer", "inferme", *ASOF, "--propose")
    assert "proposal p-" in text and "nothing is written" in text
    assert store(cfg).read_text("liabilities/inferme.yaml") == before
    (p,) = [x for x in proposals.listing(store(cfg)) if x.source == "loans-inference"]
    assert p.file == "liabilities/inferme.yaml" and any(o["path"] == "rate.nominal" for o in p.ops) and "INFERRED" in p.reason


def test_payments_and_alerts_listings(cfg, world, capsys):
    j = js(capsys, cfg, "loans", "payments", "home-loan", *ASOF)
    assert j["summary"]["count"] == 12 and j["payments"][-1]["date"] == "2026-09-03"
    text = out(capsys, cfg, "loans", "payments", "home-loan", *ASOF)
    assert "12 payment(s) match" in text and "alerts: none" in text                  # no payment alert (the capital difference is a card)
    alerts = out(capsys, cfg, "loans", "alerts", *ASOF)
    assert "the schedule differs from the declared capital" in alerts and "missed" not in alerts


def test_scenarios_run_on_the_schedule_and_can_be_saved_as_an_insight(cfg, world, capsys):
    r = js(capsys, cfg, "loans", "scenario", "prepay", "home-loan", "--amount", "20000", "--on", "2026-11-01", *ASOF)
    assert r["status"] == "computed" and {o["mode"] for o in r["options"]} == {"keep_payment", "keep_term"} and r["penalty"] != "0.00"
    text = out(capsys, cfg, "loans", "scenario", "prepay", "home-loan", "--amount", "20000", "--on", "2026-11-01", *ASOF)
    assert "keep the instalment" in text and "break-even" in text and "estimate, not an offer" in text
    reneg = js(capsys, cfg, "loans", "scenario", "renegotiate", "home-loan", "--new-rate", "1.5", "--bank-fees", "500", *ASOF)
    assert reneg["status"] == "computed" and reneg["variant"] == "rachat" and reneg["bank_fees"] == "500.00"
    out(capsys, cfg, "loans", "scenario", "prepay", "home-loan", "--amount", "20000", *ASOF, "--save")
    con = connect(cfg, insecure=True)
    rows = con.execute("SELECT kind, title, skill FROM insights WHERE skill='loan-scenario'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "finding" and "mortgage" in rows[0][1] and "home-loan" not in rows[0][1]
    with pytest.raises(SystemExit):
        cli(cfg, "loans", "scenario", "prepay", "home-loan", *ASOF)                          # --amount is required


def test_networth_cli_prints_the_composition_and_records_snapshots(cfg, world, capsys):
    j = js(capsys, cfg, "networth", *ASOF)
    assert j["complete"] is False and j["by_category"]["liabilities"] == j["liabilities"] and j["by_owner"]
    assert {c["type"] for c in j["components"]} == {"account", "asset", "liability"}
    text = out(capsys, cfg, "networth", *ASOF)
    assert "net worth on 2026-10-04" in text and "known part only" in text and "by owner" in text
    assert "none yet" in out(capsys, cfg, "networth", "--history", *ASOF)
    with pytest.raises(SystemExit) as e:                                                    # --as-of refuses every write
        cli(cfg, "networth", "--record", *ASOF)
    assert "--as-of refuses every write" in str(e.value)
    import datetime as dt
    today = dt.date.today()
    assert f"recorded the snapshot of {today}" in out(capsys, cfg, "networth", "--record")
    h = js(capsys, cfg, "networth", "--history", "--months", "3")["history"]
    assert h and h[-1]["month"] == today.strftime("%Y-%m") and h[-1]["source"] == "snapshot"
    con = connect(cfg, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM net_worth_history WHERE source='snapshot'").fetchone()[0] == 1
