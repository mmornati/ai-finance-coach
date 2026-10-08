"""i18n step 4b: the coverage notes of the analytics results carry, next to the English (`notes`), a message the web translates
(`notes_msg`: {code, params, text}, same order). The MCP finance tools (read by a model) and the CLI's --json keep the English only.

Synthetic data only."""
from __future__ import annotations

import json
from pathlib import Path

from anhelpers import account, make_ds, monthly, tx
from coach.analytics import averages, cashflow, recurring
from coach.analytics.common import non_eur_note, note, split_notes
from coach.api import views
from coach.i18n_msg import strip_msgs
from mcphelpers import TODAY, build_world, payload, session, world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _bundle(lang: str) -> dict:
    return json.loads((ROOT / "web" / "src" / "locales" / lang / "server.json").read_text(encoding="utf-8"))


def _keys(obj, out=None) -> set:
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def _aligned(cov: dict):
    assert len(cov["notes"]) == len(cov["notes_msg"])
    for text, msg in zip(cov["notes"], cov["notes_msg"]):
        assert msg is None or msg["text"] == text


# ---------------------------------------------------------------- the helper

def test_a_note_is_english_text_plus_a_message_and_a_plain_string_stays_english_only():
    texts, msgs = split_notes([non_eur_note(2), "a plain English note", note("coverage.lumpy", "seasonal")])
    assert texts == ["2 non-EUR transaction(s) left out", "a plain English note", "seasonal"]
    assert msgs[0] == {"code": "coverage.nonEur", "params": {"count": 2}, "text": "2 non-EUR transaction(s) left out"}
    assert msgs[1] is None and msgs[2]["code"] == "coverage.lumpy"
    assert split_notes(None) == ([], [])


def test_strip_msgs_removes_every_msg_sibling_at_any_depth():
    d = {"note": "x", "note_msg": {"code": "a.b"}, "coverage": {"notes": ["y"], "notes_msg": [None]}, "rows": [{"k": 1, "k_msg": {}}]}
    assert strip_msgs(d) == {"note": "x", "coverage": {"notes": ["y"]}, "rows": [{"k": 1}]}


# ---------------------------------------------------------------- the analytics results

def test_cashflow_incomplete_months_and_non_eur_notes_carry_codes_and_raw_params():
    a = monthly("2026-05-10", 5, -100, "food.groceries") + [tx("2026-06-25", 2000, "income.salary")]
    b = [tx("2026-08-16", -20, "food.groceries", "b"), tx("2026-09-10", -30, "food.groceries", "b")]
    ds = make_ds(a + b, accounts=[account(), account("b", "Card B", purpose="cards")],
                 last_sync={"a": "2026-10-04", "b": "2026-10-04"})
    ds.foreign = ["k9", "k10"]
    cov = cashflow.cashflow(ds, months=5).to_dict()["coverage"]
    _aligned(cov)
    by = {m["code"]: m for m in cov["notes_msg"]}
    assert by["coverage.nonEur"]["params"] == {"count": 2}
    inc = by["coverage.incompleteMonths"]
    assert inc["text"].startswith("incomplete months: ") and "Card B" in inc["text"]          # the English is unchanged
    assert inc["params"]["count"] >= 2 and inc["params"]["first_month"] < inc["params"]["last_month"]
    assert "Card B" in inc["params"]["accounts"].split(", ")                                   # labels: passed as they are


def test_every_module_note_is_aligned_and_known_to_the_web():
    ds = make_ds(monthly("2026-01-05", 8, -100.5) + monthly("2026-01-07", 8, -9.99, "leisure.streaming", entity="STREAM"))
    ds.foreign = ["x"]
    results = [cashflow.cashflow(ds, months=3), averages.category_averages(ds), recurring.detect_recurring(ds)]
    en, fr, it = _bundle("en"), _bundle("fr"), _bundle("it")
    seen = set()
    for r in results:
        cov = r.to_dict()["coverage"]
        _aligned(cov)
        for m in cov["notes_msg"]:
            assert m is not None, cov["notes"]
            area, name = m["code"].split(".")
            for b in (en, fr, it):
                assert name in b[area] or f"{name}_other" in b[area], (m["code"], b is en)
            seen.add(m["code"])
    assert "coverage.nonEur" in seen


def test_the_category_detail_sends_its_notes_with_messages():
    ds = make_ds(monthly("2026-07-05", 3, -50.0))
    ds.foreign = ["x"]
    d = views.category_detail(ds, None, "food.groceries")
    assert len(d["notes"]) == len(d["notes_msg"]) and d["notes"]
    codes = [m["code"] for m in d["notes_msg"]]
    assert "coverage.nonEurEveryFigure" in codes
    assert d["notes"][codes.index("coverage.nonEurEveryFigure")] == "1 non-EUR transaction(s) are left out of every figure"
    _aligned(d["coverage"])


def test_plural_notes_have_every_plural_form():
    for lang, forms in (("en", ("_one", "_other")), ("fr", ("_one", "_many", "_other")), ("it", ("_one", "_many", "_other"))):
        cov = _bundle(lang)["coverage"]
        bases = {k.rsplit("_", 1)[0] for k in cov if k.endswith(("_one", "_many", "_other"))}
        for b in bases:
            assert all(f"{b}{f}" in cov for f in forms), (lang, b)
        assert set(cov) == set(_bundle(lang)["coverage"])
    assert {k.rsplit("_", 1)[0] for k in _bundle("fr")["coverage"]} == {k.rsplit("_", 1)[0] for k in _bundle("en")["coverage"]}
    assert {k.rsplit("_", 1)[0] for k in _bundle("it")["coverage"]} == {k.rsplit("_", 1)[0] for k in _bundle("en")["coverage"]}


# ---------------------------------------------------------------- what a model and the CLI read

def test_no_mcp_tool_output_carries_a_msg_sibling(session):
    from test_mcp_tools import all_default_calls
    outs = all_default_calls(session)
    for name, r in outs.items():
        keys = _keys(json.loads(r.text))
        assert not [k for k in keys if k.endswith("_msg")], name
    cov = payload(outs["cashflow"])["coverage"]
    assert "notes" in cov and "notes_msg" not in cov
    # 4c: an anomaly carries `message_msg` (stored, for the web); the tool keeps the redacted English message only
    anoms = payload(outs["anomalies"])["anomalies"]
    assert anoms and all("message" in a and "message_msg" not in a for a in anoms)
    for name in ("loans_overview", "rental_overview", "subscription_audit"):                # loan alerts, rental and subscription cards
        assert not [k for k in _keys(payload(outs[name])) if k.endswith("_msg")], name


def test_the_redacted_registry_drops_the_msg_siblings_before_redacting(cfg, world):
    from coach.analytics.privacy import redacted_registry
    reg = redacted_registry(world, cfg, TODAY)
    for name in ("cashflow", "category_averages", "recurring", "forecast", "anomalies", "budget_status", "goals", "calendar"):
        assert not [k for k in _keys(reg.call(name)) if k.endswith("_msg")], name


def test_the_cli_json_keeps_the_english_notes_only():
    from coach.analytics.commands import _j
    ds = make_ds(monthly("2026-01-05", 8, -100.5))
    ds.foreign = ["x"]
    d = json.loads(_j(cashflow.cashflow(ds, months=3)))
    assert d["coverage"]["notes"][0] == "1 non-EUR transaction(s) left out" and "notes_msg" not in d["coverage"]
