"""The ONE reader of categorization.yaml for the classifier: the store's validated, strictly typed annotations.

Contract: never raises. A missing file means no annotations; an unreadable (permissions), non-UTF-8, syntactically
broken or schema-invalid file means NO memory annotations plus a loud warning on stderr (once per file state), so a
bad hand edit can never take down sync / normalize / transfers / classify in the daily job (`coach memory check`
reports the details)."""
from __future__ import annotations

import sys
from pathlib import Path

_WARNED: set = set()


def warn_once(key, message: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        print(f"WARNING: {message}", file=sys.stderr)


def load_annotations_safe(memory_dir) -> list[dict]:
    from coach.memory import txmatch                      # lazy: txmatch imports classify.rules
    from coach.memory.store import MemoryStore
    rel = "categorization.yaml"
    try:
        p = Path(memory_dir) / rel
        if not p.exists():
            return []
        store = MemoryStore(memory_dir, history=False)
        model, issues = store.validate_text(rel, p.read_text(encoding="utf-8"))
        if issues:
            first = issues[0]
            warn_once((str(p), first.message, first.path),
                      f"{p} is invalid ({len(issues)} problem(s); first: {first.path or 'file'}"
                      f"{':' + str(first.line) if first.line else ''}: {first.message}). Memory annotations are "
                      f"IGNORED until it is fixed: run `coach memory check`")
            return []
        return [txmatch.ann_plain(a) for a in model.annotations]
    except Exception as e:                                # noqa: BLE001 - this function must never raise
        warn_once((str(memory_dir), type(e).__name__),
                  f"cannot read {memory_dir}/categorization.yaml ({type(e).__name__}: {str(e)[:120]}). Memory "
                  f"annotations are IGNORED")
        return []
