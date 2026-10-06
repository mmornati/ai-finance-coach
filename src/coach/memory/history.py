"""Change history of the memory folder (E3-4): a private git repository whose git directory is
``<memory>/.history.git`` and whose work tree is the memory folder itself.

Why git and not a journal of patches: history, diff and revert come for free and are robust (content-addressed,
three-way revert, `git log -- file`), the user can inspect it with any git tool
(``git --git-dir=memory/.history.git --work-tree=memory log``), and edits made BY HAND in between are captured too
(every coach write first snapshots them, so a revert never destroys them). The git dir has a non-standard name on
purpose: the project repository (or any parent) never sees a nested ``.git`` inside ``memory/`` and keeps ignoring
the folder; every git call pins GIT_DIR / GIT_WORK_TREE and ignores the user's global and system git config (no
signing prompts, no hooks), and nothing here ever configures a remote or pushes.

Not versioned (listed in the repository's info/exclude): ``documents/`` (binary files, content-addressed),
``.proposals/``, ``.backups/``, the lock file and temp files.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

GIT_DIRNAME = ".history.git"
EXCLUDES = ["/documents/", "/.proposals/", "/.backups/", "/.lock", "/.history.git/", "*.tmp", ".DS_Store"]
TRAILER_RE = re.compile(r"^(Reason|Source): (.*)$", re.M)


class HistoryError(RuntimeError):
    pass


class HistoryUnavailable(HistoryError):
    """git is not installed (or history is switched off but a history command was asked for)."""


@dataclass
class Change:
    id: str                     # short commit hash: what `memory history` prints and `memory revert` takes
    date: str
    subject: str
    files: list[str] = field(default_factory=list)
    reason: str | None = None
    source: str | None = None


MINIMAL_CONFIG = """[core]
\trepositoryformatversion = 0
\tfilemode = true
\tbare = false
\tlogallrefupdates = true
"""


def sanitize_gitdir(gitdir: Path) -> bool:
    """The history repository is trusted data, not trusted configuration: git runs commands named in a repo's config
    (filter / diff / textconv drivers, hooks, fsmonitor, pager, sshCommand...) and in info/attributes. Whatever the
    file holds, it is rewritten to a fixed minimal template and info/attributes is deleted. Returns True if anything
    changed. (This also removes the absolute core.worktree.)"""
    changed = False
    cfg = gitdir / "config"
    try:
        if not cfg.exists() or cfg.read_text() != MINIMAL_CONFIG:
            cfg.write_text(MINIMAL_CONFIG)
            changed = True
        attrs = gitdir / "info" / "attributes"
        if attrs.exists() or attrs.is_symlink():
            attrs.unlink()
            changed = True
        hooks = gitdir / "hooks"
        if hooks.is_dir():
            for h in hooks.iterdir():
                if not h.name.endswith(".sample"):
                    h.unlink()
                    changed = True
    except OSError:
        pass
    return changed


def strip_worktree_setting(config: Path) -> bool:
    """Remove every `worktree = ...` line of a git config file (the absolute core.worktree that `git init` records under
    GIT_WORK_TREE and that would point a restored / moved copy at the OLD folder). Touches nothing else."""
    try:
        text = config.read_text()
    except OSError:
        return False
    lines = text.splitlines(True)
    kept = [ln for ln in lines if not re.match(r"\s*worktree\s*=", ln, re.I)]
    if len(kept) == len(lines):
        return False
    config.write_text("".join(kept))
    return True


def git_available() -> bool:
    return shutil.which("git") is not None


class MemoryRepo:
    def __init__(self, memory_dir: Path):
        self.root = Path(memory_dir)
        self.gitdir = self.root / GIT_DIRNAME

    # ------------------------------------------------------------ plumbing
    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(LC_ALL="C", LANG="C", GIT_DIR=str(self.gitdir), GIT_WORK_TREE=str(self.root), GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0",
                   GIT_EDITOR="true", GIT_AUTHOR_NAME="coach", GIT_AUTHOR_EMAIL="coach@localhost",
                   GIT_COMMITTER_NAME="coach", GIT_COMMITTER_EMAIL="coach@localhost")
        return env

    def git(self, *args: str, check: bool = True, input: str | None = None,
            env: dict | None = None) -> subprocess.CompletedProcess:
        if not git_available():
            raise HistoryUnavailable("git is not installed: the memory change history needs it "
                                     "(or set [memory] history = false in config.toml to write without history)")
        if (self.gitdir / "HEAD").exists():
            sanitize_gitdir(self.gitdir)
        cmd = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.attributesFile=" + os.devnull,
               "-c", "core.pager=cat", "-c", "core.sshCommand=false", "-c", "diff.external=", "-c", "commit.gpgsign=false", "-c", "core.quotepath=off",
               "-c", "core.autocrlf=false", "-c", "core.abbrev=8", "-c", "core.fsmonitor=false", *args]
        p = subprocess.run(cmd, cwd=self.root, env={**self._env(), **(env or {})}, capture_output=True, text=True,
                           input=input)
        if check and p.returncode != 0:
            raise HistoryError(f"git {args[0]} failed: {(p.stderr or p.stdout).strip()[:400]}")
        return p

    @contextmanager
    def lock(self):
        """Serialises writers (two `coach` processes, the UI): an exclusive flock on <memory>/.lock."""
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def repair_worktree(self) -> None:
        """Called whenever a store is opened: the repository configuration is reset to the minimal template (no filters,
        hooks or absolute core.worktree), see :func:`sanitize_gitdir`."""
        if self.exists():
            sanitize_gitdir(self.gitdir)

    # ------------------------------------------------------------ state
    def exists(self) -> bool:
        return (self.gitdir / "HEAD").exists()

    def ensure(self) -> None:
        """Create the repository on first use and record the files that already exist as the first snapshot."""
        if self.exists():
            self.repair_worktree()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--initial-branch=main")
        os.chmod(self.gitdir, 0o700)             # the history holds every past version of the personal files
        # `git init` under GIT_WORK_TREE records an ABSOLUTE core.worktree: a restored copy of the folder would point
        # at the old location. Every call pins GIT_WORK_TREE itself, so the setting is removed.
        self.git("config", "--unset-all", "core.worktree", check=False)
        self.repair_worktree()
        (self.gitdir / "info").mkdir(exist_ok=True)
        (self.gitdir / "info" / "exclude").write_text("\n".join(EXCLUDES) + "\n")
        self.git("add", "-A")
        self._commit("coach: initial snapshot of the memory folder", allow_empty=True)

    def _commit(self, message: str, allow_empty: bool = False) -> str | None:
        if not allow_empty and self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None                                  # nothing staged: no empty change
        args = ["commit", "-q", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        p = self.git(*args, check=False)
        if p.returncode != 0:
            raise HistoryError(f"git commit failed: {(p.stderr or p.stdout).strip()[:400]}")
        return self.git("rev-parse", "--short=8", "HEAD").stdout.strip()

    def dirty(self) -> list[str]:
        """Files changed outside the coach since the last recorded change (hand edits)."""
        out = self.git("status", "--porcelain", "--untracked-files=all").stdout
        return [ln[3:].strip().strip('"') for ln in out.splitlines() if ln.strip()]

    def snapshot_external(self) -> str | None:
        """Record hand edits so they are never lost by a later revert and show up in `history`."""
        self.ensure()
        files = self.dirty()
        if not files:
            return None
        self.git("add", "-A")
        return self._commit("coach: snapshot external edits (" + ", ".join(sorted(files)[:6])
                            + (" ..." if len(files) > 6 else "") + ")\n\nSource: external")

    # ------------------------------------------------------------ recording
    def record(self, action: str, files: list[str], reason: str | None = None, source: str = "cli",
               detail: str | None = None) -> str | None:
        """Commit the given files. The caller holds the lock and has already snapshotted external edits."""
        self.ensure()
        self.git("add", "-A", "--", *files)
        subject = f"coach: {action} " + ", ".join(files)
        if detail:
            subject += f" ({detail})"
        flat = re.sub(r"\s+", " ", reason).strip() if reason else ""
        body = [f"Source: {source}"] + ([f"Reason: {flat}"] if flat else [])
        return self._commit(subject + "\n\n" + "\n".join(body))

    def note(self, subject: str, source: str = "cli", reason: str | None = None) -> str | None:
        """Record an event that changes no file (a rejected proposal) as an empty commit."""
        self.ensure()
        flat = re.sub(r"\s+", " ", reason).strip() if reason else ""
        body = [f"Source: {source}"] + ([f"Reason: {flat}"] if flat else [])
        return self._commit(subject + "\n\n" + "\n".join(body), allow_empty=True)

    # ------------------------------------------------------------ reading
    def log(self, file: str | None = None, limit: int = 50) -> list[Change]:
        if not self.exists():
            return []
        fmt = "\x1e%h\x1f%aI\x1f%s\x1f%b\x1d"
        args = ["log", f"--max-count={limit}", f"--format={fmt}", "--name-only"]
        if file:
            args += ["--", file]
        out = self.git(*args).stdout
        changes = []
        for rec in out.split("\x1e")[1:]:
            head, _, names = rec.partition("\x1d")
            fields = head.split("\x1f", 3)
            if len(fields) < 4:
                continue
            h, date, subject, body = fields
            tr = dict(TRAILER_RE.findall(body))
            changes.append(Change(h, date, subject, [x for x in names.split("\n") if x.strip()],
                                  tr.get("Reason"), tr.get("Source")))
        return changes

    def diff(self, ref: str | None = None, file: str | None = None) -> str:
        """No ref: uncommitted differences (hand edits not yet recorded). A change id: that change's patch."""
        if not self.exists():
            return ""
        tail = ["--", file] if file else []
        if ref:
            self.resolve(ref)
            return self.git("show", "--format=%h %s%n", "--no-color", "--no-ext-diff", "--no-textconv", ref,
                            *tail).stdout
        self.git("add", "-N", "-A", check=False)                 # make untracked files visible to `diff`
        out = self.git("diff", "--no-color", "--no-ext-diff", "--no-textconv", "HEAD", *tail).stdout
        self.git("reset", "-q", check=False)
        return out

    def resolve(self, ref: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{4,40}", ref or ""):
            raise HistoryError(f"{ref!r} is not a change id (see `coach memory history`)")
        p = self.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        if p.returncode != 0:
            raise HistoryError(f"unknown change id {ref!r} (see `coach memory history`)")
        return p.stdout.strip()

    def show_file(self, ref: str, file: str) -> str | None:
        p = self.git("show", f"{ref}:{file}", check=False)
        return p.stdout if p.returncode == 0 else None

    def files_of(self, ref: str) -> list[str]:
        return [x for x in self.git("show", "--format=", "--name-only", ref).stdout.splitlines() if x]

    # ------------------------------------------------------------ revert
    def revert(self, ref: str, validate=None) -> tuple[str, list[str]]:
        """Undo one change with a new change. Hand edits are snapshotted first; a conflict, a revert of the first
        snapshot, or a result that `validate(files)` rejects leaves the folder exactly as it was."""
        self.ensure()
        full = self.resolve(ref)
        root = self.git("rev-list", "--max-parents=0", "HEAD").stdout.split()
        if full in root:
            raise HistoryError("the first snapshot cannot be reverted (it is the starting point)")
        self.snapshot_external()
        before = self.git("rev-parse", "HEAD").stdout.strip()
        files = self.files_of(full)
        p = self.git("revert", "--no-commit", "--no-edit", full, check=False)
        if p.returncode != 0:
            self.git("reset", "-q", "--hard", before, check=False)
            raise HistoryError(f"cannot revert {ref}: later changes touch the same lines ({(p.stderr or p.stdout).strip()[:200]}); "
                               "edit the file instead")
        if validate is not None:
            problems = validate(files)
            if problems:
                self.git("reset", "-q", "--hard", before, check=False)
                raise HistoryError("reverting would leave an invalid file, nothing changed: " + "; ".join(problems[:3]))
        return before, files

    def rollback(self, before: str) -> None:
        """Restore the folder to commit `before` (everything tracked; hand edits were snapshotted first)."""
        self.git("reset", "-q", "--hard", before, check=False)

    def commit_revert(self, ref: str, files: list[str]) -> str | None:
        """Commit the staged revert (plus regenerated companions): `git add -A` is safe here because the caller holds
        the lock and hand edits were snapshotted, and it also records DELETED paths (a reverted file creation)."""
        subject = self.git("log", "-1", "--format=%s", self.resolve(ref)).stdout.strip()
        self.git("add", "-A")
        return self._commit(f"coach: revert {ref} {', '.join(files)}\n\nSource: cli\nReason: undo '{subject}'")

    # ------------------------------------------------------------ purge
    def purge_plan(self, cutoff: str) -> tuple[list[str], list[str]]:
        """(all commits oldest first, the commits that survive a purge before `cutoff`). A cutoff after the newest
        commit keeps only the current state (one snapshot)."""
        revs = self.git("rev-list", "--reverse", "HEAD").stdout.split() if self.exists() else []
        dates = {r: self.git("log", "-1", "--format=%aI", r).stdout.strip() for r in revs}
        keep = [r for r in revs if dates[r][:10] >= cutoff]
        if not keep and revs:
            keep = [revs[-1]]
        return revs, keep

    def purge_before(self, cutoff: str) -> tuple[int, int]:
        """Rewrite the private history so that no commit older than `cutoff` (ISO date) is kept: the first kept commit
        becomes a new root ("history before DATE purged") with its tree intact, later commits are re-created on top
        with their messages and dates; old objects are then deleted (reflog expired, gc --prune=now). Returns
        (commits_dropped, commits_kept). The working files are not touched. Refused when the repository has other
        branches / tags (they would keep the old objects alive)."""
        revs, keep = self.purge_plan(cutoff)
        if not revs or len(keep) == len(revs):
            return 0, len(revs)
        refs = [r for r in self.git("for-each-ref", "--format=%(refname)").stdout.split() if r]
        head = self.git("symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
        if not head or set(refs) - {head}:
            raise HistoryError("the history repository has other branches or tags (or a detached HEAD): refusing to "
                               "rewrite it; remove them by hand with git, or delete memory/.history.git to start over")
        dates = {r: self.git("log", "-1", "--format=%aI", r).stdout.strip() for r in keep}
        parent = None
        for i, r in enumerate(keep):
            tree = self.git("rev-parse", f"{r}^{{tree}}").stdout.strip()
            msg = (f"coach: history before {cutoff} purged (snapshot of the state at that point)\n\nSource: purge"
                   if i == 0 else self.git("log", "-1", "--format=%B", r).stdout.rstrip("\n"))
            args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
            parent = self.git(*args, env={"GIT_AUTHOR_DATE": dates[r], "GIT_COMMITTER_DATE": dates[r]}).stdout.strip()
        self.git("update-ref", head, parent)
        self.git("reflog", "expire", "--expire=now", "--all", check=False)
        self.git("gc", "-q", "--prune=now", check=False)
        return len(revs) - len(keep), len(keep)
