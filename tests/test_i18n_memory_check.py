"""i18n step 4i: the issues of `coach memory check`, the warnings of the memory-write previews and the invalid budget entries carry a message
the web app translates (code ``memoryCheck.*`` / ``memoryLoad.*`` + raw params) next to the English, which the CLI and the MCP tools keep alone.

Synthetic data only (the world of memhelpers / apihelpers)."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from apihelpers import ctx, world  # noqa: F401
from coach.api.routes._write import edit_out
from coach.cli import main
from coach.memory import check as C
from coach.memory.store import Issue, MemoryStore
from memhelpers import TODAY, make_world

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "locales"
SOURCES = [ROOT / "src/coach/memory/check.py", ROOT / "src/coach/memory/store.py"]
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def _bundle(lang: str) -> dict:
    return json.loads((LOCALES / lang / "server.json").read_text(encoding="utf-8"))


def _variants(bundle: dict, code: str) -> list[str]:
    """The string of a code, or its plural forms."""
    *path, last = code.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return []
    return [v for k, v in node.items() if isinstance(v, str) and (k == last or re.fullmatch(rf"{last}_(one|many|other)", k))]


def _literal_calls() -> list[tuple[str, set]]:
    """(code, keyword names) of every server_msg("memoryCheck.* | memoryLoad.*", ...) of the memory check and the store."""
    out = []
    for p in SOURCES:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "server_msg" and node.args \
                    and isinstance(node.args[0], ast.Constant):
                out.append((node.args[0].value, {k.arg for k in node.keywords}))
    return out


# ---------------------------------------------------------------- the codes and their params

def test_the_check_and_the_store_build_their_issues_as_messages():
    calls = _literal_calls()
    assert len(calls) > 70 and all(c.split(".")[0] in ("memoryCheck", "memoryLoad") for c, _ in calls)
    for p in SOURCES:                                   # no issue left without its message
        assert not re.search(r"\bIssue\(\"", p.read_text(encoding="utf-8")), p


@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_every_code_exists_in_every_language_and_uses_only_its_params(lang):
    b = _bundle(lang)
    for code, params in _literal_calls():
        texts = _variants(b, code)
        assert texts, (lang, code)
        area, name = code.split(".")
        if lang != "en" and f"{name}_one" in _bundle("en")[area]:
            assert {f"{name}_{f}" for f in ("one", "many", "other")} <= set(b[area]), (lang, code)    # fr / it: _one, _many, _other
        for t in texts:
            assert set(PLACEHOLDER.findall(t)) <= params | {"count"}, (lang, code, t)
            if "count" in PLACEHOLDER.findall(t):
                assert "count" in params, (lang, code)


def test_the_commands_are_params_never_translated():
    for lang in ("fr", "it"):
        b = _bundle(lang)
        for area in ("memoryCheck", "memoryLoad"):
            for k, v in b[area].items():
                assert "`coach " not in v, (lang, k)      # a `coach ...` command comes in as {{command}}


# ---------------------------------------------------------------- the issues

@pytest.fixture
def mem(cfg):
    con = make_world(cfg)
    md = cfg.memory_dir
    (md / "categorization.yaml").write_text("annotations:\n  - id: a\n    match: {merchant_key: '^NOBODY$'}\n    category: no.such\n"
                                            "    event: ghost\n")
    (md / "budgets.yaml").write_text("budgets:\n  - id: b1\n    category: no.such\n    monthly: 100\n"
                                     "  - id: b2\n    category: food.groceries\n    monthly: 50\n"
                                     "  - id: b2\n    category: food.restaurants\n    monthly: 40\n")
    yield con, MemoryStore(md)
    con.close()


def test_every_issue_carries_its_message_with_the_english_text(mem):
    con, store = mem
    issues = C.run_check(store, con, today=TODAY)
    en = _bundle("en")
    assert {"unknown_category", "unknown_event", "annotation_matches_nothing", "stale_value"} <= {i.code for i in issues}
    for i in issues:
        assert i.msg and i.msg["text"] == i.message and i.msg["code"].startswith("memoryCheck."), i
        assert _variants(en, i.msg["code"]), i.msg["code"]
    u = next(i for i in issues if i.code == "unknown_category")
    assert u.message == "unknown category 'no.such' (see `coach taxonomy list`)"                   # the CLI's English is unchanged
    assert u.msg["params"] == {"category": "no.such", "command": "coach taxonomy list"}


def test_a_schema_error_translates_its_frame_only(cfg):
    con = make_world(cfg)
    (cfg.memory_dir / "assets.yaml").write_text("assets:\n  - id: a\n    kind: other\n    balance: lots\n")
    i = next(i for i in C.run_check(MemoryStore(cfg.memory_dir), con, today=TODAY) if i.code == "schema")
    assert i.msg["code"] in ("memoryCheck.invalidValue", "memoryCheck.unknownFieldTypo")
    assert i.msg["text"] == i.message
    con.close()


def test_the_cli_json_and_the_text_output_keep_the_english_only(mem, cfg, capsys):
    with pytest.raises(SystemExit):                                           # the unknown category is an error: exit 1
        main(["--config", str(cfg.config_path), "--insecure", "memory", "check", "--json"])
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["issues"] and "_msg" not in out and all(set(i) <= {"level", "code", "file", "path", "message", "line"} for i in d["issues"])
    assert "memoryCheck" not in C.format_issues(C.run_check(mem[1], mem[0], today=TODAY))


def test_to_dict_adds_the_message_only_on_request():
    i = Issue.of("warning", "household_no_adult", "household.yaml", {"code": "memoryCheck.householdNoAdult", "params": {},
                                                                     "text": "no adult member is declared"})
    assert "message_msg" not in i.to_dict()
    assert i.to_dict(messages=True)["message_msg"]["code"] == "memoryCheck.householdNoAdult"
    assert Issue("info", "x", "f", "plain").to_dict(messages=True)["message_msg"] is None      # an older issue: the English stays


def test_the_budget_problems_come_with_their_messages_in_the_same_order(mem):
    _con, store = mem
    valid, problems = store.budgets_checked()                                 # the CLI's form is unchanged
    v2, p2, msgs = store.budgets_checked(messages=True)
    assert problems == p2 and [m["text"] for m in msgs] == problems and [b.id for b in valid] == [b.id for b in v2] == ["b2"]
    assert [m["code"] for m in msgs] == ["memoryLoad.duplicateId", "memoryLoad.unknownCategory"]
    assert problems == ["b2: duplicate id (ignored)", "b1: unknown category 'no.such'"]


def test_a_write_preview_sends_its_warnings_as_messages():
    w = Issue.of("warning", "pocket_money_adult", "household.yaml", {"code": "memoryCheck.pocketMoneyAdult", "params": {"member": "adult-1"},
                                                                    "text": "pocket_money is set on 'adult-1', who is not a child"})
    res = SimpleNamespace(changed=True, diff="+x", issues=[w, Issue("info", "y", "f", "plain")])
    out = edit_out(SimpleNamespace(), res, True)
    assert out["warnings"] == [w.message, "plain"] and out["warnings_msg"] == [w.msg, None]


# ---------------------------------------------------------------- the API and the MCP tools

def test_the_api_check_and_budgets_carry_the_messages(ctx):  # noqa: F811
    md = ctx.cfg.memory_dir
    (md / "budgets.yaml").write_text("budgets:\n  - id: b1\n    category: no.such\n    monthly: 100\n"
                                     "  - id: b2\n    category: food.groceries\n    monthly: 50\n")
    c = ctx.get("/memory/check").json()
    assert c["issues"] and all("message_msg" in i for i in c["issues"])
    assert any(i["message_msg"] and i["message_msg"]["code"] == "memoryCheck.unknownCategory" for i in c["issues"])
    b = ctx.get("/budgets").json()
    assert b["problems"] == ["b1: unknown category 'no.such'"]
    assert [m["code"] for m in b["problems_msg"]] == ["memoryLoad.unknownCategory"]


def test_no_message_reaches_the_mcp_budget_and_goal_tools(cfg):
    from coach.mcp.tools import ToolSession
    con = make_world(cfg)
    (cfg.memory_dir / "budgets.yaml").write_text("budgets:\n  - id: b1\n    category: no.such\n    monthly: 100\n")
    s = ToolSession(cfg, con=con, insecure=True, today=TODAY, session_id="s_i18n_mem")
    try:
        for name in ("budget_status", "goals", "memory_context", "onboarding_status"):
            res = s.call(name, {})
            assert "_msg" not in res.text, name
        assert "left out; run `coach memory check`" in s.call("budget_status", {}).text        # the English warning is still there
    finally:
        s.close()
        con.close()


def test_a_category_preview_keeps_both_kinds_of_warnings_aligned(ctx, monkeypatch):  # noqa: F811
    """The memory edit's own warnings (run_edit, with messages) come first, the preview's (it matches no transaction) after: the two lists
    have the same length and order, neither side overwrites the other."""
    orig = MemoryStore.semantic_issues

    def with_warning(self, rel, model, strict=True):
        return orig(self, rel, model, strict) + [Issue.of("warning", "household_no_adult", rel, {
            "code": "memoryCheck.householdNoAdult", "params": {}, "text": "no adult member is declared"})]
    monkeypatch.setattr(MemoryStore, "semantic_issues", with_warning)
    pv = ctx.post("/annotations", {"merchant_key": "^NOBODY$", "category": "food.groceries"}, dry_run=True).json()
    assert len(pv["warnings"]) == len(pv["warnings_msg"]) == 2
    assert pv["warnings"][0] == "no adult member is declared" and pv["warnings_msg"][0]["code"] == "memoryCheck.householdNoAdult"
    assert pv["warnings"][1] == "it matches no transaction" and pv["warnings_msg"][1]["code"] == "annotation.matchesNothing"


def test_with_warnings_pads_a_side_without_messages():
    from coach.api.routes.transactions import _with_warnings
    m = {"code": "x.y", "params": {}, "text": "a"}
    out = _with_warnings({"warnings": ["a", "b"], "warnings_msg": [m]}, ["c"], [])
    assert out["warnings"] == ["a", "b", "c"] and out["warnings_msg"] == [m, None, None]
