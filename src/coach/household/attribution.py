"""Which member a transaction belongs to (E14-3).

Precedence, first hit wins (the same idea as the category chain of :mod:`coach.classify.rules`):

1. a MANUAL reassignment (``tx_person``: ``coach household assign``, the transaction panel). Recorded in ``tx_person_log`` and
   reversible: clearing it, or reverting a log line, restores what the rules and the owner say;
2. an ATTRIBUTION RULE of ``household.yaml`` (``attribution:``), in file order: an account, a card's last four digits (when the bank prints
   them on a shared account), a merchant or description pattern, a direction, an amount range. Every key of a rule must match;
3. the OWNER of the account (``accounts.owner``): a member id, a name or an alias of one; ``joint`` stays joint;
4. nobody (``None``): the owner is unknown, so the transaction belongs to the household only.

The result is a member id, ``"joint"`` (the household as a whole) or ``None``. It decides the person views (E14-4), the children's money
(E14-5) and the allocation (E14-9). It never changes a category, an amount or a transfer link.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from coach.db import now_iso
from coach.household.people import JOINT, People, account_owner_member, fold

# Only forms where the digits are clearly a card's tail: a masked number (X4242, ****4242), "CB*4242", "CARTE N° 4242". A bare "CB 0210" is
# a date on many French statements and is NOT read as a card.
CARD_RE = re.compile(r"(?:[X*\u2022]{2,}[ -]?|\b(?:CB|CARTE|CARD|CARTA)\s*[X*\u2022]+[ -]?|\b(?:CB|CARTE|CARD|CARTA)\s*(?:N°|N\.|NO\.?|#)\s*[X*\u2022]*[ -]?)(\d{4})\b", re.I)


class HouseholdError(Exception):
    pass


@dataclass(frozen=True)
class Attribution:
    person: Optional[str]            # member id | 'joint' | None
    source: str                      # manual | rule | account | none
    rule: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {"person": self.person, "source": self.source, "rule": self.rule, "reason": self.reason}


def card_last4(desc: Optional[str]) -> Optional[str]:
    """The last four digits of the card a bank printed in a description (``CARTE X4242``, ``CB*4242``, ``****4242``), or None."""
    m = CARD_RE.search(desc or "")
    return m.group(1) if m else None


def _rx(p: Optional[str]):
    try:
        return re.compile(p, re.I) if p else None
    except re.error:
        return None


def rule_matches(rule, *, account_uid: str, account_names: set, desc: str, mkey: str, amount: float) -> tuple[bool, str]:
    """(matched, why not). `account_names`: the folded uid / label / name of the transaction's account."""
    m = rule.match
    if m.account is not None and fold(m.account) not in account_names:
        return False, f"the account is not {m.account!r}"
    if m.card_last4 is not None and card_last4(desc) != m.card_last4:
        return False, f"the card ending {m.card_last4} is not printed on it"
    if m.merchant_key is not None:
        rx = _rx(m.merchant_key)
        if rx is None or not rx.search(mkey or ""):
            return False, "the merchant key does not match"
    if m.description is not None:
        rx = _rx(m.description)
        if rx is None or not rx.search(desc or ""):
            return False, "the description does not match"
    if m.direction == "in" and amount <= 0 or m.direction == "out" and amount >= 0:
        return False, f"the direction is not {m.direction!r}"
    mag = abs(amount)
    if m.amount_min is not None and mag < m.amount_min:
        return False, f"the amount is below {m.amount_min:g}"
    if m.amount_max is not None and mag > m.amount_max:
        return False, f"the amount is above {m.amount_max:g}"
    return True, ""


class Attributor:
    """Resolves the person of every transaction of one dataset load. Built once; ``attribute`` is a cheap lookup."""

    def __init__(self, people: People, accounts: dict, manual: Optional[dict] = None):
        # accounts: uid -> (label, name, owner)
        self.people = people
        self.manual = manual or {}
        self._acc = {}
        for uid, (label, name, owner) in accounts.items():
            names = {fold(x) for x in (uid, label, name) if x}
            self._acc[uid] = (names, owner)
        self.rules = list(people.attribution)

    def owner_person(self, account_uid: str) -> Optional[str]:
        names, owner = self._acc.get(account_uid, (set(), None))
        return self.people.resolve(owner)

    def attribute(self, tx_key: str, account_uid: str, desc: str = "", mkey: str = "", amount: float = 0.0) -> Attribution:
        man = self.manual.get(tx_key)
        if man:
            return Attribution(man, "manual", None, "reassigned by hand")
        names, owner = self._acc.get(account_uid, (set(), None))
        for r in self.rules:
            ok, _ = rule_matches(r, account_uid=account_uid, account_names=names, desc=desc, mkey=mkey, amount=amount)
            if ok:
                return Attribution(r.member, "rule", r.id, f"attribution rule {r.id}")
        who = self.people.resolve(owner)
        if who:
            return Attribution(who, "account", None, "owner of the account" if who != JOINT else "joint account")
        return Attribution(None, "none", None, "the account has no known owner")


# ---------------------------------------------------------------- the database side

def load_manual(con) -> dict:
    try:
        return {k: m for k, m in con.execute("SELECT tx_key, member FROM tx_person")}
    except Exception:                                                  # noqa: BLE001 - migration 0022 pending
        return {}


def attributor_for(con, people: People) -> Attributor:
    try:
        accs = {uid: (label, name, owner) for uid, label, name, owner in
                con.execute("SELECT uid, label, name, owner FROM accounts")}
    except Exception:                                                  # noqa: BLE001
        accs = {}
    return Attributor(people, accs, load_manual(con))


def _check_member(people: People, member: str) -> str:
    if member == JOINT or member in people.by_id:
        return member
    r = people.resolve(member)
    if r:
        return r
    raise HouseholdError(f"{member!r} is not a household member (ids: {', '.join(people.ids()) or 'none declared'}, or 'joint')")


def _tx_row(con, ref: str):
    from coach.transfers import TransferError, resolve_tx
    try:
        key = resolve_tx(con, ref)
    except TransferError as e:
        raise HouseholdError(str(e)) from None
    return key


def assign(con, people: People, tx_ref: str, member: str, *, by: str = "cli", note: Optional[str] = None) -> dict:
    """Reassign one transaction to a member (or 'joint') by hand. Recorded; ``clear`` / ``revert`` undo it."""
    key = _tx_row(con, tx_ref)
    member = _check_member(people, member)
    old = con.execute("SELECT member FROM tx_person WHERE tx_key=?", (key,)).fetchone()
    old = old[0] if old else None
    if old == member:
        return {"tx_key": key, "member": member, "changed": False}
    ts = now_iso()
    con.execute("INSERT OR REPLACE INTO tx_person(tx_key, member, set_at, set_by, note) VALUES (?,?,?,?,?)", (key, member, ts, by, note))
    con.execute("INSERT INTO tx_person_log(tx_key, old_member, new_member, action, at, by, note) VALUES (?,?,?,'set',?,?,?)",
                (key, old, member, ts, by, note))
    con.commit()
    return {"tx_key": key, "member": member, "previous": old, "changed": True}


def clear(con, tx_ref: str, *, by: str = "cli", note: Optional[str] = None) -> dict:
    """Remove the manual reassignment: the memory rules and the account owner decide again."""
    key = _tx_row(con, tx_ref)
    old = con.execute("SELECT member FROM tx_person WHERE tx_key=?", (key,)).fetchone()
    if not old:
        return {"tx_key": key, "changed": False}
    con.execute("DELETE FROM tx_person WHERE tx_key=?", (key,))
    con.execute("INSERT INTO tx_person_log(tx_key, old_member, new_member, action, at, by, note) VALUES (?,?,NULL,'clear',?,?,?)",
                (key, old[0], now_iso(), by, note))
    con.commit()
    return {"tx_key": key, "previous": old[0], "changed": True}


def log(con, tx_key: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql = "SELECT id, tx_key, old_member, new_member, action, at, by, note FROM tx_person_log"
    args: tuple = ()
    if tx_key:
        sql += " WHERE tx_key=?"
        args = (tx_key,)
    sql += " ORDER BY id DESC LIMIT ?"
    keys = ["id", "tx_key", "old_member", "new_member", "action", "at", "by", "note"]
    return [dict(zip(keys, r)) for r in con.execute(sql, (*args, limit))]


def revert(con, people: People, log_id: int, *, by: str = "cli") -> dict:
    """Undo one log line: put the transaction back as it was before it (a new log line records the revert)."""
    row = con.execute("SELECT tx_key, old_member, new_member FROM tx_person_log WHERE id=?", (log_id,)).fetchone()
    if not row:
        raise HouseholdError(f"no attribution log line #{log_id}")
    key, old, new = row
    cur = con.execute("SELECT member FROM tx_person WHERE tx_key=?", (key,)).fetchone()
    cur = cur[0] if cur else None
    if cur != new:
        raise HouseholdError("the transaction was changed again since this line: revert the newest change first")
    ts = now_iso()
    if old is None:
        con.execute("DELETE FROM tx_person WHERE tx_key=?", (key,))
    else:
        _check_member(people, old)
        con.execute("INSERT OR REPLACE INTO tx_person(tx_key, member, set_at, set_by, note) VALUES (?,?,?,?,?)",
                    (key, old, ts, by, f"revert of #{log_id}"))
    con.execute("INSERT INTO tx_person_log(tx_key, old_member, new_member, action, at, by, note) VALUES (?,?,?,'revert',?,?,?)",
                (key, cur, old, ts, by, f"revert of #{log_id}"))
    con.commit()
    return {"tx_key": key, "member": old, "reverted": log_id}


def explain(con, people: People, tx_key: str) -> dict:
    """WHY a transaction belongs to whom: the manual line, every rule with whether it matched, the account owner."""
    row = con.execute("""SELECT t.account_uid, t.description, t.amount, e.merchant_key, COALESCE(a.label, a.name, a.uid), a.owner
                         FROM transactions t LEFT JOIN tx_enriched e ON e.tx_key=t.tx_key LEFT JOIN accounts a ON a.uid=t.account_uid
                         WHERE t.tx_key=?""", (tx_key,)).fetchone()
    if not row:
        raise HouseholdError("no such transaction")
    uid, desc, amount, mkey, label, owner = row
    att = attributor_for(con, people)
    names = att._acc.get(uid, (set(), None))[0]
    result = att.attribute(tx_key, uid, desc or "", mkey or "", amount or 0.0)
    man = con.execute("SELECT member, set_at, set_by, note FROM tx_person WHERE tx_key=?", (tx_key,)).fetchone()
    rules = []
    for r in people.attribution:
        ok, why = rule_matches(r, account_uid=uid, account_names=names, desc=desc or "", mkey=mkey or "", amount=amount or 0.0)
        rules.append({"id": r.id, "member": r.member, "matched": ok, "why_not": why or None})
    return {"tx_key": tx_key, "person": result.person, "source": result.source, "rule": result.rule, "reason": result.reason,
            "manual": {"member": man[0], "set_at": man[1], "set_by": man[2], "note": man[3]} if man else None,
            "rules": rules, "account": label, "account_owner": owner, "account_owner_member": account_owner_member(people, owner),
            "card_last4": card_last4(desc), "history": log(con, tx_key, 10)}
