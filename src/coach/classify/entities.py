"""Canonical merchants (E2-12): several merchant keys (a chain's per-city descriptors, spelling variants) belong to
one entity. `merchant_entities` (name, default category) + `merchant_aliases` (merchant_key -> entity).

* ``auto_group``: keys whose merchant NAME normalises identically ("MacBurger's" / "Mac Burger's") are grouped
  automatically; looser matches (similar but not identical names) are only PROPOSED.
* An entity's category is a default: it applies to an alias key that has no label of its own and never overrides a
  user label or any existing merchant row (see classify.rules.resolve).
* Reports can group by entity (`categorised()` yields `entity`).
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from coach.classify.knn import Index
from coach.classify.parsers.common import strip_accents
from coach.classify.rules import CATEGORIES
from coach.db import connect, now_iso

GENERIC_NAMES = {"", "unknown", "unknownmerchant", "unknownshop", "unknownstore", "unknownbusiness", "unknownvendor",
                 "inconnu", "inconnue", "na", "nan", "none", "null", "nil", "tbd", "other", "others", "autre",
                 "autres", "misc", "miscellaneous", "divers", "various", "transfer", "virement", "payment",
                 "paiement", "shop", "store", "magasin", "merchant", "vendor", "unnamed", "notavailable",
                 "notprovided", "unidentified", "generic", "business", "company"}


def generic_name(norm: str) -> bool:
    """LLM placeholders ('Unknown merchant', 'N/A'...) must never glue unrelated keys into one entity."""
    return norm in GENERIC_NAMES or len(norm) < 3 or norm.startswith(("unknown", "inconnu", "unidentified"))


class EntityError(Exception):
    pass


def norm_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", strip_accents((name or "").lower()))


def _entity_for_norm(con, norm: str):
    return con.execute("SELECT id FROM merchant_entities WHERE norm_name=? ORDER BY id LIMIT 1", (norm,)).fetchone()


def _majority_category(con, keys: list[str]) -> str | None:
    rows = con.execute(f"SELECT category, source FROM merchants WHERE merchant_key IN ({','.join('?' * len(keys))})",
                       keys).fetchall() if keys else []
    users = [c for c, s in rows if s == "user"]
    pool = users or [c for c, _ in rows if c and c != "other.uncategorized"]
    return Counter(pool).most_common(1)[0][0] if pool else None


def auto_group(con, apply: bool = True) -> dict:
    """Group not-yet-aliased keys by identical normalised merchant name. Returns {created, joined, proposals}."""
    rows = con.execute("""SELECT merchant_key, merchant_name FROM merchants
                          WHERE merchant_name IS NOT NULL AND merchant_key NOT IN
                                (SELECT merchant_key FROM merchant_aliases)""").fetchall()
    groups: dict[str, list[tuple[str, str]]] = {}
    for k, n in rows:
        nn = norm_name(n)
        if generic_name(nn):
            continue
        groups.setdefault(nn, []).append((k, n))
    created = joined = 0
    for nn, members in sorted(groups.items()):
        ent = _entity_for_norm(con, nn)
        if not ent and len(members) < 2:
            continue
        if apply:
            if ent:
                eid = ent[0]
                joined += len(members)
            else:
                disp = Counter(n for _, n in members).most_common(1)[0][0]
                eid = con.execute("INSERT INTO merchant_entities(name, norm_name, category, source, created_at) "
                                  "VALUES (?,?,?,'auto',?)",
                                  (disp, nn, _majority_category(con, [k for k, _ in members]), now_iso())).lastrowid
                created += 1
                joined += len(members)
            for k, _ in members:
                con.execute("INSERT OR IGNORE INTO merchant_aliases VALUES (?,?,'auto',?)", (k, eid, now_iso()))
    if apply:
        con.commit()
    return {"created": created, "joined": joined, "proposals": proposals(con)}


def proposals(con, threshold: float = 0.8, limit: int = 30) -> list[dict]:
    """Different names that look alike (never applied automatically): entities, and un-aliased merchant names."""
    names: dict[str, tuple[str, str]] = {}       # norm -> (display, kind)
    for _id, n, nn in con.execute("SELECT id, name, norm_name FROM merchant_entities"):
        names.setdefault(nn, (n, "entity"))
    for k, n in con.execute("SELECT merchant_key, merchant_name FROM merchants WHERE merchant_name IS NOT NULL "
                            "AND merchant_key NOT IN (SELECT merchant_key FROM merchant_aliases)"):
        nn = norm_name(n)
        if not generic_name(nn) and len(nn) >= 4:
            names.setdefault(nn, (n, "merchant"))
    labelled = [(nn, d, kind) for nn, (d, kind) in sorted(names.items())]
    if len(labelled) < 2:
        return []
    idx = Index([(nn, d, kind) for nn, d, kind in labelled])
    out, seen = [], set()
    for nn, d, kind in labelled:
        for sim, other, od, okind in idx.neighbours(nn, k=3):
            pair = tuple(sorted((nn, other)))
            if sim >= threshold and pair not in seen:
                seen.add(pair)
                out.append({"a": d, "b": od, "similarity": sim})
    return sorted(out, key=lambda x: -x["similarity"])[:limit]


def _resolve_entity(con, ref: str):
    row = None
    if ref.isdigit():
        row = con.execute("SELECT id FROM merchant_entities WHERE id=?", (int(ref),)).fetchone()
    if not row:
        row = con.execute("SELECT id FROM merchant_entities WHERE norm_name=?", (norm_name(ref),)).fetchone()
    return row[0] if row else None


def _gc(con) -> None:
    con.execute("DELETE FROM merchant_entities WHERE id NOT IN (SELECT entity_id FROM merchant_aliases)")


def merge(con, refs: list[str], name: str | None = None, warn=print) -> int:
    """Put merchant keys (or whole entities, given by id / name) into ONE entity. Returns its id."""
    keys: list[str] = []
    entity_ids: list[int] = []
    for r in refs:
        eid = _resolve_entity(con, r) if not con.execute("SELECT 1 FROM tx_enriched WHERE merchant_key=? LIMIT 1",
                                                         (r,)).fetchone() else None
        if eid:
            entity_ids.append(eid)
        elif con.execute("SELECT 1 FROM tx_enriched WHERE merchant_key=? LIMIT 1", (r,)).fetchone() or \
                con.execute("SELECT 1 FROM merchants WHERE merchant_key=?", (r,)).fetchone():
            keys.append(r)
        else:
            raise EntityError(f"{r!r} is neither a merchant key nor an entity (see `coach merchants list`)")
    if len(keys) + len(entity_ids) < 2 and not name:
        raise EntityError("give at least two merchant keys / entities to merge")
    target = entity_ids[0] if entity_ids else None
    if target is None:
        disp = name or (con.execute("SELECT merchant_name FROM merchants WHERE merchant_key=?", (keys[0],)).fetchone()
                        or [keys[0].title()])[0] or keys[0].title()
        target = con.execute("INSERT INTO merchant_entities(name, norm_name, category, source, created_at) "
                             "VALUES (?,?,?,'user',?)", (disp, norm_name(disp), _majority_category(con, keys),
                                                         now_iso())).lastrowid
    # a category the user set survives a merge: the target's wins; otherwise the first user-set one of the others
    tgt = con.execute("SELECT category, category_user FROM merchant_entities WHERE id=?", (target,)).fetchone() \
        if target else None
    for eid in entity_ids[1:]:
        oth = con.execute("SELECT name, category, category_user FROM merchant_entities WHERE id=?", (eid,)).fetchone()
        if oth and oth[2] and oth[1]:                       # the other entity's category was set by the user
            if tgt and tgt[1]:                                # the target has one too: its own wins
                if tgt[0] != oth[1]:
                    warn(f"warning: {oth[0]!r} had the category {oth[1]} set by you; kept {tgt[0]} from the merge "
                         "target (change it with `coach merchants category`)")
            else:
                con.execute("UPDATE merchant_entities SET category=?, category_user=1 WHERE id=?", (oth[1], target))
                tgt = (oth[1], 1)
        con.execute("UPDATE merchant_aliases SET entity_id=?, source='user' WHERE entity_id=?", (target, eid))
    # keys leaving another entity bring that entity's user-set category along (the target's own wins on a clash)
    if keys:
        srcs = con.execute(f"""SELECT e.id, e.name, e.category FROM merchant_aliases a
                               JOIN merchant_entities e ON e.id=a.entity_id
                               WHERE a.merchant_key IN ({','.join('?' * len(keys))}) AND e.id<>? AND e.category_user=1
                                 AND e.category IS NOT NULL GROUP BY e.id""", (*keys, target)).fetchall()
        cur = con.execute("SELECT category, category_user FROM merchant_entities WHERE id=?", (target,)).fetchone()
        for _id, nm, cat in srcs:
            if cur[1]:
                if cur[0] != cat:
                    warn(f"warning: {nm!r} had the category {cat} set by you; kept {cur[0]} from the merge target "
                         "(change it with `coach merchants category`)")
            else:
                con.execute("UPDATE merchant_entities SET category=?, category_user=1 WHERE id=?", (cat, target))
                cur = (cat, 1)
    for k in keys:
        con.execute("INSERT OR REPLACE INTO merchant_aliases VALUES (?,?,'user',?)", (k, target, now_iso()))
    if name:
        con.execute("UPDATE merchant_entities SET name=?, norm_name=?, source='user' WHERE id=?",
                    (name, norm_name(name), target))
    _gc(con)
    con.commit()
    return target


def split(con, key: str) -> None:
    if not con.execute("DELETE FROM merchant_aliases WHERE merchant_key=?", (key,)).rowcount:
        raise EntityError(f"{key!r} is not part of an entity")
    _gc(con)
    con.commit()


def rename(con, ref: str, new_name: str) -> None:
    eid = _resolve_entity(con, ref)
    if not eid:
        raise EntityError(f"no entity {ref!r}")
    con.execute("UPDATE merchant_entities SET name=?, norm_name=?, source='user' WHERE id=?",
                (new_name, norm_name(new_name), eid))
    con.commit()


def set_category(con, ref: str, category: str | None) -> None:
    eid = _resolve_entity(con, ref)
    if not eid:
        raise EntityError(f"no entity {ref!r}")
    if category and category not in CATEGORIES:
        raise EntityError(f"unknown category {category}")
    # a category set here is the USER's: it beats LLM / kNN labels of the entity's keys (never user labels or rules)
    con.execute("UPDATE merchant_entities SET category=?, category_user=?, source='user' WHERE id=?",
                (category, 1 if category else 0, eid))
    con.commit()


def list_entities(con) -> list[dict]:
    rows = con.execute("""SELECT e.id, e.name, e.category, e.source, GROUP_CONCAT(a.merchant_key, '; ')
                          FROM merchant_entities e JOIN merchant_aliases a ON a.entity_id=e.id
                          GROUP BY e.id ORDER BY e.name""").fetchall()
    out = []
    for eid, name, cat, src, keys in rows:
        n, total = con.execute("""SELECT COUNT(*), ROUND(SUM(t.amount),2) FROM merchant_aliases a
                                  JOIN tx_enriched en ON en.merchant_key=a.merchant_key
                                  JOIN transactions t ON t.tx_key=en.tx_key WHERE a.entity_id=?""",
                               (eid,)).fetchone()
        out.append(dict(id=eid, name=name, category=cat, source=src, keys=(keys or "").split("; "), n=n,
                        total=total or 0))
    return out


def cmd_merchants(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    sub = a.merchants_cmd
    try:
        if sub == "list":
            ents = list_entities(con)
            for e in sorted(ents, key=lambda x: -abs(x["total"])):
                print(f"#{e['id']:<4} {e['name']:<30} {(e['category'] or '-'):<30} n={e['n']:<4} {e['total']:>10} EUR "
                      f"[{e['source']}]  {len(e['keys'])} key(s)")
                if a.verbose:
                    for k in e["keys"]:
                        print(f"        {k}")
            if not ents:
                print("no canonical merchants yet: `coach merchants group`")
            if a.proposals:
                print("\nsimilar names (not grouped, review by hand):")
                for p in proposals(con):
                    print(f"  {p['similarity']:.2f}  {p['a']}  ~  {p['b']}")
        elif sub == "group":
            r = auto_group(con, apply=not a.dry_run)
            print(f"{'would create' if a.dry_run else 'created'} {r['created']} entit(ies), joined {r['joined']} key(s)")
        elif sub == "merge":
            eid = merge(con, a.refs, a.name)
            print(f"merged into entity #{eid}")
        elif sub == "split":
            split(con, a.key)
            print(f"{a.key} is a merchant of its own again")
        elif sub == "rename":
            rename(con, a.entity, a.name)
            print("renamed")
        elif sub == "category":
            set_category(con, a.entity, None if a.category == "none" else a.category)
            print("category updated")
    except EntityError as e:
        sys.exit(f"error: {e}")
