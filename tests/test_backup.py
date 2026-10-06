import io
import tarfile
from datetime import datetime, timedelta

import pytest

from coach import backup as bk
from coach import db as dbm
from helpers import make_prototype_db

KEY = "backup-test-key"


def same_content(a, b):
    """Backups hold an SQLite-API snapshot (not the raw file bytes): compare logical content."""
    ca, cb = dbm._open(a, None), dbm._open(b, None)
    ta = {t: sorted(ca.execute(f"SELECT * FROM {t}").fetchall()) for t in dbm.user_tables(ca)}
    tb = {t: sorted(cb.execute(f"SELECT * FROM {t}").fetchall()) for t in dbm.user_tables(cb)}
    return ta == tb


@pytest.fixture
def populated(cfg):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    (cfg.memory_dir / "categorization.yaml").write_text("annotations: []\n")
    (cfg.memory_dir / "liabilities").mkdir()
    (cfg.memory_dir / "liabilities" / "loan.yaml").write_text("principal: 1000\n")
    return cfg


def test_backup_restore_roundtrip(populated, tmp_path):
    cfg = populated
    path, pruned = bk.create_backup(cfg, key=KEY)
    assert path.parent == cfg.backup_dir and path.name.startswith("coach-backup-") and path.suffix == ".enc"
    blob = path.read_bytes()
    assert blob.startswith(bk.MAGIC) and b"SQLite format" not in blob and b"categorization" not in blob
    out = tmp_path / "restored"
    files = bk.restore_backup(path, out, key=KEY)
    assert same_content(out / "finance.db", cfg.db_path)
    assert (out / "memory" / "categorization.yaml").read_text() == "annotations: []\n"
    assert (out / "memory" / "liabilities" / "loan.yaml").read_text() == "principal: 1000\n"
    assert len(files) == 3
    # restored DB is a usable database
    assert dbm.table_counts(dbm._open(out / "finance.db", None))["transactions"] == 7


def test_each_backup_uses_fresh_salt_and_nonce(populated):
    a, _ = bk.create_backup(populated, key=KEY)
    b, _ = bk.create_backup(populated, key=KEY)
    assert a != b
    assert a.read_bytes()[6:34] != b.read_bytes()[6:34]


def test_wrong_key_and_tampering_are_detected(populated, tmp_path):
    path, _ = bk.create_backup(populated, key=KEY)
    with pytest.raises(bk.BackupError, match="wrong backup_key"):
        bk.restore_backup(path, tmp_path / "x", key="nope")
    data = bytearray(path.read_bytes())
    data[-5] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(bk.BackupError):
        bk.restore_backup(path, tmp_path / "x", key=KEY)
    assert not (tmp_path / "x").exists()
    path.write_bytes(b"garbage")
    with pytest.raises(bk.BackupError, match="bad header"):
        bk.restore_backup(path, tmp_path / "x", key=KEY)


def test_restore_never_overwrites_without_force(populated, tmp_path):
    path, _ = bk.create_backup(populated, key=KEY)
    out = tmp_path / "out"
    out.mkdir()
    (out / "finance.db").write_text("precious")
    with pytest.raises(bk.BackupError, match="Refusing to overwrite"):
        bk.restore_backup(path, out, key=KEY)
    assert (out / "finance.db").read_text() == "precious"
    assert not (out / "memory").exists()                      # nothing partially extracted
    bk.restore_backup(path, out, force=True, key=KEY)
    assert same_content(out / "finance.db", populated.db_path)


def test_retention_keeps_newest_n(populated):
    base = datetime(2026, 1, 1, 3, 0, 0)
    created = [bk.create_backup(populated, now=base + timedelta(days=i), key=KEY)[0] for i in range(5)]
    left = bk.list_backups(populated.backup_dir)
    assert len(left) == 3                                      # fixture retention = 3
    assert left == created[2:]


def test_same_second_backups_do_not_collide(populated):
    now = datetime(2026, 1, 1, 3, 0, 0)
    a, _ = bk.create_backup(populated, now=now, key=KEY)
    b, _ = bk.create_backup(populated, now=now, key=KEY)
    assert a != b and a.exists() and b.exists()


def test_path_traversal_in_archive_is_rejected(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    arc = tmp_path / "evil.tar.enc"
    arc.write_bytes(bk.encrypt_bytes(buf.getvalue(), KEY))
    with pytest.raises(bk.BackupError, match="Unsafe path"):
        bk.restore_backup(arc, tmp_path / "dest", key=KEY)
    assert not (tmp_path / "evil.txt").exists()


def test_backup_key_comes_from_secrets(populated, monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_BACKUP_KEY", "from-env")
    path, _ = bk.create_backup(populated)
    bk.restore_backup(path, tmp_path / "o")
    monkeypatch.delenv("COACH_BACKUP_KEY")
    from coach.secrets import SecretNotFound
    with pytest.raises(SecretNotFound, match="backup_key"):
        bk.create_backup(populated)


def test_backup_missing_db(cfg):
    with pytest.raises(bk.BackupError, match="Database not found"):
        bk.create_backup(cfg, key=KEY)
