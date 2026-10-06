"""E12-3: comparing models on the gold set. Everything is a fake: no model, no subprocess, no network. A shadow run must never write a label."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from coach import egress
from coach.classify import backends as B
from coach.classify.backends import Usage
from coach.classify.llm import prepare_jobs
from coach.cli import main
from coach.quality import gold as G, models as M, runs
from memhelpers import label, make_world

TABLES = ("merchants", "tx_overrides", "tx_splits", "merchant_eval", "merchant_entities", "merchant_aliases", "tx_enriched")


class FakeBackend:
    """Answers every item from a script; records what it was sent. Same contract as coach.classify.backends.LLMBackend."""
    name = "claude-code"

    def __init__(self, answers, cost=0.01, seconds=1.5):
        self.answers, self.cost, self.seconds, self.calls = answers, cost, seconds, []

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        payload = json.loads(dynamic.split("Items (direction=out means money spent; avg_amount in EUR):\n", 1)[1])
        self.calls.append({"static": static, "dynamic": dynamic, "model": model, "purpose": purpose, "keys": [i["key"] for i in payload]})
        res = [{"id": i["id"], "merchant": i["key"].title(), "category": self.answers.get(i["key"], "other.uncategorized"), "confidence": 0.9,
                "recurring_hint": False} for i in payload]
        return {"results": res}, Usage(self.name, model, purpose, items, 1000, 200, 0, 0, self.cost, True, self.seconds)


@pytest.fixture
def con(cfg):
    c = make_world(cfg)
    label(c, "OTHER SHOP", "shopping.clothing", 1.0, "user")           # a user label of a merchant that is NOT in the gold set
    from helpers import add_tx
    add_tx(c, "fo", "os1", "2026-02-02", -30.0, "OTHER SHOP", "card")
    for k, cat in (("fm0", "food.groceries"), ("fm1", "food.groceries"), ("fm2", "food.groceries"), ("fm3", "food.cafes_bars"),
                   ("fm4", "food.cafes_bars"), ("stream00", "subscriptions.video_streaming"), ("stream01", "subscriptions.video_streaming"),
                   ("stream02", "subscriptions.video_streaming"), ("power00", "housing.energy"), ("power01", "housing.energy"),
                   ("gro00", "food.groceries"), ("pers0", "transfer.to_people")):
        G.set_gold(c, k, cat)
    c.commit()
    yield c
    c.close()


ANSWERS = {"FRESH MARKET": "food.groceries", "STREAMBOX": "subscriptions.video_streaming", "SUNPOWER ENERGIE": "housing.energy",
           "ACME GROCERS": "food.groceries"}


def snapshot(con, cfg):
    return ([con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in TABLES],
            {p.name: p.read_bytes() for p in cfg.memory_dir.rglob("*") if p.is_file() and ".history.git" not in p.parts})


# ---------------------------------------------------------------- what is asked

def test_gold_merchants_follow_the_rules_of_a_real_run(cfg, con):
    gm = M.gold_merchants(con, cfg)
    assert gm["keys"] == ["ACME GROCERS", "FRESH MARKET", "STREAMBOX", "SUNPOWER ENERGIE"]
    assert gm["excluded"] == {"withheld_person_like": 1}              # LUCA BIANCHI: a person is never sent
    assert sorted(len(v) for v in gm["gold_tx"].values()) == [1, 2, 3, 5]


def test_a_merchant_decided_by_a_type_rule_is_not_asked(cfg, con):
    from helpers import add_tx
    add_tx(con, "fo", "atm1", "2026-04-04", -50.0, "RET DAB 1 TESTBANK", "atm")
    G.set_gold(con, "atm1", "cash.atm_withdrawal")
    gm = M.gold_merchants(con, cfg)
    assert gm["excluded"]["decided_by_a_type_rule"] == 1              # the real run never sends it to a model either


def test_sample_limits_the_merchants_deterministically(cfg, con):
    a = M.gold_merchants(con, cfg, sample=2, seed=1)["keys"]
    assert len(a) == 2 and a == M.gold_merchants(con, cfg, sample=2, seed=1)["keys"]
    assert M.gold_merchants(con, cfg, sample=0)["keys_all"] == 4


def test_model_specs(cfg):
    assert M.parse_spec("haiku", cfg) == ("claude-code", "haiku")
    assert M.parse_spec("ollama:llama3.1", cfg) == ("ollama", "llama3.1")
    cfg.llm_backend = "anthropic-api"
    assert M.parse_spec("sonnet", cfg) == ("anthropic-api", "sonnet")
    cfg.llm_backend = "ollama"
    assert M.parse_spec("haiku", cfg) == ("claude-code", "haiku")     # a cloud alias is never silently sent to the local server
    for bad in ("", "ollama:"):
        with pytest.raises(M.ModelsError):
            M.parse_spec(bad, cfg)


# ---------------------------------------------------------------- the plan and the dry run: exact sizes, no call

def test_the_plan_sizes_are_the_exact_size_of_the_requests_a_real_run_sends(cfg, con):
    p = M.plan(con, cfg, ["sonnet"], batch=2)
    m = p["models"][0]
    from coach.classify.candidates import household_names
    jobs = prepare_jobs(con, p["keys"], "sonnet", 2, None, 0, household_names(con)[0], cfg.llm_allowlist, frozenset(p["keys"]))
    assert m["requests"] == len(jobs) == 2
    assert m["payload_bytes"] == sum(len((j["static"] + j["dynamic"]).encode("utf-8")) for _, j in jobs)
    assert m["est_tokens_out"] == 55 * 4 and m["est_cost_usd"] == B.estimate_cost("claude-sonnet-5-5", m["est_tokens_in"], m["est_tokens_out"])
    assert "notional" in m["cost_basis"] and m["egress_allowed"] is True and m["egress_kind"] == "llm.claude-code"


def test_the_gold_merchants_are_never_shown_to_the_model_as_the_answer(cfg, con):
    p = M.plan(con, cfg, ["haiku"])
    static = p["models"][0]["jobs"][0][1]["static"]
    dynamic = p["models"][0]["jobs"][0][1]["dynamic"]
    examples = dynamic.split("Items (direction=out")[0]
    assert "OTHER SHOP" in examples                                    # a user label of a merchant outside the gold set stays a convention
    assert "ACME GROCERS" not in examples and "ACME GROCERS" not in static            # the user-labelled GOLD merchant is held out of the examples ...
    assert "similar" not in dynamic                                    # ... and no nearest-neighbour hint hands it the answer


def test_the_payload_is_redacted_like_a_real_run(cfg, con):
    from helpers import add_tx
    add_tx(con, "fo", "iban1", "2026-05-01", -9.0, "FACTURE FR7630006000011234567890189 XYZSHOP", "card")
    G.set_gold(con, "iban1", "shopping.clothing")
    con.commit()
    p = M.plan(con, cfg, ["haiku"])
    flat = json.dumps(p["models"][0]["jobs"][0][1]["dynamic"])
    assert "FR76300060" not in flat and "PERSON" not in flat and "BIANCHI" not in flat    # the IBAN is masked, the person is held back


def test_prices_unknown_models_and_ollama(cfg, con):
    p = M.plan(con, cfg, ["mystery-model", "ollama:llama3.1"])
    assert p["models"][0]["est_cost_usd"] is None                      # unknown price: never shown as 0
    assert p["models"][1]["est_cost_usd"] == 0.0 and p["models"][1]["cost_basis"] == "local, free"
    text = M.format_plan(p)
    assert "unknown price" in text and "local, free" in text and "REFUSED" not in text


def test_dry_run_calls_no_model_and_writes_nothing(cfg, con, monkeypatch):
    before = snapshot(con, cfg)
    usage_before = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    monkeypatch.setattr(B, "get_backend", lambda *a, **k: pytest.fail("a backend was built on a dry run"))
    out = []
    r = M.run(con, cfg, ["haiku", "sonnet"], dry_run=True, out=out.append, backend_factory=lambda *a: pytest.fail("a backend was built"),
              isatty=lambda: pytest.fail("a dry run needs no terminal"))
    assert r["status"] == "dry-run" and len(r["plan"]["models"]) == 2
    assert snapshot(con, cfg) == before and con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == usage_before
    assert con.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 0
    assert "DRY RUN" in out[-1] and "bytes of redacted payload" in "\n".join(out)


def test_dry_run_can_show_the_exact_payload(cfg, con):
    out = []
    M.run(con, cfg, ["haiku"], dry_run=True, show_payload=True, out=out.append)
    assert "FRESH MARKET" in "\n".join(out) and "Items (direction=out" in "\n".join(out)


# ---------------------------------------------------------------- the real run: a terminal, a typed phrase, the egress gate

def test_a_real_run_needs_a_terminal(cfg, con):
    fb = FakeBackend(ANSWERS)
    with pytest.raises(M.ModelsError) as e:
        M.run(con, cfg, ["haiku"], isatty=lambda: False, input_fn=lambda p: pytest.fail("asked"), backend_factory=lambda *a: fb, out=lambda *_: None)
    assert "needs a terminal" in str(e.value) and fb.calls == []


def test_a_wrong_confirmation_sends_nothing(cfg, con):
    fb = FakeBackend(ANSWERS)
    r = M.run(con, cfg, ["haiku"], isatty=lambda: True, input_fn=lambda p: "yes", backend_factory=lambda *a: fb, out=lambda *_: None)
    assert r["status"] == "aborted" and fb.calls == [] and con.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 0


def test_the_command_cannot_run_without_a_terminal(cfg, con, capsys):
    con.close()
    with pytest.raises(SystemExit) as e:
        main(["--insecure", "--config", str(cfg.config_path), "eval", "models", "--models", "haiku"])
    assert "needs a terminal" in str(e.value)


def test_the_shadow_run_scores_against_the_gold_and_writes_no_label(cfg, con):
    before = snapshot(con, cfg)
    fb = FakeBackend(ANSWERS)
    out = []
    r = M.run(con, cfg, ["haiku"], isatty=lambda: True, input_fn=lambda p: M.PHRASE, backend_factory=lambda *a: fb, batch=2, out=out.append)
    assert r["status"] == "done"
    s = r["results"][0]
    # 11 gold transactions of 4 merchants: FRESH MARKET 3 of 5 right (3 groceries, 2 cafes), STREAMBOX 3/3, SUNPOWER 2/2, ACME 1/1 -> 9/11
    assert s["n"] == 11 and s["accuracy_tx"] == 0.8182 and s["coverage_tx"] == 1.0
    # by money: 5 x 40 + 3 x 12.99 + 85 + 82 + 25 = 430.97 ; right: 3 x 40 + 38.97 + 167 + 25 = 350.97
    run = runs.get(con, s["run_id"])
    assert run["result"]["metrics"]["money"] == "430.97" and run["result"]["metrics"]["money_correct"] == "350.97"
    assert s["accuracy_money"] == 0.8144
    assert s["requests"] == 2 and s["cost_usd"] == 0.02 and s["tokens_in"] == 2000 and s["avg_request_s"] == 1.5
    assert s["baseline_accuracy_tx"] is not None and "classifier today" in "\n".join(out)
    # the shadow run changed NOTHING but the usage log and the run table
    assert snapshot(con, cfg) == before
    assert {c["purpose"] for c in fb.calls} == {"eval"} and sorted(k for c in fb.calls for k in c["keys"]) == sorted(M.gold_merchants(con, cfg)["keys"])
    assert con.execute("SELECT COUNT(*) FROM llm_usage WHERE purpose='eval'").fetchone()[0] == 2
    assert run["kind"] == "models" and run["label"] == "haiku" and run["cost_usd"] == 0.02 and run["summary"]["latency_s"] >= 0


def test_several_models_are_compared_side_by_side_and_each_run_is_stored(cfg, con):
    backends = {"haiku": FakeBackend({**ANSWERS, "FRESH MARKET": "food.cafes_bars"}, cost=0.002, seconds=0.5), "sonnet": FakeBackend(ANSWERS, cost=0.02, seconds=2.0)}
    out = []
    r = M.run(con, cfg, ["haiku", "sonnet"], isatty=lambda: True, input_fn=lambda p: M.PHRASE, backend_factory=lambda b, m: backends[m], out=out.append)
    by = {x["spec"]: x for x in r["results"]}
    assert by["haiku"]["accuracy_tx"] == 0.7273 and by["sonnet"]["accuracy_tx"] == 0.8182     # haiku: FRESH MARKET 2 of 5 right, 3 + 2 + 1 elsewhere = 8 of 11
    assert by["haiku"]["cost_usd"] < by["sonnet"]["cost_usd"] and by["haiku"]["avg_request_s"] < by["sonnet"]["avg_request_s"]
    assert [x["label"] for x in runs.listing(con, "models")] == ["sonnet", "haiku"]
    text = "\n".join(out)
    assert "haiku" in text and "sonnet" in text and "latency" in text


def test_a_failed_batch_is_reported_not_counted_as_wrong(cfg, con):
    class Flaky(FakeBackend):
        def complete(self, *a, **k):
            if len(self.calls) == 0:
                self.calls.append({"keys": []})
                raise RuntimeError("boom")
            return super().complete(*a, **k)
    r = M.run(con, cfg, ["haiku"], isatty=lambda: True, input_fn=lambda p: M.PHRASE, backend_factory=lambda *a: Flaky(ANSWERS), batch=2,
              workers=1, out=lambda *_: None)
    s = r["results"][0]
    assert s["failed"] and s["coverage_tx"] < 1.0 and s["accuracy_tx"] == 1.0         # what was answered was right; the rest is not scored as wrong
    assert runs.get(con, s["run_id"])["result"]["metrics"]["missing_merchants"] == 2


def test_the_egress_gate_refuses_a_cloud_model_in_local_only_and_nothing_is_sent(cfg, con):
    cfg.privacy_local_only = True
    egress.activate(cfg, insecure=True)
    fb = FakeBackend(ANSWERS)
    with pytest.raises(M.ModelsError) as e:
        M.run(con, cfg, ["haiku"], isatty=lambda: True, input_fn=lambda p: M.PHRASE, backend_factory=lambda *a: fb, out=lambda *_: None)
    assert "privacy policy refuses" in str(e.value) and "local_only" in str(e.value) and fb.calls == []
    p = M.plan(con, cfg, ["ollama:llama3.1"])
    assert p["models"][0]["egress_allowed"] is True                      # a local model on this machine is allowed


def test_the_real_claude_backend_goes_through_the_egress_journal(cfg, con, monkeypatch):
    egress.activate(cfg, insecure=True)
    captured = []

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured.append(cmd)
        items = json.loads(input.split("Items (direction=out means money spent; avg_amount in EUR):\n", 1)[1])
        res = [{"id": i["id"], "merchant": "x", "category": ANSWERS.get(i["key"], "other.uncategorized"), "confidence": 0.9, "recurring_hint": False} for i in items]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": {"results": res}, "total_cost_usd": 0.01, "usage": {"input_tokens": 5, "output_tokens": 7}}), stderr="")
    monkeypatch.setattr("coach.classify.backends.subprocess.run", fake_run)
    r = M.run(con, cfg, ["haiku"], isatty=lambda: True, input_fn=lambda p: M.PHRASE, out=lambda *_: None)
    assert r["results"][0]["accuracy_tx"] == 0.8182 and captured and captured[0][:2] == ["claude", "-p"]
    egress.flush()
    rows = con.execute("SELECT kind, purpose, outcome FROM egress_journal").fetchall()
    assert ("llm.claude-code", "eval.models", "allowed") in rows


def test_the_cli_dry_run_and_the_runs_listing(cfg, con, capsys):
    con.close()
    base = ["--insecure", "--config", str(cfg.config_path), "eval"]
    main(base + ["models", "--models", "haiku,ollama:llama3.1", "--dry-run"])
    out = capsys.readouterr().out
    assert "gold merchant(s) to re-label" in out and "DRY RUN" in out and "local, free" in out
    main(base + ["models", "--models", "haiku", "--dry-run", "--json"])
    assert '"status": "dry-run"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(base + ["models", "--models", " , "])


def test_only_merchant_level_truth_is_used(cfg, con):
    from coach.classify import corrections
    corrections.set_override(con, "os1", "food.restaurants")                 # a per-transaction override: context, a model cannot know it
    G.bootstrap(con, cfg)
    gm = M.gold_merchants(con, cfg)
    assert "OTHER SHOP" not in gm["keys"]                                     # (its merchant label is the user's too, but this row is the override)
    assert all(g in ("food.groceries", "food.cafes_bars", "subscriptions.video_streaming", "housing.energy") for txs in gm["gold_tx"].values() for g, _ in txs)
