"""E12-4: the LLM usage view: per job / model / day, notional cost, where the calls went, the monthly threshold and its local-only alert."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from alerthelpers import ALL_ON, NOW, Net, settings
from coach import db as dbm
from coach.alerts import digest as D, engine, signals, store
from coach.alerts.settings import KINDS, LOCAL_ONLY_KINDS
from coach.analytics import api as analytics_api
from coach.classify.backends import Usage, record_usage
from coach.cli import main
from coach.quality import usage as U
from helpers import add_bank, add_tx

UTC = dt.timezone.utc
TODAY = dt.date(2026, 10, 4)


@pytest.fixture
def con(cfg):
    c = dbm.connect(cfg, insecure=True, create=True)
    yield c
    c.close()


def row(con, days_ago, backend, model, purpose, tin, tout, cost, dur=2.0, items=10, now=NOW):
    ts = (now - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    con.execute("""INSERT INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd,
                   cost_is_estimate, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (ts, backend, model, purpose, items, tin, tout, 0, 0, cost, 1, dur))


@pytest.fixture
def usage_rows(con):
    row(con, 1, "claude-code", "sonnet", "label", 1000, 200, 0.10, 10.0)
    row(con, 1, "claude-code", "sonnet", "label", 2000, 300, 0.20, 20.0)
    row(con, 2, "claude-code", "sonnet", "coach:ask", 5000, 400, 0.30, 15.0)
    row(con, 2, "claude-code", "sonnet", "coach:monthly-review", 4000, 500, 0.25, 12.0)
    row(con, 3, "anthropic-api", "claude-haiku-4-5", "compare", 3000, 100, 0.0035, 4.0)
    row(con, 3, "ollama", "llama3.1", "label", 800, 90, 0.0, 30.0)
    row(con, 4, "anthropic-api", "mystery-model", "enrich", 700, 70, None, 3.0)                  # no known price
    row(con, 40, "claude-code", "sonnet", "label", 9999, 999, 9.99, 99.0)                         # outside a 30 day window
    con.commit()


def test_jobs_are_named_from_the_purpose():
    assert [U.job_of(p) for p in ("label", "compare", "enrich", "doc_extract", "eval", "coach:ask", "coach:digest-weekly",
                                  "coach:monthly-review", "eval:coach", "weird")] == [
        "classify run", "classify compare", "classify enrich", "memory doc extract", "eval models", "coach ask", "coach digest",
        "coach skill monthly-review", "eval coach", "weird"]


def test_summary_groups_by_job_backend_and_model_with_hand_checked_totals(con, cfg, usage_rows):
    d = U.summary(con, cfg, 30, NOW)
    lines = {(x["job"], x["backend"], x["model"]): x for x in d["lines"]}
    lab = lines[("classify run", "claude-code", "sonnet")]
    assert (lab["calls"], lab["tokens_in"], lab["tokens_out"], lab["cost_usd"], lab["duration_s"], lab["avg_duration_s"]) == (2, 3000, 500, 0.30, 30.0, 15.0)
    assert lab["notional"] is True and lines[("classify compare", "anthropic-api", "claude-haiku-4-5")]["notional"] is False
    assert lines[("classify run", "ollama", "llama3.1")]["cost_usd"] == 0.0
    unk = lines[("classify enrich", "anthropic-api", "mystery-model")]
    assert unk["cost_usd"] is None and unk["cost_unknown_calls"] == 1                              # unknown, never 0
    t = d["totals"]
    assert t["calls"] == 7 and t["tokens_in"] == 1000 + 2000 + 5000 + 4000 + 3000 + 800 + 700 and t["unknown_cost_calls"] == 1
    assert t["cost_usd"] == 0.8535                                                                  # 0.10+0.20+0.30+0.25+0.0035 (the 40 day old call is out)
    assert t["notional_cost_usd"] == 0.85 and t["estimated_cost_usd"] == 0.0035
    by_job = {j["job"]: j for j in d["by_job"]}
    assert by_job["classify run"]["calls"] == 3 and by_job["classify run"]["cost_usd"] == 0.3
    assert by_job["coach skill monthly-review"]["cost_usd"] == 0.25 and "coach ask" in by_job


def test_the_day_series_covers_every_day_of_the_period(con, cfg, usage_rows):
    d = U.summary(con, cfg, 7, NOW)
    assert [x["date"] for x in d["by_day"]] == [(NOW.date() - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    day = {x["date"]: x for x in d["by_day"]}
    assert day["2026-10-03"]["calls"] == 2 and day["2026-10-03"]["cost_usd"] == 0.3 and day["2026-10-03"]["tokens_in"] == 3000
    assert day["2026-10-04"]["calls"] == 0                                                          # an empty day is a zero bar, not a gap
    assert sum(x["calls"] for x in d["by_day"]) == 7


def test_an_empty_period(con, cfg):
    d = U.summary(con, cfg, 30, NOW)
    assert d["totals"]["calls"] == 0 and d["lines"] == [] and "no call recorded" in U.format_report(d)


def test_destinations_come_from_the_egress_journal_without_a_payload(con, cfg, usage_rows):
    ts = NOW.isoformat(timespec="seconds")
    con.execute("INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, "llm.claude-code", "api.anthropic.com (via the claude CLI)", 5000, "classify.label", "item-redact", "allowed", "", 0))
    con.execute("INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, "llm.claude-code", "api.anthropic.com (via the claude CLI)", 7000, "eval.models", "item-redact", "denied", "local_only", 0))
    con.execute("INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, "enable_banking", "api.enablebanking.com", 10, "sync", "none", "allowed", "", 0))
    con.commit()
    d = U.summary(con, cfg, 30, NOW)
    kinds = {(x["kind"], x["purpose"]): x for x in d["destinations"]}
    assert kinds[("llm.claude-code", "classify.label")]["bytes"] == 5000 and kinds[("llm.claude-code", "eval.models")]["denied"] == 1
    assert not any(x["kind"] == "enable_banking" for x in d["destinations"])                        # only where the AI calls went
    assert set(d["destinations"][0]) == {"kind", "host", "purpose", "calls", "bytes", "denied", "web", "last"}      # no payload column
    assert "api.anthropic.com" in U.format_report(d)


def test_the_month_against_the_threshold(con, cfg, usage_rows):
    cfg.usage_monthly_warn_usd = 0.0
    assert U.month_status(con, cfg, NOW)["level"] is None and U.month_status(con, cfg, NOW)["threshold_usd"] is None
    cfg.usage_monthly_warn_usd = 2.0
    ms = U.month_status(con, cfg, NOW)
    assert ms["month"] == "2026-10" and ms["cost_usd"] == 0.8535 and ms["ratio"] == 0.427 and ms["level"] is None
    cfg.usage_monthly_warn_usd = 0.8
    assert U.month_status(con, cfg, NOW)["level"] == "medium"
    cfg.usage_monthly_warn_usd = 0.4
    assert U.month_status(con, cfg, NOW)["level"] == "high"                                         # twice the threshold
    cfg.usage_include_notional = False                                                              # a subscription's notional cost may be left out
    assert U.month_status(con, cfg, NOW)["cost_usd"] == 0.0035 and U.month_status(con, cfg, NOW)["level"] is None


def test_cache_tokens_are_shown(con, cfg):
    con.execute("""INSERT INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd,
                   cost_is_estimate, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (NOW.isoformat(timespec="seconds"), "claude-code", "sonnet", "label", 1, 10, 5, 800, 120, 0.1, 1, 1.0))
    con.commit()
    d = U.summary(con, cfg, 7, NOW)
    assert d["lines"][0]["cache_read_tokens"] == 800 and d["totals"]["cache_write_tokens"] == 120
    assert "cache r/w" in U.format_report(d) and "800/120" in U.format_report(d)


def test_the_command_prints_and_dumps_json(cfg, con, usage_rows, capsys):
    con.close()
    main(["--insecure", "--config", str(cfg.config_path), "usage", "--days", "400"])
    out = capsys.readouterr().out
    assert "classify run" in out and "NOTIONAL" in out and "this month" in out
    main(["--insecure", "--config", str(cfg.config_path), "usage", "--days", "400", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["days"] == 400 and d["totals"]["calls"] == 8


def test_usage_of_a_real_recorded_call(con, cfg):
    record_usage(con, Usage("claude-code", "sonnet", "eval", 5, 100, 20, 0, 0, 0.05, True, 3.0))
    con.commit()
    d = U.summary(con, cfg, 1)
    assert d["lines"][0]["job"] == "eval models" and d["lines"][0]["cost_usd"] == 0.05


# ---------------------------------------------------------------- the alert: local feed only

def test_the_alert_kind_is_registered_and_local_only():
    assert "llm_usage_high" in KINDS and LOCAL_ONLY_KINDS == ("llm_usage_high", "kid_budget")


def test_no_alert_without_a_threshold_and_one_event_per_month_when_it_is_passed(con, cfg, usage_rows):
    assert U.candidate(con, cfg, NOW) is None
    cfg.usage_monthly_warn_usd = 0.8
    c = U.candidate(con, cfg, NOW)
    assert (c.kind, c.key, c.severity) == ("llm_usage_high", "llm-usage:2026-10", "medium") and "0.85" in c.title
    assert c.payload == {"month": "2026-10", "cost_usd": 0.8535, "threshold_usd": 0.8, "ratio": 1.067}
    cfg.usage_monthly_warn_usd = 0.4
    assert U.candidate(con, cfg, NOW).severity == "high" and U.candidate(con, cfg, NOW).id == c.id       # the same event, escalated


def test_the_alert_engine_keeps_it_in_the_local_feed_and_no_channel_ever_gets_it(con, cfg, usage_rows):
    cfg.usage_monthly_warn_usd = 0.4
    s = settings(**ALL_ON)
    net = Net()
    c = U.candidate(con, cfg, NOW)
    ev = engine.sync_events(con, [c], s, now=NOW, today=NOW.date())
    disp = engine.dispatch(con, cfg, s, now=NOW, today=NOW.date(), transports=net.transports(), tz=UTC)
    con.commit()
    assert ev["new"] == [c.id] and net.total() == 0                                                 # not to ntfy, e-mail, Telegram or macOS
    assert all(d["status"] == "nothing" for d in disp)
    e = store.get(con, c.id)
    assert e["status"] == "new" and e["channels_sent"] == {} and e["kind"] == "llm_usage_high"
    assert [x["id"] for x in store.open_events(con, NOW.date())] == [c.id]                          # but it IS in the feed
    assert con.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 0


def test_the_weekly_digest_does_not_count_it_either(cfg, con, usage_rows):
    add_bank(con, "s1", "Testbank", "FR", [("a1", "FR7699999999999999999999", "Main account")])
    add_tx(con, "a1", "t1", "2026-09-29", -10.0, "SHOP", "card")
    con.commit()
    cfg.usage_monthly_warn_usd = 0.4
    s = settings()
    c = U.candidate(con, cfg, NOW)
    engine.sync_events(con, [c], s, now=NOW, today=NOW.date())
    con.commit()
    ds = analytics_api.build_dataset(con, cfg, TODAY)
    d = D.build(con, cfg, ds, s, today=TODAY)
    assert d["alerts"]["open"] == 0 and d["alerts"]["items"] == []


def test_all_candidates_includes_it_when_the_threshold_is_passed(con, cfg, usage_rows):
    cfg.usage_monthly_warn_usd = 0.4
    ds = analytics_api.build_dataset(con, cfg, TODAY)
    now = dt.datetime.now(UTC)
    for i in range(2):                                           # the real clock: a call of today
        row(con, 0, "claude-code", "sonnet", "label", 10, 10, 5.0, now=now)
    con.commit()
    kinds = {c.kind for c in signals.all_candidates(con, cfg, ds, settings(), now)}
    assert "llm_usage_high" in kinds


def test_config_validation_and_defaults(tmp_path):
    from coach.config import ConfigError, load_config
    p = tmp_path / "c.toml"
    p.write_text('[usage]\nmonthly_warn_usd = 12.5\ninclude_notional = false\n[logs]\nmax_kb = 64\nkeep = 3\nmax_age_days = 30\n[schedule]\neval = false\n')
    c = load_config(p, env={})
    assert (c.usage_monthly_warn_usd, c.usage_include_notional, c.log_max_kb, c.log_keep, c.log_max_age_days, c.schedule_eval) == (12.5, False, 64, 3, 30, False)
    for bad in ('[usage]\nmonthly_warn_usd = -1\n', '[usage]\nmonthly_warn_usd = "5"\n', '[logs]\nmax_kb = 1\n', '[logs]\nkeep = 0\n'):
        p.write_text(bad)
        with pytest.raises(ConfigError):
            load_config(p, env={})
