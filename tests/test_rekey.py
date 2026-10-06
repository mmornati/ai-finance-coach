"""H1: position-based bank references (Fortuneo "<date>T00:00:00-<index>") are not identities.
Transactions are keyed by content; a stored transaction is never overwritten by a different one."""
import json
import shutil

import pytest

from coach import db as dbm
from coach.classify.rules import categorised
from coach.db import connect
from coach.ingest.fingerprint import is_position_ref, remap_warnings
from coach.ingest.sync import sync_account, tx_key
from helpers import FakeClient, add_bank, add_tx, eb_tx


def pos(date, i):
    return f"{date}T00:00:00-{i}"


def feed(*txs):
    return {"acc1": [{"transactions": list(txs)}]}


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Fortuneo", "FR", [("acc1", "FR76", "M OU MME TESTOWNER ALICE")])
    return c


def run(con, *txs, full=True):
    fc = FakeClient(feed(*txs))
    return sync_account(con, fc, "acc1", full=full, force=True, out=lambda *_: None)


def contents(con):
    return sorted(con.execute("SELECT booking_date, amount, description FROM transactions").fetchall())


def test_position_reference_detection():
    assert is_position_ref("2025-12-11T00:00:00-3") and is_position_ref("2025-12-11T00:00:00-0")
    assert not is_position_ref("627040216149462026-07-07-07.55.32.639517")
    assert not is_position_ref("67013a28-88cf-a868-a276-dfa2dcbf1691") and not is_position_ref(None)
    assert not is_position_ref("2025-12-11T10:00:00-3")


def test_index_shift_does_not_overwrite_or_duplicate(con):
    d = "2026-03-02"
    A = eb_tx(pos(d, 0), d, -10.0, "CARTE 01/03 ALPHA PARIS")
    B = eb_tx(pos(d, 1), d, -20.0, "CARTE 01/03 BRAVO LYON")
    run(con, A, B)
    assert len(contents(con)) == 2
    keys_before = {r[0]: (r[1], r[2]) for r in con.execute("SELECT tx_key, amount, description FROM transactions")}
    # a late / back-dated booking appears in the middle of the day: B moves from index 1 to index 2
    C = eb_tx(pos(d, 1), d, -5.0, "CARTE 01/03 CHARLIE NICE")
    B2 = eb_tx(pos(d, 2), d, -20.0, "CARTE 01/03 BRAVO LYON")
    res = run(con, A, C, B2)
    assert res["new"] == 1 and res["conflicts"] == []
    rows = {r[0]: (r[1], r[2]) for r in con.execute("SELECT tx_key, amount, description FROM transactions")}
    assert len(rows) == 3
    for k, v in keys_before.items():                 # every stored row kept exactly its own content
        assert rows[k] == v
    assert sorted(v[0] for v in rows.values()) == [-20.0, -10.0, -5.0]


def test_resync_is_idempotent_and_identical_transactions_get_distinct_keys(con):
    d = "2026-03-02"
    coffee = lambda i: eb_tx(pos(d, i), d, -2.5, "CARTE 01/03 COFFEE SHOP")
    r1 = run(con, coffee(0), coffee(1), eb_tx(pos(d, 2), d, -9.0, "CARTE 01/03 BOOKS"))
    assert r1["new"] == 3
    keys = sorted(r[0] for r in con.execute("SELECT tx_key FROM transactions"))
    assert len(set(keys)) == 3 and all(":cfp:" in k for k in keys)
    for _ in range(3):
        again = run(con, coffee(0), coffee(1), eb_tx(pos(d, 2), d, -9.0, "CARTE 01/03 BOOKS"))
        assert again["new"] == 0
    assert sorted(r[0] for r in con.execute("SELECT tx_key FROM transactions")) == keys
    # a third identical coffee booked later is a new row, the first two keep their keys
    run(con, coffee(0), coffee(1), coffee(2), eb_tx(pos(d, 3), d, -9.0, "CARTE 01/03 BOOKS"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4
    assert set(keys) <= {r[0] for r in con.execute("SELECT tx_key FROM transactions")}


def test_user_data_follows_the_content_key_not_the_position(con):
    d = "2026-03-02"
    A = eb_tx(pos(d, 0), d, -10.0, "CARTE 01/03 ALPHA PARIS")
    B = eb_tx(pos(d, 1), d, -20.0, "CARTE 01/03 BRAVO LYON")
    run(con, A, B)
    kb = con.execute("SELECT tx_key FROM transactions WHERE amount=-20").fetchone()[0]
    con.execute("INSERT INTO tx_overrides VALUES (?,?,?)", (kb, "health.doctors", "mine"))
    con.commit()
    run(con, A, eb_tx(pos(d, 1), d, -5.0, "CARTE 01/03 CHARLIE"), eb_tx(pos(d, 2), d, -20.0, "CARTE 01/03 BRAVO LYON"))
    assert con.execute("SELECT amount FROM transactions WHERE tx_key=?", (kb,)).fetchone()[0] == -20.0
    assert con.execute("SELECT category FROM tx_overrides").fetchone()[0] == "health.doctors"


def test_conflicting_reference_keeps_both_transactions(con):
    d = "2026-03-02"
    run(con, eb_tx("STABLE-REF-1", d, -10.0, "CARTE 01/03 ALPHA PARIS"))
    out = []
    fc = FakeClient(feed(eb_tx("STABLE-REF-1", d, -77.0, "CARTE 01/03 SOMETHING ELSE")))
    res = sync_account(con, fc, "acc1", full=True, force=True, out=out.append)
    assert len(res["conflicts"]) == 1 and any("different content" in o for o in out)
    assert contents(con) == [(d, -77.0, "CARTE 01/03 SOMETHING ELSE"), (d, -10.0, "CARTE 01/03 ALPHA PARIS")]
    assert con.execute("SELECT amount FROM transactions WHERE tx_key=?", ("acc1:ref:STABLE-REF-1",)).fetchone()[0] == -10.0
    assert "key_conflicts=1" in con.execute("SELECT note FROM sync_log ORDER BY ROWID DESC").fetchone()[0]
    # idempotent: syncing the same conflicting feed again creates nothing new
    fc = FakeClient(feed(eb_tx("STABLE-REF-1", d, -77.0, "CARTE 01/03 SOMETHING ELSE")))
    sync_account(con, fc, "acc1", full=True, force=True, out=lambda *_: None)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def test_same_content_with_same_stable_reference_is_a_plain_update(con):
    d = "2026-03-02"
    run(con, eb_tx("R9", d, -10.0, "CARTE 01/03 ALPHA PARIS"))
    res = run(con, eb_tx("R9", d, -10.0, "CARTE 01/03 ALPHA PARIS"))
    assert res["new"] == 0 and res["conflicts"] == []


# ---------------------------------------------------------------- the data migration

def legacy_db(cfg, tmp_path):
    """A DB at schema version 5 holding rows keyed the old way."""
    old = tmp_path / "mig5"
    old.mkdir()
    for m in dbm.available_migrations():
        if m.version <= 5:
            shutil.copy(m.path, old / m.path.name)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    c = dbm.connect(cfg, insecure=True, create=True, migrate=False)
    dbm.apply_migrations(c, old, safety_copy=False)
    add_bank(c, "s1", "Fortuneo", "FR", [("acc1", "FR76", "M OU MME TESTOWNER ALICE")])
    return c


def add_legacy(c, i, d, amount, desc, ref_i):
    ref = pos(d, ref_i)
    key = f"acc1:ref:{ref}"
    c.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, amount, "
              "currency, counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) "
              "VALUES (?,?,?,?,?,?,?,?,?,NULL,'',?,?,?)",
              (key, "acc1", ref, d, d, amount, "EUR", "", desc, json.dumps({"i": i}), "2026-01-01", "2026-01-02"))
    return key


def test_rekey_migration_is_lossless_and_follows_every_table(cfg, tmp_path):
    c = legacy_db(cfg, tmp_path)
    d = "2026-03-02"
    k1 = add_legacy(c, 1, d, -10.0, "CARTE 01/03 ALPHA PARIS", 0)
    k2 = add_legacy(c, 2, d, -2.5, "CARTE 01/03 COFFEE", 1)
    k3 = add_legacy(c, 3, d, -2.5, "CARTE 01/03 COFFEE", 2)         # identical to k2
    k4 = add_legacy(c, 4, "2026-03-03", 200.0, "VIR MME X", 0)
    c.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, amount, currency, "
              "description, raw) VALUES ('acc1:ref:STABLE', 'acc1', 'STABLE', ?, -1, 'EUR', 'X', '{}')", (d,))
    for k in (k1, k2, k3, k4):
        c.execute("INSERT INTO tx_enriched VALUES (?,?,?,?,?,NULL,NULL)", (k, "card", None, "m", "M"))
    c.execute("INSERT INTO tx_overrides VALUES (?, 'health.doctors', 'n')", (k1,))
    c.execute("INSERT INTO transfer_links(out_tx_key, in_tx_key, amount, confidence, method, created_at) "
              "VALUES (?,?,?,?,?,?)", (k1, k4, 10.0, 0.9, "auto", "t"))
    c.execute("INSERT INTO transfer_rejections VALUES (?,?,?)", (k2, k4, "t"))
    c.execute("INSERT INTO imports(account_uid, imported_at) VALUES ('acc1','t')")
    c.execute("INSERT INTO import_rows VALUES (?,1)", (k3,))
    c.commit()
    before = c.execute("SELECT account_uid, entry_reference, booking_date, value_date, amount, currency, description, "
                       "raw, first_seen, last_seen FROM transactions ORDER BY entry_reference, raw").fetchall()
    c.close()

    c = dbm.connect(cfg, insecure=True)           # applies 0006 + 0007
    after = c.execute("SELECT account_uid, entry_reference, booking_date, value_date, amount, currency, description, "
                      "raw, first_seen, last_seen FROM transactions ORDER BY entry_reference, raw").fetchall()
    assert after == before                          # nothing but the key changed
    keys = {r[0] for r in c.execute("SELECT tx_key FROM transactions")}
    assert "acc1:ref:STABLE" in keys                # stable references are untouched
    assert sum(":cfp:" in k for k in keys) == 4 and not keys & {k1, k2, k3, k4}
    remap = dict(c.execute("SELECT old_key, new_key FROM tx_key_remap"))
    assert set(remap) == {k1, k2, k3, k4} and len(set(remap.values())) == 4
    assert {r[0] for r in c.execute("SELECT tx_key FROM tx_enriched")} == {remap[k] for k in (k1, k2, k3, k4)}
    assert c.execute("SELECT tx_key FROM tx_overrides").fetchone()[0] == remap[k1]
    assert c.execute("SELECT out_tx_key, in_tx_key FROM transfer_links").fetchone() == (remap[k1], remap[k4])
    assert c.execute("SELECT out_tx_key, in_tx_key FROM transfer_rejections").fetchone() == (remap[k2], remap[k4])
    assert c.execute("SELECT tx_key FROM import_rows").fetchone()[0] == remap[k3]
    # the two identical rows got distinct keys, in the order of their old index
    assert remap[k2] != remap[k3]
    # idempotent
    assert dbm.apply_migrations(c) == []
    snap = c.execute("SELECT tx_key FROM transactions ORDER BY tx_key").fetchall()
    c.close()
    c = dbm.connect(cfg, insecure=True)
    assert c.execute("SELECT tx_key FROM transactions ORDER BY tx_key").fetchall() == snap


def test_migrated_keys_equal_the_keys_a_fresh_sync_computes(cfg, tmp_path):
    c = legacy_db(cfg, tmp_path)
    d = "2026-03-02"
    add_legacy(c, 1, d, -10.0, "CARTE 01/03 ALPHA PARIS", 0)
    add_legacy(c, 2, d, -2.5, "CARTE 01/03 COFFEE", 1)
    add_legacy(c, 3, d, -2.5, "CARTE 01/03 COFFEE", 2)
    c.commit()
    c.close()
    c = dbm.connect(cfg, insecure=True)
    before = sorted(r[0] for r in c.execute("SELECT tx_key FROM transactions"))
    # the bank now lists the same day with shifted positions: nothing is added, nothing changes
    res = run(c, eb_tx(pos(d, 0), d, -2.5, "CARTE 01/03 COFFEE"), eb_tx(pos(d, 1), d, -10.0, "CARTE 01/03 ALPHA PARIS"),
              eb_tx(pos(d, 2), d, -2.5, "CARTE 01/03 COFFEE"))
    assert res["new"] == 0
    assert sorted(r[0] for r in c.execute("SELECT tx_key FROM transactions")) == before


def test_memory_annotation_with_an_old_key_still_matches_and_is_reported(cfg, tmp_path):
    c = legacy_db(cfg, tmp_path)
    k = add_legacy(c, 1, "2026-03-02", -10.0, "CARTE 01/03 ALPHA PARIS", 0)
    c.execute("INSERT INTO tx_enriched VALUES (?,?,?,?,?,NULL,NULL)", (k, "card", None, "ALPHA PARIS", "ALPHA PARIS"))
    c.commit()
    c.close()
    c = dbm.connect(cfg, insecure=True)
    ann = [{"id": "note-1", "match": {"tx_keys": [k]}, "category": "health.doctors", "tags": ["one_off"]}]
    t = next(categorised(c, annotations=ann))
    assert (t["category"], t["source"]) == ("health.doctors", "memory")
    (cfg.memory_dir / "categorization.yaml").write_text(
        f"annotations:\n  - id: note-1\n    match: {{tx_keys: ['{k}']}}\n    category: health.doctors\n")
    w = remap_warnings(c, cfg.memory_dir)
    assert len(w) == 1 and "note-1" in w[0] and k in w[0]
    assert remap_warnings(c, None) == []


def test_tx_key_function_still_builds_reference_keys_for_stable_references():
    assert tx_key("a", {"entry_reference": "R1"}) == "a:ref:R1"
    k = tx_key("a", {"entry_reference": pos("2026-01-01", 3), "booking_date": "2026-01-01",
                     "transaction_amount": {"amount": "5.00", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                     "remittance_information": ["X"]})
    assert ":cfp:" in k


def test_account_merge_keeps_content_keys_valid_and_moves_splits(con):
    from coach.ingest.accounts import merge_accounts
    add_bank(con, "s2", "Fortuneo", "FR", [("acc2", "FR76", "M OU MME TESTOWNER ALICE")])
    d = "2026-03-02"
    fc = FakeClient({"acc2": [{"transactions": [eb_tx(pos(d, 0), d, -10.0, "CARTE 01/03 ALPHA PARIS")]}]})
    sync_account(con, fc, "acc2", full=True, force=True, out=lambda *_: None)
    k_new = con.execute("SELECT tx_key FROM transactions").fetchone()[0]
    con.execute("INSERT INTO tx_splits(tx_key, amount, category) VALUES (?, -4, 'food.groceries')", (k_new,))
    con.execute("INSERT INTO tx_splits(tx_key, amount, category) VALUES (?, -6, 'housing.furniture')", (k_new,))
    con.commit()
    merge_accounts(con, "acc1", "acc2")
    k_merged = con.execute("SELECT tx_key FROM transactions").fetchone()[0]
    assert k_merged.startswith("acc1:cfp:") and k_merged.split(":", 1)[1] == k_new.split(":", 1)[1]
    assert {r[0] for r in con.execute("SELECT tx_key FROM tx_splits")} == {k_merged}
    # a fresh sync of the merged account computes exactly that key: no duplicate
    fc = FakeClient({"acc2": [{"transactions": [eb_tx(pos(d, 0), d, -10.0, "CARTE 01/03 ALPHA PARIS")]}]})  # bank uid
    assert sync_account(con, fc, "acc1", full=True, force=True, out=lambda *_: None)["new"] == 0


def test_db_migrate_command_on_an_encrypted_db_takes_its_safety_copy_with_the_key(cfg, tmp_path, db_key):
    from argparse import Namespace
    from coach.cli import cmd_db_migrate
    old = tmp_path / "mig5"
    old.mkdir()
    for m in dbm.available_migrations():
        if m.version <= 5:
            shutil.copy(m.path, old / m.path.name)
    c = dbm.connect(cfg, create=True, migrate=False)
    dbm.apply_migrations(c, old, safety_copy=False)
    c.execute("INSERT INTO sessions(session_id, aspsp_name) VALUES ('s','x')")
    c.commit()
    c.close()
    cmd_db_migrate(Namespace(insecure=False, create=False), cfg)
    assert list(cfg.db_path.parent.glob("*.pre-migrate-*.bak"))
    c = dbm.connect(cfg)
    assert [v for v, _, _ in dbm.status(c)["applied"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
