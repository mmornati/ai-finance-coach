"""Structured edits of a ruamel document: addressing by path, and the operations a proposal can carry.

Path syntax: dotted keys, list items selected by id or position: ``annotations[house-mortgage].category``,
``rate.nominal``, ``tags[0]``. When the file is a list of items with ids (categorization, assets, household,
events, questions, documents) the first segment may simply be an item id: ``livrets-a.balance``.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

SEG_RE = re.compile(r"^([A-Za-z0-9_-]*)((?:\[[^\[\]]+\])*)$")
OPS = {"set", "unset", "append", "remove", "create", "append_text", "replace_text"}


class EditError(ValueError):
    pass


@dataclass
class Seg:
    name: str
    sels: list


def parse_path(path: str) -> list[Seg]:
    if not path or not path.strip():
        raise EditError("empty path")
    segs = []
    for part in path.split("."):
        m = SEG_RE.match(part)
        if not m or (not m.group(1) and not m.group(2)):
            raise EditError(f"bad path segment {part!r} in {path!r} (use key, key[id] or key[0])")
        sels = re.findall(r"\[([^\[\]]+)\]", m.group(2))
        segs.append(Seg(m.group(1), [int(s) if re.fullmatch(r"-?\d+", s) else s for s in sels]))
    return segs


def to_rt(value):
    """Plain python -> ruamel nodes with the house style: short scalar lists in flow style, text blocks literal."""
    if isinstance(value, dict) and not isinstance(value, CommentedMap):
        m = CommentedMap()
        for k, v in value.items():
            m[k] = to_rt(v)
        return m
    if isinstance(value, (list, tuple)) and not isinstance(value, CommentedSeq):
        s = CommentedSeq(to_rt(v) for v in value)
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            s.fa.set_flow_style()
        return s
    if isinstance(value, str) and "\n" in value:
        return LiteralScalarString(value if value.endswith("\n") else value + "\n")
    return value


def _find_item(seq, sel, what: str):
    if isinstance(sel, int):
        try:
            seq[sel]
        except IndexError:
            raise EditError(f"{what}: no item at position {sel} (list has {len(seq)})") from None
        return sel
    for i, it in enumerate(seq):
        if isinstance(it, dict) and str(it.get("id")) == sel:
            return i
    ids = [str(it.get("id")) for it in seq if isinstance(it, dict) and it.get("id") is not None]
    raise EditError(f"{what}: no item with id {sel!r}" + (f" (ids: {', '.join(ids[:8])}{' ...' if len(ids) > 8 else ''})" if ids else ""))


def expand_shortcut(doc, path: str, list_key: str | None) -> str:
    """`item-id.field` -> `<list_key>[item-id].field` when the file is an id-list and the first segment is an id."""
    if not list_key or not isinstance(doc, dict):
        return path
    first = path.split(".", 1)[0]
    if "[" in first or first == list_key:
        return path
    seq = doc.get(list_key)
    if isinstance(seq, list) and any(isinstance(it, dict) and str(it.get("id")) == first for it in seq):
        return f"{list_key}[{first}]" + path[len(first):]
    return path


def resolve(doc, path: str, *, create: bool = False, list_key: str | None = None):
    """-> (container, key): the slot the path designates. With ``create``, missing (or null) intermediate mappings
    are created and the final key may be new."""
    path = expand_shortcut(doc, path, list_key)
    segs = parse_path(path)
    cur = doc
    here = ""
    for si, seg in enumerate(segs):
        last = si == len(segs) - 1
        here = (here + "." if here else "") + seg.name
        if seg.name:
            if not isinstance(cur, dict):
                raise EditError(f"{here}: not a mapping")
            if seg.name not in cur or cur[seg.name] is None:
                if not create or seg.sels:
                    raise EditError(f"{here}: no such " + ("list" if seg.sels else "field"))
                if last:
                    return cur, seg.name
                cur[seg.name] = CommentedMap()
            elif last and not seg.sels:
                return cur, seg.name
            cur = cur[seg.name]
        for ki, sel in enumerate(seg.sels):
            if not isinstance(cur, list):
                raise EditError(f"{here}: not a list")
            idx = _find_item(cur, sel, here)
            if last and ki == len(seg.sels) - 1:
                return cur, idx
            cur = cur[idx]
            here += f"[{sel}]"
    raise EditError(f"cannot resolve {path!r}")                    # pragma: no cover


def get_path(doc, path: str, list_key: str | None = None):
    c, k = resolve(doc, path, list_key=list_key)
    return c[k]


def _tail_slot(node):
    """(container, key) holding the comment that FOLLOWS `node` (ruamel attaches the comment lines between two list
    items to the last scalar of the first one)."""
    cur = node
    while True:
        if isinstance(cur, dict) and cur:
            k = list(cur.keys())[-1]
            if isinstance(cur[k], (dict, list)) and cur[k]:
                cur = cur[k]
                continue
            return cur, k
        if isinstance(cur, list) and cur:
            k = len(cur) - 1
            if isinstance(cur[k], (dict, list)) and cur[k]:
                cur = cur[k]
                continue
            return cur, k
        return None


def _move_tail_comment(seq, idx: int) -> None:
    """Before deleting item `idx`: the comment/header lines that precede item idx+1 are stored at the tail of item
    idx. Hand them to the item before (idx-1) so the header of the NEXT item survives the removal."""
    if idx == 0:
        return
    src, dst = _tail_slot(seq[idx]), _tail_slot(seq[idx - 1])
    if not src or not dst:
        return
    src_t, dst_t = src[0].ca.items.get(src[1]), dst[0].ca.items.get(dst[1])
    # a token is "<end-of-line comment of that scalar>\n<comment / blank lines before the next item>"
    split = lambda t: tuple((t.value if t is not None and getattr(t, "value", None) else "").partition("\n")[::2])   # noqa: E731
    src_head, src_rest = split(src_t[2] if src_t else None)
    dst_head, _ = split(dst_t[2] if dst_t else None)
    if idx == len(seq) - 1:
        src_rest = ""                                   # the removed LAST item's own header goes with it
    new_value = dst_head + ("\n" + src_rest if src_rest else ("\n" if dst_head else ""))
    if dst_t and dst_t[2] is not None:
        if new_value.strip():
            dst_t[2].value = new_value
        else:
            dst_t[2] = None
    elif src_rest.strip() and src_t and src_t[2] is not None:
        src_t[2].value = "\n" + src_rest
        dst[0].ca.items[dst[1]] = [None, None, src_t[2], None]


def drop_inline_comment(container, key) -> str | None:
    """Remove the end-of-line comment of `key` (keeping any comment / blank lines that follow it) and return its text."""
    try:
        slot = container.ca.items.get(key)
    except AttributeError:
        return None
    if not slot or len(slot) < 3 or slot[2] is None or not getattr(slot[2], "value", "").lstrip().startswith("#"):
        return None
    head, _, rest = slot[2].value.partition("\n")
    if rest.strip():
        slot[2].value = "\n" + rest
    else:
        slot[2] = None
    return head.strip()


def apply_op(doc, op: dict, list_key: str | None = None, dropped: list | None = None) -> None:
    kind = op.get("op")
    if kind not in OPS - {"create", "append_text", "replace_text"}:
        raise EditError(f"unknown operation {kind!r}")
    path = op.get("path")
    if kind == "set":
        c, k = resolve(doc, path, create=True, list_key=list_key)
        new = to_rt(op.get("value"))
        if dropped is not None and isinstance(c, dict) and k in c and c[k] != new and not isinstance(c[k], (dict, list)):
            # the comment on that line described the OLD value: it goes (and is shown in the diff), nothing else does
            gone = drop_inline_comment(c, k)
            if gone:
                dropped.append(gone)
        c[k] = new
    elif kind == "unset":
        c, k = resolve(doc, path, list_key=list_key)
        if isinstance(c, list):
            raise EditError(f"{path}: use `remove` to delete a list item")
        del c[k]
    elif kind == "append":
        c, k = resolve(doc, path, create=False, list_key=list_key)
        target = c[k]
        if not isinstance(target, list):
            raise EditError(f"{path}: not a list")
        if isinstance(op.get("value"), dict) and hasattr(target, "fa") and target.fa.flow_style():
            target.fa.set_block_style()          # a list created empty (`[]`) becomes a block list of mappings
        target.append(to_rt(op.get("value")))
    elif kind == "remove":
        c, k = resolve(doc, path, list_key=list_key)
        if not isinstance(c, list):
            raise EditError(f"{path}: only list items can be removed (use `unset` for a field)")
        _move_tail_comment(c, k)
        del c[k]


def apply_text_op(text: str, op: dict) -> str:
    kind = op.get("op")
    if kind == "append_text":
        add = op.get("value")
        if not isinstance(add, str) or not add.strip():
            raise EditError("append_text needs a non-empty text value")
        return text.rstrip("\n") + "\n\n" + add.strip("\n") + "\n" if text.strip() else add.strip("\n") + "\n"
    if kind == "replace_text":
        old, new = op.get("old"), op.get("value")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise EditError("replace_text needs `old` (non-empty) and `value`")
        n = text.count(old)
        if n != 1:
            raise EditError(f"replace_text: the old text occurs {n} times (it must occur exactly once)")
        return text.replace(old, new)
    raise EditError(f"unknown text operation {kind!r}")


def jsonable(v):
    """Make a plain value JSON-serialisable (dates -> ISO strings)."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def insert_blank_before_item(text: str, item_id: str) -> str:
    """Cosmetic: a freshly appended list item gets the blank line the hand-written items are separated with."""
    lines = text.split("\n")
    pat = re.compile(rf"^\s*- id:\s*['\"]?{re.escape(item_id)}['\"]?\s*(#.*)?$")
    for i, ln in enumerate(lines):
        if pat.match(ln) and i > 0 and lines[i - 1].strip() and not lines[i - 1].lstrip().startswith("#") \
                and not lines[i - 1].rstrip().endswith(":"):
            lines.insert(i, "")
            break
    return "\n".join(lines)
