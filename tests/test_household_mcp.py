"""E14 through the finance MCP tools: members are pseudonyms only, a person filter on the analytics tools, the children's money and who-pays tools
behind the privacy choke point. Names, aliases, rule ids and labels of the household must never reach a model."""
from __future__ import annotations

import json

import pytest

from coach.mcp.tools import READ_ONLY, ToolSession
from hhhelpers import TODAY, hh_world, match_all

RULES = """\
attribution:
  - id: mia-secret-card
    member: mia
    match: { card_last4: "4242", account: fo }
    note: Mia Rossi card, ask Anna
  - id: noa-prepaid
    member: noa
    match: { account: nk }
kid_budgets:
  - id: mia-secret-weekly
    member: mia
    period: weekly
    limit: 20
allocations:
  - id: anna-luca-groceries
    title: Groceries of the Rossi family
    match: { category: food.groceries }
    method: equal
"""

NAMES = ["anna", "luca", "mia", "noa", "rossi", "mme anna", "mia-secret", "anna-luca", "prepaid", "fortuneo", "revolut", "anna bank",
         "luca bank", "m ou mme"]


@pytest.fixture
def session(cfg):
    con = hh_world(cfg, extra_yaml=RULES)
    match_all(con, cfg)
    s = ToolSession(cfg, con=con, insecure=True, today=TODAY, session_id="s_hh")
    yield s
    con.close()


def call(s, name, args=None) -> dict:
    res = s.call(name, args or {})
    assert res.ok, res.text
    return json.loads(res.text)


def no_names(obj) -> list[str]:
    low = json.dumps(obj, ensure_ascii=False).lower()
    return [n for n in NAMES if n in low]


def test_the_household_tools_are_read_only_and_listed():
    assert {"household_overview", "kids_money", "who_pays"} <= set(READ_ONLY)


def test_household_overview_has_pseudonyms_and_counts_only(session):
    d = call(session, "household_overview")
    assert [m["member"] for m in d["members"]] == ["adult-1", "adult-2", "kid-1", "kid-2"]
    assert [m["role"] for m in d["members"]] == ["adult", "adult", "child", "child"]
    assert d["attribution_rules"] == 2 and d["kid_budgets"] == 1 and d["allocation_rules"] == 1
    kid1 = d["members"][2]
    assert kid1["accounts"] == ["account-kids-2"] and kid1["attributed_transactions"] > 0
    assert "joint" in d["transactions_by_person"] and "kid-1" in d["transactions_by_person"]
    assert no_names(d) == []
    assert not any("birth" in k for k in json.dumps(d))                      # no birth year, no age


def test_kids_money_is_pseudonymised_and_computed(session):
    d = call(session, "kids_money", {"member": "kid-1"})
    assert [c["member"] for c in d["children"]] == ["kid-1"]
    c = d["children"][0]
    series = c["pocket_money"]["series"][0]
    assert series["source"] == "adult-1" and series["amount"] == "10.00" and series["cadence"] == "monthly"
    assert all(e.startswith("h_") for e in series["evidence"])                  # transaction refs are hashes
    assert c["extra_topups"]["by_source"] == {"adult-2": "25.00", "unknown": "30.00"}
    assert c["ratio"]["pocket_share"] == round(6000 / 11500, 4)
    assert c["balance"]["current"] == "57.00" and c["balance"]["estimated"] is True
    assert d["kid_budgets"][0]["ref"] == "kid-budget-1" and d["kid_budgets"][0]["member"] == "kid-1" and d["kid_budgets"][0]["spent"] == "5.00"
    assert no_names(d) == [], no_names(d)
    allk = call(session, "kids_money")
    assert [c["member"] for c in allk["children"]] == ["kid-1", "kid-2"] and len(allk["kid_budgets"]) == 1
    assert no_names(allk) == []


def test_kids_money_refuses_an_unknown_or_real_member_and_an_adult_without_data(session):
    for bad in ("mia", "ghost", "kid-9"):
        res = session.call("kids_money", {"member": bad})
        assert not res.ok and "unknown member" in res.text and "Mia" not in res.text
    assert session.call("kids_money", {"member": "adult-1"}).ok            # an adult has a (small) report too, never an error that leaks


def test_who_pays_uses_pseudonyms_and_no_rule_labels(session):
    d = call(session, "who_pays")
    r = d["rules"][0]
    assert r["ref"] == "allocation-1" and r["among"] == ["adult-1", "adult-2"] and [m["member"] for m in r["members"]] == ["adult-1", "adult-2"]
    assert r["members"][0]["share_pct"] == 50.0 and "id" not in r
    assert no_names(d) == [], no_names(d)
    assert "Groceries of the [person] family" in json.dumps(d)                # the user's free-text title is scrubbed of the family name


def test_a_member_pseudonym_filters_every_analytics_tool(session):
    whole = call(session, "cashflow", {"months": 2})
    kid = call(session, "cashflow", {"months": 2, "member": "kid-1"})
    assert whole != kid
    sep = next(m for m in kid["household"]["months"] if m["month"] == "2026-09")
    assert sep["spending"] == "43.60"
    s = call(session, "transactions_search", {"member": "kid-1", "limit": 50, "date_from": "2026-09-01", "date_to": "2026-09-30", "direction": "out"})
    assert s["count"] == 5 and s["total_out"] == "-43.60"                    # her own account (4 payments) and her card on the shared account (1)
    assert call(session, "transactions_search", {"member": "joint", "category": "food.groceries", "date_from": "2026-09-28", "date_to": "2026-09-28"})["count"] == 1   # the other card stays joint
    assert call(session, "transactions_search", {"member": "joint", "category": "leisure.cinema_events"})["count"] == 0
    assert call(session, "transactions_search", {"member": "kid-1", "category": "leisure.cinema_events"})["count"] == 1
    for tool in ("category_averages", "recurring", "price_changes", "anomalies", "forecast", "year_review"):
        assert session.call(tool, {"member": "kid-2"}).ok, tool
    for tool in ("budget_status", "calendar", "goals"):                       # the tools without an account scope take a person too
        assert session.call(tool, {"member": "kid-2"}).ok, tool
        assert not session.call(tool, {"member": "mia"}).ok, tool
    bad = session.call("cashflow", {"member": "mia"})
    assert not bad.ok and "unknown member" in bad.text
    assert no_names(kid) == [] and no_names(s) == []


def test_explain_transaction_says_whose_it_is_and_why_without_a_rule_id(session):
    refs = call(session, "transactions_search", {"member": "kid-1", "category": "leisure.cinema_events"})["transactions"]
    d = call(session, "explain_transaction", {"tx_ref": refs[0]["ref"]})
    assert d["attribution"] == {"person": "kid-1", "source": "rule", "reason": "an attribution rule of the household memory"}
    assert no_names(d) == []
    joint = call(session, "transactions_search", {"member": "joint", "category": "food.groceries", "date_from": "2026-09-28", "date_to": "2026-09-28"})["transactions"][0]["ref"]
    assert call(session, "explain_transaction", {"tx_ref": joint})["attribution"]["source"] == "account"


def test_memory_context_and_the_new_tools_hold_no_birth_year_or_alias(session):
    d = call(session, "memory_context")
    assert no_names(d) == []


def test_kids_money_lists_the_newest_extra_top_ups_only_and_says_so(session):
    from helpers import add_tx
    for i in range(20):
        add_tx(session.con, "rl", f"cap{i}", f"2026-08-{i + 1:02d}", 3.0 + i, "Money from ANNA", "internal_transfer")
    c = call(session, "kids_money", {"member": "kid-1"})["children"][0]
    assert len(c["extra_topups"]["items"]) == 12 and c["extra_topups"]["items_shown"] == 12 and c["extra_topups"]["count"] >= 20
    assert c["extra_topups"]["items"][0]["date"] >= c["extra_topups"]["items"][-1]["date"]


def test_birth_years_never_reach_a_model(cfg):
    """Sentinel: invented birth years in household.yaml must appear in NO model-facing output (every read-only tool, memory_context in any
    detail mode, the markdown context of the CLI); a model only gets an age band. The local context (--names) keeps the year."""
    import re
    from coach.memory import context as C
    from coach.memory.store import MemoryStore
    from hhhelpers import HOUSEHOLD
    con = hh_world(cfg, household=HOUSEHOLD.replace("1984", "1973").replace("2012", "2009").replace("2015", "2016"))
    s = ToolSession(cfg, con=con, insecure=True, today=TODAY, session_id="s_birth")
    rx = re.compile(r"(?<![\d.])(1973|2009|2016)(?![\d.])|born\s+\d|birth_year", re.I)
    from coach.skills.tools import SAMPLE_ARGS
    for t in s.listing():
        if t["writes"]:
            continue
        args = {"tx_ref": json.loads(s.call("transactions_search", {"limit": 1}).text)["transactions"][0]["ref"]} if t["name"] == "explain_transaction" else SAMPLE_ARGS.get(t["name"], {})
        text = s.call(t["name"], args).text
        assert not rx.search(text), t["name"]
    assert "teen" in s.call("memory_context", {}).text
    store = MemoryStore(cfg.memory_dir, history=False)
    for coarse in (True, False):
        ctx = C.build_context(store, con, cfg, names=False, coarse=coarse, today=TODAY)
        assert not rx.search(json.dumps(ctx)) and not rx.search(C.render_markdown(ctx))
        assert [m["age_band"] for m in ctx["members"]] == ["adult", "adult", "teen", "6-11"]
    local = C.build_context(store, con, cfg, names=True, today=TODAY)
    assert {m["birth_year"] for m in local["members"]} == {1973, None, 2009, 2016}
    assert C.age_band("child", 2023, TODAY) == "under 6" and C.age_band("child", 2016, TODAY) == "6-11" and C.age_band("child", 2009, TODAY) == "teen"
    assert C.age_band("child", None, TODAY) is None and C.age_band("adult", 1973, TODAY) == "adult"
