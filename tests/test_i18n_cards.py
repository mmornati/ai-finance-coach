"""i18n step 4c: the insight cards and the alert events carry, next to their English ``title`` / ``body``, the messages the web translates
(``title_msg`` / ``body_msg``: {code, params, text}). Anomalies keep theirs in the stored payload (``message_msg``), alert events in the
event's payload; an older row without them shows its English. The MCP tools and the CLI keep the English only.

Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from alerthelpers import build_alert_world
from anhelpers import D, make_ds, tx
from coach.analytics import anomalies as an
from coach.i18n_msg import strip_msgs
from test_api_alerts import checked, ctx_for

ROOT = Path(__file__).resolve().parents[1]
AREAS = ("insight", "anomaly", "alert", "reminder", "loanAlert", "rentalAlert")


def _bundle(lang: str) -> dict:
    return json.loads((ROOT / "web" / "src" / "locales" / lang / "server.json").read_text(encoding="utf-8"))


def _has(bundle: dict, key: str) -> bool:
    *path, last = key.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return False
    return isinstance(node, dict) and (isinstance(node.get(last), str) or isinstance(node.get(f"{last}_other"), str))


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


@pytest.fixture
def world(cfg):
    build_alert_world(cfg).close()
    return cfg


@pytest.fixture
def ctx_world(world, tmp_path):
    return ctx_for(world, tmp_path)


# ---------------------------------------------------------------- the web knows every code

def test_every_code_of_the_cards_and_alerts_is_known_to_the_web():
    """The literal codes (also the conditional ones, `"a" if x else "b"`) and the codes built from a fixed vocabulary."""
    from coach.api.views import ANOMALY_TITLE_CODE
    from coach.analytics.recurring import CADENCES
    from coach.subs.decisions import KINDS
    en = _bundle("en")
    used = set()
    for p in (ROOT / "src" / "coach").rglob("*.py"):
        used |= set(re.findall(r"\"((?:%s)\.[a-zA-Z.]+[a-zA-Z])\"" % "|".join(AREAS), p.read_text(encoding="utf-8")))
    assert {"insight.budget.atRiskGroup", "loanAlert.missed.bodyUsualDayLater", "alert.consent.revoked"} <= used
    used |= {f"insight.anomaly.{c}" for c in ANOMALY_TITLE_CODE.values()}
    used |= {f"reminder.decision.stillCharged.{k}" for k in KINDS}
    used |= {f"alert.consent.{s}" for s in ("expiring", "urgent")}
    used |= {f"alert.kidBudget.{s}{p}{t}" for s in ("over", "close") for p in ("Weekly", "Monthly") for t in ("Category", "Group", "All")}
    used |= {f"alert.kidBudget.{s}Body{p}" for s in ("over", "close") for p in ("Weekly", "Monthly")}
    used |= {f"labels.cadence.{c}" for c in CADENCES}
    missing = sorted(c for c in used if not _has(en, c))
    assert not missing, missing


@pytest.mark.parametrize("lang", ["fr", "it"])
def test_french_and_italian_have_every_key_with_its_plural_forms_and_the_same_placeholders(lang):
    en = {k: v for k, v in _flat(_bundle("en")).items() if k.split(".")[0] in AREAS or k.startswith("labels.cadence.")}
    other = _flat(_bundle(lang))
    for k, v in en.items():
        base = k.rsplit("_", 1)[0] if k.endswith(("_one", "_other")) else k
        forms = [f"{base}_one", f"{base}_many", f"{base}_other"] if base != k else [k]
        for f in forms:
            assert f in other, (lang, f)
            assert set(re.findall(r"\{\{\s*(\w+)", other[f])) <= set(re.findall(r"\{\{\s*(\w+)", v)) | {"count"}, (lang, f)
        if base == k:
            assert set(re.findall(r"\{\{\s*(\w+)", other[k])) == set(re.findall(r"\{\{\s*(\w+)", v)), (lang, k)


# ---------------------------------------------------------------- anomalies (stored)

def test_an_anomaly_carries_its_message_with_raw_params_and_the_english_text():
    steady = [100, 105, 95, 100, 98, 102, 100, 101, 99, 100, 103, 600]
    from coach.analytics.common import add_months
    txs = [tx(add_months(D("2025-10-10"), i), -a, "food.groceries") for i, a in enumerate(steady)]
    txs += [tx("2026-09-20", -45.0, "leisure.cinema_events", entity="CINEMAX", key="d1"),
            tx("2026-09-21", -45.0, "leisure.cinema_events", entity="CINEMAX", key="d2")]
    r = an.detect_anomalies(make_ds(txs, last_sync={"a": "2026-10-04"}))
    spike = next(a for a in r.anomalies if a.type == "category_spike")
    m = spike.message_msg
    assert m["code"] == "anomaly.categorySpike" and m["text"] == spike.message
    assert m["params"]["spike_category"] == "food.groceries" and m["params"]["spike_month"] == "2026-09"
    assert m["params"]["spent_amount"] == "600.00" and m["params"]["typical_amount"] == "100.00" and m["params"]["count"] == 10 and "10 earlier months" in spike.message
    dup = next(a for a in r.anomalies if a.type == "duplicate_charge")
    assert dup.message_msg["code"] == "anomaly.duplicateCharge" and dup.message_msg["params"]["count"] == 1
    assert dup.message_msg["params"]["payments"] == 2 and dup.message_msg["params"]["payment_amount"] == "45.00"
    assert "message_msg" in spike.to_dict() and "message_msg" not in strip_msgs(spike.to_dict())


def test_the_stored_payload_keeps_the_message_and_an_older_row_without_it_still_reads(cfg):
    from coach.db import connect
    from coach.analytics.common import add_months
    con = connect(cfg, insecure=True, create=True)
    steady = [100, 105, 95, 100, 98, 102, 100, 101, 99, 100, 103, 600]
    ds = make_ds([tx(add_months(D("2025-10-10"), i), -a, "food.groceries") for i, a in enumerate(steady)], last_sync={"a": "2026-10-04"})
    an.refresh_anomalies(con, ds)
    rows = an.stored_anomalies(con)
    assert rows and rows[0]["message_msg"]["code"] == "anomaly.categorySpike"
    con.execute("UPDATE anomalies SET payload=?", (json.dumps({k: v for k, v in rows[0].items() if k != "message_msg"}),))
    con.commit()
    old = an.stored_anomalies(con)[0]
    assert "message_msg" not in old and old["message"] == rows[0]["message"]           # the web falls back to the English
    con.close()


# ---------------------------------------------------------------- the feed and the alert events (API)

def _check_msgs(items):
    en = _bundle("en")
    n = 0
    for c in items:
        for f in ("title", "body"):
            m = c.get(f"{f}_msg")
            if m is None:
                continue
            n += 1
            assert _has(en, m["code"]), m["code"]
            assert m["text"] == c[f] or (c.get("disclaimer") and c[f].startswith(m["text"])), (m, c[f])
    return n


def test_the_insights_feed_cards_carry_title_and_body_messages(ctx_world):
    cards = ctx_world.get("/insights").json()["cards"]
    assert cards and all("title_msg" in c and "body_msg" in c for c in cards)
    assert _check_msgs(cards) >= len(cards)


def test_alert_events_keep_their_messages_in_the_payload_and_the_api_lifts_them(ctx_world):
    c = ctx_world
    checked(c)
    items = c.get("/alerts", include_resolved=True).json()["items"]
    by_kind = {}
    for e in items:
        by_kind.setdefault(e["kind"], e)
        assert not [k for k in e["payload"] if k.endswith("_msg")]                     # lifted out of the payload
    assert by_kind["consent"]["title_msg"]["code"].startswith("alert.consent.")
    assert by_kind["consent"]["title_msg"]["params"]["bank"] and by_kind["consent"]["body_msg"]["code"] == "alert.consent.reconnect"
    assert by_kind["sync_failing"]["title_msg"]["code"] == "alert.syncFailing.title" and by_kind["sync_failing"]["title_msg"]["params"]["count"] == 3
    assert _check_msgs(items) >= len(items)
    with c.state.write() as con:                                                        # stored in the payload column: no schema change
        raw = json.loads(con.execute("SELECT payload FROM alert_events WHERE kind='consent' LIMIT 1").fetchone()[0])
        assert raw["title_msg"]["code"].startswith("alert.consent.")
        con.execute("UPDATE alert_events SET payload='{}' WHERE kind='sync_failing'")      # an event stored before i18n step 4c
        con.commit()
    old = next(e for e in c.get("/alerts").json()["items"] if e["kind"] == "sync_failing")
    assert old["title_msg"] is None and old["body_msg"] is None and old["title"]


def test_external_channel_messages_are_unchanged_by_the_stored_messages():
    """The minimal external text is built from the kind and the amount only: a message in the payload adds nothing to it."""
    from coach.alerts import messages
    e = {"kind": "unusual_charge", "severity": "high", "title": "Possible duplicate charge", "body": "x", "payload": {"amount_c": 1500}}
    with_msgs = {**e, "payload": {"amount_c": 1500, "title_msg": {"code": "insight.anomaly.duplicateCharge", "params": {}, "text": "x"},
                                  "body_msg": {"code": "anomaly.duplicateCharge", "params": {"merchant": "SECRETSHOP"}, "text": "x"}}}
    for fn in (messages.minimal_text, messages.summary_text):
        assert fn([with_msgs]) == fn([e]) and "SECRETSHOP" not in fn([with_msgs])


# ---------------------------------------------------------------- producers

def test_the_rental_scheme_card_sends_its_disclaimer_as_a_key():
    from coach import disclaimers
    src = (ROOT / "src/coach/rental/service.py").read_text(encoding="utf-8")
    assert "not tax advice" not in src and '"disclaimer": "tax_short"' in src
    assert disclaimers.get("tax_short") == "General information, not tax advice."


def test_consent_and_kid_budget_messages():
    from coach.ingest.consent import Consent, describe, describe_msg
    c = Consent("s1", "Some Bank", "FR", "2026-10-06T10:00:00+00:00", 2, "urgent", "active", None, None, 1)
    m = describe_msg(c, describe(c))
    assert m["code"] == "alert.consent.urgent" and m["params"] == {"count": 2, "until_date": "2026-10-06", "bank": "Some Bank", "country": "FR"}
    dead = Consent("s1", "Some Bank", "FR", None, None, "revoked", "active", None, None, 1)
    assert describe_msg(dead, "x")["code"] == "alert.consent.revoked"
    from coach.household.kidbudgets import _limit_msgs
    s = {"period": "weekly", "category": "food.snacks", "group": None, "spent_c": 1250, "limit_c": 1000, "days_left": 2}
    t, b = _limit_msgs(s, "kid", True, "T", "B")
    assert t["code"] == "alert.kidBudget.overWeeklyCategory" and t["params"]["limit_category"] == "food.snacks"
    assert b["code"] == "alert.kidBudget.overBodyWeekly" and b["params"] == {"spent_amount": "12.50", "limit_amount": "10.00"}
    t, b = _limit_msgs({**s, "category": None, "group": "food", "period": "monthly"}, "kid", False, "T", "B")
    assert t["code"] == "alert.kidBudget.closeMonthlyGroup" and b["code"] == "alert.kidBudget.closeBodyMonthly" and b["params"]["count"] == 2
