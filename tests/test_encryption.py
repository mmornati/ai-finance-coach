import pytest

from coach import db as dbm
from coach.secrets import SecretNotFound
from helpers import make_prototype_db


@pytest.fixture
def plain(cfg):
    cfg.db_path.parent.mkdir(parents=True)
    return make_prototype_db(cfg.db_path)


def test_plaintext_db_is_refused_by_default(cfg, plain, db_key):
    with pytest.raises(dbm.PlaintextDatabaseError, match="Refusing to open plaintext"):
        dbm.connect(cfg)
    with pytest.raises(dbm.PlaintextDatabaseError, match="--insecure"):
        dbm.connect(cfg, migrate=False)


def test_insecure_flag_allows_plaintext(cfg, plain):
    con = dbm.connect(cfg, insecure=True)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7


def test_config_flag_allows_plaintext(cfg, plain):
    cfg.insecure_plaintext_db = True
    assert dbm.connect(cfg).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7


def test_new_db_is_created_encrypted_and_needs_a_key(cfg, db_key):
    con = dbm.connect(cfg, create=True)
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES ('s','B','FR','t')")
    con.commit()
    con.close()
    assert dbm.is_plaintext(cfg.db_path) is False
    assert b"SQLite format" not in cfg.db_path.read_bytes()[:16]
    assert dbm.connect(cfg).execute("SELECT state FROM pending_auth").fetchone() == ("s",)


def test_new_db_without_key_fails_with_helpful_message(cfg):
    with pytest.raises(SecretNotFound, match="COACH_DB_KEY"):
        dbm.connect(cfg, create=True)


def test_encrypt_roundtrip(cfg, plain, db_key):
    before = dbm.table_counts(dbm._open(plain, None))
    path, bak = dbm.encrypt_database(cfg)
    assert path == cfg.db_path and bak.name == "finance.db.plaintext.bak"
    assert dbm.is_plaintext(bak) is True                       # plaintext kept for the user to check
    assert dbm.is_plaintext(path) is False
    assert b"RELAY" not in path.read_bytes() and b"WERO" not in path.read_bytes()
    con = dbm.connect(cfg)                                     # no --insecure needed any more
    assert dbm.table_counts(con, list(before)) == before
    assert con.execute("SELECT state FROM pending_auth ORDER BY state").fetchall() == [("state-aaa",), ("state-bbb",)]
    assert con.execute("SELECT 'ABC' REGEXP '^a'").fetchone()[0] == 1   # REGEXP works under SQLCipher


def test_wrong_key_is_rejected(cfg, plain, db_key, monkeypatch):
    dbm.encrypt_database(cfg)
    monkeypatch.setenv("COACH_DB_KEY", "another-key")
    with pytest.raises(dbm.WrongKeyError):
        dbm.connect(cfg)


def test_encrypt_refuses_already_encrypted_and_existing_bak(cfg, plain, db_key):
    dbm.encrypt_database(cfg)
    with pytest.raises(dbm.EncryptionError, match="already encrypted"):
        dbm.encrypt_database(cfg)
    # restore a plaintext file next to an existing .bak
    make_prototype_db(cfg.db_path.with_name("other.db"))
    with pytest.raises(dbm.EncryptionError, match="already exists"):
        (cfg.db_path.with_name("other.db.plaintext.bak")).write_text("x")
        dbm.encrypt_database(cfg, path=cfg.db_path.with_name("other.db"))


def test_encrypt_without_key_fails_and_leaves_file_untouched(cfg, plain):
    raw = plain.read_bytes()
    with pytest.raises(SecretNotFound):
        dbm.encrypt_database(cfg)
    assert plain.read_bytes() == raw and not list(plain.parent.glob("*.bak"))


def test_key_with_quotes_works(cfg, plain, monkeypatch):
    monkeypatch.setenv("COACH_DB_KEY", "it's \"quoted\"; DROP")
    dbm.encrypt_database(cfg)
    assert dbm.connect(cfg).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
