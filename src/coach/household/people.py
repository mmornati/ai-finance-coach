"""The people of the household, as the rest of the code needs them (E14-1, E14-2).

``household.yaml`` is the source of truth (``memory member add``, the web app's household page). :class:`People` is a read-only view:
which member an owner value of an account (an id, a name, an alias, ``joint``) stands for, who is a child, and the rules the other E14
modules read (attribution, kid budgets, allocations). Real names stay here and on the local pages: a model sees only the pseudonyms
(``adult-1``, ``kid-1``) built by :class:`coach.memory.context.Scrubber`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

JOINT = "joint"


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn").strip()


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^\w']+", fold(s)) if t]


@dataclass
class People:
    members: list = field(default_factory=list)            # [schemas.Member]
    attribution: list = field(default_factory=list)        # [schemas.AttributionRule]
    kid_budgets: list = field(default_factory=list)        # [schemas.KidBudget]
    allocations: list = field(default_factory=list)        # [schemas.Allocation]

    def __post_init__(self) -> None:
        self.by_id = {m.id: m for m in self.members}
        self._lookup: dict[str, str] = {}
        self._first: dict[str, str] = {}                    # a first name that only one member has -> that member
        first: dict[str, set] = {}
        for m in self.members:
            for key in {m.id, m.name, *m.aliases}:
                if fold(key):
                    self._lookup.setdefault(fold(key), m.id)
            toks = _tokens(m.name)
            if toks:
                first.setdefault(toks[0], set()).add(m.id)
        for tok, ids in first.items():                      # "Mia" for "Mia Rossi", when it is unambiguous
            if len(ids) == 1 and len(tok) >= 3:
                self._lookup.setdefault(tok, next(iter(ids)))
                self._first[tok] = next(iter(ids))

    # ---- lookups
    def __bool__(self) -> bool:
        return bool(self.members)

    def ids(self) -> list[str]:
        return [m.id for m in self.members]

    def adults(self) -> list[str]:
        return [m.id for m in self.members if m.role == "adult"]

    def children(self) -> list[str]:
        return [m.id for m in self.members if m.role == "child"]

    def role(self, member_id: str) -> Optional[str]:
        m = self.by_id.get(member_id)
        return m.role if m else None

    def name(self, member_id: Optional[str]) -> str:
        if member_id in (None, ""):
            return "unassigned"
        if member_id == JOINT:
            return "Joint"
        m = self.by_id.get(member_id)
        return m.name if m else member_id

    def first_name(self, member_id: Optional[str]) -> str:
        return self.name(member_id).split(" ")[0]

    def resolve(self, value: Optional[str]) -> Optional[str]:
        """An account-owner value (member id, name, alias, 'joint') -> a member id, 'joint', or None when it is unknown."""
        if value is None or not str(value).strip():
            return None
        v = str(value).strip()
        if v.lower() == JOINT:
            return JOINT
        return self._lookup.get(fold(v))

    def text_mentions(self, text: str, member_id: str) -> bool:
        """The member's full name (every word) or one of their aliases appears in `text`: evidence for a transfer between banks."""
        m = self.by_id.get(member_id)
        if not m or not text:
            return False
        hay = f" {' '.join(_tokens(text))} "
        for phrase in (m.name, *m.aliases):
            toks = _tokens(phrase)
            if len(toks) == 1 and len(toks[0]) < 4:
                continue
            if toks and all(f" {t} " in hay for t in toks):
                return True
        return False


    def mentioned(self, text: str) -> set:
        """Members a bank text names: the full name or an alias (every word), or a first name that only one member has (banks often print
        just "TO ANNA" or "FROM LUCA R"). A heuristic: a shared or common first name is not used, and an unknown name is no member."""
        out = {m.id for m in self.members if self.text_mentions(text, m.id)}
        toks = set(_tokens(text))
        return out | {mid for tok, mid in self._first.items() if tok in toks}


def load(store) -> People:
    """Tolerant read of household.yaml: a missing or invalid file is an empty household (``coach memory check`` explains)."""
    try:
        if not store.exists("household.yaml"):
            return People()
        model = store.model("household.yaml")
        if model is None:
            return People()
        return People(list(model.members), list(model.attribution), list(model.kid_budgets), list(model.allocations))
    except Exception:                                                  # noqa: BLE001
        return People()


def account_owner_member(people: People, owner: Optional[str]) -> Optional[str]:
    """The member id an account's owner value stands for (None for a joint or unknown owner: the household as a whole)."""
    r = people.resolve(owner)
    return r if r in people.by_id else None


def normalise_owner(people: People, owner: str) -> str:
    """The value stored in ``accounts.owner``: 'joint', or the member id when the text names a member (so a renamed alias never
    splits one person in two); an unknown text is kept as typed (the memory check reports it)."""
    r = people.resolve(owner)
    return r if r else owner.strip()


# ---------------------------------------------------------------- edits of the lists of household.yaml (one place for the CLI and the web app)

def upsert_ops(store, key: str, item_id: str, value: dict) -> list:
    """Memory-store operations that create or replace the item `item_id` of the list `key` (attribution, kid_budgets, allocations)."""
    if not store.exists("household.yaml"):
        return [{"op": "create", "value": {"members": [], key: [value]}}]
    cur = store.load_plain("household.yaml").get(key)
    if not isinstance(cur, list):
        return [{"op": "set", "path": key, "value": [value]}]
    if any(isinstance(i, dict) and str(i.get("id")) == item_id for i in cur):
        return [{"op": "set", "path": f"{key}[{item_id}]", "value": value}]
    return [{"op": "append", "path": key, "value": value}]


def delete_ops(store, key: str, item_id: str) -> Optional[list]:
    """The operation removing an item, or None when it does not exist."""
    raw = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
    cur = raw.get(key)
    if not isinstance(cur, list) or not any(isinstance(i, dict) and str(i.get("id")) == item_id for i in cur):
        return None
    return [{"op": "remove", "path": f"{key}[{item_id}]"}]
