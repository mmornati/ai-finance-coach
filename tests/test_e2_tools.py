"""E2-3 taxonomy, E2-9 review queue, E2-11 kNN, E2-12 canonical merchants, E2-13 splits."""
import json
import shutil
from argparse import Namespace
from types import SimpleNamespace

import pytest

from coach.analytics.report import build_report, coverage
from coach.classify import commands as cc, entities, knn, rules as rules_mod, splits, taxonomy
from coach.classify.rules import categorised, resolve
from coach.db import connect
from helpers import add_bank, add_tx


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s", "Fortuneo", "FR", [("a", "FR76", "M OU MME DURAND PAUL")])
    add_bank(c, "s2", "Revolut", "LT", [("r", "LT1", "Lea Durand")])
    return c


def tx(con, key, mkey, amount=-10.0, uid="a", date="2026-09-01", ttype="card", desc=None):
    add_tx(con, uid, key, date, amount, desc or f"CARTE 01/09 {mkey}", ttype)
    con.execute("UPDATE tx_enriched SET merchant_raw=?, merchant_key=? WHERE tx_key=?", (mkey, mkey, key))
    con.commit()


def label(con, key, cat, source="llm", conf=0.9, name=None):
    con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,NULL,'t')", (key, name or key.title(), cat, conf, source))
    con.commit()


# ================================================================ E2-3 taxonomy

@pytest.fixture
def tax_files(tmp_path, monkeypatch):
    t, r = tmp_path / "taxonomy.yaml", tmp_path / "rules.yaml"
    shutil.copy(rules_mod.HERE / "taxonomy.yaml", t)
    shutil.copy(rules_mod.HERE / "rules.yaml", r)
    monkeypatch.setenv("COACH_TAXONOMY_FILE", str(t))
    monkeypatch.setenv("COACH_RULES_FILE", str(r))
    rules_mod.reload_taxonomy()
    yield t, r
    monkeypatch.delenv("COACH_TAXONOMY_FILE")
    monkeypatch.delenv("COACH_RULES_FILE")
    rules_mod.reload_taxonomy()


def test_shipped_taxonomy_has_the_new_leaves_and_keeps_old_ids():
    for cid in ("debt.loan_repayment", "housing.rental_property_loan", "housing.property_charges", "income.rental",
                "insurance.life", "insurance.borrower", "insurance.pet", "insurance.other"):
        assert cid in rules_mod.CATEGORIES
    for cid in ("housing.mortgage", "housing.renovation", "debt.bnpl", "other.uncategorized", "transfer.internal",
                "fees.bank_fees", "food.work_meals"):
        assert cid in rules_mod.CATEGORIES


def test_add_leaf_existing_and_new_group(tax_files):
    t, _ = tax_files
    taxonomy.add_leaf("housing.garden", "Garden: plants, tools")
    assert "housing.garden" in rules_mod.CATEGORIES
    text = t.read_text()
    assert text.index("  garden:") < text.index("food:") and text.startswith("# Two-level")      # comments survive
    taxonomy.add_leaf("gifts.birthday", "Birthday: presents")
    assert "gifts.birthday" in rules_mod.CATEGORIES and text.index("other:") < t.read_text().index("gifts:") + 10000
    assert t.read_text().index("gifts:") < t.read_text().index("\nother:")                        # 'other' stays last
    for bad in ("nogroup", "Bad.Id", "housing.garden", "a.b-c"):
        with pytest.raises(taxonomy.TaxonomyError):
            taxonomy.add_leaf(bad, "x")
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.add_leaf("x.y", "  ")


def test_rename_migrates_every_stored_reference_and_reports_memory(tax_files, con, cfg):
    t, r = tax_files
    tx(con, "t1", "SHOP")
    label(con, "SHOP", "shopping.clothing")
    con.execute("INSERT INTO tx_overrides VALUES ('t1','shopping.clothing','n')")
    con.execute("INSERT INTO merchant_eval VALUES ('SHOP','haiku','shopping.clothing',0.8,'t')")
    con.execute("INSERT INTO merchant_entities(name,norm_name,category,source,created_at) VALUES ('S','s','shopping.clothing','auto','t')")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category) VALUES ('t1', -5, 'shopping.clothing')")
    con.commit()
    r.write_text(r.read_text() + "  - match: '\\bZARA\\b'\n    category: shopping.clothing\n")
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: a1\n    match: {merchant_key: X}\n    category: shopping.clothing\n"
        "  - id: a2\n    match: {category_in: [shopping.clothing, food.groceries]}\n    category: food.work_meals\n"
        "  - id: a3\n    match: {merchant_key: Y}\n    category: food.groceries\n")
    (cfg.memory_dir / "profile.md").write_text("we buy shopping.clothing often\n")
    res = taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel", cfg.memory_dir)
    assert "shopping.apparel" in rules_mod.CATEGORIES and "shopping.clothing" not in rules_mod.CATEGORIES
    assert res["db"] == {"merchants": 1, "tx_overrides": 1, "merchant_eval": 1, "merchant_entities": 1, "tx_splits": 1}
    assert res["rules_yaml"] >= 1
    for table, col in taxonomy.DB_REFS:
        assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}='shopping.clothing'").fetchone()[0] == 0
    assert "shopping.clothing" not in r.read_text() and "shopping.apparel" in r.read_text()
    assert "shopping.clothing" not in t.read_text() and "  apparel:" in t.read_text()
    assert rules_mod.load_rules()["merchant_rules"][-1]["category"] == "shopping.apparel"
    mem = "\n".join(res["memory"])
    assert "'a1'" in mem and "'a2'" in mem and "'a3'" not in mem and "profile.md:1" in mem
    # memory/ untouched
    assert "shopping.clothing" in (cfg.memory_dir / "categorization.yaml").read_text()
    # the pipeline resolves the new id
    assert resolve(con, "t1", "card", "SHOP")[0] == "shopping.apparel"


def test_rename_to_another_group_and_refusals(tax_files, con, cfg):
    taxonomy.rename_leaf(con, "pets.pets", "insurance.petcare", cfg.memory_dir)
    assert "insurance.petcare" in rules_mod.CATEGORIES and "pets" not in rules_mod.TAXONOMY
    for old, new in (("transfer.internal", "transfer.own"), ("other.uncategorized", "other.unknown"),
                     ("food.groceries", "food.restaurants"), ("food.groceries", "income.food"),
                     ("nope.nope", "a.b"), ("food.groceries", "BAD")):
        with pytest.raises(taxonomy.TaxonomyError):
            taxonomy.rename_leaf(con, old, new)
    assert "food.groceries" in rules_mod.CATEGORIES


# ================================================================ E2-9 review queue

def test_review_queue_all_banks_by_money_with_banks_json_and_accept(cfg, con, capsys):
    tx(con, "k1", "BIG UNKNOWN", -500.0)
    tx(con, "k2", "SMALL LOW", -20.0, uid="r")
    tx(con, "k3", "MID UNCAT", -100.0, uid="r", ttype="direct_debit")
    tx(con, "k4", "CONFIDENT", -900.0)
    tx(con, "k5", "NETFLIX", -9.99)
    tx(con, "k6", "MINE", -1000.0)
    tx(con, "k7", "ATM", -60.0, ttype="atm")
    label(con, "SMALL LOW", "food.groceries", conf=0.5)
    label(con, "MID UNCAT", "other.uncategorized", conf=0.3)
    label(con, "CONFIDENT", "food.groceries", conf=0.95)
    label(con, "MINE", "food.groceries", source="user", conf=1.0)
    ns = Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=None)
    rows = cc.cmd_review(ns, cfg)
    assert [r["key"] for r in rows] == ["BIG UNKNOWN", "MID UNCAT", "SMALL LOW"]      # by money, all banks
    assert [r["reason"] for r in rows] == ["not_labelled", "uncategorized", "low_confidence"]
    assert rows[0]["banks"] == ["Fortuneo"] and rows[1]["banks"] == ["Revolut"]
    assert rows[0]["at_stake"] == 500.0 and rows[1]["accounts"]
    out = capsys.readouterr().out
    assert "Fortuneo" in out and "Revolut" in out
    ns.json = True
    cc.cmd_review(ns, cfg)
    data = json.loads(capsys.readouterr().out)
    assert data[0]["key"] == "BIG UNKNOWN" and data[2]["confidence"] == 0.5 and isinstance(data[0]["banks"], list)
    # accept an LLM label as the user's
    ns2 = Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=["SMALL LOW"])
    cc.cmd_review(ns2, cfg)
    assert con.execute("SELECT source, confidence, category FROM merchants WHERE merchant_key='SMALL LOW'").fetchone() == (
        "user", 1.0, "food.groceries")
    assert "SMALL LOW" not in [r["key"] for r in cc.review_rows(con, cfg)]
    # nothing to accept / uncategorized cannot be accepted
    for key in ("BIG UNKNOWN", "MID UNCAT"):
        with pytest.raises(SystemExit):
            cc.cmd_review(Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=[key]), cfg)


def test_review_queue_lists_merchants_held_back_from_the_llm(cfg, con):
    tx(con, "p1", "VASSEUR T", -50.0, ttype="transfer_out")
    rows = cc.review_rows(con, cfg)
    assert rows and rows[0]["reason"] == "held_back_person_like"


# ================================================================ E2-11 kNN

LABELLED = [("CARREFOUR MARKET QUIMPER", "Carrefour", "food.groceries"),
            ("PHARMACIE DU CENTRE REDON", "Pharmacie", "health.pharmacy"),
            ("SPORTMAX CARQUEFOU", "Sportmax", "shopping.sports_gear"),
            ("AIR FRANCE PARIS", "Air France", "travel.flights"),
            ("NETFLIX COM DUBLIN", "Netflix", "subscriptions.video_streaming")]


def test_knn_neighbours_and_conservative_auto_label():
    idx = knn.Index(LABELLED)
    near = idx.neighbours("CARREFOUR MARKET DINAN", 3)
    assert near[0][1] == "CARREFOUR MARKET QUIMPER" and near[0][0] > 0.5
    assert len(idx.neighbours("CARREFOUR MARKET DINAN", 5)) <= 5
    assert idx.neighbours("CARREFOUR MARKET QUIMPER", 3, exclude_self=True)[0][1] != "CARREFOUR MARKET QUIMPER"
    # near-identical key: labelled automatically
    hit = knn.auto_label(idx, "CARREFOUR MARKET QUIMPER SUD", 0.8)
    assert hit and hit[0] == "food.groceries" and hit[1] >= 0.8
    # similar but not close enough at the default threshold: left to the LLM
    assert knn.auto_label(idx, "CARREFOUR MARKET DINAN", knn.DEFAULT_THRESHOLD) is None
    # unrelated merchants never match
    assert knn.auto_label(idx, "BOULANGERIE DUPONT", 0.5) is None
    assert idx.neighbours("ZZZZ QQQQ", 3) == []


def test_knn_conflicting_neighbours_block_the_auto_label():
    idx = knn.Index([("SUPER U NORD", "U", "food.groceries"), ("SUPER U SUD", "U", "shopping.department_general")])
    assert knn.auto_label(idx, "SUPER U OUEST", 0.5) is None


def test_knn_corpus_is_user_and_high_confidence_only(con):
    for i, k in enumerate(["ALPHA STORE", "BRAVO STORE", "CHARLIE STORE", "DELTA STORE", "ECHO STORE"]):
        tx(con, f"kc{i}", k)
    label(con, "ALPHA STORE", "food.groceries", "user", 1.0)
    label(con, "BRAVO STORE", "food.groceries", "llm", 0.95)
    label(con, "CHARLIE STORE", "food.groceries", "llm", 0.5)
    label(con, "DELTA STORE", "food.groceries", "knn", 0.99)          # never feeds itself
    label(con, "ECHO STORE", "other.uncategorized", "user", 1.0)
    assert {k for k, _, _ in knn.labelled_corpus(con)} == {"ALPHA STORE", "BRAVO STORE"}


def run_ns(**kw):
    base = dict(insecure=True, model="m", batch=50, workers=1, limit=None, refresh=False, include_ruled=False,
                no_knn=False, knn_threshold=None)
    base.update(kw)
    return Namespace(**base)


def test_cmd_run_labels_near_duplicates_without_llm_and_passes_examples(cfg, con, monkeypatch):
    from coach.classify import backends
    seen = []

    class Rec(backends.LLMBackend):
        name = "rec"

        def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
            seen.append(dynamic)
            payload = json.loads(dynamic.split("avg_amount in EUR):\n")[1])
            return {"results": [{"id": i["id"], "merchant": "M", "category": "food.restaurants", "confidence": 0.9,
                                 "recurring_hint": False} for i in payload]}, backends.Usage("rec", model)
    monkeypatch.setattr(cc, "get_backend", lambda c: Rec())
    label(con, "CARREFOUR MARKET QUIMPER", "food.groceries", "user", 1.0)
    label(con, "TRATTORIA ROMA CENTRO", "food.restaurants", "user", 1.0)
    tx(con, "l1", "CARREFOUR MARKET QUIMPER")
    tx(con, "l2", "TRATTORIA ROMA CENTRO")
    tx(con, "n1", "CARREFOUR MARKET QUIMPER SUD")           # near-duplicate -> kNN
    tx(con, "n2", "TRATTORIA ROMA NORD")                  # similar only -> LLM, with examples
    tx(con, "n3", "ZZZ UNRELATED SHOP")                   # nothing similar -> LLM, no examples
    n = cc.cmd_run(run_ns(knn_threshold=0.75), cfg)
    row = con.execute("SELECT category, source, confidence FROM merchants WHERE merchant_key='CARREFOUR MARKET QUIMPER SUD'").fetchone()
    assert row[0] == "food.groceries" and row[1] == "knn" and 0.75 <= row[2] <= 1
    assert "CARREFOUR MARKET QUIMPER SUD" not in " ".join(seen)         # never sent to the LLM
    sent = json.loads(seen[0].split("avg_amount in EUR):\n")[1])
    by_key = {i["key"]: i for i in sent}
    assert "similar" in by_key["TRATTORIA ROMA NORD"] and by_key["TRATTORIA ROMA NORD"]["similar"][0]["category"] == "food.restaurants"
    assert len(by_key["TRATTORIA ROMA NORD"]["similar"]) <= 5
    assert n == 3
    # a transfer sharing words with a labelled shop is not auto-labelled by similarity (e.g. a salary 'SPORTMAX')
    label(con, "SPORTMAX CARQUEFOU", "shopping.sports_gear", "user", 1.0)
    tx(con, "l3", "SPORTMAX CARQUEFOU")
    tx(con, "n4", "SPORTMAX CARQUEFOU SA", 5000.0, ttype="transfer_in")
    seen.clear()
    cc.cmd_run(run_ns(knn_threshold=0.5), cfg)
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='SPORTMAX CARQUEFOU SA'").fetchone()[0] == "llm"
    assert "similar" not in [i for i in json.loads(seen[-1].split("avg_amount in EUR):\n")[1])
                             if i["key"] == "SPORTMAX CARQUEFOU SA"][0]
    # the knn label does not outrank a user correction, and --no-knn disables it
    con.execute("DELETE FROM merchants WHERE source='knn'")
    con.commit()
    seen.clear()
    cc.cmd_run(run_ns(no_knn=True, knn_threshold=0.75), cfg)
    assert any("CARREFOUR MARKET QUIMPER SUD" in s for s in seen)


# ================================================================ E2-12 canonical merchants

def test_auto_group_exact_normalised_names_only(con):
    label(con, "MC DONALDS QUIMPER", "food.fast_food", name="MacBurger's")
    label(con, "MCDONALDS REDON", "food.fast_food", name="Mac Burger's")
    label(con, "MACDO DINAN", "food.fast_food", name="McDo")
    label(con, "UNIQUE SHOP", "shopping.clothing", name="Unique Shop")
    label(con, "VAGUE ONE", "food.groceries", name="Unknown")
    label(con, "VAGUE TWO", "food.groceries", name="Unknown")
    res = entities.auto_group(con)
    assert res["created"] == 1 and res["joined"] == 2
    ents = entities.list_entities(con)
    assert len(ents) == 1 and set(ents[0]["keys"]) == {"MC DONALDS QUIMPER", "MCDONALDS REDON"}
    assert ents[0]["category"] == "food.fast_food" and ents[0]["source"] == "auto"
    # "McDo" is only similar: it is a proposal, never applied
    assert entities.auto_group(con)["created"] == 0
    assert con.execute("SELECT COUNT(*) FROM merchant_aliases").fetchone()[0] == 2
    # a later variant with the same name joins the existing entity
    label(con, "MCDONALDS MONTFORT", "food.fast_food", name="MacBurgers")
    assert entities.auto_group(con)["joined"] == 1
    assert con.execute("SELECT COUNT(*) FROM merchant_entities").fetchone()[0] == 1


def test_entity_category_is_a_default_under_labels(con):
    label(con, "MCDONALDS A", "food.fast_food", name="McDonalds")
    label(con, "MCDONALDS B", "food.fast_food", name="McDonalds")
    entities.auto_group(con)
    # a new variant without a label of its own inherits the entity category...
    con.execute("INSERT INTO merchant_aliases VALUES ('MCDONALDS C', (SELECT id FROM merchant_entities), 'user', 't')")
    assert resolve(con, "x", "card", "MCDONALDS C")[0:2] == ("food.fast_food", "entity")
    # ...but never overrides a user label or any existing merchant row
    label(con, "MCDONALDS C", "food.restaurants", "user", 1.0)
    assert resolve(con, "x", "card", "MCDONALDS C") == ("food.restaurants", "user")
    label(con, "MCDONALDS D", "shopping.clothing", "llm")
    con.execute("INSERT INTO merchant_aliases VALUES ('MCDONALDS D', (SELECT id FROM merchant_entities), 'user', 't')")
    assert resolve(con, "x", "card", "MCDONALDS D") == ("shopping.clothing", "llm")


def test_merge_split_rename_category_and_reporting(cfg, con):
    for i, (k, n) in enumerate([("CAFE X QUIMPER", "Cafe X"), ("CAFE X PARIS", "Cafe X Paris")]):
        label(con, k, "food.cafes_bars", name=n)
        tx(con, f"c{i}", k, -4.0 - i)
    eid = entities.merge(con, ["CAFE X QUIMPER", "CAFE X PARIS"], "Cafe X")
    assert [e["name"] for e in entities.list_entities(con)] == ["Cafe X"]
    ts = list(categorised(con, memory_dir=cfg.memory_dir))
    assert {t["entity"] for t in ts} == {"Cafe X"}
    rep = build_report(con, memory_dir=cfg.memory_dir)
    assert rep["by_entity"][0][0] == "Cafe X" and rep["by_entity"][0][1] == -9.0 and rep["by_entity"][0][2] == 2
    entities.rename(con, str(eid), "Cafe Ex")
    entities.set_category(con, "Cafe Ex", "food.restaurants")
    assert con.execute("SELECT name, category, source FROM merchant_entities").fetchone() == ("Cafe Ex", "food.restaurants", "user")
    with pytest.raises(entities.EntityError):
        entities.set_category(con, "Cafe Ex", "nope.nope")
    entities.split(con, "CAFE X PARIS")
    assert [e["keys"] for e in entities.list_entities(con)] == [["CAFE X QUIMPER"]]
    entities.split(con, "CAFE X QUIMPER")
    assert entities.list_entities(con) == []                 # an entity without members disappears
    with pytest.raises(entities.EntityError):
        entities.split(con, "CAFE X QUIMPER")
    with pytest.raises(entities.EntityError):
        entities.merge(con, ["NOT A KEY", "NOR THIS"])


# ================================================================ E2-13 splits

def test_split_must_sum_exactly_and_analytics_use_the_parts(cfg, con):
    tx(con, "s1", "SUPERMARKET", -100.0)
    label(con, "SUPERMARKET", "food.groceries")
    with pytest.raises(splits.SplitError, match="add up"):
        splits.set_split(con, "s1", ["food.groceries:70", "housing.furniture:20"])
    with pytest.raises(splits.SplitError, match="unknown category"):
        splits.set_split(con, "s1", ["food.groceries:70", "nope.x:30"])
    with pytest.raises(splits.SplitError, match="at least two"):
        splits.set_split(con, "s1", ["food.groceries:100"])
    with pytest.raises(splits.SplitError, match="opposite sign"):
        splits.set_split(con, "s1", ["food.groceries:+130", "housing.furniture:-30"])
    assert con.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0] == 0          # nothing stored on error
    parts = splits.set_split(con, "s1", ["food.groceries:70", "housing.furniture:rest:new lamp"])
    assert parts == [(-70.0, "food.groceries", None), (-30.0, "housing.furniture", "new lamp")]
    ts = [t for t in categorised(con, memory_dir=cfg.memory_dir) if t["tx_key"] == "s1"]
    assert sorted((t["category"], t["amount"]) for t in ts) == [("food.groceries", -70.0), ("housing.furniture", -30.0)]
    assert all(t["source"] == "split" and t["split_of"] == -100.0 for t in ts)
    assert coverage(ts)["total"] == 1                                               # one transaction
    full = list(categorised(con, memory_dir=cfg.memory_dir, use_splits=False))
    assert [t["amount"] for t in full if t["tx_key"] == "s1"] == [-100.0]
    # replacing a split, and removing it
    splits.set_split(con, "s1", ["food.groceries:60", "housing.furniture:40"])
    assert con.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0] == 2
    assert splits.clear_split(con, "s1") == 2


def test_split_rounding_and_signs_and_refunds(con):
    tx(con, "r1", "SHOP", -100.0)
    parts = splits.set_split(con, "r1", ["food.groceries:33.33", "housing.furniture:33.33", "shopping.clothing:33.34"])
    assert round(sum(p[0] for p in parts), 2) == -100.0
    assert sum(int(round(p[0] * 100)) for p in parts) == -10000
    with pytest.raises(splits.SplitError):
        splits.set_split(con, "r1", ["food.groceries:33.33", "housing.furniture:33.33", "shopping.clothing:33.33"])
    # explicit negative signs are accepted for a spending transaction; rest takes the cents left over
    parts = splits.set_split(con, "r1", ["food.groceries:-0.10", "housing.furniture:rest"])
    assert [p[0] for p in parts] == [-0.1, -99.9]
    tx(con, "r2", "SHOP", 30.0, date="2026-09-02")                                   # a refund
    parts = splits.set_split(con, "r2", ["food.groceries:20", "housing.furniture:10"])
    assert [p[0] for p in parts] == [20.0, 10.0]
    with pytest.raises(splits.SplitError, match="opposite sign"):
        splits.set_split(con, "r2", ["food.groceries:-20", "housing.furniture:50"])
    tx(con, "z", "ZERO", 0.0)
    with pytest.raises(splits.SplitError, match="zero"):
        splits.set_split(con, "z", ["food.groceries:1", "housing.furniture:-1"])


def test_split_follows_a_rekey_and_works_from_the_cli(cfg, con, capsys):
    tx(con, "abcdef123456", "SHOP", -40.0)
    ns = Namespace(insecure=True, tx="abcdef", parts=["food.groceries:25", "housing.furniture:rest"], clear=False)
    splits.cmd_split(ns, cfg)
    assert "housing.furniture" in capsys.readouterr().out
    from coach.ingest.fingerprint import replace_tx_key
    con.execute("INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description) "
                "VALUES ('new-key','a','2026-09-01',-40,'EUR','CARTE 01/09 SHOP')")
    replace_tx_key(con, "abcdef123456", "new-key")
    assert con.execute("SELECT DISTINCT tx_key FROM tx_splits").fetchall() == [("new-key",)]
    with pytest.raises(SystemExit):
        splits.cmd_split(Namespace(insecure=True, tx="nope-nope", parts=["a.b:1", "c.d:2"], clear=False), cfg)
