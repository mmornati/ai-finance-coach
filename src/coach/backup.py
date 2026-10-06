"""Encrypted, dated backups of the DB file + ``memory/`` (AES-256-GCM, key derived with scrypt).

File format:  MAGIC(6) | salt(16) | nonce(12) | AES-GCM(tar bytes, aad=MAGIC|salt|nonce)
Archive layout (tar): ``finance.db`` and ``memory/...``.
"""
from __future__ import annotations

import io
import os
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from coach import secrets
from coach.config import Config
from coach.db import ensure_private_dir, is_plaintext, snapshot

MAGIC = b"AFCBK1"
SALT_LEN, NONCE_LEN = 16, 12
SCRYPT = dict(n=2**15, r=8, p=1)
PREFIX, SUFFIX = "coach-backup-", ".tar.enc"
DB_ARCNAME = "finance.db"


class BackupError(Exception):
    pass


def _derive(key: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, **SCRYPT).derive(key.encode())


def encrypt_bytes(data: bytes, key: str, magic: bytes = MAGIC) -> bytes:
    """AES-256-GCM, scrypt-derived key. `magic` tells the file kind (backups: AFCBK1, `coach export` archives: AFCEX1)."""
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    header = magic + salt + nonce
    return header + AESGCM(_derive(key, salt)).encrypt(nonce, data, header)


def decrypt_bytes(blob: bytes, key: str, magic: bytes = MAGIC) -> bytes:
    hlen = len(magic) + SALT_LEN + NONCE_LEN
    if len(blob) < hlen + 16 or not blob.startswith(magic):
        raise BackupError("Not a coach backup file (bad header)" if magic == MAGIC else "Not a coach export file (bad header)")
    header = blob[:hlen]
    salt = header[len(magic):len(magic) + SALT_LEN]
    nonce = header[len(magic) + SALT_LEN:]
    try:
        return AESGCM(_derive(key, salt)).decrypt(nonce, blob[hlen:], header)
    except InvalidTag:
        raise BackupError("Decryption failed: wrong backup_key or corrupted file") from None


def make_tar(db_snapshot: Path | None, memory_dir: Path, warn=lambda m: print(m, file=sys.stderr)) -> bytes:
    """Tar of the DB snapshot (as ``finance.db``) and ``memory/``.

    Symlinks to regular files are stored as regular files (dereferenced); symlinked directories, dangling
    links and other non-regular entries are skipped with a warning, so the archive is always restorable.
    """
    buf = io.BytesIO()
    memory_dir = memory_dir.resolve()
    # take the memory writers' lock: an archive taken in the middle of a write could capture git's index.lock or a
    # half-recorded change (only when the folder exists: the lock must not create it)
    from contextlib import nullcontext
    from coach.memory.history import MemoryRepo
    guard = MemoryRepo(memory_dir).lock() if memory_dir.exists() else nullcontext()
    with guard, tarfile.open(fileobj=buf, mode="w") as tar:
        if db_snapshot is not None:
            tar.add(db_snapshot, arcname=DB_ARCNAME)
        if memory_dir.exists():
            tar.add(memory_dir, arcname="memory", recursive=False)
            for dirpath, dirnames, filenames in os.walk(memory_dir, followlinks=False):
                base = Path(dirpath)
                keep = []
                for d in sorted(dirnames):
                    p = base / d
                    if p.is_symlink():
                        warn(f"backup: skipping symlinked directory {p}")
                        continue
                    keep.append(d)
                    tar.add(p, arcname=str(Path("memory") / p.relative_to(memory_dir)), recursive=False)
                dirnames[:] = keep
                for f in sorted(filenames):
                    if f == ".lock" and base == memory_dir:
                        continue                                 # the writers' lock file is not data
                    p = base / f
                    if p.is_symlink() and not p.resolve().is_relative_to(memory_dir):
                        warn(f"backup: skipping {p}: a symlink leading outside the memory folder is not followed")
                        continue
                    if p.is_file():  # follows symlinks; False for dangling links, sockets, fifos...
                        info = tar.gettarinfo(str(p.resolve()),
                                              arcname=str(Path("memory") / p.relative_to(memory_dir)))
                        with open(p, "rb") as fh:
                            tar.addfile(info, fh)
                    else:
                        warn(f"backup: skipping non-regular entry {p}")
    return buf.getvalue()


_NAME_RE = re.compile(rf"^{re.escape(PREFIX)}(\d{{8}}-\d{{6}})(?:-(\d+))?{re.escape(SUFFIX)}$")


def _sort_key(p: Path):
    m = _NAME_RE.match(p.name)
    return (m.group(1), int(m.group(2) or 0)) if m else ("", 0)


def list_backups(backup_dir: Path) -> list[Path]:
    """Backups oldest-first, ordered by the (UTC) timestamp in the name, not lexically."""
    return sorted((p for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}") if _NAME_RE.match(p.name)), key=_sort_key)


def _check_members(tar: tarfile.TarFile, to: Path) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        target = (to / m.name).resolve()
        if not (target == to or to in target.parents):
            raise BackupError(f"Unsafe path in archive: {m.name}")
        if not (m.isfile() or m.isdir()):
            raise BackupError(f"Unsupported entry type in archive: {m.name}")
    return members


def verify_archive(path: Path, key: str) -> list[str]:
    """Decrypt and list an archive; raises BackupError unless it is complete and restorable."""
    data = decrypt_bytes(Path(path).read_bytes(), key)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        members = _check_members(tar, Path("/verify-root").resolve())
        names = [m.name for m in members]
        for m in members:
            if m.isfile():
                tar.extractfile(m).read()
    if DB_ARCNAME not in names:
        raise BackupError("Archive does not contain the database")
    return names


def create_backup(cfg: Config, now: datetime | None = None, key: str | None = None,
                  warn=lambda m: print(m, file=sys.stderr)) -> tuple[Path, list[Path]]:
    """Write a dated (UTC) encrypted archive; returns (path, pruned_files).

    The DB is snapshotted through the SQLite backup API (consistent even if a sync is running), the
    written archive is decrypted and checked, and only then are old backups pruned.
    """
    key = key or secrets.get_secret("backup_key")
    if not cfg.db_path.exists():
        raise BackupError(f"Database not found: {cfg.db_path}")
    ensure_private_dir(cfg.backup_dir)
    with tempfile.TemporaryDirectory(prefix="coach-backup-", dir=cfg.backup_dir) as td:
        snap = Path(td) / "snapshot.db"
        db_key = None if is_plaintext(cfg.db_path) else secrets.get_secret("db_key")
        snapshot(cfg.db_path, snap, db_key)
        blob = encrypt_bytes(make_tar(snap, cfg.memory_dir, warn), key)
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc)
    stamp = when.strftime("%Y%m%d-%H%M%S")
    dest = cfg.backup_dir / f"{PREFIX}{stamp}{SUFFIX}"
    n = 1
    while dest.exists():  # two backups within one second
        dest = cfg.backup_dir / f"{PREFIX}{stamp}-{n}{SUFFIX}"
        n += 1
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(blob)
    os.chmod(tmp, 0o600)
    tmp.rename(dest)
    try:
        verify_archive(dest, key)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    existing = list_backups(cfg.backup_dir)
    pruned = existing[:-cfg.backup_retention] if len(existing) > cfg.backup_retention else []
    for p in pruned:
        p.unlink()
    return dest, pruned


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True, mode=0o700)  # new dirs only; existing ones keep their mode


def restore_backup(archive: Path, to: Path, force: bool = False, key: str | None = None) -> list[Path]:
    """Decrypt and extract into `to`. Never overwrites existing files unless `force`.
    Files are written 0600 and directories 0700 (archived modes are not re-applied)."""
    key = key or secrets.get_secret("backup_key")
    data = decrypt_bytes(Path(archive).read_bytes(), key)
    to = Path(to).resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        members = _check_members(tar, to)
        clashes = [m.name for m in members if m.isfile() and (to / m.name).exists()]
        if clashes and not force:
            raise BackupError(
                f"Refusing to overwrite {len(clashes)} existing file(s), e.g. {to / clashes[0]} "
                "(restore into an empty directory, or pass --force)")
        _mkdir(to)
        written = []
        for m in members:
            dest = to / m.name
            if m.isdir():
                _mkdir(dest)
                continue
            _mkdir(dest.parent)
            dest.write_bytes(tar.extractfile(m).read())
            os.chmod(dest, 0o600)
            written.append(dest)
    from coach.memory.history import sanitize_gitdir
    for f in written:                          # a restored history must follow the folder it was restored into
        if f.name == "config" and f.parent.name == ".history.git":
            sanitize_gitdir(f.parent)
    return written
