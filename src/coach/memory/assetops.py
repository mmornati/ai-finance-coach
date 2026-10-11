"""Rename or delete an asset of assets.yaml, with the items that point to it.

An asset id is referenced by the ``asset`` field of a loan (``liabilities/*.yaml``) and of a goal (``goals.yaml``). A rename
rewrites those references in the SAME recorded change (one history entry, one revert); a delete is refused while anything
still points to the asset, so no goal or loan is left dangling. A rental property is never renamed: its id is also the
``property-<id>`` tag of the transactions paid from another account (E15), which live in the database, not in memory.
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Optional

from coach.memory import schemas
from coach.memory.store import EditResult, MemoryStore, MemoryStoreError

ASSETS = "assets.yaml"
GOALS = "goals.yaml"


def _same(ref: Optional[str], asset_id: str) -> bool:
    """A loan's ``asset`` may be typed by hand (``Family-House``): matched like the rental model does."""
    return (ref or "").strip().lower() == asset_id.lower()


def find(store: MemoryStore, asset_id: str) -> schemas.Asset:
    for a in store.assets():
        if a.id == asset_id:
            return a
    raise MemoryStoreError(f"no asset {asset_id!r} in {ASSETS}")


def references(store: MemoryStore, asset_id: str) -> list[dict]:
    """The loans and goals whose ``asset`` is this id: [{type, id, file}]."""
    out = [{"type": "liability", "id": lb.id, "file": rel} for rel, lb in store.liabilities() if _same(lb.asset, asset_id)]
    if store.exists(GOALS):
        out += [{"type": "goal", "id": g.id, "file": GOALS} for g in store.goals() if _same(g.asset, asset_id)]
    return out


def rename(store: MemoryStore, old_id: str, new_id: str, *, source: Optional[str] = None, reason: Optional[str] = None,
           dry_run: bool = False) -> tuple[EditResult, list[dict]]:
    """Rename an asset and every loan / goal that points to it, as one recorded change. Returns (result, references)."""
    try:
        schemas._slug(new_id, "asset id")
    except ValueError as e:
        raise MemoryStoreError(str(e)) from None
    if new_id == old_id:
        raise MemoryStoreError("the new id is the same as the current one")
    a = find(store, old_id)
    if a.kind == "real_estate_rental":
        raise MemoryStoreError(f"{old_id} is a rental property: its id is also the property-{old_id} tag of its transactions, "
                               "so it cannot be renamed here")
    if store.index().get(new_id):
        raise MemoryStoreError(f"{new_id!r} is already used by another memory item: choose another id")
    refs = references(store, old_id)
    kw = dict(action="rename-asset", reason=reason, source=source, detail=f"{old_id} -> {new_id}")
    ops_by_file: dict[str, list[dict]] = {}
    for r in refs:
        path = "asset" if r["type"] == "liability" else f"goals[{r['id']}].asset"
        ops_by_file.setdefault(r["file"], []).append({"op": "set", "path": path, "value": new_id})
    with (nullcontext() if dry_run else store.locked()):     # a preview takes no lock
        main = store.edit(ASSETS, [{"op": "set", "path": f"assets[{old_id}].id", "value": new_id}], dry_run=True, **kw)
        extras = {rel: store.edit(rel, ops, dry_run=True, **kw) for rel, ops in ops_by_file.items()}
        diff = main.diff + "".join(e.diff for e in extras.values())
        issues = main.issues + [i for e in extras.values() for i in e.issues]
        if dry_run:
            return EditResult(ASSETS, main.old_text, main.new_text, diff, issues), refs
        for rel, e in extras.items():                  # the lock is held: nothing changed since the preview, but be sure
            if store.read_text(rel) != e.old_text:
                raise MemoryStoreError(f"{rel} changed while this rename was being prepared; try again")
        res = store.write_text(ASSETS, main.new_text, expect_old=main.old_text,
                               extra_files={rel: e.new_text for rel, e in extras.items()}, **kw)
        return EditResult(ASSETS, main.old_text, main.new_text, diff, issues, change_id=res.change_id), refs


def delete(store: MemoryStore, asset_id: str, *, source: Optional[str] = None, reason: Optional[str] = None,
           dry_run: bool = False) -> EditResult:
    """Remove an asset from assets.yaml; refused while a loan or a goal points to it (change or remove that link first)."""
    find(store, asset_id)
    refs = references(store, asset_id)
    if refs:
        names = ", ".join(f"{r['type']} {r['id']}" for r in refs)
        raise MemoryStoreError(f"{asset_id} is still linked to {names}: change or remove that link first")
    # the comments written inside the removed item go with it (the `remove` op keeps the neighbours' own comments)
    return store.edit(ASSETS, [{"op": "remove", "path": f"assets[{asset_id}]"}], action="delete-asset", reason=reason,
                      source=source, detail=asset_id, dry_run=dry_run, allow_comment_loss=True)
