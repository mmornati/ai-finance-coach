"""CLI of the household (E14): ``coach household ...`` and ``coach users ...``.

    household show                         members, account owners and purposes, who the transactions belong to, the rules, the logins
    household assign TX MEMBER|joint       reassign one transaction to a member by hand (recorded, reversible)
    household unassign TX                  remove that manual reassignment
    household why TX                       whose a transaction is, and why (manual line, every rule, the account owner)
    household log [TX]                     the recorded reassignments;  household undo LOG_ID  reverts one
    household rule list|add|remove         attribution rules of household.yaml (a card or an account per child, a card's last four digits ...)
    household kids [MEMBER]                the children's money: pocket money, extra top-ups, spending, balance trend, pocket vs extra
    household pocket set|clear MEMBER      declare the pocket money a child is meant to get
    household budget list|set|remove       weekly / monthly kid budgets (alerts: the local feed only)
    household allocation                   who pays what: shared costs split by rule;  household allocate set|remove  edits the rules
    users list|add|set-role|disable|enable|remove|prefs|audit    per-person logins of the web app (adult = all data, child = own data)

The writes of household.yaml preview the diff first. `--dry-run` writes nothing, `--propose` queues a sealed proposal (the coach's door:
the user accepts it THEMSELVES), otherwise a terminal and a typed yes are needed. Logins exist only through `coach users`: the web app can
neither create nor promote one. The one-time login link of a person: `coach ui --login-link --user ID`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Optional

from coach import db as db_mod
from coach.analytics import api as analytics_api
from coach.analytics.common import money_str
from coach.household import allocation as alloc_mod, attribution as attr_mod, kidbudgets, kids as kids_mod, people as people_mod, users as users_mod
from coach.household.attribution import HouseholdError
from coach.i18n_msg import strip_msgs
from coach.memory.edit import jsonable
from coach.memory.store import MemoryStore, MemoryStoreError, ValidationFailed


def _today() -> dt.date:
    return dt.date.today()


def _con(a, cfg):
    return db_mod.connect(cfg, insecure=a.insecure)


def _store(cfg, a=None) -> MemoryStore:
    return MemoryStore(cfg.memory_dir, history=cfg.memory_history, source=getattr(a, "source", None) or "cli")


def _people(cfg):
    return people_mod.load(MemoryStore(cfg.memory_dir, history=False))


def _fail(e) -> None:
    sys.exit(f"error: {e}")


def _j(obj) -> str:
    from coach.i18n_msg import strip_msgs
    return json.dumps(strip_msgs(jsonable(obj)), ensure_ascii=False, indent=2, default=str)   # the *_msg siblings are the web's only


def _eur(c) -> str:
    return "-" if c is None else money_str(c)


def _confirm(a, what: str) -> bool:
    from coach.analytics.commands import _confirm as c
    return c(a, what)


# ---------------------------------------------------------------- shared write path of household.yaml lists

def _write_list(a, cfg, ops: list, action: str, what: str, detail: str) -> None:
    from coach.memory import proposals as prop_mod
    store = _store(cfg, a)
    try:
        preview = store.edit("household.yaml", ops, action=action, reason=a.reason, dry_run=True)
    except (MemoryStoreError, ValidationFailed) as e:
        _fail(e)
    if not preview.changed:
        print("no change")
        return
    print(preview.diff.rstrip("\n"))
    if a.dry_run:
        print("(dry run: nothing written, nothing queued)")
        return
    if a.propose:
        try:
            p = prop_mod.create(store, "household.yaml", ops, a.reason or what, a.source or "coach-llm")
        except MemoryStoreError as e:
            _fail(e)
        print(f"\nnothing was written: queued as proposal {p.id}. The user applies it with `coach memory accept {p.id}`.")
        return
    if not _confirm(a, f"write {what}"):
        return
    res = store.edit("household.yaml", ops, action=action, reason=a.reason, detail=detail)
    print("\nwritten" + (f" as change {res.change_id}" if res.change_id else " (history is off)"))


def _upsert(a, cfg, key: str, item_id: str, value: dict, action: str, what: str) -> None:
    store = _store(cfg, a)
    _write_list(a, cfg, people_mod.upsert_ops(store, key, item_id, value), action, what, item_id)


def _remove(a, cfg, key: str, item_id: str, action: str, what: str) -> None:
    ops = people_mod.delete_ops(_store(cfg, a), key, item_id)
    if ops is None:
        _fail(f"no such item {item_id!r}")
    _write_list(a, cfg, ops, action, what, item_id)


# ---------------------------------------------------------------- show

def cmd_show(a, cfg):
    con = _con(a, cfg)
    ds = analytics_api.build_dataset(con, cfg, _today())
    people = ds.memory.people or people_mod.People()
    counts: dict = {}
    for t in ds.whole:
        counts[t.person or "unassigned"] = counts.get(t.person or "unassigned", 0) + 1
    manual = con.execute("SELECT COUNT(*) FROM tx_person").fetchone()[0]
    out = {"members": [{"id": m.id, "name": m.name, "role": m.role, "birth_year": m.birth_year,
                        "accounts": [x.label for x in ds.accounts.values() if people.resolve(x.owner) == m.id],
                        "transactions": counts.get(m.id, 0)} for m in people.members],
           "accounts": [{"label": x.label, "bank": x.bank, "owner": x.owner, "owner_member": people.resolve(x.owner), "purpose": x.purpose}
                        for x in ds.accounts.values()],
           "transactions_by_person": counts, "manual_reassignments": manual,
           "rules": [r.id for r in people.attribution], "kid_budgets": [b.id for b in people.kid_budgets],
           "allocations": [x.id for x in people.allocations], "logins": [u.to_dict() for u in users_mod.listing(con)]}
    if a.json:
        print(_j(out))
        return
    if not people.members:
        print("no member declared: `coach memory member add --id ID --name NAME --role adult|child`")
    for m in out["members"]:
        print(f"{m['id']:<12} {m['role']:<6} {m['name']:<24} {m['transactions']:>5} transactions   accounts: {', '.join(m['accounts']) or '-'}")
    print("\naccounts (owner = who the account belongs to; change with `coach accounts set UID --owner X --purpose Y`):")
    for x in out["accounts"]:
        who = x["owner_member"] or ("?" if x["owner"] else "-")
        print(f"  {x['label'][:30]:<30} owner {str(x['owner'] or '-'):<12} -> {who:<10} purpose {x['purpose'] or '-'}")
    print(f"\ntransactions by person: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"   (manual reassignments: {manual})")
    print(f"attribution rules: {', '.join(out['rules']) or 'none'}   kid budgets: {', '.join(out['kid_budgets']) or 'none'}   "
          f"allocations: {', '.join(out['allocations']) or 'none'}")
    print("logins: " + (", ".join(f"{u['id']} ({u['role']}{', disabled' if u['disabled'] else ''})" for u in out["logins"]) or "none (the owner login of `coach ui` only)"))


# ---------------------------------------------------------------- manual attribution

def cmd_assign(a, cfg):
    con = _con(a, cfg)
    try:
        r = attr_mod.assign(con, _people(cfg), a.tx, a.member, by=a.source or "cli", note=a.note)
    except HouseholdError as e:
        _fail(e)
    print(("reassigned" if r["changed"] else "already assigned") + f": {r['tx_key']} -> {r['member']}"
          + (f" (was {r['previous']})" if r.get("previous") else ""))


def cmd_unassign(a, cfg):
    con = _con(a, cfg)
    try:
        r = attr_mod.clear(con, a.tx, by=a.source or "cli")
    except HouseholdError as e:
        _fail(e)
    print(f"manual reassignment removed ({r['previous']}): the rules and the account owner decide again" if r["changed"]
          else "there was no manual reassignment")


def cmd_why(a, cfg):
    con = _con(a, cfg)
    try:
        key = attr_mod._tx_row(con, a.tx)
        x = attr_mod.explain(con, _people(cfg), key)
    except HouseholdError as e:
        _fail(e)
    if a.json:
        print(_j(strip_msgs(x)))          # the web's messages are not part of the CLI's JSON
        return
    print(print_why(x))


def print_why(x: dict) -> str:
    L = [f"{x['tx_key']}: {x['person'] or 'nobody (the owner is unknown)'}  [{x['source']}] {x['reason']}"]
    if x["manual"]:
        L.append(f"  manual: {x['manual']['member']} set {x['manual']['set_at'][:16]} by {x['manual']['set_by']}" + (f" ({x['manual']['note']})" if x['manual']['note'] else ""))
    for r in x["rules"]:
        L.append(f"  rule {r['id']} -> {r['member']}: " + ("MATCHES" if r["matched"] else f"no ({r['why_not']})"))
    L.append(f"  account {x['account']}: owner {x['account_owner'] or '-'} -> {x['account_owner_member'] or '-'}"
             + (f"; card ending {x['card_last4']}" if x["card_last4"] else ""))
    return "\n".join(L)


def cmd_log(a, cfg):
    con = _con(a, cfg)
    key = None
    if a.tx:
        try:
            key = attr_mod._tx_row(con, a.tx)
        except HouseholdError as e:
            _fail(e)
    rows = attr_mod.log(con, key, a.limit)
    if a.json:
        print(_j(rows))
        return
    for r in rows:
        print(f"#{r['id']:<4} {r['at'][:16]} {r['action']:<6} {r['tx_key'][:28]:<28} {r['old_member'] or '-':>10} -> {r['new_member'] or '-':<10} by {r['by']}")
    if not rows:
        print("no recorded reassignment")


def cmd_undo(a, cfg):
    con = _con(a, cfg)
    try:
        r = attr_mod.revert(con, _people(cfg), a.id, by=a.source or "cli")
    except HouseholdError as e:
        _fail(e)
    print(f"reverted #{r['reverted']}: {r['tx_key']} is now " + (r["member"] or "decided by the rules and the owner again"))


# ---------------------------------------------------------------- attribution rules

def cmd_rule_list(a, cfg):
    people = _people(cfg)
    if a.json:
        print(_j([r.model_dump(exclude_none=True) for r in people.attribution]))
        return
    for r in people.attribution:
        cond = ", ".join(f"{k}={v}" for k, v in r.match.model_dump(exclude_none=True).items())
        print(f"{r.id:<20} -> {r.member:<10} when {cond}" + (f"   # {r.note}" if r.note else ""))
    if not people.attribution:
        print("no attribution rule: the account owner decides (`coach household rule add ID --member M --account ACCOUNT`)")


def cmd_rule_add(a, cfg):
    match = {k: v for k, v in (("account", a.account), ("card_last4", a.card_last4), ("merchant_key", a.merchant), ("description", a.description),
                               ("direction", a.direction), ("amount_min", a.min), ("amount_max", a.max)) if v is not None}
    value = {"id": a.id, "member": a.member, "match": match}
    if a.note:
        value["note"] = a.note
    from pydantic import ValidationError
    from coach.memory import schemas
    try:
        schemas.AttributionRule.model_validate(value)
    except ValidationError as e:
        _fail("; ".join(f"{'.'.join(str(x) for x in er['loc'])}: {er['msg'].removeprefix('Value error, ')}" for er in e.errors()))
    _upsert(a, cfg, "attribution", a.id, value, "attribution-rule", f"attribution rule {a.id}")


def cmd_rule_remove(a, cfg):
    _remove(a, cfg, "attribution", a.id, "attribution-rule-delete", f"removal of attribution rule {a.id}")


# ---------------------------------------------------------------- children

def _print_kid(r: dict, name: str) -> None:
    print(f"== {name} ({r['member']})   window {r['window']['from']} .. {r['window']['to']}")
    bal = r["balance"]
    print(f"balance: {_eur(bal.get('current_c'))} EUR" + (f" as of {bal['as_of']}" if bal.get("as_of") else "") + (" (estimated trend below)" if bal["trend"] else "")
          + (f"; unknown: {', '.join(bal['unknown'])}" if bal["unknown"] else ""))
    for p in bal["trend"]:
        print(f"   {p['month']}  {_eur(p['end_balance_c']):>10}")
    pm = r["pocket_money"]
    for s in pm["series"]:
        print(f"pocket money: {_eur(s['amount_c'])} EUR {s['cadence']} from {s['source']} ({s['count']} payments, last {s['last']}, next about {s['next_expected']})")
    if not pm["series"]:
        print("pocket money: no regular top-up detected" + (f" (declared {_eur(pm['declared']['amount_c'])} EUR {pm['declared']['period']})" if pm["declared"] else ""))
    ex = r["extra_topups"]
    print(f"extra top-ups: {_eur(ex['total_c'])} EUR in {ex['count']} credit(s)" + (": " + ", ".join(f"{k} {_eur(v)}" for k, v in ex["by_source"].items()) if ex["by_source"] else ""))
    ra = r["ratio"]
    if ra["pocket_share"] is not None:
        print(f"pocket money vs extra: {ra['pocket_share'] * 100:.0f} % pocket money, {ra['extra_share'] * 100:.0f} % extra")
    sp = r["spending"]
    print(f"spending: {_eur(sp['total_c'])} EUR over the window ({_eur(sp['monthly_avg_c'])} per month), {_eur(sp['this_month_to_date_c'])} EUR this month so far")
    for c in sp["by_category"][:6]:
        print(f"   {c['category']:<28} {_eur(c['total_c']):>9}  {c['n']} payment(s)")
    for n in r["notes"]:
        print(f"note: {n}")


def cmd_kids(a, cfg):
    con = _con(a, cfg)
    ds = analytics_api.build_dataset(con, cfg, _today())
    people = ds.memory.people or people_mod.People()
    targets = [a.member] if a.member else people.children()
    if a.member and a.member not in people.by_id:
        _fail(f"{a.member!r} is not a household member")
    reps = [kids_mod.kid_report(ds, con, m, a.months) for m in targets]
    if a.json:
        print(_j([kids_mod.to_json(r) for r in reps]))
        return
    if not reps:
        print("no child declared (`coach memory member add --role child`)")
    for r in reps:
        _print_kid(r, people.name(r["member"]))


def cmd_pocket(a, cfg):
    people = _people(cfg)
    if a.member not in people.by_id:
        _fail(f"{a.member!r} is not a household member")
    path = f"members[{a.member}].pocket_money"
    if a.pocket_cmd == "clear":
        ops = [{"op": "unset", "path": path}]
    else:
        v: dict = {"amount": a.amount, "period": a.period}
        if a.day:
            v["day"] = a.day
        ops = [{"op": "set", "path": path, "value": v}]
    _write_list(a, cfg, ops, "pocket-money", f"pocket money of {a.member}", a.member)


def cmd_budget(a, cfg):
    if a.budget_cmd == "list":
        con = _con(a, cfg)
        ds = analytics_api.build_dataset(con, cfg, _today())
        rows = kidbudgets.status(ds)
        if a.json:
            print(_j([kids_mod.to_json(r) for r in rows]))
            return
        for r in rows:
            print(f"{r['id']:<16} {r['member']:<10} {r['period']:<8} {_eur(r['spent_c']):>8} / {_eur(r['limit_c']):<8} EUR  {r['ratio'] * 100:>5.0f} %  {r['status']}"
                  + (f"  [{r['category'] or r['group']}]" if r['category'] or r['group'] else ""))
        if not rows:
            print("no kid budget (`coach household budget set ID --member M --period weekly --limit 10`)")
    elif a.budget_cmd == "remove":
        _remove(a, cfg, "kid_budgets", a.id, "kid-budget-delete", f"removal of kid budget {a.id}")
    else:
        value: dict = {"id": a.id, "member": a.member, "period": a.period, "limit": a.limit}
        for k in ("category", "group", "note"):
            if getattr(a, k):
                value[k] = getattr(a, k)
        _upsert(a, cfg, "kid_budgets", a.id, value, "kid-budget", f"kid budget {a.id}")


# ---------------------------------------------------------------- who pays what

def cmd_allocation(a, cfg):
    con = _con(a, cfg)
    ds = analytics_api.build_dataset(con, cfg, _today())
    rep = alloc_mod.who_pays(ds, a.months, a.rule)
    if a.json:
        print(_j(alloc_mod.to_json(rep)))
        return
    people = ds.memory.people or people_mod.People()
    for r in rep["rules"]:
        print(f"== {r['title'] or r['id']} ({r['method']}): {_eur(r['total_c'])} EUR over {len(rep['window']['months'])} month(s), "
              f"{r['n']} payments; joint account {_eur(r['joint_paid_c'])}, personal {_eur(r['personal_paid_c'])}, unattributed {_eur(r['unattributed_c'])}")
        for m in r["members"]:
            print(f"   {people.name(m['member']):<20} share {m['share_pct']:>5.1f} %  owed {_eur(m['owed_c']):>9}  paid {_eur(m['paid_c']):>9}  settlement {_eur(m['net_c']):>9}")
        for n in r["notes"]:
            print(f"   note: {n}")
    for n in rep["notes"]:
        print(n)


def cmd_allocate(a, cfg):
    if a.allocate_cmd == "remove":
        _remove(a, cfg, "allocations", a.id, "allocation-delete", f"removal of allocation {a.id}")
        return
    match = {k: v for k, v in (("category", a.category), ("group", a.group), ("tag", a.tag), ("merchant_key", a.merchant), ("account", a.account)) if v}
    value: dict = {"id": a.id, "match": match, "method": a.method}
    if a.title:
        value["title"] = a.title
    if a.among:
        value["among"] = [x.strip() for x in a.among.split(",") if x.strip()]
    if a.share:
        try:
            value["shares"] = {k: float(v) for k, v in (s.split("=", 1) for s in a.share)}
        except ValueError:
            _fail("--share is MEMBER=PERCENT, for example --share anna=60 --share luca=40")
    if a.note:
        value["note"] = a.note
    _upsert(a, cfg, "allocations", a.id, value, "allocation", f"allocation {a.id}")


# ---------------------------------------------------------------- users

def cmd_users(a, cfg):
    con = _con(a, cfg)
    people = _people(cfg)
    try:
        c = a.users_cmd
        if c == "list":
            rows = users_mod.listing(con)
            if a.json:
                print(_j([u.to_dict() for u in rows]))
                return
            for u in rows:
                print(f"{u.id:<16} {u.role:<6} member {u.member_id or '-':<12} {'DISABLED' if u.disabled_at else 'active':<9} prefs {json.dumps(u.prefs) if u.prefs else '-'}")
            if not rows:
                print("no login: the web app opens with the owner login of `coach ui` (all data). `coach users add ID --role adult|child [--member M]`")
        elif c == "add":
            u = users_mod.add(con, people, a.id, a.role, a.member)
            print(f"login {u.id} created ({u.role}" + (f", member {u.member_id}" if u.member_id else "") + f"). One-time link: `coach ui --login-link --user {u.id}`")
        elif c == "set-role":
            u = users_mod.set_role(con, people, a.id, a.role, a.member)
            print(f"login {u.id} is now {u.role}" + (f" (member {u.member_id})" if u.member_id else ""))
        elif c == "disable":
            users_mod.disable(con, a.id)
            print(f"login {a.id} disabled: its sessions stop working at once")
        elif c == "enable":
            users_mod.enable(con, a.id)
            print(f"login {a.id} enabled")
        elif c == "remove":
            users_mod.remove(con, a.id)
            print(f"login {a.id} removed")
        elif c == "prefs":
            u = users_mod.get(con, a.id)
            if not u:
                _fail(f"no login {a.id!r}")
            if a.set:
                kv = {}
                for s in a.set:
                    k, _, v = s.partition("=")
                    kv[k] = v
                u = users_mod.set_prefs(con, people, a.id, kv)
            print(_j(u.prefs))
        elif c == "audit":
            rows = users_mod.audit_rows(con, a.limit, a.actor)
            if a.json:
                print(_j({"requests": rows, "memory": users_mod.memory_audit(MemoryStore(cfg.memory_dir, history=cfg.memory_history), a.limit)}))
                return
            for r in rows:
                print(f"{r['at'][:19]} {r['actor']:<14} {r['method']:<6} {r['status']} {r['path']}")
            try:
                mem = users_mod.memory_audit(MemoryStore(cfg.memory_dir, history=cfg.memory_history), a.limit)
            except Exception:                                          # noqa: BLE001
                mem = []
            if mem:
                print("\nmemory changes (who):")
            for m in mem:
                print(f"{m['date'][:19]} {m['source']:<16} {m['subject']}")
    except HouseholdError as e:
        _fail(e)


# ---------------------------------------------------------------- registration

def _write_flags(s) -> None:
    s.add_argument("--reason"); s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history")
    s.add_argument("--yes", action="store_true"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--propose", action="store_true")


def register(sub, add) -> None:
    hp = sub.add_parser("household", help="people: members, owners, who a transaction belongs to, the children's money (E14)",
                        description="household and people: see the module help in coach/household/commands.py and docs/household.md")
    hsub = hp.add_subparsers(dest="household_cmd", required=True, metavar="SUBCOMMAND")
    s = add(hsub, "show", cmd_show, "members, account owners and purposes, transactions by person, rules, logins")
    s.add_argument("--json", action="store_true")
    s = add(hsub, "assign", cmd_assign, "reassign a transaction to a member (or joint) by hand; recorded and reversible")
    s.add_argument("tx"); s.add_argument("member"); s.add_argument("--note"); s.add_argument("--source", metavar="NAME")
    s = add(hsub, "unassign", cmd_unassign, "remove the manual reassignment of a transaction")
    s.add_argument("tx"); s.add_argument("--source", metavar="NAME")
    s = add(hsub, "why", cmd_why, "whose a transaction is, and why")
    s.add_argument("tx"); s.add_argument("--json", action="store_true")
    s = add(hsub, "log", cmd_log, "the recorded manual reassignments")
    s.add_argument("tx", nargs="?"); s.add_argument("--limit", type=int, default=30); s.add_argument("--json", action="store_true")
    s = add(hsub, "undo", cmd_undo, "undo one recorded reassignment (by its log number)")
    s.add_argument("id", type=int); s.add_argument("--source", metavar="NAME")

    rp = hsub.add_parser("rule", help="attribution rules (household.yaml)", description="attribution rules")
    rsub = rp.add_subparsers(dest="rule_cmd", required=True, metavar="SUBCOMMAND")
    s = add(rsub, "list", cmd_rule_list, "the rules"); s.add_argument("--json", action="store_true")
    s = add(rsub, "add", cmd_rule_add, "create or replace a rule (preview first)")
    s.add_argument("id"); s.add_argument("--member", required=True, help="a member id, or joint")
    s.add_argument("--account", help="account uid or label (for example a prepaid card account per child)")
    s.add_argument("--card-last4", help="the last four digits of the card, when the bank prints them on a shared account")
    s.add_argument("--merchant", help="regex on the merchant key"); s.add_argument("--description", help="regex on the bank description")
    s.add_argument("--direction", choices=["in", "out"]); s.add_argument("--min", type=float); s.add_argument("--max", type=float); s.add_argument("--note")
    _write_flags(s)
    s = add(rsub, "remove", cmd_rule_remove, "remove a rule (preview first)"); s.add_argument("id"); _write_flags(s)

    s = add(hsub, "kids", cmd_kids, "the children's money: pocket money, extra top-ups, spending, balance trend")
    s.add_argument("member", nargs="?"); s.add_argument("--months", type=int, default=6); s.add_argument("--json", action="store_true")
    pp = hsub.add_parser("pocket", help="declare the pocket money a child is meant to get", description="declare the pocket money of a child")
    psub = pp.add_subparsers(dest="pocket_cmd", required=True, metavar="SUBCOMMAND")
    s = add(psub, "set", cmd_pocket, "set it (preview first)")
    s.add_argument("member"); s.add_argument("--amount", type=float, required=True); s.add_argument("--period", choices=["weekly", "monthly"], required=True)
    s.add_argument("--day", type=int); _write_flags(s)
    s = add(psub, "clear", cmd_pocket, "remove it"); s.add_argument("member"); _write_flags(s)

    bp = hsub.add_parser("budget", help="kid budgets, weekly or monthly", description="kid budgets (alerts: the local feed only)")
    bsub = bp.add_subparsers(dest="budget_cmd", required=True, metavar="SUBCOMMAND")
    s = add(bsub, "list", cmd_budget, "budgets with their progress"); s.add_argument("--json", action="store_true")
    s = add(bsub, "set", cmd_budget, "create or replace a kid budget (preview first)")
    s.add_argument("id"); s.add_argument("--member", required=True); s.add_argument("--period", choices=["weekly", "monthly"], required=True)
    s.add_argument("--limit", type=float, required=True); s.add_argument("--category"); s.add_argument("--group"); s.add_argument("--note"); _write_flags(s)
    s = add(bsub, "remove", cmd_budget, "remove a kid budget"); s.add_argument("id"); _write_flags(s)

    s = add(hsub, "allocation", cmd_allocation, "who pays what: shared costs split by rule")
    s.add_argument("--months", type=int, default=12); s.add_argument("--rule"); s.add_argument("--json", action="store_true")
    ap = hsub.add_parser("allocate", help="edit the allocation rules", description="allocation rules of shared costs")
    asub = ap.add_subparsers(dest="allocate_cmd", required=True, metavar="SUBCOMMAND")
    s = add(asub, "set", cmd_allocate, "create or replace an allocation rule (preview first)")
    s.add_argument("id"); s.add_argument("--title"); s.add_argument("--category"); s.add_argument("--group"); s.add_argument("--tag")
    s.add_argument("--merchant", help="regex on the merchant key"); s.add_argument("--account")
    s.add_argument("--method", choices=["equal", "income", "custom"], default="equal")
    s.add_argument("--among", help="comma-separated member ids (default: every adult)"); s.add_argument("--share", action="append", metavar="MEMBER=PERCENT")
    s.add_argument("--note"); _write_flags(s)
    s = add(asub, "remove", cmd_allocate, "remove an allocation rule"); s.add_argument("id"); _write_flags(s)

    up = sub.add_parser("users", help="per-person logins of the web app (E14-8)", description="logins of the web app: adult = all data, child = own data")
    usub = up.add_subparsers(dest="users_cmd", required=True, metavar="SUBCOMMAND")
    s = add(usub, "list", cmd_users, "the logins"); s.add_argument("--json", action="store_true")
    s = add(usub, "add", cmd_users, "create a login (then `coach ui --login-link --user ID` prints its one-time link)")
    s.add_argument("id"); s.add_argument("--role", choices=list(users_mod.ROLES), required=True); s.add_argument("--member", help="the household member (required for a child)")
    s = add(usub, "set-role", cmd_users, "change a login's role (and member)")
    s.add_argument("id"); s.add_argument("--role", choices=list(users_mod.ROLES), required=True); s.add_argument("--member")
    for name, hlp in (("disable", "disable a login: its sessions stop at once"), ("enable", "enable a login"), ("remove", "delete a login")):
        s = add(usub, name, cmd_users, hlp); s.add_argument("id")
    s = add(usub, "prefs", cmd_users, "show or set a login's preferences (locale, theme, default_member, landing)")
    s.add_argument("id"); s.add_argument("--set", action="append", metavar="KEY=VALUE")
    s = add(usub, "audit", cmd_users, "who changed what: the web app's audit log and the memory history sources")
    s.add_argument("--limit", type=int, default=50); s.add_argument("--actor"); s.add_argument("--json", action="store_true")
