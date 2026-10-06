"""`coach security audit` (E11-2, E11-3): a read-only audit of how this installation protects its secrets and what it exposes.

Nothing here writes (except the explicit ``fix_permissions``), nothing reaches the network, and NO SECRET VALUE is ever read into a
result or printed: secrets are only looked up to say "set (keychain)" / "not set"; files are only ``stat``-ed, except the repo scan, which
reports ``path:line`` and the KIND of the finding, never the matched text.

Checks (area, id): secrets (Keychain / environment, Enable Banking key file, proposal key, session key age), storage (database
encryption, plaintext leftovers, permissions of data_dir / backups / memory, backups encrypted + retention), repository (.gitignore coverage,
secret-looking strings in the working tree), exposure (UI bind, listening sockets, MCP stdio only, callback server loopback, Claude Code
permission rules).

A check is ``ok`` | ``info`` | ``warn`` | ``critical`` | ``skip``. The command exits non-zero when there is a critical.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from coach import secrets as secrets_mod
from coach.db import SQLITE_MAGIC, is_plaintext, plaintext_leftovers

OK, INFO, WARN, CRIT, SKIP = "ok", "info", "warn", "critical", "skip"
RANK = {OK: 0, SKIP: 0, INFO: 1, WARN: 2, CRIT: 3}
BACKUP_MAGIC = b"AFCBK1"
EXPORT_MAGIC = b"AFCEX1"
WAL_MAGICS = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"


def is_plain_sqlite_file(path: Path) -> bool:
    """A SQLite database, WAL or rollback journal in clear (judged by the header, whatever the extension). An encrypted SQLCipher file has
    a random first page and none of these headers."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return head == SQLITE_MAGIC or head[:4] in WAL_MAGICS or head[:8] == JOURNAL_MAGIC


@dataclass
class Check:
    area: str
    id: str
    status: str
    title: str
    detail: str = ""
    items: list = field(default_factory=list)       # short, secret-free evidence lines (paths, counts)

    def as_dict(self) -> dict:
        return {"area": self.area, "id": self.id, "status": self.status, "title": self.title, "detail": self.detail, "items": self.items}


# ---------------------------------------------------------------- helpers

def _mode(path: Path) -> Optional[int]:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _loose(mode: Optional[int]) -> bool:
    """True when group or others have any access."""
    return mode is not None and bool(mode & 0o077)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- .gitignore matching (no git needed)

def _pattern_rx(pat: str):
    neg = pat.startswith("!")
    p = pat[1:] if neg else pat
    dir_only = p.endswith("/")
    p = p.rstrip("/")
    anchored = "/" in p
    p = p.lstrip("/")
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if p.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    rx = re.compile(("^" if anchored else "^(?:.*/)?") + out + "$")
    return neg, dir_only, rx


def gitignore_patterns(text: str) -> list:
    pats = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        pats.append(_pattern_rx(ln))
    return pats


def is_ignored(patterns: list, relpath: str) -> bool:
    """Would git ignore `relpath` (forward slashes)? A file under an ignored directory is ignored (it cannot be re-included)."""
    parts = relpath.strip("/").split("/")
    for n in range(1, len(parts) + 1):
        prefix = "/".join(parts[:n])
        is_dir = n < len(parts)
        verdict = False
        for neg, dir_only, rx in patterns:
            if dir_only and not is_dir:
                continue
            if rx.match(prefix):
                verdict = not neg
        if verdict:
            return True
    return False


# what must NEVER be committed: (probe path, what it stands for)
GITIGNORE_PROBES = [
    ("data/finance.db", "the database (data/)"), ("data/ui-session.key", "the web session key (data/)"),
    ("data/ui-state.json", "web app state (data/)"), ("data/tls/localhost.key", "the callback TLS key (data/)"),
    ("memory/household.yaml", "household memory (memory/)"), ("memory/preferences.md", "household memory (memory/)"),
    ("memory/.history.git/config", "memory change history"), ("memory/.proposals/x.json", "memory proposals"),
    ("config.toml", "local configuration"), ("eb-private-key.pem", "a private key (*.pem)"),
    ("secrets/private.pem", "a private key (*.pem)"),
    ("backups/coach-backup-20260101-000000.tar.enc", "backups (backups/)"), ("finance.db.plaintext.bak", "plaintext database copy (*.bak)"),
    ("scratch.bak", "a backup copy (*.bak)"), ("ui-state.json", "web app state outside data/ (ui-*.json)"),
    ("ui-login.json", "web login tokens (ui-*.json)"), (".env", "environment secrets (.env)"),
    ("web/.env", "web build secrets (.env)"), ("web/.env.local", "web build secrets (.env.local)"),
    ("scratch/finance.db", "a database copy (*.db)"),
]


# ---------------------------------------------------------------- repository secret scan

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", "dist", "static"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".pdf", ".pyc", ".db", ".sqlite", ".sqlite3",
            ".enc", ".zip", ".gz", ".tar", ".lock", ".map", ".svg"}
MAX_SCAN_BYTES = 1_000_000
ALLOW_MARK = "allowlist secret"           # a line carrying this text is skipped (documented fixtures)

_PEM_RE = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
_KEY_PATTERNS = [
    ("an Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("an API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("a JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,}")),
]
_ASSIGN_RE = re.compile(r"""(?i)(?:secret|token|passw(?:or)?d|passwd|pwd|api[_-]?key|private[_-]?key|auth)\w*['"]?\s*[:=]\s*['"]([A-Za-z0-9+/=_\-]{24,})['"]""")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in freq.values())


def _looks_random(s: str) -> bool:
    classes = sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    return len(s) >= 24 and _entropy(s) >= 4.0 and classes >= 2 and not re.fullmatch(r"[0-9a-f]{32,}", s)


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """[(line, severity, kind)] of secret-looking content in `text` (never the content itself)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        if _PEM_RE.search(line):
            out.append((n, CRIT, "a PEM private key block"))
            continue
        hit = False
        for kind, rx in _KEY_PATTERNS:
            if rx.search(line):
                out.append((n, CRIT, kind))
                hit = True
                break
        if hit:
            continue
        m = _ASSIGN_RE.search(line)
        if m and _looks_random(m.group(1)):
            out.append((n, WARN, "a high-entropy value assigned to a secret-like name"))
    return out


def scan_tree(root: Path, exclude: list[Path], max_files: int = 20000) -> tuple[list[dict], int]:
    """Scan the working tree under `root` (no git needed) skipping `exclude` (data / memory / backups ...). -> (findings, files scanned)."""
    ex = [e.resolve() for e in exclude]
    findings, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath).resolve()
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not any((d / x).resolve() == e or e in (d / x).resolve().parents for e in ex)]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix.lower() in SKIP_EXT or p.is_symlink():
                continue
            try:
                if p.stat().st_size > MAX_SCAN_BYTES:
                    continue
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:2048]:
                continue
            scanned += 1
            if scanned > max_files:
                return findings, scanned
            for line, sev, kind in scan_text(raw.decode("utf-8", errors="replace")):
                findings.append({"path": _rel(p, root), "line": line, "severity": sev, "kind": kind})
    return findings, scanned


def tree_files(root: Path, names: tuple, exclude: list[Path]) -> list[Path]:
    """Files under `root` (skipping the usual build / virtualenv folders and `exclude`) whose name matches one of `names` (fnmatch)."""
    import fnmatch
    ex = [e.resolve() for e in exclude]
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath).resolve()
        dirnames[:] = [x for x in dirnames if x not in {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
                       and not any((d / x).resolve() == e for e in ex)]
        for f in filenames:
            if any(fnmatch.fnmatch(f, n) for n in names):
                out.append(Path(dirpath) / f)
    return out


# ---------------------------------------------------------------- the audit

LsofRunner = Callable[[], Optional[str]]


def default_lsof() -> Optional[str]:
    """`lsof -nP -iTCP -sTCP:LISTEN`: read-only listing of the listening TCP sockets of this machine. None when lsof is unavailable."""
    try:
        r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode in (0, 1) else None


def parse_lsof(text: str) -> list[dict]:
    """[{command, pid, user, addr, port}] from `lsof -nP -iTCP -sTCP:LISTEN` output (header and unparseable lines are skipped)."""
    out = []
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) < 9 or parts[0] == "COMMAND":
            continue
        name = " ".join(parts[8:])
        m = re.search(r"(\[[0-9a-fA-F:]+\]|[\w.*\-]+):(\d+)\s*\(LISTEN\)", name) or re.search(r"(\[[0-9a-fA-F:]+\]|[\w.*\-]+):(\d+)\s*$", name)
        if not m:
            continue
        out.append({"command": parts[0], "pid": parts[1], "user": parts[2], "addr": m.group(1).strip("[]"), "port": int(m.group(2))})
    return out


def _is_loopback_addr(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "localhost") or addr.startswith("127.")


def audit(cfg, *, scan_root: Optional[Path] = None, lsof: Optional[LsofRunner] = None, now: Optional[dt.datetime] = None,
          env=None, do_scan: bool = True) -> list[Check]:
    """Run every check. `scan_root` defaults to the project root (cfg.root); `lsof` is injectable (tests never run the real command)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    root = Path(scan_root or cfg.root)
    checks: list[Check] = []
    add = checks.append

    # ------------------------------------------------ secrets (Keychain / environment)
    def secret_state(name: str):
        try:
            _, origin = secrets_mod.lookup(name, env)
            return origin, None
        except secrets_mod.SecretBackendError as e:
            return None, type(e).__name__

    origin, err = secret_state("db_key")
    if err:
        add(Check("secrets", "db_key", WARN, "Keychain unavailable: cannot tell whether the database key is set", err))
    elif origin is None:
        add(Check("secrets", "db_key", CRIT, "database key (db_key) is not set", "the database cannot be opened or created encrypted: "
                  "`uv run coach config set-secret db_key --generate`"))
    elif origin == "env":
        add(Check("secrets", "db_key", WARN, "db_key comes from an environment variable", "visible to every child process of that shell; prefer the Keychain"))
    else:
        add(Check("secrets", "db_key", OK, "database key is in the Keychain"))
    origin, err = secret_state("backup_key")
    if err:
        add(Check("secrets", "backup_key", WARN, "Keychain unavailable: cannot tell whether the backup key is set", err))
    elif origin is None:
        add(Check("secrets", "backup_key", WARN, "backup key (backup_key) is not set", "`coach backup` and `coach export` cannot encrypt: "
                  "`uv run coach config set-secret backup_key --generate`"))
    else:
        add(Check("secrets", "backup_key", OK if origin == "keychain" else WARN, f"backup key is set ({origin})"))
    origin, err = secret_state("proposal_key")
    add(Check("secrets", "proposal_key", OK if origin else WARN,
              "memory proposal key " + (f"is set ({origin})" if origin else "is not set"),
              "" if origin else "memory proposals are sealed with a plain SHA-256 (accident-proof only): a same-user process that can write "
              "the file can reseal it. Set the HMAC key: `uv run coach config set-secret proposal_key --generate`"))
    s = cfg.alert_settings
    for ch, sec in (("ntfy", "ntfy_token"), ("email", "smtp_password"), ("telegram", "telegram_bot_token")):
        origin, err = secret_state(sec)
        if s.channel(ch).enabled and origin is None and sec != "ntfy_token":
            add(Check("secrets", sec, WARN, f"channel {ch} is enabled but its secret {sec} is not set"))
        else:
            add(Check("secrets", sec, INFO if origin is None else OK, f"{sec}: " + (f"set ({origin})" if origin else "not set (the channel is off)")))
    origin, err = secret_state("anthropic_api_key")
    needs_key = "anthropic-api" in (cfg.llm_backend, cfg.coach_backend)
    add(Check("secrets", "anthropic_api_key", WARN if needs_key and origin is None else (OK if origin else INFO),
              f"anthropic_api_key: " + (f"set ({origin})" if origin else "not set" + (" but a backend uses anthropic-api" if needs_key else " (optional)"))))

    # Enable Banking private key file
    key = cfg.eb_private_key_path
    if not key:
        add(Check("secrets", "eb_key", INFO, "no Enable Banking private key configured"))
    else:
        kp = Path(key).expanduser()
        if not kp.exists():
            add(Check("secrets", "eb_key", WARN, "the Enable Banking key file does not exist", _rel(kp, root)))
        else:
            mode = _mode(kp)
            bits = []
            st = OK
            if _loose(mode):
                bits.append(f"mode {oct(mode)} is readable by others: chmod 600")
                st = CRIT
            if _inside(kp, root):
                bits.append("the key is inside the project folder: move it outside (for example ~/.config/ai-finance-coach/)")
                st = max(st, WARN, key=RANK.get)
            add(Check("secrets", "eb_key", st, "Enable Banking private key file" + (": " + "; ".join(bits) if bits else " is private (0600) and outside the repo"),
                      "" , [f"{_rel(kp, root)} mode {oct(mode) if mode is not None else '?'}"]))
    pems = [p for p in tree_files(root, ("*.pem",), [cfg.data_dir, cfg.memory_dir, cfg.backup_dir]) if _inside(p, root)]
    if pems:
        add(Check("secrets", "pem_in_repo", WARN, f"{len(pems)} .pem file(s) inside the project folder",
                  "they are git-ignored (*.pem) but a private key does not belong in the repository folder", [_rel(p, root) for p in pems[:10]]))

    # ------------------------------------------------ storage
    db = cfg.db_path
    state = is_plaintext(db)
    if state is None:
        add(Check("storage", "db_encrypted", INFO, "no database yet", _rel(db, root)))
    elif state is True:
        add(Check("storage", "db_encrypted", CRIT, "the database is PLAINTEXT", "run `uv run coach db encrypt`, then delete the .plaintext.bak"))
    else:
        add(Check("storage", "db_encrypted", OK, "the database is encrypted (SQLCipher: no SQLite header)"))
    left = [p for p in plaintext_leftovers(db)]
    for pat in ("*.plaintext.bak", "*.encrypting", "*.importing"):
        left += [p for p in cfg.data_dir.glob(pat)] if cfg.data_dir.exists() else []
    left = sorted(set(left))
    if left:
        add(Check("storage", "plaintext_leftovers", CRIT, f"{len(left)} unencrypted copy(ies) of the data lie around",
                  "verify the encrypted database, then delete them", [_rel(p, root) for p in left]))
    else:
        add(Check("storage", "plaintext_leftovers", OK, "no plaintext leftovers (*.plaintext.bak, *.encrypting, *.importing)"))
    # database-looking files anywhere: a plaintext SQLite header is critical, any scratch copy inside the repo is a warning
    plain, scratch = [], []
    cands = []
    if cfg.data_dir.exists():                           # EVERY file of data_dir, any extension (-journal, -wal, .bak, a renamed copy)
        for dp, dn, fn in os.walk(cfg.data_dir, followlinks=False):
            cands += [Path(dp) / f for f in fn if not (Path(dp) / f).is_symlink()]
    cands += tree_files(root, ("*.db", "*.sqlite", "*.sqlite3", "*.bak", "*.db-wal", "*.db-journal", "*-journal", "*-wal"), [cfg.memory_dir, cfg.backup_dir])
    for p in sorted(set(cands)):
        if is_plain_sqlite_file(p):
            plain.append(p)
        elif p.suffix in (".db", ".sqlite", ".sqlite3") and _inside(p, root) and not _inside(p, cfg.data_dir):
            scratch.append(p)
    if plain:
        add(Check("storage", "plaintext_db_files", CRIT, f"{len(plain)} unencrypted database file(s) found",
                  "an unencrypted SQLite file holds your data in clear", [_rel(p, root) for p in plain]))
    if scratch:
        add(Check("storage", "scratch_db", WARN, f"{len(scratch)} database file(s) inside the project folder outside data/",
                  "scratch restores belong outside the repository", [_rel(p, root) for p in scratch]))
    if not plain and not scratch:
        add(Check("storage", "plaintext_db_files", OK, "no unencrypted database file found in data_dir or the project folder"))

    # permissions
    bad_dirs, bad_files, bad_secret_files = [], [], []
    secretish = re.compile(r"(?:\.key$|\.pem$|^ui-session|^ui-login|^ui-revoked|\.db(?:-wal|-shm)?$|\.bak$|\.tar\.enc$|^\.proposal)", re.I)
    for label, base in (("data_dir", cfg.data_dir), ("backups", cfg.backup_dir), ("memory", cfg.memory_dir)):
        if not base.exists():
            continue
        if _loose(_mode(base)):
            bad_dirs.append(f"{label}: {_rel(base, root)} mode {oct(_mode(base))} (want 0700)")
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for d in dirnames:
                p = Path(dirpath) / d
                if _loose(_mode(p)):
                    bad_dirs.append(f"{label}: {_rel(p, root)} mode {oct(_mode(p))}")
            for f in filenames:
                p = Path(dirpath) / f
                if p.is_symlink():
                    continue
                if _loose(_mode(p)):
                    (bad_secret_files if (secretish.search(f) and label != "memory") else bad_files).append(f"{label}: {_rel(p, root)} mode {oct(_mode(p))} (want 0600)")
    if bad_secret_files:
        add(Check("storage", "perms_secret_files", CRIT, f"{len(bad_secret_files)} key / database / backup file(s) readable by other users",
                  "chmod 600 (or run `coach security audit --fix-permissions`)", bad_secret_files[:15]))
    else:
        add(Check("storage", "perms_secret_files", OK, "keys, database and backup files are private (0600)"))
    loose = bad_dirs + bad_files
    if loose:
        add(Check("storage", "perms_dirs", WARN, f"{len(bad_dirs)} folder(s) and {len(bad_files)} file(s) of data_dir / backups / memory are open to other users",
                  "chmod 700 folders, 600 files (or run `coach security audit --fix-permissions`)", loose[:15]))
    else:
        add(Check("storage", "perms_dirs", OK, "data_dir, backups and memory are private (0700 / 0600)"))

    # backups: encrypted + retention
    from coach.backup import list_backups
    bdir = cfg.backup_dir
    backups = list_backups(bdir) if bdir.exists() else []
    stray = []
    if bdir.exists():
        for p in bdir.iterdir():
            if p.is_file() and p not in backups and not p.name.endswith(".part"):
                try:
                    head = p.read_bytes()[:16]
                except OSError:
                    continue
                if head == SQLITE_MAGIC or p.suffix in (".tar", ".zip", ".csv", ".json", ".db"):
                    stray.append(p)
    bad = []
    for p in backups:
        try:
            with open(p, "rb") as fh:
                if fh.read(len(BACKUP_MAGIC)) != BACKUP_MAGIC:
                    bad.append(p)
        except OSError:
            bad.append(p)
    if bad or stray:
        add(Check("storage", "backups_encrypted", CRIT, f"{len(bad) + len(stray)} file(s) in the backup folder are not encrypted backups",
                  "", [_rel(p, root) for p in bad + stray]))
    elif backups:
        add(Check("storage", "backups_encrypted", OK, f"all {len(backups)} backup(s) carry the encrypted-archive header"))
    if not backups:
        add(Check("storage", "backups_present", WARN, "no backup exists", "`uv run coach backup`"))
    else:
        newest = backups[-1]
        age = (now - dt.datetime.fromtimestamp(newest.stat().st_mtime, dt.timezone.utc)).days
        st = WARN if age > 14 or len(backups) > cfg.backup_retention else OK
        add(Check("storage", "backups_retention", st, f"{len(backups)} backup(s), newest {age} day(s) old, retention {cfg.backup_retention}",
                  ("the newest backup is older than 14 days" if age > 14 else "") +
                  (" more backups than the retention: `coach backup` prunes" if len(backups) > cfg.backup_retention else "")))

    # exports: every archive is encrypted (AFCEX1), no plaintext zip / csv / json export lies around (data_dir/exports and the project folder)
    exp_files = []
    for d in (cfg.data_dir / "exports", root):
        if d.exists():
            exp_files += [p for p in (d.iterdir() if d != root else root.glob("coach-export-*")) if p.is_file()]
    bad_exp = []
    for p in sorted(set(exp_files)):
        try:
            head = p.read_bytes()[:6]
        except OSError:
            continue
        if p.name.endswith(".zip.enc"):
            if head != EXPORT_MAGIC:
                bad_exp.append(p)
        elif p.suffix in (".zip", ".csv", ".json") or head[:2] == b"PK":
            bad_exp.append(p)
    if bad_exp:
        add(Check("storage", "exports_plaintext", CRIT, f"{len(bad_exp)} plaintext export file(s) found", "delete them once used", [_rel(p, root) for p in bad_exp]))
    elif exp_files:
        add(Check("storage", "exports_plaintext", OK, f"all {len(exp_files)} export archive(s) are encrypted"))

    # session key age
    kf = cfg.data_dir / "ui-session.key"
    if kf.exists():
        age_d = (now - dt.datetime.fromtimestamp(kf.stat().st_mtime, dt.timezone.utc)).days
        add(Check("secrets", "session_key_age", WARN if age_d > cfg.ui_key_rotation_days else OK,
                  f"web session key is {age_d} day(s) old (rotation every {cfg.ui_key_rotation_days})",
                  "`uv run coach ui --rotate-session-key`" if age_d > cfg.ui_key_rotation_days else ""))
    else:
        add(Check("secrets", "session_key_age", INFO, "no web session key yet (the web app was never started)"))

    # ------------------------------------------------ repository: .gitignore and secrets in the working tree
    gi = root / ".gitignore"
    if not gi.exists():
        add(Check("repository", "gitignore", CRIT, "there is no .gitignore: personal data could be committed"))
    else:
        pats = gitignore_patterns(gi.read_text())
        miss = [f"{p}  ({what})" for p, what in GITIGNORE_PROBES if not is_ignored(pats, p)]
        add(Check("repository", "gitignore", CRIT if any(m.startswith(("data/finance.db", "config.toml", "memory/household", "eb-private-key.pem", "backups/")) for m in miss) else WARN if miss else OK,
                  f"{len(miss)} sensitive path(s) are not covered by .gitignore" if miss else "every sensitive path is covered by .gitignore",
                  "add the missing patterns to .gitignore" if miss else "", miss))
    if do_scan:
        findings, n = scan_tree(root, [cfg.data_dir, cfg.memory_dir, cfg.backup_dir])
        crit = [f for f in findings if f["severity"] == CRIT]
        warn = [f for f in findings if f["severity"] == WARN]
        ev = [f"{f['path']}:{f['line']}  {f['kind']}" for f in crit + warn][:20]
        if crit:
            add(Check("repository", "secret_scan", CRIT, f"{len(crit)} secret-looking value(s) in the working tree ({n} files scanned)",
                      "values are never printed; mark a documented fixture with the text 'allowlist secret' on its line", ev))
        elif warn:
            add(Check("repository", "secret_scan", WARN, f"{len(warn)} high-entropy value(s) assigned to secret-like names ({n} files scanned)", "", ev))
        else:
            add(Check("repository", "secret_scan", OK, f"no secret-looking string in the working tree ({n} files scanned; data, memory, backups, .venv, node_modules skipped)"))

    # ------------------------------------------------ exposure (E11-3)
    from coach.api.security import is_loopback
    if is_loopback(cfg.ui_host) and not cfg.ui_allow_remote:
        add(Check("exposure", "ui_bind", OK, f"the web app binds to loopback ({cfg.ui_host}:{cfg.ui_port})"))
    elif not cfg.ui_allow_remote:
        add(Check("exposure", "ui_bind", CRIT, f"[ui] host {cfg.ui_host!r} is not loopback and allow_remote is off", "`coach ui` will refuse to start"))
    else:
        probs = []
        if not cfg.ui_remote_tls_ack:
            probs.append("allow_remote without remote_tls_ack: plain http would expose the session")
        if not cfg.ui_allowed_hosts:
            probs.append("allow_remote without allowed_hosts")
        add(Check("exposure", "ui_bind", CRIT if probs else WARN, "remote access to the web app is enabled" + (": " + "; ".join(probs) if probs else
                  " (TLS acknowledged): keep it behind Tailscale / a VPN, never the open internet")))
    run = (lsof or default_lsof)()
    if run is None:
        add(Check("exposure", "listening_sockets", SKIP, "could not list listening sockets (lsof unavailable)"))
    else:
        socks = parse_lsof(run)
        cb_port = 8443
        try:
            from urllib.parse import urlparse
            cb_port = urlparse(cfg.eb_redirect_url or "").port or 8443
        except ValueError:
            pass
        ours = {cfg.ui_port: "web app", cb_port: "bank callback"}
        exposed = [s for s in socks if s["port"] in ours and not _is_loopback_addr(s["addr"])]
        running = [s for s in socks if s["port"] in ours and _is_loopback_addr(s["addr"])]
        py_open = [s for s in socks if re.match(r"(?i)python|uvicorn|coach", s["command"]) and not _is_loopback_addr(s["addr"]) and s["port"] not in ours]
        ev = [f"{s['command']} pid {s['pid']} on {s['addr']}:{s['port']} ({ours.get(s['port'], 'python')})" for s in exposed + py_open]
        if exposed:
            add(Check("exposure", "listening_sockets", CRIT, f"{len(exposed)} coach port(s) listen on a non-loopback address", "stop it and bind to 127.0.0.1", ev))
        elif py_open:
            add(Check("exposure", "listening_sockets", WARN, f"{len(py_open)} Python process(es) listen on a non-loopback address (not coach ports)", "", ev))
        else:
            add(Check("exposure", "listening_sockets", OK, "nothing of coach listens beyond loopback"
                      + (f" (running: {', '.join(ours[s['port']] + ' ' + str(s['port']) for s in running)})" if running else " (no coach server is running now)")))
    # MCP: stdio only
    mcp_src = (Path(__file__).parent / "mcp" / "server.py")
    mcp_ok, why = True, ""
    try:
        txt = mcp_src.read_text()
        if "stdio_server" not in txt or re.search(r"\b(?:sse|streamable|uvicorn|http|socket|bind|listen)\b", txt.replace("stdio_server", "")):
            mcp_ok, why = False, "coach/mcp/server.py is not stdio-only"
    except OSError:
        mcp_ok, why = False, "coach/mcp/server.py not readable"
    mj = root / ".mcp.json"
    if mj.exists():
        try:
            servers = (json.loads(mj.read_text()) or {}).get("mcpServers", {})
            remote = [n for n, v in servers.items() if v.get("url") or v.get("type") in ("sse", "http")]
            if remote:
                mcp_ok, why = False, f".mcp.json declares a network MCP server: {', '.join(remote)}"
        except ValueError:
            mcp_ok, why = False, ".mcp.json is not valid JSON"
    add(Check("exposure", "mcp_stdio", OK if mcp_ok else CRIT, "the MCP server speaks stdio only (no port)" if mcp_ok else why))
    # callback server
    from coach.ingest.callback import LOOPBACK, parse_redirect, CallbackError
    if cfg.eb_redirect_url:
        try:
            parse_redirect(cfg.eb_redirect_url)
            add(Check("exposure", "callback_loopback", OK, "the bank callback server binds to loopback only (redirect URL is local)"))
        except CallbackError as e:
            add(Check("exposure", "callback_loopback", WARN, "the redirect URL is not local", str(e)))
    else:
        add(Check("exposure", "callback_loopback", INFO, "no redirect URL configured"))
    td = cfg.tls_dir
    if td.exists():
        loose_tls = [p for p in td.iterdir() if p.is_file() and p.suffix == ".key" and _loose(_mode(p))]     # a certificate is public
        add(Check("exposure", "tls_perms", CRIT if loose_tls else OK, "the TLS private key " + ("is readable by others" if loose_tls else "is private"),
                  "chmod 600" if loose_tls else "", [_rel(p, root) for p in loose_tls]))
    # Claude Code permission rules
    st = root / ".claude" / "settings.json"
    if st.exists():
        try:
            deny = (json.loads(st.read_text()).get("permissions") or {}).get("deny") or []
            add(Check("exposure", "agent_rules", OK if deny else WARN, f".claude/settings.json holds {len(deny)} deny rule(s) for the coding agent",
                      "" if deny else "no deny rules: a coding agent running as you can read data/ and memory/ and run destructive commands"))
        except ValueError:
            add(Check("exposure", "agent_rules", WARN, ".claude/settings.json is not valid JSON"))
    else:
        add(Check("exposure", "agent_rules", WARN, "no .claude/settings.json: a coding agent running as you has no permission rules here (start from docs/claude-settings.example.json)"))

    # the privacy mode (E11-4), for completeness
    mode = "offline" if cfg.privacy_offline else "local_only" if cfg.privacy_local_only else "standard"
    add(Check("exposure", "privacy_mode", INFO, f"privacy mode: {mode}", "`coach privacy status` shows what each outbound path may do"))
    order = {"secrets": 0, "storage": 1, "repository": 2, "exposure": 3}
    return sorted(checks, key=lambda c: order.get(c.area, 9))          # stable: grouped by area, checks keep their order inside it


def worst(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=lambda s: RANK[s], default=OK)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == CRIT for c in checks) else 0


def summary(checks: list[Check]) -> dict:
    n = {k: sum(1 for c in checks if c.status == k) for k in (OK, INFO, WARN, CRIT, SKIP)}
    return {"critical": n[CRIT], "warn": n[WARN], "info": n[INFO], "ok": n[OK], "skipped": n[SKIP], "worst": worst(checks)}


def _tighten(path: Path) -> Optional[str]:
    """Remove the group / other bits of `path` (mode & ~0o077): never adds a bit, never touches a symlink. Returns what was done."""
    if path.is_symlink():
        return None
    mode = _mode(path)
    if mode is None or not _loose(mode):
        return None
    new = mode & ~0o077
    os.chmod(path, new)
    return f"{path} {oct(mode)} -> {oct(new)}"


def _forbidden_target(p: Path) -> bool:
    home = Path.home().resolve()
    return p.resolve() in {Path("/"), home, *home.parents}


def fix_permissions(cfg) -> list[str]:
    """Tighten (mode & ~0o077) the folders and files of data_dir, backups and memory that are open to other users. Returns what changed.
    Only tightens, never follows a symlink, and refuses the home folder and its parents as a base."""
    changed = []
    for base in (cfg.data_dir, cfg.backup_dir, cfg.memory_dir):
        if not base.exists():
            continue
        if _forbidden_target(base):
            changed.append(f"refused: {base} is the home folder or one of its parents")
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for d in [Path(dirpath)] + [Path(dirpath) / x for x in dirnames]:
                if r := _tighten(d):
                    changed.append(r)
            for f in filenames:
                if r := _tighten(Path(dirpath) / f):
                    changed.append(r)
    return changed


def fix_key_file(cfg, isatty=None, input_fn=input) -> Optional[str]:
    """Offer (terminal only, typed y) to chmod 600 the Enable Banking private key when it is open to others."""
    if not cfg.eb_private_key_path:
        return None
    kp = Path(cfg.eb_private_key_path).expanduser()
    if not kp.exists() or not _loose(_mode(kp)):
        return None
    isatty = isatty or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    if not isatty():
        return f"{kp}: open to others; run this command in a terminal to be offered the fix (or chmod 600 it)"
    if input_fn(f"The Enable Banking key {kp} is readable by others (mode {oct(_mode(kp))}). chmod it to owner-only? [y/N] ").strip().lower() in ("y", "yes"):
        return _tighten(kp)
    return None


# ---------------------------------------------------------------- printing

ICON = {OK: "ok  ", INFO: "info", WARN: "WARN", CRIT: "CRIT", SKIP: "skip"}


def format_report(checks: list[Check]) -> str:
    lines = []
    area = None
    for c in checks:
        if c.area != area:
            area = c.area
            lines.append(f"\n== {area} ==")
        lines.append(f"[{ICON[c.status]}] {c.title}")
        if c.detail:
            lines.append(f"       {c.detail}")
        for it in c.items:
            lines.append(f"         - {it}")
    sm = summary(checks)
    lines.append(f"\n{sm['critical']} critical, {sm['warn']} warning(s), {sm['info']} info, {sm['ok']} ok")
    return "\n".join(lines).lstrip("\n")


def cmd_audit(a, cfg) -> int:
    if getattr(a, "fix_permissions", False):
        for line in fix_permissions(cfg):
            print(f"fixed: {line}")
        if r := fix_key_file(cfg):
            print(f"fixed: {r}" if "->" in r else r)
    checks = audit(cfg)
    if a.json:
        print(json.dumps({"summary": summary(checks), "checks": [c.as_dict() for c in checks]}, indent=2))
    else:
        print(format_report(checks))
    code = exit_code(checks)
    if code:
        sys.exit(code)
    return 0


def register(sub, add):
    sp = sub.add_parser("security", help="security audit: secrets, permissions, encryption, exposure (E11-2, E11-3)",
                        description="read-only audit of how this installation protects its secrets and what it exposes")
    ssub = sp.add_subparsers(dest="security_cmd", required=True, metavar="SUBCOMMAND")
    s = add(ssub, "audit", cmd_audit, "audit secrets, permissions, encryption, plaintext leftovers, .gitignore and exposure; exit 1 on a critical")
    s.add_argument("--json", action="store_true", help="machine-readable output (no secret values in it)")
    s.add_argument("--fix-permissions", action="store_true", help="first remove the group / other bits of the folders and files of data_dir, backups and memory (never adds a bit), and offer to do the same for the Enable Banking key")
