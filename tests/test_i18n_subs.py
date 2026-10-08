"""i18n step 4e: the subscriptions and cancellation texts the web app translates (codes + params next to the English), and the
guarantee that the MCP finance tools (read by the coach) keep their English output without any ``*_msg`` sibling.

Synthetic data only (the E8 world of subshelpers)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest


from apihelpers import ctx  # noqa: F401
from coach.i18n_msg import strip_msgs
from coach.mcp.tools import ToolSession
from coach.skills import cancel as C
from coach.subs import decisions as DEC
from mcphelpers import TODAY, payload
from subshelpers import build_subs_world

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "locales"


def _bundle(lang: str) -> dict:
    return json.loads((LOCALES / lang / "server.json").read_text(encoding="utf-8"))


def _get(bundle: dict, code: str):
    node = bundle
    for p in code.split("."):
        node = node.get(p) if isinstance(node, dict) else None
    return node


def _msg_keys(o, path="") -> list[str]:
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k.endswith("_msg"):
                out.append(f"{path}.{k}")
            out += _msg_keys(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += _msg_keys(v, f"{path}[{i}]")
    return out


# ---------------------------------------------------------------- the rules table

@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_every_rule_has_its_name_and_summary_in_every_language(lang):
    b = _bundle(lang)
    for rid in C.RULES:
        for part in ("name", "summary"):
            v = _get(b, f"{C.rule_code(rid)}.{part}")
            assert isinstance(v, str) and v.strip(), (lang, rid, part)


def test_the_english_rule_texts_are_the_server_texts():
    en = _bundle("en")
    for rid, r in C.RULES.items():
        assert _get(en, f"{C.rule_code(rid)}.name") == r["name"] and _get(en, f"{C.rule_code(rid)}.summary") == r["summary"]


def test_rule_codes_are_valid_message_codes():
    from coach.i18n_msg import CODE_RE
    assert C.rule_code("fr-3-clics") == "cancel.rule.fr3Clics" and C.rule_code("it-bersani-telecom") == "cancel.rule.itBersaniTelecom"
    assert all(CODE_RE.match(C.rule_code(r) + ".name") for r in C.RULES)


# ---------------------------------------------------------------- the cancellability result

CASES = [
    ("insurance_home", dict(start_date=dt.date(2026, 3, 1), renewal=dt.date(2027, 3, 1)), "FR"),
    ("insurance_health", dict(start_date=None), "FR"),
    ("insurance_other", dict(renewal=dt.date(2025, 11, 30)), "FR"),
    ("telecom", dict(start_date=dt.date(2025, 6, 1), commitment_end=dt.date(2027, 2, 1)), "FR"),
    ("streaming", dict(renewal=dt.date(2026, 2, 1), notice_period_days=30), "FR"),
    ("insurance_car", dict(renewal=dt.date(2027, 1, 15)), "IT"),
    ("insurance_home", dict(renewal=dt.date(2027, 1, 15)), "IT"),
    ("other", dict(), "IT"),
]


@pytest.mark.parametrize("kind,dates,country", CASES)
def test_messages_mirror_the_english_and_are_off_by_default(kind, dates, country):
    t = C.Terms(kind=kind, **dates)
    plain = C.cancellability(t, TODAY, country)
    full = C.cancellability(t, TODAY, country, messages=True)
    assert _msg_keys(plain) == []                                                      # the MCP tool, the CLI, the letters: English only
    assert strip_msgs(full) == plain                                                   # the messages add, they change nothing
    assert full["method_msg"]["text"] == full["method"]
    assert [m["text"] for m in full["conditions_msg"]] == full["conditions"]
    assert [m["text"] for m in full["unknown_msg"]] == full["unknown"]
    for r in full["rules"]:
        assert r["name_msg"]["text"] == r["name"] and r["summary_msg"]["code"] == C.rule_code(r["id"]) + ".summary"
    info = C.cancellation_info(full)
    assert info["disclaimer_key"] == "contract" and info["verify_key"] == "contract_verify"
    assert all(lb["name_msg"]["text"] == lb["name"] for lb in info["legal_basis"])
    assert "method_msg" not in C.cancellation_info(plain) and C.cancellation_info(plain)["disclaimer_key"] == "contract"


def test_a_dated_condition_carries_its_date_as_a_param():
    t = C.Terms(kind="telecom", start_date=dt.date(2025, 6, 1), commitment_end=dt.date(2027, 2, 1))
    m = C.cancellability(t, TODAY, "FR", messages=True)["conditions_msg"][0]
    assert m["code"] == "cancel.condition.telecomCommitment" and m["params"] == {"free_date": "2027-02-01"}


def test_the_web_disclaimers_include_the_cancellation_panel():
    from coach.api.routes.core import WEB_DISCLAIMERS
    assert {"contract", "contract_verify"} <= set(WEB_DISCLAIMERS)


# ---------------------------------------------------------------- decisions

def test_a_kept_decision_reason_is_a_message():
    d = DEC.Decision(id="dec_x", contract_id=None, series_id=None, name=None, decision="kept", decided_on=TODAY, effective_on=None,
                     before_c=1000, after_c=1000, note=None, source="ui", state="confirmed", created_at=None, confirmed_at=None)
    v = DEC.verify(d, None, TODAY)
    assert v["reason_msg"] == {"code": "subs.decision.kept", "params": {}, "text": v["reason"]}


def test_the_decisions_of_the_savings_view_carry_their_reason_as_a_message(ctx):  # noqa: F811
    ref = {r["name"]: r for r in ctx.get("/subs/inventory", include_ended="true").json()["rows"]}["Oldapp"]["ref"]
    assert ctx.post("/subs/decisions", {"ref": ref, "decision": "cancelled", "decided_on": "2026-01-10", "before_monthly": 3.99}).status_code == 200
    s = ctx.get("/subs/savings").json()
    d = s["decisions"][0]
    assert d["reason_msg"]["code"] == "subs.decision.noPaymentSeriesEnded" and d["reason_msg"]["text"] == d["reason"]
    assert set(d["reason_msg"]["params"]) == {"last_date"} and s["note_msg"]["text"] == s["note"]
    inv = ctx.get("/subs/inventory", include_ended="true").json()
    assert {r["name"]: r for r in inv["rows"]}["Oldapp"]["decision"]["reason_msg"]["code"].startswith("subs.decision.")


# ---------------------------------------------------------------- the web API carries the messages

@pytest.fixture
def world(cfg):
    con = build_subs_world(cfg)
    con.close()
    return cfg


def test_the_inventory_endpoint_carries_the_messages(ctx):  # noqa: F811
    d = ctx.get("/subs/inventory").json()
    assert [m["text"] for m in d["notes_msg"]] == d["notes"]
    r = {x["name"]: x for x in d["rows"]}
    c = r["TelcoCo"]["cancellation"]
    assert c["method_msg"]["code"].startswith("cancel.method.") and c["method_msg"]["text"] == c["method"]
    assert c["legal_basis"][0]["name_msg"]["code"] == "cancel.rule.frTelecom.name" and c["verify_key"] == "contract_verify"
    sb = r["StreamBox"]
    assert [s["measurable_msg"]["text"] for s in sb["usage"]["signals"]] == [s["measurable"] for s in sb["usage"]["signals"]]
    assert r["Fitclub"]["usage"]["note_not_measurable_msg"]["code"] == "subs.usage.notMeasurable"
    assert r["Fitclub"]["alternatives"]["note_msg"]["code"] == "subs.alternatives.note"
    fr = ctx.get("/meta/disclaimers", lang="fr").json()["texts"]
    from coach import disclaimers
    assert fr["contract"] == disclaimers.get("contract", "fr") and fr["contract_verify"] == disclaimers.get("contract_verify", "fr")


def test_the_draft_preview_carries_its_warnings_as_messages(ctx):  # noqa: F811
    inv = ctx.get("/subs/inventory").json()
    sid = next(x["series_id"] for x in inv["rows"] if x["draftable"])
    d = ctx.post("/subs/contracts/draft", {"series": sid}, dry_run="true").json()["contract"]
    assert [m["text"] for m in d["warnings_msg"]] == d["warnings"]
    assert all(m["code"].startswith("subs.draft.") for m in d["warnings_msg"])


# ---------------------------------------------------------------- the MCP tools stay English

@pytest.fixture
def session(cfg):
    con = build_subs_world(cfg)
    s = ToolSession(cfg, con=con, insecure=True, today=TODAY, session_id="s_i18n")
    yield s
    s.close()
    con.close()


def test_no_message_sibling_reaches_an_mcp_tool_output(session):
    outs = {}
    for t in session.listing():
        if t["writes"]:
            continue
        args = {"tx_ref": next(iter(session.data()[3]))} if t["name"] == "explain_transaction" else {}
        outs[t["name"]] = session.call(t["name"], args)
    outs["inventory_all"] = session.call("subscriptions_inventory", {"include_ended": True, "limit": 60})
    outs["cancellability_all"] = session.call("cancellability", {"include_rules": True})
    for kind in ("telecom", "insurance_home", "streaming"):
        outs[f"cancellability_{kind}"] = session.call("cancellability", {"kind": kind, "start_date": "2025-06-01", "renewal": "2025-12-01",
                                                                         "commitment_end": "2027-02-01", "country": "FR"})
    outs["calendar_long"] = session.call("calendar", {"days": 120})
    ref = next(r["ref"] for r in payload(outs["inventory_all"])["rows"] if r["monthly"])
    outs["alternatives_record"] = session.call("alternatives_record", {"ref": ref, "provider": "CheapCo", "offer_name": "Basic", "monthly_price": 1.5,
                                                                       "source_url": "https://example.org/offer", "retrieved_at": "2026-08-01"})
    outs["savings_tracker"] = session.call("savings_tracker", {})
    for name, res in outs.items():
        assert "_msg" not in res.text, name
    assert payload(outs["alternatives_record"])["savings"]["stale_warning"]           # the English sentence is still there
