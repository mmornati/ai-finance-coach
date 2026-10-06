"""E10-3: the weekly digest: numbers from code (hand-checkable), the sections, once per week, the in-app feed, the external teaser
(never the digest text), the coach commentary only when enabled and recent."""
from __future__ import annotations

import datetime as dt

import pytest

from alerthelpers import ALL_ON, NOW, TODAY, Net, cand, settings
from coach import db as dbm
from coach.agent import insights as I
from coach.alerts import digest as D, engine, store
from coach.analytics import api as analytics_api
from coach.config import load_config
from helpers import add_bank, add_tx
from memhelpers import label

UTC = dt.timezone.utc


def week(n):                                  # a Wednesday n weeks before the week of TODAY (2026-10-04, a Sunday)
    return dt.date(2026, 9, 30) - dt.timedelta(days=7 * n)


@pytest.fixture
def world(cfg):
    """10 weeks of one 100.00 grocery payment, then a week of 160.00 groceries + 40.00 leisure; a monthly gym payment due on the 6th."""
    con = dbm.connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Testbank", "FR", [("a1", "FR7699999999999999999999", "Main account")])
    con.execute("UPDATE accounts SET owner='joint', purpose='main' WHERE uid='a1'")
    for n in range(1, 11):
        add_tx(con, "a1", f"g{n}", (week(n)).isoformat(), -100.0, "GROCER", "card")
    add_tx(con, "a1", "last1", "2026-10-02", -160.0, "GROCER", "card")
    add_tx(con, "a1", "last2", "2026-09-29", -40.0, "CINEMAX", "card")
    for i, m in enumerate((5, 6, 7, 8, 9)):
        add_tx(con, "a1", f"gym{m}", f"2026-{m:02d}-06", -30.0, "GYMCLUB", "direct_debit")
    add_tx(con, "a1", "sal", "2026-09-25", 2500.0, "VIR SALAIRE", "transfer_in")
    label(con, "GROCER", "food.groceries")
    label(con, "CINEMAX", "leisure.sports_activities")
    label(con, "GYMCLUB", "subscriptions.software_cloud")
    label(con, "VIR SALAIRE", "income.salary")
    con.execute("INSERT INTO balances VALUES ('a1','2026-10-03T08:00:00+00:00','CLBD',3000.0,'EUR','2026-10-03')")
    con.commit()
    (cfg.memory_dir / "budgets.yaml").write_text("budgets:\n  - id: groceries\n    category: food.groceries\n    monthly: 100\n")
    return con


@pytest.fixture
def ds(world, cfg):
    return analytics_api.build_dataset(world, cfg, TODAY)


def test_the_numbers_are_hand_checkable(world, cfg, ds):
    d = D.build(world, cfg, ds, settings(), today=TODAY)
    assert (d["week_start"], d["week_end"]) == ("2026-09-27", "2026-10-03")
    assert d["spent_c"] == 20000 and d["usual_c"] == 10000 and d["delta_pct"] == 100.0       # the gym series (a fixed commitment) is left out of both
    assert d["top_categories"][0] == {"category": "food.groceries", "spent": "160.00"} and len(d["top_categories"]) == 2
    assert d["top_categories"][1]["spent"] == "40.00"
    assert any(u["date"] == "2026-10-06" and u["amount"] == "-30.00" for u in d["upcoming"])
    assert d["budgets"]["set"] == 1 and d["budgets"]["problems"][0]["target"] == "food.groceries"
    assert d["savings"]["verified"] == 0 and d["savings"]["realised_monthly"] == "0.00"


def test_the_markdown_has_every_section_and_is_deterministic(world, cfg, ds):
    a = D.build(world, cfg, ds, settings(), today=TODAY)["markdown"]
    b = D.build(world, cfg, ds, settings(), today=TODAY)["markdown"]
    assert a == b
    for h in ("# Weekly summary, 2026-09-27 to 2026-10-03", "## Variable spending last week", "## Next 7 days", "## Alerts", "## Budgets", "## Savings tracker"):
        assert h in a
    assert "**200.00 EUR** of variable spending" in a and "against a usual week of 100.00 EUR (+100.0%)" in a and "- food.groceries: 160.00 EUR" in a
    assert "- 2026-10-06: Gymclub, -30.00 EUR" in a and "- food.groceries: over (160.00 of 100.00 EUR" in a
    assert "No open alert." in a and "nothing in this summary is written by a model" in a


def test_the_alerts_section_lists_the_open_alerts(world, cfg, ds):
    engine.sync_events(world, [cand(severity="high", title="Possible duplicate charge")], settings(), now=NOW)
    world.commit()
    d = D.build(world, cfg, ds, settings(), today=TODAY)
    assert d["alerts"]["open"] == 1 and d["alerts"]["high"] == 1 and "[high] Possible duplicate charge" in d["markdown"]


def test_no_history_says_so_instead_of_inventing_a_usual_week(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Testbank", "FR", [("a1", "FR7699999999999999999999", "Main")])
    add_tx(con, "a1", "x", "2026-10-02", -20.0, "SHOP", "card")
    label(con, "SHOP", "shopping.clothing")
    d = D.build(con, cfg, analytics_api.build_dataset(con, cfg, TODAY), settings(), today=TODAY)
    assert d["usual_c"] is None and d["delta_pct"] is None and any("Not enough history" in n for n in d["notes"])
    assert "against a usual week" not in d["markdown"]


def test_stale_data_is_flagged(cfg):
    con = dbm.connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Testbank", "FR", [("a1", "FR7699999999999999999999", "Main")])
    add_tx(con, "a1", "x", "2026-09-01", -20.0, "SHOP", "card")
    d = D.build(con, cfg, analytics_api.build_dataset(con, cfg, TODAY), settings(), today=TODAY)
    assert any("latest transaction is from 2026-09-01" in n for n in d["notes"])


# ---------------------------------------------------------------- once a week, in the in-app feed

def test_due_is_once_since_the_digest_day(world, cfg, ds):
    s = settings(weekly_digest_day="mon")
    assert D.due(world, s, TODAY)[0] is True                          # never made
    out = D.run_weekly(world, cfg, ds, s, now=NOW)
    assert out["status"] == "done" and D.due(world, s, TODAY)[0] is False
    assert D.run_weekly(world, cfg, ds, s, now=NOW)["status"] == "skipped"
    world.execute("UPDATE insights SET created=?", ("2026-09-29T08:00:00+00:00",))     # made on the Tuesday of the week before
    world.commit()
    assert D.due(world, s, dt.date(2026, 10, 5))[0] is True          # the next Monday: due again
    assert D.due(world, s, dt.date(2026, 10, 4))[0] is False         # the Sunday before it: this week's one still counts
    world.execute("UPDATE insights SET created=?", ("2026-10-05T08:00:00+00:00",))
    assert D.due(world, s, dt.date(2026, 10, 9))[0] is False and D.due(world, s, dt.date(2026, 10, 12))[0] is True


def test_the_digest_goes_to_the_in_app_feed_as_an_insight(world, cfg, ds):
    out = D.run_weekly(world, cfg, ds, settings(), now=NOW)
    row = I.get(world, out["insight_id"])
    assert row["kind"] == "digest" and row["skill"] == "weekly-local" and row["backend"] == "code" and row["status"] == "new"
    assert row["title"] == "Weekly summary 2026-10-04" and row["body"].startswith("# Weekly summary") and row["findings"][0]["spent_c"] == 20000
    items, _ = I.listing(world, today=TODAY)
    assert row["id"] in [i["id"] for i in items]


def test_weekly_digest_off_makes_nothing_but_force_does(world, cfg, ds):
    s = settings(weekly_digest=False)
    assert D.run_weekly(world, cfg, ds, s, now=NOW) == {"status": "off"}
    assert D.run_weekly(world, cfg, ds, s, now=NOW, force=True)["status"] == "done"


def test_the_link_is_the_local_address_and_says_where_it_works(world, cfg, ds):
    md = D.build(world, cfg, ds, settings(), today=TODAY)["markdown"]
    assert f"http://{cfg.ui_host}:{cfg.ui_port}/alerts" in md and "Tailscale" in md and "machine that runs the app" in md
    assert "https://coach.example.net/" in D.build(world, cfg, ds, settings(app_url="https://coach.example.net/"), today=TODAY)["markdown"]


# ---------------------------------------------------------------- channels: a teaser, once, same gates as alerts

def test_nothing_is_sent_unless_weekly_digest_to_channels_is_on(world, cfg, ds):
    net = Net()
    out = D.run_weekly(world, cfg, ds, settings(**ALL_ON), now=NOW, transports=net.transports(), tz=UTC)
    assert out["status"] == "done" and out["deliveries"] == [] and net.total() == 0


def test_the_teaser_goes_once_to_each_enabled_channel(world, cfg, ds):
    net, s = Net(), settings(weekly_digest_to_channels=True, **ALL_ON)
    out = D.run_weekly(world, cfg, ds, s, now=NOW, transports=net.transports(), tz=UTC)
    assert {r["channel"]: r["status"] for r in out["deliveries"]} == {"macos": "sent", "ntfy": "sent", "email": "sent", "telegram": "sent"}
    assert net.posts[0]["body"] == "Coach: your weekly summary is ready. Open the app." and net.mails[0]["subject"] == "Coach weekly summary"
    assert "Spent 200.00 EUR" in net.macos[0][-2]                      # the local notification may carry the figure
    d = D.build(world, cfg, ds, s, today=TODAY)
    again = D.deliver(world, cfg, s, d, out["insight_id"], now=NOW + dt.timedelta(hours=1), transports=net.transports(), tz=UTC)
    assert {r["status"] for r in again} == {"nothing"} and net.total() == 4
    assert [r[1] for r in world.execute("SELECT channel, what FROM alert_deliveries")] == ["digest"] * 4


def test_summary_teaser_has_rounded_figures_only(world, cfg, ds):
    net, s = Net(), settings(weekly_digest_to_channels=True, external_detail="summary", ntfy=ALL_ON["ntfy"])
    D.run_weekly(world, cfg, ds, s, now=NOW, transports=net.transports(), tz=UTC)
    assert net.posts[0]["body"] == "Coach weekly summary: about 200 EUR of variable spending last week (usual week about 100 EUR), 0 open alerts. Open the app."


def test_the_digest_teaser_respects_quiet_hours_and_the_weekly_limit(world, cfg, ds):
    net, s = Net(), settings(weekly_digest_to_channels=True, quiet_hours="22:00-08:00", max_per_week=1, ntfy=ALL_ON["ntfy"])
    night = NOW.replace(hour=23)
    out = D.run_weekly(world, cfg, ds, s, now=night, transports=net.transports(), tz=UTC)
    assert out["deliveries"][0]["status"] == "deferred" and net.total() == 0
    d = D.build(world, cfg, ds, s, today=TODAY)
    out2 = D.deliver(world, cfg, s, d, out["insight_id"], now=night + dt.timedelta(hours=10), transports=net.transports(), tz=UTC)
    assert out2[0]["status"] == "sent"
    # the limit (1 a week) now holds for an alert on the same channel
    store.mark_baselined(world, "ntfy", 0)
    engine.sync_events(world, [cand()], s, now=NOW)
    world.commit()
    disp = engine.dispatch(world, cfg, s, now=night + dt.timedelta(hours=11), today=TODAY, transports=net.transports(), tz=UTC)
    assert disp[0]["status"] == "deferred" and "weekly limit" in disp[0]["reason"]


def test_digest_dry_run_sends_and_records_nothing(world, cfg, ds):
    net, s = Net(), settings(weekly_digest_to_channels=True, **ALL_ON)
    d = D.build(world, cfg, ds, s, today=TODAY)
    out = D.deliver(world, cfg, s, d, "cin_x", now=NOW, dry_run=True, transports=net.transports(), tz=UTC)
    assert {r["status"] for r in out} == {"would_send"} and net.total() == 0
    assert world.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 0
    assert out[1]["message"]["body"] == "Coach: your weekly summary is ready. Open the app."


# ---------------------------------------------------------------- the coach commentary (opt-in)

def with_coach_weekly(cfg):
    cfg.config_path.write_text(cfg.config_path.read_text() + "\n[coach]\nschedule_weekly = true\n")
    return load_config(cfg.config_path, env={})


def add_coach_digest(con, when):
    iid = I.add(con, kind="digest", title="Weekly digest", body="Groceries drove the week; nothing alarming.", skill="digest-weekly")
    con.execute("UPDATE insights SET created=? WHERE id=?", (when, iid))
    con.commit()
    return iid


def test_commentary_is_included_only_when_schedule_weekly_is_on_and_recent(world, cfg):
    add_coach_digest(world, "2026-10-02T06:00:00+00:00")
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    assert D.build(world, cfg, ds, settings(), today=TODAY)["commentary"] is None                     # [coach] schedule_weekly is off
    cfg2 = with_coach_weekly(cfg)
    d = D.build(world, cfg2, ds, settings(), today=TODAY)
    assert d["commentary"]["body"].startswith("Groceries drove") and "## Coach commentary (AI-generated, written by the model" in d["markdown"]
    world.execute("UPDATE insights SET created='2026-09-01T06:00:00+00:00'")
    world.commit()
    assert D.build(world, cfg2, ds, settings(), today=TODAY)["commentary"] is None                    # older than 7 days


def test_commentary_never_reaches_a_channel(world, cfg):
    add_coach_digest(world, "2026-10-02T06:00:00+00:00")
    cfg2 = with_coach_weekly(cfg)
    ds = analytics_api.build_dataset(world, cfg2, TODAY)
    net = Net()
    D.run_weekly(world, cfg2, ds, settings(weekly_digest_to_channels=True, external_detail="summary", **ALL_ON), now=NOW,
                 transports=net.transports(), tz=UTC)
    blob = " ".join(p["body"] for p in net.posts) + " ".join(m["body"] for m in net.mails)
    assert "Groceries drove" not in blob and "commentary" not in blob.lower()



def test_a_weekly_summary_with_coach_commentary_is_labelled_ai_and_checked_but_a_plain_one_is_not(world, cfg):
    """E11-5: the deterministic summary is not AI text; with the model's commentary inside it is labelled and goes through the compliance check."""
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    plain = D.build(world, cfg, ds, settings(), today=TODAY)
    p = I.get(world, D.store_digest(world, plain))
    assert p["ai_generated"] is False and p["ai_label"] is None and p["compliance"] == []
    iid = I.add(world, kind="digest", title="Weekly digest", body="You should invest in an ETF every month.", skill="digest-weekly", backend="claude-code")
    world.execute("UPDATE insights SET created='2026-10-02T06:00:00+00:00' WHERE id=?", (iid,))
    world.commit()
    cfg2 = with_coach_weekly(cfg)
    withc = D.build(world, cfg2, ds, settings(), today=TODAY)
    row = I.get(world, D.store_digest(world, withc))
    assert row["ai_generated"] is True and "AI-generated" in row["ai_label"]
    assert "recommendation" in row["compliance"] and "General information only" in row["compliance_banner"]
