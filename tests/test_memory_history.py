"""E3-4 change history (a private git repo in memory/.history.git), diff, revert, and proposals (E6-5)."""
import json
import os
import stat
import subprocess

import pytest

from coach.memory import proposals as P
from coach.memory.history import HistoryError
from coach.memory.store import MemoryStore, MemoryStoreError, ValidationFailed
from memhelpers import make_memory


@pytest.fixture
def mem(tmp_path):
    return make_memory(tmp_path / "memory")


@pytest.fixture
def store(mem):
    return MemoryStore(mem)


def git(mem, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", f"--git-dir={mem / '.history.git'}", f"--work-tree={mem}", *args],
                          capture_output=True, text=True, env=env).stdout


# ---------------------------------------------------------------- history

def test_history_starts_on_the_first_write_with_the_existing_files_as_baseline(store, mem):
    assert store.history() == []
    assert not (mem / ".history.git").exists()
    res = store.set_value("savings-book", "balance", 5100, reason="statement of March")
    log = store.history()
    assert [c.subject for c in log] == [log[0].subject, "coach: initial snapshot of the memory folder"]
    assert log[0].subject == "coach: set assets.yaml"
    assert log[0].id == res.change_id and log[0].reason == "statement of March" and log[0].source == "cli"
    assert log[0].files == ["assets.yaml"]
    # the baseline holds the files as they were BEFORE the first edit
    assert "balance: 5000" in git(mem, "show", f"{log[1].id}:assets.yaml")


def test_commit_message_format(store):
    store.set_value("savings-book", "balance", 1)
    assert store.history()[0].subject == "coach: set assets.yaml"


def test_history_per_file_and_limit(store):
    store.set_value("savings-book", "balance", 1)
    store.set_value("home-loan", "outstanding", 170000)
    assert [c.files for c in store.history("assets.yaml", 5)][0] == ["assets.yaml"]
    assert [c.subject for c in store.history("liabilities/home-loan.yaml")][0] == "coach: set liabilities/home-loan.yaml"
    assert len(store.history(limit=1)) == 1


def test_hand_edits_are_snapshotted_before_the_next_change_and_shown_by_diff(store, mem):
    store.set_value("savings-book", "balance", 1)
    (mem / "profile.md").write_text((mem / "profile.md").read_text() + "- hand edit\n")
    assert "+- hand edit" in store.diff()
    assert "profile.md" in store.diff(file="profile.md")
    store.set_value("savings-book", "balance", 2)
    subjects = [c.subject for c in store.history()]
    assert any("snapshot external edits (profile.md)" in s for s in subjects)
    assert store.diff().strip() == ""                       # nothing unrecorded any more


def test_diff_of_a_change_shows_its_patch(store):
    r = store.set_value("savings-book", "balance", 77)
    d = store.diff(r.change_id)
    assert "-    balance: 5000" in d and "+    balance: 77" in d


def test_revert_restores_the_file_as_a_new_change(store, mem):
    r1 = store.set_value("savings-book", "balance", 1)
    store.set_value("home-loan", "outstanding", 1)
    ch = store.revert(r1.change_id)
    assert "balance: 5000" in (mem / "assets.yaml").read_text()
    assert "outstanding: 1" in (mem / "liabilities" / "home-loan.yaml").read_text()   # other change kept
    assert store.history()[0].subject.startswith(f"coach: revert {r1.change_id}") and store.history()[0].id == ch.id


def test_revert_snapshots_hand_edits_first_so_nothing_is_lost(store, mem):
    r1 = store.set_value("savings-book", "balance", 1)
    (mem / "profile.md").write_text("hand edit\n")
    store.revert(r1.change_id)
    assert (mem / "profile.md").read_text() == "hand edit\n"
    assert any("snapshot external edits" in c.subject for c in store.history())


def test_revert_conflict_leaves_everything_as_it_was(store, mem):
    r1 = store.set_value("savings-book", "balance", 1)
    store.set_value("savings-book", "balance", 2)              # later change touches the same line
    before = (mem / "assets.yaml").read_text()
    with pytest.raises(HistoryError) as e:
        store.revert(r1.change_id)
    assert "same lines" in str(e.value)
    assert (mem / "assets.yaml").read_text() == before and git(mem, "status", "--porcelain").strip() == ""


def test_the_first_snapshot_and_unknown_ids_cannot_be_reverted(store):
    store.set_value("savings-book", "balance", 1)
    first = store.history()[-1].id
    with pytest.raises(HistoryError):
        store.revert(first)
    with pytest.raises(HistoryError):
        store.revert("deadbeef")
    with pytest.raises(HistoryError):
        store.revert("not-an-id")


def test_revert_that_would_leave_an_invalid_file_is_refused(store, mem):
    r1 = store.edit("liabilities/car.yaml", [{"op": "create", "value": {"id": "car", "kind": "loa"}}])
    # make the schema stricter than the old content by editing history behind the store's back
    (mem / "liabilities" / "car.yaml").write_text("id: car\nkind: loa\nmonthly_payment: 5\n")
    store.set_value("liabilities/car.yaml", "monthly_payment", 6)
    r3 = store.set_value("liabilities/car.yaml", "monthly_payment", 7)
    store.revert(r3.change_id)                                  # a normal revert works
    assert "monthly_payment: 6" in (mem / "liabilities" / "car.yaml").read_text()


def test_revert_of_questions_regenerates_the_markdown_view(store, mem):
    from coach.memory import questions as q
    q.add(store, "What is X?", topic="T")
    r = q.add(store, "What is Y?", topic="T")
    change = store.history()[0].id
    store.revert(change)
    assert "What is Y?" not in (mem / "open-questions.yaml").read_text()
    assert "What is Y?" not in (mem / "open-questions.md").read_text()
    assert "What is X?" in (mem / "open-questions.md").read_text()


def test_the_repository_is_private_and_isolated_from_any_parent_project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    mem = make_memory(proj / "memory")
    # hostile environment: the user's own git variables point somewhere else
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    monkeypatch.setenv("GIT_DIR", str(other))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    st = MemoryStore(mem)
    st.set_value("savings-book", "balance", 1)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert (mem / ".history.git" / "HEAD").exists() and not (mem / ".git").exists()
    assert subprocess.run(["git", "log", "--oneline"], cwd=proj, capture_output=True, text=True).returncode != 0 or \
        "snapshot" not in subprocess.run(["git", "log", "--oneline"], cwd=proj, capture_output=True, text=True).stdout
    assert subprocess.run(["git", "--git-dir", str(other), "log"], capture_output=True, text=True).returncode != 0


def test_no_remote_is_ever_configured(store, mem):
    store.set_value("savings-book", "balance", 1)
    assert git(mem, "remote").strip() == ""
    assert "push" not in open(mem / ".history.git" / "config").read()


def test_documents_proposals_and_backups_are_not_versioned(store, mem):
    (mem / "documents").mkdir()
    (mem / "documents" / "x.pdf").write_bytes(b"pdf")
    (mem / ".proposals").mkdir()
    (mem / ".proposals" / "p.json").write_text("{}")
    (mem / ".backups").mkdir()
    (mem / ".backups" / "b").write_text("x")
    store.set_value("savings-book", "balance", 1)
    tracked = git(mem, "ls-files").split()
    assert "assets.yaml" in tracked
    assert not any(t.startswith(("documents", ".proposals", ".backups", ".lock")) for t in tracked)


def test_history_can_be_switched_off(mem):
    st = MemoryStore(mem, history=False)
    r = st.set_value("savings-book", "balance", 3)
    assert r.change_id is None and "balance: 3" in (mem / "assets.yaml").read_text()
    assert not (mem / ".history.git").exists()


def test_without_git_the_write_is_refused_clearly(mem, monkeypatch):
    monkeypatch.setattr("coach.memory.store.git_available", lambda: False)
    with pytest.raises(MemoryStoreError) as e:
        MemoryStore(mem).set_value("savings-book", "balance", 3)
    assert "git is not installed" in str(e.value) and "history = false" in str(e.value)
    assert "balance: 5000" in (mem / "assets.yaml").read_text()


# ---------------------------------------------------------------- proposals (E6-5)

def test_creating_a_proposal_never_touches_the_target_file(store, mem):
    before = (mem / "assets.yaml").read_text()
    p = P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 6000}],
                 "the user said so", "coach-llm")
    assert (mem / "assets.yaml").read_text() == before
    assert not (mem / ".history.git").exists()                # not even a history entry
    f = mem / ".proposals" / f"{p.id}.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    d = json.loads(f.read_text())
    assert d["status"] == "pending" and d["source"] == "coach-llm" and d["reason"] == "the user said so"
    assert d["file"] == "assets.yaml" and d["ops"][0]["op"] == "set"
    assert d["diff"] == "" and d["changes"] == []                       # display fields are NOT stored (never trusted)
    fresh = P.describe(store, p)
    assert "+    balance: 6000" in fresh["diff"] and fresh["applicable"]
    assert fresh["changes"][0] == {"op": "set", "path": "savings-book.balance", "old": 5000, "new": 6000, "snippet": None,
                                  "suspicious": False}


def test_invalid_proposals_are_rejected_at_creation(store):
    for ops in ([{"op": "set", "path": "savings-book.balance", "value": "lots"}],
                [{"op": "set", "path": "ghost.balance", "value": 1}],
                [{"op": "frobnicate", "path": "x"}],
                []):
        with pytest.raises((ValidationFailed, MemoryStoreError, P.ProposalError)):
            P.create(store, "assets.yaml", ops, "r", "coach-llm")
    with pytest.raises(P.ProposalError):
        P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 1}], "  ", "x")
    with pytest.raises(P.ProposalError):                       # changes nothing
        P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 5000}], "r", "x")
    assert P.listing(store) == []


def test_accept_applies_validates_and_records_in_the_history(store, mem):
    p = P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 6000},
                                        {"op": "set", "path": "savings-book.as_of", "value": "2026-10-01"}],
                 "statement", "doc-extract:doc-1")
    done, res = P.accept(store, p.id, confirmed=True)
    text = (mem / "assets.yaml").read_text()
    assert "balance: 6000" in text and "as_of: 2026-10-01" in text and "'2026-10-01'" not in text
    assert done.status == "accepted" and done.change_id == res.change_id
    log = store.history()[0]
    assert log.source == "cli" and f"proposed by doc-extract:doc-1" in log.reason and "statement" in log.reason
    assert f"accept {p.id}" in log.subject
    assert P.listing(store) == [] and len(P.listing(store, "all")) == 1
    with pytest.raises(P.ProposalError):
        P.accept(store, p.id, confirmed=True)


def test_reject_closes_without_writing(store, mem):
    before = (mem / "assets.yaml").read_text()
    p = P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 1}], "r", "coach-llm")
    P.reject(store, p.id, "wrong account")
    assert (mem / "assets.yaml").read_text() == before
    assert P.get(store, p.id).status == "rejected" and P.get(store, p.id).note == "wrong account"
    with pytest.raises(P.ProposalError):
        P.accept(store, p.id, confirmed=True)


def test_accept_revalidates_against_the_file_as_it_is_now(store, mem):
    p = P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 1}], "r", "coach-llm")
    store.edit("assets.yaml", [{"op": "remove", "path": "assets[savings-book]"}], allow_comment_loss=True)
    with pytest.raises(P.ProposalError) as e:
        P.accept(store, p.id, confirmed=True)
    assert "cannot apply" in str(e.value)
    assert P.get(store, p.id).status == "pending"


def test_proposal_ids_are_validated(store):
    for bad in ("../x", "p-1", "x"):
        with pytest.raises(P.ProposalError):
            P.get(store, bad)


def test_proposals_can_append_create_and_extend_markdown(store, mem):
    a = P.create(store, "categorization.yaml", [{"op": "append", "path": "annotations", "value": {
        "id": "new-one", "match": {"merchant_key": "^NEW"}, "category": "food.groceries"}}], "r", "coach-llm")
    b = P.create(store, "liabilities/new.yaml", [{"op": "create", "value": {"id": "new", "kind": "bnpl"}}], "r", "coach-llm")
    c = P.create(store, "profile.md", [{"op": "append_text", "value": "- fact"}], "r", "coach-llm")
    for p in (a, b, c):
        P.accept(store, p.id, confirmed=True)
    assert "new-one" in (mem / "categorization.yaml").read_text() and (mem / "liabilities" / "new.yaml").exists()
    assert (mem / "profile.md").read_text().rstrip().endswith("- fact")


def test_the_llm_facing_api_cannot_write(store, mem):
    """The only function a coach/LLM integration calls is proposals.create; it must not alter any file."""
    import hashlib

    def snapshot():
        return {str(p.relative_to(mem)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in mem.rglob("*") if p.is_file() and ".proposals" not in p.parts}
    before = snapshot()
    P.create(store, "assets.yaml", [{"op": "set", "path": "savings-book.balance", "value": 9}], "r", "coach-llm")
    P.create(store, "profile.md", [{"op": "append_text", "value": "x"}], "r", "coach-llm")
    assert snapshot() == before
