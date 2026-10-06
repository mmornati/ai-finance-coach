"""Per-person logins of the web app (E14-8).

Logins are created and changed ONLY in a terminal (``coach users ...``): the web app cannot add a login, raise a role or enable a
disabled one. A login has a role:

* ``adult``: all the household's data, like the single owner login that existed before (a session without a user is that owner login);
* ``child``: the own data of one household member (a member declared with ``role: child``), through ``/api/v1/me/...`` only. The server
  denies every other endpoint to a child (deny by default, see :mod:`coach.api.app`).

The session cookie names the login; the role is read from this table on EVERY request, so disabling a login or changing its role takes
effect at once and a stale cookie can never keep a privilege. Preferences are per login (whitelisted keys). The audit log records who
changed what through the web app (method, endpoint, status; never a payload); the memory history carries ``Source: ui:<login>`` for every
memory write made from the web app.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from coach.db import now_iso
from coach.household.attribution import HouseholdError
from coach.household.people import People

ROLES = ("adult", "child")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
LEGACY_ID = "owner"
# ids a login may never take: the owner session, and the sources / actors that already mean something in the audit and the memory history
RESERVED = frozenset({LEGACY_ID, "admin", "root", "system", "cli", "ui", "coach", "coach-llm", "external", "joint", "me", "anonymous"})
# preference key -> allowed values (None = a member id or '')
PREFERENCES = {"locale": ("fr-FR", "en-GB"), "theme": ("system", "light", "dark"), "default_member": None,
               "landing": ("dashboard", "transactions", "kids")}


@dataclass(frozen=True)
class User:
    id: str
    role: str
    member_id: Optional[str] = None
    created_at: Optional[str] = None
    disabled_at: Optional[str] = None
    prefs: dict = field(default_factory=dict)

    @property
    def is_child(self) -> bool:
        return self.role == "child"

    @property
    def active(self) -> bool:
        return self.disabled_at is None

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role, "member_id": self.member_id, "created_at": self.created_at,
                "disabled": self.disabled_at is not None, "prefs": dict(self.prefs)}


# the login of a session that names no user: the owner of the machine (what `coach ui` always gave)
LEGACY = User(LEGACY_ID, "adult")


def _row(r) -> User:
    try:
        prefs = json.loads(r[5] or "{}")
        prefs = prefs if isinstance(prefs, dict) else {}
    except ValueError:
        prefs = {}
    return User(r[0], r[2], r[1], r[3], r[4], prefs)


SELECT = "SELECT id, member_id, role, created_at, disabled_at, prefs FROM ui_users"


def get(con, user_id: str) -> Optional[User]:
    try:
        r = con.execute(SELECT + " WHERE id=?", (user_id,)).fetchone()
    except Exception:                                                  # noqa: BLE001 - migration 0022 pending
        return None
    return _row(r) if r else None


def listing(con) -> list[User]:
    try:
        return [_row(r) for r in con.execute(SELECT + " ORDER BY id")]
    except Exception:                                                  # noqa: BLE001
        return []


def add(con, people: People, user_id: str, role: str, member_id: Optional[str] = None) -> User:
    if not SLUG.match(user_id or ""):
        raise HouseholdError("a login id is lowercase letters, digits, '-' or '_' (at most 31 characters)")
    if user_id in RESERVED:
        raise HouseholdError(f"{user_id!r} is a reserved login id (the owner login is the one the app opens without --user): choose another")
    if role not in ROLES:
        raise HouseholdError(f"role must be one of {', '.join(ROLES)}")
    mid = None
    if member_id:
        mid = people.resolve(member_id)
        if mid not in people.by_id:
            raise HouseholdError(f"{member_id!r} is not a household member (ids: {', '.join(people.ids()) or 'none declared'})")
    if role == "child":
        if not mid:
            raise HouseholdError("a child login needs --member: the child whose own data it shows")
        if people.role(mid) != "child":
            raise HouseholdError(f"{mid!r} is not declared as a child in household.yaml: a child login can only belong to a child member")
    if get(con, user_id):
        raise HouseholdError(f"login {user_id!r} already exists")
    con.execute("INSERT INTO ui_users(id, member_id, role, created_at, prefs) VALUES (?,?,?,?,'{}')", (user_id, mid, role, now_iso()))
    con.commit()
    return get(con, user_id)


def _must(con, user_id: str) -> User:
    u = get(con, user_id)
    if not u:
        raise HouseholdError(f"no login {user_id!r} (`coach users list`)")
    return u


def set_role(con, people: People, user_id: str, role: str, member_id: Optional[str] = None) -> User:
    u = _must(con, user_id)
    if role not in ROLES:
        raise HouseholdError(f"role must be one of {', '.join(ROLES)}")
    mid = people.resolve(member_id) if member_id else u.member_id
    if member_id and mid not in people.by_id:
        raise HouseholdError(f"{member_id!r} is not a household member")
    if role == "child" and (not mid or people.role(mid) != "child"):
        raise HouseholdError("a child login needs a member declared as a child (--member)")
    con.execute("UPDATE ui_users SET role=?, member_id=? WHERE id=?", (role, mid, user_id))
    con.commit()
    return get(con, user_id)


def disable(con, user_id: str) -> User:
    _must(con, user_id)
    con.execute("UPDATE ui_users SET disabled_at=? WHERE id=? AND disabled_at IS NULL", (now_iso(), user_id))
    con.commit()
    return get(con, user_id)


def enable(con, user_id: str) -> User:
    _must(con, user_id)
    con.execute("UPDATE ui_users SET disabled_at=NULL WHERE id=?", (user_id,))
    con.commit()
    return get(con, user_id)


def remove(con, user_id: str) -> None:
    _must(con, user_id)
    con.execute("DELETE FROM ui_users WHERE id=?", (user_id,))
    con.commit()


def clean_prefs(people: People, prefs: dict) -> dict:
    out = {}
    for k, v in prefs.items():
        if k not in PREFERENCES:
            raise HouseholdError(f"unknown preference {k!r} (known: {', '.join(PREFERENCES)})")
        allowed = PREFERENCES[k]
        if k == "default_member":
            if v not in ("", None, "joint") and v not in people.by_id:
                raise HouseholdError(f"default_member: {v!r} is not a household member")
        elif v not in allowed:
            raise HouseholdError(f"{k}: one of {', '.join(allowed)}")
        out[k] = v
    return out


def set_prefs(con, people: People, user_id: str, prefs: dict) -> User:
    """Merge whitelisted preferences into a login's own preferences."""
    u = get(con, user_id)
    if not u:
        raise HouseholdError("no such login")
    merged = {**u.prefs, **clean_prefs(people, prefs)}
    if u.is_child:                                                     # a child's default view can only be their own
        merged.pop("default_member", None)
    con.execute("UPDATE ui_users SET prefs=? WHERE id=?", (json.dumps(merged, sort_keys=True), user_id))
    con.commit()
    return get(con, user_id)


# ---------------------------------------------------------------- audit

def audit(con, actor: str, method: str, path: str, status: int) -> None:
    try:
        con.execute("INSERT INTO audit_log(at, actor, method, path, status) VALUES (?,?,?,?,?)", (now_iso(), actor, method, path[:200], status))
        con.commit()
    except Exception:                                                  # noqa: BLE001 - an audit failure never blocks a request
        pass


def audit_rows(con, limit: int = 100, actor: Optional[str] = None) -> list[dict]:
    sql = "SELECT at, actor, method, path, status FROM audit_log"
    args: tuple = ()
    if actor:
        sql += " WHERE actor=?"
        args = (actor,)
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(zip(("at", "actor", "method", "path", "status"), r)) for r in con.execute(sql, (*args, limit))]


def memory_audit(store, limit: int = 100) -> list[dict]:
    """Who changed the memory: the history's changes with their source (``ui:<login>``, ``cli``, ``coach-llm`` ...)."""
    out = []
    for c in store.history(None, limit):
        out.append({"id": c.id, "date": c.date, "source": c.source or "unknown", "subject": c.subject, "files": c.files})
    return out
