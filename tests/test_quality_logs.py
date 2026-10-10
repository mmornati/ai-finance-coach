"""E12 observability: structured run logs (JSON lines), rotation by size and age, `coach logs`, the health card, the weekly evaluation step,
and the privacy test: a scheduled run on synthetic data full of sentinels leaves none of them in any log file."""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from coach import schedule as sch
from coach.cli import main
from coach.db import connect
from coach.quality import gold as G, logs as L
from helpers import FakeClient, eb_tx
from memhelpers import make_world


# ---------------------------------------------------------------- the line vocabulary

def read(log_dir):
    return list(L.read_events(log_dir))


def test_a_run_is_start_steps_end_with_an_id_durations_and_counts(tmp_path):
    log = L.RunLog(tmp_path / "logs", version="9.9")
    log.start()
    log.step("sync", "ok", 1234, {"accounts": 3, "new": 12, "failed": 0})
    log.step("normalize", "warn", 5, {})
    log.step("alerts", "error", 7, {}, error="RuntimeError")
    log.end(1)
    ev = read(tmp_path / "logs")
    assert [e["event"] for e in ev] == ["run_start", "step", "step", "step", "run_end"]
    assert len({e["run"] for e in ev}) == 1 and ev[0]["run"] == log.run_id and ev[0]["version"] == "9.9"
    assert ev[1] == {"ts": ev[1]["ts"], "run": log.run_id, "event": "step", "step": "sync", "status": "ok", "ms": 1234,
                     "counts": {"accounts": 3, "new": 12, "failed": 0}}
    assert ev[3]["error"] == "RuntimeError"
    assert ev[4]["outcome"] == "failed" and ev[4]["exit"] == 1 and ev[4]["steps"] == {"error": 1, "ok": 1, "warn": 1}
    assert (tmp_path / "logs" / L.LOG_NAME).stat().st_mode & 0o777 == 0o600


def test_nothing_outside_the_closed_vocabulary_can_be_logged(tmp_path):
    log = L.RunLog(tmp_path)
    log.step("a step nobody listed", "weird", 1, {"new": 1, "BAD KEY": 2, "str": "SENTINEL", "float": 1.5, "flag": True, "x" * 40: 3},
             error="boom: SENTINEL merchant 12.50 EUR")
    ev = read(tmp_path)[0]
    assert ev["step"] == "other" and ev["status"] == "ok"
    assert ev["counts"] == {"new": 1, "float": 1}                      # an int-or-float with a clean name; no text, no bool, no odd key
    assert "error" not in ev                                            # an exception TEXT is never kept, only a class name
    assert "SENTINEL" not in (tmp_path / L.LOG_NAME).read_text()


def test_counts_and_status_come_from_the_step_result_only(tmp_path):
    assert L.counts_of("accounts=3 new=2 replaced=0 skipped=0 failed=1") == {"accounts": 3, "new": 2, "replaced": 0, "skipped": 0, "failed": 1}
    assert L.counts_of("labelled=5 [Some Bank: expires 2026-11-01]") == {"labelled": 5}
    assert L.counts_of("") == {} and L.counts_of("nothing to say, x=y") == {}
    assert L.status_of("accounts=2 new=0 failed=1") == "error" and L.status_of("could not run") == "warn"
    assert L.counts_of("4047 tx") == {"tx": 4047} and L.status_of("critical=1 warn=4") == "warn" and L.status_of("critical=0 warn=0") == "ok"
    assert L.status_of("errors=2 warnings=0 info=1") == "warn" and L.status_of("errors=0 warnings=0 info=1") == "ok"
    assert L.status_of("skipped ([privacy] offline = true)") == "skipped" and L.status_of("not due (weekly)") == "skipped" and L.status_of("off") == "skipped"
    assert L.status_of("accounts=1 new=3") == "ok"


# ---------------------------------------------------------------- rotation

def test_the_json_log_rotates_by_size_and_keeps_the_newest_files(tmp_path):
    d = tmp_path / "logs"
    for i in range(40):
        log = L.RunLog(d, run_id=f"run{i:03d}", max_kb=1, keep=3)
        log.start()
        log.step("sync", "ok", i, {"new": i})
        log.end(0)
    names = sorted(p.name for p in d.iterdir())
    assert names == ["runs.jsonl", "runs.jsonl.1", "runs.jsonl.2", "runs.jsonl.3"]          # keep = 3 rotated files, the oldest are gone
    assert all(p.stat().st_size < 1024 + 400 for p in d.iterdir())
    ids = [r["run"] for r in L.runs_summary(L.read_events(d))]
    assert ids == sorted(ids) and ids[-1] == "run039" and "run000" not in ids        # oldest first across the rotated files, newest last
    assert len(ids) == len(set(ids))


def test_rotation_moves_files_up_and_drops_the_last(tmp_path):
    p = tmp_path / "x.log"
    for i in range(1, 5):
        p.write_text(f"gen{i}" * 100)
        assert L.rotate(p, 100, 2) is True
    assert not p.exists()
    assert (tmp_path / "x.log.1").read_text().startswith("gen4") and (tmp_path / "x.log.2").read_text().startswith("gen3")
    assert not (tmp_path / "x.log.3").exists()
    p.write_text("small")
    assert L.rotate(p, 100, 2) is False and p.read_text() == "small"


def test_old_rotated_files_are_deleted_but_never_the_live_one(tmp_path):
    for name in ("runs.jsonl", "runs.jsonl.1", "runs.jsonl.2", "schedule.log", "schedule.log.1", "notalog.1", "other.txt"):
        (tmp_path / name).write_text("x")
    old = time.time() - 100 * 86400
    for name in ("runs.jsonl", "runs.jsonl.2", "schedule.log", "schedule.log.1", "notalog.1"):
        os.utime(tmp_path / name, (old, old))
    gone = L.prune_old(tmp_path, 90)
    assert sorted(gone) == ["runs.jsonl.2", "schedule.log.1"]
    assert (tmp_path / "runs.jsonl").exists() and (tmp_path / "schedule.log").exists() and (tmp_path / "notalog.1").exists()
    assert (tmp_path / "runs.jsonl.1").exists()                              # recent: kept


def test_the_logs_this_process_writes_rotate_at_the_start_and_launchds_files_at_the_end(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "schedule.log").write_text("x" * 70_000)
    (d / "schedule.out.log").write_text("y" * 70_000)                     # launchd's stdout file: this process is writing to it
    (d / "schedule.err.log").write_text("short")
    log = L.RunLog(d, max_kb=64, keep=2)
    done = log.start()
    assert done == ["rotated schedule.log"] and (d / "schedule.log.1").exists()
    assert (d / "schedule.out.log").exists() and not (d / "schedule.out.log.1").exists()      # not renamed under the running job
    log.end(0)
    assert (d / "schedule.out.log.1").exists() and not (d / "schedule.out.log").exists() and (d / "schedule.err.log").read_text() == "short"


# ---------------------------------------------------------------- reading and the CLI

def make_runs(d):
    for rid, code, steps in (("r1", 0, [("sync", "ok", {"new": 4})]), ("r2", 1, [("sync", "error", {}), ("alerts", "warn", {}), ("eval", "skipped", {})])):
        log = L.RunLog(d, run_id=rid)
        log.start()
        for name, st, c in steps:
            log.step(name, st, 10, c, error="ApiError" if st == "error" else None)
        log.end(code)


def test_runs_summary_and_last_run(tmp_path):
    make_runs(tmp_path)
    runs = L.runs_summary(L.read_events(tmp_path))
    assert [r["run"] for r in runs] == ["r1", "r2"]
    assert runs[1]["outcome"] == "failed" and runs[1]["failed"] == ["sync"] and runs[1]["warned"] == ["alerts"] and runs[0]["outcome"] == "ok"
    assert L.last_run(tmp_path)["run"] == "r2" and L.last_run(tmp_path / "empty") is None
    assert [e["run"] for e in L.read_events(tmp_path, run="r1")] == ["r1"] * 3 and len(list(L.read_events(tmp_path, run="r"))) == 8


def test_a_torn_line_is_skipped(tmp_path):
    make_runs(tmp_path)
    with open(tmp_path / L.LOG_NAME, "a") as f:
        f.write('{"ts": "x", "run": "r3", "event": "st')               # a write cut short
    assert [r["run"] for r in L.runs_summary(L.read_events(tmp_path))] == ["r1", "r2"]


def test_the_logs_command(cfg, capsys):
    make_runs(cfg.log_dir)
    base = ["--insecure", "--config", str(cfg.config_path), "logs"]
    main(base)
    out = capsys.readouterr().out
    assert "r1" in out and "FAILED: sync" in out and "warnings: alerts" in out
    main(base + ["--tail", "2"])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "RUN END" in out[-1]
    main(base + ["--run", "r1"])
    out = capsys.readouterr().out
    assert "sync" in out and "new=4" in out and "r2" not in out
    main(base + ["--run", "r2", "--json"])
    ev = json.loads(capsys.readouterr().out)
    assert [e["event"] for e in ev] == ["run_start", "step", "step", "step", "run_end"]


def test_the_logs_command_with_no_log(cfg, capsys):
    main(["--insecure", "--config", str(cfg.config_path), "logs"])
    assert "no run logged yet" in capsys.readouterr().out


# ---------------------------------------------------------------- the scheduler writes them

def fake_claude(captured):
    def run(cmd, input=None, capture_output=None, text=None, timeout=None, env=None, cwd=None):
        captured.append((cmd, input))
        items = json.loads(input.strip().splitlines()[-1])
        results = [{"id": it["id"], "merchant": it["key"].title(), "category": "food.groceries", "confidence": 0.9, "recurring_hint": False} for it in items]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": {"results": results}, "total_cost_usd": 0.01}), stderr="")
    return run


@pytest.fixture
def offline(cfg):
    """A configuration where nothing leaves the machine: the scheduler skips the bank and the model, everything else runs."""
    cfg.privacy_offline = True
    return cfg


def test_run_daily_writes_one_event_per_step_with_durations_and_counts(offline):
    make_world(offline).close()
    assert sch.run_daily(offline, insecure=True, out=lambda *_: None) in (0, 1)
    ev = read(offline.log_dir)
    runs = L.runs_summary(ev)
    assert len(runs) == 1
    steps = {s["step"]: s for s in runs[0]["steps"]}
    assert {"sync", "consents", "normalize", "transfers", "classify", "memory", "analytics", "networth", "alerts", "security", "eval",
            "egress-journal"} <= set(steps)
    assert steps["sync"]["status"] == "skipped" and steps["classify"]["status"] == "skipped"
    assert steps["normalize"]["status"] == "ok" and all(isinstance(s["ms"], int) and s["ms"] >= 0 for s in steps.values())
    assert steps["analytics"]["counts"]["anomalies_open"] >= 0 and "recurring" in steps["analytics"]["counts"]
    assert ev[0]["event"] == "run_start" and ev[-1]["event"] == "run_end" and ev[-1]["ms"] >= 0
    assert len((offline.log_dir / "schedule.log").read_text().strip().splitlines()) == 1         # the one-line summary is still one line per run


def test_a_failing_step_logs_its_class_never_its_message(offline):
    make_runs(offline.log_dir)
    con = make_world(offline)
    con.close()

    def boom(*a, **k):
        raise RuntimeError("SENTINEL-EXC merchant ACME 12.34 EUR FR7630006000011234567890189")
    import coach.transfers as T
    orig = T.match_transfers
    T.match_transfers = boom
    try:
        code = sch.run_daily(offline, insecure=True, out=lambda *_: None)
    finally:
        T.match_transfers = orig
    assert code == 1
    last = L.last_run(offline.log_dir)
    assert last["failed"] == ["transfers"]
    assert [s for s in last["steps"] if s["step"] == "transfers"][0]["error"] == "RuntimeError"
    for p in offline.log_dir.iterdir():
        assert "SENTINEL" not in p.read_text() and "12.34" not in p.read_text() and "FR76300060" not in p.read_text(), p.name
    assert "transfers: ERROR RuntimeError" in (offline.log_dir / "schedule.log").read_text()


def test_a_run_rotates_the_logs_of_the_folder(offline):
    make_world(offline).close()
    d = offline.log_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "schedule.out.log").write_text("y" * 200_000)
    (d / "schedule.log").write_text("z" * 200_000)
    offline.log_max_kb = 64
    sch.run_daily(offline, insecure=True, out=lambda *_: None)
    assert (d / "schedule.log.1").exists() and len((d / "schedule.log").read_text().splitlines()) == 1     # the summary line of THIS run starts a new file
    assert (d / "schedule.out.log.1").exists() and not (d / "schedule.out.log").exists()                  # launchd's file, once the run is over


# ---------------------------------------------------------------- the weekly evaluation step

def test_the_weekly_evaluation_runs_once_a_week_warns_only_and_names_the_numbers(offline):
    con = make_world(offline)
    from coach.classify import corrections
    corrections.set_override(con, "fm0", "food.restaurants")
    corrections.set_override(con, "fm1", "food.groceries")
    G.bootstrap(con, offline)
    con.close()
    out = []
    sch.run_daily(offline, insecure=True, out=out.append)
    step = {s["step"]: s for s in L.last_run(offline.log_dir)["steps"]}["eval"]
    assert step["status"] == "ok" and step["counts"]["scored"] > 0 and "classifier_tx_pct" in step["counts"] and "pipeline_tx_pct" in step["counts"] and step["counts"]["warnings"] == 0
    assert (offline.log_dir / "eval-classify.last").exists()
    con = connect(offline, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM eval_runs WHERE label='schedule'").fetchone()[0] == 1
    con.close()
    sch.run_daily(offline, insecure=True, out=out.append)                    # the same week: not due, no second run
    step2 = {s["step"]: s for s in L.last_run(offline.log_dir)["steps"]}["eval"]
    assert step2["status"] == "skipped"
    con = connect(offline, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM eval_runs WHERE label='schedule'").fetchone()[0] == 1
    con.close()


def test_the_weekly_evaluation_is_skipped_without_a_gold_set_and_can_be_switched_off(offline):
    make_world(offline).close()
    sch.run_daily(offline, insecure=True, out=lambda *_: None)
    step = {s["step"]: s for s in L.last_run(offline.log_dir)["steps"]}["eval"]
    assert step["status"] == "skipped" and not (offline.log_dir / "eval-classify.last").exists()
    offline.schedule_eval = False
    sch.run_daily(offline, insecure=True, out=lambda *_: None)
    assert "eval" not in {s["step"] for s in L.last_run(offline.log_dir)["steps"]}


def test_a_broken_evaluation_never_fails_the_daily_job(offline, monkeypatch):
    make_world(offline).close()
    con = connect(offline, insecure=True)
    G.set_gold(con, "fm0", "food.restaurants")
    con.close()
    monkeypatch.setattr("coach.quality.classify_eval.run", lambda *a, **k: (_ for _ in ()).throw(ValueError("SENTINEL")))
    out = []
    sch.run_daily(offline, insecure=True, out=out.append)
    step = {s["step"]: s for s in L.last_run(offline.log_dir)["steps"]}["eval"]
    assert step["status"] == "warn" and "eval" not in L.last_run(offline.log_dir)["failed"]
    assert any("classification evaluation could not run: ValueError" in o for o in out) and not any("SENTINEL" in o for o in out)


# ---------------------------------------------------------------- privacy: sentinels in the data, none in the logs

SENT = ["ZXQVSHOP", "KRAVOLAINE", "WRENDELOT", "TIVARIEL", "99887766", "SENTINEL-NOTE"]


def test_a_scheduled_run_on_sentinel_data_leaves_no_sentinel_in_any_log(cfg, monkeypatch, db_key):
    con = connect(cfg, create=True)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Test Bank','FR','2099-01-01','t','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','MR KRAVOLAINE TIVARIEL','FR76','EUR','CACC','{}')")
    con.commit()
    con.close()
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: sentinel-ann\n    match:\n      merchant_key: '^ZXQVSHOP'\n    category: food.groceries\n    note: SENTINEL-NOTE 99887766\n")
    (cfg.memory_dir / "household.yaml").write_text("members:\n  - id: a\n    name: Kravolaine Tivariel\n    role: adult\n")
    client = FakeClient({"acc1": [{"transactions": [
        eb_tx("r1", "2025-10-03", -4.5, "CARTE 02/10 ZXQVSHOP BEAUVAIS", creditor="ZXQVSHOP"),
        eb_tx("r2", "2025-10-04", -9.0, "CARTE 03/10 WRENDELOT 99887766 BEAUVAIS", creditor="WRENDELOT"),
        eb_tx("r3", "2025-10-05", -60.0, "VIR INST M KRAVOLAINE TIVARIEL SENTINEL-NOTE"),
    ]}]})
    monkeypatch.setattr("coach.classify.llm.subprocess.run", fake_claude([]))
    out = []
    code = sch.run_daily(cfg, client=client, out=out.append)
    assert code in (0, 1)
    # what launchd would have captured on stdout goes to its file in the same folder
    (cfg.log_dir / "schedule.out.log").write_text("\n".join(map(str, out)))
    files = sorted(p for p in cfg.log_dir.iterdir() if p.is_file())
    assert {p.name for p in files} >= {"runs.jsonl", "schedule.log", "schedule.out.log"}
    for p in files:
        text = p.read_text()
        for s in SENT:
            assert s not in text, (p.name, s)
        assert "BEAUVAIS" not in text and "Kravolaine" not in text and "KRAVOLAINE" not in text
    # and the data really was there: the run worked on it
    con = connect(cfg)
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE description LIKE '%ZXQVSHOP%'").fetchone()[0] == 1
    # the structured log is numbers, step names and statuses
    for e in read(cfg.log_dir):
        assert set(e) <= {"ts", "run", "event", "version", "step", "status", "ms", "counts", "error", "outcome", "exit", "steps"}
        assert all(isinstance(v, (int, float)) for v in (e.get("counts") or {}).values())


def test_the_log_files_and_state_markers_are_private_and_the_offline_skip_says_offline(offline):
    con = make_world(offline)
    G.set_gold(con, "fm0", "food.restaurants")
    con.close()
    out = []
    sch.run_daily(offline, insecure=True, out=out.append)
    for name in ("schedule.log", "runs.jsonl", "eval-classify.last"):
        assert (offline.log_dir / name).stat().st_mode & 0o777 == 0o600, name
    assert any("classify: skipped ([privacy] offline = true)" in o for o in out) and any("sync: skipped ([privacy] offline = true)" in o for o in out)
