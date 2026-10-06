"""E3-3 open questions: structured store, regenerated Markdown view, migration, CLI, automatic generation."""
import datetime as dt
import hashlib
import json

import pytest

from coach.cli import main
from coach.memory import qgen, questions as Q
from coach.memory.store import MemoryStore, MemoryStoreError, ValidationFailed
from memhelpers import TODAY, label, make_memory, make_world
from helpers import add_tx

LEGACY = """\
# Open questions for the user

The coach asks these when relevant.

## Categorization (ordered by money at stake)

- [x] **Works payment** +1,000 EUR (2025-06-07): own savings (answered 2026-10-04).
- [ ] **SHOP ONE**: 55 payments, −1,759 EUR. What is it?
- [ ] **SHOP TWO**: 23 payments, −1,367 EUR (hairdresser?).
- [x] **Upline charges** (2026-06-22 −12.79): lunch above the
  25 EUR/day allowance → `food.work_meals` (answered 2026-10-04).

## Profile

- [ ] Savings balances: how many accounts → `assets.yaml`.
- [x] Main bank = Test Bank (2026-10-04).
- plain bullet without a checkbox
"""


@pytest.fixture
def mem(tmp_path):
    m = make_memory(tmp_path / "memory")
    (m / "open-questions.md").write_text(LEGACY)
    return m


@pytest.fixture
def store(mem):
    return MemoryStore(mem)


# ---------------------------------------------------------------- migration

def test_plan_counts_and_is_lossless(store):
    plan, md = Q.migrate_preview(store)
    assert plan.md_items == 7 and len(plan.questions) == 7 and plan.lossless
    assert (plan.n_open, plan.n_answered) == (4, 3)                       # the plain bullet is kept as open
    by_id = {q.id: q for q in plan.questions}
    assert by_id["q-001"].status == "answered" and by_id["q-001"].answered == dt.date(2026, 10, 4)
    assert by_id["q-001"].question == "Works payment" and "own savings" in by_id["q-001"].answer
    assert by_id["q-004"].source_text.startswith("**Upline charges** (2026-06-22 −12.79): lunch above the 25 EUR/day")
    assert by_id["q-002"].stake == 1759 and by_id["q-002"].status == "open"
    assert by_id["q-005"].suggested_target.file == "assets.yaml"
    assert by_id["q-006"].answered == dt.date(2026, 10, 4)                 # "(2026-10-04)." without the word
    assert by_id["q-007"].status == "open" and by_id["q-007"].topic == "Profile"


def test_every_original_line_survives_in_the_regenerated_view(store):
    plan, md = Q.migrate_preview(store)
    for q in plan.questions:
        assert q.source_text in md                                          # nothing lost
    for h in ("## Categorization (ordered by money at stake)", "## Profile"):
        assert h in md
    assert md.count("- [x]") == 3 and md.count("- [ ]") == 4


def test_dry_run_writes_nothing_and_write_converts_with_backup(store, mem, capsys):
    before = (mem / "open-questions.md").read_text()
    plan, backup, res = Q.migrate_write(store)
    assert backup.read_text() == before and backup.parent.name == ".backups"
    assert (mem / "open-questions.yaml").exists()
    assert "## Profile" in (mem / "open-questions.md").read_text()
    assert len(store.questions()) == 7 and res.change_id
    with pytest.raises(Q.QuestionError):
        Q.migrate_write(store)                                              # one-time only


def test_preview_refuses_without_markdown(tmp_path):
    st = MemoryStore(make_memory(tmp_path / "m"))
    with pytest.raises(Q.QuestionError):
        Q.migrate_preview(st)


# ---------------------------------------------------------------- the service

@pytest.fixture
def migrated(store):
    Q.migrate_write(store)
    return store


def other_files(mem):
    return {str(p.relative_to(mem)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in mem.rglob("*") if p.is_file() and p.name not in ("open-questions.yaml", "open-questions.md")
            and ".history.git" not in p.parts and ".backups" not in p.parts and p.name != ".lock"}


def test_answer_records_only_the_answer_and_changes_no_other_file(migrated, mem):
    before = other_files(mem)
    q = Q.answer(migrated, "q-002", "A grocery store.", today=dt.date(2026, 10, 5))
    assert q.status == "answered" and q.answer == "A grocery store." and q.answered == dt.date(2026, 10, 5)
    assert other_files(mem) == before
    md = (mem / "open-questions.md").read_text()
    assert "- [x] `q-002`" in md and "A grocery store." in md                  # the view shows the NEW answer
    with pytest.raises(Q.QuestionError):
        Q.answer(migrated, "q-002", "again")
    with pytest.raises(Q.QuestionError):
        Q.answer(migrated, "q-003", "   ")
    with pytest.raises(Q.QuestionError):
        Q.answer(migrated, "q-999", "x")


def test_dismiss_and_reopen_roundtrip(migrated, mem):
    Q.dismiss(migrated, "q-003", "not interesting", today=TODAY)
    assert [q.status for q in migrated.questions() if q.id == "q-003"] == ["dismissed"]
    assert "~~" in (mem / "open-questions.md").read_text() and "not interesting" in (mem / "open-questions.md").read_text()
    Q.reopen(migrated, "q-003")
    q = next(x for x in migrated.questions() if x.id == "q-003")
    assert q.status == "open" and q.note is None and q.answered is None
    Q.answer(migrated, "q-003", "x")
    Q.reopen(migrated, "q-003")
    assert next(x for x in migrated.questions() if x.id == "q-003").answer is None


def test_add_creates_the_file_and_the_view_from_nothing(tmp_path):
    st = MemoryStore(make_memory(tmp_path / "m"))
    q = Q.add(st, "Where is the PEE held?", topic="Assets", target="assets.yaml:assets[pee].provider",
              evidence={"balance": 430000}, stake=430000, today=TODAY)
    assert q.id == "q-001" and q.suggested_target.field == "assets[pee].provider"
    md = (st.root / "open-questions.md").read_text()
    assert "## Assets" in md and "`q-001` Where is the PEE held?" in md and "balance=430000" in md
    assert Q.add(st, "Another?").id == "q-002"


def test_ids_are_unique_and_every_change_is_recorded(migrated):
    with pytest.raises(Q.QuestionError):
        Q.add(migrated, "dup", qid="q-001")
    Q.answer(migrated, "q-002", "x")
    s = migrated.history()[0]
    assert s.subject.startswith("coach: answer-question open-questions.yaml")
    assert set(s.files) == {"open-questions.md", "open-questions.yaml"}          # yaml + its view in ONE change


def test_a_hand_edited_markdown_view_blocks_the_next_write_until_regenerated(migrated, mem):
    (mem / "open-questions.md").write_text((mem / "open-questions.md").read_text() + "\nmy own note\n")
    before = (mem / "open-questions.yaml").read_text()
    with pytest.raises(Q.QuestionError) as e:
        Q.answer(migrated, "q-002", "x")
    assert "edited by hand" in str(e.value) and "coach questions" in str(e.value)
    assert (mem / "open-questions.yaml").read_text() == before                       # nothing was written
    assert "my own note" in (mem / "open-questions.md").read_text()                    # nothing was discarded
    backup = Q.regenerate_view(migrated)
    assert "my own note" in backup.read_text() and "my own note" not in (mem / "open-questions.md").read_text()
    Q.answer(migrated, "q-002", "x")                                                  # works again


def test_listing_orders_open_by_stake(migrated):
    ids = [q.id for q in Q.listing(migrated, "open")]
    assert ids[:2] == ["q-002", "q-003"]


def test_invalid_hand_edit_of_the_yaml_is_reported(migrated, mem):
    (mem / "open-questions.yaml").write_text("questions:\n  - id: q-1\n    status: answered\n    question: x\n")
    assert migrated.validate("open-questions.yaml")[0].message.startswith("an answered question needs an answer")


# ---------------------------------------------------------------- generation

@pytest.fixture
def world(cfg):
    con = make_world(cfg)
    return con, MemoryStore(cfg.memory_dir)


def gen(con, store, cfg, **kw):
    return qgen.generate(store, con, cfg, TODAY, **kw)


def keys(res):
    return {q.key for q in res.new}


def test_generates_the_expected_kinds_of_questions(cfg, world):
    con, store = world
    res = gen(con, store, cfg)
    k = keys(res)
    assert any(x.startswith("held-back:") for x in k)                       # the person-like counterparty, aggregated
    assert "recurring:SUNPOWER ENERGIE" in k                                # monthly power bill with no contract
    assert "fill:asset:family-house" in k and "member-birth:luca" in k       # no value yet / no birth year
    assert "stale:liability:home-loan:2026-01-15" in k                       # outstanding capital older than 6 months
    assert "fill:liability:home-loan" not in k                               # its key fields are all filled
    by_type = res.by_type
    assert by_type["member-birth"] == 1
    assert "recurring:STREAMBOX" in k and by_type["recurring"] == 2          # the mortgage is covered by home-loan.yaml
    assert "recurring:HOMEBANK ECH PRET" not in k


def test_stale_asset_value_is_asked(cfg, world):
    con, store = world
    store.set_value("savings-book", "as_of", dt.date(2026, 3, 1))
    assert "stale:asset:savings-book:2026-03-01" in keys(gen(con, store, cfg))


def test_a_liability_with_empty_key_fields_gets_one_question(cfg, world):
    con, store = world
    store.edit("liabilities/car.yaml", [{"op": "create", "value": {"id": "car", "kind": "loa", "monthly_payment": 200}}])
    q = [x for x in gen(con, store, cfg).new if x.key == "fill:liability:car"][0]
    # E9-6: a lease owes no capital: it is asked what its end-of-contract decision needs, not the outstanding capital or the rate
    assert q.evidence["missing"] == ["lender", "start_date", "end_date", "residual_value", "mileage_limit_km", "excess_km_fee"]
    assert q.stake == 2400 and q.suggested_target.file == "liabilities/car.yaml"


def test_the_contract_covering_a_recurring_debit_silences_the_question(cfg, world):
    con, store = world
    store.edit("contracts/power.yaml", [{"op": "create", "value": {"id": "power", "kind": "energy",
                                                                 "merchant_match": "SUNPOWER"}}])
    assert "recurring:SUNPOWER ENERGIE" not in keys(gen(con, store, cfg))


def test_person_like_counterparties_never_get_a_question_of_their_own(cfg, world):
    con, store = world
    res = gen(con, store, cfg)
    text = " ".join(q.question + json.dumps(q.evidence, default=str) for q in res.new)
    assert "BIANCHI" not in text and "LUCA BIANCHI" not in text.upper()
    held = [q for q in res.new if q.key == "held-back:people"][0]
    assert held.evidence["counterparties"] == 1 and held.evidence["gross_total"] == 800


def test_merchant_questions_respect_the_money_threshold(cfg, world):
    con, store = world
    # FRESH MARKET: low confidence but only 200 EUR at stake
    assert not any("FRESH MARKET" in q.question for q in gen(con, store, cfg).new)
    assert any("FRESH MARKET" in q.question for q in gen(con, store, cfg, min_stake=100).new)


def test_the_large_unexplained_payment_is_asked_once_per_subject(cfg, world):
    con, store = world
    res = gen(con, store, cfg)
    brico = [q for q in res.new if "BRICO RENOV" in q.question]
    assert len(brico) == 0                                                    # explained by the kitchen-works annotation
    store.edit("categorization.yaml", [{"op": "remove", "path": "annotations[kitchen-works]"}], allow_comment_loss=True)
    brico = [q for q in gen(con, store, cfg).new if "BRICO RENOV" in q.question]
    assert len(brico) == 1 and brico[0].key == "merchant:BRICO RENOV SARL"    # merchant (what is it?) beats "large"
    con.execute("INSERT INTO merchants VALUES ('BRICO RENOV SARL','Brico','housing.renovation',0.95,0,'llm','m','t')")
    con.commit()
    brico = [q for q in gen(con, store, cfg).new if "BRICO RENOV" in q.question]
    assert brico[0].key.startswith("large:") and brico[0].evidence["amount"] == -6500


def test_transfers_and_linked_legs_are_never_asked_about(cfg, world):
    con, store = world
    assert not any("VIR " in q.question for q in gen(con, store, cfg).new)


def test_generation_is_idempotent_and_never_repeats_answered_or_dismissed(cfg, world):
    con, store = world
    first = gen(con, store, cfg)
    Q.add_many(store, first.new)
    assert gen(con, store, cfg).new == [] and gen(con, store, cfg).skipped_existing == len(first.new)
    some = [q.id for q in first.new[:3]]
    Q.answer(store, some[0], "done")
    Q.dismiss(store, some[1])
    assert gen(con, store, cfg).new == []
    Q.reopen(store, some[1])
    assert gen(con, store, cfg).new == []                                     # reopened ones still exist: no duplicate


def test_ids_are_stable_across_runs(cfg, world):
    con, store = world
    a = {q.key: q.id for q in gen(con, store, cfg).new}
    b = {q.key: q.id for q in gen(con, store, cfg).new}
    assert a == b and all(i.startswith("q-") for i in a.values())


def test_a_hand_written_question_naming_the_merchant_suppresses_the_generated_one(cfg, world):
    con, store = world
    Q.add(store, "What is SUNPOWER ENERGIE exactly?", topic="Mine")
    assert "recurring:SUNPOWER ENERGIE" not in keys(gen(con, store, cfg))


def test_accounts_without_owner_or_purpose_and_missing_household(cfg):
    con = make_world(cfg, household=False)
    con.execute("UPDATE accounts SET purpose=NULL WHERE uid='ce'")
    con.commit()
    st = MemoryStore(cfg.memory_dir)
    res = gen(con, st, cfg)
    assert any(k.startswith("acct:ce") for k in keys(res)) and "household:init" in keys(res)
    acct = [q for q in res.new if q.key == "acct:ce"][0]
    assert "FR76" not in acct.question and acct.evidence["missing"] == ["purpose"]


def test_evidence_holds_numbers_not_descriptions(cfg, world):
    con, store = world
    for q in gen(con, store, cfg).new:
        for v in q.evidence.values():
            assert isinstance(v, (int, float, str, list, type(None)))
        assert "IBAN" not in q.question.upper()


def test_resolved_questions_are_detected(cfg, world):
    con, store = world
    Q.add_many(store, gen(con, store, cfg).new)
    store.set_value("luca", "birth_year", 1982)
    q = next(x for x in store.questions() if x.key == "member-birth:luca")
    assert "birth_year is now set" in qgen.resolved_reason(q, store, con, cfg)


# ---------------------------------------------------------------- CLI

def run(cfg, *argv):
    main(["--config", str(cfg.config_path), "--insecure", *argv])


def test_cli_list_answer_dismiss_add_json(cfg, world, capsys):
    con, store = world
    run(cfg, "questions", "generate")
    out = capsys.readouterr().out
    assert "new question(s)" in out and "dry run" not in out
    run(cfg, "questions", "list", "--open", "--json")
    items = json.loads(capsys.readouterr().out)
    assert items and all(i["status"] == "open" for i in items)
    qid = items[0]["id"]
    run(cfg, "questions", "answer", qid, "the answer")
    assert "only RECORDED" in capsys.readouterr().out
    run(cfg, "questions", "dismiss", items[1]["id"], "--reason", "meh")
    run(cfg, "questions", "add", "Is the car insured?", "--topic", "Cars", "--target", "contracts/car.yaml", "--stake", "500",
        "--evidence", "monthly=60")
    run(cfg, "questions", "list", "--status", "answered")
    assert qid in capsys.readouterr().out
    run(cfg, "questions", "generate", "--dry-run")
    assert "0 new question(s)" in capsys.readouterr().out


def test_cli_generate_dry_run_writes_nothing(cfg, world, capsys):
    run(cfg, "questions", "generate", "--dry-run")
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out
    assert not (cfg.memory_dir / "open-questions.yaml").exists()


def test_cli_migrate_questions_dry_run_then_write(cfg, capsys):
    make_memory(cfg.memory_dir)
    (cfg.memory_dir / "open-questions.md").write_text(LEGACY)
    run_noconn = lambda *a: main(["--config", str(cfg.config_path), *a])            # no database needed
    run_noconn("memory", "migrate-questions")
    out = capsys.readouterr().out
    assert "7 items (4 open, 3 answered)" in out and "lossless: True" in out and "dry run" in out
    assert not (cfg.memory_dir / "open-questions.yaml").exists()
    run_noconn("memory", "migrate-questions", "--write")
    assert (cfg.memory_dir / "open-questions.yaml").exists() and list((cfg.memory_dir / ".backups").iterdir())
    with pytest.raises(SystemExit) as e:
        run_noconn("memory", "migrate-questions", "--write")
    assert "already exists" in str(e.value)


def test_cli_unknown_question_id_is_a_clean_error(cfg):
    make_memory(cfg.memory_dir)
    with pytest.raises(SystemExit) as e:
        main(["--config", str(cfg.config_path), "questions", "answer", "q-1", "x"])
    assert str(e.value).startswith("error:")
