"""E12-2: the gold set and the accuracy evaluation. The math is hand-checked on small tables; the pipeline runs on the synthetic household."""
from __future__ import annotations

import json

import pytest

from coach.classify import corrections
from coach.classify import rules as R
from coach.classify.rules import resolve
from coach.cli import main
from coach.quality import classify_eval as CE, gold as G, runs
from helpers import add_tx
from memhelpers import label, make_world

UNC = "other.uncategorized"


def row(gold, pred, amount, source="llm", origin="manual", label_source="user", **kw):
    return {"tx_key": kw.get("tx_key", f"{gold}{pred}{amount}"), "gold": gold, "pred": pred, "amount": amount, "source": source, "origin": origin,
            "labeled_by": "user", "label_source": label_source, **kw}


# ---------------------------------------------------------------- the math, checked by hand

TABLE = [
    row("a.x", "a.x", -100.0, "llm"),            # right
    row("a.x", "b.y", -50.0, "llm"),             # wrong: a.x predicted as b.y
    row("b.y", "b.y", -25.0, "rule"),            # right
    row("b.y", "a.x", 25.0, "rule"),             # wrong: b.y predicted as a.x (a refund: the sign does not matter)
    row("c.z", "c.z", -10.0, "llm"),             # right
]


def test_accuracy_by_transaction_and_by_money():
    m = CE.metrics(TABLE)
    assert (m["n"], m["correct"]) == (5, 3)
    assert m["accuracy_tx"] == 0.6                                  # 3 / 5
    assert (m["money"], m["money_correct"]) == ("210.00", "135.00")   # |100|+|50|+|25|+|25|+|10| ; 100 + 25 + 10
    assert m["accuracy_money"] == 0.6429                            # 135 / 210
    assert m["group_accuracy_tx"] == 0.6                            # a.x/b.y are different groups (a vs b), c.z right: same as the leaf here


def test_precision_recall_f1_per_category_and_macro():
    m = CE.metrics(TABLE)
    by = {c["category"]: c for c in m["categories"]}
    # a.x: tp 1 (row 1), fp 1 (row 4: b.y predicted as a.x), fn 1 (row 2) -> P 1/2, R 1/2, F1 = 2*1 / (2*1 + 1 + 1) = 1/2
    assert (by["a.x"]["tp"], by["a.x"]["fp"], by["a.x"]["fn"]) == (1, 1, 1)
    assert (by["a.x"]["precision"], by["a.x"]["recall"], by["a.x"]["f1"]) == (0.5, 0.5, 0.5)
    # b.y: tp 1, fp 1 (row 2), fn 1 (row 4)
    assert (by["b.y"]["precision"], by["b.y"]["recall"], by["b.y"]["f1"]) == (0.5, 0.5, 0.5)
    # c.z: perfect
    assert (by["c.z"]["precision"], by["c.z"]["recall"], by["c.z"]["f1"]) == (1.0, 1.0, 1.0)
    assert m["macro_f1"] == 0.6667                                  # (0.5 + 0.5 + 1) / 3
    assert by["a.x"]["money"] == "150.00" and by["a.x"]["support"] == 2


def test_confusions_are_ordered_by_count_then_money():
    m = CE.metrics(TABLE)
    assert [(c["gold"], c["predicted"], c["n"], c["money"]) for c in m["confusions"]] == [("a.x", "b.y", 1, "50.00"), ("b.y", "a.x", 1, "25.00")]


def test_uncategorized_is_wrong_and_lowers_coverage():
    rows = TABLE + [row("a.x", UNC, -40.0, "none")]
    m = CE.metrics(rows)
    assert m["accuracy_tx"] == 0.5                                  # 3 / 6
    assert m["coverage_tx"] == 0.8333                               # 5 / 6 have a prediction
    assert m["coverage_money"] == round(210 / 250, 4)               # 40 EUR is uncovered
    assert {c["category"]: c for c in m["categories"]}[UNC]["fp"] == 1


def test_by_source_and_llm_only():
    res = CE.evaluate_rows(TABLE)
    llm = res["by_source"]["llm"]
    assert (llm["n"], llm["correct"], llm["accuracy_tx"]) == (3, 2, 0.6667)
    assert (llm["money"], llm["money_correct"], llm["accuracy_money"]) == ("160.00", "110.00", 0.6875)   # 100+50+10 ; 100+10
    rule = res["by_source"]["rule"]
    assert (rule["n"], rule["accuracy_tx"], rule["accuracy_money"]) == (2, 0.5, 0.5)
    assert res["llm_only"]["n"] == 3 and res["llm_only"]["accuracy_tx"] == 0.6667


def test_a_prediction_from_the_layer_the_label_comes_from_is_tautological_and_left_out():
    taut = row("a.x", "a.x", -500.0, "user", label_source="user")
    stale = row("a.x", "b.y", -80.0, "user", label_source="user")
    res = CE.evaluate_rows(TABLE + [taut, stale])
    assert res["headline"]["n"] == 5 and res["headline"]["accuracy_tx"] == 0.6      # nothing of the two tautological rows leaks into the figures
    assert "user" not in res["by_source"]
    assert res["tautological"]["n"] == 2 and res["tautological"]["agree"] == 1 and res["tautological"]["disagree"] == 1
    assert res["tautological"]["by_source"] == {"user": 2}


def test_empty_input_is_not_a_division_by_zero():
    m = CE.metrics([])
    assert m["n"] == 0 and m["accuracy_tx"] is None and m["accuracy_money"] is None and m["macro_f1"] is None
    assert CE.evaluate_rows([])["llm_only"] is None


# ---------------------------------------------------------------- the gold set

@pytest.fixture
def con(cfg):
    c = make_world(cfg)
    yield c
    c.close()


def test_migration_0021_creates_the_tables(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"gold_labels", "eval_runs"} <= names
    cols = {r[1] for r in con.execute("PRAGMA table_info(gold_labels)")}
    assert {"tx_key", "category", "labeled_by", "labeled_at", "note"} <= cols
    with pytest.raises(Exception):
        con.execute("INSERT INTO gold_labels(tx_key, category, labeled_by, labeled_at) VALUES ('x','food.groceries','robot','t')")


def test_bootstrap_marks_each_decision_by_its_origin(cfg, con):
    corrections.set_override(con, "fm0", "food.restaurants", note="a lunch")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES ('fm1', -10, 'food.groceries', NULL), ('fm1', -30, 'food.restaurants', NULL)")
    con.commit()
    r = G.bootstrap(con, cfg)
    assert r["wanted"] == {"merchant_label": 70, "annotation": 13, "override": 1, "split": 1}
    rows = {g["tx_key"]: g for g in G.gold_rows(con)}
    assert rows["fm0"]["origin"] == "override" and rows["fm0"]["labeled_by"] == "correction" and rows["fm0"]["category"] == "food.restaurants"
    assert rows["fm1"]["origin"] == "split" and rows["fm1"]["category"] == "food.restaurants" and "dominant part of 2" in rows["fm1"]["note"]   # the biggest part: 30
    assert rows["reno1"]["origin"] == "annotation" and rows["reno1"]["labeled_by"] == "memory" and rows["reno1"]["category"] == "housing.renovation"
    assert rows["gro00"]["origin"] == "merchant_label" and rows["gro00"]["labeled_by"] == "user"
    assert "fm2" not in rows                                        # an LLM label is NOT the user's truth
    assert G.counts(con)["by_origin"] == {"annotation": 13, "merchant_label": 70, "override": 1, "split": 1}


def test_the_strongest_decision_is_the_origin_and_a_manual_label_is_never_touched(cfg, con):
    corrections.set_override(con, "gro00", "food.restaurants")        # also a user-labelled merchant
    G.set_gold(con, "gro01", "food.cafes_bars", note="by hand")        # given by hand
    G.bootstrap(con, cfg)
    rows = {g["tx_key"]: g for g in G.gold_rows(con)}
    assert rows["gro00"]["origin"] == "override"                     # override outranks the merchant label
    assert rows["gro01"]["origin"] == "manual" and rows["gro01"]["category"] == "food.cafes_bars" and rows["gro01"]["note"] == "by hand"
    r = G.bootstrap(con, cfg)
    assert r["added"] == {} and r["updated"] == {} and r["removed"] == {} and r["manual_kept"] == 1


def test_bootstrap_follows_the_user_decision_and_drops_what_it_no_longer_supports(cfg, con):
    G.bootstrap(con, cfg)
    corrections.correct_merchant(con, "ACME GROCERS", "food.restaurants")                       # the user changed their mind
    r = G.bootstrap(con, cfg)
    assert r["updated"] == {"merchant_label": 70}
    assert {g["category"] for g in G.gold_rows(con) if g["origin"] == "merchant_label"} == {"food.restaurants"}
    con.execute("UPDATE merchants SET source='llm' WHERE merchant_key='ACME GROCERS'")           # the label is no longer the user's
    con.commit()
    assert G.bootstrap(con, cfg)["removed"] == {"merchant_label": 70}
    assert G.counts(con)["by_origin"] == {"annotation": 13}


def test_dry_run_writes_nothing(cfg, con):
    r = G.bootstrap(con, cfg, dry_run=True)
    assert r["added"]["merchant_label"] == 70 and r["dry_run"] is True
    assert G.counts(con)["total"] == 0


def test_set_gold_validates(con):
    with pytest.raises(G.GoldError):
        G.set_gold(con, "gro00", "not.a.category")
    with pytest.raises(G.GoldError):
        G.set_gold(con, "gro00", UNC)
    with pytest.raises(G.GoldError):
        G.set_gold(con, "no-such-tx", "food.groceries")
    G.set_gold(con, "gro00", "food.groceries")
    assert G.remove_gold(con, "gro00") and not G.remove_gold(con, "gro00")


def test_a_gold_label_follows_its_transaction_when_the_key_changes(con):
    from coach.ingest.fingerprint import rekey_tables
    G.set_gold(con, "gro00", "food.groceries")
    rekey_tables(con, "gro00", "gro00-new")
    assert [g["tx_key"] for g in G.gold_rows(con)] == ["gro00-new"]


# ---------------------------------------------------------------- sampling

def test_sample_never_proposes_what_is_gold_or_already_decided_by_the_user(cfg, con):
    G.bootstrap(con, cfg)
    G.set_gold(con, "fm0", "food.groceries")
    items = G.sample(con, cfg, 500, "money", seed=1)
    keys = {i["tx_key"] for i in items}
    assert "fm0" not in keys and not (keys & {g["tx_key"] for g in G.gold_rows(con)})
    assert {i["source"] for i in items} <= set(G.SAMPLE_SOURCES)
    assert not any(k.startswith("gro") for k in keys)               # user-labelled merchant: already decided
    assert all({"tx_key", "date", "amount", "description", "category", "source"} <= set(i) for i in items)


def test_sample_is_deterministic_for_a_seed_and_differs_between_seeds(cfg, con):
    a = [i["tx_key"] for i in G.sample(con, cfg, 20, "money", seed=3)]
    assert a == [i["tx_key"] for i in G.sample(con, cfg, 20, "money", seed=3)]
    assert a != [i["tx_key"] for i in G.sample(con, cfg, 20, "money", seed=4)]
    s = [i["tx_key"] for i in G.sample(con, cfg, 20, "stratified", seed=3)]
    assert s == [i["tx_key"] for i in G.sample(con, cfg, 20, "stratified", seed=3)]


def test_money_strategy_favours_big_amounts_and_stratified_spreads_over_sources(cfg, con):
    for i in range(40):                                              # a pile of tiny payments and a few big ones, all LLM-labelled
        add_tx(con, "fo", f"tiny{i}", "2026-06-01", -1.0, "TINYSHOP", "card")
    add_tx(con, "fo", "huge1", "2026-06-02", -9000.0, "HUGESHOP", "card")
    label(con, "TINYSHOP", "food.cafes_bars")
    label(con, "HUGESHOP", "shopping.electronics")
    con.commit()
    got = [i["tx_key"] for i in G.sample(con, cfg, 3, "money", seed=5)]
    assert "huge1" in got                                            # weight 9000 vs 1: practically certain
    strat = G.sample(con, cfg, 12, "stratified", seed=5)
    assert len({(i["source"], i["category"].split(".")[0]) for i in strat}) >= 3
    with pytest.raises(G.GoldError):
        G.sample(con, cfg, 3, "nonsense")


# ---------------------------------------------------------------- the terminal loop

def test_label_loop_accept_fix_skip_quit(cfg, con):
    items = G.sample(con, cfg, 4, "money", seed=2)
    assert len(items) == 4
    answers = iter(["a", "f", "xx-nothing", "restaurants", "food.restaurants", "s", "q"])
    shown = []
    res = G.label_loop(con, cfg, items, input_fn=lambda p: next(answers), out=shown.append)
    assert res == {"accepted": 1, "fixed": 1, "skipped": 1, "left": 1}
    gold = {g["tx_key"]: g for g in G.gold_rows(con)}
    assert gold[items[0]["tx_key"]]["category"] == items[0]["category"] and gold[items[0]["tx_key"]]["origin"] == "manual"
    assert gold[items[1]["tx_key"]]["category"] == "food.restaurants"
    assert items[2]["tx_key"] not in gold and items[3]["tx_key"] not in gold
    text = "\n".join(map(str, shown))
    assert "Decision chain" in text and "current label:" in text and "no such category id" in text      # explain() and the current label are shown


def test_label_loop_writes_the_gold_set_only(cfg, con):
    before = [con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("merchants", "tx_overrides", "tx_splits", "merchant_eval")]
    items = G.sample(con, cfg, 3, "money", seed=2)
    answers = iter(["a", "a", "a"])
    G.label_loop(con, cfg, items, input_fn=lambda p: next(answers), out=lambda *_: None)
    assert [con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("merchants", "tx_overrides", "tx_splits", "merchant_eval")] == before
    assert (cfg.memory_dir / "categorization.yaml").read_text().count("id:") == 2           # memory untouched


def test_the_label_command_needs_a_terminal(cfg, con, monkeypatch, capsys):
    con.close()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as e:
        main(["--insecure", "--config", str(cfg.config_path), "eval", "gold", "label"])
    assert "needs a terminal" in str(e.value)


# ---------------------------------------------------------------- predictions without the user's own decision

def test_resolve_can_leave_the_users_layers_out(con):
    assert resolve(con, "gro00", "card", "ACME GROCERS") == ("food.groceries", "user")
    assert resolve(con, "gro00", "card", "ACME GROCERS", ignore=frozenset({"user"})) == (UNC, "none")     # nothing else knows this merchant
    corrections.set_override(con, "fm0", "food.restaurants")
    assert resolve(con, "fm0", "card", "FRESH MARKET") == ("food.restaurants", "override")
    assert resolve(con, "fm0", "card", "FRESH MARKET", ignore=frozenset({"override"})) == ("food.groceries", "llm")   # what the model said


def test_predictions_are_what_the_automatic_chain_said_without_the_origin_layer(cfg, con):
    corrections.set_override(con, "fm0", "food.restaurants")          # the model said groceries, the user says restaurants
    corrections.set_override(con, "fm1", "food.groceries")            # the model was right
    G.bootstrap(con, cfg)
    rows, missing = CE.predict(con, cfg, G.gold_rows(con))
    by = {r["tx_key"]: r for r in rows}
    assert missing == 0
    assert (by["fm0"]["pred"], by["fm0"]["source"], by["fm0"]["gold"]) == ("food.groceries", "llm", "food.restaurants")
    assert (by["fm1"]["pred"], by["fm1"]["gold"]) == ("food.groceries", "food.groceries")
    assert by["reno1"]["source"] in ("llm", "none") and by["reno1"]["origin"] == "annotation"     # the memory annotation is not applied
    assert by["gro00"]["no_counterfactual"] is True                    # the user label replaced the model's: nothing else would have answered
    assert all(r["source"] != r["label_source"] for r in rows)


def test_the_full_run_scores_stores_and_leaves_tautologies_out(cfg, con):
    corrections.set_override(con, "fm0", "food.restaurants")
    corrections.set_override(con, "fm1", "food.groceries")
    G.set_gold(con, "gro02", "food.groceries")                                      # a manual label on a merchant the user also labelled
    G.set_gold(con, "fm2", "food.cafes_bars")                                       # a manual label against an LLM prediction
    G.bootstrap(con, cfg)
    res = CE.run(con, cfg, label="test")
    assert res["gold"]["tautological"] == 1 and res["tautological"]["by_source"] == {"user": 1}      # gro02: user label vs user prediction
    assert res["gold"]["no_counterfactual"] == 69                                    # the other gro* rows
    h = res["headline"]
    llm = res["by_source"]["llm"]
    assert llm["n"] >= 3 and 0 < llm["accuracy_tx"] < 1
    assert res["llm_only"]["n"] == llm["n"]
    assert res["run_id"] and runs.get(con, res["run_id"])["summary"]["n"] == h["n"]
    assert "WARNING" not in CE.format_report(res)


def test_every_run_is_stored_and_a_fall_in_accuracy_warns(cfg, con):
    G.set_gold(con, "fm0", "food.groceries")                         # hand labels: merchant-level truth, scored against the classifier
    G.set_gold(con, "fm1", "food.groceries")
    first = CE.run(con, cfg)
    assert first["warnings"] == [] and first["changed_since_previous"] is None and first["method"] == "v2"
    G.set_gold(con, "fm0", "food.restaurants")                       # the user now disagrees with the model on more transactions
    G.set_gold(con, "fm1", "food.restaurants")
    second = CE.run(con, cfg)
    assert second["summary"]["accuracy_tx"] < first["summary"]["accuracy_tx"]
    assert second["warnings"] and "classifier accuracy by transaction fell from" in second["warnings"][0]
    assert second["changed_since_previous"] == ["gold"]             # only the gold set changed
    assert [r["id"] for r in runs.listing(con, "classify")] == [second["run_id"], first["run_id"]]
    # a taxonomy / rules / prompt / model change is named
    cfg.llm_model = "other-model"
    third = CE.run(con, cfg)
    assert third["changed_since_previous"] == ["model"]


def test_the_evaluation_is_deterministic(cfg, con):
    corrections.set_override(con, "fm0", "food.restaurants")
    G.bootstrap(con, cfg)
    a, b = CE.run(con, cfg, store=False), CE.run(con, cfg, store=False)
    for k in ("headline", "by_source", "llm_only", "tautological", "fingerprint"):
        assert a[k] == b[k]


def test_cli_classify_bootstrap_sample_and_list(cfg, con, capsys):
    corrections.set_override(con, "fm0", "food.restaurants")
    con.close()
    base = ["--insecure", "--config", str(cfg.config_path), "eval"]
    main(base + ["gold", "bootstrap"])
    assert "annotation" in capsys.readouterr().out
    main(base + ["gold", "list", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["counts"]["total"] == 84 and d["counts"]["by_origin"]["override"] == 1
    main(base + ["gold", "sample", "--n", "5", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 5
    main(base + ["classify", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["gold"]["total"] == 84 and "headline" in out and out["run_id"]
    main(base + ["classify"])
    out_ = capsys.readouterr().out
    assert "CLASSIFIER accuracy on merchant-level truth" in out_ and "PIPELINE with memory" in out_
    main(base + ["runs"])
    assert "classify" in capsys.readouterr().out
    main(base + ["runs", "--show", str(out["run_id"])])
    assert json.loads(capsys.readouterr().out)["kind"] == "classify"
    main(base + ["gold", "remove", "fm0"])
    assert "removed" in capsys.readouterr().out


def test_classify_with_an_empty_gold_set_says_what_to_do(cfg, con, capsys):
    con.close()
    main(["--insecure", "--config", str(cfg.config_path), "eval", "classify", "--no-store"])
    assert "nothing to evaluate" in capsys.readouterr().out


def test_a_labelling_run_rescored_the_gold_set_quietly_when_there_is_none_and_loudly_when_there_is(cfg, con, capsys):
    CE.after_label_run(con, cfg)
    assert capsys.readouterr().out == ""                             # no gold set: not a word
    corrections.set_override(con, "fm0", "food.restaurants")
    G.bootstrap(con, cfg)
    CE.after_label_run(con, cfg)
    assert "gold-set check" in capsys.readouterr().out


# ---------------------------------------------------------------- method v2: two metrics, never mixed

def write_annotations(cfg, text):
    (cfg.memory_dir / "categorization.yaml").write_text("annotations:\n" + text)


def test_a_category_in_annotation_never_counts_against_the_classifier_but_a_context_free_one_does(cfg, con):
    # FRESH MARKET: the model says groceries (a card payment). One annotation re-categorises Saturdays only (context: category_in), another
    # one says STREAMBOX is restaurants without any condition (a merchant-level statement the classifier gets wrong: it says streaming)
    write_annotations(cfg, """  - id: ctx-rule
    match: {merchant_key: '^FRESH MARKET', category_in: [food.groceries, food.restaurants]}
    category: food.cafes_bars
  - id: free-rule
    match: {merchant_key: '^STREAMBOX'}
    category: food.restaurants
""")
    G.bootstrap(con, cfg)
    rows, _ = CE.predict(con, cfg, G.gold_rows(con))
    by = {r["tx_key"]: r for r in rows}
    assert by["fm0"]["truth"] == "context_rule" and by["fm0"]["kind"] == "annotation:category_in" and by["fm0"]["category_in"] == ["food.groceries", "food.restaurants"]
    assert by["stream00"]["truth"] == "merchant_truth" and by["stream00"]["kind"] == "annotation:context_free"
    res = CE.evaluate_all(rows)
    cl = res["classifier"]
    assert not any(r["tx_key"].startswith("fm") for r in rows if r["truth"] == "merchant_truth")        # the context rows are not in the classifier set
    streams = [r for r in rows if r["tx_key"].startswith("stream")]
    assert len(streams) == 12 and cl["by_kind"]["annotation:context_free"]["n"] == 12 and cl["by_kind"]["annotation:context_free"]["accuracy_tx"] == 0.0
    assert "annotation:category_in" not in cl["by_kind"]                                              # never counted against the classifier
    bc = res["base_category"]
    assert (bc["n"], bc["agree"], bc["ratio"]) == (5, 5, 1.0)                                         # the model's groceries IS inside category_in
    # the pipeline (memory on) gets all of them right: the annotations are what the user sees
    assert res["pipeline"]["headline"]["accuracy_tx"] == 1.0 and res["pipeline"]["failed"] == 0


def test_the_pipeline_metric_reports_a_broken_or_shadowed_annotation(cfg, con):
    write_annotations(cfg, """  - id: first
    match: {merchant_key: '^STREAMBOX'}
    category: food.restaurants
  - id: second
    match: {merchant_key: '^STREAMBOX'}
    category: food.cafes_bars
""")
    G.bootstrap(con, cfg)                                           # the gold set records the first match (what the pipeline shows)
    write_annotations(cfg, """  - id: second
    match: {merchant_key: '^STREAMBOX'}
    category: food.cafes_bars
""")                                                                 # ... then the first annotation disappears: the pipeline now shows another category
    res = CE.run(con, cfg, store=False)
    p = res["pipeline"]
    assert p["failed"] == 12 and p["headline"]["accuracy_tx"] < 1.0 and p["failures"][0]["gold"] == "food.restaurants" and p["failures"][0]["final"] == "food.cafes_bars"
    assert p["failures_by_kind"] == {"annotation:unknown": 12}               # its annotation no longer exists: kind unknown
    assert any("pipeline-with-memory" in w for w in CE.regressions({"method": "v2", "pipeline_accuracy_tx": 0.5}, {"method": "v2", "pipeline_accuracy_tx": 1.0}))


def test_the_two_metrics_are_reported_apart_with_clear_labels(cfg, con):
    G.set_gold(con, "fm0", "food.groceries")
    res = CE.run(con, cfg, store=False)
    text = CE.format_report(res)
    assert text.index("CLASSIFIER accuracy on merchant-level truth (memory off)") < text.index("PIPELINE with memory (what you see)")
    assert set(res["summary"]) >= {"accuracy_tx", "pipeline_accuracy_tx", "equiv_accuracy_tx", "base_category_ratio", "method"} and res["summary"]["method"] == "v2"


def test_equivalent_categories_count_at_the_equivalent_level_only():
    eq = CE.load_equivalence()
    assert frozenset(("housing.mortgage", "debt.loan_repayment")) in eq and frozenset(("housing.rental_property_loan", "debt.loan_repayment")) in eq
    assert frozenset(("housing.mortgage", "housing.rental_property_loan")) not in eq               # not transitive
    rows = [row("housing.mortgage", "debt.loan_repayment", -1000.0), row("housing.mortgage", "housing.mortgage", -500.0),
            row("housing.mortgage", "food.groceries", -100.0)]
    m = CE.metrics(rows, eq)
    assert m["accuracy_tx"] == 0.3333 and m["equiv_accuracy_tx"] == 0.6667                       # strict leaf stays strict
    assert m["accuracy_money"] == round(500 / 1600, 4) and m["equiv_accuracy_money"] == round(1500 / 1600, 4)
    assert CE.metrics(rows)["equiv_accuracy_tx"] == 0.3333                                          # without the map: the same as leaf


def test_a_user_copy_of_the_equivalence_map_replaces_the_packaged_one(cfg, tmp_path, monkeypatch):
    d = tmp_path / "ucfg"
    d.mkdir()
    (d / "taxonomy_equivalence.yaml").write_text("equivalent:\n  - [food.groceries, food.restaurants]\n")
    monkeypatch.setenv("COACH_CONFIG_DIR", str(d))
    assert CE.load_equivalence(cfg) == frozenset({frozenset(("food.groceries", "food.restaurants"))})


def test_old_runs_are_marked_method_v1(con):
    rid = runs.record(con, "classify", summary={"accuracy_tx": 0.9}, result={})
    assert runs.get(con, rid)["summary"]["method"] == "method_v1" and runs.listing(con, "classify")[0]["summary"]["method"] == "method_v1"
    assert CE.regressions({"method": "v2", "accuracy_tx": 0.1}, runs.get(con, rid)["summary"]) == []      # not comparable with v2


def test_a_loan_instalment_is_the_mortgage_when_the_liability_says_so(cfg, con):
    from helpers import add_tx
    add_tx(con, "ce", "loanx1", "2026-05-03", -900.0, "HOMEBANK ECH PRET 123", "loan_payment")
    add_tx(con, "ce", "loanx2", "2026-05-04", -300.0, "CARCREDIT ECH PRET 9", "loan_payment")
    con.commit()
    got = {r["tx_key"]: r for r in R.categorised(con, memory_dir=cfg.memory_dir, only_tx_keys={"loanx1", "loanx2"})}
    assert got["loanx1"]["category"] == "housing.mortgage" and got["loanx1"]["source"] == "type_rule"      # liability home-loan: kind mortgage, payment_match ^HOMEBANK
    assert got["loanx2"]["category"] == "debt.loan_repayment"                                              # no liability: still unknown
    con.execute("UPDATE accounts SET purpose='rental' WHERE uid='ce'")
    con.commit()
    got = {r["tx_key"]: r for r in R.categorised(con, memory_dir=cfg.memory_dir, only_tx_keys={"loanx1"})}
    assert got["loanx1"]["category"] == "housing.rental_property_loan"                                    # a rental account keeps its own category
