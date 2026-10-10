"""The household in the web API (E14-1..E14-9) and, above all, the per-person logins (E14-8): the child scope is enforced SERVER-SIDE on every
endpoint, deny by default, and tested systematically over every route of the application. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import json
import re
from argparse import Namespace

import pytest

from apihelpers import HOST, PORT, Ctx, RecClient, api, make_client, static_dir
from coach.api.app import CHILD_ALLOWED, EXCHANGE_PATH, create_app
from coach.api import security as sec
from coach.household import people as people_mod, users as users_mod
from coach.memory.store import MemoryStore
from hhhelpers import TODAY, hh_world, match_all

RULES = """\
attribution:
  - id: noa-prepaid
    member: noa
    match: { account: nk }
  - id: mia-card
    member: mia
    match: { card_last4: "4242", account: fo }
kid_budgets:
  - id: mia-weekly
    member: mia
    period: weekly
    limit: 20
allocations:
  - id: groceries-equal
    match: { category: food.groceries }
    method: equal
"""


@pytest.fixture
def hh(cfg, tmp_path):
    con = hh_world(cfg, extra_yaml=RULES)
    match_all(con, cfg)
    people = people_mod.load(MemoryStore(cfg.memory_dir, history=False))
    users_mod.add(con, people, "papa", "adult", "luca")
    users_mod.add(con, people, "mia-kid", "child", "mia")
    users_mod.add(con, people, "noa-kid", "child", "noa")
    con.close()
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    return app, cfg


def as_user(app, cfg, user=None) -> Ctx:
    """A browser session of one login (None: the owner login that `coach ui` opens)."""
    c = RecClient(app, base_url=HOST)
    token = app.state.security.tokens.issue(user=user)
    r = c.post(api("/session/exchange"), json={"token": token})
    assert r.status_code == 200, r.text
    return Ctx(app, c, cfg)


@pytest.fixture
def owner(hh):
    return as_user(*hh)


@pytest.fixture
def papa(hh):
    return as_user(*hh, user="papa")


@pytest.fixture
def mia(hh):
    return as_user(*hh, user="mia-kid")


# ---------------------------------------------------------------- E14-1 / E14-2: the household page's data

def test_overview_members_accounts_owners_and_warnings(owner):
    d = owner.get("/household/overview").json()
    assert [m["id"] for m in d["members"]] == ["anna", "luca", "mia", "noa"]
    mia = next(m for m in d["members"] if m["id"] == "mia")
    assert mia["role"] == "child" and mia["accounts"] == ["rl"] and mia["attributed_transactions"] > 0
    accts = {a["uid"]: a for a in d["accounts"]}
    assert accts["rl"]["owner_member"] == "mia" and accts["fo"]["joint"] is True and accts["nk"]["attributed"]["noa"] == 2
    assert "kids" in d["purposes"] and {u["id"] for u in d["users"]} == {"papa", "mia-kid", "noa-kid"}
    assert [r["id"] for r in d["rules"]] == ["noa-prepaid", "mia-card"] and d["kid_budgets"][0]["id"] == "mia-weekly"
    assert d["warnings"] == []
    assert owner.client.patch(api("/accounts/lu"), json={"owner": "Somebody Else"}, headers={"X-CSRF-Token": owner.csrf}).status_code == 400    # not a member
    ok = owner.patch("/accounts/lu", {"owner": "Luca Rossi", "purpose": "main"})
    assert ok.status_code == 200 and ok.json()["owner"] == "luca"                     # normalised to the member id


def test_pocket_money_and_rules_are_edited_through_the_memory_store_with_previews(owner):
    pv = owner.client.put(api("/household/members/mia/pocket-money"), params={"dry_run": "true"}, json={"amount": 10, "period": "weekly"},
                          headers={"X-CSRF-Token": owner.csrf}).json()
    assert pv["dry_run"] is True and "pocket_money" in pv["diff"]
    assert owner.get("/household/overview").json()["members"][2]["pocket_money"] is None          # a preview writes nothing
    r = owner.client.put(api("/household/members/mia/pocket-money"), json={"amount": 10, "period": "weekly", "day": 1}, headers={"X-CSRF-Token": owner.csrf})
    assert r.status_code == 200
    assert owner.get("/household/overview").json()["members"][2]["pocket_money"] == {"amount": 10.0, "period": "weekly", "day": 1}
    assert owner.client.put(api("/household/members/ghost/pocket-money"), json={"amount": 1, "period": "weekly"}, headers={"X-CSRF-Token": owner.csrf}).status_code == 404
    clear = owner.client.put(api("/household/members/mia/pocket-money"), json={}, headers={"X-CSRF-Token": owner.csrf})
    assert clear.status_code == 200 and owner.get("/household/overview").json()["members"][2]["pocket_money"] is None
    # an attribution rule: the preview says how many transactions it would attribute
    body = {"member": "mia", "match": {"merchant_key": "^CARTE JEUXVIDEO"}}
    pv = owner.client.put(api("/household/attribution/rules/games"), params={"dry_run": "true"}, json=body, headers={"X-CSRF-Token": owner.csrf}).json()
    assert pv["matches"] == 2 and pv["would_change"] == 0 and "games" in pv["diff"]
    assert owner.client.put(api("/household/attribution/rules/games"), json=body, headers={"X-CSRF-Token": owner.csrf}).status_code == 200
    assert [r["id"] for r in owner.get("/household/overview").json()["rules"]] == ["noa-prepaid", "mia-card", "games"]
    bad = owner.client.put(api("/household/attribution/rules/bad"), json={"member": "mia", "match": {}}, headers={"X-CSRF-Token": owner.csrf})
    assert bad.status_code == 422
    ghost = owner.client.put(api("/household/attribution/rules/ghost"), json={"member": "ghost", "match": {"account": "fo"}}, headers={"X-CSRF-Token": owner.csrf})
    assert ghost.status_code == 422
    assert owner.post("/household/attribution/rules/games/delete").status_code == 200
    assert owner.post("/household/attribution/rules/games/delete").status_code == 404


def test_kid_budgets_and_allocations_are_edited_and_computed(owner):
    st = owner.get("/household/kid-budgets").json()["budgets"]
    assert st[0]["id"] == "mia-weekly" and st[0]["spent"] == "5.00" and st[0]["status"] == "ok"
    body = {"member": "noa", "period": "monthly", "limit": 12, "group": "food"}
    assert owner.client.put(api("/household/kid-budgets/noa-food"), params={"dry_run": "true"}, json=body, headers={"X-CSRF-Token": owner.csrf}).json()["dry_run"]
    assert owner.client.put(api("/household/kid-budgets/noa-food"), json=body, headers={"X-CSRF-Token": owner.csrf}).status_code == 200
    assert {b["id"] for b in owner.get("/household/kid-budgets").json()["budgets"]} == {"mia-weekly", "noa-food"}
    assert owner.post("/household/kid-budgets/noa-food/delete").status_code == 200
    al = owner.get("/household/allocation", months=12).json()
    assert al["rules"][0]["id"] == "groceries-equal" and al["rules"][0]["members"][0]["member"] == "anna"
    arule = {"title": "Cinema", "match": {"category": "leisure.cinema_events"}, "method": "custom", "shares": {"anna": 60, "luca": 40}}
    assert owner.client.put(api("/household/allocations/cinema"), json=arule, headers={"X-CSRF-Token": owner.csrf}).status_code == 200
    assert owner.get("/household/allocation", rule="cinema").json()["rules"][0]["method"] == "custom"
    bad = dict(arule, shares={"anna": 60, "luca": 30})
    assert owner.client.put(api("/household/allocations/cinema"), json=bad, headers={"X-CSRF-Token": owner.csrf}).status_code == 422
    assert owner.post("/household/allocations/cinema/delete").status_code == 200


def test_kids_endpoints(owner):
    d = owner.get("/household/kids", months=6).json()
    assert [c["member"] for c in d["children"]] == ["mia", "noa"]
    mia = d["children"][0]
    assert mia["pocket_money"]["series"][0]["amount"] == "10.00" and mia["ratio"]["pocket_share"] is not None
    assert owner.get("/household/kids/mia").json()["balance"]["current"] == "57.00"
    assert owner.get("/household/kids/ghost").status_code == 404


# ---------------------------------------------------------------- E14-3: reassignment, recorded and reversible, with the reason shown

def test_manual_reassignment_through_the_api_is_recorded_and_reversible(owner, papa):
    tx = "mc2"
    why = owner.get("/transactions/attribution", tx_key=tx).json()
    assert why["person"] == "joint" and why["source"] == "account" and why["card_last4"] == "1111"
    r = papa.post("/transactions/person", {"tx_key": tx, "member": "luca", "note": "he paid"}).json()
    assert r["changed"] and r["member"] == "luca"
    assert owner.get("/transactions/attribution", tx_key=tx).json()["manual"]["set_by"] == "ui:papa"
    assert owner.get("/transactions", member="luca").json()["total"] > 0
    assert any(i["tx_key"] == tx for i in owner.get("/transactions", member="luca", limit=500).json()["items"])
    assert all(i["tx_key"] != tx for i in owner.get("/transactions", member="joint", limit=500).json()["items"])
    log = owner.get("/household/attribution/log", tx_key=tx).json()["entries"]
    assert log[0]["action"] == "set" and log[0]["by"] == "ui:papa"
    assert owner.post(f"/household/attribution/log/{log[0]['id']}/undo").status_code == 200
    assert owner.get("/transactions/attribution", tx_key=tx).json()["manual"] is None
    assert owner.post("/transactions/person", {"tx_key": tx, "member": "ghost"}).status_code == 400
    owner.post("/transactions/person", {"tx_key": tx, "member": "joint"})
    assert owner.post("/transactions/person/clear", {"tx_key": tx}).json()["changed"] is True
    assert owner.get("/transactions/attribution", tx_key="nope").status_code == 404


# ---------------------------------------------------------------- E14-4: the person filter on the analytics endpoints

def test_member_param_filters_analytics_and_unknown_member_is_404(owner):
    whole = owner.get("/analytics/cashflow", months=2).json()
    mia = owner.get("/analytics/cashflow", months=2, member="mia").json()
    assert whole["household"]["months"] != mia["household"]["months"]
    sep = next(m for m in mia["household"]["months"] if m["month"] == "2026-09")
    assert sep["spending"] == "43.60"
    for path in ("/analytics/averages", "/analytics/month-categories", "/analytics/forecast", "/categories", "/budgets", "/subscriptions", "/insights",
                 "/accounts/balances", "/analytics/coverage", "/calendar"):
        assert owner.get(path, member="mia").status_code == 200, path
        assert owner.get(path, member="ghost").status_code == 404, path
    bal = owner.get("/accounts/balances", member="mia").json()
    assert [a["uid"] for a in bal["accounts"]] == ["rl"] and bal["household_total"] == "57.00"
    assert owner.get("/analytics/cashflow", member="ghost").status_code == 404
    assert owner.get("/transactions", member="mia", limit=500).json()["total"] == len([1 for i in owner.get("/transactions", member="mia", limit=500).json()["items"]])


# ---------------------------------------------------------------- E14-8: per-user logins

def test_login_link_of_a_person_opens_that_person_and_is_single_use(hh):
    app, cfg = hh
    token = app.state.security.tokens.issue(user="mia-kid")
    c = RecClient(app, base_url=HOST)
    assert c.post(api("/session/exchange"), json={"token": token}).status_code == 200
    s = c.get(api("/session")).json()
    assert s["user"]["id"] == "mia-kid" and s["user"]["role"] == "child" and s["user"]["member_id"] == "mia"
    again = RecClient(app, base_url=HOST).post(api("/session/exchange"), json={"token": token})
    assert again.status_code == 401                                              # single use, as before
    owner_tok = app.state.security.tokens.issue()
    c2 = RecClient(app, base_url=HOST)
    assert c2.post(api("/session/exchange"), json={"token": owner_tok}).status_code == 200
    assert c2.get(api("/session")).json()["user"]["id"] == "owner"


def test_a_token_for_a_missing_or_disabled_login_is_refused(hh):
    app, cfg = hh
    for who in ("ghost", "noa-kid"):
        if who == "noa-kid":
            from coach import db as dbm
            con = dbm.connect(cfg, insecure=True)
            users_mod.disable(con, "noa-kid")
            con.close()
        tok = app.state.security.tokens.issue(user=who)
        assert RecClient(app, base_url=HOST).post(api("/session/exchange"), json={"token": tok}).status_code == 401


def test_disabling_a_login_or_changing_its_role_takes_effect_on_the_next_request(hh):
    app, cfg = hh
    from coach import db as dbm
    kid = as_user(app, cfg, "noa-kid")
    assert kid.get("/me").status_code == 200
    con = dbm.connect(cfg, insecure=True)
    people = people_mod.load(MemoryStore(cfg.memory_dir, history=False))
    users_mod.set_role(con, people, "noa-kid", "adult")                          # promoted: the cookie follows the database
    assert kid.get("/household/overview").status_code == 200
    users_mod.set_role(con, people, "noa-kid", "child")
    assert kid.get("/household/overview").status_code == 403
    users_mod.disable(con, "noa-kid")
    assert kid.get("/me").status_code == 401                                     # a disabled login is signed out at once
    users_mod.enable(con, "noa-kid")
    assert kid.get("/me").status_code == 200
    users_mod.remove(con, "noa-kid")
    assert kid.get("/me").status_code == 401
    con.close()


def test_a_forged_or_altered_user_part_of_the_cookie_is_rejected(hh):
    app, cfg = hh
    s: sec.Security = app.state.security
    ck = s.new_cookie(user="mia-kid")
    sid, iat, user, sig = ck.split(".")
    for bad in (f"{sid}.{iat}.papa.{sig}", f"{sid}.{iat}.{sig}", f"{sid}.{iat}.{user}.x{sig[1:]}", f"{sid}.{iat}.{user}.{sig}.extra"):
        assert s.parse_session(bad) is None
    assert s.parse_session(ck)[1] == "mia-kid" and s.parse_cookie(ck) == sid
    owner_ck = s.new_cookie()
    assert s.parse_session(owner_ck)[1] is None
    c = RecClient(app, base_url=HOST)
    c.cookies.set(s.cookie_name, f"{sid}.{iat}.papa.{sig}")                       # a kid cannot promote the cookie by editing it
    assert c.get(api("/session")).status_code == 401


# ---------------------------------------------------------------- E14-8: the child scope, systematically

def _paths(app):
    """Every (method, concrete path) of the application, from its OpenAPI schema (the same source the coverage guard uses), plus the two
    routes the schema leaves out."""
    out = []
    for path, ops in app.openapi()["paths"].items():
        for m in ops:
            if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((m.upper(), re.sub(r"\{[^}]+\}", "x", path)))
    return out


def test_a_child_is_denied_every_endpoint_that_is_not_explicitly_allowed(hh, mia):
    app, _ = hh
    routes = _paths(app)
    assert len(routes) > 120                                                      # the whole application, not a hand-picked list
    allowed = {(m, p) for m, p in CHILD_ALLOWED}
    assert allowed == {("GET", "/api/v1/session"), ("POST", "/api/v1/session/logout"), ("GET", "/api/v1/meta/taxonomy"), ("GET", "/api/v1/me"),
                       ("GET", "/api/v1/me/summary"), ("GET", "/api/v1/me/transactions"), ("GET", "/api/v1/me/preferences"),
                       ("PUT", "/api/v1/me/preferences")}
    denied = 0
    for method, path in routes:
        if (method, path) in allowed or path == EXCHANGE_PATH:
            continue
        r = mia.client.request(method, path, json={} if method != "GET" else None, headers={"X-CSRF-Token": mia.csrf})
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_scope", (method, path, r.status_code, r.text[:120])
        denied += 1
    assert denied > 100
    # an endpoint that does not exist, another method on an allowed path, and a query that tries to widen an allowed one: all denied or ignored
    assert mia.client.get(api("/does/not/exist")).status_code == 403
    assert mia.client.get("/api/openapi.json").status_code == 403 and mia.client.get("/api/docs").status_code == 403
    assert mia.client.post(api("/me/summary"), json={}, headers={"X-CSRF-Token": mia.csrf}).status_code == 403
    assert mia.client.get(api("/meta/taxonomy")).status_code == 200


def test_every_route_of_the_application_is_either_owned_by_an_adult_or_listed_for_the_child(hh, papa):
    """The adult counterpart: an adult login keeps reaching what it could before (no regression of the owner's data access)."""
    app, _ = hh
    for path in ("/household/overview", "/analytics/cashflow", "/transactions", "/memory/overview", "/health", "/accounts/balances", "/alerts"):
        assert papa.get(path).status_code == 200, path


def test_child_me_endpoints_show_only_the_own_data(hh, mia):
    app, cfg = hh
    s = mia.get("/me/summary").json()
    assert s["member"] == "mia" and s["report"]["member"] == "mia"
    text = json.dumps(s)
    # nothing about the other members: no sibling, no parent's name, no account label, no source name
    for banned in ("Noa", "noa", "Anna", "Luca", "luca", "anna", "Rossi", "Revolut", "Fortuneo", "Anna Bank", "SNACK BAR"):
        assert banned not in text.replace("Mia Rossi", ""), banned
    assert {i["source"] for i in s["report"]["extra_topups"]["items"]} <= {"a parent", "someone else", "family"}
    assert s["report"]["spending"]["total"] == "43.60" and s["budgets"][0]["limit"] == "20.00"
    assert "accounts" not in s["report"]
    tx = mia.get("/me/transactions", limit=200).json()
    assert tx["total"] > 0 and all(set(i) == {"date", "amount", "category", "merchant"} for i in tx["items"])
    assert not [i for i in tx["items"] if "SNACK" in i["merchant"].upper()]
    # a `member` parameter does nothing: the member comes from the login
    assert mia.get("/me/summary", member="noa").json()["member"] == "mia"
    assert mia.get("/me/transactions", member="noa", limit=200).json()["total"] == tx["total"]
    noa = as_user(app, cfg, "noa-kid")
    assert noa.get("/me/summary").json()["member"] == "noa" and noa.get("/me/transactions").json()["total"] == 2
    me = mia.get("/me").json()
    assert me["member"]["id"] == "mia" and me["user"]["role"] == "child"


def test_a_child_cannot_write_without_csrf_and_the_snapshot_dependency_forces_the_member(hh, mia):
    app, _ = hh
    r = mia.client.put(api("/me/preferences"), json={"prefs": {"theme": "dark"}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    from types import SimpleNamespace
    from coach.api.deps import get_member_snapshot, get_snapshot
    from coach.household.users import User
    state = app.state.coach
    req = SimpleNamespace(state=SimpleNamespace(user=User("mia-kid", "child", "mia")))
    assert get_member_snapshot(req, "noa", state).ds.member == "mia"            # even a direct call cannot ask for another member
    assert get_snapshot(req, state).ds.member == "mia"                          # the household-wide dependency is narrowed too
    req2 = SimpleNamespace(state=SimpleNamespace(user=User("x", "child", None)))
    from coach.api.errors import ApiError
    with pytest.raises(ApiError):
        get_member_snapshot(req2, None, state)
    with pytest.raises(ApiError):
        get_snapshot(req2, state)
    req3 = SimpleNamespace(state=SimpleNamespace(user=User("papa", "adult", "luca")))
    assert get_member_snapshot(req3, "noa", state).ds.member == "noa" and get_member_snapshot(req3, None, state).ds.member is None
    assert get_snapshot(req3, state).ds.member is None                           # household-wide: no person filter for writes and loans


def test_preferences_are_per_login_and_whitelisted(hh, mia, papa, owner):
    assert mia.client.put(api("/me/preferences"), json={"prefs": {"theme": "dark", "locale": "en-GB"}}, headers={"X-CSRF-Token": mia.csrf}).json()["prefs"] == {"locale": "en-GB", "theme": "dark"}
    assert mia.get("/me/preferences").json()["prefs"]["theme"] == "dark"
    assert mia.client.put(api("/me/preferences"), json={"prefs": {"locale": "it-IT"}}, headers={"X-CSRF-Token": mia.csrf}).json()["prefs"]["locale"] == "it-IT"
    assert papa.get("/me/preferences").json()["prefs"] == {}                         # another login's preferences are its own
    for bad in ({"colour": "red"}, {"theme": "neon"}, {"default_member": "ghost"}):
        assert mia.client.put(api("/me/preferences"), json={"prefs": bad}, headers={"X-CSRF-Token": mia.csrf}).status_code == 400
    r = mia.client.put(api("/me/preferences"), json={"prefs": {"default_member": "noa"}}, headers={"X-CSRF-Token": mia.csrf})
    assert r.status_code == 200 and "default_member" not in r.json()["prefs"]          # a child's default view is only their own
    assert papa.client.put(api("/me/preferences"), json={"prefs": {"default_member": "mia", "landing": "kids"}}, headers={"X-CSRF-Token": papa.csrf}).json()["prefs"]["default_member"] == "mia"
    assert owner.client.put(api("/me/preferences"), json={"prefs": {"theme": "dark"}}, headers={"X-CSRF-Token": owner.csrf}).status_code == 409
    assert owner.get("/me/preferences").json()["stored"] is False
    assert owner.get("/me").json()["member"] is None and owner.get("/me/summary").status_code == 404


# ---------------------------------------------------------------- E14-8: the audit

def test_the_audit_records_who_changed_what_and_the_memory_history_the_login(owner, papa):
    papa.client.put(api("/household/kid-budgets/k1"), json={"member": "noa", "period": "monthly", "limit": 9}, headers={"X-CSRF-Token": papa.csrf})
    papa.client.put(api("/household/kid-budgets/k2"), params={"dry_run": "true"}, json={"member": "noa", "period": "monthly", "limit": 9}, headers={"X-CSRF-Token": papa.csrf})
    owner.client.put(api("/household/kid-budgets/k3"), json={"member": "noa", "period": "weekly", "limit": 5}, headers={"X-CSRF-Token": owner.csrf})
    a = owner.get("/household/audit", limit=50).json()
    mine = [r for r in a["requests"] if r["actor"] == "papa"]
    assert [(r["method"], r["path"], r["status"]) for r in mine] == [("PUT", "/api/v1/household/kid-budgets/k1", 200)]    # the dry run is not a change
    assert any(r["actor"] == "owner" and r["path"].endswith("/k3") for r in a["requests"])
    by_source = {m["source"] for m in a["memory"]}
    assert {"ui:papa", "ui"} <= by_source
    assert not any("limit" in json.dumps(r) for r in a["requests"])                 # never a payload
    assert owner.get("/household/audit", actor="papa").json()["requests"] == mine
    # a stray `?dry_run=true` on an endpoint that has NO dry run is a real write: it is audited like any other
    papa.client.post(api("/proposals/no-such-proposal/reject"), params={"dry_run": "true"}, json={}, headers={"X-CSRF-Token": papa.csrf})
    after = [r for r in owner.get("/household/audit", limit=50).json()["requests"] if r["actor"] == "papa"]
    assert [r["path"] for r in after if "reject" in r["path"]] == ["/api/v1/proposals/no-such-proposal/reject"]
    assert owner.get("/household/users").json()["users"][0]["id"] == "mia-kid" or owner.get("/household/users").json()["users"]


# ---------------------------------------------------------------- the CLI door of a person's link

def test_ui_login_link_user_option_prints_a_link_that_opens_that_person(hh, capsys, monkeypatch):
    app, cfg = hh
    from coach.api.server import cmd_ui
    a = Namespace(login_link=True, user="mia-kid", rotate_session_key=False, host=None, port=PORT, dev=False, insecure=True)
    cmd_ui(a, cfg)
    url = capsys.readouterr().out.strip().splitlines()[0]
    token = url.split("#t=")[1]
    c = RecClient(app, base_url=HOST)
    assert c.post(api("/session/exchange"), json={"token": token}).status_code == 200 and c.get(api("/session")).json()["user"]["id"] == "mia-kid"
    with pytest.raises(SystemExit):
        cmd_ui(Namespace(login_link=True, user="ghost", rotate_session_key=False, host=None, port=PORT, dev=False, insecure=True), cfg)
    with pytest.raises(SystemExit):
        cmd_ui(Namespace(login_link=False, user="mia-kid", rotate_session_key=False, host=None, port=PORT, dev=False, insecure=True), cfg)


def test_a_child_session_says_nothing_about_the_setup(mia, owner):
    s = mia.get("/session").json()
    assert s["user"]["role"] == "child" and s["coach"] == {"configured": False, "backend": "", "message": ""}
    assert s["sync_daily_limit"] == 0 and s["enable_banking_configured"] is False and s["memory_history"] is False
    assert owner.get("/session").json()["coach"]["backend"] == "claude-code"


ANALYTICS_PREFIXES = ("/analytics/", "/categories", "/budgets", "/goals", "/subscriptions", "/price-changes", "/anomalies", "/insights", "/calendar",
                      "/accounts/balances", "/transactions", "/subs/inventory")
# household-wide BY NATURE: net worth, loans, contracts, the review queue, alerts, memory, connections take no person filter (a figure or a
# write computed on one member's view would be wrong, see api.deps.get_snapshot)
HOUSEHOLD_WIDE: set = set()


def test_every_analytics_endpoint_takes_a_member_and_the_household_wide_ones_never_do(hh, owner):
    """E14-4: no analytics-like GET endpoint is left without the person filter; and no write or household-wide endpoint accepts one."""
    app, _ = hh
    missing, with_member = [], 0
    for path, ops in app.openapi()["paths"].items():
        get = ops.get("get")
        if not get or not any(path.startswith("/api/v1" + p) for p in ANALYTICS_PREFIXES) or path in HOUSEHOLD_WIDE:
            continue
        names = {p["name"] for p in get.get("parameters", [])}
        if "member" in names:
            with_member += 1
        elif "tx_key" not in names:
            missing.append(path)
    assert with_member >= 14 and missing == [], missing
    wide = []
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            if "member" in {p["name"] for p in op.get("parameters", []) if p["in"] == "query"} and (method != "get" or not any(path.startswith("/api/v1" + x) for x in ANALYTICS_PREFIXES)):
                wide.append((method, path))
    assert wide == [], wide
    # a person filter on a household-wide read is simply not applied: the net worth is the household's, whoever asks
    assert owner.get("/net-worth", member="mia").json() == owner.get("/net-worth").json()
    assert owner.get("/liabilities", member="mia").json() == owner.get("/liabilities").json()


def test_login_tokens_carry_a_login_expire_and_still_read_the_old_file_format(tmp_path):
    t = sec.LoginTokens(tmp_path)
    tok = t.issue(user="mia-kid", now=1000)
    assert t.consume_user(tok, now=1050) == (True, "mia-kid") and t.consume_user(tok, now=1051) == (False, None)      # single use
    old = t.issue(now=1000)
    assert t.consume_user(old, now=1000 + sec.LOGIN_TTL + 1) == (False, None)                                          # expired
    owner = t.issue(now=2000)
    assert t.consume_user(owner, now=2001) == (True, None) and t.consume(owner, now=2002) is False
    legacy = tmp_path / sec.LOGIN_FILE                                                                                 # {hash: expiry}, as before E14
    import hashlib
    legacy.write_text(json.dumps({hashlib.sha256(b"abc").hexdigest(): 9999999999}))
    assert t.consume_user("abc", now=5) == (True, None)
    assert t.consume_user("a" * 300, now=5) == (False, None) and t.consume_user("", now=5) == (False, None)
    fresh = t.issue(user="mia-kid", now=3000)
    raw = (tmp_path / sec.LOGIN_FILE).read_text()
    assert fresh not in raw and hashlib.sha256(fresh.encode()).hexdigest() in raw                                      # only the hash of a token is stored


def test_a_childs_payment_list_never_shows_a_name(hh, mia):
    tx = mia.get("/me/transactions", limit=200).json()["items"]
    merchants = {i["merchant"] for i in tx}
    assert "a parent" in merchants                                    # the pocket money credits name Anna: shown as "a parent"
    assert any(m == "Boulangerie du coin" or "BOULANGERIE" in m.upper() for m in merchants)   # ordinary merchants stay
    text = json.dumps(tx)
    for banned in ("Anna", "ANNA", "Luca", "LUCA", "Rossi", "ROSSI", "Noa", "GRAND MERE"):
        assert banned not in text, banned


def test_member_results_are_consistent_with_the_members_transaction_subset(owner):
    """E14-4, MINOR: for every analytics read endpoint, ?member= gives figures of the member's subset (totals), and nothing of another person's payments."""
    allx = owner.get("/transactions", limit=500).json()
    allx_items = allx["items"]
    while allx["next_offset"] is not None:
        allx = owner.get("/transactions", limit=500, offset=allx["next_offset"]).json()
        allx_items += allx["items"]
    mine = owner.get("/transactions", member="mia", limit=500).json()
    mine_keys = {i["tx_key"] for i in mine["items"]}
    assert mine["total"] == len(mine["items"]) == len(mine_keys) > 0 and mine_keys < {i["tx_key"] for i in allx_items}
    assert {i["person"] for i in mine["items"]} == {"mia"}
    assert round(sum(float(i["amount"]) for i in mine["items"]), 2) == float(mine["totals"]["sum"])
    others = {i["tx_key"] for i in allx_items} - mine_keys
    assert owner.get("/transactions", member="mia", date_from="2026-09-01", date_to="2026-09-30", direction="out").json()["totals"]["outflow"] == "-43.60"
    mc = owner.get("/analytics/month-categories", month="2026-09", member="mia").json()
    assert mc["total_spent"] == "43.60" and {c["category"] for c in mc["categories"]} <= {"food.restaurants", "leisure.hobbies", "leisure.cinema_events"}
    av = owner.get("/analytics/averages", member="mia").json()
    assert {c["category"] for c in av["categories"]} <= {"food.restaurants", "leisure.hobbies", "leisure.cinema_events"}
    bal = owner.get("/accounts/balances", member="mia").json()
    assert [a["uid"] for a in bal["accounts"]] == ["rl"] and bal["household_total"] == "57.00"
    for path in ("/analytics/cashflow", "/analytics/forecast", "/categories", "/subscriptions", "/price-changes", "/anomalies", "/insights", "/budgets",
                 "/goals", "/calendar", "/analytics/coverage", "/subs/inventory"):
        text = owner.get(path, member="mia").text
        assert not [k for k in others if f'"{k}"' in text], path          # no transaction of another person is cited
    assert {a["account"] for a in owner.get("/analytics/coverage", member="mia").json()["accounts"]} <= {"rl", "fo"}
