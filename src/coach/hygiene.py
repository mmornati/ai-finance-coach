"""Repository hygiene (E13-4): is there anything personal in a file that would be published?

Two layers.

**Structural rules, shipped in the tree** (they contain nothing about anybody): a PEM private key block, an Anthropic API key, a person's home
folder (``/Users/<name>/``, ``/home/<name>/``) that is not an obvious placeholder, an IBAN with a valid checksum (the examples and tests use invalid
ones, plus the published example IBANs), an e-mail address outside example / reserved domains.

**Real-data terms, kept OUTSIDE the publishable tree.** Anything that identifies the author's household (names, employers, towns, account labels, IBAN
fragments, counterparties, distinctive merchants, exact recurring amounts, loan and asset figures) lives in a LOCAL file, by default
``<coach home>/hygiene-terms.txt`` (override: ``COACH_HYGIENE_TERMS``), mode 0600, git-ignored. ``coach dev hygiene --build-terms`` derives it from the local
database and memory and never prints a term. No hash, salt or list of real terms is part of this package: a hash of a name is reversible with a
dictionary, so the only safe place for the list is the owner's machine.

A hit never prints the matched text: only ``file:line`` and the category and number of the term (``name#7``).
Matching ignores case, accents, punctuation and spacing, and finds amounts written ``1234.56``, ``1 234,56`` or ``1234,56``.
"""
from __future__ import annotations

import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

TERMS_ENV = "COACH_HYGIENE_TERMS"
TERMS_NAME = "hygiene-terms.txt"
CATEGORIES = ("name", "employer", "place", "school", "account", "id", "contact", "counterparty", "merchant", "amount", "machine")
TERMS_HEADER = ("# LOCAL ONLY: terms that identify your household, one `category<TAB>term` per line, used by `coach dev hygiene`.\n"
                "# Never commit, share or paste this file. Rebuild it with `coach dev hygiene --build-terms`.\n")

LOCK_NAMES = frozenset({"pnpm-lock.yaml", "package-lock.json", "yarn.lock", "uv.lock"})     # resolved dependency lists: a `name@version` is not an e-mail
BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pyc", ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz",
                             ".enc", ".db", ".sqlite", ".pdf", ".lock", ".icns"})
# never part of a published tree, whatever .gitignore says (tool caches and dependency folders)
ALWAYS_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache",
                              "graphify-out", ".idea", ".vscode"})
# Git never publishes a path named ".git", at any depth, folder or file: in a worktree or a submodule ".git" is a FILE ("gitdir: <absolute path>").
GIT_NEVER_PUBLISHES = frozenset({".git"})

PLACEHOLDER_USERS = frozenset({"you", "user", "username", "name", "me", "example", "runner", "yourname", "your-name", "someone", "alice", "bob",
                               "coach", "app", "shared", "home", "x", "<you>", "<user>", "appuser", "jdoe", "maria"})
EMAIL_OK_DOMAINS = ("example.com", "example.org", "example.net", "example.fr", "example.it", "exemple.fr", "test", "invalid", "localhost",
                    "b.co", "y.org", "anthropic.com", "users.noreply.github.com", "noreply.github.com", "lemmy.world")
EXAMPLE_IBANS = frozenset({"GB82WEST12345698765432", "DE89370400440532013000", "FR7630006000011234567890189", "FR7630001007941234567890185",
                           "IT60X0542811101000000123456", "NL91ABNA0417164300", "ES9121000418450200051332", "BE68539007547034"})
IBAN_LENGTHS = {"FR": 27, "IT": 27, "DE": 22, "GB": 22, "ES": 24, "BE": 16, "NL": 18, "PT": 25, "CH": 21, "LU": 20, "IE": 22, "AT": 20}

_PEM = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
_ANTHROPIC_KEY = re.compile(r"\bsk-ant-(?:api|admin|sid|oat)\d{2}-[A-Za-z0-9_\-]{20,}")
_HOME = re.compile(r"(?<![\w.])(?:/Users|/home)/([A-Za-z0-9._\-<>]+)/")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_NUMBER = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[ \u00a0\u202f.,]\d{3})+|\d+)(?:[.,]\d{1,2})?(?!\d|[.,]\d)")


@dataclass(frozen=True)
class Hit:
    path: str
    rule: str          # private-key | api-key | home-folder | iban | email | real-data:<category>#<n>
    line: int = 0

    def __str__(self) -> str:
        return f"{self.path}" + (f":{self.line}" if self.line else "") + f"  [{self.rule}]"


# ---------------------------------------------------------------- .gitignore semantics

def _pattern_regex(pat: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out += "/.*"
            i += 3
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


class Gitignore:
    """The rules of a .gitignore file (one level: the project's own), with git's last-match-wins and parent-exclusion semantics."""

    def __init__(self, text: str):
        self.rules: list[tuple[bool, bool, bool, re.Pattern]] = []      # (negate, dir_only, anchored, regex)
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            neg = line.startswith("!")
            if neg:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = "/" in line
            line = line.lstrip("/")
            if not line:
                continue
            self.rules.append((neg, dir_only, anchored, _pattern_regex(line)))

    def _decide(self, parts: tuple[str, ...], is_dir: bool) -> bool:
        ignored = False
        rel = "/".join(parts)
        for neg, dir_only, anchored, rx in self.rules:
            if dir_only and not is_dir:
                continue
            if rx.match(rel if anchored else parts[-1]):
                ignored = not neg
        return ignored

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        parts = tuple(p for p in rel.split("/") if p)
        for i in range(1, len(parts) + 1):
            last = i == len(parts)
            state = self._decide(parts[:i], is_dir if last else True)
            if state and not last:
                return True
            if last:
                return state
        return False


def publishable_files(root: Path) -> list[Path]:
    """Every file under `root` that the project's .gitignore does not exclude, sorted."""
    root = Path(root)
    gi_path = root / ".gitignore"
    gi = Gitignore(gi_path.read_text() if gi_path.is_file() else "")
    out: list[Path] = []

    def walk(d: Path, rel: str) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for p in entries:
            name = p.name
            r = f"{rel}/{name}" if rel else name
            if p.is_symlink() or name in GIT_NEVER_PUBLISHES:
                continue
            if p.is_dir():
                if name in ALWAYS_SKIP_DIRS or gi.ignored(r, True):
                    continue
                walk(p, r)
            elif p.is_file() and not gi.ignored(r, False):
                out.append(p)

    walk(root, "")
    return out


# ---------------------------------------------------------------- folding and the local terms

def fold(s: str) -> str:
    """Lower-case, accents removed, every run of non-alphanumeric characters one space: 'Hôtel-de  Ville' -> 'hotel de ville'."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.casefold()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _squash(s: str) -> str:
    return fold(s).replace(" ", "")


@dataclass(frozen=True)
class Term:
    n: int
    category: str
    text: str


def _repo_roots(extra: Optional[Path] = None) -> list[Path]:
    from coach import home as home_mod
    roots = [Path(extra).resolve()] if extra else []
    if home_mod.in_checkout():
        roots.append(home_mod.CHECKOUT_ROOT.resolve())
    return roots


def inside_repository(path: Path, root: Optional[Path] = None) -> bool:
    """True when `path` is below the checkout (or the scanned root): an interactive agent working in the repository could read it there."""
    p = Path(path).expanduser().resolve()
    return any(p == r or r in p.parents for r in _repo_roots(root))


def terms_path(root: Optional[Path] = None, env=None) -> Path:
    """Where the local term file lives: $COACH_HYGIENE_TERMS, else the per-user coach home (~/.ai-finance-coach), NEVER inside the repository
    (`root` is accepted for compatibility and ignored). A path that resolves inside the repository is reported by :func:`terms_file_problem`."""
    env = os.environ if env is None else env
    if env.get(TERMS_ENV):
        return Path(env[TERMS_ENV]).expanduser()
    return Path.home() / ".ai-finance-coach" / TERMS_NAME


def load_terms(path: Path) -> list[Term]:
    """The terms of a local file (None-safe: a missing file is an empty list). Lines: `category<TAB>term`; `#` lines are comments."""
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#") or "\t" not in raw:
            continue
        cat, term = raw.split("\t", 1)
        if cat.strip() in CATEGORIES and term.strip():
            out.append(Term(len(out) + 1, cat.strip(), term.strip()))
    return out


def terms_file_problem(path: Path, root: Optional[Path] = None) -> Optional[str]:
    """Why the local term file cannot be trusted (inside the repository, missing, empty, readable by others), or None."""
    if inside_repository(path, root):
        return "inside the repository (an agent working there could read it): move it out with COACH_HYGIENE_TERMS or the default ~/.ai-finance-coach"
    try:
        st = Path(path).stat()
    except OSError:
        return "missing"
    if st.st_mode & 0o077:
        return f"readable by other users (mode {stat.S_IMODE(st.st_mode):04o}): chmod 600"
    if not load_terms(path):
        return "empty"
    return None


def _amount_values(token: str) -> set[Decimal]:
    """The value of a number as written: '1 234,56', '1234.56', '1.234,56', '1,234.56', '1234,56', '1,234' (thousands), '1234'. Thousands groups are
    exactly three digits and the decimals one or two, so '30, 100' or '3,450 %' are not read as amounts they are not."""
    t = token.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    m = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,](\d{1,2}))?", t)
    if not m:
        return set()
    try:
        return {Decimal(re.sub(r"[.,]", "", m.group(1)) + "." + (m.group(2) or "0")).quantize(Decimal("0.01"))}
    except InvalidOperation:
        return set()


def written_values(token: str) -> set[Decimal]:
    """Every amount a written number may stand for, including a whole number of CENTS ('123456' for 1234.56)."""
    vals = _amount_values(token)
    t = token.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if t.isdigit() and 4 <= len(t) <= 12:
        vals.add((Decimal(t) / 100).quantize(Decimal("0.01")))
    return vals


class TermIndex:
    """The terms of a local file, ready to be matched against many lines."""

    def __init__(self, terms: Iterable[Term]):
        self.by_first: dict[str, list[tuple[tuple[str, ...], Term]]] = {}
        self.squashed: list[tuple[str, Term]] = []
        self.amounts: dict[Decimal, Term] = {}
        self.count = 0
        for t in terms:
            self.count += 1
            if t.category == "amount":
                try:
                    self.amounts.setdefault(Decimal(t.text.replace(",", ".").replace(" ", "")).quantize(Decimal("0.01")), t)
                except InvalidOperation:
                    pass
                continue
            toks = tuple(fold(t.text).split())
            if not toks:
                continue
            self.by_first.setdefault(toks[0], []).append((toks, t))
            sq = "".join(toks)
            if t.category in ("id", "account") and len(sq) >= 8:
                self.squashed.append((sq, t))

    def find(self, line: str) -> list[Term]:
        found: dict[int, Term] = {}
        toks = fold(line).split()
        for i, tok in enumerate(toks):
            for phrase, term in self.by_first.get(tok, ()):
                if tuple(toks[i:i + len(phrase)]) == phrase:
                    found[term.n] = term
        if self.squashed:
            sq = "".join(toks)
            for s, term in self.squashed:
                if s in sq:
                    found[term.n] = term
        if self.amounts:
            for m in _NUMBER.finditer(line):
                for v in written_values(m.group(0)):
                    if v in self.amounts:
                        found[self.amounts[v].n] = self.amounts[v]
        return list(found.values())


# ---------------------------------------------------------------- the rules

def iban_valid(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if not 15 <= len(s) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    if s[:2] in IBAN_LENGTHS and len(s) != IBAN_LENGTHS[s[:2]]:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def scan_text(rel: str, text: str, index: Optional[TermIndex] = None) -> list[Hit]:
    hits: list[Hit] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _PEM.search(line):
            hits.append(Hit(rel, "private-key", lineno))
        if _ANTHROPIC_KEY.search(line):
            hits.append(Hit(rel, "api-key", lineno))
        for m in _HOME.finditer(line):
            if m.group(1).lower() not in PLACEHOLDER_USERS and not m.group(1).startswith("<"):
                hits.append(Hit(rel, "home-folder", lineno))
        for m in _EMAIL.finditer(line):
            dom = m.group(1).lower()
            if not any(dom == ok or dom.endswith("." + ok) for ok in EMAIL_OK_DOMAINS):
                hits.append(Hit(rel, "email", lineno))
        for m in _IBAN.finditer(line):
            cand = m.group(0).replace(" ", "")
            if cand not in EXAMPLE_IBANS and iban_valid(cand):
                hits.append(Hit(rel, "iban", lineno))
        if index is not None:
            for t in index.find(line):
                hits.append(Hit(rel, f"real-data:{t.category}#{t.n}", lineno))
    return hits


def _readable_text(p: Path) -> Optional[str]:
    if p.suffix.lower() in BINARY_SUFFIXES or p.name in LOCK_NAMES:
        return None
    try:
        return p.read_text()
    except (UnicodeDecodeError, OSError):
        return None


def scan(root: Path, files: list[Path] | None = None, index: Optional[TermIndex] = None) -> list[Hit]:
    """Hits over every publishable file of `root` (or over `files`). Without an `index` only the structural rules run."""
    root = Path(root)
    hits: list[Hit] = []
    for p in files if files is not None else publishable_files(root):
        text = _readable_text(p)
        if text is not None:
            hits += scan_text(p.relative_to(root).as_posix(), text, index)
    return hits


def scan_names(names_and_texts: Iterable[tuple[str, str]], index: Optional[TermIndex] = None) -> list[Hit]:
    """The same rules over (name, text) pairs: the member list and contents of a built sdist or wheel."""
    hits: list[Hit] = []
    for name, text in names_and_texts:
        hits += scan_text(name, text, index)
    return hits


# ---------------------------------------------------------------- building the local term file

def _numbers(obj, out: set[Decimal]) -> None:
    """Figures of the memory worth looking for: a round value of 10000 or more, or one with cents from 30 up (a rate, a year or a small round number is not identifying)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        d = abs(Decimal(str(obj)))
        if (d >= 10000) or (d >= 30 and d != d.to_integral()):
            out.add(d)
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)


def _add(terms: dict, cat: str, text, min_len: int = 3) -> None:
    t = (text or "").strip() if isinstance(text, str) else ""
    if len(fold(t)) >= min_len:
        terms.setdefault(cat, set()).add(t)


def build_terms(con, memory_dir: Path, *, eb_app_id: Optional[str] = None, extra_machine: Iterable[str] = (), extra_amounts: Iterable = ()) -> dict[str, set]:
    """Derive the terms from a database connection and a memory folder. Reads only; returns {category: {term}}."""
    import yaml
    terms: dict[str, set] = {}

    def yload(rel):
        try:
            return yaml.safe_load((Path(memory_dir) / rel).read_text()) or {}
        except (OSError, yaml.YAMLError):
            return {}

    hh = yload("household.yaml")
    for m in hh.get("members") or []:
        for key in ("name", "id"):
            _add(terms, "name", m.get(key), 4)
        for tok in str(m.get("name") or "").split():
            _add(terms, "name", tok, 4)
        for a in m.get("aliases") or []:
            _add(terms, "name", a)
            for tok in str(a).split():
                _add(terms, "name", tok, 4)
    for key, cat in (("employers", "employer"), ("places", "place"), ("schools", "school")):
        for v in hh.get(key) or []:
            _add(terms, cat, v)
    for v in (hh.get("contact") or {}).values():
        if isinstance(v, str):
            for part in re.split(r"[\n,]", v):
                _add(terms, "contact", part, 5)
    for rel in ("assets.yaml",):
        amounts: set[Decimal] = set()
        _numbers(yload(rel), amounts)
        for a in amounts:
            terms.setdefault("amount", set()).add(str(a))
    mem = Path(memory_dir)
    for sub in ("liabilities", "contracts"):
        for p in sorted((mem / sub).glob("*.yaml")) if (mem / sub).is_dir() else []:
            if p.name.startswith("_"):
                continue
            data = yload(f"{sub}/{p.name}")
            amounts = set()
            _numbers(data, amounts)
            for a in amounts:
                terms.setdefault("amount", set()).add(str(a))
            for key in ("lender", "provider", "debited_from"):
                _add(terms, "merchant", data.get(key) if isinstance(data, dict) else None, 4)
    cols = [r[1] for r in con.execute("PRAGMA table_info(accounts)")]
    for row in con.execute("SELECT * FROM accounts"):
        d = dict(zip(cols, row))
        for key in ("name", "label"):
            _add(terms, "account", d.get(key), 8)
        uid = str(d.get("uid") or "")
        if len(uid) >= 8:
            _add(terms, "id", uid[:8], 8)
        iban = (d.get("iban") or "").replace(" ", "")
        if len(iban) >= 14:
            _add(terms, "id", iban[-10:], 8)
            _add(terms, "id", iban[4:14], 8)
        for tok in str(d.get("owner") or "").split():
            if tok.lower() not in ("joint", "main", "shared"):
                _add(terms, "name", tok, 4)
    for (sid,) in con.execute("SELECT session_id FROM sessions"):
        if len(str(sid)) >= 8:
            _add(terms, "id", str(sid)[:8], 8)
    for (cp,) in con.execute("SELECT DISTINCT t.counterparty FROM transactions t JOIN tx_enriched e ON e.tx_key=t.tx_key "
                             "WHERE e.tx_type IN ('person_transfer_in','person_transfer_out','internal_transfer')"):
        _add(terms, "counterparty", cp, 6)
        for tok in str(cp or "").split():
            _add(terms, "counterparty", tok, 5)
    for (raw,) in con.execute("SELECT DISTINCT e.merchant_raw FROM tx_enriched e WHERE e.tx_type IN ('person_transfer_in','person_transfer_out')"):
        _add(terms, "counterparty", raw, 6)
    public = public_vocabulary()
    for (k,) in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key IS NOT NULL"):
        if k and len(k) >= 10 and not str(k).replace(" ", "").isdigit() and re.search(r"[A-Za-z]{4}", str(k)) \
                and len(fold(str(k)).split()) >= 2 \
                and not all(tok in public or tok.isdigit() for tok in fold(str(k)).split()):
            _add(terms, "merchant", k, 10)
    # exact recurring amounts: a figure WITH cents that comes back (a round amount such as 100.00 is not identifying)
    for a, n in con.execute("SELECT ROUND(ABS(amount), 2), COUNT(*) FROM transactions GROUP BY 1 HAVING COUNT(*) >= 3 AND ABS(amount) >= 30"):
        if round(a * 100) % 100:
            terms.setdefault("amount", set()).add(f"{a:.2f}")
    for a in extra_amounts:                                     # recurring series and price-history levels: amounts WITH cents only
        d = abs(Decimal(str(a)))
        cents = int(d * 100) % 100
        if d >= 5 and cents and not (d < 100 and cents in (90, 95, 99, 49, 50)):      # retail price points (9.99, 19.90...) are everywhere: noise, not identity
            terms.setdefault("amount", set()).add(f"{d:.2f}")
    for m in extra_machine:
        _add(terms, "machine", m, 4)
    if eb_app_id:
        _add(terms, "id", eb_app_id[:8], 8)
    return terms


def public_vocabulary() -> set[str]:
    """Words that are public by construction because the package itself ships them: the banking format words and the vocabulary of the shipped
    rules / taxonomy. A merchant key made only of those words (a chain, a bank label) is not identifying and is left out of the terms."""
    words: set[str] = set()
    try:
        from coach.quality.fixtures import STRUCTURAL
        words |= {fold(w) for w in STRUCTURAL}
    except Exception:                                                              # noqa: BLE001
        pass
    base = Path(__file__).resolve().parent / "classify"
    for name in ("rules.yaml", "taxonomy.yaml", "taxonomy_equivalence.yaml"):
        try:
            words |= set(fold((base / name).read_text()).split())
        except OSError:
            pass
    return {w for w in words if w}


def machine_terms() -> list[str]:
    """The login name and the host name of this machine (they end up in paths and screenshots)."""
    import getpass
    import platform
    out = []
    try:
        out.append(getpass.getuser())
    except Exception:                                                              # noqa: BLE001
        pass
    try:
        out.append(platform.node().split(".")[0])
    except Exception:                                                              # noqa: BLE001
        pass
    return [m for m in out if m and m.lower() not in PLACEHOLDER_USERS]


def write_terms(path: Path, terms: dict[str, set]) -> int:
    """Write the local term file, mode 0600 from the first byte. Returns the number of terms."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [TERMS_HEADER]
    n = 0
    for cat in CATEGORIES:
        for t in sorted(terms.get(cat, ()), key=str.casefold):
            lines.append(f"{cat}\t{t}\n")
            n += 1
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return n
