"""E10-1 privacy: what leaves the machine through ntfy / e-mail / Telegram is MINIMAL and passes the guard, in both detail modes.
SENTINELS (member names, merchant person names, IBANs, the contact block, the employer, towns, contract numbers, bank and merchant
names) are planted in the data and must never be in an external message, whatever the event titles say."""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from alerthelpers import ALL_ON, NOW, SENTINELS, TODAY, Net, build_alert_world, cand, settings
from coach import db as dbm
from coach.alerts import digest as D, engine, messages as M, store
from coach.analytics import api as analytics_api
from coach.analytics.identity import fold


@pytest.fixture
def world(cfg):
    con = build_alert_world(cfg)
    yield con
    con.close()


@pytest.fixture
def events(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    res = engine.run(world, cfg, ds, s=settings(), now=NOW, send=False)
    return res["events"]


def leaks(text: str) -> list[str]:
    low = fold(text)
    return [s for s in SENTINELS if fold(s) in low]


def test_the_sentinels_really_are_in_the_local_event_text(events):
    """Guard against a vacuous test: the local titles and payloads DO carry names, banks and merchants."""
    local = json.dumps([[e["title"], e["body"], e["payload"]] for e in events], ensure_ascii=False)
    found = set(leaks(local))
    assert {"Fortuneo", "StreamBox", "Cloudbox", "Homebank", "Rossi"} <= found or len(found) >= 4, found


@pytest.mark.parametrize("detail", ["minimal", "summary"])
def test_external_messages_hold_no_sentinel_in_either_mode(world, cfg, events, detail):
    guard = M.external_guard(cfg, world)
    assert guard is not None
    for subset in (events, events[:1], [e for e in events if e["severity"] == "high"], [e for e in events if e["kind"] == "unusual_charge"]):
        if not subset:
            continue
        m = M.compose_external(subset, detail, guard)
        text = m.title + "\n" + m.body
        assert leaks(text) == [], (detail, text)
        assert not re.search(r"[A-Za-z]{2}\d{2}[A-Za-z0-9]{10,}|@|https?://|/Users/", text)


def test_minimal_mode_is_exactly_the_documented_text(events):
    high = sum(1 for e in events if e["severity"] == "high")
    m = M.compose_external(events, "minimal", None)
    assert m.body == f"Coach: {len(events)} new alerts ({high} high). Open the app." and m.detail == "minimal"
    assert M.compose_external(events[:1], "minimal", None).body in ("Coach: 1 new alert. Open the app.", "Coach: 1 new alert (1 high). Open the app.")


def test_minimal_mode_has_no_amount_no_kind_no_digit_but_the_counts(events):
    text = M.compose_external(events, "minimal", None).body
    assert re.fullmatch(r"Coach: \d+ new alerts( \(\d+ high\))?\. Open the app\.", text)
    assert not any(label.split()[0].lower() in text.lower() for label in M.KIND_LABEL.values() if label.split()[0] not in ("Open", "Coach"))


class AllowAll:
    def violations(self, obj):
        return []


def test_summary_mode_is_kind_severity_and_a_rounded_amount_only(events):
    text = M.compose_external(events, "summary", AllowAll()).body
    lines = text.splitlines()
    assert lines[0].startswith("Coach: ") and lines[-1] == "Open the app."
    for ln in lines[1:-1]:
        assert re.fullmatch(r"- (?:%s) \((?:low|medium|high)\)(?:, about \d+ EUR)?|- and \d+ more" % "|".join(map(re.escape, M.KIND_LABEL.values())), ln), ln
    amounts = [int(x) for x in re.findall(r"about (\d+) EUR", M.compose_external([e for e in events if e["kind"] in M.AMOUNT_KINDS], "summary", AllowAll()).body)]
    assert amounts and all(a % 10 == 0 or a % 100 == 0 for a in amounts)


def test_summary_amount_rounding():
    assert [M.round_amount(c) for c in (0, 499, 500, 1499, 15234, 99999, 100000, 123456, 5000000)] == [0, 0, 10, 10, 150, 1000, 1000, 1200, 50000]
    assert M.round_amount(-15234) == 150


def test_summary_never_shows_amounts_of_balances_or_kinds_that_could_identify(events):
    text = M.compose_external([e for e in events if e["kind"] == "low_balance"], "summary", AllowAll()).body
    assert "EUR" not in text


def test_a_summary_the_guard_refuses_falls_back_to_minimal(world, cfg):
    class Guard:                     # the guard of a household whose member is called "Unusual"
        def violations(self, obj):
            return ["household name"] if "unusual" in fold(json.dumps(obj)) else []
    ev = [{"id": "x", "kind": "unusual_charge", "severity": "high", "title": "t", "body": "", "payload": {"amount_c": 15000}}]
    m = M.compose_external(ev, "summary", Guard())
    assert m.detail == "minimal" and m.body == "Coach: 1 new alert (1 high). Open the app." and "refused" in m.note


def test_a_minimal_text_the_guard_refuses_is_not_sent_at_all(world, cfg):
    class Guard:
        def violations(self, obj):
            return ["household name"]
    ev = [{"id": "x", "kind": "consent", "severity": "high", "title": "t", "body": "", "payload": {}}]
    assert M.compose_external(ev, "minimal", Guard()) is None


def test_an_unavailable_guard_degrades_summary_to_minimal():
    ev = [{"id": "x", "kind": "unusual_charge", "severity": "high", "title": "t", "body": "", "payload": {"amount_c": 15000}}]
    assert M.compose_external(ev, "summary", None).detail == "minimal"


def test_the_guard_is_built_from_the_real_household(world, cfg):
    g = M.external_guard(cfg, world)
    for s in ("Anna", "Rossi", "Zephyrtech Industries", "Montpellier", "Sète", "Lycée Pasteur", "TC-778899", "FR7600000000000000000001", "M OU MME ROSSI ANNA", "rue des Lilas", "anna.rossi@example.org", "Fortuneo"):
        assert g.violations(f"Coach alert about {s}"), s
    assert g.violations("Coach: 2 new alerts (1 high). Open the app.") == []


@pytest.mark.parametrize("detail", ["minimal", "summary"])
def test_what_each_external_transport_receives_holds_no_sentinel(world, cfg, detail):
    net = Net()
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    s = settings(external_detail=detail, **ALL_ON)
    res = engine.run(world, cfg, ds, s=s, now=NOW, transports=net.transports(), tz=dt.timezone.utc)
    assert {d["channel"]: d["status"] for d in res["dispatch"]} == {"macos": "sent", "ntfy": "sent", "email": "sent", "telegram": "sent"}
    assert len(net.posts) == 2 and len(net.mails) == 1 and len(net.macos) == 1
    for p in net.posts:
        sent = p["body"] + json.dumps({k: v for k, v in p["headers"].items() if k != "Authorization"})
        body = json.loads(p["body"])["text"] if "telegram" in p["url"] else p["body"]
        assert leaks(body + sent) == [], (detail, body)
    m = net.mails[0]
    assert leaks(m["subject"] + m["body"]) == [], (detail, m["body"])
    assert "Open the app." in m["body"]


def test_the_local_macos_notification_may_show_names_but_never_an_iban_or_a_long_number(world, cfg):
    ev = [{"id": "x", "kind": "unusual_charge", "severity": "high",
           "title": "Transfer to FR7600000000000000000001 and FR76 3000 6000 0112 3456 7890 189, ref 123456789012, mail a.b@example.org",
           "body": "", "payload": {}}]
    m = M.compose_local(ev)
    assert "FR76" not in m.body and "123456789012" not in m.body and "@" not in m.body
    assert "[iban]" in m.body and m.detail == "local"
    shown = M.compose_local([{"id": "y", "kind": "consent", "severity": "medium", "title": "Fortuneo (FR): consent EXPIRING", "body": "", "payload": {}}])
    assert "Fortuneo" in shown.body                                    # local detail is allowed


def test_macos_message_is_local_detail_and_external_ones_are_not(world, cfg, events):
    local = M.compose_local(events)
    assert local.detail == "local" and local.body != M.compose_external(events, "minimal", None).body


def test_digest_teaser_holds_no_sentinel_in_either_mode(world, cfg):
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    s = settings()
    d = D.build(world, cfg, ds, s, today=TODAY)
    guard = M.external_guard(cfg, world)
    mini = D.teaser(d, "minimal", guard)
    summ = D.teaser(d, "summary", guard)
    assert mini.body == "Coach: your weekly summary is ready. Open the app."
    assert re.fullmatch(r"Coach weekly summary: about \d+ EUR of variable spending last week( \(usual week about \d+ EUR\))?, \d+ open alerts?\. Open the app\.", summ.body)
    for m in (mini, summ):
        assert leaks(m.title + m.body) == []
    assert "Top categories" not in summ.body and "##" not in summ.body and d["markdown"][:20] not in summ.body


def test_the_digest_markdown_stays_local_and_does_not_reach_a_channel(world, cfg):
    net = Net()
    ds = analytics_api.build_dataset(world, cfg, TODAY)
    s = settings(weekly_digest_to_channels=True, external_detail="summary", **ALL_ON)
    out = D.run_weekly(world, cfg, ds, s, now=NOW, transports=net.transports(), tz=dt.timezone.utc)
    assert out["status"] == "done" and net.total() == 4
    local = D.build(world, cfg, ds, s, today=TODAY)["markdown"]
    for p in net.posts:
        assert local[:40] not in p["body"] and "## " not in p["body"]
    for m in net.mails:
        assert "## " not in m["body"] and leaks(m["subject"] + m["body"]) == []


def test_an_external_title_is_a_fixed_word_never_the_event_text(world, cfg, events):
    for detail in ("minimal", "summary"):
        m = M.compose_external(events, detail, None)
        assert m.title == "Coach alerts"
