"""E3-4 memory store: round-trip YAML, schemas, validated edits, atomic writes."""
import datetime as dt
import os
import stat
import threading

import pytest

from coach.memory import edit as edit_mod, schemas, yamlio
from coach.memory.store import MemoryStore, MemoryStoreError, ValidationFailed
from memhelpers import ASSETS, CATEGORIZATION, LOAN, make_memory


@pytest.fixture
def mem(tmp_path):
    return make_memory(tmp_path / "memory")


@pytest.fixture
def store(mem):
    return MemoryStore(mem)


# ---------------------------------------------------------------- yamlio

@pytest.mark.parametrize("text", [
    CATEGORIZATION, ASSETS, LOAN,
    "a: null\nb: { x: 1, y: null }   # c\nc: [1, 2]\nd: {}\ne: 'quoted: yes'\n",
    "# only a comment\nkey: value  # trailing\n\n\nother:\n  - 1\n  - 2   # two\n",
    "note: >-\n  folded text\n  continues here\nlist:\n  - id: a\n    v: 1\n",
])
def test_untouched_files_dump_byte_for_byte(text):
    assert yamlio.dumps(yamlio.loads(text)) == text


def test_none_is_written_null_and_dates_stay_unquoted():
    doc = yamlio.loads("a: 1\n")
    doc["b"] = None
    doc["d"] = dt.date(2026, 1, 31)
    assert yamlio.dumps(doc) == "a: 1\nb: null\nd: 2026-01-31\n"


def test_invalid_yaml_reports_a_line():
    with pytest.raises(yamlio.YamlError) as e:
        yamlio.loads("a: [1, 2\nb: 3\n")
    assert "invalid YAML" in str(e.value)


@pytest.mark.parametrize("raw,expected", [
    ("12", 12), ("1.5", 1.5), ("true", True), ("null", None), ("", None), ("2026-01-31", dt.date(2026, 1, 31)),
    ("hello world", "hello world"), ("'12'", "12"), ("[a, b]", ["a", "b"]),
])
def test_parse_scalar(raw, expected):
    assert yamlio.parse_scalar(raw) == expected


def test_comment_lines_ignore_hashes_inside_quotes():
    c = yamlio.comment_lines("a: 'x # not a comment'  # real\nb: \"#no\"\n# top\n")
    assert sorted(c) == ["# real", "# top"]


def test_atomic_write_is_private_and_leaves_no_temp(tmp_path):
    p = tmp_path / "d" / "f.yaml"
    yamlio.atomic_write(p, "a: 1\n")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and p.read_text() == "a: 1\n"
    os.chmod(p, 0o640)
    yamlio.atomic_write(p, "a: 2\n")
    assert stat.S_IMODE(p.stat().st_mode) == 0o640           # an existing file keeps its permissions
    assert [x.name for x in p.parent.iterdir()] == ["f.yaml"]


def test_atomic_write_failure_keeps_the_old_file(tmp_path, monkeypatch):
    p = tmp_path / "f.yaml"
    p.write_text("old\n")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        yamlio.atomic_write(p, "new\n")
    monkeypatch.undo()
    assert p.read_text() == "old\n" and [x.name for x in tmp_path.iterdir()] == ["f.yaml"]


# ---------------------------------------------------------------- schemas

def ann(**kw):
    base = {"id": "a1", "match": {"merchant_key": "^X"}, "category": "food.groceries"}
    base.update(kw)
    return base


@pytest.mark.parametrize("bad,why", [
    (ann(match={"merchant": "^X"}), "Extra inputs"),                        # typo: would silently never match
    (ann(match={}), "at least one criterion"),
    (ann(match={"merchant_key": "([unclosed"}), "invalid regular expression"),
    (ann(match={"description": "(?P<"}), "invalid regular expression"),
    (ann(match={"date_from": "2026-02-01", "date_to": "2026-01-01"}), "after date_to"),
    (ann(match={"amount_min": -5, "amount_max": -50}), "greater than amount_max"),
    (ann(match={"weekdays": ["funday"]}), "Input should be"),
    (ann(match={"date_from": "soon"}), "date"),
    (ann(id="Bad Id"), "lowercase"),
    (ann(category=None), "needs an effect"),
    (ann(category="nodot"), "group.leaf"),
    (ann(tags=["One Off"]), "lowercase word"),
    (ann(tag=["x"]), "Extra inputs"),
])
def test_annotation_schema_rejects(bad, why):
    with pytest.raises(Exception) as e:
        schemas.CategorizationFile.model_validate({"annotations": [bad]})
    assert why in str(e.value)


def test_duplicate_ids_are_errors():
    with pytest.raises(Exception) as e:
        schemas.CategorizationFile.model_validate({"annotations": [ann(), ann()]})
    assert "duplicate annotation id" in str(e.value)


def test_liability_schema_enums_and_numbers():
    ok = schemas.Liability.model_validate({"id": "l", "kind": "loa", "monthly_payment": 120.5,
                                           "rate": {"type": "fixed", "nominal": 3}})
    assert ok.monthly_payment == 120.5
    for bad in ({"id": "l", "kind": "boat"}, {"id": "l", "kind": "loa", "monthly_payment": "lots"},
                {"id": "l", "kind": "loa", "rate": {"type": "wobbly"}}, {"id": "l", "kind": "loa", "payment_match": "("},
                {"id": "l", "kind": "loa", "start_date": "2030-01-01", "end_date": "2020-01-01"}):
        with pytest.raises(Exception):
            schemas.Liability.model_validate(bad)


def test_member_role_and_birth_year():
    schemas.Member.model_validate({"id": "a", "name": "A B", "role": "adult", "birth_year": 1980})
    for bad in ({"id": "a", "name": "A", "role": "dog"}, {"id": "a", "name": "A", "role": "child", "birth_year": 1700},
                {"id": "A", "name": "A", "role": "child"}):
        with pytest.raises(Exception):
            schemas.Member.model_validate(bad)


def test_question_consistency_rules():
    schemas.Question.model_validate({"id": "q-1", "question": "x"})
    with pytest.raises(Exception):
        schemas.Question.model_validate({"id": "q-1", "question": "x", "status": "answered"})     # no answer
    with pytest.raises(Exception):
        schemas.Question.model_validate({"id": "q-1", "question": "x", "answer": "y"})            # answer while open
    with pytest.raises(Exception):
        schemas.Question.model_validate({"id": "q-1", "question": "x", "evidence": {"k": {"nested": 1}}})


def test_extra_fields_are_kept_on_assets_but_reported():
    m = schemas.AssetsFile.model_validate({"assets": [{"id": "a", "kind": "other", "custom": 1}]})
    from coach.memory.store import extra_fields
    assert extra_fields(m) == ["assets[a].custom"]


# ---------------------------------------------------------------- the store: reading and addressing

def test_files_skip_templates_and_unknown(store, mem):
    (mem / "liabilities" / "_template.yaml").write_text("id: null\n")
    (mem / "notes.txt").write_text("x")
    f = store.files()
    assert "liabilities/home-loan.yaml" in f and "categorization.yaml" in f
    assert not any("_template" in x or "notes" in x for x in f)


def test_paths_cannot_escape_the_memory_folder(store):
    for bad in ("../x.yaml", "/etc/passwd", "liabilities/../../x"):
        with pytest.raises(MemoryStoreError):
            store.path(bad)


def test_resolve_ref_by_file_or_id_and_ambiguity(store, mem):
    assert store.resolve_ref("assets.yaml") == ("assets.yaml", None)
    assert store.resolve_ref("savings-book") == ("assets.yaml", "savings-book")
    assert store.resolve_ref("home-loan") == ("liabilities/home-loan.yaml", "home-loan")
    (mem / "contracts").mkdir()
    (mem / "contracts" / "x.yaml").write_text("id: savings-book\nkind: other\n")
    with pytest.raises(MemoryStoreError) as e:
        store.resolve_ref("savings-book")
    assert "ambiguous" in str(e.value)
    with pytest.raises(MemoryStoreError):
        store.resolve_ref("nothing-like-this")


def test_event_ids_come_from_headings_and_optional_events_yaml(store, mem):
    assert store.event_ids() == {"kitchen-2026"}
    (mem / "events.yaml").write_text("events:\n  - id: move-2027\n    start: 2027-01-01\n")
    assert store.event_ids() == {"kitchen-2026", "move-2027"}


def test_validate_all_reports_paths_with_ids_and_lines(store, mem):
    (mem / "categorization.yaml").write_text(CATEGORIZATION.replace("'^STREAMBOX'", "'(oops'"))
    issues = store.validate_all()
    assert len(issues) == 1
    i = issues[0]
    assert i.file == "categorization.yaml" and "annotations[streambox-sub].match.merchant_key" in i.path
    assert "invalid regular expression" in i.message and i.line


# ---------------------------------------------------------------- editing

def test_set_changes_only_the_value_and_keeps_every_comment(store, mem):
    before = (mem / "assets.yaml").read_text()
    res = store.set_value("savings-book", "balance", 5200)
    after = (mem / "assets.yaml").read_text()
    assert after == before.replace("balance: 5000", "balance: 5200")
    assert res.changed and "-    balance: 5000" in res.diff and "+    balance: 5200" in res.diff


def test_set_nested_path_and_flow_mapping_style_preserved(store, mem):
    store.set_value("liabilities/home-loan.yaml", "rate.nominal", 1.9)
    text = (mem / "liabilities" / "home-loan.yaml").read_text()
    assert "rate: { type: fixed, nominal: 1.9, taeg: null }" in text
    assert "# from the January statement" in text


def test_set_creates_a_nested_mapping_when_missing(store, mem):
    store.set_value("home-loan", "insurance.provider", "SafeCover")
    assert "provider: SafeCover" in (mem / "liabilities" / "home-loan.yaml").read_text()


def test_set_with_dates_lists_and_null(store, mem):
    store.set_value("savings-book", "as_of", dt.date(2026, 10, 1))
    store.set_value("home-loan", "documents", ["documents/a.pdf"])
    store.set_value("savings-book", "provider", None)
    t = (mem / "assets.yaml").read_text()
    assert "as_of: 2026-10-01" in t and "provider: null" in t
    assert "documents: [documents/a.pdf]" in (mem / "liabilities" / "home-loan.yaml").read_text()


def test_invalid_value_is_refused_and_the_file_is_untouched(store, mem):
    before = (mem / "assets.yaml").read_text()
    with pytest.raises(ValidationFailed) as e:
        store.set_value("savings-book", "balance", "lots")
    assert "balance" in str(e.value)
    with pytest.raises(ValidationFailed):
        store.set_value("savings-book", "as_of", "yesterday")
    assert (mem / "assets.yaml").read_text() == before
    assert not (mem / ".history.git").exists()                # a refused edit records nothing


def test_unknown_field_refused_unless_allowed(store, mem):
    with pytest.raises(MemoryStoreError) as e:
        store.set_value("savings-book", "colour", "red")
    assert "unknown field" in str(e.value)
    store.set_value("savings-book", "colour", "red", allow_new_fields=True)
    assert "colour: red" in (mem / "assets.yaml").read_text()


def test_unknown_category_and_event_are_refused_for_annotations(store):
    with pytest.raises(ValidationFailed) as e:
        store.set_value("streambox-sub", "category", "no.such_category")
    assert "unknown category" in str(e.value)
    with pytest.raises(ValidationFailed) as e:
        store.set_value("streambox-sub", "event", "missing-event")
    assert "not defined" in str(e.value)


def test_dry_run_writes_nothing_and_records_nothing(store, mem):
    before = (mem / "assets.yaml").read_text()
    res = store.set_value("savings-book", "balance", 1, dry_run=True)
    assert res.changed and (mem / "assets.yaml").read_text() == before and res.change_id is None
    assert not (mem / ".history.git").exists()


def test_unset_and_the_comment_guard(store, mem):
    with pytest.raises(MemoryStoreError) as e:
        store.edit("assets.yaml", [{"op": "unset", "path": "savings-book.kind"}], allow_new_fields=True)
    assert "kind" in str(e.value) or "Field required" in str(e.value)
    # removing a key that carries a comment would drop the comment: refused ...
    with pytest.raises(MemoryStoreError) as e:
        store.edit("assets.yaml", [{"op": "unset", "path": "savings-book.liquidity"},
                                   {"op": "unset", "path": "savings-book.provider"},
                                   {"op": "remove", "path": "assets[savings-book]"}])
    assert "comment" in str(e.value)
    # ... unless the user accepts that
    store.edit("assets.yaml", [{"op": "remove", "path": "assets[savings-book]"}], allow_comment_loss=True)
    assert "savings-book" not in (mem / "assets.yaml").read_text()


def test_append_adds_an_item_with_blank_line_and_style(store, mem):
    store.edit("categorization.yaml", [{"op": "append", "path": "annotations", "value": {
        "id": "extra", "match": {"merchant_key": "^ZZZ"}, "category": "food.groceries", "tags": ["one_off"]}}])
    text = (mem / "categorization.yaml").read_text()
    assert text.endswith("\n\n  - id: extra\n    match:\n      merchant_key: ^ZZZ\n    category: food.groceries\n"
                         "    tags: [one_off]\n")
    assert "# --- streaming" in text                           # untouched comments survive


def test_list_item_selector_by_position_and_id(store, mem):
    store.edit("categorization.yaml", [{"op": "set", "path": "annotations[0].note", "value": "changed"}])
    store.edit("categorization.yaml", [{"op": "set", "path": "annotations[streambox-sub].note", "value": "also"}])
    t = (mem / "categorization.yaml").read_text()
    assert "note: changed" in t and "note: also" in t
    with pytest.raises(MemoryStoreError):
        store.edit("categorization.yaml", [{"op": "set", "path": "annotations[nope].note", "value": "x"}])


def test_creating_a_file_and_refusing_to_overwrite(store, mem):
    store.edit("liabilities/car.yaml", [{"op": "create", "value": {"id": "car", "kind": "loa", "monthly_payment": 300}}])
    p = mem / "liabilities" / "car.yaml"
    assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600
    with pytest.raises(MemoryStoreError):
        store.edit("liabilities/car.yaml", [{"op": "create", "value": {"id": "car", "kind": "loa"}}])
    with pytest.raises(ValidationFailed):
        store.edit("liabilities/bad.yaml", [{"op": "create", "value": {"id": "bad", "kind": "yacht"}}])
    assert not (mem / "liabilities" / "bad.yaml").exists()


def test_markdown_files_accept_text_operations_only(store, mem):
    store.edit("profile.md", [{"op": "append_text", "value": "- New fact."}])
    assert (mem / "profile.md").read_text().endswith("\n\n- New fact.\n")
    store.edit("preferences.md", [{"op": "replace_text", "old": "Tone: short", "value": "Tone: warm"}])
    assert "Tone: warm" in (mem / "preferences.md").read_text()
    with pytest.raises(MemoryStoreError):
        store.edit("preferences.md", [{"op": "replace_text", "old": "nowhere", "value": "x"}])
    with pytest.raises(MemoryStoreError):
        store.set_value("profile.md", "x", 1)


def test_the_generated_questions_view_is_not_writable(store, mem):
    (mem / "open-questions.md").write_text("# q\n")
    with pytest.raises(MemoryStoreError) as e:
        store.edit("open-questions.md", [{"op": "append_text", "value": "x"}])
    assert "generated" in str(e.value)


def test_lost_update_is_detected(store, mem):
    old = (mem / "assets.yaml").read_text()
    (mem / "assets.yaml").write_text(old + "\n# edited elsewhere\n")
    with pytest.raises(MemoryStoreError) as e:
        store.write_text("assets.yaml", old.replace("5000", "1"), action="x", expect_old=old)
    assert "changed while" in str(e.value)


def test_concurrent_writers_are_serialised(store, mem):
    errors = []

    def work(i):
        try:
            MemoryStore(mem).edit("assets.yaml", [{"op": "set", "path": f"savings-book.balance", "value": 100 + i}])
        except Exception as e:                                # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(store.history("assets.yaml")) >= 6
    assert yamlio.loads((mem / "assets.yaml").read_text())          # still valid YAML


def test_path_grammar():
    assert [(s.name, s.sels) for s in edit_mod.parse_path("annotations[a-1].match.tags[0]")] == \
        [("annotations", ["a-1"]), ("match", []), ("tags", [0])]
    for bad in ("", "a..b", "a[", "a b"):
        with pytest.raises(edit_mod.EditError):
            edit_mod.parse_path(bad)


def test_a_symlinked_memory_file_keeps_its_link(store, mem, tmp_path):
    target = mem / "elsewhere.yaml"
    target.write_text((mem / "assets.yaml").read_text())
    (mem / "assets.yaml").unlink()
    (mem / "assets.yaml").symlink_to(target)
    store.set_value("savings-book", "balance", 77)
    assert (mem / "assets.yaml").is_symlink() and "balance: 77" in target.read_text()


def test_history_and_backup_dirs_are_private(store, mem):
    store.set_value("savings-book", "balance", 1)
    assert stat.S_IMODE((mem / ".history.git").stat().st_mode) == 0o700
