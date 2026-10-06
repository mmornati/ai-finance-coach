"""E12-5: scoring the coach's answers (offline, over stored insights) and the curated question suite run through the real runner with a fake
`claude`. No model, no network. Every text here is invented."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from coach import disclaimers
from coach.agent import insights as I, prompt as P
from coach.agent.runner import RunResult
from coach.classify.backends import Usage
from coach.cli import main
from coach.db import connect
from coach.quality import coachcheck as CC, runs
from mcphelpers import world  # noqa: F401
from test_agent_runner import fake_claude  # noqa: F401  (the fixture that installs the fake `claude` executable)

SUITE = Path(__file__).resolve().parents[1] / "evals" / "coach_questions.yaml"
REF = "h_0123456789"


def item(body, skill="ask", evidence=(), unverified=(), ai=True, backend="claude-code", **kw):
    return {"id": kw.pop("id", "x1"), "skill": skill, "title": "t", "body": body, "evidence": list(evidence), "unverified_numbers": list(unverified),
            "ai_generated": ai, "backend": backend, **kw}


def checks(it, extra=None):
    return CC.score_text(it, extra)["checks"]


# ---------------------------------------------------------------- the checks, one by one

def test_a_clean_answer_passes_every_check_that_applies():
    s = CC.score_text(item(f"Groceries cost 120.50 EUR last month ({REF}).", evidence=[REF]))
    assert s["passed"] and s["failed"] == []
    c = s["checks"]
    assert c["numbers_traced"]["pass"] and c["numbers_traced"]["numbers"] == 1 and c["numbers_traced"]["traced_ratio"] == 1.0
    assert c["evidence_refs"]["pass"] and c["ai_label"]["pass"] and c["compliance"]["pass"] and c["length"]["pass"]
    assert c["sections"]["applicable"] is False and c["completed"]["applicable"] is False


def test_numbers_traced_ratio_and_the_unverified_numbers_are_listed():
    c = checks(item(f"You spent 120.50 EUR and 999.99 EUR ({REF}).", evidence=[REF], unverified=["999.99"]))["numbers_traced"]
    assert c["pass"] is False and c["numbers"] == 2 and c["unverified"] == ["999.99"] and c["traced_ratio"] == 0.5
    # a number the runner flagged that the text no longer holds does not count against it
    assert checks(item("Nothing changed (5 payments).", unverified=["999.99"]))["numbers_traced"]["applicable"] is False
    assert checks(item("No figure at all."))["numbers_traced"]["applicable"] is False


def test_evidence_refs_must_be_ones_a_tool_returned():
    c = checks(item(f"See {REF} and h_ffffffffff.", evidence=[REF]))["evidence_refs"]
    assert c["pass"] is False and c["invalid"] == ["h_ffffffffff"] and c["cited"] == 2
    assert checks(item("A claim with no ref."))["evidence_refs"]["applicable"] is False


def test_a_model_written_text_must_carry_the_ai_flag():
    assert checks(item("Hello 1.50 EUR", ai=False))["ai_label"]["pass"] is False
    assert checks(item("Hello 1.50 EUR", ai=True))["ai_label"]["pass"] is True
    assert checks(item("A deterministic digest", ai=False, backend="local"))["ai_label"]["applicable"] is False


def test_the_compliance_check_runs_on_the_text():
    c = checks(item("You could consider US0378331005 for your savings."))["compliance"]
    assert c["pass"] is False and c["codes"] == ["isin"]
    assert checks(item("You should buy shares of a world ETF now."))["compliance"]["codes"] == ["recommendation"]


def test_monthly_review_needs_the_three_actions_heading_and_exactly_three():
    good = "Saved 200.00 EUR.\n\n## Three actions\n\n1. Cut one thing.\n2. Move a payment.\n3. Review a budget.\n"
    assert checks(item(good, skill="monthly-review"))["sections"] == {"applicable": True, "pass": True, "problems": []}
    four = good + "4. One more.\n"
    c = checks(item(four, skill="monthly-review"))["sections"]
    assert c["pass"] is False and "4 numbered action(s), exactly 3 required" in c["problems"][0]
    two = "Saved.\n\n**Three actions**\n1. A.\n2. B.\n"
    assert checks(item(two, skill="monthly-review"))["sections"]["pass"] is False
    none = "Saved 200.00 EUR. Do three things: 1. A 2. B 3. C"
    assert checks(item(none, skill="monthly-review"))["sections"]["problems"] == ["the heading is missing"]
    assert checks(item(good, skill="explain-spike"))["sections"]["applicable"] is False


def test_the_investment_disclaimer_is_required_when_the_text_talks_about_saving_or_investing():
    talk = "Putting aside savings every month builds a cushion."
    assert checks(item(talk))["disclaimer"]["pass"] is False and checks(item(talk))["disclaimer"]["missing"] == ["general advice sentence"]
    for lang in ("en", "fr", "it"):
        assert checks(item(f"{talk} {disclaimers.get('general_advice', lang)}"))["disclaimer"]["pass"] is True
    assert checks(item("Groceries cost 120.50 EUR."))["disclaimer"]["applicable"] is False        # nothing about saving: nothing required
    assert checks(item("Your subscriptions cost 80.00 EUR.", skill="subscription-audit"))["disclaimer"]["pass"] is False    # this skill always ends with it
    assert checks(item("Your subscriptions cost 80.00 EUR. This is general information, not financial advice.", skill="subscription-audit"))["disclaimer"]["pass"]


def test_skills_with_their_own_required_wording():
    assert checks(item("The rate looks fine. This is general information, not financial advice.", skill="mortgage-check"))["disclaimer"]["pass"] is False
    assert checks(item("Estimate only: consult your bank. This is general information, not financial advice.", skill="mortgage-check"))["disclaimer"]["pass"]
    assert checks(item("You can cancel it.", skill="contract-check"))["disclaimer"]["pass"] is False
    assert checks(item("You can cancel it. " + disclaimers.get("contract_verify", "en"), skill="contract-check"))["disclaimer"]["pass"]
    assert checks(item("Candidates only.", skill="tax-helper"))["disclaimer"]["pass"] is False


def test_length_follows_what_the_skill_prompt_asks_with_ten_percent_slack():
    ok = " ".join(["word"] * 195)                    # the weekly digest asks for at most 180 words: 195 <= 198
    assert checks(item(ok, skill="digest-weekly"))["length"] == {"applicable": True, "pass": True, "words": 195, "max_words": 180}
    assert checks(item(" ".join(["word"] * 199), skill="digest-weekly"))["length"]["pass"] is False
    assert checks(item("hi"))["length"]["pass"] is False                                       # an empty-ish answer is not an answer
    assert checks(item(ok, skill="digest-weekly"), {"max_words": 50})["length"]["max_words"] == 50      # a question can tighten it


def test_question_expectations():
    ex = {"must_include_any": ["(?i)proposal"], "must_not_include": ["(?i)deleted"]}
    assert checks(item("I created a proposal."), ex)["sections"]["pass"] is True
    assert checks(item("Done."), ex)["sections"]["pass"] is False
    assert checks(item("A proposal, then I deleted it."), ex)["sections"]["problems"] == ["a forbidden phrase is present"]


def test_a_live_run_must_finish_and_use_a_tool():
    live = lambda **k: item("Groceries cost 120.50 EUR.", unverified=[], **k)                   # noqa: E731
    assert checks(live(finish_reason="stop", tool_calls=2))["completed"]["pass"] is True
    assert checks(live(finish_reason="max_tool_calls", tool_calls=12))["completed"]["pass"] is False
    assert checks(live(finish_reason="stop", tool_calls=0))["completed"]["pass"] is False      # numbers come from tools
    assert checks(live(finish_reason="stop", tool_calls=0), {"no_tools": True})["completed"]["pass"] is True


def test_aggregate_rates_by_hand():
    a = CC.score_text(item(f"Spent 120.50 EUR ({REF}).", evidence=[REF], id="a"))
    b = CC.score_text(item(f"Spent 120.50 and 250.75 EUR ({REF}).", evidence=[REF], unverified=["250.75"], id="b"))
    c = CC.score_text(item("Saving is hard.", skill="ask", id="c"))        # no advice sentence: fails the disclaimer check
    g = CC.aggregate([a, b, c])
    assert (g["n"], g["all_passed"], g["pass_rate"]) == (3, 1, 0.3333)
    nt = g["checks"]["numbers_traced"]
    assert (nt["applicable"], nt["passed"], nt["rate"], nt["failing"]) == (2, 1, 0.5, ["b"])
    assert g["numbers"] == {"total": 3, "unverified": 1, "traced_ratio": 0.6667} and g["unverified_listed"] == ["250.75"]
    assert g["checks"]["disclaimer"]["failing"] == ["c"] and g["by_skill"] == {"ask": {"n": 3, "passed": 1, "rate": 0.3333}}
    assert CC.aggregate([])["pass_rate"] is None


# ---------------------------------------------------------------- offline: the stored answers

@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    yield c
    c.close()


def store(con, body, skill="ask", **kw):
    return I.add(con, kind=kw.pop("kind", "answer"), title="a question", body=body, skill=skill, backend=kw.pop("backend", "claude-code"),
                 model="sonnet", evidence=kw.pop("evidence", []), unverified_numbers=kw.pop("unverified", []), **kw)


def test_offline_scores_the_stored_answers_and_stores_the_run(cfg, con):
    good = store(con, f"Spent 10.00 EUR ({REF}).", evidence=[REF])
    bad = store(con, "Spent 10.00 and 99.00 EUR.", unverified=["99"])
    local = store(con, "A deterministic weekly summary 5.00 EUR.", backend="local", kind="digest", skill="weekly-local")
    store(con, "Review: saved 300.00 EUR.\n\n## Three actions\n1. A\n2. B\n", skill="monthly-review", kind="review")
    res = CC.run_offline(con, cfg)
    assert res["n"] == 3 and local not in {i["id"] for i in res["items"]}              # a text no model wrote is not scored
    failing = {i["id"]: i["failed"] for i in res["items"]}
    assert failing[bad] == ["numbers_traced"] and good not in failing
    assert any(f == ["sections"] for f in failing.values())                              # the review with two actions
    assert res["numbers"]["unverified"] == 1 and res["checks"]["sections"]["failing"]
    r = runs.get(con, res["run_id"])
    assert r["kind"] == "coach" and r["label"] == "offline" and r["summary"]["n"] == 3 and r["cost_usd"] is None
    assert "answer(s)" in CC.format_report(res) and "numbers traced" in CC.format_report(res)


def test_offline_filters_by_skill_and_limit(cfg, con):
    for i in range(4):
        store(con, f"Spent {i + 1}.50 EUR.", skill="ask")
    store(con, "Why? Because 9.90 EUR.", skill="explain-spike", kind="anomaly-explain")
    assert CC.run_offline(con, cfg, skill="explain-spike", store=False)["n"] == 1
    assert CC.run_offline(con, cfg, limit=2, store=False)["n"] == 2
    assert CC.run_offline(con, cfg, store=False)["n"] == 5
    assert con.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 0


def test_offline_with_nothing_stored_says_so(cfg, con):
    res = CC.run_offline(con, cfg, store=False)
    assert res["n"] == 0 and "nothing to score" in CC.format_report(res)


def test_the_offline_command(cfg, con, capsys):
    store(con, f"Spent 10.00 EUR ({REF}).", evidence=[REF])
    con.close()
    base = ["--insecure", "--config", str(cfg.config_path), "eval", "coach"]
    main(base + ["--offline"])
    assert "coach answer checks (offline): 1 answer(s)" in capsys.readouterr().out
    main(base + ["--offline", "--json", "--no-store"])
    assert json.loads(capsys.readouterr().out)["pass_rate"] == 1.0
    with pytest.raises(SystemExit):
        main(base)                                                    # one of --offline / --run is required


# ---------------------------------------------------------------- the question suite

def test_the_shipped_suite_is_valid_generic_and_covers_every_runnable_skill():
    qs = CC.load_questions(SUITE)
    assert len({q["id"] for q in qs}) == len(qs) >= 15
    assert {q["skill"] for q in qs} >= set(P.SKILL_SPECS) | {"ask", "digest-weekly", "digest-monthly"}
    for q in qs:
        text = q["question"]
        assert not re.search(r"\d", text) and "@" not in text and "http" not in text, q["id"]       # no amount, date, address or link
        assert q.get("lang", "en") in ("en", "fr", "it")
    raw = SUITE.read_text()
    assert not re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,}\b", raw)                                     # no IBAN
    assert {q["lang"] for q in qs if "lang" in q} == {"fr", "it"}


def test_the_suite_is_validated(tmp_path):
    p = tmp_path / "q.yaml"
    for bad in ("questions: []", "questions:\n  - {id: a}", "questions:\n  - {id: a, question: x}\n  - {id: a, question: y}",
                "questions:\n  - {id: a, question: x, skill: find-cheaper}", "- not a mapping"):
        p.write_text(bad)
        with pytest.raises(ValueError):
            CC.load_questions(p)
    p.write_text("questions:\n  - {id: a, question: x, skill: monthly-review, month: '2026-09'}\n")
    q = CC.load_questions(p)[0]
    assert CC.request_text(q) == "x\nmonth=2026-09"                       # the same request text `coach coach ask --skill` builds


# ---------------------------------------------------------------- the live run: gated, confirmed, scored, stored

def stub_runner(texts, usage=True, finish="stop", calls=1):
    seen = []

    def run(cfg, spec, question, **kw):
        seen.append((spec.id, question))
        text = texts[len(seen) - 1] if len(seen) <= len(texts) else texts[-1]
        u = Usage("claude-code", "sonnet", f"coach:{spec.id}", 2, 1000, 200, 0, 0, 0.05, True, 4.0) if usage else None
        return RunResult(text=text, finish_reason=finish, usage=u, tool_calls=[{"name": "coverage"}] * calls, refs=[REF], unverified_numbers=[],
                         backend="claude-code", model="sonnet", session_id="s")
    run.seen = seen
    return run


QS = [{"id": "q-ask", "skill": "ask", "question": "How are we doing?"},
      {"id": "q-review", "skill": "monthly-review", "question": "Review last month."},
      {"id": "q-invest", "skill": "ask", "question": "Which ETF?", "no_tools": True, "must_include_any": ["(?i)not financial advice"]}]
REVIEW = "Saved 100.00 EUR (h_0123456789).\n\n## Three actions\n1. A\n2. B\n3. C\n"


def test_the_plan_lists_sizes_and_bounds_the_cost(cfg):
    p = CC.plan(cfg, QS)
    assert [r["id"] for r in p["questions"]] == ["q-ask", "q-review", "q-invest"] and p["backend"] == "claude-code"
    assert p["questions"][1]["max_tool_calls"] == cfg.coach_max_tool_calls * 2            # monthly-review: tool_factor 2
    assert p["max_cost_usd"] == 1.0 + 2.0 + 1.0 and p["bytes"] > 3000
    assert "up to" in CC.format_plan(p)


def test_dry_run_calls_nothing(cfg, con):
    r = CC.run_live(con, cfg, QS, dry_run=True, runner=lambda *a, **k: pytest.fail("a model was called"), isatty=lambda: pytest.fail("needs no terminal"),
                    out=lambda *_: None)
    assert r["status"] == "dry-run" and len(r["plan"]["questions"]) == 3
    assert con.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


def test_a_live_run_needs_a_terminal_and_the_typed_phrase(cfg, con):
    with pytest.raises(CC.CoachEvalError) as e:
        CC.run_live(con, cfg, QS, isatty=lambda: False, input_fn=lambda p: pytest.fail("asked"), runner=lambda *a, **k: pytest.fail("called"), out=lambda *_: None)
    assert "needs a terminal" in str(e.value)
    runner = stub_runner(["x"])
    r = CC.run_live(con, cfg, QS, isatty=lambda: True, input_fn=lambda p: "y", runner=runner, out=lambda *_: None)
    assert r["status"] == "aborted" and runner.seen == []


def test_the_command_refuses_without_a_terminal(cfg, con, capsys):
    con.close()
    with pytest.raises(SystemExit) as e:
        main(["--insecure", "--config", str(cfg.config_path), "eval", "coach", "--run", "--questions", str(SUITE), "--skill", "monthly-review"])
    assert "needs a terminal" in str(e.value)


def test_the_command_dry_run_and_the_skill_filter(cfg, con, capsys):
    con.close()
    main(["--insecure", "--config", str(cfg.config_path), "eval", "coach", "--run", "--dry-run", "--skill", "monthly-review"])
    out = capsys.readouterr().out
    assert "skill-monthly-review" in out and "skill-what-if" not in out and "DRY RUN" in out
    with pytest.raises(SystemExit) as e:
        main(["--insecure", "--config", str(cfg.config_path), "eval", "coach", "--run", "--dry-run", "--skill", "find-cheaper"])
    assert "no question of the suite" in str(e.value)


def test_the_privacy_policy_refuses_a_cloud_coach_backend_in_local_only(cfg, con):
    cfg.privacy_local_only = True
    with pytest.raises(CC.CoachEvalError) as e:
        CC.run_live(con, cfg, QS, isatty=lambda: True, input_fn=lambda p: CC.PHRASE, runner=lambda *a, **k: pytest.fail("called"), out=lambda *_: None)
    assert "privacy policy refuses" in str(e.value)


def test_a_live_run_scores_stores_and_records_the_usage_without_adding_insights(cfg, con):
    runner = stub_runner(["Spent 10.00 EUR (h_0123456789).", REVIEW, "I cannot recommend a product. This is general information, not financial advice."])
    out = []
    res = CC.run_live(con, cfg, QS, isatty=lambda: True, input_fn=lambda p: CC.PHRASE, runner=runner, out=out.append)
    assert res["status"] == "done" and [s for s, _ in runner.seen] == ["ask", "monthly-review", "ask"]
    assert runner.seen[1][1] == "Review last month."
    assert res["n"] == 3 and res["all_passed"] == 3 and res["pass_rate"] == 1.0
    assert res["cost_usd"] == 0.15 and res["checks"]["completed"] == {"applicable": 3, "passed": 3, "rate": 1.0, "failing": []}
    r = runs.get(con, res["run_id"])
    assert (r["kind"], r["label"], r["backend"], r["model"], r["n"], r["cost_usd"]) == ("coach", "live", "claude-code", "sonnet", 3, 0.15)
    purposes = [x[0] for x in con.execute("SELECT purpose FROM llm_usage")]
    assert purposes == ["eval:coach"] * 3                                                 # the usage view can tell an evaluation from real use
    assert con.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 0                # the feed is not filled with test questions
    assert "PASS" in "\n".join(out)


def test_failures_are_named(cfg, con):
    runner = stub_runner(["Spent 10.00 and 77.00 EUR (h_0123456789).", "Saved 100.00 EUR.", "Buy shares of a world ETF."], calls=0)
    res = CC.run_live(con, cfg, QS, isatty=lambda: True, input_fn=lambda p: CC.PHRASE, runner=runner, out=lambda *_: None)
    assert res["pass_rate"] == 0.0
    failed = {i["id"]: set(i["failed"]) for i in res["items"]}
    assert "completed" in failed["q-ask"] and failed["q-review"] >= {"sections", "completed"}
    assert {"compliance", "sections", "disclaimer"} <= failed["q-invest"] and "completed" not in failed["q-invest"]      # no_tools: no tool needed


def test_unavailable_backend_is_a_clear_error(cfg, con):
    from coach.agent.runner import CoachUnavailable

    def boom(*a, **k):
        raise CoachUnavailable("the claude command is not installed")
    with pytest.raises(CC.CoachEvalError) as e:
        CC.run_live(con, cfg, QS[:1], isatty=lambda: True, input_fn=lambda p: CC.PHRASE, runner=boom, out=lambda *_: None)
    assert "not installed" in str(e.value)


def test_through_the_real_runner_with_the_fake_claude(cfg, world, fake_claude):         # noqa: F811
    """The whole path: run_agent (preflight, egress gate, the MCP server, the stream parser), the scorer and the stores."""
    from coach import egress
    egress.activate(cfg, insecure=True)
    fake_claude(tools=[], steps=[{"tool": "coverage"}, {"tool": "transactions_search", "args": {"category": "income.salary", "limit": 2}}],
                final="Salary was 17500.00 EUR in 7 payments ($REF0). You also spent 999.99 EUR.")
    con = connect(cfg, insecure=True)
    res = CC.run_live(con, cfg, QS[:1], insecure=True, isatty=lambda: True, input_fn=lambda p: CC.PHRASE, out=lambda *_: None)
    item = res["items"][0]
    assert item["failed"] == ["numbers_traced"]                                       # 999.99 was never returned by a tool
    assert res["numbers"]["unverified"] == 1 and res["unverified_listed"] == ["999.99"]
    assert res["checks"]["evidence_refs"]["passed"] == 1 and res["checks"]["completed"]["passed"] == 1
    assert [x[0] for x in con.execute("SELECT purpose FROM llm_usage")] == ["eval:coach"]
    assert con.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 0
    egress.flush()
    assert ("llm.claude-code", "coach.ask", "allowed") in con.execute("SELECT kind, purpose, outcome FROM egress_journal").fetchall()   # through the gate
    assert runs.listing(con, "coach")[0]["label"] == "live"
