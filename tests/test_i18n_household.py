"""i18n step 4h: household, onboarding, setup, connections, calendar and the generated memory questions carry, next to their English, a
message the web translates (`<field>_msg` = {code, params, text}). The generated questions STORE it in open-questions.yaml (optional fields,
old files still load). The MCP tools and the CLI's --json keep the English only.

Synthetic data only."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from apihelpers import ctx, world  # noqa: F401
from coach.memory import check as check_mod, qgen, questions as Q, schemas
from coach.memory.store import MemoryStore
from memhelpers import TODAY as MEM_TODAY, make_world

ROOT = Path(__file__).resolve().parents[1]


def _bundle(lang: str) -> dict:
    return json.loads((ROOT / "web" / "src" / "locales" / lang / "server.json").read_text(encoding="utf-8"))


EN = _bundle("en")


def _has(bundle: dict, code: str) -> bool:
    *path, last = code.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return False
    return isinstance(node, dict) and (isinstance(node.get(last), str) or isinstance(node.get(f"{last}_other"), str))


def _pairs(obj, found=None) -> list:
    """Every (English, message) pair of a payload: a `<field>_msg` next to its `<field>` (a list pairs item by item)."""
    found = [] if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_msg") and k[:-4] in obj:
                eng = obj[k[:-4]]
                if isinstance(v, list):
                    assert isinstance(eng, list) and len(eng) == len(v), k
                    found += [(e, m) for e, m in zip(eng, v) if m is not None]
                elif v is not None:
                    found.append((eng, v))
            else:
                _pairs(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _pairs(v, found)
    return found


def _check(payload) -> list:
    pairs = _pairs(payload)
    for eng, msg in pairs:
        assert msg["text"] == eng, (eng, msg)
        assert _has(EN, msg["code"]), msg["code"]
    return pairs


# ---------------------------------------------------------------- the API pages

def test_every_page_of_the_chunk_sends_aligned_messages_with_known_codes(ctx):  # noqa: F811
    codes = set()
    for path, params in (("/calendar", {"days": 120}), ("/onboarding", {}), ("/health", {}), ("/connections", {}), ("/setup/wizard", {}),
                         ("/household/overview", {}), ("/household/allocation", {}), ("/household/kids", {}), ("/questions", {"status": "all"})):
        r = ctx.get(path, **params)
        assert r.status_code == 200, path
        codes |= {m["code"] for _, m in _check(r.json())}
    assert {"onboarding.how.check", "onboarding.coarseIds"} <= codes
    assert any(c.startswith("calendar.") for c in codes) and any(c.startswith("wizard.") for c in codes)


def test_calendar_items_translate_their_titles_and_notes_but_never_a_name(ctx):  # noqa: F811
    items = ctx.get("/calendar", days=120).json()["items"]
    for i in items:
        if i["kind"] in ("payment", "income", "saving"):                    # the title is a merchant's name: no message
            assert i["title_msg"] is None
        if i["title_msg"]:
            assert i["title_msg"]["text"] == i["title"]
    assert any(i["note_msg"] for i in items)


def test_the_ics_export_stays_english():
    from coach.analytics import upcoming
    from anhelpers import make_ds, monthly
    ds = make_ds(monthly("2026-01-05", 9, -20.0))
    res = upcoming.calendar_items(ds, 60)
    assert "_msg" not in upcoming.to_ics(res)


@pytest.mark.parametrize("table, suffixes", [("CADENCE_CODE", ("", "Variable")), ("CHECK_CODE", ("",)), ("CHARGED_CODE", ("",)),
                                             ("CONSENT_CODE", ("",))])
def test_the_calendar_code_tables_are_known_to_the_web(table, suffixes):
    from coach.analytics import upcoming
    for code in getattr(upcoming, table).values():
        for s in suffixes:
            for lang in ("en", "fr", "it"):
                assert _has(_bundle(lang), code + s), (lang, code + s)


def test_the_health_problems_and_the_sync_notes_have_their_codes():
    from coach.ingest import health as hm
    for code in [*hm.DEAD_CODE.values(), "health.consentDead", "sync.consentExpired", "sync.consentRevoked"]:
        assert _has(EN, code), code


def test_health_problems_are_paired_and_the_manual_group_has_a_code(cfg):
    from datetime import datetime, timedelta, timezone
    from coach.db import connect
    from coach.ingest import health as hm
    from helpers import add_bank
    now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Bank", "FR", [("a1", "FR7600000000000000000000001", "ONE")], (now + timedelta(days=2)).isoformat())
    con.execute("INSERT INTO sync_log VALUES ('a1', ?, 0, 0, 1, 'boom')", ((now - timedelta(days=5)).isoformat(timespec="seconds"),))
    con.commit()
    rep = hm.health(con, 4, 2, now).to_dict()
    a = rep["banks"][0]["accounts"][0]
    assert len(a["problems"]) == len(a["problems_msg"]) >= 3
    codes = {m["code"] for _, m in _check(rep)}
    assert {"health.consentUrgent", "health.lastSyncFailed", "health.neverSynced"} <= codes
    urgent = next(m for m in a["problems_msg"] if m["code"] == "health.consentUrgent")
    assert urgent["params"] == {"count": 2, "command": 'coach reconnect "Bank"'}         # the command is a param, never translated


def test_the_wizard_details_are_messages():
    from coach.setup import wizard as W
    d = W.Detected.of("sync", "todo", {"code": "wizard.transactions", "params": {"count": 0}, "text": "0 transaction(s)"})
    assert d.to_dict()["detail_msg"]["code"] == "wizard.transactions" and d.detail == "0 transaction(s)"
    assert _has(EN, "wizard.finishFirst") and all(_has(EN, f"labels.setupStep.{n}") for n in set(W.NEEDS.values()))


def test_the_sync_job_message_is_a_message(ctx):  # noqa: F811
    from coach.api import jobs
    j = jobs.Job("sync")
    j.message_msg = {"code": "job.syncSummary", "params": {"count": 2, "skipped": 0, "failed": 0}, "text": "x"}
    assert j.to_dict()["message_msg"]["code"] == "job.syncSummary"
    assert "message_msg" in ctx.get("/connections").json()["sync"]


# ---------------------------------------------------------------- the generated questions

def test_generated_questions_carry_a_topic_code_and_a_message_of_their_own_text(cfg):
    con = make_world(cfg)
    res = qgen.generate(MemoryStore(cfg.memory_dir), con, cfg, MEM_TODAY)
    assert res.new
    with_msg = [q for q in res.new if q.question_msg]
    assert len(with_msg) >= len(res.new) - 1
    for q in res.new:
        assert q.topic_code in qgen.TOPIC_CODE.values()
        if q.question_msg:
            assert q.question_msg.text == q.question and _has(EN, q.question_msg.code)
    kinds = {q.question_msg.code for q in with_msg}
    assert {"question.recurringNoFile", "question.birthYearAdult"} & kinds


def test_the_account_question_codes_exist():
    for miss in ("accountNoOwner", "accountNoPurpose", "accountNoOwnerPurpose"):
        for suffix in ("", "UnknownBank"):
            for lang in ("en", "fr", "it"):
                assert _has(_bundle(lang), f"question.{miss}{suffix}"), (lang, miss, suffix)


def test_the_english_topic_labels_are_the_generators_topics():
    assert {code: topic for topic, code in qgen.TOPIC_CODE.items()} == EN["labels"]["questionTopic"]


def test_the_messages_round_trip_through_the_yaml_and_pass_the_memory_check(cfg):
    con = make_world(cfg)
    store = MemoryStore(cfg.memory_dir)
    new = qgen.generate(store, con, cfg, MEM_TODAY).new
    Q.add_many(store, new)
    raw = yaml.safe_load((cfg.memory_dir / Q.YAML_FILE).read_text())
    stored = [q for q in raw["questions"] if q.get("question_msg")]
    assert stored and all(set(q["question_msg"]) == {"code", "params", "text"} for q in stored)
    again = {q.id: q for q in store.questions()}
    for q in new:
        assert again[q.id].question_msg == q.question_msg and again[q.id].topic_code == q.topic_code
    issues = [i for i in check_mod.run_check(store, con, today=MEM_TODAY, cfg=cfg) if i.file == Q.YAML_FILE and i.level == "error"]
    assert not issues


def test_an_old_question_without_the_new_fields_still_loads():
    q = schemas.Question.model_validate({"id": "q-1", "topic": "General", "question": "Old?", "origin": "manual"})
    assert q.question_msg is None and q.topic_code is None and q.context_msg is None


def test_a_message_with_a_wrong_param_type_or_code_is_refused():
    base = {"id": "q-1", "question": "Q?"}
    for bad in ({"code": "question.x", "params": {"count": "2"}, "text": "Q?"}, {"code": "Nope", "params": {}, "text": "Q?"},
                {"code": "question.x", "params": {"since_date": "05/10/2026"}, "text": "Q?"}, {"code": "question.x", "params": {}, "text": ""},
                {"code": "question.x", "params": {}, "text": "Q?", "extra": 1}):
        with pytest.raises(ValidationError):
            schemas.Question.model_validate({**base, "question_msg": bad})
    with pytest.raises(ValidationError):
        schemas.Question.model_validate({**base, "topic_code": "Not a code"})


def test_an_unquoted_yaml_date_is_read_back_as_its_iso_text():
    q = schemas.Question.model_validate({"id": "q-1", "question": "Q?", "question_msg": {
        "code": "question.assetStale", "params": {"id": "a", "as_of_date": dt.date(2026, 1, 2), "first": dt.date(2026, 1, 3)}, "text": "Q?"}})
    assert q.question_msg.params == {"id": "a", "as_of_date": "2026-01-02", "first": "2026-01-03"}


def test_a_hand_edited_question_drops_its_stale_message():
    q = schemas.Question.model_validate({"id": "q-1", "question": "Edited by hand?", "context": "c",
                                         "question_msg": {"code": "question.contractFields", "params": {"id": "x", "fields": "renewal"},
                                                          "text": "Contract x: renewal unknown."},
                                         "context_msg": {"code": "a.b", "params": {}, "text": "c"}})
    assert q.question_msg is None and q.context_msg is not None


def test_the_questions_cli_json_keeps_the_english_only(cfg, capsys):
    from coach.cli import main
    con = make_world(cfg)
    store = MemoryStore(cfg.memory_dir)
    Q.add_many(store, qgen.generate(store, con, cfg, MEM_TODAY).new)
    main(["--config", str(cfg.config_path), "--insecure", "questions", "list", "--open", "--json"])
    items = json.loads(capsys.readouterr().out)
    assert items and not [k for i in items for k in i if k.endswith("_msg")]


def test_onboarding_cli_json_keeps_the_english_only(cfg, capsys):
    from coach.cli import main
    make_world(cfg).close()
    main(["--config", str(cfg.config_path), "--insecure", "onboarding", "status", "--json"])
    out = capsys.readouterr().out
    assert "_msg" not in out and "how" in json.loads(out)
