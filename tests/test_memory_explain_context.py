"""E3-7 `coach explain` and the privacy-aware memory context for the coach."""
import json
import re

import pytest

from coach.classify.rules import categorised, load_rules
from coach.cli import main
from coach.memory import context as Ctx, explain as E
from coach.memory.store import MemoryStore
from helpers import add_tx
from memhelpers import TODAY, label, make_memory, make_world


@pytest.fixture
def world(cfg):
    con = make_world(cfg)
    return con, MemoryStore(cfg.memory_dir)


def steps(x):
    return {s["step"]: s for s in x["steps"]}


def decided(x):
    return [s["step"] for s in x["steps"] if s.get("decides")]


# ---------------------------------------------------------------- explain

def test_memory_annotated_transaction(world):
    con, store = world
    x = E.explain(con, store, "reno1")
    assert x["final"]["category"] == "housing.renovation" and x["final"]["source"] == "memory"
    assert x["final"]["tags"] == ["capital", "one_off"] and x["final"]["event"] == "kitchen-2026"
    assert x["final"]["before_memory"] == {"category": "other.uncategorized", "source": "none"}
    ann = {a["id"]: a for a in x["memory"]["annotations"]}
    assert ann["kitchen-works"]["matched"] and ann["kitchen-works"]["winner"] and ann["kitchen-works"]["line"] == 3
    assert not ann["streambox-sub"]["matched"] and "merchant_key" in ann["streambox-sub"]["reason"]
    assert "categorization.yaml line 3" in x["edit"] and "kitchen-works" in x["edit"]
    assert x["consistent"]


def test_llm_labelled_transaction_shows_model_confidence_date(world):
    con, store = world
    x = E.explain(con, store, "power00")
    d = decided(x)
    assert d == ["LLM label"] and x["final"]["category"] == "housing.energy" and x["final"]["source"] == "llm"
    s = steps(x)["LLM label"]
    assert "confidence 0.90" in s["detail"] and "model sonnet" in s["detail"]
    assert x["parsed"]["tx_type"] == "direct_debit" and x["parsed"]["merchant_key"] == "SUNPOWER ENERGIE"
    assert "coach classify correct" in x["edit"]


def test_transfer_link_wins_over_everything_below(world):
    con, store = world
    x = E.explain(con, store, "tr_out")
    assert decided(x) == ["transfer link"] and x["final"]["category"] == "transfer.internal"
    assert "tr_in" in steps(x)["transfer link"]["detail"] and "coach transfers unlink" in x["edit"]


def test_user_label_outranks_a_rule_and_the_llm(world):
    con, store = world
    x = E.explain(con, store, "gro00")
    assert decided(x) == ["user merchant label"] and x["final"]["source"] == "user"
    assert "coach classify correct" in x["edit"]


def test_override_decides_first_and_split_is_reported(world):
    con, store = world
    con.execute("INSERT INTO tx_overrides VALUES ('stream00','leisure.hobbies','my fix')")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES ('stream01', -10, 'food.groceries', NULL),"
                "('stream01', -2.99, 'leisure.hobbies', NULL)")
    con.commit()
    x = E.explain(con, store, "stream00")
    assert decided(x) == ["override"]
    assert x["memory"]["winner"] == "streambox-sub"           # memory annotations are applied ON TOP of an override
    assert x["final"]["category"] == "subscriptions.video_streaming" and x["final"]["source"] == "memory"
    assert x["final"]["before_memory"] == {"category": "leisure.hobbies", "source": "override"}
    y = E.explain(con, store, "stream01")
    assert y["split"] and y["final"]["source"] == "split" and "coach split stream01 --clear" in y["edit"]


def test_type_rule_rule_and_entity_steps(world):
    con, store = world
    add_tx(con, "fo", "atm1", "2026-06-01", -50.0, "RET DAB BANQUE", "atm")
    add_tx(con, "fo", "rule1", "2026-06-02", -10.0, "NETFLIX COM", "card")
    con.commit()
    x = E.explain(con, store, "atm1")
    assert decided(x) == ["type rule"] and x["final"]["category"] == "cash.atm_withdrawal"
    assert "type_rules.atm" in steps(x)["type rule"]["edit"]
    y = E.explain(con, store, "rule1")
    assert decided(y) == ["rule"] and y["final"]["category"].startswith("subscriptions.")
    assert "\\bNETFLIX\\b" in steps(y)["rule"]["detail"] and "merchant_rules" in y["edit"]
    con.execute("INSERT INTO merchant_entities(name, norm_name, category, source, created_at, category_user) "
                "VALUES ('Chain', 'chain', 'shopping.clothing', 'auto', 't', 1)")
    eid = con.execute("SELECT id FROM merchant_entities").fetchone()[0]
    con.execute("INSERT INTO merchant_aliases VALUES ('FRESH MARKET', ?, 'auto', 't')", (eid,))
    con.commit()
    z = E.explain(con, store, "fm0")
    assert decided(z) == ["entity"] and z["final"]["category"] == "shopping.clothing"
    assert [s["step"] for s in z["steps"] if s["applies"]] == ["entity", "LLM label"]       # applies but outranked


def test_every_transaction_chain_agrees_with_the_classifier(world):
    """Property: for ALL transactions the explained final category/source/tags/event equal categorised()."""
    con, store = world
    con.execute("INSERT INTO tx_overrides VALUES ('stream03','leisure.hobbies','x')")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category, note) VALUES ('fm1', -10, 'food.groceries', NULL),"
                "('fm1', -30, 'leisure.hobbies', NULL)")
    con.commit()
    expected = {}
    for t in categorised(con, memory_dir=store.root, use_splits=False):
        expected[t["tx_key"]] = t
    n = 0
    for key, t in expected.items():
        x = E.explain(con, store, key)
        assert x["consistent"], key
        if not x["split"]:
            assert (x["final"]["category"], x["final"]["source"]) == (t["category"], t["source"]), key
        assert set(x["final"]["tags"]) == t["tags"] and x["final"]["event"] == t["event"], key
        n += 1
    assert n > 100


def test_find_transactions_by_key_or_fragment(world):
    con, _ = world
    assert [r[0] for r in E.find_transactions(con, "reno1")] == ["reno1"]
    assert {r[0] for r in E.find_transactions(con, "BRICO")} == {"reno1"}
    assert len(E.find_transactions(con, "SUNPOWER")) == 12
    assert E.find_transactions(con, "no-such-thing") == []
    with pytest.raises(E.ExplainError):
        E.explain(con, MemoryStore("/nonexistent"), "no-such-key")


def test_format_is_readable_and_names_the_file_to_edit(world):
    con, store = world
    text = E.format_explanation(E.explain(con, store, "reno1"))
    for needle in ("Parser output", "Decision chain", "Memory annotations", ">> kitchen-works: MATCHES",
                   "Final: housing.renovation", "To change it:", "line 3"):
        assert needle in text


def run(cfg, *argv):
    main(["--config", str(cfg.config_path), "--insecure", *argv])


def test_cli_explain_json_fragment_and_ambiguity(world, cfg, capsys):
    run(cfg, "explain", "reno1", "--json")
    d = json.loads(capsys.readouterr().out)
    assert d["final"]["category"] == "housing.renovation" and d["transaction"]["tx_key"] == "reno1"
    run(cfg, "explain", "BRICO")
    assert "Final: housing.renovation" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run(cfg, "explain", "SUNPOWER")                          # 12 matches: pick one
    assert "12 transactions match" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        run(cfg, "explain", "zzz-nothing")
    assert "no transaction matches" in str(e.value)


# ---------------------------------------------------------------- context

NAMES = ["Anna", "Luca", "Mia", "Rossi", "ANNA", "ROSSI"]


def test_names_are_replaced_by_member_ids_by_default(world, cfg):
    con, store = world
    ctx = Ctx.build_context(store, con, cfg, today=TODAY)
    md = Ctx.render_markdown(ctx)
    for n in NAMES:
        assert not re.search(rf"\b{n}\b", md, re.I), (n, md)
    assert "[family]" in md and ctx["names_included"] is False
    assert {m["id"] for m in ctx["members"]} == {"anna", "luca", "mia"} or \
        {m["id"] for m in ctx["members"]} == {"adult-1", "adult-2", "kid-1"}


def test_pseudonym_is_used_when_the_member_id_would_reveal_the_name(world, cfg):
    con, store = world
    ids = {m["id"] for m in Ctx.build_context(store, con, cfg, today=TODAY)["members"]}
    assert ids == {"adult-1", "adult-2", "kid-1"}               # ids anna/luca/mia equal the first names


def test_member_ids_that_do_not_reveal_names_are_kept(tmp_path, cfg):
    mem = cfg.memory_dir
    make_world(cfg, household=False)
    (mem / "household.yaml").write_text("members:\n  - id: parent-a\n    name: Anna Rossi\n    role: adult\n"
                                        "  - id: kid-1\n    name: Mia Rossi\n    role: child\n    birth_year: 2012\n")
    store = MemoryStore(mem)
    con = None
    ctx = Ctx.build_context(store, con, cfg, today=TODAY)
    assert {m["id"] for m in ctx["members"]} == {"parent-a", "kid-1"}
    assert "parent-a" in Ctx.render_markdown(ctx) and "Anna" not in Ctx.render_markdown(ctx)
    assert "talk to parent-a" in Ctx.render_markdown(ctx).lower()          # preferences.md: "Talk to Anna" -> id


def test_ibans_and_account_labels_never_appear(world, cfg):
    con, store = world
    md = Ctx.render_markdown(Ctx.build_context(store, con, cfg, today=TODAY))
    assert "FR76" not in md and "[IBAN]" in md                     # profile.md had a (fake) IBAN: redacted
    assert "ROSSI ANNA" not in md.upper()


def test_names_flag_keeps_names_but_still_redacts_ibans(world, cfg):
    con, store = world
    ctx = Ctx.build_context(store, con, cfg, names=True, today=TODAY)
    md = Ctx.render_markdown(ctx)
    assert "Anna Rossi" in md and ctx["names_included"] is True and "FR76" not in md


def test_without_a_household_file_given_names_and_the_family_name_are_still_removed(cfg):
    con = make_world(cfg, household=False)
    store = MemoryStore(cfg.memory_dir)
    md = Ctx.render_markdown(Ctx.build_context(store, con, cfg, today=TODAY))
    assert not re.search(r"\b(anna|luca|rossi)\b", md, re.I) and "[person]" in md and "[family]" in md


def test_max_tokens_trims_least_important_parts_first(world, cfg):
    con, store = world
    ctx = Ctx.build_context(store, con, cfg, today=TODAY)
    full = Ctx.render_markdown(ctx)
    small_ctx, small = Ctx.fit(ctx, Ctx.est_tokens(full) // 2)
    assert Ctx.est_tokens(small) <= Ctx.est_tokens(full) // 2 + 20 and len(small) < len(full)
    assert "## Liabilities" in small                                # structured facts survive, prose goes first
    tiny_ctx, tiny = Ctx.fit(ctx, 60)
    assert len(tiny) <= 60 * 4 + 80 and "truncated" in tiny
    big_ctx, big = Ctx.fit(ctx, 10**6)
    assert big == full


def test_context_has_no_raw_bank_descriptions(world, cfg):
    con, store = world
    ctx = Ctx.build_context(store, con, cfg, today=TODAY)
    blob = json.dumps(ctx, default=str)
    assert "HOMEBANK ECH PRET" not in blob and "VIR TO SAVINGS" not in blob


def test_cli_context_md_json_and_limits(world, cfg, capsys):
    run(cfg, "memory", "context", "--md", "--max-tokens", "400")
    cap = capsys.readouterr()
    assert "# Household memory" in cap.out and "tokens" in cap.err
    run(cfg, "memory", "context", "--json")
    d = json.loads(capsys.readouterr().out)
    assert d["names_included"] is False and "liabilities" in d and "manual_totals" in d
    run(cfg, "memory", "context", "--names")
    assert "Anna Rossi" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run(cfg, "memory", "context", "--md", "--json")
