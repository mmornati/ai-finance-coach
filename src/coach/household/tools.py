"""The finance MCP tools of the household epic (E14): ``household_overview``, ``kids_money`` and ``who_pays`` (all read-only).

They sit behind the same choke point as every tool (``ToolSession._publish``). What is generalised before it gets there: members are the
pseudonyms ``adult-1`` / ``kid-1`` (never an id, a name or an alias; birth years and ages are not given), accounts are the account pseudonyms,
a payment is its hashed ``ref``, and the user's own labels of rules and budgets (``attribution`` rule ids, kid budget ids, allocation ids and
titles) are replaced by ``kid-budget-1``, ``allocation-2`` ... or scrubbed. Amounts, dates and categories are allowed. A model may FILTER
any analytics tool by a member pseudonym (the ``member`` argument): then only what is attributed to that person is counted.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable

from coach.household import allocation as alloc_mod, kidbudgets, kids as kids_mod
from coach.mcp.tools import DATA_NOTE, ToolError, ToolSession, ToolSpec, _obj

SPECIAL = ("joint", "other", "unknown", "unassigned")
MAX_ITEMS = 12                                                       # extra top-ups listed one by one in `kids_money`


def pseudonymiser(s: ToolSession) -> tuple[Callable, Callable]:
    """(member id -> pseudonym, pseudonym -> member id). The same pseudonyms as the owners of every other tool."""
    pseudo = dict(s.reg.red.sc.pseudo)
    back = {v: k for k, v in pseudo.items()}

    def fwd(m):
        if m is None or m in SPECIAL:
            return m
        return pseudo.get(m, "[person]")

    def rev(p):
        if p in SPECIAL:
            return p
        if p not in back:
            raise ToolError("unknown member: use a member pseudonym as it appears in a tool result (adult-N, kid-N, or joint)")
        return back[p]
    return fwd, rev


def member_of(s: ToolSession, pseudonym: str):
    """The real member id behind a pseudonym of a tool argument ('joint' stays 'joint'); refuses anything else."""
    return pseudonymiser(s)[1](pseudonym)


def _people(s: ToolSession):
    people = s.reg.ds.memory.people
    if people is None or not people:
        raise ToolError("no household member is declared yet: the user adds them with `coach memory member add`")
    return people


# ---------------------------------------------------------------- household_overview

def _overview(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    people = _people(s)
    fwd, _ = pseudonymiser(s)
    counts = Counter(t.person or "unassigned" for t in ds.whole)
    members = []
    for m in people.members:
        owned = [reg.red.account.get(u.uid) for u in ds.accounts.values() if people.resolve(u.owner) == m.id]
        members.append({"member": fwd(m.id), "role": m.role, "accounts": sorted(x for x in owned if x),
                        "attributed_transactions": counts.get(m.id, 0)})
    per: dict = {}
    for t in ds.whole:
        per.setdefault(t.account, Counter())[fwd(t.person) if t.person else "unassigned"] += 1
    accounts = []
    for uid, acc in sorted(ds.accounts.items()):
        who = people.resolve(acc.owner)
        accounts.append({"account": reg.red.account.get(uid), "purpose": acc.purpose,
                         "owner": fwd(who) if who else None, "attributed": dict(sorted(per.get(uid, {}).items()))})
    manual = 0
    try:
        manual = s.con.execute("SELECT COUNT(*) FROM tx_person").fetchone()[0]
    except Exception:                                                      # noqa: BLE001 - migration pending
        pass
    raw = {"as_of": ds.today.isoformat(), "members": members, "accounts": accounts,
           "transactions_by_person": {fwd(k) if k != "unassigned" else k: v for k, v in sorted(counts.items(), key=lambda kv: str(kv[0]))},
           "attribution_rules": len(people.attribution), "manual_reassignments": manual, "kid_budgets": len(people.kid_budgets),
           "allocation_rules": len(people.allocations),
           "note": ("Members are pseudonyms; the transactions of a joint account belong to `joint` unless a rule or the user attributed them "
                    "to a person. Pass a member pseudonym as `member` to any analytics tool to see only that person's money.")}
    return reg.red.walk(raw)


# ---------------------------------------------------------------- kids_money

def _kids(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    people = _people(s)
    fwd, rev = pseudonymiser(s)
    months = int(a.get("months") or kids_mod.WINDOW_MONTHS)
    if a.get("member"):
        m = rev(a["member"])
        if m not in people.by_id:
            raise ToolError("that member is not a person with an account of their own: use a member pseudonym (adult-N, kid-N)")
        targets = [m]
    else:
        targets = people.children()
    if not targets:
        raise ToolError("no child is declared in the household: the user adds one with `coach memory member add --role child`")
    out = []
    for m in targets:
        j = kids_mod.to_json(kids_mod.kid_report(ds, s.con, m, months))
        j["member"] = fwd(j["member"])
        items = j["extra_topups"]["items"]
        if len(items) > MAX_ITEMS:                                   # the newest ones; the count and the total cover all of them
            j["extra_topups"]["items"] = items = items[:MAX_ITEMS]
            j["extra_topups"]["items_shown"] = MAX_ITEMS
        for it in items:
            it["source"] = fwd(it["source"])
        j["extra_topups"]["by_source"] = {fwd(k): v for k, v in j["extra_topups"]["by_source"].items()}
        for sr in j["pocket_money"]["series"]:
            sr["source"] = fwd(sr["source"])
            sr["evidence"] = list(sr["evidence"])
        out.append(j)
    budgets = []
    for n, b in enumerate(kidbudgets.status(ds), 1):
        if a.get("member") and b["member"] not in targets:
            continue
        j = kids_mod.to_json({k: v for k, v in b.items() if k not in ("id", "note")})
        j["member"] = fwd(j["member"])
        j["ref"] = f"kid-budget-{n}"
        budgets.append(j)
    raw = {"as_of": ds.today.isoformat(), "children": out, "kid_budgets": budgets,
           "note": ("Pocket money = a regular credit (3 or more, same amount, weekly / fortnightly / monthly rhythm) or the declared amount; "
                    "everything else is an extra top-up with its source. The balance trend is rebuilt backwards from the newest balance "
                    "(estimated). A transfer from a parent is an internal transfer of the household: not income or spending for the "
                    "household, but it is the child's income here.")}
    return reg.red.walk(raw)


# ---------------------------------------------------------------- who_pays

def _who_pays(s: ToolSession, a: dict):
    reg = s.reg
    ds = reg.ds
    _people(s)
    fwd, _ = pseudonymiser(s)
    rep = alloc_mod.to_json(alloc_mod.who_pays(ds, int(a.get("months") or alloc_mod.WINDOW_MONTHS)))
    rules = []
    for n, r in enumerate(rep["rules"], 1):
        r["ref"] = f"allocation-{n}"
        r.pop("id", None)
        r["among"] = [fwd(m) for m in r["among"]]
        for m in r["members"]:
            m["member"] = fwd(m["member"])
        if r.get("income_basis"):
            r["income_basis"] = {fwd(k): v for k, v in r["income_basis"].items()}
        rules.append(r)
    rep["rules"] = rules
    for m in rep["by_member"]:
        m["member"] = fwd(m["member"])
    rep["note"] = ("Computed shares and settlement per rule: `owed` is the member's fair share of the whole cost, `paid` what they paid out of their "
                   "own money, `net` the settlement (positive: the others owe them). Costs paid from the joint account settle nothing. "
                   "Quote the numbers exactly; give no legal or tax advice on a couple's finances.")
    return reg.red.walk(rep)


def specs() -> list[ToolSpec]:
    member = {"type": "string", "maxLength": 40, "description": "member pseudonym as it appears in results, e.g. 'adult-1' or 'kid-1'"}
    return [
        ToolSpec("household_overview", "Who is in the household (pseudonyms and roles only, never names), which accounts each owns, and how many "
                 "transactions belong to each person, to `joint` or to nobody. Start here before filtering any tool by `member`." + DATA_NOTE,
                 _obj({}), _overview),
        ToolSpec("kids_money", "The children's money: the regular pocket money (amount, rhythm, source), the extra top-ups with their source, "
                 "spending by category and month, the balance trend (estimated) and the pocket-money versus extra ratio, plus the kid budgets "
                 "with this week's / month's progress. One child (`member`) or all of them. Amounts are computed; a parent's top-up is an "
                 "internal transfer for the household but income for the child." + DATA_NOTE,
                 _obj({"member": member, "months": {"type": "integer", "minimum": 1, "maximum": 24}}), _kids),
        ToolSpec("who_pays", "Shared costs split by the household's allocation rules (equal, by income, custom percentages): each member's "
                 "share, what they paid and the settlement per rule. It reports a split; it never moves money." + DATA_NOTE,
                 _obj({"months": {"type": "integer", "minimum": 1, "maximum": 36}}), _who_pays),
    ]
