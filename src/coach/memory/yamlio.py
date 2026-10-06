"""Round-trip YAML for the memory files (ruamel.yaml): comments, key order, quoting and the compact style of the
files written by hand are preserved, and an untouched file dumps back byte for byte (checked by the tests on the
shapes of the real files). Two details are customised so that this holds: ``None`` is written ``null`` (ruamel
writes nothing) and flow mappings keep the ``{ a: b }`` spacing the memory files use."""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.emitter import Emitter
from ruamel.yaml.events import MappingEndEvent
from ruamel.yaml.representer import RoundTripRepresenter

class _Representer(RoundTripRepresenter):
    """A dedicated representer (ruamel's own is left untouched for any other user in the process)."""


_Representer.add_representer(
    type(None), lambda self, data: self.represent_scalar("tag:yaml.org,2002:null", "null"))


class _Emitter(Emitter):
    def expect_first_flow_mapping_key(self):
        if not isinstance(self.event, MappingEndEvent):
            self.stream.write(" ")
            self.column += 1
        return super().expect_first_flow_mapping_key()

    def expect_flow_mapping_key(self):
        if isinstance(self.event, MappingEndEvent):
            old = self.flow_map_end
            self.flow_map_end = " " + old
            try:
                return super().expect_flow_mapping_key()
            finally:
                self.flow_map_end = old
        return super().expect_flow_mapping_key()


def make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096                      # never re-wrap a long line the user wrote
    y.indent(mapping=2, sequence=4, offset=2)
    y.Emitter = _Emitter
    y.Representer = _Representer
    return y


class YamlError(ValueError):
    """The text is not valid YAML (message carries the line)."""


def loads(text: str):
    try:
        return make_yaml().load(text) if text.strip() else CommentedMap()
    except Exception as e:                                           # noqa: BLE001 - ruamel has many error types
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise YamlError(f"invalid YAML{where}: {getattr(e, 'problem', None) or str(e).splitlines()[0]}") from e


def dumps(doc) -> str:
    buf = io.StringIO()
    make_yaml().dump(doc, buf)
    return buf.getvalue()


def to_plain(obj):
    """Recursively convert ruamel containers/scalars into plain dict/list/str/int/float/bool/None/date."""
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, _dt.datetime):
        return obj
    if isinstance(obj, _dt.date):
        return obj
    if isinstance(obj, str):
        return str(obj)
    return obj


_INT_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_FLOAT_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)\.\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_scalar(text: str):
    """A command-line value: only plain numbers (12, -1.5), true/false, ISO dates, null/~/empty and flow lists /
    mappings ([a, b], {k: v}) are typed; EVERYTHING else is the raw string, so `Loan # 2` or `a: b` or `2.10 %`
    are never truncated or reinterpreted. A value wrapped in matching quotes is the text inside the quotes."""
    t = text.strip()
    if t in ("", "null", "~"):
        return None
    if t == "true":
        return True
    if t == "false":
        return False
    if _INT_RE.match(t):
        return int(t)
    if _FLOAT_RE.match(t):
        return float(t)
    if _DATE_RE.match(t):
        try:
            return _dt.date.fromisoformat(t)
        except ValueError:
            return text
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        return t[1:-1].replace("''", "'") if t[0] == "'" else t[1:-1].replace('\\"', '"')
    if t[0] in "[{" and t[-1] in "]}":
        try:
            v = make_yaml().load(t)
            if isinstance(v, (CommentedMap, CommentedSeq)):
                return v
        except Exception:                                            # noqa: BLE001
            pass
    return text


# ---------------------------------------------------------------- comments

_BLOCK_START_RE = re.compile(r"(?:^|[\s:-])[|>][+-]?\d*\s*(?:#.*)?$")


def comment_lines(text: str) -> Counter:
    """Multiset of the comments (text after an unquoted ``#``) of a YAML text; used to refuse an edit that would
    silently drop one. Text inside block scalars (`>-`, `|`) is content, not comments."""
    found: Counter = Counter()
    block_indent = None                       # indentation of the line that opened the block scalar
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if block_indent is not None:
            if not stripped or indent > block_indent:
                continue
            block_indent = None
        quote = None
        hit = None
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"" and (i == 0 or line[i - 1] in " \t:-[{,"):
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                hit = i
                break
        if hit is not None:
            found[line[hit:].strip()] += 1
        body = line if hit is None else line[:hit]
        if _BLOCK_START_RE.search(body.rstrip()):
            block_indent = indent
    return found


def lost_comments(before: str, after: str) -> list[str]:
    diff = comment_lines(before) - comment_lines(after)
    return sorted(diff.elements())


# ---------------------------------------------------------------- files

def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write temp file + fsync + os.replace (a crash never leaves a half-written memory file). An existing file
    keeps its permissions, a new one is created private (0600)."""
    path = Path(os.path.realpath(path))        # a symlinked memory file keeps its link: the target is replaced
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)          # make the rename itself durable
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].strip("-") or "item"
