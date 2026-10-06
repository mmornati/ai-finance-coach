"""`coach export` (E11-6): everything the coach holds about the household, as one archive the user owns.

Contents of the ZIP (``--format zip``, the only format):

* ``manifest.json``         when, schema version, row counts per table, what is NOT included
* ``transactions.csv``      the transactions with account, merchant, category (spreadsheet-ready; cells that start with = + - @ are escaped)
* ``data/<table>.json``     every table of the database except the ones that hold bank credentials (see EXCLUDED_TABLES): accounts,
                            transactions, categories (the taxonomy), merchants, insights, decisions, alternatives, net worth history,
                            alerts, recurring series, budgets / goals data tables ...
* ``memory/...``            the memory folder (household, loans, contracts, notes, documents) without its git history and proposals
* ``README.txt``            how to read it

By default the ZIP is encrypted with the backup crypto (AES-256-GCM, scrypt, key = the ``backup_key`` secret) under the magic ``AFCEX1`` and
written 0600 into ``<data_dir>/exports/``. ``--plain`` writes an UNENCRYPTED zip: only on a terminal, after typing a confirmation phrase.

Nothing is sent anywhere: this module makes no network call.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional

from coach import secrets
from coach.backup import BackupError, decrypt_bytes, encrypt_bytes
from coach.db import connect, ensure_private_dir, user_tables

EXPORT_MAGIC = b"AFCEX1"
SUFFIX_ENC, SUFFIX_PLAIN = ".zip.enc", ".zip"
PLAIN_PHRASE = "EXPORT UNENCRYPTED"
# tables that hold Enable Banking session / authorisation state or transient batch bookkeeping: not "your data", and credential-adjacent
EXCLUDED_TABLES = {"sessions", "pending_auth", "schema_migrations", "llm_batches", "llm_batch_jobs", "sqlite_sequence", "consent_alerts"}
MEMORY_SKIP_DIRS = {".history.git", ".proposals", ".backups"}
MEMORY_SKIP_FILES = {".lock", ".DS_Store"}


class ExportError(Exception):
    pass


def _cell(v):
    """CSV cell: a text starting with = + - @ would be run as a formula by a spreadsheet; prefix it with a quote."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return {"_bytes_hex": bytes(o).hex()}
    return str(o)


def table_rows(con, table: str) -> tuple[list[str], list[tuple]]:
    cur = con.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def transactions_csv(con) -> bytes:
    """One row per transaction with its account label, normalised merchant and category (the effective one: override > merchant label)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "amount", "currency", "account", "bank", "counterparty", "description", "merchant", "category", "tx_type", "tx_key"])
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ov = "LEFT JOIN tx_overrides o ON o.tx_key=t.tx_key" if "tx_overrides" in have else ""
    cat = "COALESCE(o.category, m.category)" if "tx_overrides" in have else "m.category"
    acc = "COALESCE(a.label, a.name, a.uid)" if _has_col(con, "accounts", "label") else "COALESCE(a.name, a.uid)"
    bank = "a.bank" if _has_col(con, "accounts", "bank") else "''"
    q = f"""SELECT t.booking_date, t.amount, t.currency, {acc}, {bank}, t.counterparty, t.description,
                   COALESCE(m.merchant_name, e.merchant_key, ''), COALESCE({cat}, ''), COALESCE(e.tx_type, ''), t.tx_key
            FROM transactions t LEFT JOIN accounts a ON a.uid=t.account_uid
            LEFT JOIN tx_enriched e ON e.tx_key=t.tx_key LEFT JOIN merchants m ON m.merchant_key=e.merchant_key {ov}
            ORDER BY t.booking_date, t.tx_key"""
    for row in con.execute(q):
        w.writerow([_cell(c) for c in row])
    return buf.getvalue().encode("utf-8")


def _has_col(con, table: str, col: str) -> bool:
    try:
        return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))
    except Exception:                                                              # noqa: BLE001
        return False


def memory_files(memory_dir: Path) -> list[tuple[Path, str]]:
    """(file, arcname) of the memory folder, skipping the git history, proposals, locks and anything that leads outside the folder."""
    out = []
    if not memory_dir.exists():
        return out
    base = memory_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(memory_dir, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in MEMORY_SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if f in MEMORY_SKIP_FILES or (p.is_symlink() and not p.resolve().is_relative_to(base)) or not p.is_file():
                continue
            out.append((p, "memory/" + p.relative_to(memory_dir).as_posix()))
    return out


README = """AI finance coach: export of {when}

manifest.json      row counts and what is not included
transactions.csv   your transactions (date, amount, account, counterparty, merchant, category)
data/<table>.json  every table of the database (list of {{column: value}} objects), except bank session credentials
memory/            your household memory files (YAML / Markdown / documents), without the change history and proposals
categories.json    the category taxonomy

This archive holds your whole financial life. Keep it encrypted; delete it when you no longer need it.
"""


def build_zip(cfg, con, now: Optional[dt.datetime] = None) -> bytes:
    now = now or dt.datetime.now(dt.timezone.utc)
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in sorted(user_tables(con)):
            if t in EXCLUDED_TABLES:
                continue
            cols, rows = table_rows(con, t)
            counts[t] = len(rows)
            data = [dict(zip(cols, r)) for r in rows]
            z.writestr(f"data/{t}.json", json.dumps(data, ensure_ascii=False, indent=1, default=_json_default))
        z.writestr("transactions.csv", transactions_csv(con))
        try:
            from coach.classify import rules
            z.writestr("categories.json", json.dumps(dict(rules.CATEGORIES), ensure_ascii=False, indent=1))
        except Exception:                                                          # noqa: BLE001
            pass
        n_mem = 0
        for p, arc in memory_files(cfg.memory_dir):
            z.write(p, arc)
            n_mem += 1
        z.writestr("manifest.json", json.dumps({
            "created": now.isoformat(timespec="seconds"), "format": "zip", "tables": counts, "memory_files": n_mem,
            "not_included": sorted(EXCLUDED_TABLES) + ["memory/.history.git", "memory/.proposals", "secrets (Keychain)", "backups"],
            "schema_migrations": [r[0] for r in con.execute("SELECT version FROM schema_migrations ORDER BY version")]}, indent=1))
        z.writestr("README.txt", README.format(when=now.strftime("%Y-%m-%d %H:%M UTC")))
    return buf.getvalue()


def default_out(cfg, encrypted: bool, now: Optional[dt.datetime] = None) -> Path:
    now = now or dt.datetime.now(dt.timezone.utc)
    return cfg.data_dir / "exports" / f"coach-export-{now:%Y%m%d-%H%M%S}{SUFFIX_ENC if encrypted else SUFFIX_PLAIN}"


def _write_private(path: Path, data: bytes) -> None:
    ensure_private_dir(path.parent) if path.parent.name == "exports" else path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ExportError(f"{path} already exists: choose another --out (nothing is overwritten)")
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def export_encrypted(cfg, out: Optional[Path] = None, *, key: Optional[str] = None, insecure: bool = False,
                     now: Optional[dt.datetime] = None, con=None) -> Path:
    """Write the encrypted archive and verify it by decrypting it again; returns the path."""
    key = key or secrets.get_secret("backup_key")
    own = con is None
    con = con or connect(cfg, insecure=insecure)
    try:
        blob = encrypt_bytes(build_zip(cfg, con, now), key, EXPORT_MAGIC)
    finally:
        if own:
            con.close()
    out = Path(out) if out else default_out(cfg, True, now)
    _write_private(out, blob)
    try:
        with zipfile.ZipFile(io.BytesIO(decrypt_bytes(out.read_bytes(), key, EXPORT_MAGIC))) as z:
            bad = z.testzip()
            if bad or "manifest.json" not in z.namelist():
                raise BackupError("the written archive failed its check")
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


def export_plain(cfg, out: Optional[Path] = None, *, insecure: bool = False, now: Optional[dt.datetime] = None, con=None) -> Path:
    own = con is None
    con = con or connect(cfg, insecure=insecure)
    try:
        data = build_zip(cfg, con, now)
    finally:
        if own:
            con.close()
    out = Path(out) if out else default_out(cfg, False, now)
    _write_private(out, data)
    return out


def decrypt_export(archive: Path, to: Path, key: Optional[str] = None) -> list[Path]:
    """Decrypt an export archive and extract it into `to` (an empty / new folder, files 0600)."""
    key = key or secrets.get_secret("backup_key")
    data = decrypt_bytes(Path(archive).read_bytes(), key, EXPORT_MAGIC)
    to = Path(to).resolve()
    written = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            target = (to / info.filename).resolve()
            if not (target == to or to in target.parents):
                raise ExportError(f"unsafe path in archive: {info.filename}")
        to.mkdir(parents=True, exist_ok=True, mode=0o700)
        for info in z.infolist():
            if info.is_dir():
                continue
            dest = to / info.filename
            if dest.exists():
                raise ExportError(f"refusing to overwrite {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest.write_bytes(z.read(info))
            os.chmod(dest, 0o600)
            written.append(dest)
    return written


# ---------------------------------------------------------------- the command

def confirm_plain(isatty: Callable[[], bool], input_fn: Callable[[str], str], out=print) -> bool:
    """TTY + a typed phrase: the only way an unencrypted export is written."""
    if not isatty():
        out("error: --plain needs a terminal (an unencrypted export is never written from a script or an agent)")
        return False
    out("WARNING: an UNENCRYPTED export holds your whole financial life in clear text (transactions, IBANs, memory).")
    out(f"Anyone who can read the file can read everything. Type {PLAIN_PHRASE!r} to continue.")
    return input_fn("> ").strip() == PLAIN_PHRASE


def refuse_target(to: Path, cfg) -> Optional[str]:
    """Why a plaintext extraction may not go to `to`: inside the project folder, data_dir or memory_dir (a stray copy there is committed,
    backed up, scanned by agents)."""
    t = to.expanduser().resolve()
    for label, base in (("the project folder", cfg.root), ("data_dir", cfg.data_dir), ("the memory folder", cfg.memory_dir), ("the backup folder", cfg.backup_dir)):
        b = base.resolve()
        if t == b or b in t.parents:
            return f"refusing to write plaintext inside {label} ({base}): choose a folder outside the repository"
    return None


def cmd_export(a, cfg, *, isatty: Optional[Callable[[], bool]] = None, input_fn: Callable[[str], str] = input) -> None:
    isatty = isatty or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    if a.format != "zip":
        sys.exit("error: --format must be zip")
    if getattr(a, "decrypt", None):
        if not a.to:
            sys.exit("error: --decrypt ARCHIVE needs --to DIR")
        bad = refuse_target(Path(a.to), cfg)
        if bad:
            sys.exit(f"error: {bad}")
        # decrypting writes your whole financial life in clear text: terminal + typed phrase, like --plain
        if not confirm_plain(isatty, input_fn):
            sys.exit("aborted: nothing was written")
        files = decrypt_export(Path(a.decrypt), Path(a.to))
        print(f"extracted {len(files)} file(s) into {a.to} (files 0600). Delete them when you no longer need them.")
        return
    try:
        if a.plain:
            if not confirm_plain(isatty, input_fn):
                sys.exit("aborted: nothing was written")
            path = export_plain(cfg, Path(a.out) if a.out else None, insecure=a.insecure)
            print(f"UNENCRYPTED export written: {path} (0600). Delete it as soon as you have used it.")
        else:
            path = export_encrypted(cfg, Path(a.out) if a.out else None, insecure=a.insecure)
            print(f"encrypted export written: {path} ({path.stat().st_size} bytes, 0600; key = secret 'backup_key')")
            print(f"read it back with: coach export --decrypt {path} --to <empty folder>")
    except (ExportError, BackupError) as e:
        sys.exit(f"error: {e}")
    root = cfg.root.resolve()
    p = path.resolve()
    if p.is_relative_to(root) and not p.is_relative_to(cfg.data_dir.resolve()):
        print("warning: the export is inside the project folder: move it somewhere outside the repository when you are done",
              file=sys.stderr)


def register(sub, add):
    s = add(sub, "export", cmd_export, "export all your data: transactions CSV + JSON tables + memory, as an encrypted archive (E11-6)")
    s.add_argument("--out", metavar="FILE", help="output file (default <data_dir>/exports/coach-export-<time>.zip.enc)")
    s.add_argument("--format", default="zip", help="archive format (zip)")
    s.add_argument("--plain", action="store_true", help="write an UNENCRYPTED zip: needs a terminal and a typed confirmation")
    s.add_argument("--decrypt", metavar="ARCHIVE", help="decrypt an export archive instead of exporting (needs --to)")
    s.add_argument("--to", metavar="DIR", help="folder to extract into (with --decrypt)")
