"""E13-1: `coach schedule loop`: the daily job without launchd (containers, Linux). Fake clock, fake sleep, fake runner: no real waiting."""
from datetime import datetime, timedelta

import pytest

from coach import schedule as S


class Clock:
    def __init__(self, start):
        self.now = start
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def test_next_run_after():
    assert S.next_run_after(datetime(2026, 10, 6, 6, 0), "07:30") == datetime(2026, 10, 6, 7, 30)
    assert S.next_run_after(datetime(2026, 10, 6, 8, 0), "07:30") == datetime(2026, 10, 7, 7, 30)
    assert S.next_run_after(datetime(2026, 10, 6, 7, 30), "07:30") == datetime(2026, 10, 7, 7, 30)          # exactly now = tomorrow
    assert S.next_run_after(datetime(2026, 10, 31, 23, 59, 30), "00:05") == datetime(2026, 11, 1, 0, 5)
    assert S.next_run_after(datetime(2026, 12, 31, 12, 0), "07:30") == datetime(2027, 1, 1, 7, 30)


def test_the_loop_sleeps_in_slices_of_at_most_a_minute_then_runs_at_the_time(cfg):
    clk = Clock(datetime(2026, 10, 6, 7, 20))
    runs, said = [], []
    cfg.schedule_time = "07:30"
    code = S.loop(cfg, clock=clk, sleep=clk.sleep, runner=lambda: runs.append(clk.now) or 0, out=said.append, max_runs=1)
    assert code == 0 and runs == [datetime(2026, 10, 6, 7, 30)]
    assert max(clk.slept) <= 60 and sum(clk.slept) == 600 and len(clk.slept) == 10
    assert any("next run at 2026-10-06T07:30" in s for s in said) and any("exit code 0" in s for s in said)


def test_the_loop_repeats_daily_and_a_failing_run_does_not_stop_it(cfg):
    clk = Clock(datetime(2026, 10, 6, 8, 0))
    cfg.schedule_time = "07:30"
    codes = iter([1, 0, 2])
    seen = []
    S.loop(cfg, clock=clk, sleep=clk.sleep, runner=lambda: (seen.append(clk.now), next(codes))[1], out=lambda *a: None, max_runs=3)
    assert seen == [datetime(2026, 10, 7, 7, 30), datetime(2026, 10, 8, 7, 30), datetime(2026, 10, 9, 7, 30)]


def test_a_restart_does_not_run_the_missed_time_but_run_now_does(cfg):
    clk = Clock(datetime(2026, 10, 6, 15, 0))
    cfg.schedule_time = "07:30"
    seen = []
    S.loop(cfg, clock=clk, sleep=clk.sleep, runner=lambda: seen.append(clk.now) or 0, out=lambda *a: None, max_runs=1)
    assert seen == [datetime(2026, 10, 7, 7, 30)]                                # nothing ran at 15:00
    clk = Clock(datetime(2026, 10, 6, 15, 0))
    seen.clear()
    S.loop(cfg, clock=clk, sleep=clk.sleep, runner=lambda: seen.append(clk.now) or 0, out=lambda *a: None, run_now=True, max_runs=2)
    assert seen == [datetime(2026, 10, 6, 15, 0), datetime(2026, 10, 7, 7, 30)]


def test_the_default_runner_is_the_daily_job_of_the_same_configuration(cfg, monkeypatch):
    called = []
    monkeypatch.setattr(S, "run_daily", lambda c, insecure=False, **k: called.append((c, insecure)) or 0)
    clk = Clock(datetime(2026, 10, 6, 7, 29))
    S.loop(cfg, insecure=False, clock=clk, sleep=clk.sleep, out=lambda *a: None, max_runs=1)
    assert called == [(cfg, False)]


def test_the_command_is_registered_with_run_now_and_no_agents_dir():
    from coach.cli import build_parser
    a = build_parser().parse_args(["schedule", "loop", "--run-now"])
    assert a.sched_cmd == "loop" and a.run_now is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["schedule", "loop", "--agents-dir", "x"])
