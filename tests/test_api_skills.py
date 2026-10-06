"""E7: the web side of the skills - skill quick prompts and `skill` on the coach endpoints, the onboarding checklist and its two small
previewed writes (household declarations, preferences). Fake runner: no model, no network."""
from __future__ import annotations

import json

import pytest

from apihelpers import api, ctx, world  # noqa: F401
from coach.agent import prompt as P
from coach.agent.runner import RunResult
from coach.classify.backends import Usage
from test_api_coach import parse

WHICH = "coach.api.coachjobs.shutil.which"


@pytest.fixture(autouse=True)
def claude_present(monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")


def recording_runner(seen):
    def run(cfg, spec, question, *, emit, cancel, insecure, session_id, **kw):
        seen.append((spec.id, question))
        emit("delta", {"text": "Reviewed."})
        return RunResult(text="Reviewed.", finish_reason="stop", usage=Usage("claude-code", "sonnet", f"coach:{spec.id}", 1, 10, 5, 0, 0, 0.01, True, 1.0),
                         tool_calls=[], session_id=session_id, backend="claude-code", model="sonnet", refs=[], unverified_numbers=[])
    return run


def test_the_quick_prompts_include_the_runnable_skills_and_a_skill_request_runs_that_skills_prompt(ctx):
    prompts = ctx.get("/coach/prompts").json()["prompts"]
    skills = {p["skill"]: p["text"] for p in prompts if p.get("skill")}
    assert set(skills) == set(P.SKILL_SPECS) and "find-cheaper" not in skills               # the web runtime has no web search
    assert any(not p.get("skill") for p in prompts) and skills["monthly-review"] == "Review last month"
    seen = []
    ctx.state.coach_jobs.runner = recording_runner(seen)
    ev = parse(ctx.post("/coach/stream", {"question": "Review last month", "skill": "monthly-review"}).text)
    assert seen == [("monthly-review", "Review last month")] and ev[-1][0] == "done"
    ctx.post("/coach/stream", {"question": "plain question"})
    assert seen[-1] == ("ask", "plain question")
    row = ctx.sql("SELECT kind, skill, title FROM insights ORDER BY created")
    assert ("review", "monthly-review", "Review last month") in row and any(k == "answer" and sk == "ask" for k, sk, _ in row)
    snap = ctx.post("/coach/jobs", {"question": "Which expenses could lower my taxes?", "skill": "tax-helper"}).json()
    assert snap["skill"] == "tax-helper" and seen[-1][0] == "tax-helper"


def test_an_unknown_or_web_only_skill_is_refused_without_starting_a_job(ctx):
    seen = []
    ctx.state.coach_jobs.runner = recording_runner(seen)
    for skill in ("nonsense", "find-cheaper", "../etc"):
        r = ctx.post("/coach/stream", {"question": "x", "skill": skill})
        assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_skill"
    assert seen == [] and ctx.get("/coach/status").json()["busy"] is False
    assert "monthly_review" in ctx.get("/coach/status").json()["tools"] and "questions_propose" in ctx.get("/coach/status").json()["tools"]


def test_the_onboarding_checklist(ctx):
    d = ctx.get("/onboarding").json()
    steps = {s["id"]: s for s in d["steps"]}
    assert d["progress"]["total"] == 7 and set(steps) == {"household", "accounts", "loans", "contracts", "preferences", "budgets", "questions"}
    assert steps["household"]["status"] in ("partial", "todo") and any("country" in m for m in steps["household"]["missing"])
    assert steps["loans"]["liabilities"][0]["id"] == "home-loan" and "insurance.monthly" in steps["loans"]["liabilities"][0]["missing"]
    assert steps["contracts"]["recurring_without_contract"]
    assert d["next_actions"] and all(a["command"].startswith("uv run coach") for a in d["next_actions"])


def test_household_declarations_are_previewed_then_written_through_the_store(ctx):
    path = ctx.cfg.memory_dir / "household.yaml"
    from coach.memory.store import MemoryStore
    MemoryStore(ctx.cfg.memory_dir, history=False).edit("household.yaml", [{"op": "set", "path": "employers", "value": ["Acme Corp"]},
                                                                           {"op": "set", "path": "places", "value": ["Lillebourg"]}], action="test")
    before = path.read_text()
    pv = ctx.put("/onboarding/household", {"country": "IT", "employers": {"add": ["Globex", "  "]}, "places": {"add": ["Rouen"]}}, dry_run=True).json()
    assert pv["dry_run"] and pv["changed"] and "country: IT" in pv["diff"] and path.read_text() == before
    assert pv["added"] == {"employers": ["Globex"], "places": ["Rouen"]} and pv["removes_privacy_terms"] is False
    w = ctx.put("/onboarding/household", {"country": "IT", "places": {"add": ["Rouen"]}}).json()
    assert w["changed"] and w["change_id"]
    from coach.memory.store import MemoryStore
    store = MemoryStore(ctx.cfg.memory_dir, history=False)
    hh = store.load_plain("household.yaml")
    # ADD semantics: the declared town and employer of the file are still there
    assert hh["country"] == "IT" and hh["places"] == ["Lillebourg", "Rouen"] and hh["employers"] == ["Acme Corp"]
    assert not [i for i in store.validate_all() if i.level == "error"]
    assert ctx.put("/onboarding/household", {"country": "DE"}).status_code == 422
    assert ctx.put("/onboarding/household", {}).json()["error"]["code"] == "nothing_to_change"
    assert ctx.put("/onboarding/household", {"places": {"add": ["rouen", "LILLEBOURG"]}}).json()["error"]["code"] == "nothing_to_change"   # already there
    assert "ui" in json.dumps(ctx.get("/memory/history").json())
    path.unlink()
    assert ctx.put("/onboarding/household", {"country": "FR"}).json()["changed"] and "country: FR" in path.read_text()


def test_a_real_shaped_list_of_nineteen_places_survives_a_single_addition_and_removal_needs_confirmation(ctx):
    from coach.memory.store import MemoryStore
    places = [f"Town Number {i}" for i in range(19)]
    store = MemoryStore(ctx.cfg.memory_dir, history=False)
    store.edit("household.yaml", [{"op": "set", "path": "places", "value": places}], action="test")
    d = ctx.get("/onboarding").json()
    assert d["declared"]["places"] == places                                  # the form can pre-fill every chip
    assert ctx.put("/onboarding/household", {"places": {"add": ["One More"]}}).json()["changed"]
    assert store.load_plain("household.yaml")["places"] == places + ["One More"]            # 20 entries: 19 kept
    # removal: previewed with a warning, refused without the flag, written with it; only the named term goes
    pv = ctx.put("/onboarding/household", {"places": {"remove": ["town number 3"]}}, dry_run=True).json()
    assert pv["removes_privacy_terms"] and "no longer be masked" in pv["warning"] and pv["removed"] == {"places": ["Town Number 3"]}
    r = ctx.put("/onboarding/household", {"places": {"remove": ["town number 3"]}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirm_required"
    assert "Town Number 3" in store.load_plain("household.yaml")["places"]
    assert ctx.put("/onboarding/household", {"places": {"remove": ["town number 3"]}, "confirm_removal": True}).json()["changed"]
    left = store.load_plain("household.yaml")["places"]
    assert len(left) == 19 and "Town Number 3" not in left
    # caps: 50 per list, 50 per request
    assert ctx.put("/onboarding/household", {"places": {"add": [f"X{i}" for i in range(51)]}}).status_code == 422
    assert ctx.put("/onboarding/household", {"places": {"add": [f"Y{i}" for i in range(40)]}}).json()["error"]["code"] == "too_many_terms"


def test_preferences_are_appended_after_a_preview(ctx):
    path = ctx.cfg.memory_dir / "preferences.md"
    before = path.read_text()
    pv = ctx.post("/onboarding/preferences", {"language": "fr", "tone": "short"}, dry_run=True).json()
    assert pv["dry_run"] and "- Language: fr" in pv["diff"] and path.read_text() == before
    assert ctx.post("/onboarding/preferences", {"language": "fr", "tone": "short", "goals": "save for the roof"}).json()["changed"]
    t = path.read_text()
    assert t.startswith(before.rstrip("\n")) and "- Language: fr" in t and "- Tone: short" in t and "- Goals: save for the roof" in t
    assert ctx.post("/onboarding/preferences", {}).json()["error"]["code"] == "nothing_to_change"
    path.unlink()
    assert ctx.post("/onboarding/preferences", {"language": "en"}).json()["error"]["code"] == "missing_file"
    assert ctx.client.put(api("/onboarding/household"), json={"country": "FR"}).status_code == 403          # CSRF still applies
