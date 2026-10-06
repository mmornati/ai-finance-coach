"""The memory store (E3): the one place that finds, loads, validates and writes every file of ``memory/``.

Every write goes through :meth:`MemoryStore.edit` / :meth:`MemoryStore.write_text`:
validate (schema + semantics) -> refuse if a comment would be lost -> take the lock -> snapshot hand edits ->
atomic write -> record in the change history. The coach/LLM never calls these directly: it can only create a
*proposal* (:mod:`coach.memory.proposals`) that the user accepts.
"""
from __future__ import annotations

import difflib
import re
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError

from coach.memory import edit as edit_mod, schemas, yamlio
from coach.memory.history import Change, MemoryRepo, git_available

# relative path -> (model, key of the list of id-ed items or None)
FIXED_YAML = {
    "categorization.yaml": (schemas.CategorizationFile, "annotations"),
    "assets.yaml": (schemas.AssetsFile, "assets"),
    "household.yaml": (schemas.HouseholdFile, "members"),
    "events.yaml": (schemas.EventsFile, "events"),
    "open-questions.yaml": (schemas.QuestionsFile, "questions"),
    "documents.yaml": (schemas.DocumentsFile, "documents"),
    "budgets.yaml": (schemas.BudgetsFile, "budgets"),
    "goals.yaml": (schemas.GoalsFile, "goals"),
}
DIR_YAML = {"liabilities": schemas.Liability, "contracts": schemas.Contract}
MARKDOWN = ("profile.md", "preferences.md", "events.md", "open-questions.md", "README.md")
GENERATED_MD = {"open-questions.md"}       # a view regenerated from open-questions.yaml
EVENT_HEADING_RE = re.compile(r"^##\s+([a-z0-9][a-z0-9_-]*)\s*$", re.M)


class MemoryStoreError(RuntimeError):
    pass


class ValidationFailed(MemoryStoreError):
    def __init__(self, issues: list["Issue"]):
        self.issues = issues
        super().__init__("; ".join(f"{i.file}: {i.path + ': ' if i.path else ''}{i.message}" for i in issues[:5])
                         + (f" (+{len(issues) - 5} more)" if len(issues) > 5 else ""))


@dataclass
class Issue:
    level: str                  # error | warning | info
    code: str
    file: str
    message: str
    path: str = ""
    line: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"level": self.level, "code": self.code, "file": self.file, "path": self.path, "message": self.message}
        if self.line:
            d["line"] = self.line
        return d


@dataclass
class EditResult:
    file: str
    old_text: str
    new_text: str
    diff: str
    issues: list[Issue] = field(default_factory=list)
    change: Optional[Change] = None
    change_id: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.old_text != self.new_text


def loc_to_path(loc: tuple) -> str:
    out = ""
    for p in loc:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
    return out


def _path_with_ids(loc: tuple, data) -> str:
    """annotations[3] -> annotations[3] (id foo): the position alone is hard to find in a 100-item file."""
    base = loc_to_path(loc)
    if len(loc) >= 2 and isinstance(loc[1], int) and isinstance(data, dict):
        try:
            item = data[loc[0]][loc[1]]
            if isinstance(item, dict) and item.get("id"):
                head = f"{loc[0]}[{loc[1]}]"
                return base.replace(head, f"{loc[0]}[{item['id']}]", 1)
        except (KeyError, IndexError, TypeError):
            pass
    return base


def extra_fields(model, prefix: str = "") -> list[str]:
    """Names of keys the schema does not declare (kept, but reported as info; new ones are refused by `set`)."""
    out: list[str] = []
    if isinstance(model, BaseModel):
        for k in (model.model_extra or {}):
            out.append(f"{prefix}{k}")
        for name in type(model).model_fields:
            out += extra_fields(getattr(model, name, None), f"{prefix}{name}.")
    elif isinstance(model, list):
        for i, it in enumerate(model):
            ident = getattr(it, "id", i)
            out += extra_fields(it, f"{prefix[:-1]}[{ident}].")
    return out


class MemoryStore:
    def __init__(self, memory_dir, *, history: bool = True, source: str = "cli"):
        self.root = Path(memory_dir)
        self.use_history = history
        self.source = source
        self.repo = MemoryRepo(self.root)
        self._tl = threading.local()
        try:
            self.repo.repair_worktree()
        except Exception:                                            # noqa: BLE001 - never block opening a store
            pass

    @contextmanager
    def locked(self):
        """The writers' lock, re-entrant for the same thread: a whole read-modify-write is one critical section, so
        two concurrent edits (two `coach` processes, the UI) are serialised instead of one being lost."""
        depth = getattr(self._tl, "depth", 0)
        if depth:
            self._tl.depth = depth + 1
            try:
                yield
            finally:
                self._tl.depth -= 1
            return
        with self.repo.lock():
            self._tl.depth = 1
            try:
                yield
            finally:
                self._tl.depth = 0

    # ---------------------------------------------------------------- files
    def path(self, rel: str) -> Path:
        p = (self.root / rel)
        try:
            resolved = p.resolve()
            resolved.relative_to(self.root.resolve())
        except (ValueError, OSError):
            raise MemoryStoreError(f"{rel!r} is outside the memory folder") from None
        if ".." in Path(rel).parts or rel.startswith("/"):
            raise MemoryStoreError(f"{rel!r} is outside the memory folder")
        return p

    def exists(self, rel: str) -> bool:
        try:
            return self.path(rel).exists()
        except MemoryStoreError:              # a symlink leading out of the folder: not a memory file (see `escaping`)
            return False

    def _dir_usable(self, d: str) -> bool:
        """A memory sub-folder that exists and does not lead out of the memory folder (symlinked directories are fine
        inside, never outside)."""
        dp = self.root / d
        try:
            return dp.is_dir() and (not dp.is_symlink() or dp.resolve().is_relative_to(self.root.resolve()))
        except OSError:
            return False

    def escaping_symlinks(self) -> list[str]:
        """Memory files that are symlinks pointing OUTSIDE the folder (never read or written; `memory check` reports them)."""
        out = []
        candidates = list(FIXED_YAML) + list(MARKDOWN)
        for d in (*DIR_YAML, "documents"):
            dp = self.root / d
            if dp.is_symlink() and not self._dir_usable(d):
                out.append(d + "/")
        for d in DIR_YAML:
            dp = self.root / d
            if self._dir_usable(d):
                candidates += [f"{d}/{p.name}" for p in sorted(dp.glob("*.yaml"))]
        for rel in candidates:
            p = self.root / rel
            if p.is_symlink():
                try:
                    p.resolve().relative_to(self.root.resolve())
                except (ValueError, OSError):
                    out.append(rel)
        return out

    @staticmethod
    def _decode(raw: bytes) -> tuple[str, bool, bool]:
        """-> (text with LF newlines and no BOM, had_bom, had_crlf): the file's style is re-applied when it is written."""
        text = raw.decode("utf-8")
        bom = text.startswith("\ufeff")
        if bom:
            text = text[1:]
        crlf = "\r\n" in text
        return text.replace("\r\n", "\n"), bom, crlf

    def read_text(self, rel: str) -> str:
        p = self.path(rel)
        return self._decode(p.read_bytes())[0] if p.exists() else ""

    def _style(self, rel: str) -> tuple[bool, str]:
        """(had a BOM, the raw old text with its own line endings)."""
        p = self.path(rel)
        if not p.exists():
            return False, ""
        raw = p.read_bytes().decode("utf-8")
        bom = raw.startswith("\ufeff")
        return bom, raw[1:] if bom else raw

    @staticmethod
    def restyle(old_raw: str, new: str) -> str:
        """Give `new` (LF) the line endings of the file it replaces: lines that are unchanged keep their own ending
        (a file with mixed endings stays mixed), changed or added lines get the dominant ending."""
        if "\r" not in old_raw:
            return new
        old_lines = old_raw.splitlines(keepends=True)
        n_crlf = sum(1 for ln in old_lines if ln.endswith("\r\n"))
        dominant = "\r\n" if n_crlf * 2 >= len(old_lines) else "\n"
        old_plain = [ln.rstrip("\r\n") for ln in old_lines]
        new_lines = new.split("\n")
        trailing = new.endswith("\n")
        if trailing:
            new_lines = new_lines[:-1]
        out = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_plain, new_lines, autojunk=False).get_opcodes():
            if tag == "equal":
                out += old_lines[i1:i2]
            else:
                out += [ln + dominant for ln in new_lines[j1:j2]]
        text = "".join(out)
        if not trailing and text.endswith(("\r\n", "\n")):
            text = text.rstrip("\r\n")
        return text

    @staticmethod
    def kind_of(rel: str) -> Optional[str]:
        """'yaml' | 'markdown' | None (not a memory file this store manages)."""
        parts = Path(rel).parts
        if rel in FIXED_YAML:
            return "yaml"
        if len(parts) == 2 and parts[0] in DIR_YAML and parts[1].endswith(".yaml") and not parts[1].startswith("_"):
            return "yaml"
        if rel in MARKDOWN:
            return "markdown"
        return None

    @staticmethod
    def schema_for(rel: str):
        if rel in FIXED_YAML:
            return FIXED_YAML[rel]
        parts = Path(rel).parts
        if len(parts) == 2 and parts[0] in DIR_YAML:
            return DIR_YAML[parts[0]], None
        raise MemoryStoreError(f"{rel!r} is not a known memory YAML file "
                               f"(known: {', '.join(FIXED_YAML)}, liabilities/*.yaml, contracts/*.yaml)")

    def files(self) -> list[str]:
        """Every managed memory file that exists, in a stable order (templates, '_*' files, are skipped)."""
        out = [r for r in list(FIXED_YAML) + list(MARKDOWN) if self.exists(r)]
        for d in DIR_YAML:
            dp = self.root / d
            if self._dir_usable(d):
                out += [f"{d}/{p.name}" for p in sorted(dp.glob("*.yaml")) if not p.name.startswith("_")]
        return out

    # ---------------------------------------------------------------- loading
    def load_doc(self, rel: str):
        return yamlio.loads(self.read_text(rel))

    def load_plain(self, rel: str) -> dict:
        if not self.exists(rel):
            return {}
        data = yamlio.to_plain(self.load_doc(rel))
        return data if isinstance(data, dict) else {}

    def validate_text(self, rel: str, text: str) -> tuple[Optional[BaseModel], list[Issue]]:
        model_cls, _ = self.schema_for(rel)
        try:
            doc = yamlio.loads(text)
        except yamlio.YamlError as e:
            return None, [Issue("error", "yaml_syntax", rel, str(e))]
        data = yamlio.to_plain(doc)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, [Issue("error", "schema", rel, "the file must be a YAML mapping")]
        try:
            return model_cls.model_validate(data), []
        except ValidationError as e:
            issues = []
            for err in e.errors(include_url=False):
                msg = err["msg"].removeprefix("Value error, ")
                if err["type"] == "extra_forbidden":
                    msg = "unknown field (typo?)"
                loc = tuple(x for x in err["loc"] if x not in ("function-after[_has_effect(), Annotation]",))
                line = self._line_of(doc, loc)
                issues.append(Issue("error", "schema", rel, msg, _path_with_ids(loc, data), line))
            return None, issues

    @staticmethod
    def _line_of(doc, loc: tuple) -> Optional[int]:
        cur = doc
        line = None
        try:
            for p in loc:
                if isinstance(cur, dict):
                    if p in cur:
                        lc = cur.lc.data.get(p)
                        line = lc[0] + 1 if lc else line
                        cur = cur[p]
                    else:
                        break
                elif isinstance(cur, list) and isinstance(p, int):
                    lc = cur.lc.data.get(p) if hasattr(cur, "lc") else None
                    line = lc[0] + 1 if lc else line
                    cur = cur[p]
                else:
                    break
        except Exception:                                           # noqa: BLE001 - line numbers are best effort
            return line
        return line

    def model(self, rel: str) -> Optional[BaseModel]:
        """The validated model of a file, or None if absent. Raises ValidationFailed if invalid."""
        if not self.exists(rel):
            return None
        m, issues = self.validate_text(rel, self.read_text(rel))
        if issues:
            raise ValidationFailed(issues)
        return m

    def validate(self, rel: str) -> list[Issue]:
        if self.kind_of(rel) != "yaml":
            return []
        return self.validate_text(rel, self.read_text(rel))[1]

    def validate_all(self) -> list[Issue]:
        out: list[Issue] = []
        for rel in self.files():
            out += self.validate(rel)
        return out

    # typed accessors (lenient: an invalid file yields nothing here, `memory check` reports it)
    def _items(self, rel: str, attr: str) -> list:
        try:
            m = self.model(rel)
        except ValidationFailed:
            return []
        return list(getattr(m, attr)) if m is not None else []

    def annotations(self) -> list[schemas.Annotation]:
        return self._items("categorization.yaml", "annotations")

    def members(self) -> list[schemas.Member]:
        return self._items("household.yaml", "members")

    def assets(self) -> list[schemas.Asset]:
        return self._items("assets.yaml", "assets")

    def questions(self) -> list[schemas.Question]:
        return self._items("open-questions.yaml", "questions")

    def documents(self) -> list[schemas.Document]:
        return self._items("documents.yaml", "documents")

    def budgets(self) -> list[schemas.Budget]:
        return self._items("budgets.yaml", "budgets")

    def goals(self) -> list[schemas.Goal]:
        return self._items("goals.yaml", "goals")

    def _checked(self, rel: str, key: str, cls) -> tuple[list, list[str]]:
        """Entry-by-entry reading of a list file: (valid items, problems 'id: why'). One invalid entry must not hide the
        others (a budget typo must not make every budget vanish); `memory check` reports the file as a whole."""
        from pydantic import ValidationError as VE
        if not self.exists(rel):
            return [], []
        try:
            data = self.load_plain(rel)
        except Exception as e:                                          # noqa: BLE001
            return [], [f"{rel}: not readable ({str(e)[:80]})"]
        valid, problems, seen = [], [], set()
        for i, it in enumerate(data.get(key) or []):
            ident = str(it.get("id")) if isinstance(it, dict) and it.get("id") else f"entry #{i + 1}"
            try:
                m = cls.model_validate(it)
            except VE as e:
                err = e.errors(include_url=False)[0]
                problems.append(f"{ident}: {err['msg'].removeprefix('Value error, ')}")
                continue
            if m.id in seen:
                problems.append(f"{ident}: duplicate id (ignored)")
                continue
            seen.add(m.id)
            valid.append(m)
        if rel == "budgets.yaml":
            from coach.classify.rules import CATEGORIES, TAXONOMY
            keep = []
            for b in valid:
                if b.category and b.category not in CATEGORIES:
                    problems.append(f"{b.id}: unknown category {b.category!r}")
                elif b.group and b.group not in TAXONOMY:
                    problems.append(f"{b.id}: unknown category group {b.group!r}")
                else:
                    keep.append(b)
            valid = keep
        return valid, problems

    def budgets_checked(self) -> tuple[list, list[str]]:
        return self._checked("budgets.yaml", "budgets", schemas.Budget)

    def goals_checked(self) -> tuple[list, list[str]]:
        return self._checked("goals.yaml", "goals", schemas.Goal)

    def liabilities(self) -> list[tuple[str, schemas.Liability]]:
        return self._dir_models("liabilities")

    def contracts(self) -> list[tuple[str, schemas.Contract]]:
        return self._dir_models("contracts")

    def _dir_models(self, d: str) -> list[tuple[str, BaseModel]]:
        out = []
        for rel in self.files():
            if rel.startswith(d + "/"):
                try:
                    out.append((rel, self.model(rel)))
                except ValidationFailed:
                    continue
        return out

    def event_ids(self) -> set[str]:
        """Ids of events: `## slug` headings of events.md plus the ids of the optional events.yaml."""
        ids = set(EVENT_HEADING_RE.findall(self.read_text("events.md")))
        ids |= {e.id for e in self._items("events.yaml", "events")}
        return ids

    # ---------------------------------------------------------------- references (`show`, `set`)
    def index(self) -> dict[str, list[tuple[str, str, str]]]:
        """id -> [(file, list_key, kind label)]: every addressable item."""
        idx: dict[str, list[tuple[str, str, str]]] = {}

        def add(i, rel, key, label):
            idx.setdefault(str(i), []).append((rel, key, label))

        for rel, (_, key) in FIXED_YAML.items():
            if not self.exists(rel):
                continue
            for it in (self.load_plain(rel).get(key) or []):
                if isinstance(it, dict) and it.get("id"):
                    add(it["id"], rel, key, {"annotations": "annotation", "assets": "asset", "members": "member",
                                              "events": "event", "questions": "question",
                                              "documents": "document", "budgets": "budget",
                                              "goals": "goal"}[key])
        for rel in self.files():
            if rel.split("/")[0] in DIR_YAML:
                d = self.load_plain(rel)
                if d.get("id"):
                    add(d["id"], rel, "", rel.split("/")[0][:-1])
        return idx

    def resolve_ref(self, ref: str) -> tuple[str, Optional[str]]:
        """A file path or an item id -> (file, item id or None)."""
        if self.kind_of(ref) is not None or (self.exists(ref) and self.kind_of(ref)):
            return ref, None
        hits = self.index().get(ref, [])
        if len(hits) == 1:
            return hits[0][0], ref
        if len(hits) > 1:
            raise MemoryStoreError(f"{ref!r} is ambiguous: " + ", ".join(f"{h[2]} in {h[0]}" for h in hits)
                                   + " (use the file name)")
        known = ", ".join(self.files())
        raise MemoryStoreError(f"no memory file or item {ref!r} (files: {known})")

    def list_key_of(self, rel: str) -> Optional[str]:
        return FIXED_YAML[rel][1] if rel in FIXED_YAML else None

    # ---------------------------------------------------------------- semantic validation
    def semantic_issues(self, rel: str, model, strict: bool = True) -> list[Issue]:
        """Cross-file rules enforced when WRITING (and listed by `memory check`): categories exist, events exist."""
        out: list[Issue] = []
        if rel == "budgets.yaml" and model is not None:
            from coach.classify.rules import CATEGORIES, TAXONOMY
            for b in model.budgets:
                if b.category and b.category not in CATEGORIES:
                    out.append(Issue("error", "unknown_category", rel, f"unknown category {b.category!r} "
                                     f"(see `coach taxonomy list`)", f"budgets[{b.id}].category"))
                if b.group and b.group not in TAXONOMY:
                    out.append(Issue("error", "unknown_group", rel, f"unknown category group {b.group!r} "
                                     f"(groups: {', '.join(sorted(TAXONOMY))})", f"budgets[{b.id}].group"))
                members = {m.id for m in self.members()}
                if b.owner and members and b.owner != "joint" and b.owner not in members:
                    out.append(Issue("error" if strict else "warning", "unknown_owner", rel,
                                     f"owner {b.owner!r} is neither 'joint' nor a household member ({', '.join(sorted(members))})",
                                     f"budgets[{b.id}].owner"))
            return out
        if rel == "household.yaml" and model is not None:
            return self._household_issues(model, strict)
        if rel != "categorization.yaml" or model is None:
            return out
        from coach.classify.rules import CATEGORIES
        events = self.event_ids()
        for a in model.annotations:
            if a.category and a.category not in CATEGORIES:
                out.append(Issue("error", "unknown_category", rel, f"unknown category {a.category!r} "
                                 f"(see `coach taxonomy list`)", f"annotations[{a.id}].category"))
            if a.match.category_in:
                for c in a.match.category_in:
                    if c not in CATEGORIES:
                        out.append(Issue("error", "unknown_category", rel, f"unknown category {c!r} in category_in",
                                         f"annotations[{a.id}].match.category_in"))
            if a.event and a.event not in events:
                out.append(Issue("error" if strict else "warning", "unknown_event", rel,
                                 f"event {a.event!r} is not defined in events.md (a `## {a.event}` heading) "
                                 f"or events.yaml", f"annotations[{a.id}].event"))
        return out

    def _household_issues(self, model, strict: bool) -> list[Issue]:
        """E14: the rules of household.yaml name members that exist, categories that exist, shares that add up."""
        from coach.classify.rules import CATEGORIES, TAXONOMY
        rel = "household.yaml"
        out: list[Issue] = []
        ids = {m.id for m in model.members}
        level = "error" if strict else "warning"

        def known(who, where, allow_joint=False):
            if who in ids or (allow_joint and who == "joint"):
                return
            out.append(Issue(level, "unknown_member", rel, f"{who!r} is not a household member ({', '.join(sorted(ids)) or 'none declared'})", where))
        for m in model.members:
            if m.pocket_money is not None and m.role != "child":
                out.append(Issue("warning", "pocket_money_adult", rel, f"pocket_money is set on {m.id!r}, who is not a child", f"members[{m.id}].pocket_money"))
        for r in model.attribution:
            known(r.member, f"attribution[{r.id}].member", allow_joint=True)
        for b in model.kid_budgets:
            known(b.member, f"kid_budgets[{b.id}].member")
            if b.category and b.category not in CATEGORIES:
                out.append(Issue("error", "unknown_category", rel, f"unknown category {b.category!r} (see `coach taxonomy list`)", f"kid_budgets[{b.id}].category"))
            if b.group and b.group not in TAXONOMY:
                out.append(Issue("error", "unknown_group", rel, f"unknown category group {b.group!r}", f"kid_budgets[{b.id}].group"))
        for a in model.allocations:
            for who in a.among:
                known(who, f"allocations[{a.id}].among")
            for who in a.shares:
                known(who, f"allocations[{a.id}].shares")
            if a.match.category and a.match.category not in CATEGORIES:
                out.append(Issue("error", "unknown_category", rel, f"unknown category {a.match.category!r}", f"allocations[{a.id}].match.category"))
            if a.match.group and a.match.group not in TAXONOMY:
                out.append(Issue("error", "unknown_group", rel, f"unknown category group {a.match.group!r}", f"allocations[{a.id}].match.group"))
        return out

    # ---------------------------------------------------------------- writing
    def _history_on(self) -> bool:
        if self.use_history and not git_available():
            raise MemoryStoreError("git is not installed, so the change history cannot be kept; install git or "
                                   "set [memory] history = false in config.toml to write without history")
        return self.use_history

    def write_text(self, rel: str, new_text: str, *, action: str, reason: Optional[str] = None,
                   source: Optional[str] = None, dry_run: bool = False, allow_comment_loss: bool = False,
                   allow_new_fields: bool = True, strict_semantic: bool = True,
                   detail: Optional[str] = None, expect_old: Optional[str] = None,
                   extra_files: Optional[dict[str, str]] = None,
                   companion: Optional[Callable[[BaseModel], dict[str, str]]] = None,
                   dropped_comments: Optional[list[str]] = None) -> EditResult:
        """Validate and write ONE file (plus optional generated companions in `extra_files`) as a single recorded
        change. `expect_old`: the text the edit was computed from; if the file changed meanwhile the write is
        refused (lost update)."""
        kind = self.kind_of(rel)
        if kind is None:
            raise MemoryStoreError(f"{rel!r} is not a memory file the store manages")
        if rel in GENERATED_MD:
            raise MemoryStoreError(f"{rel} is generated from open-questions.yaml; use `coach questions ...`")
        old_text = self.read_text(rel)
        if expect_old is not None and old_text != expect_old:
            raise MemoryStoreError(f"{rel} changed while this edit was being prepared; try again")
        issues: list[Issue] = []
        if kind == "yaml":
            model, issues = self.validate_text(rel, new_text)
            if issues:
                raise ValidationFailed(issues)
            sem = self.semantic_issues(rel, model, strict_semantic)
            if any(i.level == "error" for i in sem):
                raise ValidationFailed([i for i in sem if i.level == "error"])
            issues += sem
            if not allow_new_fields and old_text.strip():
                old_model, _ = self.validate_text(rel, old_text)
                added = set(extra_fields(model)) - set(extra_fields(old_model) if old_model else [])
                if added:
                    raise MemoryStoreError(f"{rel}: unknown field(s) {', '.join(sorted(added))}: not part of the schema "
                                           "(pass --new-field to add a custom field on purpose)")
        if old_text.strip() and not allow_comment_loss and kind == "yaml":
            lost = yamlio.lost_comments(old_text, new_text)
            for c in dropped_comments or []:           # inline comments of values this very edit replaced
                if c in lost:
                    lost.remove(c)
            if lost:
                raise MemoryStoreError(f"{rel}: this edit would drop {len(lost)} comment(s) (e.g. {lost[0][:70]!r}); "
                                       "refusing. Edit the file by hand, or pass --drop-comments if you accept that")
        diff = "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True),
                                            f"a/{rel}", f"b/{rel}"))
        res = EditResult(rel, old_text, new_text, diff, issues)
        if companion is not None and kind == "yaml":
            extra_files = {**(extra_files or {}), **companion(model)}
            extra_files = {k: v for k, v in extra_files.items() if v != self.read_text(k)}
        if dry_run or (new_text == old_text and not extra_files):
            return res
        history = self._history_on()
        with self.locked():
            if expect_old is not None and self.read_text(rel) != expect_old:
                raise MemoryStoreError(f"{rel} changed while this edit was being prepared; try again")
            if history:
                self.repo.ensure()
                self.repo.snapshot_external()
            bom, old_raw = self._style(rel)
            yamlio.atomic_write(self.path(rel), ("\ufeff" if bom else "") + self.restyle(old_raw, new_text))
            files = [rel]
            for er, et in (extra_files or {}).items():
                yamlio.atomic_write(self.path(er), et)
                files.append(er)
            if history:
                cid = self.repo.record(action, files, reason, source or self.source, detail)
                res.change_id = cid
        return res

    def edit(self, rel: str, ops: list[dict], *, action: str = "edit", reason: Optional[str] = None,
             source: Optional[str] = None, dry_run: bool = False, allow_comment_loss: bool = False,
             allow_new_fields: bool = False, strict_semantic: bool = True, detail: Optional[str] = None,
             companion: Optional[Callable[[BaseModel], dict[str, str]]] = None,
             replace_inline_comments: bool = False) -> EditResult:
        """Apply structured operations (see :mod:`coach.memory.edit`) to a file, validated and recorded."""
        with (nullcontext() if dry_run else self.locked()):
            return self._edit(rel, ops, action=action, reason=reason, source=source, dry_run=dry_run,
                              allow_comment_loss=allow_comment_loss, allow_new_fields=allow_new_fields,
                              strict_semantic=strict_semantic, detail=detail, companion=companion,
                              replace_inline_comments=replace_inline_comments)

    def _edit(self, rel, ops, *, action, reason, source, dry_run, allow_comment_loss, allow_new_fields,
              strict_semantic, detail, companion, replace_inline_comments=False) -> EditResult:
        kind = self.kind_of(rel)
        if kind is None:
            raise MemoryStoreError(f"{rel!r} is not a memory file the store manages")
        old_text = self.read_text(rel)
        if kind == "markdown":
            text = old_text
            try:
                for op in ops:
                    text = edit_mod.apply_text_op(text, op)
            except edit_mod.EditError as e:
                raise MemoryStoreError(f"{rel}: {e}") from e
            return self.write_text(rel, text, action=action, reason=reason, source=source, dry_run=dry_run,
                                   detail=detail, expect_old=old_text)
        list_key = self.list_key_of(rel)
        creating = bool(ops) and ops[0].get("op") == "create"
        if creating:
            if self.exists(rel):
                raise MemoryStoreError(f"{rel} already exists")
            doc = edit_mod.to_rt(ops[0]["value"])
            rest = ops[1:]
        else:
            if not self.exists(rel):
                raise MemoryStoreError(f"{rel} does not exist")
            try:
                doc = yamlio.loads(old_text)
            except yamlio.YamlError as e:
                raise ValidationFailed([Issue("error", "yaml_syntax", rel, str(e))]) from e
            rest = ops
        appended_ids: list[str] = []
        dropped: Optional[list[str]] = [] if replace_inline_comments else None
        try:
            for op in rest:
                edit_mod.apply_op(doc, op, list_key, dropped)
                if op.get("op") == "append" and isinstance(op.get("value"), dict) and op["value"].get("id"):
                    appended_ids.append(str(op["value"]["id"]))
        except edit_mod.EditError as e:
            raise MemoryStoreError(f"{rel}: {e}") from e
        new_text = yamlio.dumps(doc)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        for i in appended_ids:
            new_text = edit_mod.insert_blank_before_item(new_text, i)
        return self.write_text(rel, new_text, action=action, reason=reason, source=source, dry_run=dry_run,
                               allow_comment_loss=allow_comment_loss, allow_new_fields=allow_new_fields or creating,
                               strict_semantic=strict_semantic, detail=detail,
                               expect_old=None if creating else old_text, companion=companion,
                               dropped_comments=dropped)

    def set_value(self, ref: str, path: str, value, **kw) -> EditResult:
        """`coach memory set`: `ref` is a file or an item id; `path` is relative to the file (or to the item)."""
        rel, item = self.resolve_ref(ref)
        if self.kind_of(rel) != "yaml":
            raise MemoryStoreError(f"{rel} is Markdown: edit it by hand or propose an appended note")
        lk = self.list_key_of(rel)
        full = f"{lk}[{item}].{path}" if item and lk else path
        return self.edit(rel, [{"op": "set", "path": full, "value": value}], action="set", **kw)

    # ---------------------------------------------------------------- history passthrough
    def history(self, file: Optional[str] = None, limit: int = 50) -> list[Change]:
        return self.repo.log(file, limit)

    def diff(self, ref: Optional[str] = None, file: Optional[str] = None) -> str:
        if ref or not self.repo.exists():
            return self.repo.diff(ref, file)
        with self.locked():                      # the unrecorded-edits diff briefly touches the index
            return self.repo.diff(ref, file)

    def revert(self, change_id: str) -> Change:
        self._history_on()
        with self.locked():
            touched_refs = {"categorization.yaml", "events.md", "events.yaml"}

            def check(files):
                bad = []
                for f in files:
                    if self.kind_of(f) == "yaml" and self.exists(f):
                        iss = self.validate(f)
                        if iss:
                            bad.append(f"{f}: {iss[0].message}")
                # cross-file rules: reverting the creation of an event (or a category change) must not leave an
                # annotation pointing at something that no longer exists
                if not bad and touched_refs & set(files) and self.exists("categorization.yaml"):
                    try:
                        for i in self.semantic_issues("categorization.yaml", self.model("categorization.yaml"), True):
                            bad.append(f"categorization.yaml: {i.message}")
                    except ValidationFailed as e:
                        bad.append(str(e))
                return bad

            before, files = self.repo.revert(change_id, validate=check)
            try:
                if "open-questions.yaml" in files and self.exists("open-questions.yaml"):
                    from coach.memory import questions as q
                    yamlio.atomic_write(self.path("open-questions.md"), q.render_markdown(self.questions()))
                cid = self.repo.commit_revert(change_id, files)
            except BaseException:
                self.repo.rollback(before)          # never leave a half-applied revert (staged / deleted paths)
                raise
        return Change(cid or "", "", f"coach: revert {change_id}", files)
