"""A transaction's "why" for the web's translations (i18n step 4d): the decision chain (`steps[].step_code`, `detail_msg`), the memory
annotations (`reason_msg`) and the attribution rules (`why_not_msg`), while the CLI and the MCP tool keep their English unchanged.

Synthetic household only (tests/mcphelpers.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from coach.household import attribution as attr_mod, people as people_mod
from coach.i18n_msg import CODE_RE
from coach.mcp.tools import ToolSession
from coach.memory import explain as ex
from coach.memory.store import MemoryStore
from helpers import add_tx
from mcphelpers import TODAY, build_world, payload

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "locales"
SOURCES = ["src/coach/memory/explain.py", "src/coach/classify/rules.py", "src/coach/household/attribution.py", "src/coach/api/simulate.py",
           "src/coach/api/routes/transactions.py"]

ANNOTATIONS = """
  - id: a-cat
    match: { merchant_key: '^(FRESH|STREAMBOX)', category_in: [food.restaurants] }
    tags: [x]
  - id: a-wd
    match: { merchant_key: '^(FRESH|STREAMBOX)', weekdays: [sun] }
    tags: [x]
  - id: a-keys
    match: { tx_keys: [nope1] }
    tags: [x]
  - id: a-desc
    match: { description: 'ZZZNOPE' }
    tags: [x]
  - id: a-from
    match: { merchant_key: '^(FRESH|STREAMBOX)', date_from: 2030-01-01 }
    tags: [x]
  - id: a-to
    match: { merchant_key: '^(FRESH|STREAMBOX)', date_to: 2000-01-01 }
    tags: [x]
  - id: a-min
    match: { merchant_key: '^(FRESH|STREAMBOX)', amount_min: 100000 }
    tags: [x]
  - id: a-max
    match: { merchant_key: '^(FRESH|STREAMBOX)', amount_max: -100000.5 }
    tags: [x]
"""
ATTRIBUTION = """attribution:
  - id: r-acc
    member: anna
    match: { account: zz }
  - id: r-card
    member: mia
    match: { card_last4: "4242" }
  - id: r-mk
    member: luca
    match: { merchant_key: 'ZZZ' }
  - id: r-desc
    member: luca
    match: { description: 'ZZZ' }
  - id: r-in
    member: luca
    match: { account: fo, direction: in }
  - id: r-out
    member: luca
    match: { account: fo, direction: out, amount_min: 99999 }
  - id: r-max
    member: luca
    match: { account: fo, direction: out, amount_max: 1.5 }
"""


def _bundle(lang: str) -> dict:
    return json.loads((LOCALES / lang / "server.json").read_text(encoding="utf-8"))


def _has(bundle: dict, key: str) -> bool:
    *path, last = key.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return False
    return isinstance(node, dict) and (isinstance(node.get(last), str) or isinstance(node.get(f"{last}_other"), str))


@pytest.fixture
def why_world(cfg):
    con = build_world(cfg)
    p = cfg.memory_dir / "categorization.yaml"
    p.write_text(p.read_text() + ANNOTATIONS)
    h = cfg.memory_dir / "household.yaml"
    h.write_text(h.read_text() + ATTRIBUTION)
    con.execute("INSERT OR REPLACE INTO tx_overrides VALUES ('fm1','food.restaurants','lunch')")
    con.execute("INSERT OR REPLACE INTO tx_overrides VALUES ('fm2','food.restaurants',NULL)")
    con.execute("INSERT INTO merchant_entities(id,name,norm_name,category,source,created_at) VALUES (1,'Fresh Mkt','fresh mkt','food.groceries','auto','t')")
    con.execute("INSERT INTO merchant_aliases VALUES ('FRESH MARKET',1,'auto','t')")
    con.execute("INSERT INTO merchant_entities(id,name,norm_name,category,source,created_at) VALUES (2,'Power Co','power co',NULL,'auto','t')")
    con.execute("INSERT INTO merchant_aliases VALUES ('SUNPOWER ENERGIE',2,'auto','t')")
    con.execute("UPDATE merchants SET source='knn' WHERE merchant_key='SUNPOWER ENERGIE'")
    con.execute("UPDATE merchants SET source='llm_web' WHERE merchant_key='STREAMBOX'")
    add_tx(con, "fo", "atm1", "2026-06-01", -50.0, "RETRAIT DAB X4242", "atm")
    add_tx(con, "ce", "tax1", "2026-06-02", -900.0, "TAXE FONCIERE 2026", "direct_debit")
    con.commit()
    yield con
    con.close()


def _keys(con) -> list[str]:
    keys = [r[0] for r in con.execute("SELECT tx_key FROM transactions ORDER BY tx_key")]
    return [k for k in keys if not k.startswith("gro") or k in ("gro00", "gro01")]


def test_every_sentence_of_the_why_carries_its_message(cfg, why_world):
    """Each step, annotation reason and attribution "why not" has a message whose English is the sentence itself, whose code the web knows,
    and a step code with its label in every language."""
    store = MemoryStore(cfg.memory_dir, history=False)
    people = people_mod.load(store)
    en = _bundle("en")
    langs = {lang: _bundle(lang)["labels"]["explainStep"] for lang in ("en", "fr", "it")}
    codes, step_codes = set(), set()
    for k in _keys(why_world):
        x = ex.explain(why_world, store, k)
        for s in x["steps"]:
            assert s["detail_msg"] and s["detail_msg"]["text"] == s["detail"], (k, s)
            codes.add(s["detail_msg"]["code"])
            step_codes.add(s["step_code"])
        for a in x["memory"]["annotations"]:
            assert a["reason_msg"] and a["reason_msg"]["text"] == a["reason"], (k, a)
            codes.add(a["reason_msg"]["code"])
        at = attr_mod.explain(why_world, people, k)
        assert at["rules"]
        for r in at["rules"]:
            if r["matched"]:
                assert r["why_not_msg"] is None
            else:
                assert r["why_not_msg"]["text"] == r["why_not"]
                codes.add(r["why_not_msg"]["code"])
    assert not sorted(c for c in codes if not _has(en, c))
    for lang, labels in langs.items():
        assert step_codes <= set(labels), lang
    # every branch of the synthetic world is exercised (one per kind of sentence)
    assert {"explain.override", "explain.overrideNote", "explain.noOverride", "explain.transferLink", "explain.noTransferLink",
            "explain.typeRule", "explain.noTypeRule", "explain.userLabel", "explain.noUserLabel", "explain.rule", "explain.noRule",
            "explain.entityOutranked", "explain.entityNoCategory", "explain.noEntity", "explain.llmLabel", "explain.llmWebLabel",
            "explain.knnLabel", "explain.noAutoLabel"} <= codes
    assert {f"annotation.{c}" for c in ("allMatch", "categoryIn", "weekdays", "txKeys", "merchantKey", "description", "dateFrom", "dateTo",
                                         "amountMin", "amountMax")} <= codes
    assert {f"attribution.{c}" for c in ("account", "card", "merchantKey", "description", "directionIn", "amountBelow", "amountAbove")} <= codes
    assert {"override", "transferLink", "typeRule", "userLabel", "rule", "entity", "llm", "llmWeb", "knn", "autoLabel"} == step_codes


def test_the_params_are_raw_and_the_data_is_never_in_the_key(cfg, why_world):
    store = MemoryStore(cfg.memory_dir, history=False)
    x = ex.explain(why_world, store, "fm1")
    by = {s["step_code"]: s["detail_msg"] for s in x["steps"]}
    assert by["override"] == {"code": "explain.overrideNote", "params": {"override_category": "food.restaurants", "note": "lunch"},
                              "text": "per-transaction override food.restaurants (lunch)"}
    assert by["entity"]["params"] == {"merchant": "Fresh Mkt", "default_category": "food.groceries"}
    reasons = {a["id"]: a["reason_msg"] for a in x["memory"]["annotations"]}
    assert reasons["a-min"]["params"] == {"tx_amount": "-40.00", "min_amount": "100000.00"}
    assert reasons["a-wd"]["params"] == {"weekdays": "sun", "weekday": "sat", "payment_date": "2026-02-14"}
    assert reasons["kitchen-works"]["params"] == {"pattern": "^BRICO RENOV", "merchant_key": "FRESH MARKET"}


def test_every_literal_code_of_the_why_exists_in_every_language():
    """The codes built from a variable (the annotation and attribution checks, the explain helper) are not seen by the generic scan of
    tests/test_i18n_msg.py: they are checked here, in every language."""
    used = set()
    for rel in SOURCES:
        used |= set(re.findall(r"\"((?:explain|annotation|attribution|categoryEdit)\.[a-zA-Z0-9]+)\"", (ROOT / rel).read_text(encoding="utf-8")))
    assert len(used) > 40 and all(CODE_RE.match(c) for c in used)
    for lang in ("en", "fr", "it"):
        b = _bundle(lang)
        assert not sorted(c for c in used if not _has(b, c)), lang


def test_cli_and_mcp_keep_their_english_without_messages(cfg, why_world, capsys):
    """`coach explain --json` and the `explain_transaction` tool carry no `*_msg` (and no step code); the CLI text is the same as before."""
    store = MemoryStore(cfg.memory_dir, history=False)
    x = ex.explain(why_world, store, "stream00")
    plain = json.dumps(ex.plain(x), default=str)
    assert "_msg" not in plain and "step_code" not in plain
    txt = ex.format_explanation(x)
    assert ">> LLM with web evidence label -> subscriptions.video_streaming" in txt
    assert "a-wd: MATCHES - all criteria match" in txt and "a-cat: no - category_in ['food.restaurants'] does not include" in txt
    s = ToolSession(cfg, con=why_world, insecure=True, today=TODAY, session_id="s_test")
    for r in payload(s.call("transactions_search", {"limit": 50}))["transactions"]:
        res = s.call("explain_transaction", {"tx_ref": r["ref"]})
        assert res.ok and "_msg" not in res.text and "step_code" not in res.text, r["ref"]
        e = json.loads(res.text)
        assert set(e["decision_chain"][0]) == {"step", "applies", "decides", "category", "source", "meaning"}
        low = res.text.lower()                        # the params of the messages (patterns, notes, the rules' account) never reach the model
        assert "zzznope" not in low and "brico renov" not in low and "lunch" not in low and "'zz'" not in low
