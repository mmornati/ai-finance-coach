"""`coach wipe` (E11-6): delete everything the coach holds, interactively, after an export.

Only a human at a terminal can run it: it needs a TTY on stdin and stdout, there is NO ``--yes``, and the last confirmation is the typed
phrase ``DELETE MY DATA``. ``--dry-run`` lists exactly what would be deleted and changes nothing (no terminal needed, no network).

Order (each step after the previous one succeeded; any refusal or failure stops BEFORE anything is deleted, except where noted):

1. the plan is shown (database, memory, data_dir, backups, Keychain secrets, bank sessions) and the user is asked, SEPARATELY, whether to
   revoke the Enable Banking sessions, delete the backups and delete the Keychain secrets;
2. unless ``--no-export``: an encrypted export is written (outside what will be deleted) and verified; no export, no deletion;
3. the typed phrase;
4. revoke the bank sessions (``DELETE /sessions/{id}``: the ONLY network call, only if the user said yes; through the egress gate);
5. delete the database files, ``memory/`` (including ``.history.git`` and ``.proposals``; the template files of the repository are kept),
   ``data_dir`` (keys, TLS, logs), the backups (if asked), the Keychain secrets (if asked).

This is the ONLY code path that deletes Keychain items. Deleted files are unlinked, not shredded: on an SSD with FileVault the key is what
protects them; to make the recovery impossible, also delete the Keychain secrets (the data was encrypted with them).
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from coach import secrets
from coach.db import connect

PHRASE = "DELETE MY DATA"
TEMPLATE_NAMES = {"README.md", "_template.yaml"}          # files of the repository inside memory/: kept


class WipeError(Exception):
    pass


@dataclass
class Plan:
    db_files: list[Path] = field(default_factory=list)
    memory: list[Path] = field(default_factory=list)       # files and folders under memory/ that go
    memory_kept: list[Path] = field(default_factory=list)
    data_dir: list[Path] = field(default_factory=list)     # what is under data_dir besides the database
    backups: list[Path] = field(default_factory=list)
    keychain: list[str] = field(default_factory=list)      # names of the secrets present in the Keychain
    sessions: list[str] = field(default_factory=list)      # ids of the bank sessions that could be revoked (read from the database)
    dirs_to_remove: list[Path] = field(default_factory=list)
    preserved: list[Path] = field(default_factory=list)     # backups / exports that live inside data_dir and are kept unless the backups are deleted


USER_DIRS = ("Documents", "Desktop", "Downloads", "Library", "Pictures", "Movies", "Music", "Public", "Applications", ".ssh", ".config", ".local", "iCloud Drive")
# what a folder created by this application holds: at least one of these must be there, or the folder is not ours and is never deleted
KNOWN = {"data": {"finance.db", "logs", "tls", "ui-session.key", "exports", "ui-state.json", "ui-login.json"},
         "memory": {"household.yaml", "categorization.yaml", "profile.md", "preferences.md", "README.md", ".history.git", ".proposals",
                    "assets.yaml", "liabilities", "contracts", "documents", "events.md", "open-questions.yaml"}}


def _safe_target(path: Path, cfg, kind: str | None = None) -> None:
    """Refuse the filesystem root, the home folder and its parents, the well-known user folders (Documents, Desktop, Downloads, Library ...),
    the project root and its parents, and (with `kind`) any folder that does not look like one this application created."""
    p = path.resolve()
    home = Path.home().resolve()
    forbidden = {Path("/"), home, cfg.root.resolve(), *cfg.root.resolve().parents, *home.parents, *(home / d for d in USER_DIRS)}
    if p in forbidden or len(p.parts) < 3 or any(p in (home / d).parents for d in USER_DIRS if (home / d) != p):
        raise WipeError(f"refusing to delete {p}: it is the root, the home folder, a well-known user folder or a parent of the project")
    if kind == "backups":
        from coach.backup import PREFIX
        stray = [c.name for c in p.iterdir() if not (c.name.startswith(PREFIX) or c.name.endswith(".part") or c.name == ".DS_Store")] if p.is_dir() else []
        if stray:
            raise WipeError(f"refusing to delete {p}: it holds files that are not coach backups ({', '.join(stray[:3])})")
    elif kind in KNOWN and p.is_dir():
        entries = {c.name for c in p.iterdir()}
        if entries and not entries & KNOWN[kind]:
            raise WipeError(f"refusing to delete {p}: it does not look like a coach {kind} folder (none of {', '.join(sorted(KNOWN[kind])[:4])} ... is in it)")


def _list_tree(base: Path) -> list[Path]:
    out = []
    for p in sorted(base.rglob("*")):
        out.append(p)
    return out


def build_plan(cfg, *, with_sessions: bool = False, insecure: bool = False, has_secret: Optional[Callable[[str], bool]] = None) -> Plan:
    """What `wipe` would delete. Reads the filesystem and (with_sessions) the session ids from the database; never deletes, never uses the network."""
    plan = Plan()
    db = cfg.db_path
    for pat in ("", "-wal", "-shm", "-journal", ".plaintext.bak", ".encrypting", ".importing"):
        p = db.with_name(db.name + pat)
        if p.exists():
            plan.db_files.append(p)
    for p in sorted(db.parent.glob(db.name + ".pre-migrate-*.bak")) + sorted(db.parent.glob(db.name + ".pre-import*.bak")):
        if p not in plan.db_files:
            plan.db_files.append(p)
    mem = cfg.memory_dir
    if mem.exists():
        _safe_target(mem, cfg, "memory")
        inside_repo = mem.resolve().is_relative_to(cfg.root.resolve())
        kept_dirs: set[Path] = set()
        for p in _list_tree(mem):
            if inside_repo and p.is_file() and p.name in TEMPLATE_NAMES:
                plan.memory_kept.append(p)
                kept_dirs.update(p.parents)
        for p in _list_tree(mem):
            if p in plan.memory_kept or (p.is_dir() and p in kept_dirs):
                continue
            plan.memory.append(p)
        if not inside_repo or not plan.memory_kept:
            plan.dirs_to_remove.append(mem)
    dd = cfg.data_dir
    if dd.exists():
        _safe_target(dd, cfg, "data")
        keep = [x for x in (cfg.backup_dir, dd / "exports") if x.exists() and x.resolve().is_relative_to(dd.resolve())]
        plan.preserved = [x.resolve() for x in keep]
        for p in _list_tree(dd):
            if p not in plan.db_files and not any(p.resolve() == k or k in p.resolve().parents for k in plan.preserved):
                plan.data_dir.append(p)
        plan.dirs_to_remove.append(dd)
    bd = cfg.backup_dir
    if bd.exists():
        _safe_target(bd, cfg, "backups")
        plan.backups = _list_tree(bd)
        if bd.resolve() != dd.resolve() and not bd.resolve().is_relative_to(dd.resolve()):
            plan.dirs_to_remove.append(bd)
    check = has_secret or secrets.in_keychain
    for name in secrets.SECRETS:
        try:
            if check(name):
                plan.keychain.append(name)
        except secrets.SecretBackendError:
            pass
    if with_sessions and db.exists():
        try:
            con = connect(cfg, insecure=insecure, migrate=False)
            try:
                plan.sessions = [r[0] for r in con.execute("SELECT session_id FROM sessions WHERE status IS NULL OR status <> 'replaced'")]
            finally:
                con.close()
        except Exception:                                                          # noqa: BLE001 - no database / no key: nothing to revoke
            plan.sessions = []
    return plan


def format_plan(plan: Plan, cfg) -> str:
    root = cfg.root.resolve()

    def rel(p):
        try:
            return str(p.resolve().relative_to(root))
        except ValueError:
            return str(p)

    out = ["The following would be DELETED:", ""]
    out.append(f"database ({len(plan.db_files)} file(s)):")
    out += [f"  {rel(p)}" for p in plan.db_files] or ["  (none)"]
    files = [p for p in plan.memory if p.is_file()]
    out.append(f"\nmemory folder: {len(files)} file(s) incl. documents, .history.git and .proposals" + (f" ({len(plan.memory_kept)} repository template file(s) are kept)" if plan.memory_kept else ""))
    for p in files[:12]:
        out.append(f"  {rel(p)}")
    if len(files) > 12:
        out.append(f"  ... and {len(files) - 12} more")
    out.append(f"\ndata folder (keys, TLS, logs, exports): {len([p for p in plan.data_dir if p.is_file()])} file(s) besides the database")
    for p in [p for p in plan.data_dir if p.is_file()][:12]:
        out.append(f"  {rel(p)}")
    bfiles = [p for p in plan.backups if p.is_file()]
    if plan.preserved:
        out.append("\nkept unless the backups are deleted (inside the data folder): " + ", ".join(rel(p) for p in plan.preserved))
    out.append(f"\nbackups (asked separately): {len(bfiles)} file(s) in {rel(cfg.backup_dir)}")
    out += [f"  {rel(p)}" for p in bfiles[:12]]
    out.append(f"\nKeychain secrets (asked separately): {', '.join(plan.keychain) or '(none stored in the Keychain)'}")
    out.append(f"\nEnable Banking sessions that can be revoked (asked separately; a network call): {len(plan.sessions) if plan.sessions else 'unknown until the database is read'}")
    return "\n".join(out)


def _rm(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink(missing_ok=True)
    elif p.is_dir():
        shutil.rmtree(p)


def execute(plan: Plan, cfg, *, delete_backups: bool, delete_keychain: bool, out=print, keep_secrets: tuple = ()) -> dict:
    """Delete what the plan lists (and, if asked, the backups and the Keychain secrets)."""
    done = {"files": 0, "keychain": 0}
    for p in plan.db_files:
        _rm(p)
        done["files"] += 1
    for p in sorted(plan.memory, key=lambda x: len(x.parts), reverse=True):
        if p.is_dir() and any(p.iterdir()):
            continue                                    # a folder that still holds a kept template file
        _rm(p)
        done["files"] += 1
    mem = cfg.memory_dir
    if mem in plan.dirs_to_remove and mem.exists():
        shutil.rmtree(mem)
    if cfg.data_dir.exists():
        keep = [] if delete_backups else plan.preserved
        if not keep:
            shutil.rmtree(cfg.data_dir)
        else:                                            # backups / exports inside data_dir that the user did not ask to delete stay
            for child in sorted(cfg.data_dir.rglob("*"), key=lambda x: len(x.parts), reverse=True):
                rc = child.resolve()
                if any(rc == k or k in rc.parents or rc in k.parents for k in keep):
                    continue
                if child.is_dir() and not child.is_symlink():
                    if not any(child.iterdir()):
                        child.rmdir()
                else:
                    child.unlink(missing_ok=True)
    out(f"deleted the database, the memory folder and {cfg.data_dir}" + (" (backups and exports inside it were kept)" if plan.preserved and not delete_backups else ""))
    if delete_backups and cfg.backup_dir.exists():
        shutil.rmtree(cfg.backup_dir)
        out(f"deleted the backups in {cfg.backup_dir}")
    if delete_keychain:
        for name in [n for n in plan.keychain if n not in keep_secrets]:
            secrets.delete_secret(name)
            done["keychain"] += 1
        out(f"deleted {done['keychain']} Keychain secret(s)")
    return done


def revoke_sessions(cfg, session_ids: list[str], client=None, out=print) -> dict:
    """DELETE /sessions/{id} for each id. A 404 means the bank no longer knew it (counted as revoked); other failures are reported and do not stop the wipe."""
    from coach.ingest.client import ApiError, EnableBankingClient
    ok, failed = 0, []
    try:
        client = client or EnableBankingClient.from_config(cfg)
    except Exception as e:                                                         # noqa: BLE001
        return {"revoked": 0, "failed": [f"no Enable Banking client ({type(e).__name__})"]}
    for sid in session_ids:
        try:
            client.call("DELETE", f"/sessions/{sid}")
            ok += 1
        except ApiError as e:
            if e.status == 404:
                ok += 1
            else:
                failed.append(f"HTTP {e.status}")
        except Exception as e:                                                     # noqa: BLE001 - egress refusal (offline), network down ...
            failed.append(type(e).__name__ if not str(e) else str(e)[:120])
    out(f"revoked {ok} of {len(session_ids)} bank session(s)" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return {"revoked": ok, "failed": failed}


def _yes(input_fn, prompt: str) -> bool:
    return input_fn(prompt + " [y/N] ").strip().lower() in ("y", "yes")


def run_wipe(a, cfg, *, isatty: Optional[Callable[[], bool]] = None, input_fn: Callable[[str], str] = input, out=print,
             client=None, export_fn=None, now: Optional[dt.datetime] = None) -> int:
    isatty = isatty or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    if a.dry_run:
        plan = build_plan(cfg, with_sessions=False, insecure=a.insecure)
        out(format_plan(plan, cfg))
        out("\nDRY RUN: nothing was deleted, nothing was sent.")
        return 0
    if not isatty():
        out("error: `coach wipe` needs a terminal. It is a human action: it cannot run from a script or an agent (there is no --yes).")
        return 2
    plan = build_plan(cfg, with_sessions=True, insecure=a.insecure)
    out(format_plan(plan, cfg))
    out("")
    revoke = bool(plan.sessions) and _yes(input_fn, f"Revoke the {len(plan.sessions)} Enable Banking session(s) at the bank (a network call)?")
    drop_backups = _yes(input_fn, "Also delete the encrypted BACKUPS? (they are your only copy if you keep them)")
    drop_keys = _yes(input_fn, "Also delete the Keychain SECRETS? (the data becomes unrecoverable, including any export or backup made with these keys)")
    keep_secrets: tuple = ()
    drop_backup_key = False
    if drop_keys and not a.no_export and "backup_key" in plan.keychain:
        # the safety export is encrypted with backup_key: deleting it makes that export unreadable, so it is kept unless you take a copy
        drop_backup_key = _yes(input_fn, "The safety export can only be decrypted with backup_key. Delete backup_key too? (it will be shown ONCE on this terminal: save it first)")
        if not drop_backup_key:
            keep_secrets = ("backup_key",)
    export_path = None
    if not a.no_export:
        if not secrets.lookup("backup_key")[0]:
            out("error: no backup_key secret: the safety export cannot be encrypted. Set it (`coach config set-secret backup_key --generate`) or pass --no-export.")
            return 2
        default = Path(a.export_to) if a.export_to else Path.home() / f"ai-finance-coach-export-{(now or dt.datetime.now()):%Y%m%d-%H%M%S}.zip.enc"
        for guarded in (cfg.data_dir, cfg.memory_dir, cfg.backup_dir):
            if guarded.exists() and default.resolve().is_relative_to(guarded.resolve()):
                out(f"error: the export file {default} is inside {guarded}, which will be deleted: choose --export-to elsewhere.")
                return 2
        out(f"\nA safety export (encrypted with backup_key) will be written to {default} and verified BEFORE anything is deleted.")
    if input_fn(f"\nType {PHRASE!r} to delete everything listed above (anything else aborts): ").strip() != PHRASE:
        out("aborted: nothing was deleted.")
        return 1
    if not a.no_export:
        from coach import export as export_mod
        try:
            export_path = (export_fn or export_mod.export_encrypted)(cfg, default, insecure=a.insecure)
        except Exception as e:                                                     # noqa: BLE001
            out(f"error: the safety export failed ({type(e).__name__}: {str(e)[:160]}): nothing was deleted.")
            return 2
        out(f"safety export written and verified: {export_path}")
    if revoke:
        revoke_sessions(cfg, plan.sessions, client=client, out=out)
    if drop_backup_key:
        out(f"\nbackup_key (shown once, save it in a password manager NOW): {secrets.get_secret('backup_key', required=False)}")
        input_fn("Press Enter when it is saved (it is deleted right after): ")
    execute(plan, cfg, delete_backups=drop_backups, delete_keychain=drop_keys, out=out, keep_secrets=keep_secrets)
    if keep_secrets:
        out(f"kept the Keychain secret(s) {', '.join(keep_secrets)}: your export cannot be read without it")
    out("\ndone. Everything listed was deleted." + (f" Your export: {export_path}" if export_path else ""))
    return 0


def cmd_wipe(a, cfg) -> None:
    code = run_wipe(a, cfg)
    if code:
        sys.exit(code)


def register(sub, add):
    s = add(sub, "wipe", cmd_wipe, "delete ALL your data (database, memory, data folder; backups and Keychain secrets asked separately): terminal only (E11-6)")
    s.add_argument("--dry-run", action="store_true", help="list everything that would be deleted; delete nothing, send nothing")
    s.add_argument("--no-export", action="store_true", help="do not write the safety export first")
    s.add_argument("--export-to", metavar="FILE", help="where the safety export goes (default: your home folder; never inside what is deleted)")
