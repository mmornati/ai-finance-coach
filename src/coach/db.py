"""Database access: every code path opens the DB through :func:`connect`.

* SQLCipher (``sqlcipher3``) with the ``db_key`` secret; a plaintext DB is refused unless
  ``--insecure`` / ``insecure_plaintext_db = true``.
* Versioned migrations: numbered ``src/coach/migrations/NNNN_name.sql`` files tracked in
  ``schema_migrations``.
* A ``REGEXP`` SQL function (case-insensitive) for the classification pipeline.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import sqlcipher3

from coach import secrets
from coach.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SQLITE_MAGIC = b"SQLite format 3\x00"

Connection = sqlcipher3.Connection
Error = sqlcipher3.Error


class PlaintextDatabaseError(Exception):
    pass


class WrongKeyError(Exception):
    pass


class DatabaseMissingError(Exception):
    pass


class MigrationError(Exception):
    pass


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (0700) if needed and tighten its mode."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _prepare_parent(cfg: Config, path: Path) -> None:
    """data_dir is tightened to 0700; any other parent directory is only created, never chmod'ed."""
    if path.parent.resolve() == cfg.data_dir.resolve():
        ensure_private_dir(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)


def secure_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- file inspection

def is_plaintext(path: Path) -> bool | None:
    """True if `path` is a plain SQLite file, False if encrypted/unknown, None if missing/empty."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, "rb") as f:
        return f.read(16) == SQLITE_MAGIC


def _sql_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _regexp(pattern, value):
    return value is not None and re.search(pattern, value, re.I) is not None


def _open(path: Path, key: str | None, shared: bool = False) -> Connection:
    # shared: the connection may be used from several threads (the web app serialises its use with a lock)
    con = sqlcipher3.connect(str(path), check_same_thread=not shared)
    if key:
        con.execute(f"PRAGMA key = {_sql_quote(key)}")
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher3.DatabaseError as e:
        con.close()
        raise WrongKeyError(
            f"Cannot read {path}: wrong db_key, or the file is not a database ({e}).") from e
    con.create_function("REGEXP", 2, _regexp)
    return con


# ---------------------------------------------------------------- connect

def connect(cfg: Config, *, insecure: bool = False, migrate: bool = True,
            path: Path | None = None, create: bool = False, shared: bool = False) -> Connection:
    """Open the application database.

    insecure: allow a plaintext DB (CLI ``--insecure`` or config ``insecure_plaintext_db``).
    migrate: apply pending migrations.
    create: allow creating a new DB when the file is missing (explicit setup commands only);
        otherwise a missing file is an error, so a wrong path never yields a silent empty DB.
        The new DB is encrypted when a ``db_key`` is available, plaintext only if insecure is allowed
        and no key exists.
    """
    path = Path(path or cfg.db_path)
    allow_plain = insecure or cfg.insecure_plaintext_db
    stale = path.with_name(path.name + ".encrypting")
    if stale.exists():
        raise DatabaseMissingError(
            f"An interrupted encryption left {stale}. Your data is in {path} (or its .plaintext.bak): "
            f"check them, delete the leftover {stale.name}, and rerun `coach db encrypt`.")
    state = is_plaintext(path)
    if state is None and not create:
        raise DatabaseMissingError(
            f"No database at {path} (check db_path / data_dir / COACH_HOME). "
            "Create one with `coach db migrate --create`, or `coach db import-prototype`.")
    _prepare_parent(cfg, path)
    key_used = None
    if state is True:
        if not allow_plain:
            raise PlaintextDatabaseError(
                f"Refusing to open plaintext database {path}.\n"
                "  - encrypt it:        uv run coach db encrypt              (key from secret 'db_key')\n"
                "  - or knowingly use it: pass --insecure, or set insecure_plaintext_db = true in config.toml")
        con = _open(path, None, shared)
    elif state is False:
        key_used = secrets.get_secret("db_key")
        con = _open(path, key_used, shared)
    else:  # new database
        key_used = secrets.get_secret("db_key", required=not allow_plain)
        con = _open(path, key_used, shared)
    secure_file(path)
    if migrate:
        try:
            if state is not None and not cfg.db_auto_migrate:
                behind = [m for m in available_migrations() if m.version not in applied_versions(con)]
                if behind:
                    raise MigrationError(
                        f"{path} is behind this version of coach ({len(behind)} pending migration(s), next "
                        f"{behind[0].version:04d}_{behind[0].name}) and [db] auto_migrate is false: run "
                        "`uv run coach db migrate` when you are ready (a safety copy is taken first).")
            applied = apply_migrations(con, key=key_used)
            try:        # an interrupted taxonomy rename is completed or forgotten whenever a database is opened
                from coach.classify.taxonomy import recover_journal
                recover_journal(con)
            except ImportError:
                pass
            if applied and state is not None:      # an existing database was changed behind the user's back: say so
                print(f"coach: applied migration(s) {', '.join(f'{m.version:04d}_{m.name}' for m in applied)} to "
                      f"{path} (disable with [db] auto_migrate = false)", file=sys.stderr)
        except Exception:
            con.close()
            raise
    return con


# ---------------------------------------------------------------- migrations

@dataclass
class Migration:
    version: int
    name: str
    path: Path

    @property
    def is_python(self) -> bool:
        return self.path.suffix == ".py"

    @property
    def sql(self) -> str:
        return self.path.read_text()

    def run_python(self, con) -> None:
        """Data migration written in Python: the module defines ``run(con)`` and executes it inside the
        migration's own transaction (it must not commit)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"coach_migration_{self.version:04d}", self.path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run(con)


def available_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out = []
    for p in sorted([*directory.glob("*.sql"), *directory.glob("[0-9]*.py")]):
        m = re.fullmatch(r"(\d+)_(.+)\.(?:sql|py)", p.name)
        if not m:
            raise ValueError(f"Bad migration file name: {p.name} (expected NNNN_name.sql)")
        out.append(Migration(int(m.group(1)), m.group(2), p))
    versions = [m.version for m in out]
    if len(set(versions)) != len(versions):
        raise ValueError("Duplicate migration numbers")
    return out


def _ensure_table(con) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    con.commit()


def applied_versions(con) -> dict[int, tuple[str, str]]:
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not has:
        return {}
    return {v: (n, at) for v, n, at in con.execute("SELECT version, name, applied_at FROM schema_migrations")}


def split_sql(script: str) -> list[str]:
    """Split a SQL script into complete statements (comment-only tails dropped)."""
    out, start = [], 0
    for i, ch in enumerate(script):
        if ch == ";" and sqlcipher3.complete_statement(script[start:i + 1]):
            out.append(script[start:i + 1])
            start = i + 1
    return out


def _db_file(con) -> Path | None:
    row = con.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def apply_migrations(con, directory: Path = MIGRATIONS_DIR, safety_copy: bool = True,
                     key: str | None = None) -> list[Migration]:
    """Apply every not-yet-applied migration in order; returns the ones applied now.

    Each migration runs in a BEGIN IMMEDIATE transaction and re-checks ``schema_migrations`` inside it, so
    two processes cannot apply it twice. A DB with migrations unknown to this version is refused, and a
    safety copy (``*.pre-migrate-<ts>.bak``, made with the backup API, same key, newest 2 kept) is
    taken before touching a non-empty DB unless `safety_copy` is False (used by import-prototype).
    """
    avail = available_migrations(directory)
    known = {m.version for m in avail}
    done = applied_versions(con)
    if unknown := sorted(set(done) - known):
        raise MigrationError(
            f"Database was migrated by a newer version (unknown migrations {unknown}); upgrade the app.")
    pending = [m for m in avail if m.version not in done]
    if not pending:
        return []
    f = _db_file(con)
    if safety_copy and f and f.exists() and any(t != "schema_migrations" for t in user_tables(con)):
        bak = f.with_name(f"{f.name}.pre-migrate-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.bak")
        if not bak.exists():
            snapshot(f, bak, key)
        for old in sorted(f.parent.glob(f"{f.name}.pre-migrate-*.bak"))[:-2]:
            old.unlink()
    _ensure_table(con)
    applied = []
    for m in pending:
        con.execute("BEGIN IMMEDIATE")
        try:
            if m.version in applied_versions(con):  # another process won the race
                con.rollback()
                continue
            if m.is_python:
                m.run_python(con)
            else:
                for stmt in split_sql(m.sql):
                    con.execute(stmt)
            con.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)",
                        (m.version, m.name, now_iso()))
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        applied.append(m)
    return applied


def status(con, directory: Path = MIGRATIONS_DIR) -> dict:
    done = applied_versions(con)
    avail = available_migrations(directory)
    return {
        "applied": [(m.version, m.name, done[m.version][1]) for m in avail if m.version in done],
        "pending": [(m.version, m.name) for m in avail if m.version not in done],
        "unknown": sorted(set(done) - {m.version for m in avail}),
    }


# ---------------------------------------------------------------- helpers

def user_tables(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def table_counts(con, tables: list[str] | None = None) -> dict[str, int]:
    tables = tables if tables is not None else user_tables(con)
    return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


# ---------------------------------------------------------------- encrypt / import

class EncryptionError(Exception):
    pass


def _fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(path: Path, dest: Path, key: str | None) -> None:
    """Consistent copy of a (possibly encrypted) DB via the SQLite backup API; `dest` gets the same key."""
    src = _open(path, key)
    try:
        dst = sqlcipher3.connect(str(dest))
        try:
            if key:
                dst.execute(f"PRAGMA key = {_sql_quote(key)}")
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    secure_file(dest)


def plaintext_leftovers(path: Path) -> list[Path]:
    """Plaintext copies of the DB next to it (``*.plaintext.bak``, pre-migrate / pre-import copies)."""
    path = Path(path)
    found = []
    for pat in (".plaintext.bak", ".pre-migrate-*.bak", ".pre-import*.bak"):
        for p in sorted(path.parent.glob(path.name + pat)):
            if is_plaintext(p) is True and p not in found:
                found.append(p)
    return found


def encrypt_database(cfg: Config, path: Path | None = None, key: str | None = None) -> tuple[Path, Path]:
    """Convert the plaintext DB at `path` to an encrypted one (sqlcipher_export).

    One connection holds ``BEGIN IMMEDIATE`` on the source from before the export until after the swap, so
    no writer (sync, normalize, classify...) can change the DB in between: concurrent writes fail with
    "database is locked" instead of being lost. The plaintext is hard-linked to ``<name>.plaintext.bak`` and
    the encrypted file moved over it with an atomic ``os.replace``. Returns (encrypted_path, backup_path).
    """
    path = Path(path or cfg.db_path)
    state = is_plaintext(path)
    if state is None:
        raise EncryptionError(f"No database to encrypt at {path}")
    if state is False:
        raise EncryptionError(f"{path} is already encrypted (not a plaintext SQLite file)")
    key = key or secrets.get_secret("db_key")
    bak = path.with_name(path.name + ".plaintext.bak")
    if bak.exists():
        if os.path.samefile(bak, path):   # hard link left by a crash between link and replace
            bak.unlink()
        else:
            raise EncryptionError(f"{bak} already exists: check it, delete or move it away first")
    tmp = path.with_name(path.name + ".encrypting")
    tmp.unlink(missing_ok=True)

    lock = _open(path, None)
    try:
        lock.execute("BEGIN IMMEDIATE")          # held until the swap is done; readers are still allowed
        exp = _open(path, None)
        try:
            exp.execute(f"ATTACH DATABASE {_sql_quote(str(tmp))} AS encrypted KEY {_sql_quote(key)}")
            exp.execute("SELECT sqlcipher_export('encrypted')")
            before = table_counts(exp)
            after = {t: exp.execute(f'SELECT COUNT(*) FROM encrypted."{t}"').fetchone()[0] for t in before}
            exp.commit()
            exp.execute("DETACH DATABASE encrypted")
        finally:
            exp.close()
        if before != after:
            raise EncryptionError(f"Row counts differ after export: before={before} after={after}")
        enc = _open(tmp, key)
        try:
            if table_counts(enc, list(before)) != before:
                raise EncryptionError("Row counts differ in the exported file; aborting")
        finally:
            enc.close()
        if is_plaintext(tmp):
            raise EncryptionError("Exported file is still plaintext; aborting")
        secure_file(tmp)
        _fsync_path(tmp)
        try:
            os.link(path, bak)                   # plaintext kept, never a window without a DB
        except FileExistsError:
            raise EncryptionError(f"{bak} already exists") from None
        try:
            os.replace(tmp, path)
        except Exception:
            bak.unlink(missing_ok=True)          # path is still the untouched original
            raise
        secure_file(path)
        secure_file(bak)
        try:        # the encrypted file is a new database: new identity (a pending taxonomy journal follows it)
            from coach.classify.taxonomy import regenerate_identity_of_file
            regenerate_identity_of_file(path, key, rebind_journal=True)
        except Exception:                                  # noqa: BLE001  (never fail an encryption for this)
            pass
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        try:
            lock.rollback()
        finally:
            lock.close()
    return path, bak


def import_prototype(cfg: Config, source: Path, force: bool = False,
                     warn=lambda m: print(m, file=sys.stderr)) -> tuple[Path, dict[str, tuple[int, int]]]:
    """Copy the prototype DB (read-only) to cfg.db_path (plaintext), migrate it, verify counts.

    With `force`, an existing destination is moved aside to ``*.pre-import.bak`` (never destroyed).
    Returns (dest, {table: (source_count, dest_count)}). The source is never modified.
    """
    import sqlite3  # plain sqlite for the read-only source

    source = Path(source).resolve()
    dest = Path(cfg.db_path).resolve()
    if not source.exists():
        raise EncryptionError(f"Prototype DB not found: {source}")
    if source == dest:
        raise EncryptionError("Source and destination are the same file")
    if dest.exists() and not force:
        raise EncryptionError(f"{dest} already exists (use --force to overwrite)")
    _prepare_parent(cfg, dest)
    tmp = dest.with_name(dest.name + ".importing")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
    src_tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    src_counts = {t: src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in src_tables}
    src.close()

    con = _open(tmp, None)
    try:
        apply_migrations(con, safety_copy=False)
        dst_counts = table_counts(con, src_tables)
    finally:
        con.close()
    result = {t: (src_counts[t], dst_counts[t]) for t in src_tables}
    bad = {t: v for t, v in result.items() if v[0] != v[1]}
    if bad:
        tmp.unlink(missing_ok=True)
        raise EncryptionError(f"Row count mismatch after import: {bad}")
    secure_file(tmp)
    if dest.exists():
        aside = dest.with_name(dest.name + ".pre-import.bak")
        if aside.exists():
            aside = dest.with_name(f"{dest.name}.pre-import-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.bak")
        if is_plaintext(dest) is False:
            warn(f"WARNING: replacing an ENCRYPTED database with a plaintext import; the old one is kept "
                 f"as {aside}. Run `coach db encrypt` afterwards.")
        os.replace(dest, aside)
        secure_file(aside)
        warn(f"existing database moved aside to {aside}")
    tmp.replace(dest)
    return dest, result
